"""Indexed Showdown field scoring must preserve arithmetic and frame evidence."""
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


class ShowdownIndexedFieldTests(unittest.TestCase):
    def test_flex_order_duplicate_multiplicity_and_captain_arithmetic(self):
        rosters = [('cpt', flex) for flex in permutations(('a', 'b', 'c', 'd', 'e'))]
        rosters += [rosters[0], rosters[0]]
        bank = (rosters, list(range(len(rosters))))
        outcomes = dict(cpt=-0., a=1e16, b=1., c=-1e16, d=2., e=.25)
        original = copy.deepcopy(bank)
        self.assertEqual(exact(sim._score_indexed_bank(sim._index_score_bank(bank), outcomes)),
                         exact(sim._score_compiled_bank(bank, outcomes)))
        self.assertEqual(bank, original)

    def test_empty_single_repeated_slots_and_strict_missing_ids(self):
        bank = ([('cpt', ()), ('cpt', ('a',)), ('a', ('a', 'a')), ('cpt', ('small', 'a'))], [0, 1, 2, 3, 2])
        indexed = sim._index_score_bank(bank)
        for outcomes in (dict(cpt=-0., a=2, small=1e-300), dict(cpt=10., a=-7., small=2.)):
            original = copy.deepcopy(outcomes)
            actual, expected = sim._score_indexed_bank(indexed, outcomes), sim._score_compiled_bank(bank, outcomes)
            self.assertEqual(exact(actual), exact(expected))
            self.assertEqual([type(x) for x in actual], [type(x) for x in expected])
            self.assertEqual(outcomes, original)
        for outcomes in ({}, {'cpt': 2}, {'cpt': 2, 'a': 3}):
            errors = []
            for score, compiled in ((sim._score_indexed_bank, indexed), (sim._score_compiled_bank, bank)):
                with self.assertRaises(KeyError) as error:
                    score(compiled, outcomes)
                errors.append(error.exception.args)
            self.assertEqual(errors[0], errors[1])
        self.assertEqual(sim._score_indexed_bank(sim._index_score_bank(([], [])), {}), [])

    def compare_simulators(self, partial=False, transform=None):
        players = _showdown_players()
        original = copy.deepcopy(players)
        rows = ShowdownOptimizer(players).build_lineups(8)
        real_draw = sim._scenario_outcomes
        results, reports, states = [], [], []
        for indexed in (False, True):
            draws = []
            def draw(rng, *args, **kwargs):
                result = real_draw(rng, *args, **kwargs)
                draws.append((copy.deepcopy(result), rng.getstate()))
                return result
            from contextlib import ExitStack
            with ExitStack() as stack:
                stack.enter_context(patch.object(sim, '_scenario_outcomes', side_effect=draw))
                if not indexed:
                    stack.enter_context(patch.object(sim, '_index_score_bank', side_effect=lambda bank: bank))
                    stack.enter_context(patch.object(sim, '_score_indexed_bank', side_effect=sim._score_compiled_bank))
                result = sim.simulate_showdown(copy.deepcopy(rows), players, scenarios=40,
                    field_lineup_count=30, seed=481516, capture_distributions=True, precompiled_rosters=True,
                    cancel_callback=(lambda: len(draws) >= 3) if partial else None, outcome_transform=transform)
            self.assertEqual(result['report']['scenarios'], 3 if partial else 40)
            results.append(exact(result_values(result)))
            reports.append(exact(normalized(result['report'])))
            states.append(draws)
        self.assertEqual(results[0], results[1])
        self.assertEqual(reports[0], reports[1])
        self.assertEqual(states[0], states[1])
        self.assertEqual(players, original)

    def test_full_simulation_preserves_draws_metrics_hits_values_and_distributions(self):
        self.compare_simulators()

    def test_partial_cancellation_preserves_same_frames(self):
        self.compare_simulators(partial=True)

    def test_transformed_frames_use_new_values(self):
        self.compare_simulators(transform=lambda outcomes: {key: value * 1.125 for key, value in outcomes.items()})

    def test_precancel_warm_replay_and_per_call_reset(self):
        players = _showdown_players()
        rows = ShowdownOptimizer(players).build_lineups(4)
        opts = dict(scenarios=9, field_lineup_count=20, seed=239, precompiled_rosters=True)
        with patch.object(sim, '_index_score_bank', side_effect=AssertionError('unused')):
            result = sim.simulate_showdown(copy.deepcopy(rows), players, cancel_callback=lambda: True, **opts)
            self.assertEqual(result['report']['scenarios'], 0)
        real = sim._index_score_bank
        with patch.object(sim, '_index_score_bank', wraps=real) as compile_bank:
            cold = sim.simulate_showdown(copy.deepcopy(rows), players, scenario_cache=True, **opts)
            self.assertEqual(compile_bank.call_count, 3)
        self.assertEqual(cold['report']['scenario_cache']['status'], 'saved')
        with patch.object(sim, '_index_score_bank', side_effect=AssertionError('must replay')):
            warm = sim.simulate_showdown(copy.deepcopy(rows), players, scenario_cache=True, **opts)
        self.assertEqual(warm['report']['scenario_cache']['reused_scenarios'], 9)
        self.assertEqual(exact(result_values(cold)), exact(result_values(warm)))
        with patch.object(sim, '_index_score_bank', wraps=real) as compile_bank:
            sim.simulate_showdown(copy.deepcopy(rows), players, **opts)
            sim.simulate_showdown(copy.deepcopy(rows), players, **opts)
            self.assertEqual(compile_bank.call_count, 6)


if __name__ == '__main__':
    unittest.main()
