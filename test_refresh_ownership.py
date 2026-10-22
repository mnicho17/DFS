import copy
import unittest
from unittest import mock
from PyQt5 import QtWidgets
from main_window import MainWindow


class RefreshOwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def run_refresh(self, change, full=False, initialized=True):
        window = mock.Mock()
        window.players = [dict(Name='Test', FlexProjection=10, OwnershipSource='Lineup simulation',
                               ProjOwnPct=75.6, ProjCptOwnPct=41.0, ProjFlexOwnPct=34.6,
                               LiveStatusChanged=True, LiveStatusUpdatedAt='old')]
        if not initialized:
            window.players[0].pop('OwnershipSource')
        before = copy.deepcopy(window.players)
        window._auto_fade_out_players.side_effect = lambda: MainWindow._auto_fade_out_players(window)
        def refresh(players):
            players[0].update(change)
            return dict(sleeper=1)
        name = 'apply_auto_nfl_context' if full else 'refresh_live_nfl_data'
        with mock.patch('main_window.' + name, side_effect=refresh):
            summary = MainWindow._run_live_nfl_check(window, show_dialog=False, full_context=full)
        return window, before, summary

    def test_unchanged_refresh_preserves_simulated_ownership(self):
        for full in (False, True):
            with self.subTest(full=full):
                window, before, summary = self.run_refresh(dict(LiveStatusChanged=False, LiveStatusUpdatedAt='new'), full)
                window.recalc_ownership_quick.assert_not_called()
                for key in ('OwnershipSource', 'ProjOwnPct', 'ProjCptOwnPct', 'ProjFlexOwnPct'):
                    self.assertEqual(window.players[0][key], before[0][key])
                self.assertEqual(summary['ownership_action'], 'preserved')
                self.assertEqual(summary['ownership_simulation_replaced'], 0)
                window._record_live_check.assert_called_once_with(summary)
                self.assertIn('Ownership preserved', window.status.showMessage.call_args.args[0])

    def test_relevant_changes_recalculate_and_report(self):
        for change in (dict(FlexProjection=11), dict(InjuryStatus='OUT'), dict(FadeCpt=True),
                       dict(NFLUsageHistory={'current': {'games': 5}}), dict(LiveStatusConflict=True)):
            with self.subTest(change=change):
                window, _, summary = self.run_refresh(change)
                window.recalc_ownership_quick.assert_called_once_with()
                self.assertEqual(summary['ownership_action'], 'recalculated')
                self.assertEqual(summary['ownership_simulation_replaced'], 1)
                if change.get('InjuryStatus') == 'OUT':
                    self.assertTrue(window.players[0]['FadeFlex'])
                    self.assertTrue(window.players[0]['FadeCpt'])
                self.assertIn('Ownership recalculated', window.status.showMessage.call_args.args[0])

    def test_missing_ownership_initializes_estimates(self):
        window, _, summary = self.run_refresh({}, initialized=False)
        window.recalc_ownership_quick.assert_called_once_with()
        self.assertEqual(summary['ownership_action'], 'recalculated')
        self.assertEqual(summary['ownership_simulation_replaced'], 0)
