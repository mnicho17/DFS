"""One cancellable preparation sequence using existing validators and loaders."""
import time


class BuildPreparation:
    def __init__(self, worker):
        self.worker = worker
        self.rows = []

    def check_cancelled(self):
        if self.worker._cancel_event.is_set():
            raise InterruptedError('Build preparation cancelled.')

    def begin(self, key, detail):
        self.check_cancelled()
        self.rows.append(dict(stage=key, state='Running', detail=detail))
        self.started = time.monotonic()
        self.worker.progress.emit(0, 0, f'Preparing {len(self.rows)}/4 — {detail}')

    def complete(self, detail):
        row = self.rows[-1]
        row.update(state='Completed', detail=detail, seconds=round(time.monotonic()-self.started, 3))
        self.check_cancelled()

    def report(self, cancelled=False):
        rows = [dict(row) for row in self.rows]
        if rows and rows[-1]['state'] == 'Running':
            rows[-1]['state'] = 'Cancelled' if cancelled else 'Stopped'
        return rows


def prepare_build(worker):
    flow = worker.preparation = BuildPreparation(worker)
    flow.begin('captain', 'Checking Captain pool and exposure adjustments')
    if worker.kind == 'showdown':
        from captain_pool import prepare_captain_pool, adjustment_text
        worker.players, worker.portfolio_rules, worker.captain_adjustments = prepare_captain_pool(
            worker.players, worker.portfolio_rules, worker.num_lineups, worker.retained_lineups)
        if worker.captain_adjustments:
            worker.progress.emit(0, worker.num_lineups, adjustment_text(worker.captain_adjustments))

    flow.complete('Captain pool checked; any existing adjustments remain visible.')
    flow.begin('history', 'Validating requested historical field evidence')
    profile = worker.field_calibration.get('showdown_field_profile')
    if profile:
        if worker.kind != 'showdown' or worker.sport != 'NFL' or not worker.sim_enabled or not worker.compute_mode.casefold().startswith('deep'):
            raise ValueError('Historical Showdown fields require NFL Deep with SIM enabled.')
        from learning_db import history_db_path
        from showdown_history_field import revalidate_profile
        revalidate_profile(profile, history_db_path(), worker.players, worker.field_preset, worker._cancel_event.is_set,
            lambda text: worker.progress.emit(0, worker.num_lineups, text))

    flow.complete('Historical field evidence checked.' if profile else 'Historical field not requested.')
    flow.begin('library', 'Choosing and validating candidate inputs')
    if worker.candidate_library:
        if not worker._load_candidate_library(profile):
            return False
    elif (worker.automatic_libraries or worker.library_folder) and worker.sport == 'NFL' and worker.sim_enabled and worker.compute_mode.casefold().startswith('deep') and not worker.retained_lineups and not worker.repair_source:
        import sqlite3
        from build_preparation import discover_libraries
        worker.progress.emit(0, 0, 'Preparing build: looking for compatible saved libraries…')
        attempts = 0
        for path in discover_libraries(worker.automatic_libraries, worker.library_folder, worker.players,
                worker.kind, worker.salary_cap, cancelled=worker._cancel_event.is_set,
                progress=lambda text: worker.progress.emit(0, 0, text)):
            attempts += 1
            if attempts > 3:
                break
            worker.candidate_library = path
            try:
                if not worker._load_candidate_library(profile):
                    return False
                if not worker.library_candidates:
                    raise ValueError('No candidates satisfy current rules.')
                worker.library_report['automatic_discovery'] = True
                worker.library_report['library_path'] = path
                worker.progress.emit(0, 0, 'Reusing compatible library: ' + path)
                break
            except (ValueError, OSError, sqlite3.Error) as exc:
                worker.progress.emit(0, 0, 'Library unavailable under current inputs: ' + str(exc))
                worker.candidate_library = ''
                worker.library_candidates, worker.library_report = [], {}
        if not worker.candidate_library:
            worker.progress.emit(0, 0, 'No compatible saved library accepted; using the existing fresh candidate search.')
    flow.complete('Validated selected library.' if worker.candidate_library else 'Fresh candidate search will prepare the build inputs.')
    flow.begin('eligibility', 'Checking player eligibility')
    if worker.sport == "NFL":
        from nfl_eligibility import eligible_players, apply_qb_eligibility
        worker.players = apply_qb_eligibility([dict(p) for p in worker.players])
        eligible_players(worker.players)
    flow.complete('Player eligibility checked.')
    flow.check_cancelled()
    worker.progress.emit(0, 0, 'Preparation complete — running candidate search, required screening, SIM and selection under current settings.')
    return True


def preparation_plan(w):
    """Describe the requested route without scanning files or changing settings."""
    if not w.players:
        return 'Load a player CSV to prepare a build.'
    nfl = w._current_sport() == 'NFL'
    deep = nfl and w.chk_nfl_contest_sim.isChecked() and w.combo_nfl_compute_mode.currentText().startswith('Deep')
    selected = bool(getattr(w, '_candidate_library', ''))
    if selected and not deep:
        return 'Selected library requires NFL Deep + SIM. Choose a timed budget or clear the library in Advanced.'
    if selected:
        route = 'validate selected library; reuse compatible screening or prepare fresh screening'
    elif deep and w.action_auto_library.isChecked() and not getattr(w, '_snapshot_replay', False):
        route = 'find a compatible library or search fresh candidates; prepare required screening'
    elif deep:
        route = 'search fresh candidates and prepare required screening'
    else:
        route = 'run the current Fast search'
    return 'Generate will check current inputs; ' + route + '; then validate and select under your current rules.'
