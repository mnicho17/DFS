from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from build_diagnostics import (
    clear_build_history,
    create_build_diagnostic,
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
            portfolio_report={"compliant": False, "warnings": ["Secret Player total exposure is 4/150; minimum is 10."]},
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
        self.assertIn("A player exposure constraint was not met", report)
        self.assertIn("no players, lineups, file paths, or API keys", report)
        serialized = json.dumps(diagnostic)
        self.assertNotIn("player-id", serialized)
        self.assertNotIn("MaxPct", serialized)
        self.assertNotIn("Secret Player", serialized)

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


if __name__ == "__main__":
    unittest.main()
