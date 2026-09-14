import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5 import QtWidgets, QtCore
from main_window import MainWindow, LineupBuildWorker, _deep_shortlist
from showdown_simulation import (showdown_score, showdown_signature, generate_showdown_field,
                                 simulate_showdown, active_showdown_players)
from optimizers import ShowdownOptimizer, ShowdownLineup, _salary, _cpt_salary
from nfl_simulation import player_key
from test_showdown_performance import _showdown_players
from build_diagnostics import create_build_diagnostic, format_build_report


class ShowdownDeepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_captain_multiplier_and_identity_survive_shortlist(self):
        players = _showdown_players()
        six = players[:3] + players[18:21]
        a = ShowdownLineup(six[0], six[1:])
        b = ShowdownLineup(six[1], [six[0]] + six[2:])
        outcomes = {player_key(p): i + 1.0 for i, p in enumerate(six)}
        self.assertEqual(showdown_score(a, outcomes), 21.5)
        self.assertEqual(showdown_score(b, outcomes), 22.0)
        self.assertNotEqual(showdown_signature(a), showdown_signature(b))
        self.assertEqual(len(_deep_shortlist([a, b], 2)), 2)

    def test_opponent_field_ignores_personal_fades_and_preserves_legal_slots(self):
        players = _showdown_players()
        for p in players:
            p["FadeFlex"] = p["FadeCpt"] = True
        players[0]["LockCpt"] = True
        field = generate_showdown_field(players, 80, seed=4)
        self.assertEqual(len(field), 80)
        self.assertGreater(len({player_key(lu["Captain"]) for lu in field}), 1)
        for lu in field:
            roster = [lu["Captain"]] + lu["Flex"]
            self.assertEqual(len({player_key(p) for p in roster}), 6)
            self.assertEqual(len({p["Team"] for p in roster}), 2)
            self.assertLessEqual(_cpt_salary(lu["Captain"]) + sum(_salary(p) for p in lu["Flex"]), 50000)

    def test_shared_outcomes_are_used_for_every_candidate_and_captain(self):
        players = _showdown_players()
        candidates = ShowdownOptimizer(players).build_lineups(3)
        outcomes = {player_key(p): float(i + 1) for i, p in enumerate(players)}
        with mock.patch("showdown_simulation._scenario_outcomes", return_value=outcomes) as draw:
            result = simulate_showdown(candidates, players, scenarios=12, field_lineup_count=20)
        self.assertEqual(draw.call_count, 12)
        self.assertEqual(result["report"]["scenarios"], 12)
        for lineup in result["lineups"]:
            self.assertAlmostEqual(lineup.sim_metrics["sim_mean"], showdown_score(lineup, outcomes))
            self.assertEqual(lineup.sim_metrics["sim_scenarios"], 12)

    def test_unavailable_lock_rejected_and_field_cancel_is_immediate(self):
        players = _showdown_players()
        self.assertEqual(generate_showdown_field(players, 100, cancel_callback=lambda: True), [])
        players[0].update(NFLAvailability="OUT", LockCpt=True)
        with self.assertRaisesRegex(ValueError, "locked"):
            active_showdown_players(players)

    def test_deep_worker_runs_real_sim_preserves_retained_and_slot_rules(self):
        players = _showdown_players()
        players[0]["LockFlex"] = True
        players[1]["FadeCpt"] = True
        retained = ShowdownOptimizer(players).build_lineups(1)
        worker = LineupBuildWorker(players, kind="showdown", sport="NFL", num_lineups=8, salary_strategy="Balanced Spend",
            salary_cap=50000, sim_enabled=True, compute_mode="Deep", sim_scenarios=2500,
            deep_time_limit_seconds=20, deep_options={"candidates": 160, "shortlist": 20, "field": 50, "screening": 250},
            retained_lineups=retained, portfolio_rules={"min_unique": 1, "balance_ownership": False})
        results, errors = [], []
        worker.finished.connect(results.append)
        worker.error.connect(errors.append)
        worker.run()
        self.assertFalse(errors, errors)
        self.assertTrue(results)
        result = results[0]
        self.assertEqual(len(result["lineups"]), 8)
        self.assertIn(showdown_signature(retained[0]), {showdown_signature(lu) for lu in result["lineups"]})
        self.assertGreater(result["timing_report"]["validation_scenarios"], 0)
        report = format_build_report(create_build_diagnostic(
            context={"sport": "NFL", "kind": "showdown", "settings": {"sim_enabled": True, "sim_scenarios": 2500}},
            timing_report=result["timing_report"], portfolio_report=result["portfolio_report"],
            sim_report=result["sim_report"], lineups=result["lineups"], displayed_count=8,
        ))
        self.assertIn("NFL Showdown", report)
        self.assertIn("Compute: Deep", report)
        self.assertIn("Deep validation:", report)
        for lu in result["lineups"]:
            self.assertIn(player_key(players[0]), {player_key(p) for p in lu["Flex"]})
            self.assertNotEqual(player_key(lu["Captain"]), player_key(players[1]))

    def test_showdown_ui_exposes_deep_and_does_not_claim_classic_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = QtCore.QSettings(directory + "/ui.ini", QtCore.QSettings.IniFormat)
            with mock.patch("main_window.QtCore.QSettings", return_value=settings):
                window = MainWindow()
                window.tabs_lineups.setCurrentIndex(0)
                window.combo_nfl_compute_mode.setCurrentIndex(1)
                window.chk_nfl_contest_sim.setChecked(True)
                self.assertFalse(window.btn_deep_compute.isHidden())
                text = []
                def inspect():
                    dialog = self.app.activeModalWidget()
                    text.append(dialog.findChild(QtWidgets.QLabel, "deepRuntimeEstimate").text())
                    dialog.reject()
                QtCore.QTimer.singleShot(0, inspect)
                window._edit_deep_compute_settings()
                self.assertIn("not calibrated", text[0])
                window.close()

    def test_cancelled_worker_returns_without_simulation_or_losing_retained(self):
        players = _showdown_players()
        retained = ShowdownOptimizer(players).build_lineups(1)
        worker = LineupBuildWorker(players, kind="showdown", num_lineups=8, salary_cap=50000,
            compute_mode="Deep", sim_enabled=True, retained_lineups=retained)
        worker.request_cancel()
        results, errors = [], []
        worker.finished.connect(results.append)
        worker.error.connect(errors.append)
        with mock.patch("showdown_simulation.simulate_showdown") as sim:
            worker.run()
        self.assertFalse(errors, errors)
        sim.assert_not_called()
        self.assertTrue(results[0]["cancelled"])
        self.assertEqual(showdown_signature(results[0]["lineups"][0]), showdown_signature(retained[0]))
        self.assertTrue(results[0]["portfolio_report"]["warnings"])

    def test_showdown_sim_column_does_not_change_export_slots(self):
        window = MainWindow()
        players = _showdown_players()
        lineup = ShowdownOptimizer(players).build_lineups(1)[0]
        wrapped = ShowdownLineup(lineup["Captain"], lineup["Flex"])
        wrapped.sim_metrics = {"sim_edge": 80, "sim_scenarios": 100, "sim_field_lineups": 20}
        window._populate_showdown_lineups([wrapped])
        self.assertEqual(window.tbl_sd.horizontalHeaderItem(7).text(), "SIM Edge")
        self.assertEqual(len(window.last_showdown[0]["Flex"]), 5)
        window._populate_showdown_lineups([lineup])
        self.assertEqual(window.tbl_sd.columnCount(), 7)
        window.close()


if __name__ == "__main__":
    unittest.main()
