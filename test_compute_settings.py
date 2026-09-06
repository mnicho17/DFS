import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5 import QtCore, QtWidgets
from compute_settings import normalize_deep_settings, deep_candidate_budget, deep_search_seeds
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


if __name__ == "__main__":
    unittest.main()

