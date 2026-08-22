from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtCore, QtWidgets

from main_window import BuildDiagnosticsDialog, EntrySafetyDialog, FinalLockCheckDialog, LineupBuildWorker, LiveDataSettingsDialog, MainWindow, PortfolioInsightsDialog, ResultsImportWorker, ResultsLearningDialog, StackExposureDialog, _deep_shortlist
from build_diagnostics import create_build_diagnostic, load_build_history, save_build_diagnostic
from learning_db import generate_learning_report
from nfl_simulation import SimLineup
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
        self.assertEqual(button.text(), "Results and Learning")
        self.assertIsNotNone(window.findChild(QtWidgets.QSpinBox, "portfolioMinUnique"))
        insights_button = window.findChild(QtWidgets.QPushButton, "portfolioSummaryButton")
        self.assertIsNotNone(insights_button)
        self.assertEqual(insights_button.text(), "Insights")
        portfolio_action = window.findChild(QtWidgets.QAction, "portfolioInsightsAction")
        self.assertIsNotNone(portfolio_action)
        self.assertEqual(portfolio_action.text(), "Portfolio Insights...")
        self.assertNotIn("â", portfolio_action.text())
        sim_toggle = window.findChild(QtWidgets.QCheckBox, "nflSimEdgeCheck")
        self.assertIsNotNone(sim_toggle)
        self.assertTrue(sim_toggle.isChecked())
        self.assertIsNotNone(window.findChild(QtWidgets.QSpinBox, "nflSimScenarios"))
        preset = window.findChild(QtWidgets.QComboBox, "nflFieldPreset")
        self.assertIsNotNone(preset)
        self.assertEqual(preset.currentText(), "150-Max")
        compute = window.findChild(QtWidgets.QComboBox, "nflComputeMode")
        self.assertIsNotNone(compute)
        self.assertEqual(compute.currentText(), "Fast (default)")
        self.assertEqual(window.findChild(QtWidgets.QPushButton, "gameDayCheckButton").text(), "Game-Day Check")
        self.assertIsNotNone(window.findChild(QtWidgets.QPushButton, "liveDataSettingsButton"))
        self.assertIn("Live data", window.findChild(QtWidgets.QLabel, "liveDataStatusLabel").text())
        self.assertEqual(window.findChild(QtWidgets.QPushButton, "slateReadinessButton").text(), "Slate Readiness")
        self.assertIn("Readiness", window.findChild(QtWidgets.QLabel, "slateReadinessStatus").text())
        self.assertIn("Lineup space", window.findChild(QtWidgets.QLabel, "lineupSpaceStatus").text())
        self.assertIsNotNone(window.findChild(QtWidgets.QToolButton, "clearReadinessFilterButton"))
        self.assertIsNotNone(window.findChild(QtWidgets.QAction, "saveBuildRecipeAction"))
        self.assertIsNotNone(window.findChild(QtWidgets.QAction, "manageBuildRecipesAction"))
        copy_action = window.findChild(QtWidgets.QAction, "copyLastBuildReportAction")
        self.assertIsNotNone(copy_action)
        self.assertFalse(copy_action.isEnabled())
        build_history_action = window.findChild(QtWidgets.QAction, "buildHistoryAction")
        self.assertIsNotNone(build_history_action)
        self.assertEqual(build_history_action.text(), "Build History...")
        window.close()

    def test_deep_shortlist_preserves_retained_and_candidate_source_diversity(self):
        lineups = []
        for index in range(18):
            source = "optimizer" if index < 14 else ("field_shaped" if index < 16 else "scenario_built")
            archetype = "general" if source != "scenario_built" else ("Ceiling" if index == 16 else "Low-Dup")
            players = [{
                "Name": f"Deep {index}-{slot}",
                "FlexID": f"deep-{index}-{slot}",
                "FlexProjection": 20.0 - slot,
            } for slot in range(3)]
            lineups.append(SimLineup(
                players,
                metrics={
                    "sim_edge": float(95 - index),
                    "sim_top_one_pct": 5.0,
                    "sim_return_index": float(90 - index),
                },
                candidate_source=source,
                candidate_archetype=archetype,
            ))
        retained_signature = tuple(sorted(player["FlexID"] for player in lineups[-1]))

        shortlisted = _deep_shortlist(
            lineups,
            8,
            reserved_signatures=[retained_signature],
        )

        signatures = {
            tuple(sorted(player["FlexID"] for player in lineup))
            for lineup in shortlisted
        }
        sources = {lineup.candidate_source for lineup in shortlisted}
        self.assertEqual(len(shortlisted), 8)
        self.assertIn(retained_signature, signatures)
        self.assertEqual(sources, {"optimizer", "field_shaped", "scenario_built"})

    def test_portfolio_insights_dialog_exposes_overview_details_and_copy(self):
        dialog = PortfolioInsightsDialog({
            "status": "Review 1 lineup",
            "text": "DFS Optimizer Portfolio Insights\nCandidate sources",
            "flagged_count": 1,
            "lineup_rows": [{
                "number": 1, "grade": "A", "source": "Scenario-built", "archetype": "Ceiling",
                "salary": 49900, "stack": "QB+2", "bringback": "Yes", "flex": "WR",
                "ownership": 108, "edge": 84, "leverage": 75, "duplication": 25,
                "top_one_pct": 4.2, "return_index": 80, "top_scenarios": 9,
                "flag_codes": ["concentrated_core"], "review": "concentrated core",
                "player_keys": ["BUF QB"],
            }],
            "exposure_rows": [{
                "key": "BUF QB", "name": "BUF QB", "team": "BUF", "position": "QB",
                "count": 1, "pct": 100.0, "lineup_numbers": [1],
            }],
        })
        self.assertEqual(dialog.findChild(QtWidgets.QTabWidget, "portfolioInsightsTabs").count(), 3)
        self.assertIn(
            "Candidate sources",
            dialog.findChild(QtWidgets.QPlainTextEdit, "portfolioInsightsReport").toPlainText(),
        )
        self.assertEqual(dialog.findChild(QtWidgets.QTableWidget, "portfolioInsightsLineups").rowCount(), 1)
        self.assertEqual(dialog.findChild(QtWidgets.QTableWidget, "portfolioInsightsExposure").rowCount(), 1)
        dialog.findChild(QtWidgets.QTableWidget, "portfolioInsightsExposure").selectRow(0)
        dialog.findChild(QtWidgets.QPushButton, "showExposureLineups").click()
        self.assertEqual(dialog.findChild(QtWidgets.QTabWidget, "portfolioInsightsTabs").currentIndex(), 1)
        self.assertEqual(dialog.selected_lineup_indexes(), [0])
        dialog.findChild(QtWidgets.QPushButton, "selectFlaggedLineups").click()
        self.assertEqual(dialog.selected_lineup_indexes(), [0])
        dialog.findChild(QtWidgets.QPushButton, "copyPortfolioInsights").click()
        self.assertIn("Portfolio Insights", QtWidgets.QApplication.clipboard().text())
        dialog.findChild(QtWidgets.QPushButton, "removeInsightLineups").click()
        self.assertEqual(dialog.requested_action, "remove")
        self.assertEqual(dialog.requested_indexes, [0])
        dialog.close()

    def test_final_lock_dialog_can_replace_exact_affected_lineups(self):
        dialog = FinalLockCheckDialog({
            "status": "attention",
            "title": "2 Saved Lineups Need Review",
            "player_count": 192,
            "sleeper_matches": 190,
            "lineup_count": 20,
            "affected_lineups": 2,
            "affected_indexes": [3, 11],
            "changes": [{
                "name": "Late Scratch",
                "team": "BUF",
                "availability": "OUT",
                "change": "Active → Out",
                "lineup_numbers": [4, 12],
            }],
            "text": "FINAL LOCK CHECK",
        })
        repair = dialog.findChild(QtWidgets.QPushButton, "repairFinalLockLineups")
        self.assertIsNotNone(repair)
        self.assertEqual(
            dialog.findChild(QtWidgets.QTableWidget, "finalLockChanges").item(0, 4).text(),
            "4, 12",
        )
        repair.click()
        self.assertTrue(dialog.repair_requested)
        dialog.close()

    def test_entry_safety_dialog_can_replace_blocked_lineups(self):
        dialog = EntrySafetyDialog({
            "status": "blocked",
            "title": "Blocked",
            "sport": "NFL",
            "kind": "classic",
            "lineup_count": 20,
            "blockers": 1,
            "reviews": 0,
            "blocked_lineup_indexes": [2],
            "checks": [{
                "status": "block",
                "label": "Roster validity",
                "summary": "One player is repeated.",
                "action": "Replace the affected lineup.",
            }],
            "text": "ENTRY SAFETY — BLOCKED",
        })
        repair = dialog.findChild(QtWidgets.QPushButton, "repairBlockedEntrySafetyLineups")
        export = dialog.findChild(QtWidgets.QPushButton, "confirmSafeExport")
        self.assertIsNotNone(repair)
        self.assertFalse(export.isEnabled())
        repair.click()
        self.assertTrue(dialog.repair_requested)
        dialog.close()

    def test_saved_lineup_repair_keeps_unaffected_indexes_fixed(self):
        window = MainWindow()
        lineups = [[{"FlexID": str(index)}] for index in range(4)]
        with mock.patch.object(window, "_handle_portfolio_insights_action") as handle:
            window._repair_saved_lineups("classic", lineups, [1, 3], 50000)
        handle.assert_called_once()
        call = handle.call_args.kwargs
        self.assertEqual(call["indexes"], [1, 3])
        self.assertEqual(call["action"], "replace")
        self.assertEqual(call["source_label"], "saved")
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
            ["Build Strategy", "Portfolio Rules", "Data and Learning"],
        )
        self.assertTrue(window.tabs_workspace_controls.isHidden())
        self.assertIn("Strategic", window.lbl_workspace_summary.text())
        show_controls = window.findChild(QtWidgets.QAction, "showBuildControlsAction")
        self.assertIsNotNone(show_controls)
        show_controls.setChecked(True)
        self.assertFalse(window.tabs_workspace_controls.isHidden())
        show_controls.setChecked(False)
        self.assertTrue(window.tabs_workspace_controls.isHidden())
        saved_panel = window.findChild(QtWidgets.QWidget, "savedPortfolioPanel")
        self.assertIsNotNone(saved_panel)
        show_saved = window.findChild(QtWidgets.QAction, "showSavedPortfolioAction")
        self.assertFalse(show_saved.isChecked())
        self.assertTrue(saved_panel.isHidden())
        show_saved.setChecked(True)
        self.assertFalse(saved_panel.isHidden())
        self.assertIsNotNone(window.findChild(QtWidgets.QGroupBox, "playerInspector"))
        self.assertEqual(window.tabs_saved.count(), 2)
        window.close()

    def test_stack_exposure_only_shows_pitchers_for_mlb(self):
        common = {
            "total_lineups": 2,
            "team_rows": [],
            "stack_rows": [],
            "salary_rows": [],
            "pitcher_rows": [{
                "Pitcher": "Example Pitcher", "Team": "SEA", "Count": 1,
                "Pct": 50.0, "AvgSalary": 9000, "AvgProj": 22.5,
            }],
        }

        nfl = StackExposureDialog(None, sport="NFL", **common)
        nfl_tabs = nfl.findChild(QtWidgets.QTabWidget, "stackExposureTabs")
        self.assertEqual(
            [nfl_tabs.tabText(i) for i in range(nfl_tabs.count())],
            ["Team Exposure", "Stack Shapes", "Salary Bands"],
        )
        self.assertIsNone(nfl.tbl_pitcher)
        nfl.close()

        mlb = StackExposureDialog(None, sport="MLB", **common)
        mlb_tabs = mlb.findChild(QtWidgets.QTabWidget, "stackExposureTabs")
        self.assertEqual(mlb_tabs.tabText(mlb_tabs.count() - 1), "Pitchers")
        self.assertEqual(mlb.tbl_pitcher.rowCount(), 1)
        mlb.close()

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

    def test_player_and_lineup_columns_use_content_appropriate_alignment(self):
        window = MainWindow()
        window.resize(1540, 700)
        window.show()
        self.app.processEvents()
        window.players = [{
            "Name": "Example Receiver", "Team": "BUF", "Position": "WR",
            "FlexSalary": 6100, "BaseProjection": 15.0, "FlexProjection": 16.2,
            "NFLAvailability": "STARTER", "NFLAdjScore": 0.4,
            "NFLUsageScore": 0.3, "NFLMatchupScore": 0.2,
            "NFLRole": "SWR1", "NFLRoleScore": 0.8,
            "NFLWeatherScore": -0.1, "NFLVegasTeamTotal": 25.5,
            "ProjOwnPct": 12.4,
        }]
        window._refresh_players_table()
        self.app.processEvents()

        horizontal_mask = int(QtCore.Qt.AlignHorizontal_Mask)
        self.assertEqual(
            window.tbl_players.horizontalHeaderItem(0).textAlignment() & horizontal_mask,
            int(QtCore.Qt.AlignLeft),
        )
        self.assertEqual(
            window.tbl_players.horizontalHeaderItem(4).textAlignment() & horizontal_mask,
            int(QtCore.Qt.AlignRight),
        )
        self.assertEqual(
            window.tbl_players.item(0, 1).textAlignment() & horizontal_mask,
            int(QtCore.Qt.AlignHCenter),
        )
        self.assertEqual(
            window.tbl_players.item(0, 4).textAlignment() & horizontal_mask,
            int(QtCore.Qt.AlignRight),
        )
        player_header = window.tbl_players.horizontalHeader()
        self.assertLess(player_header.sectionSize(10), player_header.sectionSize(0))

        window.tabs_lineups.setCurrentIndex(1)
        total_column = window.tbl_cl.columnCount() - 2
        grade_column = window.tbl_cl.columnCount() - 1
        self.assertEqual(window.tbl_cl.horizontalHeader().sectionSize(total_column), 88)
        self.assertEqual(window.tbl_cl.horizontalHeader().sectionSize(grade_column), 92)
        self.assertEqual(
            window.tbl_cl.horizontalHeaderItem(total_column).textAlignment() & horizontal_mask,
            int(QtCore.Qt.AlignRight),
        )
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

    def test_classic_worker_replaces_only_open_portfolio_slots(self):
        rules = {
            "min_unique": 2,
            "max_team_pct": 100.0,
            "max_game_pct": 100.0,
            "balance_ownership": True,
            "groups": [],
            "player_constraints": {},
        }
        initial = []
        first = LineupBuildWorker(
            _fixture_players(),
            kind="classic",
            sport="NFL",
            num_lineups=8,
            salary_cap=50000,
            build_style="Strategic",
            salary_strategy="Near Cap",
            portfolio_rules=rules,
            sim_enabled=False,
        )
        first.finished.connect(initial.append)
        first.run()
        self.assertTrue(initial)
        retained = initial[0]["lineups"][:6]

        repaired = []
        worker = LineupBuildWorker(
            _fixture_players(),
            kind="classic",
            sport="NFL",
            num_lineups=8,
            salary_cap=50000,
            build_style="Strategic",
            salary_strategy="Near Cap",
            portfolio_rules=rules,
            sim_enabled=False,
            retained_lineups=retained,
            repair_source="generated",
        )
        worker.finished.connect(repaired.append)
        worker.run()

        self.assertTrue(repaired)
        payload = repaired[0]
        self.assertEqual(len(payload["lineups"]), 8)
        self.assertEqual(payload["lineups"][:6], retained)
        self.assertEqual(payload["repair_source"], "generated")
        self.assertEqual(payload["timing_report"]["retained_count"], 6)
        self.assertEqual(payload["timing_report"]["replacement_requested"], 2)

    def test_portfolio_insights_repair_keeps_unselected_rows_fixed(self):
        window = MainWindow()
        lineups = [object(), object(), object()]
        with mock.patch.object(
            QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Yes
        ), mock.patch.object(window, "_start_lineup_build") as start_build:
            window._handle_portfolio_insights_action(
                kind="classic",
                sport="NFL",
                source_label="generated",
                lineups=lineups,
                action="replace",
                indexes=[1],
                salary_cap=50000,
            )

        start_build.assert_called_once_with(
            kind="classic",
            sport="NFL",
            num=3,
            cap=50000,
            retained_lineups=[lineups[0], lineups[2]],
            repair_source="generated",
        )
        window.close()

    def test_sim_repair_rescores_retained_and_replacement_lineups_together(self):
        rules = {
            "min_unique": 2,
            "max_team_pct": 100.0,
            "max_game_pct": 100.0,
            "balance_ownership": True,
            "groups": [],
            "player_constraints": {},
        }
        initial = []
        first = LineupBuildWorker(
            _fixture_players(),
            kind="classic",
            sport="NFL",
            num_lineups=8,
            salary_cap=50000,
            build_style="Strategic",
            salary_strategy="Near Cap",
            portfolio_rules=rules,
            sim_enabled=False,
        )
        first.finished.connect(initial.append)
        first.run()
        retained = initial[0]["lineups"][:6]
        retained_signatures = {
            tuple(sorted(str(player["FlexID"]) for player in lineup))
            for lineup in retained
        }

        repaired = []
        worker = LineupBuildWorker(
            _fixture_players(),
            kind="classic",
            sport="NFL",
            num_lineups=8,
            salary_cap=50000,
            build_style="Strategic",
            salary_strategy="Near Cap",
            portfolio_rules=rules,
            sim_enabled=True,
            sim_scenarios=100,
            retained_lineups=retained,
            repair_source="generated",
        )
        worker.finished.connect(repaired.append)
        worker.run()

        self.assertTrue(repaired)
        output = repaired[0]["lineups"]
        self.assertEqual(len(output), 8)
        output_signatures = {
            tuple(sorted(str(player["FlexID"]) for player in lineup))
            for lineup in output
        }
        self.assertTrue(retained_signatures.issubset(output_signatures))
        self.assertTrue(all(
            int(getattr(lineup, "sim_metrics", {}).get("sim_scenarios", 0) or 0) == 100
            for lineup in output
        ))

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
        selected_sources = payload["sim_report"]["candidate_sources"]["selected"]
        self.assertEqual(sum(selected_sources.values()), len(payload["lineups"]))
        self.assertTrue(all(
            getattr(lineup, "candidate_source", "") in {"optimizer", "field_shaped", "scenario_built"}
            for lineup in payload["lineups"]
        ))
        self.assertTrue(payload["sim_report"]["preset_comparison"]["available"])
        self.assertIn("largest gap", payload["sim_report"]["preset_comparison"]["summary"])
        self.assertGreater(payload["portfolio_report"]["sim_summary"]["top_one_scenarios_covered"], 0)
        self.assertTrue(all(hasattr(lineup, "sim_scenario_values") for lineup in payload["lineups"]))

    def test_deep_worker_keeps_best_completed_stage_under_a_short_time_budget(self):
        progress = []
        finished = []
        worker = LineupBuildWorker(
            _fixture_players(),
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
            sim_scenarios=250,
            compute_mode="Deep (up to 5 min)",
            deep_time_limit_seconds=4.0,
        )
        worker.progress.connect(lambda done, total, text: progress.append(text))
        worker.finished.connect(finished.append)

        worker.run()

        self.assertTrue(finished)
        payload = finished[0]
        timing = payload["timing_report"]
        deep = payload["sim_report"]["deep_build"]
        self.assertEqual(len(payload["lineups"]), 8)
        self.assertEqual(timing["compute_mode"], "Deep")
        self.assertGreater(payload["candidate_count"], 8)
        self.assertGreater(deep["screening_scenarios"], 0)
        self.assertGreaterEqual(deep["shortlist_count"], 8)
        self.assertIn("refinement_swaps", deep)
        self.assertIn("duplication_refinement_swaps", deep)
        self.assertIn("refinement_stop_reason", deep)
        self.assertIn("time_remaining_seconds", deep)
        self.assertTrue(any("Phase 1 of 4" in text for text in progress))
        self.assertTrue(any("Phase 2 of 4" in text for text in progress))
        self.assertTrue(any("Phase 4 of 4" in text for text in progress))

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
        ), mock.patch.object(
            EntrySafetyDialog, "exec_", return_value=QtWidgets.QDialog.Accepted
        ), mock.patch.object(QtWidgets.QMessageBox, "information"):
            window.on_export_saved("showdown")
        self.assertTrue(os.path.isfile(export_path))
        report = generate_learning_report()
        self.assertEqual(report["exported_lineups"], 1)
        window.close()


if __name__ == "__main__":
    unittest.main()
