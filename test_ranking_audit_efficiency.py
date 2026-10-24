from test_environment import install
install()
from copy import deepcopy
from itertools import permutations
import time
import unittest
from unittest.mock import patch
from optimizers import ShowdownOptimizer
from showdown_simulation import (simulate_showdown, showdown_score, showdown_signature,
    _compile_score_roster, _score_compiled_roster)
from ranking_stability import audit_ranking, format_stability
from test_ranking_stability import bank
from test_showdown_performance import _showdown_players


def values(result):
    return [(showdown_signature(row), row.sim_metrics, row.sim_top_hits,
             row.sim_top_five_hits, row.sim_win_hits, row.sim_scenario_values)
            for row in result['lineups']]


def report_values(result):
    # Observational timing differs per run; all model/cache evidence remains exact.
    return {key:value for key,value in result['report'].items() if key!='simulation_profile'}


class RankingAuditEfficiencyTests(unittest.TestCase):
    def test_compiled_score_preserves_flex_order_and_exact_arithmetic(self):
        captain = dict(FlexID='c'); flex = [dict(FlexID=str(i)) for i in range(5)]
        outcomes = dict(c=1.25, **{'0': 1e16, '1': 1., '2': -1e16, '3': 2., '4': .25})
        for ordered in permutations(flex):
            row = dict(Captain=captain, Flex=list(ordered))
            expected = showdown_score(row, outcomes)
            self.assertEqual(_score_compiled_roster(_compile_score_roster(row), outcomes), expected)
            self.assertEqual(_compile_score_roster(row)[1], tuple(p['FlexID'] for p in ordered))

    def test_full_simulation_results_and_outcome_draws_match_exactly(self):
        import showdown_simulation
        players = _showdown_players(); rows = ShowdownOptimizer(players)._build_lineups_fast(24)
        original = deepcopy(players)
        opts = dict(scenarios=80, field_lineup_count=60, seed=481516)
        with patch('showdown_simulation._scenario_outcomes', wraps=showdown_simulation._scenario_outcomes) as draw:
            ordinary = simulate_showdown(rows, players, **opts)
            self.assertEqual(draw.call_count, 80)
        ordinary_values=deepcopy(values(ordinary))
        with patch('showdown_simulation._scenario_outcomes', wraps=showdown_simulation._scenario_outcomes) as draw:
            compiled = simulate_showdown(rows, players, precompiled_rosters=True, **opts)
            self.assertEqual(draw.call_count, 80)
        self.assertEqual(values(compiled), ordinary_values)
        self.assertEqual(report_values(compiled), report_values(ordinary))
        self.assertEqual(players, original)

    def test_compiled_partial_simulation_matches_existing_cancelled_sample(self):
        import showdown_simulation
        players = _showdown_players(); rows = ShowdownOptimizer(players)._build_lineups_fast(4)
        results = []
        for compiled in (False, True):
            calls = []
            original_draw = showdown_simulation._scenario_outcomes
            def draw(*args, **kwargs):
                calls.append(True); return original_draw(*args, **kwargs)
            with patch('showdown_simulation._scenario_outcomes', side_effect=draw):
                result = simulate_showdown(rows, players, scenarios=100, field_lineup_count=30,
                    seed=481516, precompiled_rosters=compiled, cancel_callback=lambda: len(calls) >= 3)
            self.assertEqual(result['report']['scenarios'], 3); results.append(deepcopy(result))
        self.assertEqual(values(results[0]), values(results[1]))
        self.assertEqual(report_values(results[0]), report_values(results[1]))

    def test_compiled_scoring_preserves_existing_scenario_cache_qualification(self):
        import os
        import tempfile
        players = _showdown_players(); rows = ShowdownOptimizer(players)._build_lineups_fast(4)
        opts = dict(scenarios=12, field_lineup_count=30, seed=481516, scenario_cache=True)
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'DFS_OPTIMIZER_DATA_DIR': folder}):
            ordinary = simulate_showdown(rows, players, **opts)
            self.assertEqual(ordinary['report']['scenario_cache']['status'], 'saved')
            with patch('showdown_simulation._scenario_outcomes', side_effect=AssertionError('qualified cache must replay')):
                compiled = simulate_showdown(rows, players, precompiled_rosters=True, **opts)
            self.assertEqual(values(compiled), values(ordinary))
            self.assertEqual(compiled['report']['scenario_cache']['reused_scenarios'], 12)

    def test_invalid_rosters_still_fail_before_precompilation(self):
        players = _showdown_players(); row = dict(Captain=players[0], Flex=[players[0]] * 5)
        with patch('showdown_simulation._compile_score_roster', side_effect=AssertionError('must validate first')):
            with self.assertRaises(ValueError):
                simulate_showdown([row], players, scenarios=10, field_lineup_count=20, precompiled_rosters=True)

    def test_audit_cap_and_build_deadline_have_distinct_stop_reasons(self):
        for deadline, expected in ((500, 'audit_time_limit'), (120, 'build_deadline')):
            clock = [0.]
            def simulate(stop):
                clock[0] = 119.9; self.assertFalse(stop())
                clock[0] = 120.; self.assertTrue(stop())
                return dict(lineups=bank(), report=dict(scenarios=1978))
            with patch('ranking_stability.time.perf_counter', side_effect=lambda: clock[0]):
                result = audit_ranking(bank(), simulate, lambda row: row[0]['FlexID'], deadline=deadline)
            self.assertEqual(result['stop_reason'], expected)
            self.assertEqual(result['status'], 'incomplete'); self.assertEqual(result['time_budget_seconds'], 120)
            self.assertEqual(result['seconds'], 120); self.assertNotIn('overlap_pct', result)
            text = '\n'.join(format_stability(result))
            self.assertIn('1,978/2,000 scenarios', text); self.assertIn('120s diagnostic cap', text)

    def test_partial_without_observed_limit_does_not_invent_timeout(self):
        with patch('ranking_stability.time.perf_counter', return_value=0):
            result = audit_ranking(bank(), lambda stop: dict(lineups=bank(), report=dict(scenarios=1978)),
                lambda row: row[0]['FlexID'], deadline=500)
        self.assertEqual(result['stop_reason'], 'partial_sample')
        self.assertEqual(result['reason'], 'audit sample did not finish'); self.assertNotIn('overlap_pct', result)

    def test_late_cancellation_suppresses_complete_result(self):
        cancelled = [False]
        def simulate(stop):
            cancelled[0] = True
            return dict(lineups=bank(), report=dict(scenarios=2000))
        result = audit_ranking(bank(), simulate, lambda row: row[0]['FlexID'],
            deadline=time.perf_counter() + 500, cancelled=lambda: cancelled[0])
        self.assertEqual(result['stop_reason'], 'cancelled'); self.assertNotIn('overlap_pct', result)

    def test_missing_or_changed_candidate_identities_do_not_publish_overlap(self):
        for rows in (bank()[:-1], bank()[:-1] + [bank()[0]]):
            result = audit_ranking(bank(), lambda stop: dict(lineups=rows, report=dict(scenarios=2000)),
                lambda row: row[0]['FlexID'], deadline=time.perf_counter() + 500)
            self.assertEqual(result['stop_reason'], 'candidate_mismatch'); self.assertNotIn('overlap_pct', result)

    def test_legacy_report_still_formats_without_new_budget_fields(self):
        text = '\n'.join(format_stability(dict(status='incomplete', reason='audit sample did not finish', scenarios=1978)))
        self.assertIn('1,978/2,000', text); self.assertNotIn('diagnostic cap', text)

    def test_all_deep_showdown_simulation_stages_enable_precompiled_scoring(self):
        import showdown_simulation
        from main_window import LineupBuildWorker, _deep_shortlist
        players = _showdown_players()
        worker = LineupBuildWorker(players, kind='showdown', num_lineups=1, salary_cap=50000,
            salary_strategy='Balanced Spend', sim_enabled=True, compute_mode='Deep', sim_scenarios=2500,
            deep_time_limit_seconds=30, deep_options=dict(shortlist=24, field=20, screening=250))
        worker.library_candidates = ShowdownOptimizer(players)._build_lineups_fast(24)
        before = deepcopy(worker.library_candidates)
        def audit(reference, simulate, signature, **kwargs):
            before = deepcopy([(row.sim_metrics, row.sim_top_hits, row.sim_top_five_hits,
                                row.sim_win_hits, row.sim_scenario_values) for row in reference])
            report = audit_ranking(reference, simulate, signature, deadline=time.perf_counter() + 500)
            self.assertEqual([(row.sim_metrics, row.sim_top_hits, row.sim_top_five_hits,
                               row.sim_win_hits, row.sim_scenario_values) for row in reference], before)
            return report
        with patch('ranking_stability.audit_ranking', side_effect=audit), \
                patch('showdown_simulation.simulate_showdown', wraps=showdown_simulation.simulate_showdown) as simulate:
            result = showdown_simulation.run_deep_showdown(worker, _deep_shortlist)
        calls = simulate.call_args_list
        self.assertEqual([call.kwargs.get('precompiled_rosters', False) for call in calls], [True, True, True])
        self.assertEqual(calls[-1].kwargs['scenarios'], 2000); self.assertEqual(calls[-1].kwargs['seed'], 481516)
        self.assertEqual(result['sim_report']['deep_build']['ranking_audit']['status'], 'completed')
        self.assertEqual(worker.library_candidates, before)
        self.assertEqual(len(result['lineups']), 1)


if __name__ == '__main__':
    unittest.main()
