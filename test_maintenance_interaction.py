from test_environment import install
install()
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PyQt5 import QtCore, QtGui, QtWidgets
from main_window import MainWindow
from csv_loading import CsvLoadController


class MaintenanceInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def window(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        root=Path(tmp.name);source=root/'sources';source.mkdir()
        w=MainWindow();self.addCleanup(w.close)
        w.background_queue.timer.stop();w.idle_backup.timer.stop()
        self.app.processEvents()  # Drain the constructor startup before configuring test sources.
        w.history_refresh.settings.setValue('learning/results_folder',str(source))
        w.history_refresh.db_path=str(root/'history.sqlite')
        clock=[0.0];w.idle_backup.clock=lambda:clock[0];w.idle_backup.last_input=0
        return w,clock,root

    def wait(self,predicate):
        end=time.monotonic()+8
        while not predicate() and time.monotonic()<end:
            self.app.processEvents();time.sleep(.003)
        self.assertTrue(predicate())

    def wheel(self, window):
        event=QtGui.QWheelEvent(QtCore.QPointF(10,10),QtCore.QPointF(10,10),
            QtCore.QPoint(),QtCore.QPoint(0,120),QtCore.Qt.NoButton,QtCore.Qt.NoModifier,
            QtCore.Qt.NoScrollPhase,False)
        QtWidgets.QApplication.sendEvent(window,event)

    def slow_import(self, entered, *args, **kwargs):
        entered.set()
        end=time.monotonic()+5
        while not kwargs['cancelled']() and time.monotonic()<end:time.sleep(.003)
        return dict(cancelled=True,errors=[])

    def test_automatic_history_ignores_input_yields_for_manual_worker_and_requeues_after_retirement(self):
        w,clock,root=self.window();c=w.history_refresh;entered=threading.Event()
        manual=w.tasks.start('Manual worker before history')
        with patch('analysis_imports_ui.import_folders',side_effect=lambda *a,**kw:self.slow_import(entered,*a,**kw)):
            c.start(automatic=True);w.background_queue.pump()
            self.assertFalse(c.busy);self.assertIn('history',w.background_queue.pending)
            w.tasks.finish(manual,'Completed');w.background_queue.pump();self.wait(entered.is_set)
            old=c.thread
            self.wheel(w);w.background_queue.pump();self.assertFalse(c.stop.is_set())
            manual=w.tasks.start('Manual worker after history');w.background_queue.pump()
            self.assertTrue(c.stop.is_set());self.assertIs(c.thread,old)
            self.assertEqual(w.background_queue.active['key'],'history')
            self.wait(lambda:not c.busy)
        self.assertFalse(c.watch.paused);self.assertIn('history',w.background_queue.pending)
        self.assertIsNone(w.background_queue.active)
        self.assertIn('yielded safely',c.text)
        c.cancel();w.background_queue.pump()
        self.assertNotIn('history',w.background_queue.pending)
        self.assertTrue(c.watch.paused)

    def test_explicit_history_refresh_replaces_queued_auto_and_waits_for_manual_worker(self):
        w,clock,root=self.window();c=w.history_refresh
        # The mocked importer below must leave the real empty catalog that a
        # successful import creates, so downstream evidence discovery can run.
        from contextlib import closing
        from learning_db import _connect
        from analysis_imports import ensure_tables
        with closing(_connect(c.db_path)) as conn:
            ensure_tables(conn);conn.commit()
        manual=w.tasks.start('Manual worker')
        c.start(automatic=True)
        self.assertIn('history',w.background_queue.pending);self.assertFalse(c.busy)
        with patch('analysis_imports_ui.import_folders',return_value=dict(errors=[],cancelled=False)), \
             patch('opponent_history.sync_saved',return_value={'contests':[],'errors':[]}):
            c.start(automatic=False);self.assertFalse(c.busy)
            self.assertFalse(w.background_queue.pending['history']['idle_only'])
            w.tasks.finish(manual,'Completed');w.background_queue.pump();self.assertTrue(c.busy)
            self.assertFalse(w.background_queue.active['idle_only'])
            self.assertFalse(c._automatic)
            self.wait(lambda:not c.busy)
        self.assertFalse(c.watch.paused,c.text)
        self.assertNotIn('need retry',c.text)
        with closing(_connect(c.db_path)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM calibration_capture_evidence').fetchone()[0],0)

    def test_user_cancel_after_automatic_yield_prevents_retry(self):
        w,clock,root=self.window();c=w.history_refresh;clock[0]=61;entered=threading.Event()
        with patch('analysis_imports_ui.import_folders',side_effect=lambda *a,**kw:self.slow_import(entered,*a,**kw)):
            c.start(automatic=True);self.wait(entered.is_set)
            manual=w.tasks.start('Manual work');w.background_queue.pump()
            self.assertTrue(c.stop.is_set());c.cancel()
            self.wait(lambda:not c.busy)
        self.assertNotIn('history',w.background_queue.pending);self.assertTrue(c.watch.paused)
        self.assertIn('cancelled',c.text.lower())

    def test_csv_foreground_waits_for_real_history_retirement_before_source_read(self):
        w,clock,root=self.window();c=w.history_refresh;clock[0]=61;entered=threading.Event()
        w.csv_load=CsvLoadController(w);reads=[]
        def read(path):
            reads.append((c.busy,w.background_queue.active))
            raise ValueError('synthetic failed read')
        with patch('analysis_imports_ui.import_folders',side_effect=lambda *a,**kw:self.slow_import(entered,*a,**kw)), \
             patch('csv_loading.read_players_csv',side_effect=read), \
             patch('csv_loading.QtWidgets.QMessageBox.critical'):
            c.start(automatic=True);self.wait(entered.is_set)
            w.idle_backup.last_input=clock[0]  # Choosing a CSV is real user activity.
            w.csv_load.start('synthetic.csv');self.assertTrue(c.stop.is_set())
            self.assertEqual(reads,[])
            self.wait(lambda:not w.csv_load.busy)
            self.assertEqual(reads,[(False,None)])
            # History may already have resumed; worker completion, not inactivity, gates it.
            self.wait(lambda:c.busy or 'history' in w.background_queue.pending)
            c.cancel();self.wait(lambda:not c.busy)
        self.assertTrue(c.watch.paused)

    def test_automatic_backup_ignores_wheel_yields_for_manual_worker_and_preserves_previous_manifest(self):
        w,clock,root=self.window();source=root/'history';source.mkdir();dest=root/'backup';dest.mkdir()
        c=w.idle_backup;c.source=source;c.settings.setValue('backup/destination',str(dest))
        c.settings.setValue('backup/last_manifest','previous-complete')
        clock[0]=61;c.last_change=0;entered=threading.Event()
        from history_backup import BackupCancelled
        def slow(*args,**kwargs):
            entered.set()
            end=time.monotonic()+5
            while not kwargs['cancelled']() and time.monotonic()<end:time.sleep(.003)
            raise BackupCancelled()
        with patch('history_backup.backup_history',side_effect=slow):
            c.tick();self.wait(entered.is_set)
            self.wheel(w);w.background_queue.pump();self.assertFalse(c.worker.stop.is_set())
            manual=w.tasks.start('Manual worker');w.background_queue.pump()
            self.assertTrue(c.worker.stop.is_set());self.assertIsNotNone(c.thread)
            self.wait(lambda:c.thread is None)
        self.assertFalse(c.paused);self.assertTrue(c.dirty)
        self.assertEqual(c.settings.value('backup/last_manifest'),'previous-complete')
        self.assertEqual(w.history_refresh._reports,0)

    def test_manual_backup_does_not_yield_just_for_mouse_input(self):
        w,clock,root=self.window();source=root/'history';source.mkdir();dest=root/'backup';dest.mkdir()
        c=w.idle_backup;c.source=source;c.settings.setValue('backup/destination',str(dest))
        entered=threading.Event()
        from history_backup import BackupCancelled
        def slow(*args,**kwargs):
            entered.set()
            end=time.monotonic()+5
            while not kwargs['cancelled']() and time.monotonic()<end:time.sleep(.003)
            raise BackupCancelled()
        with patch('history_backup.backup_history',side_effect=slow):
            c.request();self.wait(entered.is_set)
            self.wheel(w);w.background_queue.pump()
            self.assertFalse(c.worker.stop.is_set());self.assertFalse(w.background_queue.active['idle_only'])
            c.cancel();self.wait(lambda:c.thread is None)

    def test_running_automatic_backup_does_not_treat_its_own_lease_as_contention(self):
        w,clock,root=self.window();source=root/'history';source.mkdir();dest=root/'backup';dest.mkdir()
        c=w.idle_backup;c.source=source;c.settings.setValue('backup/destination',str(dest))
        clock[0]=61;c.last_change=0;entered=threading.Event()
        from history_backup import BackupCancelled
        def slow(*args,**kwargs):
            entered.set()
            end=time.monotonic()+5
            while not kwargs['cancelled']() and time.monotonic()<end:time.sleep(.003)
            raise BackupCancelled()
        with patch('history_backup.backup_history',side_effect=slow):
            c.tick();self.wait(entered.is_set)
            w.background_queue.pump();w.background_queue.pump()
            self.assertFalse(c.worker.stop.is_set())
            self.assertTrue(w.background_queue.active['idle_only'])
            c.cancel();self.wait(lambda:c.thread is None)

    def test_explicit_backup_promotes_queued_auto_request_without_later_idle_yield(self):
        w,clock,root=self.window();source=root/'history';source.mkdir();dest=root/'backup';dest.mkdir()
        c=w.idle_backup;c.source=source;c.settings.setValue('backup/destination',str(dest))
        clock[0]=61;c.last_change=0;entered=threading.Event()
        from history_backup import BackupCancelled
        def slow(*args,**kwargs):
            entered.set()
            end=time.monotonic()+5
            while not kwargs['cancelled']() and time.monotonic()<end:time.sleep(.003)
            raise BackupCancelled()
        foreground=w.tasks.start('Synthetic foreground')
        with patch('history_backup.backup_history',side_effect=slow):
            c.tick();self.assertTrue(w.background_queue.pending['backup']['idle_only'])
            c.request();self.assertFalse(w.background_queue.pending['backup']['idle_only'])
            w.tasks.finish(foreground,'Completed');w.background_queue.pump();self.wait(entered.is_set)
            self.wheel(w);w.background_queue.pump()
            self.assertFalse(c.worker.stop.is_set())
            c.cancel();self.wait(lambda:c.thread is None)
