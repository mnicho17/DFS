"""Report responsiveness, worker retirement, cancellation and retry."""
from test_environment import install
install()
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PyQt5 import QtWidgets
from main_window import ResultsLearningDialog


class LearningReportUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = str(Path(self.temp.name) / 'history.sqlite')
        with patch('learning_db.history_db_path', return_value=self.db):
            self.dialog = ResultsLearningDialog()
        self.release = threading.Event()
        self.entered = threading.Event()
        self.calls = []
        self.addCleanup(self.cleanup_dialog)

    def cleanup_dialog(self):
        self.release.set()
        self.wait_until(lambda: self.dialog._import_thread is None)
        self.dialog.close()
        self.dialog.deleteLater()
        self.app.processEvents()

    def wait_until(self, predicate):
        deadline = time.monotonic() + 10
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(predicate())

    def slow_report(self, **kwargs):
        self.calls.append((threading.get_ident(), kwargs))
        self.entered.set()
        if not self.release.wait(8):
            raise RuntimeError('Test failed to release worker')
        return dict(text='Completed report', exported_lineups=7)

    def test_open_is_responsive_and_report_uses_pinned_database(self):
        gui_thread = threading.get_ident()
        with patch('main_window.generate_learning_report', side_effect=self.slow_report):
            self.dialog.show()
            self.wait_until(self.entered.is_set)
            self.assertTrue(self.dialog.isVisible())
            self.assertNotEqual(self.calls[0][0], gui_thread)
            self.assertEqual(self.calls[0][1]['db_path'], self.db)
            self.assertFalse(self.dialog.import_new_button.isEnabled())
            self.assertNotEqual(self.dialog.report.toPlainText(), 'Completed report')
            self.release.set()
            self.wait_until(lambda: self.dialog._import_thread is None)
        self.assertEqual(self.dialog.report.toPlainText(), 'Completed report')
        self.assertIn('7 exported lineups', self.dialog.summary.text())
        self.assertTrue(self.dialog.import_new_button.isEnabled())
        self.assertEqual(self.dialog.history_refresh._reports, 0)

    def test_cancel_keeps_previous_report_and_waits_for_retirement(self):
        self.dialog.report.setPlainText('Previous report')
        with patch('main_window.generate_learning_report', side_effect=self.slow_report):
            self.dialog.refresh_report()
            self.wait_until(self.entered.is_set)
            self.dialog.cancel_import()
            self.assertIsNotNone(self.dialog._import_thread)
            self.assertFalse(self.dialog.refresh_button.isEnabled())
            self.release.set()
            self.wait_until(lambda: self.dialog._import_thread is None)
        self.assertEqual(self.dialog.report.toPlainText(), 'Previous report')
        self.assertIn('cancelled', self.dialog.summary.text())

    def test_close_waits_for_worker_and_does_not_publish_report(self):
        with patch('main_window.generate_learning_report', side_effect=self.slow_report):
            self.dialog.show()
            self.wait_until(self.entered.is_set)
            self.dialog.close()
            self.assertTrue(self.dialog.isVisible())
            self.release.set()
            self.wait_until(lambda: self.dialog._import_thread is None and not self.dialog.isVisible())
        self.assertEqual(self.dialog.report.toPlainText(), '')

    def test_failure_preserves_report_and_allows_retry(self):
        self.dialog.report.setPlainText('Previous report')
        with patch('main_window.generate_learning_report', side_effect=ValueError('unavailable')):
            self.dialog.refresh_report()
            self.wait_until(lambda: self.dialog._import_thread is None)
        self.assertEqual(self.dialog.report.toPlainText(), 'Previous report')
        self.assertIn('unavailable', self.dialog.summary.text())
        with patch('main_window.generate_learning_report', return_value=dict(text='Retried report')):
            self.dialog.refresh_report()
            self.wait_until(lambda: self.dialog._import_thread is None)
        self.assertEqual(self.dialog.report.toPlainText(), 'Retried report')

    def test_shared_history_refresh_defers_until_report_retires(self):
        controller = self.dialog.history_refresh
        with patch('main_window.generate_learning_report', side_effect=self.slow_report):
            self.dialog.refresh_report()
            self.wait_until(self.entered.is_set)
            self.assertFalse(controller.start())
            self.assertTrue(controller._deferred_start)
            with patch.object(controller, 'start', return_value=False) as resume:
                self.release.set()
                self.wait_until(lambda: self.dialog._import_thread is None)
                self.app.processEvents()
                resume.assert_called_once()

    def test_immediate_close_does_not_start_a_hidden_report(self):
        with patch('main_window.generate_learning_report') as build:
            self.dialog.show()
            self.dialog.close()
            self.app.processEvents()
            build.assert_not_called()
            self.assertIsNone(self.dialog._import_thread)


if __name__ == '__main__':
    unittest.main()
