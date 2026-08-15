from __future__ import annotations

import itertools
import time
import unittest

from nfl_simulation import SimLineup
from portfolio_rules import portfolio_report, select_portfolio


def _player(name: str, team: str, game: str, projection: float, **extra):
    value = {
        "Name": name,
        "FlexNamePlusID": name,
        "FlexID": name,
        "CptID": f"CPT-{name}",
        "Team": team,
        "Opponent": "OPP",
        "GameKey": game,
        "FlexProjection": projection,
        "CptProjection": projection * 1.5,
        "ProjOwnPct": 10.0 + projection,
        "ProjCptOwnPct": 5.0 + projection / 2.0,
    }
    value.update(extra)
    return value


class PortfolioRulesTests(unittest.TestCase):
    def test_sim_portfolio_covers_distinct_tournament_scenarios(self):
        candidates = []
        for index, (edge, hits) in enumerate([
            (95.0, {0, 1}),
            (94.0, {0, 1}),
            (82.0, {2, 3}),
            (75.0, {4, 5}),
        ]):
            players = [_player(f"S{index}-{slot}", f"T{index}", f"G{index}", 25 - slot) for slot in range(3)]
            metrics = {
                "sim_edge": edge,
                "sim_return_index": edge,
                "duplicate_risk": 20.0,
                "sim_top_one_pct": len(hits) / 10.0 * 100.0,
                "sim_top_five_pct": len(hits) / 10.0 * 100.0,
                "sim_cash_rate": 30.0,
                "sim_bust_rate": 20.0,
                "sim_scenarios": 10,
            }
            candidates.append(SimLineup(
                players,
                metrics=metrics,
                top_hits=hits,
                top_five_hits=hits,
                scenario_values={scenario: 6.0 for scenario in hits},
            ))

        result = select_portfolio(candidates, 2, rules={"min_unique": 1}, kind="classic")
        selected_hits = set().union(*(lineup.sim_top_hits for lineup in result["lineups"]))
        edge_only_hits = set().union(*(lineup.sim_top_hits for lineup in candidates[:2]))

        self.assertGreater(len(selected_hits), len(edge_only_hits))
        self.assertEqual(result["report"]["sim_summary"]["top_one_scenarios_covered"], len(selected_hits))
        self.assertIn("SIM portfolio", result["report"]["text"])

    def test_sim_portfolio_does_not_use_a_c_grade_only_for_novelty(self):
        candidates = []
        for index, (edge, hits) in enumerate(((95.0, {0}), (90.0, {0}), (60.0, {1}))):
            players = [_player(f"Q{index}-{slot}", f"T{index}", f"G{index}", 25 - slot) for slot in range(3)]
            candidates.append(SimLineup(
                players,
                metrics={
                    "sim_edge": edge,
                    "sim_return_index": edge,
                    "duplicate_risk": 20.0,
                    "sim_top_one_pct": 10.0,
                    "sim_top_five_pct": 20.0,
                    "sim_cash_rate": 30.0,
                    "sim_bust_rate": 20.0,
                    "sim_scenarios": 10,
                },
                top_hits=hits,
                top_five_hits=hits,
                scenario_values={scenario: 6.0 for scenario in hits},
            ))

        result = select_portfolio(candidates, 2, rules={"min_unique": 1}, kind="classic")
        selected_edges = {lineup.sim_metrics["sim_edge"] for lineup in result["lineups"]}

        self.assertEqual(selected_edges, {95.0, 90.0})

    def test_sim_portfolio_selection_stays_fast_at_150(self):
        candidates = []
        for index in range(600):
            players = [_player(f"P{index}-{slot}", f"T{index % 16}", f"G{index % 8}", 22 - slot) for slot in range(9)]
            hits = {(index * 7 + offset * 19) % 200 for offset in range(5)}
            candidates.append(SimLineup(
                players,
                metrics={
                    "sim_edge": float(40 + index % 60),
                    "sim_return_index": float(35 + index % 65),
                    "duplicate_risk": float(index % 100),
                    "sim_top_one_pct": 2.5,
                    "sim_top_five_pct": 8.0,
                    "sim_cash_rate": 24.0,
                    "sim_bust_rate": 32.0,
                    "sim_scenarios": 200,
                },
                top_hits=hits,
                top_five_hits=hits,
                scenario_values={scenario: 6.0 for scenario in hits},
            ))

        started = time.perf_counter()
        result = select_portfolio(candidates, 150, rules={"min_unique": 2}, kind="classic")
        elapsed = time.perf_counter() - started

        self.assertEqual(len(result["lineups"]), 150)
        self.assertLess(elapsed, 5.0)
        self.assertGreater(result["report"]["sim_summary"]["top_one_scenarios_covered"], 0)

    def test_total_minimum_and_maximum_exposure_are_enforced(self):
        a = _player("A", "T1", "G1", 30, MinPct=50, MaxPct=50)
        b = _player("B", "T2", "G2", 29, MaxPct=25)
        others = [_player(name, f"T{index}", f"G{index}", 20 - index) for index, name in enumerate("CDEFGH", 3)]
        candidates = [
            [a, b, others[0]],
            [a, others[1], others[2]],
            [a, others[3], others[4]],
            [b, others[0], others[1]],
            [others[0], others[2], others[4]],
            [others[1], others[3], others[5]],
            [others[2], others[4], others[5]],
        ]
        result = select_portfolio(
            candidates,
            4,
            rules={"min_unique": 1},
            kind="classic",
            refinement_passes=3,
        )
        report = result["report"]
        exposures = {row["name"]: row["count"] for row in report["players"]}
        self.assertEqual(len(result["lineups"]), 4)
        self.assertEqual(exposures["A"], 2)
        self.assertLessEqual(exposures.get("B", 0), 1)
        self.assertFalse(any("A total exposure" in warning for warning in report["warnings"]))
        self.assertIn("refinement_swaps", report)

    def test_refinement_honors_its_time_stop_without_changing_the_greedy_result(self):
        players = [_player(name, f"T{index}", f"G{index}", 30 - index) for index, name in enumerate("ABCDEFGHI")]
        candidates = [list(combo) for combo in itertools.combinations(players, 3)]

        baseline = select_portfolio(candidates, 6, rules={"min_unique": 1}, kind="classic")
        stopped = select_portfolio(
            candidates,
            6,
            rules={"min_unique": 1},
            kind="classic",
            refinement_passes=3,
            refinement_stop_callback=lambda: True,
        )

        self.assertEqual(stopped["lineups"], baseline["lineups"])
        self.assertEqual(stopped["report"]["refinement_swaps"], 0)

    def test_remaining_time_polish_lowers_duplication_without_losing_sim_strength(self):
        def candidate(prefix, projection, edge, return_index, duplicate_risk, hits):
            players = [
                _player(f"{prefix}-{slot}", f"T{prefix}", f"G{prefix}", projection / 3.0)
                for slot in range(3)
            ]
            return SimLineup(
                players,
                metrics={
                    "sim_edge": edge,
                    "sim_return_index": return_index,
                    "duplicate_risk": duplicate_risk,
                    "sim_top_one_pct": 10.0,
                    "sim_top_five_pct": 20.0,
                    "sim_cash_rate": 30.0,
                    "sim_bust_rate": 20.0,
                    "sim_scenarios": 10,
                },
                top_hits=hits,
                top_five_hits=hits,
                scenario_values={scenario: 6.0 for scenario in hits},
            )

        candidates = [
            candidate("A", 75.0, 95.0, 95.0, 20.0, {0}),
            candidate("B", 75.0, 94.0, 94.0, 80.0, {1}),
            candidate("C", 65.0, 94.0, 94.0, 60.0, {1}),
        ]
        baseline = select_portfolio(candidates, 2, rules={"min_unique": 1}, kind="classic")
        polished = select_portfolio(
            candidates,
            2,
            rules={"min_unique": 1},
            kind="classic",
            refinement_passes=8,
            refinement_polish_duplication=True,
        )

        baseline_sim = baseline["report"]["sim_summary"]
        polished_sim = polished["report"]["sim_summary"]
        self.assertGreater(polished["report"]["duplication_refinement_swaps"], 0)
        self.assertLess(
            polished_sim["average_duplicate_risk"],
            baseline_sim["average_duplicate_risk"],
        )
        self.assertGreaterEqual(polished_sim["average_edge"], baseline_sim["average_edge"] - 0.5)
        self.assertIn("local optimum", polished["report"]["refinement_stop_reason"])

    def test_showdown_captain_minimum_and_maximum(self):
        a = _player("A", "T1", "G1", 30, MinCptPct=25, MaxCptPct=25)
        players = [a] + [_player(name, "T2", "G1", 20 - index) for index, name in enumerate("BCDEFGHI", 1)]
        candidates = []
        for captain in players[:6]:
            for offset in range(3):
                flex = [player for player in players if player is not captain][offset:offset + 5]
                if len(flex) == 5:
                    candidates.append({"Captain": captain, "Flex": flex})
        result = select_portfolio(candidates, 4, rules={"min_unique": 1}, kind="showdown")
        exposure = {row["name"]: row for row in result["report"]["players"]}
        self.assertEqual(len(result["lineups"]), 4)
        self.assertEqual(exposure["A"]["cpt_count"], 1)

    def test_groups_and_team_game_limits(self):
        a = _player("A", "T1", "G1", 30)
        b = _player("B", "T2", "G2", 29)
        c = _player("C", "T3", "G3", 28)
        d = _player("D", "T4", "G4", 27)
        e = _player("E", "T1", "G1", 26)
        f = _player("F", "T2", "G2", 25)
        candidates = [[a, c], [a, d], [b, c], [b, d], [a, c, d], [e, c], [f, d]]
        rules = {
            "min_unique": 1,
            "max_team_pct": 50,
            "max_game_pct": 50,
            "groups": [
                {"type": "at_least_one", "player_keys": ["A", "B"]},
                {"type": "never_together", "player_keys": ["C", "D"]},
            ],
        }
        result = select_portfolio(candidates, 4, rules=rules, kind="classic")
        self.assertEqual(len(result["lineups"]), 4)
        for lineup in result["lineups"]:
            keys = {player["Name"] for player in lineup}
            self.assertTrue(keys & {"A", "B"})
            self.assertLessEqual(len(keys & {"C", "D"}), 1)
        self.assertTrue(all(row["pct"] <= 50.0 for row in result["report"]["teams"]))
        self.assertTrue(all(row["pct"] <= 50.0 for row in result["report"]["games"]))

    def test_minimum_uniques_and_safe_relaxation(self):
        players = [_player(name, f"T{index}", f"G{index}", 30 - index) for index, name in enumerate("ABCDEFGH")]
        strict_candidates = [
            [players[0], players[1], players[2]],
            [players[0], players[3], players[4]],
            [players[1], players[5], players[6]],
        ]
        strict = select_portfolio(strict_candidates, 3, rules={"min_unique": 2}, kind="classic")
        self.assertEqual(len(strict["lineups"]), 3)
        self.assertGreaterEqual(strict["report"]["min_observed_unique"], 2)

        tight = select_portfolio(
            [[players[0], players[1], players[2]], [players[0], players[1], players[3]]],
            2,
            rules={"min_unique": 2},
            kind="classic",
        )
        self.assertEqual(len(tight["lineups"]), 2)
        self.assertTrue(any("relaxed" in warning.lower() for warning in tight["report"]["warnings"]))

    def test_retained_lineups_are_preserved_while_open_slots_are_selected(self):
        players = [
            _player(name, f"T{index}", f"G{index}", 30 - index)
            for index, name in enumerate("ABCDEFGHIJKL")
        ]
        retained = [players[0], players[1], players[2]]
        candidates = [
            list(retained),
            [players[3], players[4], players[5]],
            [players[6], players[7], players[8]],
            [players[9], players[10], players[11]],
        ]

        result = select_portfolio(
            candidates,
            3,
            rules={"min_unique": 2},
            kind="classic",
            retained_lineups=[retained],
        )

        self.assertEqual(len(result["lineups"]), 3)
        self.assertIs(result["lineups"][0], retained)
        self.assertEqual(result["retained_count"], 1)
        self.assertEqual(result["candidate_count"], 3)
        signatures = {
            tuple(sorted(player["Name"] for player in lineup))
            for lineup in result["lineups"]
        }
        self.assertEqual(len(signatures), 3)

    def test_impossible_group_returns_neutral_partial_result_with_warning(self):
        players = [_player(name, "T1", "G1", 20) for name in "ABC"]
        result = select_portfolio(
            [players],
            3,
            rules={
                "groups": [{"type": "at_least_one", "player_keys": ["MISSING"]}],
                "player_constraints": {
                    "MISSING": {"Name": "Missing Player", "MinPct": 50},
                },
            },
            kind="classic",
        )
        self.assertEqual(result["lineups"], [])
        self.assertTrue(any("Built 0 of 3" in warning for warning in result["report"]["warnings"]))
        self.assertTrue(any("Missing Player total exposure" in warning for warning in result["report"]["warnings"]))

    def test_large_candidate_pool_selects_150_without_duplicates(self):
        players = [
            _player(f"P{index:02d}", f"T{index % 5}", f"G{index % 3}", 40 - index * 0.4)
            for index in range(20)
        ]
        candidates = []
        for combo in itertools.islice(itertools.combinations(players, 6), 500):
            candidates.append({"Captain": combo[0], "Flex": list(combo[1:])})
        result = select_portfolio(
            candidates,
            150,
            rules={"min_unique": 1, "balance_ownership": True},
            kind="showdown",
        )
        self.assertEqual(len(result["lineups"]), 150)
        signatures = {
            (lineup["Captain"]["Name"], tuple(sorted(player["Name"] for player in lineup["Flex"])))
            for lineup in result["lineups"]
        }
        self.assertEqual(len(signatures), 150)

    def test_report_flags_group_violations(self):
        a = _player("A", "T1", "G1", 20)
        b = _player("B", "T2", "G2", 19)
        report = portfolio_report(
            [[a, b]],
            {"groups": [{"type": "never_together", "player_keys": ["A", "B"]}]},
            kind="classic",
        )
        self.assertFalse(report["compliant"])
        self.assertTrue(any("player-group" in warning for warning in report["warnings"]))


if __name__ == "__main__":
    unittest.main()
