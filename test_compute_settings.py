import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5 import QtCore, QtWidgets
from compute_settings import normalize_deep_settings, deep_candidate_budget, deep_search_seeds
from compute_settings import DEEP_PROFILES, matching_deep_profile, estimate_acer_runtime
from build_recipes import dump_recipes_json, load_recipes_json
from main_window import MainWindow, LineupBuildWorker
from optimizers import MultiSportClassicOptimizer
from test_nfl_logic import _fixture_players


class ComputeSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_auto_preserves_original_budgets_and_seeds(self):
        self.assertEqual(deep_candidate_budget(150, {}), 4500)
        self.assertEqual(deep_candidate_budget(150, {}, False), 3600)
        self.assertEqual(deep_search_seeds(4), (1337, 4241, 7919, 12007))
        self.assertEqual(len(set(deep_search_seeds(32))), 32)

    def test_portable_settings_are_bounded_and_preserve_explicit_limits(self):
        options = normalize_deep_settings({"minutes": 999, "field": -10, "seeds": "bad", "shortlist": 99999})
        self.assertEqual(options["minutes"], 60)
        self.assertEqual(options["field"], 0)
        self.assertEqual(options["seeds"], 4)
        self.assertEqual(options["shortlist"], 2000)
        self.assertEqual(deep_candidate_budget(150, {"candidates": 12000}, False), 12000)
        self.assertEqual(deep_candidate_budget(150, {"candidates": 10}), 150)
        recipe = {"deep_compute": {"minutes": 30, "candidates": 12000}}
        loaded = load_recipes_json(dump_recipes_json({"Server": recipe}))
        self.assertEqual(loaded["Server"]["deep_compute"]["candidates"], 12000)

    def test_dialog_persists_and_old_recipe_restores_original_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = QtCore.QSettings(os.path.join(directory, "settings.ini"), QtCore.QSettings.IniFormat)
            with mock.patch("main_window.QtCore.QSettings", return_value=settings):
                window = MainWindow()
                window.combo_nfl_compute_mode.setCurrentIndex(1)
                window.chk_nfl_contest_sim.setChecked(True)
                self.assertTrue(window.btn_deep_compute.isEnabled())
                def accept_settings():
                    dialog = self.app.activeModalWidget()
                    dialog.findChild(QtWidgets.QSpinBox, "deep_minutes").setValue(30)
                    dialog.findChild(QtWidgets.QSpinBox, "deep_candidates").setValue(12000)
                    dialog.accept()
                QtCore.QTimer.singleShot(0, accept_settings)
                window._edit_deep_compute_settings()
                self.assertEqual(window.deep_compute_settings["minutes"], 30)
                second = MainWindow()
                self.assertEqual(second.deep_compute_settings["candidates"], 12000)
                window._apply_build_recipe("Old", {"nfl_compute_mode": "Deep (up to 5 min)"})
                self.assertTrue(window.combo_nfl_compute_mode.currentText().startswith("Deep"))
                self.assertEqual(window.deep_compute_settings["minutes"], 5)
                window.chk_nfl_contest_sim.setChecked(False)
                self.assertFalse(window.btn_deep_compute.isEnabled())
                window.close()
                second.close()

    def test_worker_routes_custom_pools_to_generation_and_validation(self):
        players = _fixture_players()
        lineups = MultiSportClassicOptimizer(players, sport="NFL").build_lineups(12)
        self.assertTrue(lineups)
        calls = []
        def simulate(candidates, players, **kwargs):
            calls.append(kwargs)
            return {"lineups": candidates, "report": {"scenarios": kwargs["scenarios"]}}
        worker = LineupBuildWorker(
            players, kind="classic", num_lineups=8, salary_cap=50000,
            sim_enabled=True, compute_mode="Deep", sim_scenarios=10000,
            deep_time_limit_seconds=1800,
            deep_options={"candidates": 12000, "shortlist": 8, "field": 8000, "seeds": 8, "screening": 1000},
        )
        finished, errors = [], []
        worker.finished.connect(finished.append)
        worker.error.connect(errors.append)
        with mock.patch("main_window.MultiSportClassicOptimizer.build_lineups", return_value=lineups) as build, mock.patch(
            "main_window.simulate_nfl_contest", side_effect=simulate
        ), mock.patch("main_window.generate_nfl_field_lineups", return_value=([], [])), mock.patch(
            "main_window.generate_nfl_scenario_lineups", return_value=([], {})
        ):
            worker.run()
        self.assertFalse(errors, errors)
        self.assertTrue(finished)
        self.assertEqual(build.call_count, 8)
        self.assertGreater(build.call_args_list[0].kwargs["num_lineups"], 500)
        self.assertEqual(calls[0]["scenarios"], 1000)
        self.assertEqual(calls[1]["scenarios"], 10000)
        self.assertEqual(calls[1]["field_lineup_count"], 8000)
        timing = finished[0]["timing_report"]
        self.assertEqual(timing["deep_time_limit_seconds"], 1800)
        self.assertEqual(timing["deep_options"]["candidates"], 12000)
        self.assertLessEqual(timing["shortlist_count"], 8)

    def test_runtime_estimates_reproduce_measured_workloads_and_mark_extrapolation(self):
        balanced = DEEP_PROFILES["Balanced — 15 min cap"]
        runtime = estimate_acer_runtime(balanced, balanced["scenarios"])
        self.assertAlmostEqual(runtime["seconds"], 574.70, places=2)
        self.assertFalse(runtime["extrapolated"])
        thorough = DEEP_PROFILES["Thorough — 20 min cap"]
        self.assertAlmostEqual(estimate_acer_runtime(thorough, 10000)["seconds"], 790.28, places=2)
        maximum = DEEP_PROFILES["Maximum — 60 min cap"]
        self.assertTrue(estimate_acer_runtime(maximum, 10000)["extrapolated"])
        capped = estimate_acer_runtime(dict(maximum, minutes=5), 10000)
        self.assertTrue(capped["budget_limited"])
        self.assertEqual(capped["seconds"], 300)

    def test_tier_sets_all_controls_custom_retains_values_and_cancel_discards(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = QtCore.QSettings(os.path.join(directory, "settings.ini"), QtCore.QSettings.IniFormat)
            with mock.patch("main_window.QtCore.QSettings", return_value=settings):
                window = MainWindow()
                before = dict(window.deep_compute_settings)
                scenarios_before = window.spin_nfl_sim_scenarios.value()
                assertions = []
                def choose_then_cancel():
                    dialog = self.app.activeModalWidget()
                    combo = dialog.findChild(QtWidgets.QComboBox, "deepProfile")
                    for name, profile in DEEP_PROFILES.items():
                        combo.setCurrentText(name)
                        for key in ("minutes", "candidates", "shortlist", "field", "seeds", "screening"):
                            spin = dialog.findChild(QtWidgets.QSpinBox, "deep_" + key)
                            assertions.append(spin.value() == profile[key] and not spin.isEnabled())
                        assertions.append(dialog.findChild(QtWidgets.QSpinBox, "deep_validation").value() == profile["scenarios"])
                    combo.setCurrentText("Custom")
                    assertions.append(dialog.findChild(QtWidgets.QSpinBox, "deep_candidates").value() == 20000)
                    assertions.append(dialog.findChild(QtWidgets.QSpinBox, "deep_candidates").isEnabled())
                    dialog.reject()
                QtCore.QTimer.singleShot(0, choose_then_cancel)
                window._edit_deep_compute_settings()
                self.assertTrue(all(assertions))
                self.assertEqual(window.deep_compute_settings, before)
                self.assertEqual(window.spin_nfl_sim_scenarios.value(), scenarios_before)
                def choose_then_accept():
                    dialog = self.app.activeModalWidget()
                    dialog.findChild(QtWidgets.QComboBox, "deepProfile").setCurrentText("Thorough — 20 min cap")
                    dialog.accept()
                QtCore.QTimer.singleShot(0, choose_then_accept)
                window._edit_deep_compute_settings()
                second = MainWindow()
                self.assertEqual(matching_deep_profile(second.deep_compute_settings, second.spin_nfl_sim_scenarios.value()), "Thorough — 20 min cap")
                self.assertEqual(second._current_build_recipe()["nfl_sim_scenarios"], 10000)
                window.close()
                second.close()


if __name__ == "__main__":
    unittest.main()

