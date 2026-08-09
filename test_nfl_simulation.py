from __future__ import annotations

import unittest

from nfl_simulation import (
    build_nfl_role_pool,
    generate_nfl_field_lineups,
    simulate_nfl_contest,
    simulate_nfl_field_ownership,
)
from optimizers import MultiSportClassicOptimizer, lineup_grade_for_sport
from test_nfl_logic import _fixture_players


class NFLSimulationTests(unittest.TestCase):
    def test_role_pool_removes_inactive_and_backup_quarterbacks(self):
        players = _fixture_players()
        players.extend([
            {
                "Name": "BUF inactive QB", "Team": "BUF", "Opponent": "MIA",
                "GameKey": "BUF@MIA", "Position": "QB", "FlexID": "inactive",
                "FlexSalary": 9000, "FlexProjection": 40, "Status": "OUT",
            },
            {
                "Name": "BUF backup QB", "Team": "BUF", "Opponent": "MIA",
                "GameKey": "BUF@MIA", "Position": "QB", "FlexID": "backup",
                "FlexSalary": 3000, "FlexProjection": 5, "NFLDepthOrder": 2,
            },
        ])
        pool = build_nfl_role_pool(players)
        buf_qbs = [player for player in pool if player["Team"] == "BUF" and player["Position"] == "QB"]
        self.assertEqual(len(buf_qbs), 1)
        self.assertNotEqual(buf_qbs[0]["FlexID"], "inactive")
        self.assertNotEqual(buf_qbs[0]["FlexID"], "backup")

    def test_field_generator_counts_only_complete_near_cap_lineups(self):
        players = _fixture_players()
        lineups, role_pool = generate_nfl_field_lineups(players, 80, seed=17)
        self.assertEqual(len(role_pool), 64)
        self.assertEqual(len(lineups), 80)
        for lineup in lineups:
            self.assertEqual(len(lineup), 9)
            self.assertEqual(len({player["FlexID"] for player in lineup}), 9)
            salary = sum(float(player["FlexSalary"]) for player in lineup)
            self.assertGreaterEqual(salary, 49000)
            self.assertLessEqual(salary, 50000)
            qb = next(player for player in lineup if player["Position"] == "QB")
            dst = next(player for player in lineup if player["Position"] == "DST")
            self.assertNotEqual(dst["Team"], qb["Opponent"])

        ownership = simulate_nfl_field_ownership(players, 80)
        self.assertEqual(ownership["meta"]["valid_lineups"], 80)
        self.assertAlmostEqual(sum(ownership["total"].values()), 900.0, places=6)

    def test_contest_sim_attaches_slate_relative_edge_metrics(self):
        players = _fixture_players()
        candidates = MultiSportClassicOptimizer(
            players,
            sport="NFL",
            build_style="Strategic",
            salary_strategy="Near Cap",
        ).build_lineups(24)
        result = simulate_nfl_contest(
            candidates,
            players,
            scenarios=120,
            field_lineup_count=180,
            seed=29,
        )
        lineups = result["lineups"]
        self.assertEqual(len(lineups), 24)
        self.assertEqual(result["report"]["scenarios"], 120)
        edges = [lineup.sim_metrics["sim_edge"] for lineup in lineups]
        self.assertGreater(max(edges), min(edges))
        self.assertTrue(all(0.0 <= edge <= 100.0 for edge in edges))
        self.assertTrue(all(lineup.sim_metrics["sim_scenarios"] == 120 for lineup in lineups))

        grade = lineup_grade_for_sport(lineups[0], "NFL", 50000)
        self.assertAlmostEqual(grade["score"], lineups[0].sim_metrics["sim_edge"])
        self.assertIn("sim_top_one_pct", grade)
        self.assertIn(grade["grade"], {"A", "B", "C", "D"})


if __name__ == "__main__":
    unittest.main()
