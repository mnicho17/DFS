"""Regression coverage for scoring inputs versus search preferences."""
import random
import unittest

from nfl_simulation import _projection, _scenario_outcomes, generate_nfl_field_lineups, lineup_signature
from showdown_simulation import active_showdown_players
from test_nfl_logic import _fixture_players
from test_showdown_performance import _showdown_players


class SimulationContextTests(unittest.TestCase):
    def test_search_preferences_do_not_change_seeded_scores(self):
        players = _fixture_players()
        boosted = [dict(p, _PortfolioCandidateBoost=12, _PortfolioCptCandidateBoost=14) for p in players]
        first, second = random.Random(57), random.Random(57)
        for _ in range(100):
            self.assertEqual(_scenario_outcomes(first, players), _scenario_outcomes(second, boosted))

    def test_explicit_team_projection_adjustment_still_applies(self):
        self.assertAlmostEqual(_projection(dict(FlexProjection=20, TeamAdjPct=10,
                                                _PortfolioCandidateBoost=12)), 22)

    def test_search_preferences_do_not_change_opponent_field(self):
        players = _fixture_players()
        boosted = [dict(p, _PortfolioCandidateBoost=12 if i % 3 == 0 else 0)
                   for i, p in enumerate(players)]
        first, _ = generate_nfl_field_lineups(players, 20, seed=17)
        second, _ = generate_nfl_field_lineups(boosted, 20, seed=17)
        self.assertEqual(len(first), 20)
        self.assertEqual([lineup_signature(lu) for lu in first],
                         [lineup_signature(lu) for lu in second])

    def test_missing_context_matches_complete_context_without_mutating_input(self):
        players = _showdown_players()
        inferred = active_showdown_players(players)
        explicit = active_showdown_players([
            dict(p, Opponent='CAR' if p['Team'] == 'ARI' else 'ARI',
                 GameKey='ARI@CAR', GameInfo='ARI@CAR') for p in players])
        self.assertEqual(inferred, explicit)
        self.assertNotIn('Opponent', players[0])
        first, second = random.Random(31), random.Random(31)
        for _ in range(20):
            self.assertEqual(_scenario_outcomes(first, inferred), _scenario_outcomes(second, explicit))

    def test_conflicting_opponent_rejected(self):
        players = _showdown_players()
        players[0]['Opponent'] = 'BUF'
        with self.assertRaisesRegex(ValueError, 'opponent information'):
            active_showdown_players(players)

    def test_conflicting_game_keys_rejected(self):
        players = _showdown_players()
        players[0].update(GameKey='ARI@CAR', GameInfo='BUF@MIA')
        with self.assertRaisesRegex(ValueError, 'one game'):
            active_showdown_players(players)

    def test_partial_game_context_is_shared(self):
        players = _showdown_players()
        players[0]['GameInfo'] = 'CAR@ARI 09/07/2026 8:00PM ET'
        pool = active_showdown_players(players)
        self.assertEqual({p['GameKey'] for p in pool}, {'CAR@ARI'})


if __name__ == '__main__':
    unittest.main()
