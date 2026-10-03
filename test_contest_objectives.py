"""CO-01 intent storage, historical evidence, UI, and compatibility contracts."""
from test_environment import install, network_attempts
install()

import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PyQt5 import QtCore, QtWidgets
from contest_objectives import (OBJECTIVES, TOURNAMENT, DOUBLE_UP, MULTIPLIER,
    normalize_objective, objective_label, recorded_objective, objective_evidence_label,
    objective_counts, FRAMEWORK_NOTE)
from contest_profiles import normalize_contest_profile, dump_profiles_json, load_profiles_json
from build_recipes import normalize_recipe, dump_recipes_json, load_recipes_json
from build_snapshots import (create_snapshot, validate_snapshot, save_snapshot, load_snapshot,
    snapshot_objective, fingerprint)
from candidate_library import (candidate_generation_id, initialize, metadata, connect,
    load_candidates, roster_keys)
from build_diagnostics import (create_build_diagnostic, format_build_report,
    save_build_diagnostic, load_build_history)
from build_archives import save_build_archive, archive_folder
from learning_db import init_db, record_export, generate_learning_report
from main_window import ContestProfileDialog, MainWindow, LineupBuildWorker
from test_nfl_logic import _fixture_players
from test_showdown_performance import _showdown_players


def profile(**values):
    return dict(name='Named contest', field_size=100, entry_fee=10,
                user_entries=4, payouts='1 = 200\n2-10 = 20', **values)


def snap(objective=TOURNAMENT, kind='classic'):
    players = _fixture_players() if kind == 'classic' else _showdown_players()
    return create_snapshot(players, dict(sport='NFL', contest_kind=kind,
        salary_strategy='Flexible', contest_objective=objective), {})


def legacy_snapshot(kind='classic'):
    value = snap(kind=kind)
    value['inputs']['recipe'].pop('contest_objective')
    value['input_id'] = fingerprint(value['inputs'])
    return value


class ObjectiveStorageTests(unittest.TestCase):
    def test_canonical_values_and_labels(self):
        for value, label in zip(OBJECTIVES, ('Tournament', 'Double-Up', 'Multiplier')):
            self.assertEqual(normalize_objective(value), value)
            self.assertEqual(objective_label(value), label)

    def test_safe_aliases(self):
        for value, canonical in [('Tournament', TOURNAMENT), ('GPP', TOURNAMENT),
                ('Double-Up', DOUBLE_UP), ('double up', DOUBLE_UP), ('double_up', DOUBLE_UP),
                ('Multiplier', MULTIPLIER)]:
            self.assertEqual(normalize_objective(value), canonical)

    def test_execution_missing_invalid_default_is_canonical(self):
        for value in (None, '', 'cash', '50/50', '3x', 4, {}, 'contest Double-Up'):
            self.assertEqual(normalize_objective(value), TOURNAMENT)
            self.assertIsNone(recorded_objective(value))
        self.assertEqual(normalize_objective(None, default='double up'), DOUBLE_UP)
        self.assertEqual(normalize_objective(None, default='invalid'), TOURNAMENT)

    def test_historical_absence_is_not_execution_default(self):
        self.assertEqual(objective_evidence_label(None), 'Not recorded')
        self.assertEqual(objective_evidence_label('unknown'), 'Not recorded')
        self.assertEqual(objective_counts([None, *OBJECTIVES]),
                         {'Tournament': 1, 'Double-Up': 1, 'Multiplier': 1, 'Not recorded': 1})

    def test_legacy_profile_defaults_without_payout_changes(self):
        value = normalize_contest_profile(profile())
        self.assertEqual(value['objective'], TOURNAMENT)
        self.assertEqual((value['cash_places'], value['prize_pool'], value['top_prize']), (10, 380, 200))
        for objective in OBJECTIVES:
            changed = normalize_contest_profile(profile(objective=objective))
            self.assertEqual({k: v for k, v in changed.items() if k != 'objective'},
                             {k: v for k, v in value.items() if k != 'objective'})

    def test_profile_roundtrip_and_no_mutation(self):
        original = profile(objective='double up')
        before = copy.deepcopy(original)
        loaded = load_profiles_json(dump_profiles_json({'Named contest': original}))
        self.assertEqual(loaded['Named contest']['objective'], DOUBLE_UP)
        self.assertEqual(original, before)

    def test_recipe_execution_default(self):
        self.assertEqual(normalize_recipe({})['contest_objective'], TOURNAMENT)
        self.assertEqual(load_recipes_json('{"old": {}}')['old']['contest_objective'], TOURNAMENT)

    def test_recipe_all_objectives_roundtrip_no_other_changes(self):
        base = normalize_recipe(dict(sport='NFL', contest_kind='classic', requested_lineups=5))
        for objective in OBJECTIVES:
            original = dict(base, contest_objective=objective)
            before = copy.deepcopy(original)
            loaded = load_recipes_json(dump_recipes_json({'saved': original}))['saved']
            self.assertEqual(loaded, original)
            self.assertEqual(original, before)

    def test_new_snapshot_records_objective_and_schema_one(self):
        with tempfile.TemporaryDirectory() as folder:
            for objective in OBJECTIVES:
                value = snap(objective)
                path = Path(folder) / 'snapshot.json'
                save_snapshot(str(path), value)
                self.assertEqual(load_snapshot(str(path)), value)
                self.assertEqual(value['schema_version'], 1)
                self.assertEqual(snapshot_objective(value), objective)

    def test_snapshot_creation_defaults_without_mutating_inputs(self):
        recipe = dict(sport='NFL', contest_kind='classic')
        players = _fixture_players()
        before = copy.deepcopy((recipe, players))
        value = create_snapshot(players, recipe, {})
        self.assertEqual(value['inputs']['recipe']['contest_objective'], TOURNAMENT)
        self.assertEqual((recipe, players), before)

    def test_legacy_snapshot_valid_read_only_and_execution_default(self):
        original = legacy_snapshot()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'old.json'
            save_snapshot(str(path), original)
            before = path.read_bytes()
            loaded = load_snapshot(str(path))
            self.assertEqual(snapshot_objective(loaded), TOURNAMENT)
            self.assertNotIn('contest_objective', loaded['inputs']['recipe'])
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(loaded, original)

    def test_snapshot_contest_only_metadata_and_integrity(self):
        value = legacy_snapshot()
        value['inputs']['contest']['objective'] = DOUBLE_UP
        value['input_id'] = fingerprint(value['inputs'])
        self.assertEqual(snapshot_objective(validate_snapshot(value)), DOUBLE_UP)
        value['inputs']['recipe']['contest_objective'] = MULTIPLIER
        with self.assertRaisesRegex(ValueError, 'input ID'):
            validate_snapshot(value)

    def test_candidate_identity_ignores_both_objective_locations_only(self):
        identities, full = [], []
        for objective in OBJECTIVES:
            value = snap(objective)
            value['inputs']['contest'].update(objective=objective, contest_objective=objective)
            value['input_id'] = fingerprint(value['inputs'])
            before = copy.deepcopy(value)
            identities.append(candidate_generation_id(value))
            full.append(value['input_id'])
            self.assertEqual(value, before)
        self.assertEqual(len(set(identities)), 1)
        self.assertEqual(len(set(full)), 3)
        self.assertEqual(identities[0], candidate_generation_id(legacy_snapshot()))

    def test_candidate_identity_retains_meaningful_inputs(self):
        original = snap()
        changes = [('recipe', 'salary_cap', 49000), ('recipe', 'contest_kind', 'showdown'),
                   ('recipe', 'ownership_weight', .7), ('recipe', 'build_style', 'Chalk'),
                   ('rules', 'min_unique', 3), ('calibration', 'ownership_exponent', .8),
                   ('contest', 'field_size', 400)]
        for section, key, value in changes:
            changed = copy.deepcopy(original)
            changed['inputs'][section][key] = value
            changed['input_id'] = fingerprint(changed['inputs'])
            self.assertNotEqual(candidate_generation_id(changed), candidate_generation_id(original))
        for key, value in [('FlexSalary', 123), ('GameInfo', 'next slate'),
                           ('FlexID', 'new ID'), ('FlexProjection', 200), ('LockFlex', True)]:
            changed = copy.deepcopy(original)
            changed['inputs']['players'][0][key] = value
            changed['input_id'] = fingerprint(changed['inputs'])
            self.assertNotEqual(candidate_generation_id(changed), candidate_generation_id(original))

    def test_candidate_library_resume_across_objectives_keeps_provenance(self):
        for kind in ('classic', 'showdown'):
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'library.db'
                original = snap(kind=kind)
                initialize(path, original)
                before = path.read_bytes()
                for objective in OBJECTIVES:
                    initialize(path, snap(objective, kind))
                    info = metadata(path)
                    self.assertEqual(info['snapshot'], original)
                    self.assertEqual(info['input_id'], original['input_id'])
                    self.assertEqual(info['candidate_generation_id'], candidate_generation_id(original))
                self.assertEqual(path.read_bytes(), before)

    def test_legacy_library_derives_identity_without_migration_or_code_bypass(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'library.db'
            original = legacy_snapshot()
            initialize(path, original)
            with connect(path) as con:
                con.execute("DELETE FROM library_meta WHERE key='candidate_generation_id'")
            before = path.read_bytes()
            initialize(path, snap(DOUBLE_UP))
            self.assertEqual(path.read_bytes(), before)
            with patch('candidate_library.code_id', return_value='another version'):
                with self.assertRaisesRegex(ValueError, 'different inputs or app code'):
                    initialize(path, snap())
                with self.assertRaisesRegex(ValueError, 'different app code'):
                    load_candidates(path, original['inputs']['players'], kind='classic', salary_cap=50000)

    def test_real_candidates_reuse_across_objectives_and_current_rules_still_apply(self):
        from optimizers import MultiSportClassicOptimizer, ShowdownOptimizer
        for kind, optimizer in [('classic', MultiSportClassicOptimizer), ('showdown', ShowdownOptimizer)]:
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'library.db'
                original = snap(kind=kind)
                players = original['inputs']['players']
                actual = optimizer(players).build_lineups(2)
                self.assertTrue(actual)
                initialize(path, original)
                with connect(path) as con:
                    for row in actual:
                        encoded = json.dumps(roster_keys(row, kind))
                        con.execute('INSERT INTO candidates VALUES (?,?,?,?,?)', (encoded, encoded, 0, 'Strategic', 1337))
                expected = sorted(roster_keys(row, kind) for row in actual)
                for objective in OBJECTIVES:
                    initialize(path, snap(objective, kind))
                    rows, report = load_candidates(path, players, kind=kind, salary_cap=50000,
                                                   salary_strategy='Flexible')
                    self.assertEqual(sorted(roster_keys(row, kind) for row in rows), expected)
                    self.assertEqual(report['input_id'], original['input_id'])
                changed = copy.deepcopy(players)
                for player in changed:
                    player['FadeFlex'] = True
                with self.assertRaisesRegex(ValueError, 'No saved candidates'):
                    load_candidates(path, changed, kind=kind, salary_cap=50000)
                with self.assertRaisesRegex(ValueError, 'No saved candidates'):
                    load_candidates(path, players, kind=kind, salary_cap=50000,
                        rules={'groups': [{'type': 'at_least_one', 'player_keys': ['absent-player']}]})
                changed = copy.deepcopy(players)
                changed.append(dict(players[0], FlexID='new-locked-player', LockFlex=True))
                with self.assertRaisesRegex(ValueError, 'different player slate'):
                    load_candidates(path, changed, kind=kind, salary_cap=50000)

    def test_library_metadata_tamper_and_generation_change_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'library.db'
            initialize(path, snap())
            changed = snap()
            changed['inputs']['recipe']['salary_cap'] = 49000
            changed['input_id'] = fingerprint(changed['inputs'])
            with self.assertRaisesRegex(ValueError, 'different inputs'):
                initialize(path, changed)
            with connect(path) as con:
                con.execute("UPDATE library_meta SET value='bad' WHERE key='candidate_generation_id'")
            with self.assertRaisesRegex(ValueError, 'identity'):
                metadata(path)

    def test_worker_context_canonical_and_profile_not_mutated(self):
        original = profile(objective='double up')
        before = copy.deepcopy(original)
        worker = LineupBuildWorker([], kind='classic', num_lineups=1, salary_cap=50000,
                                  contest_profile=original)
        self.assertEqual(worker.contest_objective, DOUBLE_UP)
        self.assertEqual(original, before)
        worker = LineupBuildWorker([], kind='showdown', num_lineups=1, salary_cap=50000,
                                  contest_objective='multiplier')
        self.assertEqual(worker.contest_objective, MULTIPLIER)

    def test_diagnostic_roundtrip_objective_is_not_warning(self):
        with tempfile.TemporaryDirectory() as folder:
            for objective in OBJECTIVES:
                record = create_build_diagnostic(context={'settings': {'contest_objective': objective}},
                                                 timing_report={})
                path = str(Path(folder) / 'history.json')
                saved = save_build_diagnostic(record, path=path)
                self.assertEqual(load_build_history(path=path)[0], saved)
                self.assertEqual(saved['contest_objective'], objective)
                text = format_build_report(saved)
                self.assertIn('Objective: ' + objective_label(objective), text)
                if objective != TOURNAMENT:
                    self.assertIn('strategy framework only', text)

    def test_legacy_diagnostic_remains_unrecorded_without_rewriting(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'history.json'
            path.write_text(json.dumps({'schema_version': 1, 'records': [{'status': 'completed'}]}))
            before = path.read_bytes()
            old = load_build_history(path=str(path))[0]
            self.assertIn('Objective: Not recorded', format_build_report(old))
            self.assertNotIn('contest_objective', old)
            self.assertEqual(path.read_bytes(), before)

    def test_archive_records_intent_and_keeps_exact_snapshot(self):
        from optimizers import ShowdownLineup
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'DFS_OPTIMIZER_DATA_DIR': folder}):
            value = snap(MULTIPLIER, 'showdown')
            source = archive_folder().parent / 'snapshots' / (value['input_id'] + '.json')
            save_snapshot(str(source), value)
            before = source.read_bytes()
            ps = value['inputs']['players']
            row = ShowdownLineup(ps[0], ps[1:3] + ps[18:21])
            saved = save_build_archive({'kind': 'showdown', 'lineups': [row]},
                dict(input_id=value['input_id'], settings={'contest_objective': MULTIPLIER}), {})
            with zipfile.ZipFile(archive_folder() / saved['filename']) as archive:
                meta = json.loads(archive.read('manifest.json'))['metadata']
                self.assertEqual(meta['contest_objective'], MULTIPLIER)
                self.assertEqual(json.loads(archive.read('input-snapshot.json')), value)
            self.assertEqual(source.read_bytes(), before)

    def test_legacy_archive_snapshot_evidence_not_inferred(self):
        from review_build_evidence import snapshot, explanation
        value = legacy_snapshot()
        value['inputs']['contest'] = {'name': 'Double-Up', 'field_size': 100, 'entry_fee': 5}
        value['input_id'] = fingerprint(value['inputs'])
        self.assertIsNone(explanation(snapshot(value), 0)['contest_objective'])
        self.assertEqual(objective_evidence_label({}.get('contest_objective')), 'Not recorded')

    def test_isolated_network_remains_unused(self):
        self.assertEqual(network_attempts, [])


class ObjectiveHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / 'history.sqlite')

    def tearDown(self):
        self.temp.cleanup()

    def legacy_database(self):
        with connect(self.path) as con:
            con.execute('''CREATE TABLE exports (export_id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                app_version TEXT, sport TEXT, contest_type TEXT, salary_cap REAL, lineup_count INTEGER,
                export_path TEXT, build_style TEXT, own_mode TEXT, own_weight REAL, field_preset TEXT,
                mlb_stack_pref TEXT, salary_strategy TEXT, validation_json TEXT)''')
            con.execute('''INSERT INTO exports (export_id,created_at,sport,contest_type,lineup_count,
                export_path,field_preset,validation_json) VALUES ('legacy','2026-01-01','NFL','classic',0,
                'Double-Up 3x.csv','Single Entry','{"contest_name":"Double-Up", "entry_fee":10,"roi":30}')''')

    def test_additive_migration_existing_export_remains_null(self):
        self.legacy_database()
        with connect(self.path) as con:
            before = con.execute('SELECT * FROM exports').fetchall()
            init_db(con)
            init_db(con)
            after = con.execute('SELECT * FROM exports').fetchall()
            self.assertEqual(after[0][:-1], before[0])
            self.assertIsNone(after[0][-1])
            columns = {r[1]: r for r in con.execute('PRAGMA table_info(exports)')}
            self.assertEqual(columns['contest_objective'][2], 'TEXT')
            self.assertEqual(columns['contest_objective'][3], 0)
            self.assertIsNone(columns['contest_objective'][4])

    def export(self, objective):
        return record_export(kind='classic', sport='NFL', lineups=[], rows=[], salary_cap=50000,
            export_path='example.csv', validation={}, settings={'contest_objective': objective}, db_path=self.path)

    def test_new_exports_store_actual_canonical_objective(self):
        for value in ('Tournament', 'Double-Up', 'multiplier'):
            self.export(value)
        with connect(self.path) as con:
            self.assertEqual([r[0] for r in con.execute('SELECT contest_objective FROM exports')], list(OBJECTIVES))
            self.assertEqual([r[0] for r in con.execute('SELECT validation_json FROM exports')], ['{}'] * 3)

    def test_new_legacy_caller_defaults_tournament(self):
        self.export(None)
        with connect(self.path) as con:
            self.assertEqual(con.execute('SELECT contest_objective FROM exports').fetchone()[0], TOURNAMENT)

    def test_mixed_history_report_never_infers_legacy_objective(self):
        self.legacy_database()
        for value in OBJECTIVES:
            self.export(value)
        report = generate_learning_report(db_path=self.path)
        self.assertEqual(report['contest_objectives'],
            {'Tournament': 1, 'Double-Up': 1, 'Multiplier': 1, 'Not recorded': 1})
        for label in report['contest_objectives']:
            self.assertIn(f'- {label}: 1 exports', report['text'])
        with connect(self.path) as con:
            self.assertIsNone(con.execute("SELECT contest_objective FROM exports WHERE export_id='legacy'").fetchone()[0])


class ObjectiveUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_dialog_defaults_to_tournament(self):
        dialog = ContestProfileDialog({})
        try:
            self.assertEqual(dialog.objective, TOURNAMENT)
            self.assertEqual(dialog.objective_combo.count(), 3)
        finally:
            dialog.close()

    def test_dialog_saved_profile_restores_objective(self):
        saved = normalize_contest_profile(profile(objective=DOUBLE_UP))
        dialog = ContestProfileDialog({'Named contest': saved})
        try:
            dialog.profile_combo.setCurrentIndex(1)
            self.assertEqual(dialog.objective, DOUBLE_UP)
            self.assertEqual(dialog.objective_note.text(), FRAMEWORK_NOTE)
            dialog.objective_combo.setCurrentIndex(2)
            dialog._save_and_use()
            self.assertEqual(dialog.profiles['Named contest']['objective'], MULTIPLIER)
        finally:
            dialog.close()

    def test_open_dialog_uses_current_objective_after_recipe(self):
        saved = normalize_contest_profile(profile())
        dialog = ContestProfileDialog({'Named contest': saved}, 'Named contest', objective=DOUBLE_UP)
        try:
            self.assertEqual(dialog.objective, DOUBLE_UP)
            dialog.profile_combo.setCurrentIndex(0)
            dialog.profile_combo.setCurrentIndex(1)
            self.assertEqual(dialog.objective, TOURNAMENT)
        finally:
            dialog.close()

    def test_preset_only_preserves_objective_and_settings_persistence(self):
        window = MainWindow()
        try:
            self.assertEqual(window._current_contest_objective(), TOURNAMENT)
            for objective in (DOUBLE_UP, MULTIPLIER):
                dialog = ContestProfileDialog({}, parent=window, objective=objective)
                dialog._use_preset_only()
                with patch('main_window.ContestProfileDialog', return_value=dialog), \
                        patch.object(dialog, 'exec_', return_value=QtWidgets.QDialog.Accepted):
                    window.on_contest_profiles()
                self.assertIsNone(window._active_contest_profile())
                self.assertEqual(window.app_settings.value('contest/objective'), objective)
                self.assertEqual(window._current_build_recipe()['contest_objective'], objective)
            # Reopen the same explicit INI store through the isolated real QSettings.
            window.app_settings = QtCore.QSettings('objective-reopen.ini', QtCore.QSettings.IniFormat)
            window._set_contest_objective(DOUBLE_UP)
            window.app_settings = QtCore.QSettings('objective-reopen.ini', QtCore.QSettings.IniFormat)
            self.assertEqual(window._current_contest_objective(), DOUBLE_UP)
        finally:
            window.close()

    def test_recipe_restore_changes_only_objective_and_legacy_defaults(self):
        window = MainWindow()
        try:
            baseline = window._current_build_recipe()
            for objective in OBJECTIVES:
                value = dict(baseline, contest_objective=objective)
                window._apply_build_recipe('Saved', value)
                self.assertEqual(window._current_build_recipe(), value)
            baseline.pop('contest_objective')
            window._apply_build_recipe('Legacy', baseline)
            self.assertEqual(window._current_contest_objective(), TOURNAMENT)
        finally:
            window.close()

    def test_snapshot_replay_restores_new_and_legacy_objective(self):
        window = MainWindow()
        try:
            for value, objective in [(snap(DOUBLE_UP), DOUBLE_UP), (legacy_snapshot(), TOURNAMENT)]:
                before = copy.deepcopy(value)
                window._restore_snapshot(value)
                self.assertEqual(window._current_contest_objective(), objective)
                self.assertEqual(value, before)
        finally:
            window.close()

    def test_build_captures_objective_before_worker_runs(self):
        window = MainWindow()
        try:
            window._restore_snapshot(snap(MULTIPLIER))
            with patch('main_window.QtCore.QThread'), patch('main_window.LineupBuildWorker') as worker:
                window._start_lineup_build(kind='classic', sport='NFL', num=4, cap=50000)
                self.assertEqual(worker.call_args.kwargs['contest_objective'], MULTIPLIER)
                self.assertEqual(window._active_build_context['settings']['contest_objective'], MULTIPLIER)
        finally:
            window._build_thread = None
            window._build_worker = None
            window.close()
