from __future__ import annotations

import unittest
from unittest import mock

import nfl_auto_data
from nfl_auto_data import (
    MAX_NFL_ADJUSTMENT,
    apply_auto_nfl_context,
    apply_context_adjustment,
    fetch_recent_usage_with_fallback,
    normalize_nfl_team,
    refresh_live_nfl_data,
    score_matchup,
    score_vegas,
    score_weather,
)


def _player(**overrides):
    player = {
        "Name": "Example Runner",
        "Team": "JAX",
        "Opponent": "BUF",
        "Position": "RB",
        "GameKey": "JAX@BUF",
        "HomeTeam": "BUF",
        "GameInfo": "JAX@BUF 09/13/2026 01:00PM ET",
        "FlexProjection": 15.0,
        "CptProjection": 22.5,
        "BaseProjection": 15.0,
    }
    player.update(overrides)
    return player


def _usage_row(
    name: str,
    team: str,
    opponent: str,
    position: str,
    week: int,
    *,
    carries: float = 0,
    targets: float = 0,
    attempts: float = 0,
    points: float = 0,
    season: int = 2025,
):
    return {
        "player_display_name": name,
        "recent_team": team,
        "opponent_team": opponent,
        "position": position,
        "season": str(season),
        "week": str(week),
        "season_type": "REG",
        "carries": str(carries),
        "targets": str(targets),
        "attempts": str(attempts),
        "fantasy_points_ppr": str(points),
    }


class NFLContextTests(unittest.TestCase):
    def test_enrichment_combines_sleeper_usage_matchup_weather_and_neutral_vegas(self):
        players = [_player()]
        sleeper = {
            "1": {
                "full_name": "Example Runner",
                "team": "JAC",
                "position": "RB",
                "depth_chart_position": "RB",
                "depth_chart_order": 1,
                "practice_participation": "Full Participation",
                "injury_status": None,
            }
        }
        rows = []
        for week in range(1, 5):
            rows.append(_usage_row("Example Runner", "JAC", "HOU", "RB", week, carries=18, targets=5, points=21))
            # BUF permits more RB production than the comparison defenses.
            rows.append(_usage_row(f"Buffalo Opp {week}", "MIA", "BUF", "RB", week, carries=15, targets=5, points=28))
            rows.append(_usage_row(f"New England Opp {week}", "NYJ", "NE", "RB", week, carries=15, targets=5, points=10))

        summary = apply_auto_nfl_context(
            players,
            sleeper_data=sleeper,
            usage_rows=rows,
            usage_season=2025,
            weather_by_game={
                "JAX@BUF": {
                    "temperature_f": 28,
                    "wind_mph": 22,
                    "precipitation_probability": 70,
                    "weather_code": 71,
                    "indoor": False,
                }
            },
            fetch_external=False,
            season=2026,
        )

        player = players[0]
        self.assertEqual(summary["sleeper"], 1)
        self.assertEqual(summary["usage"], 1)
        self.assertEqual(player["NFLUsageSeason"], 2025)
        self.assertEqual(player["NFLRole"], "RB1 FP")
        self.assertGreater(player["NFLUsageScore"], 0)
        self.assertGreater(player["NFLMatchupScore"], 0)
        self.assertLess(player["NFLWeatherScore"], 0)
        self.assertEqual(player["NFLVegas"], 0.0)
        self.assertAlmostEqual(player["FlexProjection"], 15.0 + player["NFLAdjScore"])
        self.assertLessEqual(abs(player["NFLAdjScore"]), MAX_NFL_ADJUSTMENT)

    def test_unavailable_sources_fall_back_to_neutral(self):
        players = [_player()]
        summary = apply_auto_nfl_context(players, fetch_external=False, season=2026)
        player = players[0]
        self.assertEqual(summary["sleeper"], 0)
        self.assertEqual(summary["usage"], 0)
        self.assertEqual(player["NFLUsageScore"], 0.0)
        self.assertEqual(player["NFLMatchupScore"], 0.0)
        self.assertEqual(player["NFLRoleScore"], 0.0)
        self.assertEqual(player["NFLWeatherScore"], 0.0)
        self.assertEqual(player["NFLVegas"], 0.0)
        self.assertEqual(player["NFLAdjScore"], 0.0)
        self.assertEqual(player["FlexProjection"], 15.0)

    def test_nflverse_uses_prior_season_fallback(self):
        prior_rows = [_usage_row("Prior Player", "KC", "LV", "WR", 18, targets=8, points=17)]
        with mock.patch("nfl_auto_data._fetch_nflverse_season_rows", side_effect=[[], prior_rows]) as fetch:
            rows, used_season = fetch_recent_usage_with_fallback(2026)
        self.assertEqual(rows, prior_rows)
        self.assertEqual(used_season, 2025)
        self.assertEqual([call.args[0] for call in fetch.call_args_list], [2026, 2025])

    def test_team_aliases_normalize_across_dk_sleeper_and_nflverse(self):
        aliases = {
            "JAC": "JAX",
            "OAK": "LV",
            "SD": "LAC",
            "STL": "LAR",
            "WSH": "WAS",
            "GNB": "GB",
            "NWE": "NE",
            "SFO": "SF",
        }
        for raw, expected in aliases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_nfl_team(raw), expected)

    def test_matchup_scoring_is_directional_neutral_and_bounded(self):
        self.assertGreater(score_matchup(28, 20), 0)
        self.assertLess(score_matchup(12, 20), 0)
        self.assertEqual(score_matchup(20, 20), 0.0)
        self.assertEqual(score_matchup(20, 0), 0.0)
        self.assertLessEqual(score_matchup(100, 20), 1.0)
        self.assertGreaterEqual(score_matchup(0, 20), -1.0)

    def test_weather_is_neutral_indoor_and_position_aware_outdoor(self):
        indoor = score_weather(
            temperature_f=10,
            wind_mph=35,
            precipitation_probability=100,
            position="WR",
            indoor=True,
        )
        severe_wr = score_weather(
            temperature_f=18,
            wind_mph=25,
            precipitation_probability=80,
            weather_code=71,
            position="WR",
        )
        severe_rb = score_weather(
            temperature_f=18,
            wind_mph=25,
            precipitation_probability=80,
            weather_code=71,
            position="RB",
        )
        severe_dst = score_weather(
            temperature_f=18,
            wind_mph=25,
            precipitation_probability=80,
            weather_code=71,
            position="DST",
        )
        self.assertEqual(indoor, 0.0)
        self.assertLess(severe_wr, 0)
        self.assertLess(severe_rb, 0)
        self.assertGreater(severe_rb, severe_wr)
        self.assertGreater(severe_dst, 0)

    def test_projection_adjustment_is_capped_at_plus_and_minus_3_5(self):
        positive = _player()
        negative = _player()
        plus = apply_context_adjustment(positive, usage=5, matchup=5, role=5, weather=5, vegas=0)
        minus = apply_context_adjustment(negative, usage=-5, matchup=-5, role=-5, weather=-5, vegas=0)
        self.assertEqual(plus, 3.5)
        self.assertEqual(minus, -3.5)
        self.assertEqual(positive["FlexProjection"], 18.5)
        self.assertEqual(negative["FlexProjection"], 11.5)

    def test_context_applies_vegas_team_total_and_adjustment(self):
        player = _player(Team="BUF", Opponent="JAX", GameKey="JAX@BUF", HomeTeam="BUF", Position="QB")
        summary = apply_auto_nfl_context(
            [player],
            sleeper_data={},
            usage_rows=[],
            weather_by_game={},
            odds_by_game={
                "JAX@BUF": {
                    "home_team": "BUF", "away_team": "JAX", "game_total": 50,
                    "home_spread": -4, "away_spread": 4, "home_implied": 27,
                    "away_implied": 23, "bookmakers": 5, "last_update": "2026-09-12T20:00:00Z",
                }
            },
            fetch_external=False,
            season=2026,
        )
        self.assertEqual(summary["odds_state"], "ok")
        self.assertEqual(summary["odds_matched_games"], 1)
        self.assertEqual(player["NFLVegasTeamTotal"], 27.0)
        self.assertEqual(player["NFLVegasGameTotal"], 50.0)
        self.assertGreater(player["NFLVegas"], 0.0)
        self.assertGreater(player["FlexProjection"], 15.0)

    def test_live_refresh_preserves_locked_out_player_and_reports_conflict(self):
        player = _player(LockFlex=True, FadeFlex=False, FadeCpt=False)
        sleeper = {
            "1": {
                "full_name": "Example Runner", "team": "JAC", "position": "RB",
                "depth_chart_position": "RB", "depth_chart_order": 1,
                "status": "Active", "active": True, "injury_status": "Out",
                "practice_participation": "Did Not Participate",
            }
        }
        summary = refresh_live_nfl_data(
            [player],
            sleeper_data=sleeper,
            odds_result={"state": "not_configured", "games": {}, "remaining": None, "message": ""},
            fetch_external=False,
        )
        self.assertTrue(player["LockFlex"])
        self.assertFalse(bool(player.get("FadeFlex")))
        self.assertEqual(player["NFLAvailability"], "OUT")
        self.assertTrue(player["LiveStatusConflict"])
        self.assertEqual(summary["locked_conflicts"], 1)

    def test_out_starter_promotes_next_active_player_and_refresh_can_reverse_it(self):
        starter = _player(Name="Starting Runner")
        backup = _player(Name="Backup Runner", FlexProjection=10.0, CptProjection=15.0, BaseProjection=10.0)
        out_sleeper = {
            "1": {
                "full_name": "Starting Runner", "team": "JAC", "position": "RB",
                "depth_chart_position": "RB", "depth_chart_order": 1,
                "status": "Active", "active": True, "injury_status": "Out",
            },
            "2": {
                "full_name": "Backup Runner", "team": "JAC", "position": "RB",
                "depth_chart_position": "RB", "depth_chart_order": 2,
                "status": "Active", "active": True, "injury_status": None,
            },
        }
        summary = apply_auto_nfl_context(
            [starter, backup],
            sleeper_data=out_sleeper,
            usage_rows=[],
            weather_by_game={},
            odds_by_game={},
            fetch_external=False,
            season=2026,
        )

        self.assertEqual(summary["replacement_promotions"], 1)
        self.assertEqual(backup["NFLReplacementBoost"], 0.5)
        self.assertEqual(backup["NFLReplacementFor"], "Starting Runner")
        self.assertIn("NEXT UP", backup["NFLRole"])
        self.assertAlmostEqual(backup["FlexProjection"], 10.55)

        active_sleeper = {
            **out_sleeper,
            "1": {**out_sleeper["1"], "injury_status": None},
        }
        refreshed = refresh_live_nfl_data(
            [starter, backup],
            sleeper_data=active_sleeper,
            odds_result={"state": "not_configured", "games": {}, "remaining": None, "message": ""},
            fetch_external=False,
        )
        self.assertEqual(refreshed["replacement_promotions"], 0)
        self.assertEqual(backup["NFLReplacementBoost"], 0.0)
        self.assertNotIn("NEXT UP", backup["NFLRole"])
        self.assertAlmostEqual(backup["FlexProjection"], 10.05)
        self.assertFalse(bool(starter.get("FadeFlex")))

    def test_draftkings_questionable_status_survives_blank_sleeper_injury(self):
        player = _player(Status="Q")
        sleeper = {
            "1": {
                "full_name": "Example Runner", "team": "JAC", "position": "RB",
                "depth_chart_position": "RB", "depth_chart_order": 1,
                "status": "Active", "active": True, "injury_status": None,
            }
        }
        refresh_live_nfl_data(
            [player],
            sleeper_data=sleeper,
            odds_result={"state": "not_configured", "games": {}, "remaining": None, "message": ""},
            fetch_external=False,
        )
        self.assertEqual(player["InjuryStatus"], "QUESTIONABLE")
        self.assertEqual(player["NFLAvailability"], "QUESTIONABLE")
        self.assertFalse(bool(player.get("FadeFlex")))

    def test_vegas_scoring_rewards_high_totals_and_favored_defenses(self):
        self.assertGreater(score_vegas(team_implied=28, opponent_implied=20, team_spread=-6, position="QB"), 0)
        self.assertLess(score_vegas(team_implied=17, opponent_implied=28, team_spread=8, position="WR"), 0)
        self.assertGreater(score_vegas(team_implied=24, opponent_implied=17, team_spread=-7, position="DST"), 0)

if __name__ == "__main__":
    unittest.main()

