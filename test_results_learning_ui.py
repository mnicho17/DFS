from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtWidgets

from main_window import BuildDiagnosticsDialog, LineupBuildWorker, LiveDataSettingsDialog, MainWindow, ResultsImportWorker, ResultsLearningDialog
from build_diagnostics import create_build_diagnostic, load_build_history, save_build_diagnostic
from learning_db import generate_learning_report
from test_learning_results import _showdown_lineup, _write_csv
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
        self.assertIsNotNone(dialog.findChild(QtWidgets.QPushButton, "attachFieldSalaryButton"))
        self.assertIsNotNone(dialog.findChild(QtWidgets.QProgressBar, "resultsImportProgress"))
        self.assertIsNotNone(dialog.findChild(QtWidgets.QPushButton, "cancelResultsImportButton"))
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
        preset = window.findChild(QtWidgets.QComboBox, "nflFieldPreset")
        self.assertIsNotNone(preset)
        self.assertEqual(preset.currentText(), "150-Max")
        self.assertEqual(window.findChild(QtWidgets.QPushButton, "gameDayCheckButton").text(), "Game-Day Check")
        self.assertIsNotNone(window.findChild(QtWidgets.QPushButton, "liveDataSettingsButton"))
        self.assertIn("Live data", window.findChild(QtWidgets.QLabel, "liveDataStatusLabel").text())
        self.assertEqual(window.findChild(QtWidgets.QPushButton, "slateReadinessButton").text(), "Slate Readiness")
        self.assertIn("Readiness", window.findChild(QtWidgets.QLabel, "slateReadinessStatus").text())
        self.assertIn("Lineup space", window.findChild(QtWidgets.QLabel, "lineupSpaceStatus").text())
        self.assertIsNotNone(window.findChild(QtWidgets.QToolButton, "clearReadinessFilterButton"))
        copy_action = window.findChild(QtWidgets.QAction, "copyLastBuildReportAction")
        self.assertIsNotNone(copy_action)
        self.assertFalse(copy_action.isEnabled())
        self.assertIsNotNone(window.findChild(QtWidgets.QAction, "buildHistoryAction"))
        window.close()

    def test_build_history_dialog_and_copy_report_use_local_diagnostics(self):
        diagnostic = create_build_diagnostic(
            context={
                "sport": "NFL", "kind": "classic", "salary_cap": 50000,
                "requested_count": 150,
                "lineup_space": {"loaded": 80, "eligible": 55, "structural_combinations": 500000},
                "settings": {"build_style": "Strategic", "sim_enabled": True, "sim_scenarios": 750},
                "portfolio_rules": {"min_unique": 2},
            },
            timing_report={
                "generation_seconds": 1, "simulation_seconds": 2, "selection_seconds": 0.5,
                "total_seconds": 3.5, "candidate_target": 600, "candidate_count": 600,
                "selected_count": 150, "requested_count": 150,
            },
            displayed_count=150,
        )
        save_build_diagnostic(diagnostic)
        dialog = BuildDiagnosticsDialog()
        self.assertEqual(dialog.history_list.count(), 1)
        self.assertIn("DFS Optimizer Build Report", dialog.report.toPlainText())
        dialog.copy_selected_report()
        self.assertIn("NFL Classic", QtWidgets.QApplication.clipboard().text())
        dialog.close()

        window = MainWindow()
        self.assertTrue(window.action_copy_build_report.isEnabled())
        window.copy_last_build_report()
        self.assertIn("Candidates: 600 generated", QtWidgets.QApplication.clipboard().text())
        self.assertEqual(len(load_build_history()), 1)
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

    def test_results_import_worker_reports_progress_and_supports_cancellation(self):
        path = os.path.join(self.temp.name, "NFL Sunday 150-Max.csv")
        rows = [
            {
                "Sport": "NFL", "Contest Name": "Sunday 150-Max",
                "Entry Name": f"field-{index}", "Entry Fee": "", "Winnings": "",
                "Points": str(100 - index), "Rank": str(index + 1),
                "Entries": "30", "Places Paid": "6",
                "Lineup": "10001,10002,10003,10004,10005,10006",
            }
            for index in range(30)
        ]
        _write_csv(path, rows)
        progress = []
        finished = []
        worker = ResultsImportWorker([path])
        worker.progress.connect(lambda done, total, text: progress.append((done, total, text)))
        worker.finished.connect(finished.append)
        worker.run()
        self.assertTrue(finished)
        self.assertEqual(finished[0]["field_only_files"], 1)
        self.assertTrue(progress)

        cancelled = []
        second = ResultsImportWorker([path])
        second.finished.connect(cancelled.append)
        second.request_cancel()
        second.run()
        self.assertTrue(cancelled[0]["cancelled"])

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
        timing = finished[0]["timing_report"]
        self.assertGreaterEqual(timing["generation_seconds"], 0.0)
        self.assertGreaterEqual(timing["selection_seconds"], 0.0)
        self.assertGreaterEqual(timing["total_seconds"], timing["generation_seconds"])
        self.assertEqual(timing["selected_count"], 150)

    def test_strategic_classic_prunes_deep_backups_without_sim_and_preserves_lock(self):
        players = _fixture_players()
        teams = sorted({str(player.get("Team")) for player in players})
        locked_backup = None
        next_id = 9000
        for team in teams:
            opponent = next(
                str(player.get("Opponent")) for player in players if player.get("Team") == team
            )
            game = next(
                str(player.get("GameKey")) for player in players if player.get("Team") == team
            )
            for position, depth in (("QB", 2), ("RB", 3), ("WR", 4), ("TE", 2)):
                backup = {
                    "Name": f"{team} deep {position}",
                    "Team": team,
                    "Opponent": opponent,
                    "GameKey": game,
                    "GameInfo": game,
                    "Position": position,
                    "FlexSalary": 5200,
                    "FlexProjection": 12.0,
                    "FlexID": str(next_id),
                    "NFLDepthOrder": depth,
                    "NFLRoleScore": 0.10,
                }
                next_id += 1
                players.append(backup)
                if team == "BUF" and position == "WR":
                    backup["LockFlex"] = True
                    locked_backup = backup

        finished = []
        worker = LineupBuildWorker(
            players,
            kind="classic",
            sport="NFL",
            num_lineups=12,
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
            sim_enabled=False,
        )
        worker.finished.connect(finished.append)
        worker.run()

        self.assertTrue(finished)
        payload = finished[0]
        timing = payload["timing_report"]
        self.assertTrue(timing["role_pool_applied"])
        self.assertEqual(timing["unfiltered_build_pool_size"], len(players))
        self.assertEqual(timing["build_pool_size"], 64)
        self.assertEqual(timing["role_pool_omitted"], len(players) - 64)
        self.assertEqual(len(payload["lineups"]), 12)
        self.assertIsNotNone(locked_backup)
        locked_id = str(locked_backup["FlexID"])
        self.assertTrue(all(
            locked_id in {str(player.get("FlexID")) for player in lineup}
            for lineup in payload["lineups"]
        ))

    def test_readiness_player_filter_can_be_cleared(self):
        window = MainWindow()
        window.players = [
            {"Name": "Starter", "Position": "QB", "FlexSalary": 7000, "FlexProjection": 20},
            {"Name": "Backup", "Position": "QB", "FlexSalary": 5000, "FlexProjection": 8},
        ]
        window._refresh_players_table()
        window.focus_readiness_players({"details": {"player_names": ["Backup"]}})
        visible = [
            window.tbl_players.item(row, 0).text()
            for row in range(window.tbl_players.rowCount()) if not window.tbl_players.isRowHidden(row)
        ]
        self.assertEqual(visible, ["Backup"])
        self.assertFalse(window.btn_clear_readiness_filter.isHidden())
        window.clear_readiness_player_filter()
        self.assertTrue(all(not window.tbl_players.isRowHidden(row) for row in range(2)))
        window.close()

    def test_nfl_sim_worker_builds_a_wider_pool_and_reports_portfolio_coverage(self):
        finished = []
        worker = LineupBuildWorker(
            _fixture_players(),
            kind="classic",
            sport="NFL",
            num_lineups=24,
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
            sim_enabled=True,
            sim_scenarios=100,
        )
        worker.finished.connect(finished.append)

        worker.run()

        self.assertTrue(finished)
        payload = finished[0]
        self.assertEqual(len(payload["lineups"]), 24)
        self.assertGreater(payload["candidate_count"], 24)
        timing = payload["timing_report"]
        self.assertGreater(timing["ownership_candidate_target"], 0)
        self.assertGreater(timing["scenario_candidate_target"], 0)
        self.assertEqual(
            timing["candidate_target"],
            timing["optimizer_candidate_target"]
            + timing["ownership_candidate_target"]
            + timing["scenario_candidate_target"],
        )
        self.assertLessEqual(payload["candidate_count"], timing["candidate_target"])
        self.assertEqual(
            timing["scenario_candidate_report"]["model"],
            "correlated-scenario-candidates-v1",
        )
        self.assertGreater(
            timing["scenario_candidate_report"]["unique_source_additions"]["scenario_built"],
            0,
        )
        self.assertEqual(payload["sim_report"]["model"], "scenario-portfolio-v4")
        self.assertEqual(payload["sim_report"]["field_preset"], "150-Max")
        self.assertTrue(payload["sim_report"]["preset_comparison"]["available"])
        self.assertIn("largest gap", payload["sim_report"]["preset_comparison"]["summary"])
        self.assertGreater(payload["portfolio_report"]["sim_summary"]["top_one_scenarios_covered"], 0)
        self.assertTrue(all(hasattr(lineup, "sim_scenario_values") for lineup in payload["lineups"]))

    def test_nfl_sim_lock_keeps_candidate_generation_in_the_optimizer_path(self):
        players = _fixture_players()
        locked = next(player for player in players if player["Position"] == "QB")
        locked["LockFlex"] = True
        finished = []
        worker = LineupBuildWorker(
            players,
            kind="classic",
            sport="NFL",
            num_lineups=8,
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
            sim_enabled=True,
            sim_scenarios=100,
        )
        worker.finished.connect(finished.append)

        worker.run()

        self.assertTrue(finished)
        payload = finished[0]
        timing = payload["timing_report"]
        self.assertEqual(timing["ownership_candidate_target"], 0)
        self.assertEqual(timing["scenario_candidate_target"], 0)
        locked_key = str(locked["FlexID"])
        self.assertTrue(all(
            locked_key in {str(player["FlexID"]) for player in lineup}
            for lineup in payload["lineups"]
        ))

    def test_build_completion_handles_missing_real_duplication_metric(self):
        window = MainWindow()
        payload = {
            "kind": "classic",
            "sport": "NFL",
            "requested": 5,
            "lineups": [],
            "cancelled": False,
            "portfolio_report": {"warnings": []},
            "sim_report": {
                "field_comparison": {
                    "available": True,
                    "simulated": {"duplicate_entry_pct": 12.5},
                    "real": {"duplicate_entry_pct": None},
                    "report_only": True,
                },
            },
            "timing_report": {
                "generation_seconds": 1.0,
                "simulation_seconds": 2.0,
                "selection_seconds": 0.1,
            },
        }

        with mock.patch.object(window, "_record_build_diagnostic"):
            window._on_lineup_build_finished(payload)

        self.assertIn("SIM duplication 12.5% vs real n/a", window.status.currentMessage())
        window.close()

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
