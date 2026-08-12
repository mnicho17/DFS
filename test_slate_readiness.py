from __future__ import annotations

import unittest
from datetime import datetime, timezone

from nfl_simulation import compare_nfl_lineups_to_preset, generate_nfl_field_lineups, nfl_field_preset
from slate_readiness import audit_slate
from test_nfl_logic import _fixture_players


class SlateReadinessTests(unittest.TestCase):
    def test_empty_slate_is_blocked_with_a_load_action(self):
        report = audit_slate([], sport="NFL", mode="classic")
        self.assertEqual(report["status"], "blocked")
        self.assertGreater(report["blockers"], 0)
        self.assertIn("Load a DraftKings salary CSV", report["text"])

    def test_complete_nfl_inputs_only_review_the_missing_portfolio(self):
        players = _fixture_players()
        ownership = 900.0 / len(players)
        for player in players:
            player["ProjOwnPct"] = ownership
            player["NFLDepthOrder"] = 1
            player["NFLAvailability"] = "ACTIVE"
        now = datetime(2026, 9, 13, 16, 0, tzinfo=timezone.utc)
        report = audit_slate(
            players,
            sport="NFL",
            mode="classic",
            field_preset=nfl_field_preset("150-Max"),
            live_summary={
                "sleeper_state": "ok", "sleeper": len(players),
                "checked_at": "2026-09-13T15:55:00Z",
                "odds_state": "ok", "odds_matched_games": 8,
            },
            now=now,
        )
        self.assertEqual(report["status"], "review")
        self.assertEqual(report["blockers"], 0)
        source_confidence = {source["name"]: source for source in report["sources"]}
        self.assertEqual(source_confidence["Player news / roles"]["confidence"], "High")
        self.assertEqual(source_confidence["Player news / roles"]["freshness"], "5 minutes old")
        portfolio = next(check for check in report["checks"] if check["key"] == "portfolio")
        self.assertEqual(portfolio["status"], "review")

    def test_locked_out_player_blocks_readiness(self):
        players = _fixture_players()
        players[0]["LockFlex"] = True
        players[0]["NFLAvailability"] = "OUT"
        report = audit_slate(players, sport="NFL", mode="classic")
        locks = next(check for check in report["checks"] if check["key"] == "locks")
        self.assertEqual(locks["status"], "block")
        self.assertIn(players[0]["Name"], locks["details"]["player_names"])
        self.assertEqual(report["status"], "blocked")

    def test_missing_inputs_and_role_findings_identify_players_for_click_through(self):
        players = _fixture_players()
        players[0]["FlexProjection"] = 0
        players[1]["ProjOwnPct"] = 0
        players[2]["NFLDepthOrder"] = 3
        report = audit_slate(players, sport="NFL", mode="classic")
        by_key = {check["key"]: check for check in report["checks"]}
        self.assertIn(players[0]["Name"], by_key["projections"]["details"]["player_names"])
        self.assertIn(players[1]["Name"], by_key["ownership"]["details"]["player_names"])
        self.assertIn(players[2]["Name"], by_key["roles"]["details"]["player_names"])

    def test_generated_lineups_receive_an_explainable_preset_fit(self):
        players = _fixture_players()
        for index, player in enumerate(players):
            player["ProjOwnPct"] = 5.0 + index % 18
        config = nfl_field_preset("150-Max")
        lineups, _ = generate_nfl_field_lineups(players, 80, seed=811, field_config=config)
        comparison = compare_nfl_lineups_to_preset(lineups, config)
        self.assertTrue(comparison["available"])
        self.assertGreaterEqual(comparison["fit_score"], 0.0)
        self.assertLessEqual(comparison["fit_score"], 100.0)
        self.assertIn("largest gap", comparison["summary"])
        self.assertEqual(
            set(comparison["components"]),
            {"salary", "qb_stacks", "bring_backs", "flex_mix", "ownership_coverage"},
        )


if __name__ == "__main__":
    unittest.main()
