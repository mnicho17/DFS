from __future__ import annotations

import copy
import unittest

from entry_safety import build_entry_safety_report


def _player(name: str, position: str, salary: int) -> dict:
    player_id = name.replace(" ", "-")
    return {
        "Name": name,
        "Position": position,
        "FlexSalary": salary,
        "FlexID": player_id,
        "FlexNamePlusID": f"{name} ({player_id})",
        "NFLAvailability": "ACTIVE",
        "Team": "T1" if len(name) % 2 else "T2",
        "GameKey": "T1@T2",
    }


def _nfl_lineup() -> list[dict]:
    return [
        _player("Quarterback", "QB", 7000),
        _player("Running Back One", "RB", 6500),
        _player("Running Back Two", "RB", 6000),
        _player("Receiver One", "WR", 6000),
        _player("Receiver Two", "WR", 5500),
        _player("Receiver Three", "WR", 5000),
        _player("Tight End", "TE", 4500),
        _player("Flex Back", "RB", 5500),
        _player("Defense", "DST", 3500),
    ]


def _rows(lineups: list[list[dict]]) -> list[list[str]]:
    return [[str(player["FlexID"]) for player in lineup] for lineup in lineups]


class EntrySafetyTests(unittest.TestCase):
    def _report(self, lineups, **overrides):
        params = {
            "kind": "classic",
            "sport": "NFL",
            "salary_cap": 50000,
            "export_rows": _rows(lineups),
            "portfolio_report": {"warnings": []},
            "readiness_report": {"checks": []},
            "min_salary_pct": 0.94,
        }
        params.update(overrides)
        return build_entry_safety_report(lineups, **params)

    def test_valid_saved_portfolio_is_ready(self):
        report = self._report([_nfl_lineup()])
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["blockers"], 0)
        self.assertEqual(report["reviews"], 0)
        self.assertIn("ENTRY SAFETY — READY", report["text"])

    def test_unavailable_player_blocks_export(self):
        lineup = _nfl_lineup()
        lineup[4]["NFLAvailability"] = "OUT"
        report = self._report([lineup])
        availability = next(check for check in report["checks"] if check["key"] == "availability")
        self.assertEqual(availability["status"], "block")
        self.assertIn("Receiver Two", availability["summary"])
        self.assertEqual(report["status"], "blocked")

    def test_duplicate_lineups_and_portfolio_rule_failures_block_export(self):
        lineup = _nfl_lineup()
        report = self._report(
            [lineup, copy.deepcopy(lineup)],
            portfolio_report={"warnings": ["Observed minimum uniqueness is 0; requested 2."]},
        )
        by_key = {check["key"]: check for check in report["checks"]}
        self.assertEqual(by_key["duplicates"]["status"], "block")
        self.assertEqual(by_key["portfolio_rules"]["status"], "block")
        self.assertEqual(report["status"], "blocked")

    def test_missing_export_id_is_a_roster_blocker(self):
        lineup = _nfl_lineup()
        rows = _rows([lineup])
        rows[0][3] = ""
        report = self._report([lineup], export_rows=rows)
        roster = next(check for check in report["checks"] if check["key"] == "rosters")
        self.assertEqual(roster["status"], "block")

    def test_questionable_player_and_stale_data_are_review_items(self):
        lineup = _nfl_lineup()
        lineup[0]["NFLAvailability"] = "QUESTIONABLE"
        report = self._report(
            [lineup],
            readiness_report={"checks": [{
                "key": "live_status", "label": "Player news and roles", "status": "review"
            }]},
        )
        self.assertEqual(report["status"], "review")
        self.assertEqual(report["blockers"], 0)
        self.assertGreaterEqual(report["reviews"], 2)

    def test_showdown_captain_salary_falls_back_to_flex_salary(self):
        players = [
            _player("Captain", "WR", 10000),
            _player("Flex One", "QB", 9000),
            _player("Flex Two", "RB", 8000),
            _player("Flex Three", "WR", 7000),
            _player("Flex Four", "TE", 6000),
            _player("Flex Five", "DST", 5000),
        ]
        lineup = {"Captain": players[0], "Flex": players[1:]}
        report = build_entry_safety_report(
            [lineup],
            kind="showdown",
            sport="NFL",
            salary_cap=50000,
            export_rows=[[player["FlexID"] for player in players]],
            portfolio_report={"warnings": []},
            readiness_report={"checks": []},
            min_salary_pct=0.90,
        )
        salary_check = next(check for check in report["checks"] if check["key"] == "salary_cap")
        self.assertEqual(salary_check["status"], "pass")
        self.assertEqual(report["status"], "ready")


if __name__ == "__main__":
    unittest.main()
