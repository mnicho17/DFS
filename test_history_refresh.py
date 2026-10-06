from test_environment import install
install()
import csv
import time
import unittest
from unittest.mock import patch
from PyQt5 import QtCore, QtWidgets
from test_hindsight_evidence import SourceFixture
from test_portfolio_risk_evidence import logical_db, source_bytes
from analysis_imports_ui import CombinedImportWorker
from history_refresh_ui import HistoryRefreshController, refresh_summary


class HistoryRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def fixture(self):
        f = SourceFixture()
        self.addCleanup(f.close)
        return f

    def worker(self, f):
        return CombinedImportWorker(str(f.results), str(f.salaries), 'Synthetic_User',
            db_path=f.db, refresh_history=True)

    def run_worker(self, worker):
        results, errors = [], []
        worker.finished.connect(results.append)
        worker.error.connect(errors.append)
        worker.run()
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        return results[0]

    def settings(self, f):
        settings = QtCore.QSettings(str(f.root/'settings.ini'), QtCore.QSettings.IniFormat)
        settings.setValue('learning/results_folder', str(f.results))
        settings.setValue('learning/salary_folder', str(f.salaries))
        settings.setValue('learning/dk_username', 'Synthetic_User')
        return settings

    def wait(self, controller):
        until = time.monotonic()+10
        while controller.busy and time.monotonic()<until:
            self.app.processEvents()
            time.sleep(.005)
        self.assertFalse(controller.busy)

    def test_existing_sources_index_once_and_second_refresh_is_unchanged(self):
        f = self.fixture()
        sources = source_bytes(f.root)
        with patch('performance_review.analyze_saved_results', side_effect=AssertionError('duplicate forecast rerun')):
            first = self.run_worker(self.worker(f))
            self.assertFalse(first['username_history']['contests'][0]['unchanged'])
            before = logical_db(f.db)
            second = self.run_worker(self.worker(f))
        self.assertTrue(second['username_history']['contests'][0]['unchanged'])
        self.assertEqual(before, logical_db(f.db))
        self.assertEqual(sources, source_bytes(f.root))
        self.assertIn('1 unchanged contests verified', refresh_summary(second))

    def test_new_results_flow_through_folder_import_and_username_index(self):
        f = self.fixture()
        with f.raw_result.open(newline='', encoding='utf-8-sig') as handle:
            rows = list(csv.reader(handle))
        rows[1][1] = '2000'
        rows[1][5] = 'another-contest'
        path = f.results/'new.csv'
        with path.open('w', newline='', encoding='utf-8-sig') as handle:
            csv.writer(handle).writerows(rows)
        with patch('performance_review.analyze_saved_results', return_value={'message':'done'}):
            result = self.run_worker(self.worker(f))
        self.assertEqual(result['results_imported'], 1)
        self.assertEqual(result['errors'], [])
        self.assertEqual(f.query('SELECT COUNT(*) FROM opponent_entries')[0][0], 2)
        self.assertIn('2000', [r[0] for r in f.query('SELECT entry_id FROM opponent_entries')])

    def test_changed_file_is_separate_evidence_and_overlap_needs_review(self):
        f = self.fixture()
        self.run_worker(self.worker(f))
        old_pair = f.query('SELECT result_hash,salary_hash FROM analysis_salary_pairs')
        text = f.raw_result.read_text(encoding='utf-8-sig')
        f.raw_result.write_text(text.replace('Synthetic_User', 'Changed_User'), encoding='utf-8-sig')
        with patch('performance_review.analyze_saved_results', return_value={'message':'done'}):
            result = self.run_worker(self.worker(f))
        self.assertEqual(result['changed_sources'], ['synthetic.csv'])
        self.assertTrue(result['username_history']['errors'])
        self.assertEqual(f.query('SELECT COUNT(*) FROM opponent_entries')[0][0], 1)
        self.assertIn(old_pair[0], f.query('SELECT result_hash,salary_hash FROM analysis_salary_pairs'))
        self.assertIn('Changed files saved as separate evidence', refresh_summary(result))

    def test_unmapped_results_remain_visible_without_invented_salary_match(self):
        f = self.fixture()
        f.change('DELETE FROM analysis_salary_pairs')
        f.change("UPDATE analysis_sources SET manifest=replace(manifest,'2026-09-21','2026-09-22') WHERE kind='salary'")
        result = self.run_worker(self.worker(f))
        self.assertEqual(result['unpaired'], 1)
        self.assertEqual(result['username_history']['contests'], [])
        self.assertIn('1 results need salary matching', refresh_summary(result))

    def test_cancel_before_indexing_does_not_start_history(self):
        f = self.fixture()
        worker = self.worker(f)
        worker.cancelled.set()
        with patch('opponent_history.sync_saved', side_effect=AssertionError('cancelled indexing')):
            result = self.run_worker(worker)
        self.assertTrue(result['cancelled'])
        self.assertNotIn('username_history', result)

    def test_startup_setting_off_and_missing_folders_make_no_writes(self):
        f = self.fixture()
        settings = self.settings(f)
        controller = HistoryRefreshController(settings, f.db)
        before = logical_db(f.db)
        settings.setValue('learning/auto_history_refresh', False)
        controller.startup()
        self.assertFalse(controller.busy)
        settings.setValue('learning/auto_history_refresh', True)
        settings.setValue('learning/results_folder', str(f.root/'missing'))
        controller.startup()
        self.wait(controller)
        self.assertIn('Folder unavailable', controller.text)
        self.assertEqual(before, logical_db(f.db))

    def test_controller_prevents_overlapping_jobs_and_persists_summary(self):
        f = self.fixture()
        settings = self.settings(f)
        controller = HistoryRefreshController(settings, f.db)
        finished = []
        controller.completed.connect(lambda:finished.append(True))
        self.assertTrue(controller.start())
        self.assertFalse(controller.start())
        self.wait(controller)
        self.assertEqual(finished, [True])
        self.assertEqual(settings.value('learning/last_history_refresh'), controller.text)
        self.assertIn('1 newly indexed contests', controller.text)

    def test_queued_completion_then_cancel_is_reported_cancelled(self):
        f = self.fixture()
        controller = HistoryRefreshController(self.settings(f), f.db)
        self.assertTrue(controller.start())
        controller.receive(dict(results_imported=2, report={'text':'old result'}))
        controller.cancel()
        self.wait(controller)
        self.assertIn('Refresh cancelled', controller.text)

    def test_dialog_uses_shared_controller_and_blocks_other_history_actions(self):
        from main_window import ResultsLearningDialog
        f = self.fixture()
        parent = QtWidgets.QWidget()
        parent.history_refresh = HistoryRefreshController(self.settings(f), f.db, parent)
        with patch('learning_db.history_db_path', return_value=f.db):
            dialog = ResultsLearningDialog(parent)
        self.assertIs(dialog.history_refresh, parent.history_refresh)
        self.assertTrue(parent.history_refresh.start())
        self.assertFalse(dialog.import_new_button.isEnabled())
        self.assertFalse(dialog.opponents_button.isEnabled())
        self.assertFalse(dialog.history_refresh_button.isEnabled())
        self.wait(parent.history_refresh)
        deadline = time.monotonic() + 10
        while dialog._import_thread is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertIsNone(dialog._import_thread)
        self.assertTrue(dialog.import_new_button.isEnabled())
        self.assertIn('1 newly indexed contests', dialog.history_status.toPlainText())
        dialog.reject()
        dialog.deleteLater()
        parent.deleteLater()
        self.app.processEvents()

    def test_standalone_dialog_close_cancels_before_controller_destruction(self):
        from main_window import ResultsLearningDialog
        f = self.fixture()
        with patch('learning_db.history_db_path', return_value=f.db):
            dialog = ResultsLearningDialog()
        # Configure the isolated controller, not desktop registry preferences.
        dialog.history_refresh.settings = self.settings(f)
        dialog.show()
        self.assertTrue(dialog.history_refresh.start())
        dialog.reject()
        self.assertTrue(dialog.history_refresh.stop.is_set())
        self.assertTrue(dialog.isVisible())
        self.wait(dialog.history_refresh)
        self.app.processEvents()
        self.assertFalse(dialog.isVisible())
        dialog.deleteLater()
        self.app.processEvents()
