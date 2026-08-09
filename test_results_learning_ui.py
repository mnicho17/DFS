from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtWidgets

from main_window import LineupBuildWorker, MainWindow, ResultsLearningDialog
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
