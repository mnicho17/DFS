"""Compiled Classic field scoring preserves original ordered-sum semantics."""
from test_environment import install
install()
import copy
from contextlib import ExitStack
from itertools import permutations
import unittest
from unittest.mock import patch
import nfl_simulation as nfl
from optimizers import MultiSportClassicOptimizer
from test_nfl_logic import _fixture_players
from test_scenario_cache import result_values
from scripts.benchmark_scenario_replay import exact


def naive_score(bank, outcomes):
    return sorted(sum(outcomes.get(key, 0.0) for key in signature) for signature in bank)


def normalized(value):
    if isinstance(value, dict):
        return {k: normalized(v) for k, v in value.items()
                if k not in ('simulation_profile', 'started_at', 'finished_at')}
    if isinstance(value, (list, tuple)):
        return [normalized(v) for v in value]
    return value


class ClassicFieldScoreBankTests(unittest.TestCase):
    def test_slot_order_and_duplicate_rosters_preserve_exact_sums(self):
        keys = ('a', 'b', 'c', 'd', 'e')
        bank = list(permutations(keys))
        bank += [bank[0], bank[0]]
        outcomes = dict(a=1e16, b=1., c=-1e16, d=2., e=.25)
        original = copy.deepcopy(bank)
        scores = nfl._score_classic_bank(nfl._compile_classic_score_bank(bank), outcomes)
        self.assertEqual(exact(scores), exact(naive_score(bank, outcomes)))
        self.assertEqual(len(scores), len(bank))
        self.assertEqual(bank, original)

    def test_missing_repeated_empty_and_single_slots_keep_types_and_values(self):
        bank = [(), ('a',), ('unknown',), ('a', 'a'), ('negzero',), ('small', 'a', 'missing')]
        outcomes = dict(a=2, negzero=-0., small=1e-300)
        compiled = nfl._compile_classic_score_bank(iter(bank))
        scores = nfl._score_classic_bank(compiled, outcomes)
        expected = naive_score(bank, outcomes)
        self.assertEqual(exact(scores), exact(expected))
        self.assertEqual([type(v) for v in scores], [type(v) for v in expected])

    def test_reused_bank_reads_new_outcomes_each_frame_and_leaves_inputs_unchanged(self):
        bank = [('a', 'b', 'unknown'), ('b', 'a'), ('a', 'a')]
        compiled = nfl._compile_classic_score_bank(bank)
        for outcomes in ({'a': 1., 'b': 2.}, {'a': -4., 'b': 9., 'unknown': 3.}, {}):
            original = copy.deepcopy(outcomes)
            self.assertEqual(exact(nfl._score_classic_bank(compiled, outcomes)), exact(naive_score(bank, outcomes)))
            self.assertEqual(outcomes, original)

    def compare_simulators(self, *, partial=False, transform=None):
        players = _fixture_players()
        original = copy.deepcopy(players)
        rows = MultiSportClassicOptimizer(players).build_lineups(8)
        real_draw = nfl._scenario_outcomes
        evidence, reports, rng_states = [], [], []
        for compiled in (False, True):
            draws = []
            def draw(rng, *args, **kwargs):
                result = real_draw(rng, *args, **kwargs)
                draws.append((copy.deepcopy(result), rng.getstate()))
                return result
            with ExitStack() as stack:
                stack.enter_context(patch.object(nfl, '_scenario_outcomes', side_effect=draw))
                if not compiled:
                    stack.enter_context(patch.object(nfl, '_compile_classic_score_bank', side_effect=lambda bank: tuple(bank)))
                    stack.enter_context(patch.object(nfl, '_score_classic_bank', side_effect=naive_score))
                result = nfl.simulate_nfl_contest(copy.deepcopy(rows), players, scenarios=40,
                    field_lineup_count=30, seed=481516, capture_distributions=True,
                    cancel_callback=(lambda: len(draws) >= 3) if partial else None,
                    outcome_transform=transform)
            self.assertEqual(result['report']['scenarios'], 3 if partial else 40)
            self.assertEqual(len(draws), 3 if partial else 40)
            evidence.append(exact(result_values(result)))
            reports.append(exact(normalized(result['report'])))
            rng_states.append(draws)
        self.assertEqual(evidence[0], evidence[1])
        self.assertEqual(reports[0], reports[1])
        self.assertEqual(rng_states[0], rng_states[1])
        self.assertEqual(players, original)

    def test_complete_simulations_preserve_draws_reports_hits_and_distributions(self):
        self.compare_simulators()

    def test_partial_cancellation_preserves_same_fresh_frames(self):
        self.compare_simulators(partial=True)

    def test_diagnostic_transform_can_remove_player_scores(self):
        self.compare_simulators(transform=lambda outcomes: {k: v for i, (k, v) in enumerate(outcomes.items()) if i % 3})

    def test_cancellation_and_warm_replay_skip_compilation_and_fresh_calls_reset(self):
        players = _fixture_players()
        rows = MultiSportClassicOptimizer(players).build_lineups(4)
        opts = dict(scenarios=9, field_lineup_count=20, seed=239)
        with patch.object(nfl, '_compile_classic_score_bank', side_effect=AssertionError('unused')):
            cancelled = nfl.simulate_nfl_contest(copy.deepcopy(rows), players,
                cancel_callback=lambda: True, **opts)
            self.assertEqual(cancelled['report']['scenarios'], 0)
        real = nfl._compile_classic_score_bank
        with patch.object(nfl, '_compile_classic_score_bank', wraps=real) as compile_bank:
            cold = nfl.simulate_nfl_contest(copy.deepcopy(rows), players, scenario_cache=True, **opts)
            self.assertEqual(compile_bank.call_count, 3)
        self.assertEqual(cold['report']['scenario_cache']['status'], 'saved')
        with patch.object(nfl, '_compile_classic_score_bank', side_effect=AssertionError('must replay')):
            warm = nfl.simulate_nfl_contest(copy.deepcopy(rows), players, scenario_cache=True, **opts)
        self.assertEqual(warm['report']['scenario_cache']['reused_scenarios'], 9)
        self.assertEqual(exact(result_values(cold)), exact(result_values(warm)))
        with patch.object(nfl, '_compile_classic_score_bank', wraps=real) as compile_bank:
            nfl.simulate_nfl_contest(copy.deepcopy(rows), players, **opts)
            nfl.simulate_nfl_contest(copy.deepcopy(rows), players, **opts)
            self.assertEqual(compile_bank.call_count, 6)


if __name__ == '__main__':
    unittest.main()
