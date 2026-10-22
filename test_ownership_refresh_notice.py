import copy
import json
import unittest
from unittest import mock

from ownership_refresh_notice import describe
from build_diagnostics import create_build_diagnostic, format_build_report
import test_build_diagnostics


class OwnershipNoticeTests(unittest.TestCase):
    def test_fallback_persists_with_quick_ownership_without_mutating_inputs(self):
        players = [{'OwnershipSource': 'Quick roster-slot estimate', 'ProjOwnPct': 20}]
        before = copy.deepcopy(players)
        for action in ('recalculated', 'preserved'):
            result = describe(players, dict(ownership_simulation_replaced=1, ownership_action=action))
            self.assertIn('replaced simulated ownership for 1 players', result['notice'])
        self.assertEqual(players, before)

    def test_fresh_simulation_and_empty_slate_clear_notice(self):
        for players in ([], [{'OwnershipSource': 'Lineup simulation'}]):
            self.assertEqual(describe(players, {'ownership_simulation_replaced': 1})['notice'], '')
        from main_window import MainWindow
        window = mock.Mock()
        window.players = [{'Name': 'Test', 'OwnershipSource': 'Quick roster-slot estimate'}]
        window.last_live_check_summary = {'ownership_simulation_replaced': 1, 'sleeper': 1}
        MainWindow._on_own_sim_finished(window, {})
        self.assertNotIn('ownership_simulation_replaced', window.last_live_check_summary)
        self.assertEqual(window.last_live_check_summary['sleeper'], 1)
        # Explicit quick recalculation after a fresh simulation has no old refresh warning.
        window.players[0]['OwnershipSource'] = 'Quick roster-slot estimate'
        self.assertEqual(describe(window.players, window.last_live_check_summary)['notice'], '')


    def test_initial_quick_estimates_are_not_a_fallback(self):
        self.assertEqual(describe([{'OwnershipSource': 'Quick roster-slot estimate'}])['notice'], '')

    def test_mixed_and_missing_sources_are_explicit(self):
        result = describe([{}, {'OwnershipSource': 'Lineup simulation'},
                           {'OwnershipSource': 'Quick roster-slot estimate'}],
                          {'ownership_simulation_replaced': 1})
        self.assertEqual(result['sources'], 'Lineup simulation: 1, Not recorded: 1, Quick roster-slot estimate: 1')
        self.assertTrue(result['notice'])

    def test_report_carries_sources_and_notice_and_supports_old_records(self):
        record = test_build_diagnostics.BuildDiagnosticsTests()._diagnostic()
        self.assertNotIn('Ownership: Quick roster-slot estimate', format_build_report(record))
        provenance = describe([{'OwnershipSource': 'Quick roster-slot estimate'}],
                              {'ownership_simulation_replaced': 1})
        record = create_build_diagnostic(context={'ownership_refresh': provenance}, timing_report={},
                                         portfolio_report={}, sim_report={}, displayed_count=0)
        record = json.loads(json.dumps(record))
        self.assertEqual(record['ownership_refresh'], provenance)
        report = format_build_report(record)
        self.assertIn('Ownership: Quick roster-slot estimate: 1', report)
        self.assertIn('Run ownership simulation again', report)
