"""AR-01: real worker/GUI reproductions plus explicitly controlled boundaries."""
from __future__ import annotations

from test_environment import install, network_attempts
ISOLATION_ROOT = install()  # before DFS imports or QApplication construction

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import threading
import time
import unittest
from unittest import mock

from PyQt5 import QtCore, QtWidgets
import main_window as mw
from build_diagnostics import build_history_path, load_build_history, format_build_report
from learning_db import history_db_path
from saved_repair import signatures
from nfl_simulation import SimLineup


class SavedRepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.windows = []
        self.held = []
        self.network_start = len(network_attempts)
        self.patches = []
        for name, value in (("question", QtWidgets.QMessageBox.Yes), ("critical", None), ("warning", None)):
            patch = mock.patch.object(QtWidgets.QMessageBox, name, return_value=value)
            patch.start()
            self.patches.append(patch)

    def tearDown(self):
        # Unstarted real workers used only for boundary tests still retire in
        # their owning thread. No forceful thread termination or live cleanup.
        for window, thread, worker, receipt in self.held:
            if not receipt.thread_finished:
                worker.request_cancel()
                thread.started.disconnect(worker.run)
                thread.started.connect(thread.quit, QtCore.Qt.DirectConnection)
                thread.finished.connect(worker.deleteLater)
                thread.start()
        deadline = time.monotonic() + 10
        while any(not receipt.thread_finished for _, _, _, receipt in self.held) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.001)
        self.assertTrue(all(receipt.thread_finished for _, _, _, receipt in self.held))
        for window in self.windows:
            if window._build_thread is not None:
                window._cancel_lineup_build()
                self.drain(window)
            window.close()
            window.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        self.app.processEvents()
        for patch in reversed(self.patches):
            patch.stop()
        self.assertEqual(network_attempts[self.network_start:], [])

    def fixture(self, kind):
        data = json.loads((Path(__file__).parent / 'tests' / 'fixtures' / f'ar01_{kind}.json').read_text())
        players = data['players']
        lookup = {p['FlexID']: p for p in players}
        catalog = {}
        for row in data['roster_catalog']:
            members = [lookup[k] for k in row['athlete_flex_ids']]
            captain = row['scoring_identity']['captain_flex_id']
            catalog[row['roster_id']] = ({'Captain': lookup[captain], 'Flex': [p for p in members if p['FlexID'] != captain]} if kind == 'showdown' else SimLineup(members))
        states = {key: [catalog[r['roster_id']] for r in value['entries']] for key, value in data['states'].items()}
        window = mw.MainWindow()
        self.windows.append(window)
        window.players = players
        window.saved_showdown = list(states['source_A']) if kind == 'showdown' else []
        window.saved_classic = list(states['source_A']) if kind == 'classic' else []
        window.combo_build_style.setCurrentText('Balanced')
        window.combo_salary_strategy.setCurrentText('Balanced Spend')
        window.chk_nfl_contest_sim.setChecked(False)
        window.spin_portfolio_unique.setValue(1)
        window.spin_team_exposure.setValue(100)
        window.spin_game_exposure.setValue(100)
        window.chk_portfolio_balance.setChecked(False)
        window.last_portfolio_report = {'source': 'original'}
        window.last_sim_report = {'source': 'original'}
        window.last_build_timing_report = {'source': 'original'}
        window.last_build_diagnostic = {'source': 'original'}
        self.assertEqual(len(states['source_A']), 20)
        self.assertEqual(len(set(signatures(states['retained_17'], kind))), 17)
        self.assertEqual(window._portfolio_rules(), data['parameters']['portfolio_rules'])
        self.assertTrue(Path(window.app_settings.fileName()).is_relative_to(ISOLATION_ROOT))
        self.assertEqual(window.app_settings.format(), QtCore.QSettings.IniFormat)
        self.assertFalse(window.app_settings.fallbacksEnabled())
        self.assertTrue(Path(history_db_path()).is_relative_to(ISOLATION_ROOT))
        self.assertTrue(Path(build_history_path()).is_relative_to(ISOLATION_ROOT))
        return window, states

    def launch(self, window, kind, *, hold=False, cancel=False, deep_seconds=None):
        finished, errors, affinity = [], [], []
        original_start = QtCore.QThread.start
        original_handler = window._on_lineup_build_finished

        def handler(payload, **kwargs):
            affinity.append(QtCore.QThread.currentThread() is self.app.thread())
            return original_handler(payload, **kwargs)

        def start(thread, *args):
            if deep_seconds is not None:
                window._build_worker.deep_time_limit_seconds = deep_seconds
            window._build_worker.finished.connect(finished.append)
            window._build_worker.error.connect(errors.append)
            if cancel:
                window._cancel_lineup_build()
            if hold:
                self.held.append((window, thread, window._build_worker, window._build_receipt))
            else:
                original_start(thread, *args)

        with mock.patch.object(window, '_run_live_nfl_check', return_value={'sleeper_state': 'ok'}) as live, mock.patch.object(QtCore.QThread, 'start', start), mock.patch.object(window, '_on_lineup_build_finished', handler):
            window._repair_saved_lineups(kind, list(getattr(window, 'saved_' + kind)), [3, 11, 18], 50000)
            self.assertEqual(live.call_count, 1 if window._current_sport() == 'NFL' else 0)
            self.assertIsNotNone(window._build_receipt)
            if not hold:
                self.drain(window)
                self.assertEqual(affinity, [True])
        return finished, errors, window._build_receipt

    def drain(self, window):
        deadline = time.monotonic() + 90
        while window._build_thread is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.001)
        self.assertIsNone(window._build_thread, 'Owned worker did not retire within 90 seconds')
        self.app.processEvents()

    def payload(self, kind, states, state='proposal_full_20', **values):
        payload = dict(kind=kind, sport='NFL', requested=20, repair_source='saved',
                       lineups=deepcopy(states[state]), cancelled=False,
                       portfolio_report={'kind': kind, 'lineup_count': 20, 'effective_min_unique': 1},
                       sim_report={}, timing_report={'retained_count': 17, 'replacement_requested': 3})
        payload.update(values)
        return payload

    def unchanged(self, window, kind, originals, reports):
        saved = getattr(window, 'saved_' + kind)
        self.assertEqual(len(saved), len(originals))
        self.assertTrue(all(a is b for a, b in zip(saved, originals)))
        self.assertIs(window.last_portfolio_report, reports)

    def test_C02_actual_cancelled_worker_to_GUI(self):
        for kind in ('classic', 'showdown'):
            with self.subTest(kind=kind):
                window, states = self.fixture(kind)
                report = window.last_portfolio_report
                finished, errors, receipt = self.launch(window, kind, cancel=True)
                self.assertEqual(errors, [])
                self.assertEqual(len(finished), 1)
                self.assertTrue(finished[0]['cancelled'])
                # Classic's real fallback can return 20 even after cancellation.
                self.assertEqual(len(finished[0]['lineups']), 17 if kind == 'showdown' else 20)
                self.unchanged(window, kind, states['source_A'], report)
                self.assertEqual(receipt.disposition, 'cancelled')
                self.assertTrue(receipt.thread_finished)

    def test_C09_C06_C21_actual_success_applies_once_and_late_cancel_is_not_undo(self):
        for kind in ('classic', 'showdown'):
            with self.subTest(kind=kind):
                window, states = self.fixture(kind)
                revision = window._saved_revisions[kind]
                finished, errors, receipt = self.launch(window, kind)
                self.assertEqual(errors, [])
                self.assertEqual(len(finished), 1)
                self.assertFalse(finished[0]['cancelled'])
                saved = getattr(window, 'saved_' + kind)
                self.assertEqual(len(saved), 20)
                self.assertFalse(Counter(signatures(states['retained_17'], kind)) - Counter(signatures(saved, kind)))
                expected = list(finished[0]['lineups'])
                if kind == 'classic':
                    expected.sort(key=lambda lu: float(mw.lineup_grade_for_sport(lu, 'NFL', 50000)['score']), reverse=True)
                self.assertEqual(signatures(saved, kind), signatures(expected, kind))
                self.assertEqual(receipt.disposition, 'applied')
                window._cancel_lineup_build()
                window._on_lineup_build_finished(finished[0], receipt=receipt)
                window._on_lineup_build_error('late error', receipt=receipt)
                self.assertIs(getattr(window, 'saved_' + kind), saved)
                self.assertEqual(window._saved_revisions[kind], revision + 1)
                self.assertEqual(window.last_build_diagnostic['application']['status'], 'applied')

    def test_C01_preflight_cancel_and_source_change(self):
        for kind in ('classic', 'showdown'):
            window, states = self.fixture(kind)
            with mock.patch.object(window, '_ensure_live_nfl_before_build', return_value=False):
                window._repair_saved_lineups(kind, states['source_A'], [3,11,18], 50000)
            self.assertIsNone(window._build_receipt)
            self.assertEqual(getattr(window, 'saved_' + kind), states['source_A'])
            def preflight():
                window.on_clear_saved()
                return True
            with mock.patch.object(window, '_ensure_live_nfl_before_build', side_effect=preflight):
                window._repair_saved_lineups(kind, states['source_A'], [3,11,18], 50000)
            self.assertIsNone(window._build_receipt)
            self.assertEqual(getattr(window, 'saved_' + kind), [])

    def test_C03_actual_mid_generation_cancel(self):
        for kind, optimizer in (('classic', mw.MultiSportClassicOptimizer), ('showdown', mw.ShowdownOptimizer)):
            with self.subTest(kind=kind):
                window, states = self.fixture(kind)
                report = window.last_portfolio_report
                entered, release = threading.Event(), threading.Event()
                build = optimizer.build_lineups
                def gated_build(opt, *args, **kwargs):
                    progress = kwargs['progress_callback']
                    def checkpoint(done, total, text):
                        progress(done, total, text)
                        if done > 0 and not entered.is_set():
                            entered.set()
                            if not release.wait(10):
                                raise RuntimeError('test checkpoint timeout')
                    kwargs['progress_callback'] = checkpoint
                    return build(opt, *args, **kwargs)
                with mock.patch.object(optimizer, 'build_lineups', gated_build):
                    finished, errors, receipt = self.launch(window, kind, hold=True)
                    self.held.pop()
                    window._build_thread.start()
                    try:
                        self.assertTrue(entered.wait(10))
                        window._cancel_lineup_build()
                    finally:
                        release.set()
                        self.drain(window)
                self.assertEqual(errors, [])
                self.assertEqual(len(finished), 1)
                self.assertTrue(finished[0]['cancelled'])
                self.unchanged(window, kind, states['source_A'], report)
                self.assertEqual(receipt.disposition, 'cancelled')

    def test_C02_C09_supported_Classic_Deep_worker(self):
        for cancel in (True, False):
            with self.subTest(cancel=cancel):
                window, states = self.fixture('classic')
                window.chk_nfl_contest_sim.setChecked(True)
                window.spin_nfl_sim_scenarios.setValue(100)
                window.combo_nfl_compute_mode.setCurrentText('Deep (background)')
                # Use the actual accepted label, without importing PR #37 tiers.
                for index in range(window.combo_nfl_compute_mode.count()):
                    if window.combo_nfl_compute_mode.itemText(index).startswith('Deep'):
                        window.combo_nfl_compute_mode.setCurrentIndex(index)
                self.assertTrue(window.combo_nfl_compute_mode.currentText().startswith('Deep'))
                report = window.last_portfolio_report
                finished, errors, receipt = self.launch(window, 'classic', cancel=cancel, deep_seconds=10)
                self.assertEqual(errors, [])
                self.assertEqual(len(finished), 1)
                self.assertEqual(finished[0]['timing_report']['compute_mode'], 'Deep')
                if cancel:
                    self.unchanged(window, 'classic', states['source_A'], report)
                    self.assertEqual(receipt.disposition, 'cancelled')
                else:
                    self.assertEqual(len(window.saved_classic), 20)
                    self.assertEqual(receipt.disposition, 'applied')

    def test_C05_queued_success_cannot_override_GUI_cancel(self):
        class Emitter(QtCore.QObject):
            finished = QtCore.pyqtSignal(dict)
        for kind in ('classic', 'showdown'):
            window, states = self.fixture(kind)
            report = window.last_portfolio_report
            _, _, receipt = self.launch(window, kind, hold=True)
            delivery = mw.LineupBuildDelivery(window, receipt)
            emitter = Emitter()
            emitter.finished.connect(delivery.finished, QtCore.Qt.QueuedConnection)
            emitter.finished.emit(self.payload(kind, states))
            window._cancel_lineup_build()
            self.app.processEvents()
            self.assertEqual(receipt.disposition, 'cancelled')
            self.unchanged(window, kind, states['source_A'], report)

    def test_C03_C04_C05_C07_C10_C11_C12_C27_controlled_rejections(self):
        cases = [
            ('C03', 'proposal_partial_19', {'cancelled': True}, 'cancelled'),
            ('C04', 'proposal_full_20', {'cancelled': True}, 'cancelled'),
            ('C05', 'proposal_full_20', {}, 'cancelled'),
            ('C07', 'proposal_partial_19', {}, 'incomplete'),
            ('C10', 'proposal_missing_retained_20', {}, 'retained_mismatch'),
            ('C11', 'proposal_changed_retained_20', {}, 'retained_mismatch'),
            ('C12', 'proposal_full_20', {'sport': 'NBA'}, 'scope_mismatch'),
            ('C27', 'proposal_full_20', {}, 'pending'),
        ]
        for kind in ('classic', 'showdown'):
            for case, state, values, reason in cases:
                with self.subTest(kind=kind, case=case):
                    window, states = self.fixture(kind)
                    report = window.last_portfolio_report
                    _, _, receipt = self.launch(window, kind, hold=True)
                    if case == 'C05':
                        window._cancel_lineup_build()
                    window._on_lineup_build_finished(self.payload(kind, states, state, **values), receipt=None if case == 'C27' else receipt)
                    self.assertEqual(receipt.disposition, reason)
                    self.unchanged(window, kind, states['source_A'], report)

    def test_C08_partial_analytics_and_advisory_minimum_allow_complete_repair(self):
        for kind in ('classic', 'showdown'):
            window, states = self.fixture(kind)
            window.players[-1]['MinPct'] = 100
            _, _, receipt = self.launch(window, kind, hold=True)
            payload = self.payload(kind, states, sim_report={'partial': True}, portfolio_report={'effective_min_unique': 1, 'warnings': ['advisory minimum shortfall']})
            window._on_lineup_build_finished(payload, receipt=receipt)
            self.assertEqual(receipt.disposition, 'applied')
            self.assertEqual(window.last_sim_report, {'partial': True})
            self.assertNotIn('source', window.last_portfolio_report)

    def test_C13_C14_saved_mutation_hooks_preserve_newer_state(self):
        for kind in ('classic', 'showdown'):
            for action in ('add', 'remove_readd', 'clear', 'insights_remove'):
                with self.subTest(kind=kind, action=action):
                    window, states = self.fixture(kind)
                    _, _, receipt = self.launch(window, kind, hold=True)
                    hook = window._sd_checkbox_changed if kind == 'showdown' else window._cl_checkbox_changed
                    if action == 'add':
                        setattr(window, 'last_' + kind, states['current_B_added_21'])
                        hook(20, QtCore.Qt.Checked)
                    elif action == 'remove_readd':
                        setattr(window, 'last_' + kind, states['source_A'])
                        hook(19, QtCore.Qt.Unchecked)
                        hook(19, QtCore.Qt.Checked)
                        self.assertEqual(signatures(getattr(window, 'saved_' + kind), kind), receipt.source[1])
                    elif action == 'clear':
                        window.on_clear_saved()
                    else:
                        window._handle_portfolio_insights_action(kind=kind, sport='NFL', source_label='saved', lineups=states['source_A'], action='remove', indexes=[3], salary_cap=50000)
                    current = list(getattr(window, 'saved_' + kind))
                    report = window.last_portfolio_report
                    window._on_lineup_build_finished(self.payload(kind, states), receipt=receipt)
                    self.assertEqual(receipt.disposition, 'source_changed')
                    self.unchanged(window, kind, current, report)

    def test_C15_presentation_does_not_invalidate_source(self):
        for kind in ('classic', 'showdown'):
            window, states = self.fixture(kind)
            _, _, receipt = self.launch(window, kind, hold=True)
            # Main has table sorting but no PR #37 paging subsystem.
            table = window.tbl_sd if kind == 'showdown' else window.tbl_cl
            table.setSortingEnabled(True)
            table.sortItems(1, QtCore.Qt.DescendingOrder)
            window.players.reverse()
            window._on_lineup_build_finished(self.payload(kind, states), receipt=receipt)
            self.assertEqual(receipt.disposition, 'applied')

    def test_C16_C26_rule_news_slate_and_captain_changes(self):
        for kind in ('classic', 'showdown'):
            for change in ('rule', 'news', 'slate', 'role', 'news_cancel'):
                with self.subTest(kind=kind, change=change):
                    window, states = self.fixture(kind)
                    report = window.last_portfolio_report
                    _, _, receipt = self.launch(window, kind, hold=True)
                    if change == 'rule':
                        window.spin_team_exposure.setValue(50)
                    elif change.startswith('news'):
                        window.players[0]['NFLAvailability'] = 'OUT'
                        if change == 'news_cancel':
                            window._cancel_lineup_build()
                    elif change == 'slate':
                        window.players[0]['GameKey'] = 'NEW@GAME'
                    elif kind == 'showdown':
                        lineup = window.saved_showdown[0]
                        lineup['Captain'], lineup['Flex'][0] = lineup['Flex'][0], lineup['Captain']
                    else:
                        window.saved_classic[0][0] = window.players[-8]
                    current = list(getattr(window, 'saved_' + kind))
                    window._on_lineup_build_finished(self.payload(kind, states), receipt=receipt)
                    self.assertNotEqual(receipt.disposition, 'applied')
                    self.unchanged(window, kind, current, report)
                    if change.startswith('news'):
                        self.assertEqual(window.players[0]['NFLAvailability'], 'OUT')

    def test_C17_C18_C19_C20_stale_callbacks_cannot_touch_new_job(self):
        class Emitter(QtCore.QObject):
            finished = QtCore.pyqtSignal(dict)
            error = QtCore.pyqtSignal(str)
            progress = QtCore.pyqtSignal(int, int, str)
        for kind in ('classic', 'showdown'):
            window, states = self.fixture(kind)
            _, _, old = self.launch(window, kind, hold=True)
            _, _, new = self.launch(window, kind, hold=True)
            worker, thread = window._build_worker, window._build_thread
            context = window._active_build_context
            report = window.last_portfolio_report
            emitter = Emitter()
            delivery = mw.LineupBuildDelivery(window, old)
            emitter.progress.connect(delivery.progress, QtCore.Qt.QueuedConnection)
            emitter.finished.connect(delivery.finished, QtCore.Qt.QueuedConnection)
            emitter.error.connect(delivery.error, QtCore.Qt.QueuedConnection)
            window._build_progress.setValue(2)
            emitter.progress.emit(999, 999, 'obsolete')
            self.app.processEvents()
            self.assertEqual(window._build_progress.value(), 2)
            emitter.finished.emit(self.payload(kind, states))
            emitter.error.emit('old error')
            self.app.processEvents()
            # Exercise error-first too, independently of terminal suppression.
            from dataclasses import replace
            stale_error = replace(old, disposition='pending', recorded=False)
            window._on_lineup_build_error('old first error', receipt=stale_error)
            window._on_lineup_thread_finished(receipt=stale_error)
            self.assertIs(window._build_worker, worker)
            self.assertIs(window._build_thread, thread)
            self.assertIs(window._active_build_context, context)
            self.assertEqual(new.disposition, 'pending')
            self.unchanged(window, kind, states['source_A'], report)
            records = load_build_history()
            old_record = next(row for row in records if row.get('application', {}).get('job_id') == old.job_id)
            self.assertEqual(old_record['application']['status'], 'not_applied')
            self.assertIn('not_applied', format_build_report(old_record))

    def test_C22_nested_worker_graph_is_detached(self):
        for kind in ('classic', 'showdown'):
            window, states = self.fixture(kind)
            original = states['source_A'][0]
            player = original['Captain'] if kind == 'showdown' else original[0]
            player['probe'] = {'nested': [1, 2]}
            if kind == 'classic':
                original.sim_metrics = {'probe': [1]}
                original.sim_top_hits = {1, 2}
            _, _, receipt = self.launch(window, kind, hold=True)
            worker = window._build_worker
            retained = worker.retained_lineups[0]
            detached = retained['Captain'] if kind == 'showdown' else retained[0]
            self.assertIs(next(p for p in worker.players if p['FlexID'] == detached['FlexID']), detached)
            detached['probe']['nested'].append(3)
            if kind == 'classic':
                retained.sim_metrics['probe'].append(2)
                retained.sim_top_hits.add(3)
                self.assertEqual(original.sim_metrics, {'probe': [1]})
                self.assertEqual(original.sim_top_hits, {1, 2})
            self.assertEqual(player['probe'], {'nested': [1, 2]})
            window._cancel_lineup_build()
            window._on_lineup_build_finished(self.payload(kind, states), receipt=receipt)
            self.assertIs(getattr(window, 'saved_' + kind)[0], original)

    def test_C22_fixed_work_detachment_preserves_worker_results(self):
        for kind in ('classic', 'showdown'):
            with self.subTest(kind=kind):
                window, states = self.fixture(kind)
                outputs = []
                for detach in (False, True):
                    players, retained = deepcopy((window.players, states['retained_17']))
                    if detach:
                        players, retained = deepcopy((players, retained))
                    worker = mw.LineupBuildWorker(players, kind=kind, sport='NFL', num_lineups=20,
                                                 salary_cap=50000, build_style='Balanced', salary_strategy='Balanced Spend',
                                                 portfolio_rules=window._portfolio_rules(), sim_enabled=False,
                                                 retained_lineups=retained, repair_source='saved')
                    results, errors = [], []
                    worker.finished.connect(results.append)
                    worker.error.connect(errors.append)
                    worker.run()
                    self.assertEqual(errors, [])
                    self.assertEqual(len(results), 1)
                    outputs.append(results[0])
                self.assertEqual(signatures(outputs[0]['lineups'], kind), signatures(outputs[1]['lineups'], kind))
                self.assertEqual(outputs[0]['candidate_count'], outputs[1]['candidate_count'])
                reports = [deepcopy(row['portfolio_report']) for row in outputs]
                for report in reports:
                    # Elapsed wall time is not a numerical model output.
                    self.assertGreaterEqual(report.pop('refinement_seconds'), 0)
                self.assertEqual(reports[0], reports[1])

    def test_invalid_rosters_and_hard_rules_rejected_without_changing_selection(self):
        for kind in ('classic', 'showdown'):
            for invalid in ('salary', 'role', 'maximum', 'lock', 'fade'):
                with self.subTest(kind=kind, invalid=invalid):
                    window, states = self.fixture(kind)
                    if invalid == 'maximum':
                        window.players[0]['MaxPct'] = 0
                    if invalid == 'lock':
                        window.players[-1]['LockFlex'] = True
                    if invalid == 'fade':
                        window.players[0]['FadeFlex'] = True
                    report = window.last_portfolio_report
                    _, _, receipt = self.launch(window, kind, hold=True)
                    payload = self.payload(kind, states)
                    if invalid in ('salary', 'role'):
                        retained = set(signatures(states['retained_17'], kind))
                        replacement = next(lu for lu in payload['lineups'] if signatures([lu], kind)[0] not in retained)
                        if invalid == 'salary':
                            player = replacement['Captain'] if kind == 'showdown' else replacement[0]
                            player['CptSalary' if kind == 'showdown' else 'FlexSalary'] = 100000
                        elif kind == 'showdown':
                            replacement['Flex'].pop()
                        else:
                            replacement.pop()
                    window._on_lineup_build_finished(payload, receipt=receipt)
                    self.assertEqual(receipt.disposition, 'validation_failed')
                    self.unchanged(window, kind, states['source_A'], report)

    def test_C23_C24_C25_record_prepare_and_render_failures(self):
        for kind in ('classic', 'showdown'):
            for failure in ('record', 'record_cancel', 'prepare', 'render'):
                with self.subTest(kind=kind, failure=failure):
                    window, states = self.fixture(kind)
                    report = window.last_portfolio_report
                    _, _, receipt = self.launch(window, kind, hold=True)
                    target = {'record': 'save_build_diagnostic', 'record_cancel': 'save_build_diagnostic', 'prepare': 'validate_proposal'}.get(failure)
                    if failure == 'record_cancel':
                        window._cancel_lineup_build()
                    patch = mock.patch.object(mw, target, side_effect=OSError('injected')) if target else mock.patch.object(window, '_refresh_saved_tables', side_effect=RuntimeError('injected'))
                    with patch:
                        window._on_lineup_build_finished(self.payload(kind, states), receipt=receipt)
                    if failure in ('prepare', 'record_cancel'):
                        self.unchanged(window, kind, states['source_A'], report)
                        self.assertEqual(receipt.disposition, 'preparation_failed' if failure == 'prepare' else 'cancelled')
                    else:
                        self.assertEqual(receipt.disposition, 'applied')
                        self.assertEqual(len(getattr(window, 'saved_' + kind)), 20)
                        self.assertNotIn('source', window.last_portfolio_report)
                        self.assertIn('failed', window.status.currentMessage())
                        if failure == 'record':
                            self.assertEqual(window.last_build_diagnostic['application']['status'], 'applied')
                        if failure == 'render':
                            committed = getattr(window, 'saved_' + kind)
                            window._refresh_saved_tables()
                            self.assertIs(getattr(window, 'saved_' + kind), committed)
                            self.assertEqual((window.tbl_saved_sd if kind == 'showdown' else window.tbl_saved_cl).rowCount(), 20)

    def test_C28_ordinary_partial_build_keeps_saved_state(self):
        for kind in ('classic', 'showdown'):
            window, states = self.fixture(kind)
            payload = self.payload(kind, states, 'retained_17', repair_source='', cancelled=True)
            window._on_lineup_build_finished(payload)
            self.assertEqual(signatures(getattr(window, 'saved_' + kind), kind), signatures(states['source_A'], kind))
            self.assertEqual(len(getattr(window, 'last_' + kind)), 17)

    def test_other_supported_sports_real_cancel_and_success(self):
        for sport in ('NBA', 'WNBA', 'MLB'):
            for cancel in (True, False):
                with self.subTest(sport=sport, cancel=cancel):
                    window, _ = self.fixture('classic')
                    window.combo_sport.setCurrentText(sport)
                    slots = mw.get_roster_slots_for_sport(sport)
                    window.players = []
                    groups = []
                    for slot_index, slot in enumerate(slots):
                        position = {'UTIL': 'G' if sport == 'WNBA' else 'PG', 'G': 'PG' if sport == 'NBA' else 'G', 'F': 'SF' if sport == 'NBA' else 'F'}.get(slot, slot)
                        group = []
                        for variant in range(5):
                            player = dict(Name=f'Test {sport} {slot_index} {variant}', FlexID=f'TEST_{sport}_{slot_index}_{variant}', Position=position,
                                          FlexSalary=int(48000 / len(slots)), FlexProjection=20 + variant,
                                          Team=f'T{slot_index % 2 + 1}', GameKey='T1@T2', NFLAvailability='ACTIVE')
                            window.players.append(player)
                            group.append(player)
                        groups.append(group)
                    originals = [SimLineup([group[(index // (5 ** slot_index)) % 5] for slot_index, group in enumerate(groups)]) for index in range(20)]
                    window.saved_classic = list(originals)
                    report = window.last_portfolio_report
                    finished, errors, receipt = self.launch(window, 'classic', cancel=cancel)
                    self.assertEqual(errors, [])
                    self.assertEqual(len(finished), 1)
                    if cancel:
                        self.unchanged(window, 'classic', originals, report)
                        self.assertEqual(receipt.disposition, 'cancelled')
                    else:
                        self.assertEqual(receipt.disposition, 'applied')
                        self.assertEqual(len(window.saved_classic), 20)

    def test_close_cooperatively_cancels_running_worker_before_destroying_window(self):
        for kind in ('classic', 'showdown'):
            window, states = self.fixture(kind)
            entered, release = threading.Event(), threading.Event()
            run = mw.LineupBuildWorker.run
            def gated_run(worker):
                entered.set()
                if not release.wait(10):
                    worker.error.emit('test barrier timeout')
                    return
                run(worker)
            with mock.patch.object(mw.LineupBuildWorker, 'run', gated_run):
                _, _, receipt = self.launch(window, kind, hold=True)
                # Cleanup must disconnect the same bound function if setup fails.
                held = self.held.pop()
                window._build_thread.start()
                self.assertTrue(entered.wait(5))
                window.close()
                self.assertFalse(window._accept_build_results)
                self.assertTrue(receipt.cancelled)
                release.set()
                self.drain(window)
            self.assertEqual(receipt.disposition, 'closing')
            self.assertTrue(receipt.thread_finished)
            self.assertEqual(signatures(getattr(window, 'saved_' + kind), kind), signatures(states['source_A'], kind))


if __name__ == '__main__':
    unittest.main()
