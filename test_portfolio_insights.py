from __future__ import annotations

import unittest

from nfl_simulation import SimLineup
from portfolio_insights import build_portfolio_insights


def _player(name, position, team, opponent, salary, projection, ownership):
    return {
        "Name": name,
        "FlexID": name,
        "Position": position,
        "Team": team,
        "Opponent": opponent,
        "GameKey": "BUF@MIA",
        "FlexSalary": salary,
        "FlexProjection": projection,
        "ProjOwnPct": ownership,
    }


def _lineup(*, source: str, archetype: str, edge: float, duplicate: float, hits):
    players = [
        _player("BUF QB", "QB", "BUF", "MIA", 7000, 22, 15),
        _player("BUF RB", "RB", "BUF", "MIA", 6200, 16, 18),
        _player("MIA RB", "RB", "MIA", "BUF", 5900, 15, 12),
        _player("BUF WR1", "WR", "BUF", "MIA", 6500, 18, 20),
        _player("BUF WR2", "WR", "BUF", "MIA", 5100, 13, 8),
        _player("MIA WR1", "WR", "MIA", "BUF", 6000, 17, 14),
        _player("BUF TE", "TE", "BUF", "MIA", 4100, 11, 7),
        _player("MIA WR2", "WR", "MIA", "BUF", 4800, 12, 4),
        _player("BUF DST", "DST", "BUF", "MIA", 3400, 8, 9),
    ]
    return SimLineup(
        players,
        metrics={
            "sim_edge": edge,
            "sim_leverage": 71,
            "duplicate_risk": duplicate,
            "sim_top_one_pct": 3.4,
            "sim_return_index": 76,
            "sim_scenarios": 100,
        },
        top_hits=hits,
        candidate_source=source,
        candidate_archetype=archetype,
    )


class PortfolioInsightsTests(unittest.TestCase):
    def test_report_explains_quality_sources_construction_and_scenarios(self):
        lineups = [
            _lineup(source="scenario_built", archetype="Ceiling", edge=84, duplicate=28, hits={1, 2}),
            _lineup(source="optimizer", archetype="", edge=65, duplicate=74, hits={2, 5}),
        ]
        report = build_portfolio_insights(
            lineups,
            sport="NFL",
            kind="classic",
            field_preset="20-Max",
            portfolio_report={
                "players": [{"name": "BUF QB", "pct": 100.0}],
                "warnings": [],
            },
            sim_report={
                "candidate_sources": {
                    "generated": {"optimizer": 20, "field_shaped": 5, "scenario_built": 10},
                },
                "preset_comparison": {
                    "available": True,
                    "preset": "20-Max",
                    "fit_score": 86,
                    "summary": "Portfolio closely matches the 20-Max preset.",
                },
            },
        )

        self.assertEqual(report["grade_counts"], {"A": 1, "B": 1})
        self.assertEqual(report["source_counts"], {"scenario_built": 1, "optimizer": 1})
        self.assertEqual(report["scenario_coverage"], 3)
        self.assertEqual(report["lineup_rows"][0]["source"], "Scenario-built")
        self.assertEqual(report["lineup_rows"][0]["stack"], "QB+3")
        self.assertEqual(report["lineup_rows"][0]["bringback"], "Yes")
        self.assertIn("Candidate sources", report["text"])
        self.assertIn("Scenario archetypes selected", report["text"])
        self.assertIn("Top-1% paths covered: 3/100", report["text"])
        self.assertIn("Preset fit: 86/100", report["text"])
        self.assertTrue(report["review_flags"])
        self.assertEqual(report["flagged_count"], 2)
        self.assertIn("concentrated_core", report["lineup_rows"][0]["flag_codes"])
        self.assertIn("high_duplication", report["lineup_rows"][1]["flag_codes"])
        quarterback = next(row for row in report["exposure_rows"] if row["name"] == "BUF QB")
        self.assertEqual(quarterback["lineup_numbers"], [1, 2])
        self.assertEqual(quarterback["pct"], 100.0)
        self.assertIn("individual review signals: 2/2", report["text"])

    def test_incomplete_optional_sim_values_do_not_break_insights(self):
        lineup = _lineup(source="field_shaped", archetype="", edge=60, duplicate=35, hits=set())
        lineup.sim_metrics["sim_leverage"] = None
        lineup.sim_metrics["duplicate_risk"] = float("nan")
        report = build_portfolio_insights([lineup], sport="NFL", kind="classic")
        self.assertEqual(report["lineup_count"], 1)
        self.assertIn("Field-shaped", report["text"])

    def test_joint_contest_outlook_is_compact_and_actionable(self):
        lineup = _lineup(source="optimizer", archetype="", edge=72, duplicate=40, hits={1})
        report = build_portfolio_insights(
            [lineup],
            sport="NFL",
            kind="classic",
            sim_report={
                "joint_portfolio": {
                    "joint_portfolio": True, "entries_simulated": 1,
                    "planned_entries": 1, "entry_count_match": True,
                    "total_entry_cost": 20, "expected_total_payout": 25,
                    "expected_total_profit": 5, "expected_roi_pct": 25,
                    "profit_probability_pct": 18, "double_probability_pct": 8,
                    "any_top_ten_probability_pct": 2, "payout_p10": 0,
                    "payout_p50": 0, "payout_p90": 40, "stability": "Moderate",
                    "scenarios": 750, "opponent_field_samples": 3,
                    "roi_ci_low": -10, "roi_ci_high": 60,
                }
            },
        )
        self.assertIn("Joint contest outlook", report["text"])
        self.assertIn("Profit chance: 18.0%", report["text"])
        self.assertIn("10th $0 | median $0 | 90th $40", report["text"])
        self.assertEqual(report["joint_contest"]["entries_simulated"], 1)


if __name__ == "__main__":
    unittest.main()
