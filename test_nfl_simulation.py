from __future__ import annotations

import unittest

from nfl_simulation import (
    lineup_signature,
    build_nfl_role_pool,
    generate_nfl_field_lineups,
    nfl_field_preset,
    should_use_nfl_role_pool,
    simulate_nfl_contest,
    simulate_nfl_field_ownership,
)
from optimizers import MultiSportClassicOptimizer, lineup_grade_for_sport
from test_nfl_logic import _fixture_players


class NFLSimulationTests(unittest.TestCase):
    def test_role_pool_policy_covers_normal_classic_styles_and_preserves_broad_randomized_mode(self):
        for style in ("Strategic", "Balanced", "Contrarian", "Chalk"):
            self.assertTrue(should_use_nfl_role_pool(
                sport="NFL", kind="classic", build_style=style, sim_enabled=False
            ))
        self.assertFalse(should_use_nfl_role_pool(
            sport="NFL", kind="classic", build_style="Randomized", sim_enabled=False
        ))
        self.assertTrue(should_use_nfl_role_pool(
            sport="NFL", kind="classic", build_style="Randomized", sim_enabled=True
        ))
        self.assertFalse(should_use_nfl_role_pool(
            sport="NFL", kind="showdown", build_style="Strategic", sim_enabled=True
        ))
        self.assertFalse(should_use_nfl_role_pool(
            sport="MLB", kind="classic", build_style="Strategic", sim_enabled=True
        ))

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

        required_pool = build_nfl_role_pool(players, preserve_player_keys={"backup"})
        required_buf_ids = {
            player["FlexID"]
            for player in required_pool
            if player["Team"] == "BUF" and player["Position"] == "QB"
        }
        self.assertEqual(len(required_buf_ids), 2)
        self.assertIn("backup", required_buf_ids)

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
        self.assertTrue(all(0.0 <= lineup.sim_metrics["sim_return_index"] <= 100.0 for lineup in lineups))
        self.assertTrue(all(0.0 <= lineup.sim_metrics["sim_leverage"] <= 100.0 for lineup in lineups))
        self.assertTrue(all("sim_top_five_pct" in lineup.sim_metrics for lineup in lineups))
        self.assertTrue(all("sim_bust_rate" in lineup.sim_metrics for lineup in lineups))
        self.assertTrue(all(len(lineup.sim_metrics["sim_edge_drivers"]) == 6 for lineup in lineups))
        self.assertTrue(all("Duplication safety" in lineup.sim_metrics["sim_edge_components"] for lineup in lineups))
        self.assertTrue(all(lineup.sim_top_hits.issubset(lineup.sim_top_five_hits) for lineup in lineups))
        self.assertEqual(result["report"]["model"], "scenario-portfolio-v3")
        self.assertTrue(result["report"]["field_model_preset_comparison"]["available"])

        grade = lineup_grade_for_sport(lineups[0], "NFL", 50000)
        self.assertAlmostEqual(grade["score"], lineups[0].sim_metrics["sim_edge"])
        self.assertIn("sim_top_one_pct", grade)
        self.assertIn("sim_return_index", grade)
        self.assertIn("sim_edge_drivers", grade)
        self.assertIn(grade["grade"], {"A", "B", "C", "D"})

    def test_contest_sim_is_deterministic_for_the_same_seed(self):
        players = _fixture_players()
        candidates = MultiSportClassicOptimizer(
            players,
            sport="NFL",
            build_style="Strategic",
            salary_strategy="Near Cap",
        ).build_lineups(12)

        first = simulate_nfl_contest(candidates, players, scenarios=60, field_lineup_count=90, seed=117)
        second = simulate_nfl_contest(candidates, players, scenarios=60, field_lineup_count=90, seed=117)

        def snapshot(result):
            return [
                (
                    lineup_signature(lineup),
                    round(lineup.sim_metrics["sim_edge"], 8),
                    tuple(sorted(lineup.sim_top_hits)),
                    tuple(sorted(lineup.sim_top_five_hits)),
                )
                for lineup in result["lineups"]
            ]

        self.assertEqual(snapshot(first), snapshot(second))

    def test_contest_preset_controls_field_size_and_is_reported(self):
        players = _fixture_players()
        candidates = MultiSportClassicOptimizer(
            players,
            sport="NFL",
            build_style="Strategic",
            salary_strategy="Near Cap",
        ).build_lineups(8)
        config = nfl_field_preset("Single Entry")
        result = simulate_nfl_contest(
            candidates,
            players,
            scenarios=30,
            field_lineup_count=60,
            field_config=config,
            seed=811,
        )
        self.assertEqual(result["report"]["field_preset"], "Single Entry")
        self.assertEqual(result["report"]["field_size"], 5000)
        self.assertFalse(result["report"]["learned_field_model"])

    def test_real_field_reference_is_compared_without_enabling_learning(self):
        players = _fixture_players()
        candidates = MultiSportClassicOptimizer(players, sport="NFL").build_lineups(8)
        config = nfl_field_preset("150-Max", {
            "enabled": False,
            "reference": {
                "contests": 1,
                "entries": 593447,
                "duplicate_entry_pct": 65.67,
                "ownership_profile": {
                    "field": {
                        "avg_total_ownership": 155.0,
                        "avg_sub_five_players": 0.8,
                        "avg_sub_ten_players": 2.1,
                        "avg_twenty_plus_players": 3.0,
                        "avg_thirty_plus_players": 1.0,
                    }
                },
                "report_only": True,
            },
        })
        result = simulate_nfl_contest(
            candidates,
            players,
            scenarios=30,
            field_lineup_count=60,
            field_config=config,
            seed=812,
        )
        comparison = result["report"]["field_comparison"]
        self.assertTrue(comparison["available"])
        self.assertTrue(comparison["report_only"])
        self.assertEqual(comparison["real"]["entries"], 593447)
        self.assertIn("duplicate_entry_pct", comparison["differences"])

    def test_guarded_learned_profile_adds_only_a_small_candidate_fit_signal(self):
        players = _fixture_players()
        for index, player in enumerate(players):
            player["ProjOwnPct"] = 5.0 + (index % 18)
        candidates = MultiSportClassicOptimizer(players, sport="NFL").build_lineups(8)
        config = nfl_field_preset("150-Max", {
            "enabled": True,
            "entries": 3000,
            "contests": 3,
            "field_config": {
                "winning_ownership_profile": {
                    "lineups": 100,
                    "avg_total_ownership": 150.0,
                    "avg_sub_five_players": 1.0,
                    "avg_sub_ten_players": 2.0,
                    "avg_twenty_plus_players": 3.0,
                    "avg_thirty_plus_players": 1.0,
                }
            },
        })
        result = simulate_nfl_contest(
            candidates, players, scenarios=30, field_lineup_count=60,
            field_config=config, seed=813,
        )
        self.assertTrue(result["report"]["learned_field_model"])
        self.assertTrue(all(
            lineup.sim_metrics.get("learned_profile_fit") is not None
            for lineup in result["lineups"]
        ))


if __name__ == "__main__":
    unittest.main()
