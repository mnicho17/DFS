"""Default publication paths stay outside checkouts and read-only history."""
from test_environment import install, network_attempts
install()

from datetime import date
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PyQt5 import QtWidgets
import data_paths
import review_report as rr
from review_report_ui import ReviewReportDialog


class ReviewLocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'DFS_OPTIMIZER_DATA_DIR': str(self.root / 'data'),
                                          'LOCALAPPDATA': str(self.root / 'local')})
        self.env.start(); self.addCleanup(self.env.stop)
        self.network_before = list(network_attempts)

    def tearDown(self):
        self.assertEqual(network_attempts, self.network_before)

    def dialog(self):
        dialog = ReviewReportDialog()
        dialog._report = rr.capture()
        dialog._options_at_capture = dialog.options()
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_default_directory_created_outside_checkout_and_source_history(self):
        target = self.root / 'data' / 'review-reports'
        self.assertFalse(target.exists())
        actual = data_paths.review_reports_directory()
        self.assertEqual(actual, target)
        self.assertTrue(actual.is_dir())
        self.assertFalse(actual.is_relative_to(Path(data_paths.__file__).resolve().parent))
        self.assertFalse(actual.is_relative_to(rr.source_paths()[0].parent))

    def test_source_checkout_override_falls_back_to_local_app_data(self):
        checkout = self.root / 'checkout'
        (checkout / '.git').mkdir(parents=True)
        with patch.dict(os.environ, {'DFS_OPTIMIZER_DATA_DIR': str(checkout / 'custom-data')}):
            target = data_paths.review_reports_directory()
        self.assertEqual(target, self.root / 'local' / 'DFS Optimizer' / 'review-reports')
        self.assertFalse((checkout / 'custom-data').exists())

    def test_packaged_and_source_resolve_same_history_as_learning_database(self):
        from learning_db import history_db_path
        for frozen in (False, True):
            with self.subTest(frozen=frozen), patch.object(sys, 'frozen', frozen, create=True):
                self.assertEqual(Path(history_db_path()), rr.source_paths()[0])
                self.assertEqual(rr.source_paths(), data_paths.history_source_paths())
                if frozen:
                    with patch.dict(os.environ, {'DFS_OPTIMIZER_DATA_DIR': ''}):
                        self.assertEqual(Path(history_db_path()), self.root / 'local' / 'DFS Optimizer' / 'history' / 'exports.sqlite')
                        self.assertEqual(Path(history_db_path()), rr.source_paths()[0])
                else:
                    with patch.dict(os.environ, {'DFS_OPTIMIZER_DATA_DIR': ''}):
                        self.assertEqual(data_paths.history_source_paths()[0].parent.parent, Path(data_paths.__file__).resolve().parent)

    def test_default_filename_and_cancel_leave_no_partial_zip(self):
        dialog = self.dialog()
        with patch.object(QtWidgets.QFileDialog, 'getSaveFileName', return_value=('', '')) as save:
            dialog.save_report()
        suggestion = Path(save.call_args.args[2])
        self.assertEqual(suggestion.parent, self.root / 'data' / 'review-reports')
        self.assertEqual(suggestion.name, f'DFS-Review-{date.today().isoformat()}-{dialog._report.data["report_id"][:8]}.zip')
        self.assertEqual(list(suggestion.parent.iterdir()), [])
        self.assertIsNone(dialog._job)

    def test_user_destination_keeps_frozen_contents_and_final_path(self):
        dialog = self.dialog()
        chosen = self.root / 'chosen.zip'
        def launch(operation, kind):
            self.assertEqual(kind, 'save')
            result = operation(lambda: False, lambda _: None)
            worker = type('Worker', (), {'cancelled': type('Event', (), {'is_set': lambda _: False})()})()
            dialog._job = dict(identity=1, kind=kind, result=result, error=None, worker=worker)
            dialog._retired(1)
        with patch.object(QtWidgets.QFileDialog, 'getSaveFileName', return_value=(str(chosen), '')), patch.object(dialog, '_launch', side_effect=launch):
            dialog.save_report()
        self.assertIn(str(chosen), dialog.status.text())
        with zipfile.ZipFile(chosen) as archive:
            self.assertEqual(archive.namelist(), ['summary.md', 'evidence.json'])
            self.assertEqual(archive.read('evidence.json'), dialog._report.evidence)
            self.assertEqual(archive.read('summary.md').decode(), dialog._report.summary())
            self.assertNotIn(str(self.root), archive.read('evidence.json').decode())

    def test_existing_default_requires_ordinary_save_dialog_consent(self):
        dialog = self.dialog()
        directory = data_paths.review_reports_directory()
        target = directory / f'DFS-Review-{date.today().isoformat()}-{dialog._report.data["report_id"][:8]}.zip'
        target.write_bytes(b'previous report')
        with patch.object(QtWidgets.QFileDialog, 'getSaveFileName', return_value=('', '')) as save:
            dialog.save_report()
        self.assertEqual(Path(save.call_args.args[2]), target)
        # Default Qt options retain native overwrite confirmation.
        self.assertNotIn('options', save.call_args.kwargs)
        self.assertEqual(target.read_bytes(), b'previous report')
        self.assertIsNone(dialog._job)

    def test_default_publish_and_cancel_preserve_source_history(self):
        db, diagnostic = rr.source_paths()
        db.parent.mkdir(parents=True)
        diagnostic.write_text('{}')
        before = {p: p.read_bytes() for p in db.parent.iterdir()}
        report = rr.capture()
        target = data_paths.review_reports_directory(excluded_roots=report._source_roots) / 'review.zip'
        with self.assertRaises(rr.Cancelled):
            rr.publish(report, target, cancelled=lambda: True)
        self.assertEqual(list(target.parent.iterdir()), [])
        rr.publish(report, target)
        self.assertEqual(before, {p: p.read_bytes() for p in db.parent.iterdir()})
        for forbidden in (db.parent / 'review.zip', diagnostic):
            with self.assertRaises(rr.SourceDestination):
                rr.publish(report, forbidden)

    def test_selected_source_root_is_not_used_as_default_destination(self):
        external = self.root / 'data'
        directory = data_paths.review_reports_directory(excluded_roots=(external,))
        self.assertFalse(directory.is_relative_to(external))

    def test_unavailable_default_reports_error_without_relative_fallback(self):
        dialog = self.dialog()
        with patch('data_paths.review_reports_directory', side_effect=OSError('private path')), patch.object(QtWidgets.QFileDialog, 'getSaveFileName') as save:
            dialog.save_report()
        save.assert_not_called()
        self.assertIn('unavailable', dialog.status.text())
        self.assertNotIn('private path', dialog.status.text())


if __name__ == '__main__':
    unittest.main()
