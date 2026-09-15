import unittest
from unittest.mock import patch
from nfl_auto_data import apply_auto_nfl_context
from projection_coverage import summarize_projection_coverage, format_projection_coverage


def player(name):
    return dict(Name=name, Team='SEA', Position='RB', FlexProjection=8,
                BaseProjection=8, GameInfo='SEA@SF 09/13/2026 01:00PM ET')


def row(name, carries, season=2026, team='SEA'):
    return dict(player_display_name=name, recent_team=team, position='RB',
                week=1, season=season, season_type='REG', carries=carries)


class ProjectionCoverageTests(unittest.TestCase):
    def test_per_player_fallback_preserves_observed_zero_and_missing_rookie(self):
        players = [player(n) for n in ['Current', 'Veteran', 'Rookie']]
        with patch('nfl_auto_data._fetch_nflverse_season_rows') as fetch:
            report = apply_auto_nfl_context(players, season=2026, fetch_external=False,
                usage_rows=[row('Current', 0)], usage_season=2026,
                prior_usage_rows=[row('Current', 20, 2025), row('Veteran', 15, 2025)])
            fetch.assert_not_called()
        self.assertEqual((report['usage_current_matches'], report['usage_prior_matches']), (1, 1))
        self.assertEqual(players[0]['NFLRecentCarries'], 0)
        self.assertEqual(players[0]['NFLUsageSeason'], 2026)
        self.assertEqual(players[1]['NFLRecentCarries'], 15)
        self.assertEqual(players[1]['NFLUsageSeason'], 2025)
        self.assertEqual(players[2]['NFLUsageGames'], 0)
        self.assertIsNone(players[2]['NFLUsageSeason'])

    def test_live_fetch_loads_prior_when_current_is_partial(self):
        with patch('nfl_auto_data.fetch_recent_usage_with_fallback', return_value=([row('Current', 0)], 2026)), patch('nfl_auto_data._fetch_nflverse_season_rows', return_value=[row('Veteran', 15, 2025)]) as fetch:
            players = [player('Veteran')]
            apply_auto_nfl_context(players, season=2026, sleeper_data={}, weather_by_game={})
            fetch.assert_called_once_with(2025)
            self.assertEqual(players[0]['NFLUsageSource'], 'prior_season')

    def test_prior_unavailable_and_ambiguous_name_do_not_invent_usage(self):
        for prior in [[], [row('Veteran', 15, 2025, 'BUF'), row('Veteran', 9, 2025, 'KC')]]:
            players = [player('Veteran')]
            apply_auto_nfl_context(players, season=2026, fetch_external=False,
                usage_rows=[row('Current', 1)], usage_season=2026, prior_usage_rows=prior)
            self.assertEqual(players[0]['NFLUsageGames'], 0)

    def test_coverage_distinguishes_estimates_zero_and_missing(self):
        players = [player(n) for n in ['Estimate', 'Zero', 'Missing']]
        players[0].update(ProjectionSource='Automatic workload estimate', NFLUsageSeason=2025, NFLUsageGames=4)
        players[1].update(ProjectionSource='Manual override', BaseProjection=0, NFLUsageSeason=2026, NFLUsageGames=1)
        players[2].update(ProjectionSource='Missing forecast', BaseProjection=None)
        result = summarize_projection_coverage(players)
        self.assertEqual(result['eligible'], 3)
        self.assertEqual(result['missing_count'], 1)
        self.assertEqual(result['usage']['Prior-season usage'], 1)
        self.assertEqual(result['usage']['Current-season usage'], 1)
        self.assertIn('Missing [SEA RB]', '\n'.join(format_projection_coverage(result)))

if __name__ == '__main__':
    unittest.main()
