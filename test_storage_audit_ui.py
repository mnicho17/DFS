from test_environment import install
install()
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
import unittest
from PyQt5 import QtCore,QtWidgets
from main_window import MainWindow
from storage_audit import audit
from storage_audit_ui import StorageAuditDialog


class StorageAuditUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    def setup_window(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        root=Path(temp.name);(root/'result.csv').write_text('valuable data')
        w=MainWindow();self.addCleanup(w.close);w.idle_backup.timer.stop()
        return w,root
    def wait(self,dialog):
        self.app.processEvents()
        end=time.monotonic()+10
        while (dialog.queued or dialog.thread is not None) and time.monotonic()<end:
            self.app.processEvents();time.sleep(.005)
        self.assertIsNone(dialog.thread);self.assertFalse(dialog.queued);self.app.processEvents()
    def test_real_worker_copies_read_only_report_and_retires_through_dashboard(self):
        w,root=self.setup_window();threads=[]
        def observe(*args,**kwargs):threads.append(QtCore.QThread.currentThread());return audit(*args,**kwargs)
        with patch('storage_audit_ui.configured_roots',return_value=[dict(path=root,role='sources')]),patch('storage_audit_ui.audit',side_effect=observe):
            d=StorageAuditDialog(w);self.wait(d)
            self.assertEqual(d.report['files'],1);self.assertNotEqual(threads[0],self.app.thread())
            self.assertEqual(w.history_refresh._reports,0);self.assertIsNone(w.background_queue.active)
            self.assertTrue(any(row['title']=='Read-only storage audit' and row['state']=='Completed' for row in w.tasks.rows.values()))
            d.copy_report();self.assertIn('No deletion',self.app.clipboard().text())
            self.assertEqual((root/'result.csv').read_text(),'valuable data');d.reject();d.deleteLater()
    def test_explicit_audit_waits_for_manual_work_and_can_cancel_before_start(self):
        w,root=self.setup_window();key=w.tasks.start('Manual build')
        with patch('storage_audit_ui.configured_roots',return_value=[dict(path=root,role='sources')]):
            d=StorageAuditDialog(w);self.app.processEvents();self.assertTrue(d.queued);self.assertIsNone(d.thread)
            d.cancel();self.assertFalse(d.queued);self.assertIsNone(d.report)
            w.tasks.finish(key,'Completed');self.app.processEvents();self.assertIsNone(d.thread)
            d.reject();d.deleteLater()
    def test_close_waits_for_cancelled_worker_and_releases_history_and_queue(self):
        w,root=self.setup_window();started=[]
        def slow(roots,*,cancelled,**kwargs):
            started.append(True)
            while not cancelled():time.sleep(.005)
            return audit(roots,cancelled=cancelled)
        with patch('storage_audit_ui.configured_roots',return_value=[dict(path=root,role='sources')]),patch('storage_audit_ui.audit',side_effect=slow):
            d=StorageAuditDialog(w);w._storage_audit_dialog=d
            end=time.monotonic()+5
            while not started and time.monotonic()<end:self.app.processEvents();time.sleep(.005)
            self.assertTrue(started);w.close();self.assertTrue(w._close_after_storage_audit);self.assertIsNotNone(d.thread)
            self.assertTrue(w.background_queue.closed)
            self.wait(d);self.assertEqual(w.history_refresh._reports,0);self.assertIsNone(w.background_queue.active)
            self.assertTrue(d.report['cancelled']);w._storage_audit_dialog=None;d.deleteLater()

    def test_archive_is_explicit_uses_backup_destination_and_existing_worker_queue(self):
        from test_legacy_scenario_archive import legacy
        w,root=self.setup_window();cache=root/'cache';backup=root/'backup';cache.mkdir();backup.mkdir()
        path=legacy(cache);before=path.read_bytes()
        roots=[dict(path=cache,role='scenario'),dict(path=root/'result.csv',role='sources'),dict(path=backup,role='backup')]
        with patch('storage_audit_ui.configured_roots',return_value=roots),patch.object(w.idle_backup,'destination',return_value=str(backup)):
            d=StorageAuditDialog(w);self.wait(d);self.assertFalse(list(backup.rglob('*.sqlite')))
            d.archive();self.wait(d)
            self.assertIn('1 verified copies',d.status.text());self.assertFalse(path.exists())
            self.assertIn('Freed: 1 working copies',d.status.text())
            self.assertEqual(next(backup.rglob('*.sqlite')).read_bytes(),before)
            self.assertEqual(len(list(backup.rglob('*.sqlite'))),1)
            self.assertTrue(any(row['title']=='Legacy cache archive' and 'Freed: 1 working copies' in row['detail'] for row in w.tasks.rows.values()))
            d.copy_report();self.assertIn('Freed: 1 working copies',self.app.clipboard().text())
            d.reject();d.deleteLater()

    def test_invalid_legacy_archive_keeps_source_and_retires_with_review_details(self):
        import sqlite3
        from test_legacy_scenario_archive import legacy
        w,root=self.setup_window();cache=root/'cache';backup=root/'backup';cache.mkdir();backup.mkdir()
        path=legacy(cache)
        with sqlite3.connect(path) as con:con.execute("UPDATE meta SET value=? WHERE key='digest'",('0'*64,))
        con.close();before=path.read_bytes()
        roots=[dict(path=cache,role='scenario'),dict(path=root/'result.csv',role='sources'),dict(path=backup,role='backup')]
        with patch('storage_audit_ui.configured_roots',return_value=roots),patch.object(w.idle_backup,'destination',return_value=str(backup)):
            d=StorageAuditDialog(w);self.wait(d);d.archive();self.wait(d)
            row=next(row for row in reversed(list(w.tasks.rows.values())) if row['title']=='Legacy cache archive')
            self.assertEqual(row['state'],'Needs review');self.assertIsNotNone(row['ended'])
            self.assertIn('1 error reported.',row['attention_reason'])
            self.assertIn(path.name,row['attention_reason']);self.assertIsNone(w.background_queue.active)
            self.assertFalse(w.tasks.bindings);self.assertEqual(path.read_bytes(),before)
            self.assertFalse(list(backup.rglob('*.sqlite')))
            d.reject();d.deleteLater()
