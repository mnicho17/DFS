import unittest
from unittest import mock
from nfl_simulation import SimLineup
from ranking_stability import audit_ranking, format_stability


def bank(reverse=False):
    result = []
    for i in range(200):
        lu = SimLineup([{'FlexID': str(i)}])
        lu.sim_metrics = dict(sim_scenarios=10000, sim_top_one_pct=i if reverse else 200-i)
        result.append(lu)
    return result


class RankingStabilityTests(unittest.TestCase):
    def test_identical_and_reversed_rankings(self):
        for reverse, expected in [(False, 100), (True, 0)]:
            reference = bank()
            before = [dict(lu.sim_metrics) for lu in reference]
            with mock.patch('ranking_stability.time.perf_counter', return_value=0):
                result = audit_ranking(reference, lambda stop: {'lineups': bank(reverse), 'report': {'scenarios': 2000}},
                                       lambda lu: lu[0]['FlexID'], deadline=200)
            self.assertEqual(result['overlap_pct'], expected)
            self.assertEqual(result['leading_count'], 50)
            self.assertEqual(before, [lu.sim_metrics for lu in reference])
            self.assertIn('does not measure historical accuracy', '\n'.join(format_stability(result)))

    def test_small_budget_and_cancellation_do_not_simulate(self):
        simulate = mock.Mock(side_effect=AssertionError('should not run'))
        with mock.patch('ranking_stability.time.perf_counter', return_value=0):
            result = audit_ranking(bank(), simulate, str, deadline=50)
            self.assertEqual(result['status'], 'skipped')
            result = audit_ranking(bank(), simulate, str, deadline=500, cancelled=lambda: True)
            self.assertEqual(result['reason'], 'cancelled')

    def test_partial_audit_does_not_publish_overlap(self):
        with mock.patch('ranking_stability.time.perf_counter', return_value=0):
            result = audit_ranking(bank(), lambda stop: {'lineups': bank(), 'report': {'scenarios': 100}}, str, deadline=500)
        self.assertEqual(result['status'], 'incomplete')
        self.assertNotIn('overlap_pct', result)

    def test_small_bank_never_compares_whole_bank(self):
        values = bank()[:20]
        with mock.patch('ranking_stability.time.perf_counter', return_value=0):
            result = audit_ranking(values, lambda stop: {'lineups': values, 'report': {'scenarios': 2000}}, str, deadline=500)
        self.assertEqual(result['leading_count'], 5)

    def test_real_classic_and_showdown_samples_preserve_reference_scores(self):
        from test_nfl_logic import _fixture_players
        from test_showdown_performance import _showdown_players
        from nfl_simulation import generate_nfl_field_lineups, simulate_nfl_contest, lineup_signature
        from showdown_simulation import generate_showdown_field, simulate_showdown, showdown_signature
        for showdown in (False, True):
            players = _showdown_players() if showdown else _fixture_players()
            candidates = generate_showdown_field(players, 20) if showdown else generate_nfl_field_lineups(players, 20)[0]
            simulate = simulate_showdown if showdown else simulate_nfl_contest
            reference = simulate(candidates, players, scenarios=1000, field_lineup_count=20, seed=90210)['lineups']
            before = [dict(lu.sim_metrics) for lu in reference]
            with mock.patch('ranking_stability.time.perf_counter', return_value=0):
                report = audit_ranking(reference,
                    lambda stop: simulate(reference, players, scenarios=2000, field_lineup_count=20,
                                          seed=481516, cancel_callback=stop),
                    showdown_signature if showdown else lineup_signature, deadline=500)
            self.assertEqual(report['status'], 'completed')
            self.assertEqual(report['scenarios'], 2000)
            self.assertEqual(before, [lu.sim_metrics for lu in reference])


if __name__ == '__main__':
    unittest.main()
