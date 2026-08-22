from __future__ import annotations

import copy
import unittest

from entry_safety import build_entry_safety_report
from optimizers import lineup_slots_for_sport


def _player(name: str, position: str, salary: int) -> dict:
    player_id = name.replace(" ", "-")
    return {
        "Name": name,
        "Position": position,
        "FlexSalary": salary,
        "FlexID": player_id,
        "CptID": f"CPT-{player_id}",
        "CptSalary": salary * 1.5,
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
    return [
        [str(player["FlexID"]) for _, player in lineup_slots_for_sport(lineup, "NFL")]
        for lineup in lineups
    ]


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
            export_rows=[[players[0]["CptID"]] + [player["FlexID"] for player in players[1:]]],
            portfolio_report={"warnings": []},
            readiness_report={"checks": []},
            min_salary_pct=0.90,
        )
        salary_check = next(check for check in report["checks"] if check["key"] == "salary_cap")
        self.assertEqual(salary_check["status"], "pass")
        self.assertEqual(report["status"], "ready")

    def test_same_player_in_captain_and_flex_is_blocked(self):
        captain = _player("Duplicated Star", "WR", 9000)
        captain["Team"] = "T1"
        others = [
            captain,
            {**_player("Other One", "QB", 8000), "Team": "T2"},
            {**_player("Other Two", "RB", 7000), "Team": "T1"},
            {**_player("Other Three", "WR", 6000), "Team": "T2"},
            {**_player("Other Four", "TE", 5000), "Team": "T1"},
        ]
        lineup = {"Captain": captain, "Flex": others}
        report = build_entry_safety_report(
            [lineup],
            kind="showdown",
            sport="NFL",
            salary_cap=50000,
            export_rows=[[captain["CptID"]] + [player["FlexID"] for player in others]],
            portfolio_report={"warnings": []},
            readiness_report={"checks": []},
        )
        roster = next(check for check in report["checks"] if check["key"] == "rosters")
        self.assertEqual(roster["status"], "block")
        self.assertEqual(report["blocked_lineup_indexes"], [0])

    def test_classic_lineup_from_only_one_team_is_blocked(self):
        lineup = _nfl_lineup()
        for player in lineup:
            player["Team"] = "T1"
        report = self._report([lineup])
        diversity = next(check for check in report["checks"] if check["key"] == "team_diversity")
        self.assertEqual(diversity["status"], "block")
        self.assertEqual(diversity["lineup_indexes"], [0])

    def test_current_player_pool_overrides_stale_saved_availability(self):
        lineup = _nfl_lineup()
        current_pool = copy.deepcopy(lineup)
        current_pool[2]["NFLAvailability"] = "OUT"
        report = self._report([lineup], player_pool=current_pool)
        availability = next(check for check in report["checks"] if check["key"] == "availability")
        self.assertEqual(availability["status"], "block")
        self.assertIn("Running Back Two", availability["summary"])

    def test_player_missing_from_current_slate_is_blocked(self):
        lineup = _nfl_lineup()
        report = self._report([lineup], player_pool=copy.deepcopy(lineup[:-1]))
        membership = next(check for check in report["checks"] if check["key"] == "slate_membership")
        self.assertEqual(membership["status"], "block")
        self.assertEqual(membership["lineup_indexes"], [0])

    def test_same_name_with_a_different_slate_id_does_not_mask_missing_player(self):
        lineup = _nfl_lineup()
        current_pool = copy.deepcopy(lineup)
        current_pool[0]["FlexID"] = "different-slate-id"
        current_pool[0]["CptID"] = "different-captain-id"
        current_pool[0]["FlexNamePlusID"] = "Quarterback (different-slate-id)"
        report = self._report([lineup], player_pool=current_pool)
        membership = next(check for check in report["checks"] if check["key"] == "slate_membership")
        self.assertEqual(membership["status"], "block")
        self.assertEqual(membership["lineup_indexes"], [0])

    def test_showdown_requires_captain_specific_export_id(self):
        players = [
            {**_player(f"Player {index}", "WR", 5000 + index * 200), "Team": "T1" if index < 3 else "T2"}
            for index in range(6)
        ]
        lineup = {"Captain": players[0], "Flex": players[1:]}
        wrong_row = [[players[0]["FlexID"]] + [player["FlexID"] for player in players[1:]]]
        report = build_entry_safety_report(
            [lineup],
            kind="showdown",
            sport="NFL",
            salary_cap=50000,
            export_rows=wrong_row,
            portfolio_report={"warnings": []},
            readiness_report={"checks": []},
        )
        export_ids = next(check for check in report["checks"] if check["key"] == "export_ids")
        self.assertEqual(export_ids["status"], "block")
        self.assertEqual(export_ids["lineup_indexes"], [0])

    def test_missing_salary_data_blocks_export(self):
        lineup = _nfl_lineup()
        lineup[5]["FlexSalary"] = 0
        report = self._report([lineup])
        salary_data = next(check for check in report["checks"] if check["key"] == "salary_data")
        self.assertEqual(salary_data["status"], "block")
        self.assertEqual(salary_data["lineup_indexes"], [0])


if __name__ == "__main__":
    unittest.main()
