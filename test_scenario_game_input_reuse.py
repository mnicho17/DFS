"""Static game setup reuse preserves every fresh draw and its ordering."""
from test_environment import install
install()
import copy
import random
import unittest
from collections import Counter
from unittest.mock import patch
import nfl_simulation as nfl
import showdown_simulation as showdown
from optimizers import MultiSportClassicOptimizer, ShowdownOptimizer
from test_nfl_logic import _fixture_players
from test_showdown_performance import _showdown_players
from test_scenario_cache import result_values
from scripts.benchmark_scenario_replay import exact


def report_values(result):
    return {k: v for k, v in result['report'].items() if k != 'simulation_profile'}


class ScenarioGameInputReuseTests(unittest.TestCase):
    def fixtures(self):
        for kind, fixture, optimizer, module, simulate in (
            ('classic', _fixture_players, MultiSportClassicOptimizer, nfl, nfl.simulate_nfl_contest),
            ('showdown', _showdown_players, ShowdownOptimizer, showdown, showdown.simulate_showdown),
        ):
            players = fixture()
            yield kind, players, optimizer(players).build_lineups(8), module, simulate

    def test_draws_rng_state_and_scripts_match_with_spreads_and_missing_metadata(self):
        for fixture in (_fixture_players, _showdown_players):
            for model in ('events', 'legacy'):
                for variant in ('zero_spreads', 'nonzero_spreads', 'missing'):
                    with self.subTest(fixture=fixture.__name__, model=model, variant=variant):
                        players = fixture()
                        for i, p in enumerate(players):
                            p['NFLVegasSpread'] = 0 if variant == 'zero_spreads' else (i % 3 - 1) * 7
                            if variant == 'missing' and i % 2:
                                for key in ('Team', 'GameKey', 'GameInfo', 'NFLVegasGameTotal'):
                                    p.pop(key, None)
                        before = copy.deepcopy(players)
                        live_rng, prepared_rng = random.Random(481516), random.Random(481516)
                        scripts, prepared_scripts = Counter(), Counter()
                        table = {}
                        for _ in range(15):
                            live = nfl._scenario_outcomes(live_rng, players, specialist_model=model,
                                                          script_counter=scripts)
                            prepared = nfl._scenario_outcomes(prepared_rng, players, specialist_model=model,
                                script_counter=prepared_scripts, _prepared_games=table)
                            self.assertEqual(exact(live), exact(prepared))
                            self.assertEqual(live_rng.getstate(), prepared_rng.getstate())
                        self.assertEqual(scripts, prepared_scripts)
                        self.assertEqual(players, before)

    def test_game_setup_runs_once_per_simulation_and_changed_inputs_refresh(self):
        for kind, players, rows, module, simulate in self.fixtures():
            with self.subTest(kind=kind):
                opts = dict(scenarios=20, field_lineup_count=20, seed=481516)
                real = nfl._prepare_scenario_games
                with patch.object(nfl, '_prepare_scenario_games', wraps=real) as prepare:
                    simulate(copy.deepcopy(rows), players, **opts)
                    self.assertEqual(prepare.call_count, 1)
                    simulate(copy.deepcopy(rows), players, **opts)
                    self.assertEqual(prepare.call_count, 2)
                for p in players:
                    p['NFLVegasGameTotal'] = 55
                    p['NFLVegasSpread'] = 9
                for row in rows:
                    roster = [row['Captain']] + row['Flex'] if kind == 'showdown' else row
                    for p in roster:
                        p['NFLVegasGameTotal'] = 55
                        p['NFLVegasSpread'] = 9
                original = copy.deepcopy(players)
                prepared = simulate(copy.deepcopy(rows), players, **opts)
                draw = nfl._scenario_outcomes
                def unprepared(*args, **kwargs):
                    kwargs.pop('_prepared_games', None)
                    return draw(*args, **kwargs)
                with patch.object(module, '_scenario_outcomes', side_effect=unprepared), \
                     patch.object(nfl, '_prepare_scenario_games', wraps=real) as prepare:
                    reference = simulate(copy.deepcopy(rows), players, **opts)
                    self.assertEqual(prepare.call_count, 20)
                self.assertEqual(exact(result_values(prepared)), exact(result_values(reference)))
                self.assertEqual(report_values(prepared), report_values(reference))
                self.assertEqual(players, original)

    def test_pre_draw_cancellation_and_warm_replay_skip_game_setup(self):
        for kind, players, rows, module, simulate in self.fixtures():
            with self.subTest(kind=kind):
                opts = dict(scenarios=20, field_lineup_count=20, seed=481516)
                with patch.object(nfl, '_prepare_scenario_games', side_effect=AssertionError('unused')):
                    cancelled = simulate(copy.deepcopy(rows), players, cancel_callback=lambda: True, **opts)
                    self.assertEqual(cancelled['report']['scenarios'], 0)
                cold = simulate(copy.deepcopy(rows), players, scenario_cache=True, **opts)
                self.assertEqual(cold['report']['scenario_cache']['status'], 'saved')
                with patch.object(nfl, '_prepare_scenario_games', side_effect=AssertionError('must replay')):
                    warm = simulate(copy.deepcopy(rows), players, scenario_cache=True, **opts)
                self.assertEqual(warm['report']['scenario_cache']['reused_scenarios'], 20)
                self.assertEqual(exact(result_values(cold)), exact(result_values(warm)))

    def test_empty_game_set_is_prepared_once_and_direct_draws_remain_uncached(self):
        table = {}
        with patch.object(nfl, '_prepare_scenario_games', wraps=nfl._prepare_scenario_games) as prepare:
            for _ in range(3):
                self.assertEqual(nfl._scenario_outcomes(random.Random(1), [], _prepared_games=table), {})
            self.assertEqual(prepare.call_count, 1)
            for _ in range(3):
                self.assertEqual(nfl._scenario_outcomes(random.Random(1), []), {})
            self.assertEqual(prepare.call_count, 4)


if __name__ == '__main__':
    unittest.main()
