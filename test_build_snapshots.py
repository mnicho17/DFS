import copy
import os
import tempfile
import unittest
from unittest import mock
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5 import QtWidgets
from build_snapshots import create_snapshot, fingerprint, load_snapshot, save_snapshot
from build_diagnostics import format_build_comparison
from main_window import MainWindow
from test_nfl_logic import _fixture_players
from test_showdown_performance import _showdown_players


class BuildSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {'DFS_OPTIMIZER_DATA_DIR': self.temp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_build_autosaves_exact_worker_players_and_input_id(self):
        window = MainWindow()
        try:
            window.combo_sport.setCurrentText('NFL')
            window.tabs_lineups.setCurrentIndex(1)
            window.players = _fixture_players()
            window._restore_snapshot(window._capture_snapshot(calibration={}, contest={}))
            with mock.patch('main_window.QtCore.QThread'), mock.patch('main_window.LineupBuildWorker') as worker:
                window._start_lineup_build(kind='classic', sport='NFL', num=8, cap=50000)
                input_id = window._active_build_context['input_id']
                self.assertTrue(input_id)
                from build_diagnostics import build_history_path
                snapshot = load_snapshot(os.path.join(os.path.dirname(build_history_path()), 'snapshots', input_id + '.json'))
                self.assertEqual(snapshot['inputs']['players'], worker.call_args.args[0])
                self.assertEqual(snapshot['inputs']['recipe']['requested_lineups'], 8)
                self.assertIsNot(window.players[0], worker.call_args.args[0][0])
        finally:
            window._build_thread = None
            window._build_worker = None
            window.close()

    def test_file_roundtrip_and_modified_content_rejected(self):
        value = create_snapshot(_fixture_players(), {'sport': 'NFL', 'contest_kind': 'classic'}, {})
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'snapshot.json')
            save_snapshot(path, value)
            self.assertEqual(load_snapshot(path), value)
            altered = copy.deepcopy(value)
            altered['inputs']['players'][0]['FlexProjection'] += 1
            with self.assertRaises(ValueError):
                save_snapshot(path, altered)
            self.assertEqual(load_snapshot(path), value)

    def test_fingerprint_includes_ownership_rules_and_calibration(self):
        original = create_snapshot(_fixture_players(), {'sport': 'NFL', 'contest_kind': 'classic'}, {})
        for section, key in [('rules', 'min_unique'), ('calibration', 'ownership_exponent')]:
            altered = copy.deepcopy(original['inputs'])
            altered[section][key] = 2
            self.assertNotEqual(original['input_id'], fingerprint(altered))
        altered = copy.deepcopy(original['inputs'])
        altered['players'][0]['ProjOwnPct'] = 99
        self.assertNotEqual(original['input_id'], fingerprint(altered))

    def test_both_contests_restore_exact_inputs_without_external_refresh(self):
        for kind, fixture in [('classic', _fixture_players), ('showdown', _showdown_players)]:
            window = MainWindow()
            try:
                window.combo_sport.setCurrentText('NFL')
                window.tabs_lineups.setCurrentIndex(0 if kind == 'showdown' else 1)
                window.players = fixture()
                window.players[0].update(MinPct=12, MaxPct=60, ProjOwnPct=22.5)
                snapshot = window._capture_snapshot(calibration={'ownership_exponent': .7}, contest={})
                with mock.patch('main_window.apply_auto_nfl_context', side_effect=AssertionError('external refresh')), \
                     mock.patch.object(window, 'recalc_ownership_quick', side_effect=AssertionError('ownership changed')), \
                     mock.patch.object(window, '_run_live_nfl_check', side_effect=AssertionError('live check')):
                    window._restore_snapshot(snapshot)
                    self.assertTrue(window._ensure_live_nfl_before_build())
                    self.assertEqual(window._capture_snapshot()['inputs'], snapshot['inputs'])
                    self.assertTrue(window._snapshot_replay)
            finally:
                window.close()

    def test_comparison_never_calls_unknown_inputs_equal(self):
        self.assertIn('unavailable', format_build_comparison({}, {}))
        self.assertIn('Matching build inputs', format_build_comparison({'input_id': 'a'}, {'input_id': 'a'}))
        self.assertIn('Different build inputs', format_build_comparison({'input_id': 'a'}, {'input_id': 'b'}))

    def test_projection_override_sorted_table_and_replay_both_contests(self):
        from projection_sources import initialize_projection
        from PyQt5 import QtCore
        for kind, fixture in [('classic', _fixture_players), ('showdown', _showdown_players)]:
            window = MainWindow()
            try:
                window.combo_sport.setCurrentText('NFL')
                window.tabs_lineups.setCurrentIndex(0 if kind == 'showdown' else 1)
                window.players = fixture()
                for p in window.players:
                    initialize_projection(p, historical=0, imported=10)
                window._refresh_players_table()
                window.tbl_players.sortItems(0, QtCore.Qt.DescendingOrder)
                window.tbl_players.selectRow(0)
                selected = window.players[window._get_selected_player_rows()[0]]
                with mock.patch('main_window.QtWidgets.QInputDialog.getText', return_value=('17.5', True)):
                    window._edit_player_projection(0, 5)
                self.assertEqual(selected['FlexProjection'], 17.5)
                self.assertEqual(sum(p.get('ManualProjection') == 17.5 for p in window.players), 1)
                snapshot = window._capture_snapshot(calibration={}, contest={})
                window._restore_snapshot(snapshot)
                self.assertEqual(snapshot['inputs'], window._capture_snapshot()['inputs'])
                self.assertTrue(any(p.get('ProjectionSource') == 'Manual override' for p in window.players))
            finally:
                window.close()


if __name__ == '__main__':
    unittest.main()
