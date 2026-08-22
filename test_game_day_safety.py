from __future__ import annotations

import copy
import unittest

from game_day_safety import build_final_lock_report


def _player(name: str, player_id: str, team: str, status: str = "ACTIVE") -> dict:
    return {
        "Name": name,
        "FlexID": player_id,
        "Team": team,
        "NFLAvailability": status,
    }


class GameDaySafetyTests(unittest.TestCase):
    def test_live_change_maps_to_exact_saved_lineups(self):
        alpha = _player("Alpha", "1", "A")
        beta = _player("Beta", "2", "B")
        gamma = _player("Gamma", "3", "C")
        report = build_final_lock_report(
            [[alpha, beta], [copy.deepcopy(beta), gamma]],
            kind="classic",
            player_pool=[alpha, beta, gamma],
            live_summary={
                "sleeper_state": "ok",
                "sleeper": 3,
                "total": 3,
                "changes": [{
                    "name": "Beta",
                    "team": "B",
                    "player_key": "2",
                    "before": ("", "Active", 1, "", ""),
                    "after": ("Questionable", "Active", 1, "Limited", ""),
                    "availability": "QUESTIONABLE",
                }],
            },
        )
        self.assertEqual(report["status"], "attention")
        self.assertEqual(report["affected_indexes"], [0, 1])
        self.assertEqual(report["changes"][0]["lineup_numbers"], [1, 2])

    def test_already_unavailable_player_is_affected_without_new_change(self):
        saved = _player("Alpha", "1", "A")
        current = copy.deepcopy(saved)
        current["NFLAvailability"] = "OUT"
        report = build_final_lock_report(
            [[saved]],
            kind="classic",
            player_pool=[current],
            live_summary={"sleeper_state": "ok", "sleeper": 1, "total": 1, "changes": []},
        )
        self.assertEqual(report["affected_indexes"], [0])
        self.assertEqual(report["unavailable_indexes"], [0])
        self.assertEqual(report["unavailable_players"], ["Alpha"])

    def test_unavailable_live_source_is_explicit(self):
        report = build_final_lock_report(
            [],
            kind="classic",
            player_pool=[],
            live_summary={"sleeper_state": "unavailable", "changes": []},
            used_cached_check=True,
        )
        self.assertEqual(report["status"], "unavailable")
        self.assertTrue(report["used_cached_check"])
        self.assertIn("could not be confirmed", report["text"])

    def test_same_name_change_maps_only_to_matching_team(self):
        team_a = _player("Shared Name", "1", "A")
        team_b = _player("Shared Name", "2", "B")
        report = build_final_lock_report(
            [[team_a], [team_b]],
            kind="classic",
            player_pool=[team_a, team_b],
            live_summary={
                "sleeper_state": "ok",
                "sleeper": 2,
                "total": 2,
                "changes": [{
                    "name": "Shared Name",
                    "team": "B",
                    "before": ("", "Active", 1, "", ""),
                    "after": ("Questionable", "Active", 1, "Limited", ""),
                    "availability": "QUESTIONABLE",
                }],
            },
        )
        self.assertEqual(report["affected_indexes"], [1])
        self.assertEqual(report["changes"][0]["lineup_numbers"], [2])


if __name__ == "__main__":
    unittest.main()
