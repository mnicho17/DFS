from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from build_diagnostics import (
    clear_build_history,
    create_build_diagnostic,
    format_build_comparison,
    format_build_report,
    load_build_history,
    save_build_diagnostic,
)


class BuildDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(
            os.environ, {"DFS_OPTIMIZER_DATA_DIR": self.temp.name}, clear=False
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def _diagnostic(self, *, total: float = 12.5):
        return create_build_diagnostic(
            context={
                "sport": "NFL",
                "kind": "classic",
                "salary_cap": 50000,
                "requested_count": 150,
                "lineup_space": {
                    "loaded": 84,
                    "eligible": 58,
                    "omitted": 26,
                    "locked": 1,
                    "structural_combinations": 1234567,
                    "exact": True,
                    "pool_label": "NFL SIM starter/rotation pool",
                    "explanation": "Exact NFL roster-shape count before salary and strategy rules.",
                },
                "settings": {
                    "build_style": "Strategic",
                    "salary_strategy": "Near Cap",
                    "ownership_mode": "Balanced",
                    "ownership_weight": 0.15,
                    "sim_enabled": True,
                    "sim_scenarios": 750,
                    "field_preset": "150-Max",
                    "contest_profile": {
                        "name": "Sunday Main", "field_size": 100000,
                        "entry_fee": 20, "user_entries": 150,
                    },
                },
                "portfolio_rules": {
                    "min_unique": 2,
                    "max_team_pct": 100,
                    "max_game_pct": 100,
                    "balance_ownership": True,
                    "groups": [{"kind": "never_together"}],
                    "player_constraints": {"player-id": {"MaxPct": 25}},
                },
            },
            timing_report={
                "generation_seconds": 4.0,
                "simulation_seconds": 7.5,
                "selection_seconds": 1.0,
                "total_seconds": total,
                "candidate_target": 600,
                "optimizer_candidate_target": 400,
                "ownership_candidate_target": 80,
                "scenario_candidate_target": 120,
                "candidate_count": 590,
                "selected_count": 150,
                "requested_count": 150,
            },
            portfolio_report={
                "compliant": False,
                "warnings": ["Secret Player total exposure is 4/150; minimum is 10."],
                "sim_summary": {
                    "contest_aware": True,
                    "average_expected_roi_pct": 12.5,
                    "average_expected_payout": 22.5,
                    "average_expected_profit": 2.5,
                },
            },
            sim_report={"preset_comparison": {"available": True, "fit_score": 88}},
            displayed_count=150,
        )

    def test_report_has_actionable_aggregate_details_and_no_sensitive_context(self):
        diagnostic = self._diagnostic()
        report = format_build_report(diagnostic)
        self.assertIn("NFL Classic", report)
        self.assertIn("590 generated / 600 budget", report)
        self.assertIn("400 optimizer + 80 field-shaped + 120 scenario-built", report)
        self.assertIn("Slowest phase: SIM", report)
        self.assertIn("750 scenarios, 150-Max", report)
        self.assertIn("Contest-Aware SIM: Sunday Main", report)
        self.assertIn("Contest portfolio: ROI +12.5%", report)
        self.assertIn("A player exposure constraint was not met", report)
        self.assertIn("no players, lineups, file paths, or API keys", report)
        serialized = json.dumps(diagnostic)
        self.assertNotIn("player-id", serialized)
        self.assertNotIn("MaxPct", serialized)
        self.assertNotIn("Secret Player", serialized)

    def test_deep_build_report_includes_screening_validation_and_refinement(self):
        diagnostic = self._diagnostic()
        diagnostic["settings"]["compute_mode"] = "Deep"
        diagnostic["candidates"]["shortlisted"] = 900
        diagnostic["sim"].update({
            "screening_scenarios": 500,
            "validation_scenarios": 3000,
            "refinement_swaps": 2,
            "duplication_refinement_swaps": 1,
            "refinement_attempts": 5,
            "refinement_seconds": 18.25,
            "refinement_stop_reason": "duplication local optimum",
            "time_remaining_seconds": 71.0,
            "deep_time_limit_seconds": 300.0,
            "deep_time_limit_reached": False,
            "validation_top_overlap_pct": 78.4,
        })

        report = format_build_report(diagnostic)

        self.assertIn("Compute: Deep", report)
        self.assertIn("Deep shortlist: 900 candidates after 500 screening scenarios", report)
        self.assertIn("3,000 independent scenarios; 2 portfolio swaps (1 duplication polish)", report)
        self.assertIn("duplication local optimum with 71s remaining", report)
        self.assertIn("Deep polish: 18.25s across 5 search passes", report)
        self.assertIn("Independent top-candidate agreement: 78.4%", report)

    def test_showdown_report_includes_lineups_flags_and_honest_estimate_labels(self):
        class Lineup(dict):
            pass

        lineup = Lineup(
            Captain={
                "Name": "Alpha QB", "Team": "AAA", "Position": "QB",
                "CptSalary": 15000, "CptProjection": 33, "ProjCptOwnPct": 24,
            },
            Flex=[
                {"Name": "Beta DST", "Team": "BBB", "Position": "DST", "FlexSalary": 4000, "FlexProjection": 8, "ProjFlexOwnPct": 20},
                {"Name": "Alpha WR", "Team": "AAA", "Position": "WR", "FlexSalary": 9000, "FlexProjection": 18, "ProjFlexOwnPct": 30},
                {"Name": "Alpha RB", "Team": "AAA", "Position": "RB", "FlexSalary": 8000, "FlexProjection": 16, "ProjFlexOwnPct": 25},
                {"Name": "Beta QB", "Team": "BBB", "Position": "QB", "FlexSalary": 10000, "FlexProjection": 21, "ProjFlexOwnPct": 45},
                {"Name": "Beta WR", "Team": "BBB", "Position": "WR", "FlexSalary": 4000, "FlexProjection": 9, "ProjFlexOwnPct": 12},
            ],
        )
        lineup.sim_metrics = {
            "candidate_archetype": "Passing Stack",
            "duplicate_risk": 52,
            "showdown_correlation_flags": ["QB Captain vs opposing DST"],
        }
        diagnostic = create_build_diagnostic(
            context={
                "sport": "NFL", "kind": "showdown", "salary_cap": 50000,
                "requested_count": 20, "lineup_space": {},
                "settings": {"sim_enabled": False, "compute_mode": "Fast"},
                "portfolio_rules": {"min_unique": 2, "max_team_pct": 100, "max_game_pct": 100},
            },
            timing_report={"requested_count": 20, "selected_count": 1},
            portfolio_report={
                "warnings": [],
                "sim_summary": {"average_edge": 85, "average_return_index": 75, "average_duplicate_risk": 52},
            },
            displayed_count=1,
            lineups=[lineup],
        )
        report = format_build_report(diagnostic)
        self.assertIn("Portfolio estimates (no SIM)", report)
        self.assertNotIn("top-1% paths", report)
        self.assertNotIn("game max", report)
        self.assertIn("Correlation exceptions: 1 flags across 1 of 1 lineups", report)
        self.assertIn("CPT Alpha QB [AAA QB]", report)
        self.assertIn("FLEX Beta DST [BBB DST]", report)
        self.assertIn("Flags: QB Captain vs opposing DST", report)
        self.assertIn("excludes file paths and API keys", report)

    def test_joint_contest_report_shows_total_cost_range_and_stability(self):
        diagnostic = create_build_diagnostic(
            context={
                "sport": "NFL", "kind": "classic", "salary_cap": 50000,
                "requested_count": 20, "lineup_space": {},
                "settings": {
                    "sim_enabled": True, "sim_scenarios": 750, "field_preset": "20-Max",
                    "contest_profile": {
                        "name": "Sunday Twenty", "field_size": 5000,
                        "entry_fee": 10, "user_entries": 20,
                    },
                },
                "portfolio_rules": {},
            },
            timing_report={"requested_count": 20, "selected_count": 20},
            portfolio_report={"warnings": [], "sim_summary": {}},
            sim_report={
                "joint_portfolio": {
                    "joint_portfolio": True, "entries_simulated": 20,
                    "planned_entries": 20, "entry_count_match": True,
                    "total_entry_cost": 200, "expected_total_payout": 238,
                    "expected_total_profit": 38, "expected_roi_pct": 19,
                    "roi_ci_low": -8, "roi_ci_high": 46,
                    "profit_probability_pct": 31, "payout_p10": 0,
                    "payout_p50": 140, "payout_p90": 600,
                    "scenarios": 750, "opponent_field_samples": 3,
                    "stability": "Moderate",
                    "volatility_model": "role-aware-player-volatility-v1",
                }
            },
            displayed_count=20,
        )

        report = format_build_report(diagnostic)

        self.assertIn("Joint contest: 20 entries cost $200.00", report)
        self.assertIn("profit chance 31.0%", report)
        self.assertIn("$0/$140/$600", report)
        self.assertIn("750 scenarios; 3 opponent-field samples; Moderate stability", report)
        self.assertIn("role-aware player ranges", report)

    def test_history_keeps_newest_first_and_respects_limit(self):
        first = save_build_diagnostic(self._diagnostic(total=10.0), limit=2)
        second = save_build_diagnostic(self._diagnostic(total=20.0), limit=2)
        third = save_build_diagnostic(self._diagnostic(total=30.0), limit=2)
        history = load_build_history()
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["diagnostic_id"], third["diagnostic_id"])
        self.assertEqual(history[1]["diagnostic_id"], second["diagnostic_id"])
        self.assertNotEqual(first["diagnostic_id"], third["diagnostic_id"])

    def test_corrupt_history_is_safe_and_clear_replaces_it(self):
        history_path = os.path.join(self.temp.name, "history", "build-diagnostics.json")
        os.makedirs(os.path.dirname(history_path), exist_ok=True)
        with open(history_path, "w", encoding="utf-8") as handle:
            handle.write("not json")
        self.assertEqual(load_build_history(), [])
        save_build_diagnostic(self._diagnostic())
        self.assertEqual(len(load_build_history()), 1)
        clear_build_history()
        self.assertEqual(load_build_history(), [])

    def test_two_build_comparison_shows_speed_preset_and_sim_deltas(self):
        earlier = self._diagnostic(total=20.0)
        later = self._diagnostic(total=12.0)
        earlier["created_at"] = "2026-08-14T10:00:00-04:00"
        later["created_at"] = "2026-08-14T11:00:00-04:00"
        earlier["settings"]["field_preset"] = "20-Max"
        later["settings"]["field_preset"] = "150-Max"
        earlier["sim"].update({"average_edge": 70, "average_duplicate_risk": 45})
        later["sim"].update({
            "average_edge": 78,
            "average_duplicate_risk": 35,
            "selected_sources": {"optimizer": 90, "scenario_built": 60},
        })

        comparison = format_build_comparison(earlier, later)

        self.assertIn("Build Comparison", comparison)
        self.assertIn("Preset: 20-Max -> 150-Max", comparison)
        self.assertIn("Total: 20.00s -> 12.00s (-8.00s", comparison)
        self.assertIn("Average Edge: 70.0 -> 78.0 (+8.0)", comparison)
        self.assertIn("90 optimizer + 60 scenario-built", comparison)


if __name__ == "__main__":
    unittest.main()

