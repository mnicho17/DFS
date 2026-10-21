from test_environment import install
install()
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PyQt5 import QtWidgets,QtCore
from main_window import MainWindow
from history_backup import BackupCancelled,restore_history
from idle_backup_ui import BackupsDialog,IDLE_SECONDS,RECHECK_SECONDS


class IdleBackupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setup_controller(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        root=Path(tmp.name);source=root/'history';dest=root/'backup';source.mkdir();dest.mkdir()
        (source/'record.json').write_text('original')
        w=MainWindow();self.addCleanup(w.close);c=w.idle_backup;c.timer.stop()
        c.source=source;c.settings.setValue('backup/destination',str(dest));c.settings.setValue('backup/idle_enabled',True)
        clock=[0.0];c.clock=lambda:clock[0];c.last_input=c.last_change=c.next_attempt=0;c.next_check=RECHECK_SECONDS
        c.arm();return w,c,clock,root,source,dest

    def wait(self,c):
        end=time.monotonic()+10
        while c.thread is not None and time.monotonic()<end:self.app.processEvents();time.sleep(.005)
        self.assertIsNone(c.thread)
        self.app.processEvents()

    def slow(self,*args,**kwargs):
        while not kwargs['cancelled']():time.sleep(.005)
        raise BackupCancelled()

    def test_existing_launcher_folder_and_saved_override_are_respected(self):
        w,c,*_=self.setup_controller();c.settings.remove('backup/destination')
        with patch.dict(os.environ,{'DFS_BACKUP_ROOT':'configured-drive'}):
            self.assertEqual(c.destination(),'configured-drive')
            c.settings.setValue('backup/destination','explicit-folder')
            self.assertEqual(c.destination(),'explicit-folder')

    def test_automatic_start_waits_for_source_quiet_and_manual_workers(self):
        w,c,clock,*_=self.setup_controller()
        with patch.object(c,'start') as start:
            clock[0]=29;c.tick();start.assert_not_called()
            c.last_change=IDLE_SECONDS-10;clock[0]=IDLE_SECONDS;c.tick();start.assert_not_called()
            clock[0]+=30
            key=w.tasks.start('Build');c.tick();start.assert_not_called()
            w.tasks.finish(key,'Completed');c.tick();start.assert_called_once()

    def test_disabled_and_unconfigured_backups_do_no_work(self):
        w,c,clock,*_=self.setup_controller();clock[0]=1000
        c.settings.setValue('backup/idle_enabled',False)
        with patch.object(c,'start') as start:c.tick()
        start.assert_not_called()
        c.settings.setValue('backup/idle_enabled',True);c.settings.setValue('backup/destination','')
        with patch.object(c,'start') as start:c.tick()
        start.assert_not_called();self.assertEqual(c.state,'Not configured')

    def test_real_worker_records_manifest_and_unchanged_pass_does_not_copy_again(self):
        w,c,clock,root,source,dest=self.setup_controller()
        c.request();self.assertEqual(w.history_refresh._reports,1);self.wait(c)
        self.assertEqual(w.history_refresh._reports,0)
        manifest=str(c.settings.value('backup/last_manifest'));self.assertTrue(Path(manifest).is_file(),c.state+' | '+c.message)
        restore_history(manifest,root/'restored');self.assertEqual((root/'restored/record.json').read_text(),'original')
        c.request();self.wait(c)
        self.assertEqual(c.settings.value('backup/last_manifest'),manifest)
        self.assertEqual(len(list((dest/'incremental-v1/manifests').glob('*.json'))),1)
        self.assertIn('No current work',w.backup_summary.text())
        self.assertTrue(any(row['title']=='Incremental history backup' and row['state']=='Completed' for row in w.tasks.rows.values()))

    def test_manual_queue_pause_and_explicit_resume(self):
        w,c,clock,*_=self.setup_controller();key=w.tasks.start('Other work')
        c.request();self.assertTrue(c.manual);self.assertEqual(c.state,'Queued');self.assertIsNone(c.thread)
        c.cancel();self.assertTrue(c.paused);self.assertFalse(c.manual)
        w.tasks.finish(key,'Completed')
        with patch.object(c,'start') as start:c.tick()
        start.assert_not_called()
        c.request();self.wait(c);self.assertFalse(c.paused)

    def test_new_foreground_task_preempts_backup_and_preserves_previous_receipt(self):
        w,c,clock,*_=self.setup_controller()
        c.settings.setValue('backup/last_manifest','previous-complete')
        with patch('history_backup.backup_history',side_effect=self.slow):
            c.request();key=w.tasks.start('New build')
            self.assertTrue(c.worker.stop.is_set());self.wait(c)
        self.assertEqual(c.settings.value('backup/last_manifest'),'previous-complete')
        self.assertFalse(c.paused);self.assertTrue(c.dirty)
        w.tasks.finish(key,'Completed');clock[0]+=IDLE_SECONDS+1
        with patch.object(c,'start') as start:c.tick()
        start.assert_called_once()

    def test_user_cancel_preserves_receipt_and_requires_resume(self):
        w,c,clock,*_=self.setup_controller();c.settings.setValue('backup/last_manifest','previous')
        with patch('history_backup.backup_history',side_effect=self.slow):
            c.request();c.cancel();self.wait(c)
        self.assertEqual(c.settings.value('backup/last_manifest'),'previous')
        clock[0]=1000
        with patch.object(c,'start') as start:c.tick()
        start.assert_not_called();self.assertEqual(c.state,'Paused')

    def test_offline_drive_reports_attention_without_creation_and_backs_off(self):
        w,c,clock,root,*_=self.setup_controller();missing=root/'offline-drive'
        c.settings.setValue('backup/destination',str(missing));c.request();self.wait(c)
        self.assertEqual(c.state,'Needs attention');self.assertFalse(missing.exists())
        clock[0]=RECHECK_SECONDS-1
        with patch.object(c,'start') as start:c.tick()
        start.assert_not_called()
        self.assertFalse(c.settings.value('backup/last_manifest',''))

    def test_close_cancels_and_waits_without_starting_an_exit_backup(self):
        w,c,clock,*_=self.setup_controller()
        with patch('history_backup.backup_history',side_effect=self.slow) as backup:
            c.request();w.close()
            self.assertIsNotNone(c.thread);self.assertTrue(c.closed)
            self.wait(c)
        self.assertEqual(backup.call_count,1);self.assertFalse(c.timer.isActive())
        self.assertEqual(w.history_refresh._reports,0)
        with patch.object(c,'start') as start:c.request();c.tick()
        start.assert_not_called()

    def test_backup_status_and_settings_are_readonly_until_an_explicit_action(self):
        w,c,clock,*_=self.setup_controller()
        from preparation_status_ui import preparation_rows
        with patch('history_backup.backup_history',side_effect=AssertionError('No backup on open')):
            d=BackupsDialog(c,w);self.addCleanup(d.close)
            self.assertEqual(d.destination.text(),c.destination())
            rows={r['key']:r for r in preparation_rows(w)}
            self.assertEqual(rows['backup']['state'],c.state)
            self.assertIsNone(c.thread)
        d.enabled.setChecked(False);self.assertFalse(c.enabled())

    def test_stop_after_publication_retains_the_completed_manifest(self):
        w,c,clock,root,*_=self.setup_controller();retire=c.retire
        with patch.object(c,'retire') as delayed:
            c.request();end=time.monotonic()+10
            while not delayed.called and time.monotonic()<end:self.app.processEvents();time.sleep(.005)
            self.assertTrue(delayed.called)
            manifest=c.pending['manifest'];self.assertTrue(Path(manifest).is_file())
            c.cancel();retire()
        self.assertEqual(c.settings.value('backup/last_manifest'),manifest)
        self.assertEqual(c.state,'Paused');self.assertIn('complete manifest was retained',c.message)
        self.assertEqual(w.history_refresh._reports,0)
