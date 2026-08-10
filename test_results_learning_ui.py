from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtWidgets

from main_window import LineupBuildWorker, LiveDataSettingsDialog, MainWindow, ResultsLearningDialog
from learning_db import generate_learning_report
from test_learning_results import _showdown_lineup
from test_nfl_logic import _fixture_players


class ResultsLearningUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(
            os.environ, {"DFS_OPTIMIZER_DATA_DIR": self.temp.name}, clear=False
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_dialog_starts_with_local_empty_state(self):
        dialog = ResultsLearningDialog()
        self.assertIsNotNone(dialog.findChild(QtWidgets.QPushButton, "importResultsButton"))
        self.assertIn("0 exported lineups", dialog.summary.text())
        self.assertIn("All history stays on this computer", dialog.report.toPlainText())
        dialog.close()

    def test_main_window_exposes_results_learning_button(self):
        window = MainWindow()
        button = window.findChild(QtWidgets.QPushButton, "resultsLearningButton")
        self.assertIsNotNone(button)
        self.assertEqual(button.text(), "Results & Learning")
        self.assertIsNotNone(window.findChild(QtWidgets.QSpinBox, "portfolioMinUnique"))
        self.assertIsNotNone(window.findChild(QtWidgets.QPushButton, "portfolioSummaryButton"))
        sim_toggle = window.findChild(QtWidgets.QCheckBox, "nflSimEdgeCheck")
        self.assertIsNotNone(sim_toggle)
        self.assertTrue(sim_toggle.isChecked())
        self.assertIsNotNone(window.findChild(QtWidgets.QSpinBox, "nflSimScenarios"))
        self.assertEqual(window.findChild(QtWidgets.QPushButton, "gameDayCheckButton").text(), "Game-Day Check")
        self.assertIsNotNone(window.findChild(QtWidgets.QPushButton, "liveDataSettingsButton"))
        self.assertIn("Live data", window.findChild(QtWidgets.QLabel, "liveDataStatusLabel").text())
        window.close()

    def test_compact_workspace_keeps_primary_actions_and_saved_views(self):
        window = MainWindow()
        self.assertIsNotNone(window.findChild(QtWidgets.QFrame, "compactCommandBar"))
        self.assertEqual(window.btn_primary_build.text(), "Generate")
        self.assertIn("Showdown", window.btn_primary_build.toolTip())
        self.assertEqual(window.tabs_workspace_controls.count(), 3)
        self.assertEqual(
            [window.tabs_workspace_controls.tabText(i) for i in range(3)],
            ["Build Strategy", "Portfolio Rules", "Data & Learning"],
        )
        self.assertIsNotNone(window.findChild(QtWidgets.QGroupBox, "playerInspector"))
        self.assertEqual(window.tabs_saved.count(), 2)
        window.close()

    def test_player_columns_follow_the_selected_sport(self):
        window = MainWindow()
        self.assertFalse(window.tbl_players.isColumnHidden(10))  # NFL role
        self.assertTrue(window.tbl_players.isColumnHidden(20))   # MLB order
        self.assertTrue(window.tbl_players.isColumnHidden(18))   # limits live in inspector

        window.combo_sport.setCurrentText("MLB")
        self.assertEqual(window.tbl_players.horizontalHeaderItem(10).text(), "Park")
        self.assertFalse(window.tbl_players.isColumnHidden(20))
        self.assertFalse(window.tbl_players.isColumnHidden(22))
        if hasattr(window.tabs_lineups, "isTabVisible"):
            self.assertTrue(window.tabs_lineups.isTabVisible(2))

        window.combo_sport.setCurrentText("NBA")
        self.assertTrue(window.tbl_players.isColumnHidden(7))
        self.assertTrue(window.tbl_players.isColumnHidden(20))
        if hasattr(window.tabs_lineups, "isTabVisible"):
            self.assertFalse(window.tabs_lineups.isTabVisible(2))
        window.close()

    def test_selected_player_updates_contextual_inspector(self):
        window = MainWindow()
        window.players = [{
            "Name": "Example QB", "Team": "BUF", "Position": "QB",
            "FlexSalary": 7000, "FlexProjection": 20.5, "ProjOwnPct": 14.2,
            "NFLAvailability": "STARTER", "MaxPct": 25.0,
        }]
        window._refresh_players_table()
        window.tbl_players.selectRow(0)
        window._update_player_inspector()
        self.assertEqual(window.lbl_player_inspector_title.text(), "Example QB")
        self.assertIn("BUF · QB", window.lbl_player_inspector_meta.text())
        self.assertIn("Exposure max 25%", window.lbl_player_inspector_meta.text())
        self.assertTrue(all(button.isEnabled() for button in window._player_action_buttons))

        window.tabs_lineups.setCurrentIndex(1)
        self.assertEqual(window.btn_primary_build.text(), "Generate")
        self.assertIn("NFL Classic", window.btn_primary_build.toolTip())
        self.assertTrue(all(button.isHidden() for button in window._captain_action_buttons))
        window.close()

    def test_live_data_settings_masks_the_odds_api_key(self):
        dialog = LiveDataSettingsDialog("secret-test-key")
        field = dialog.findChild(QtWidgets.QLineEdit, "oddsApiKeyEdit")
        self.assertIsNotNone(field)
        self.assertEqual(field.text(), "secret-test-key")
        self.assertEqual(field.echoMode(), QtWidgets.QLineEdit.Password)
        dialog.close()

    def test_player_table_shows_live_availability_and_implied_team_total(self):
        window = MainWindow()
        window.players = [{
            "Name": "Example QB", "Team": "BUF", "Position": "QB",
            "FlexSalary": 7000, "BaseProjection": 20, "FlexProjection": 20.5,
            "NFLAvailability": "STARTER", "NFLDepthPosition": "QB", "NFLDepthOrder": 1,
            "NFLRole": "QB2 NEXT UP", "NFLRoleScore": 0.55,
            "NFLReplacementFor": "Starting QB", "NFLReplacementBoost": 0.30,
            "NFLRosterStatus": "Active", "NFLPractice": "Full Participation",
            "InjurySource": "Sleeper", "LiveStatusUpdatedAt": "2026-09-13T15:00:00Z",
            "NFLVegas": 0.5, "NFLVegasTeamTotal": 27.0, "NFLVegasGameTotal": 50.0,
            "NFLVegasSpread": -4.0, "NFLVegasBookmakers": 5,
            "NFLVegasUpdatedAt": "2026-09-13T14:55:00Z", "NFLVegasState": "matched",
        }]
        window._refresh_players_table()
        self.assertEqual(window.tbl_players.item(0, 3).text(), "Starter")
        self.assertIn("Depth: QB1", window.tbl_players.item(0, 3).toolTip())
        self.assertEqual(window.tbl_players.item(0, 12).text(), "27.0")
        self.assertIn("Game total: 50.0", window.tbl_players.item(0, 12).toolTip())
        self.assertIn("Starting QB", window.tbl_players.item(0, 10).toolTip())
        window.close()

    def test_classic_worker_reports_high_volume_progress_without_surplus_candidates(self):
        progress = []
        finished = []
        worker = LineupBuildWorker(
            _fixture_players(),
            kind="classic",
            sport="NFL",
            num_lineups=150,
            salary_cap=50000,
            build_style="Strategic",
            salary_strategy="Near Cap",
            portfolio_rules={
                "min_unique": 2,
                "max_team_pct": 100.0,
                "max_game_pct": 100.0,
                "balance_ownership": True,
                "groups": [],
                "player_constraints": {},
            },
        )
        worker.progress.connect(lambda done, total, text: progress.append((done, total, text)))
        worker.finished.connect(finished.append)

        worker.run()

        self.assertTrue(finished)
        self.assertEqual(len(finished[0]["lineups"]), 150)
        self.assertEqual(finished[0]["candidate_count"], 150)
        self.assertEqual(progress[0][:2], (0, 150))
        self.assertEqual(progress[-1][:2], (150, 150))
        self.assertEqual([event[0] for event in progress], sorted(event[0] for event in progress))

    def test_saved_export_writes_csv_and_learning_record(self):
        window = MainWindow()
        window.saved_showdown = [_showdown_lineup()]
        export_path = os.path.join(self.temp.name, "saved.csv")
        with mock.patch.object(
            QtWidgets.QFileDialog, "getSaveFileName", return_value=(export_path, "CSV Files (*.csv)")
        ), mock.patch.object(
            QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Yes
        ), mock.patch.object(QtWidgets.QMessageBox, "information"):
            window.on_export_saved("showdown")
        self.assertTrue(os.path.isfile(export_path))
        report = generate_learning_report()
        self.assertEqual(report["exported_lineups"], 1)
        window.close()


if __name__ == "__main__":
    unittest.main()
