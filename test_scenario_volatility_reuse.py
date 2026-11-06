"""Per-simulation reuse must preserve fresh outcomes, RNG state and input isolation."""
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
    return {key: value for key, value in result['report'].items() if key != 'simulation_profile'}


class ScenarioVolatilityReuseTests(unittest.TestCase):
    def test_fresh_draws_and_random_state_match_for_both_specialist_models(self):
        for fixture in (_fixture_players, _showdown_players):
            for model in ('events', 'legacy'):
                with self.subTest(fixture=fixture.__name__, model=model):
                    players = fixture()
                    original = copy.deepcopy(players)
                    live_rng, reused_rng = random.Random(481516), random.Random(481516)
                    live_scripts, reused_scripts = Counter(), Counter()
                    table = {}
                    for _ in range(30):
                        live = nfl._scenario_outcomes(live_rng, players,
                            script_counter=live_scripts, specialist_model=model)
                        reused = nfl._scenario_outcomes(reused_rng, players,
                            script_counter=reused_scripts, specialist_model=model,
                            _volatility_by_player=table)
                        self.assertEqual(exact(live), exact(reused))
                        self.assertEqual(live_rng.getstate(), reused_rng.getstate())
                    self.assertEqual(live_scripts, reused_scripts)
                    self.assertEqual(len(table), len(players))
                    self.assertEqual(players, original)

    def fixtures(self):
        for kind, fixture, optimizer, module, simulate in (
            ('classic', _fixture_players, MultiSportClassicOptimizer, nfl, nfl.simulate_nfl_contest),
            ('showdown', _showdown_players, ShowdownOptimizer, showdown, showdown.simulate_showdown),
        ):
            players = fixture()
            yield kind, players, optimizer(players).build_lineups(8), module, simulate

    def test_complete_simulations_reuse_only_static_values_and_refresh_next_call(self):
        for kind, players, rows, module, simulate in self.fixtures():
            with self.subTest(kind=kind):
                opts = dict(scenarios=20, field_lineup_count=20, seed=481516)
                original = copy.deepcopy(players)
                real_cv = nfl._player_volatility_cv
                with patch.object(nfl, '_player_volatility_cv', wraps=real_cv) as cv:
                    reused = simulate(copy.deepcopy(rows), players, **opts)
                    first = copy.deepcopy(exact(result_values(reused)))
                    count = cv.call_count
                    self.assertGreater(count, 0)
                    self.assertLessEqual(count, len(players))
                    again = simulate(copy.deepcopy(rows), players, **opts)
                    self.assertEqual(cv.call_count, 2 * count)
                    self.assertEqual(first, exact(result_values(again)))
                players[0]['NFLDepthOrder'] = 3
                players[0]['NFLRoleScore'] = -.8
                players[0]['InjuryStatus'] = 'QUESTIONABLE'
                with patch.object(nfl, '_player_volatility_cv', wraps=real_cv) as cv:
                    changed = simulate(copy.deepcopy(rows), players, **opts)
                    changed_count = cv.call_count
                    self.assertEqual(changed_count, len({id(call.args[0]) for call in cv.call_args_list}))
                draw = nfl._scenario_outcomes
                def uncached(*args, **kwargs):
                    kwargs.pop('_volatility_by_player', None)
                    return draw(*args, **kwargs)
                with patch.object(module, '_scenario_outcomes', side_effect=uncached), \
                     patch.object(nfl, '_player_volatility_cv', wraps=real_cv) as cv:
                    reference = simulate(copy.deepcopy(rows), players, **opts)
                    self.assertEqual(cv.call_count, 20 * changed_count)
                self.assertEqual(exact(result_values(changed)), exact(result_values(reference)))
                self.assertEqual(report_values(changed), report_values(reference))
                self.assertEqual(original[1:], players[1:])

    def test_cancelled_before_first_draw_does_not_compute_volatility(self):
        for kind, players, rows, module, simulate in self.fixtures():
            with self.subTest(kind=kind), patch.object(nfl, '_player_volatility_cv',
                    side_effect=AssertionError('No outcome parameters needed')):
                result = simulate(rows, players, scenarios=20, field_lineup_count=20,
                                  seed=481516, cancel_callback=lambda: True)
                self.assertEqual(result['report']['scenarios'], 0)

    def test_direct_diagnostic_draws_refresh_changed_inputs(self):
        players = _showdown_players()
        with patch.object(nfl, '_player_volatility_cv', wraps=nfl._player_volatility_cv) as cv:
            nfl._scenario_outcomes(random.Random(7), players)
            players[0]['NFLDepthOrder'] = 3
            nfl._scenario_outcomes(random.Random(7), players)
            self.assertEqual(cv.call_count, 2 * len(players))


if __name__ == '__main__':
    unittest.main()
