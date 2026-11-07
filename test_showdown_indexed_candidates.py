"""Candidate indexing preserves input order and fresh per-frame evidence."""
from test_environment import install
install()
import copy
from itertools import permutations
import unittest
from unittest.mock import patch
import showdown_simulation as sim
from optimizers import ShowdownOptimizer
from test_showdown_performance import _showdown_players
from test_scenario_cache import result_values
from scripts.benchmark_scenario_replay import exact
from test_classic_field_score_bank import normalized


class IndexedCandidateTests(unittest.TestCase):
    def test_ordered_rosters_repeats_and_floating_sums_match_scalar_scorer(self):
        rosters = [('captain', flex) for flex in permutations(('a', 'b', 'c', 'd', 'e'))]
        rosters += [rosters[7], ('captain', ()), ('a', ('a',)), ('a', ('a', 'a'))]
        original = copy.deepcopy(rosters)
        indexed = sim._index_score_rosters(iter(rosters))
        for outcomes in (dict(captain=-0., a=1e16, b=1., c=-1e16, d=2., e=.25),
                         dict(captain=2, a=-3, b=1e-300, c=4., d=-0., e=9.)):
            frozen = copy.deepcopy(outcomes)
            expected = [sim._score_compiled_roster(row, outcomes) for row in rosters]
            actual = sim._score_indexed_rosters(indexed, outcomes)
            self.assertEqual(exact(actual), exact(expected))
            self.assertEqual([type(value) for value in actual], [type(value) for value in expected])
            self.assertEqual(outcomes, frozen)
        self.assertEqual(rosters, original)
        self.assertEqual(sim._score_indexed_rosters(sim._index_score_rosters([]), {}), [])

    def test_missing_candidate_ids_remain_strict(self):
        rosters = [('captain', ('a', 'missing')), ('other', ())]
        indexed = sim._index_score_rosters(rosters)
        for outcomes in ({}, {'captain': 2.}, {'captain': 2., 'a': 1.}):
            errors = []
            for score in (lambda: sim._score_indexed_rosters(indexed, outcomes),
                          lambda: [sim._score_compiled_roster(row, outcomes) for row in rosters]):
                with self.assertRaises(KeyError) as error:
                    score()
                errors.append(error.exception.args)
            self.assertEqual(errors[0], errors[1])

    def test_duplicate_candidate_order_full_rng_reports_and_values_match_naive_path(self):
        players = _showdown_players()
        rows = ShowdownOptimizer(players).build_lineups(8)
        rows = [rows[3]] + rows + [copy.deepcopy(rows[3])]
        signatures = [sim._compile_score_roster(row) for row in rows]
        original = copy.deepcopy(players)
        real_draw = sim._scenario_outcomes
        evidence, reports, states = [], [], []
        for indexed in (False, True):
            draws = []
            def draw(rng, *args, **kwargs):
                outcomes = real_draw(rng, *args, **kwargs)
                draws.append((copy.deepcopy(outcomes), rng.getstate()))
                return outcomes
            with patch.object(sim, '_scenario_outcomes', side_effect=draw):
                result = sim.simulate_showdown(copy.deepcopy(rows), players, scenarios=30,
                    field_lineup_count=30, seed=481516, capture_distributions=True,
                    precompiled_rosters=indexed)
            self.assertEqual([sim._compile_score_roster(row) for row in result['lineups']], signatures)
            evidence.append(exact(result_values(result)))
            reports.append(exact(normalized(result['report'])))
            states.append(draws)
        self.assertEqual(evidence[0], evidence[1])
        self.assertEqual(reports[0], reports[1])
        self.assertEqual(states[0], states[1])
        self.assertEqual(players, original)


if __name__ == '__main__':
    unittest.main()
