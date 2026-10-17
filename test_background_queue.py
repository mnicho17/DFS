from test_environment import install
install()
import copy
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from PyQt5 import QtCore, QtWidgets
from background_queue import BackgroundQueue
from task_dashboard import TaskHub
from main_window import MainWindow
from build_snapshots import create_snapshot
from test_showdown_library import players
from showdown_library import status
from showdown_library_ui import PreparationDialog


class BackgroundQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def queue(self):
        owner=QtCore.QObject();owner.tasks=TaskHub(owner)
        queue=BackgroundQueue(owner);queue.timer.stop()
        self.addCleanup(queue.shutdown)
        self.owner=owner
        return queue

    def test_priority_fifo_coalescing_and_visible_queued_state(self):
        q=self.queue();calls=[]
        for key,priority in [('candidate',30),('backup',20),('history',10),('second-history',10)]:
            q.request(key,key,priority,lambda key=key:calls.append(key),None)
        q.request('history','duplicate',10,lambda:calls.append('duplicate'),None)
        self.assertEqual(len(q.pending),4)
        self.assertTrue(all(r['state']=='Queued' for r in q.hub.rows.values()))
        for key in ('history','second-history','backup','candidate'):
            q.pump();self.assertEqual(calls[-1],key);q.release(key)
        self.assertEqual(calls,['history','second-history','backup','candidate'])

    def test_higher_priority_waits_for_explicit_retirement_after_one_stop(self):
        q=self.queue();stop=Mock();started=Mock()
        q.request('backup','backup',20,lambda:True,stop);q.pump()
        q.request('history','history',10,started,None)
        q.pump();q.pump();stop.assert_called_once();started.assert_not_called()
        self.assertEqual(q.active['key'],'backup')
        q.release('backup');q.pump();started.assert_called_once()

    def test_foreground_work_and_idle_readiness_block_and_yield(self):
        q=self.queue();ready=[False];stop=Mock();start=Mock()
        q.request('candidate','candidate',30,start,stop,ready=lambda:ready[0],idle_only=True)
        q.pump();start.assert_not_called();ready[0]=True
        key=q.hub.start('Generate');q.pump();start.assert_not_called()
        q.hub.finish(key,'Completed');q.pump();start.assert_called_once()
        ready[0]=False;q.pump();stop.assert_called_once()
        self.assertIsNotNone(q.active)

    def test_cancel_pending_and_shutdown_never_dispatch_jobs(self):
        q=self.queue();start=Mock();cancel=Mock()
        q.request('one','one',20,start,cancel);q.cancel('one')
        cancel.assert_called_once();q.pump();start.assert_not_called()
        q.request('two','two',20,start,cancel);q.shutdown();q.pump()
        start.assert_not_called();self.assertFalse(q.pending)
        self.assertFalse(q.request('three','three',20,start,cancel))

    def test_failed_start_releases_lease_and_does_not_block_next_job(self):
        q=self.queue()
        def fail():raise ValueError('unavailable')
        q.request('bad','bad',10,fail,None);q.pump()
        self.assertIsNone(q.active);self.assertEqual(q.hub.rows[1]['state'],'Failed')
        start=Mock();q.request('next','next',20,start,None);q.pump();start.assert_called_once()


class IdlePreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def window(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        root=Path(temp.name);w=MainWindow();self.addCleanup(w.close)
        w.background_queue.timer.stop();w.idle_backup.timer.stop()
        w.idle_backup.settings.setValue('backup/destination','')
        clock=[0.0];w.idle_backup.clock=lambda:clock[0];w.idle_backup.last_input=0
        snapshot=create_snapshot(players(),dict(contest_kind='showdown',salary_cap=50000),{})
        return w,clock,root,snapshot

    def enqueue(self,w,root,snapshot,limit=1000):
        return w.idle_preparation.enqueue(root/'idle.sdlib',snapshot,60,limit,None,8*1024**3,False,False)

    def wait(self,predicate,seconds=10):
        end=time.monotonic()+seconds
        while not predicate() and time.monotonic()<end:self.app.processEvents();time.sleep(.005)
        self.assertTrue(predicate())

    def test_opt_in_freezes_inputs_waits_for_idle_and_completes_real_library(self):
        w,clock,root,snapshot=self.window();original=copy.deepcopy(snapshot)
        self.assertTrue(self.enqueue(w,root,snapshot));snapshot['inputs']['players'][0]['FlexSalary']=99999
        w.background_queue.pump();self.assertFalse((root/'idle.sdlib').exists())
        self.assertFalse(self.enqueue(w,root,original))
        clock[0]=61;w.background_queue.pump()
        self.wait(lambda:w.idle_preparation.thread is None)
        saved=status(root/'idle.sdlib');self.assertTrue(saved['complete']);self.assertEqual(saved['saved'],168)
        self.assertEqual(w.idle_preparation.state,'Completed');self.assertIsNone(w.idle_preparation.request_data)
        self.assertEqual(w.history_refresh._reports,0)

    def test_user_cancel_before_start_creates_no_library_or_restart(self):
        w,clock,root,snapshot=self.window();self.enqueue(w,root,snapshot)
        task=w.background_queue.pending['preparation']['task'];w.tasks.cancel(task)
        clock[0]=100;w.background_queue.pump();self.app.processEvents()
        self.assertFalse((root/'idle.sdlib').exists());self.assertIsNone(w.idle_preparation.request_data)
        self.assertNotIn('preparation',w.background_queue.pending)

    def test_resource_limit_stops_without_automatic_retry(self):
        w,clock,root,snapshot=self.window();self.enqueue(w,root,snapshot,limit=4)
        clock[0]=61;w.background_queue.pump();self.wait(lambda:w.idle_preparation.thread is None)
        self.assertFalse(status(root/'idle.sdlib')['complete']);self.assertEqual(status(root/'idle.sdlib')['saved'],4)
        self.assertEqual(w.idle_preparation.state,'Paused');self.assertNotIn('preparation',w.background_queue.pending)
        self.assertIsNone(w.idle_preparation.request_data)

    def test_input_yield_saves_checkpoint_then_resumes_without_duplicates(self):
        w,clock,root,snapshot=self.window();self.enqueue(w,root,snapshot);clock[0]=61
        entered=threading.Event()
        from showdown_library import prepare
        def checkpoint(path,rows,**kwargs):
            result=prepare(path,rows,max_candidates=5)
            entered.set()
            while not kwargs['cancelled']():time.sleep(.002)
            result['cancelled']=True
            return result
        with patch('showdown_library_ui.prepare',side_effect=checkpoint):
            w.background_queue.pump();self.wait(entered.is_set)
            w.idle_backup.last_input=clock[0];w.background_queue.pump()
            self.wait(lambda:w.idle_preparation.thread is None)
            self.app.processEvents()
        self.assertEqual(status(root/'idle.sdlib')['saved'],5)
        self.assertIn('preparation',w.background_queue.pending)
        clock[0]+=61;w.background_queue.pump();self.wait(lambda:w.idle_preparation.thread is None)
        self.assertTrue(status(root/'idle.sdlib')['complete']);self.assertEqual(status(root/'idle.sdlib')['saved'],168)

    def test_shutdown_waits_for_checkpoint_and_never_resumes(self):
        w,clock,root,snapshot=self.window();self.enqueue(w,root,snapshot);clock[0]=61
        entered=threading.Event()
        def slow(*args,**kwargs):
            entered.set()
            while not kwargs['cancelled']():time.sleep(.002)
            return dict(saved=0,checked=0,complete=False,cancelled=True)
        with patch('showdown_library_ui.prepare',side_effect=slow):
            w.background_queue.pump();self.wait(entered.is_set);w.close()
            self.wait(lambda:w.idle_preparation.thread is None)
        self.assertTrue(w.background_queue.closed);self.assertFalse(w.background_queue.pending)
        self.assertEqual(w.history_refresh._reports,0)

    def test_dialog_queue_captures_limits_and_returns_without_running(self):
        w,clock,root,snapshot=self.window();d=PreparationDialog(snapshot,w);self.addCleanup(d.close)
        d.path.setText(str(root/'queued.sdlib'));d.limit.setCurrentIndex(1);d.screen.setChecked(True)
        d.full_screen.setChecked(True);d.queue_idle()
        data=w.idle_preparation.request_data
        self.assertEqual(data['limit'],1000000);self.assertTrue(data['screen']);self.assertTrue(data['full_screen'])
        self.assertIsNone(d.thread);self.assertIsNone(w.idle_preparation.thread)

    def test_ready_history_preempts_real_backup_before_starting_refresh(self):
        w,clock,root,snapshot=self.window();source=root/'history';dest=root/'backups';source.mkdir();dest.mkdir()
        c=w.idle_backup;c.source=source;c.settings.setValue('backup/destination',str(dest))
        from history_backup import BackupCancelled
        entered=threading.Event()
        def slow(*args,**kwargs):
            entered.set()
            while not kwargs['cancelled']():time.sleep(.002)
            raise BackupCancelled()
        sources=root/'sources';sources.mkdir();w.history_refresh.settings.setValue('learning/results_folder',str(sources))
        with patch('history_backup.backup_history',side_effect=slow):
            c.request();self.wait(entered.is_set)
            w.history_refresh.watch.schedule();w.history_refresh.watch.dispatch()
            self.assertTrue(c.worker.stop.is_set());self.assertFalse(w.history_refresh.busy)
            self.wait(lambda:c.thread is None)
            self.wait(lambda:any(r['title']=='Results and salary history refresh' and r['state']=='Completed' for r in w.tasks.rows.values()))
        self.assertFalse(w.history_refresh.busy);self.assertEqual(w.history_refresh._reports,0)

    def test_backup_preempts_candidate_and_candidate_requeues_after_retirement(self):
        w,clock,root,snapshot=self.window();self.enqueue(w,root,snapshot);clock[0]=61
        source=root/'history';dest=root/'backups';source.mkdir();dest.mkdir();(source/'note.txt').write_text('saved')
        c=w.idle_backup;c.source=source;c.settings.setValue('backup/destination',str(dest))
        entered=threading.Event()
        def slow(*args,**kwargs):
            entered.set()
            while not kwargs['cancelled']():time.sleep(.002)
            return dict(saved=0,checked=0,complete=False,cancelled=True)
        with patch('showdown_library_ui.prepare',side_effect=slow):
            w.background_queue.pump();self.wait(entered.is_set)
            c.request();self.assertTrue(w.idle_preparation.worker.stop.is_set());self.assertIsNone(c.thread)
            # Block candidate resume until the patched slow worker is removed.
            w.idle_backup.last_input=clock[0]
            self.wait(lambda:c.settings.value('backup/last_manifest',''))
            self.wait(lambda:c.thread is None)
        self.assertIn('preparation',w.background_queue.pending)
        clock[0]+=61;w.background_queue.pump();self.wait(lambda:w.idle_preparation.thread is None)
        self.assertTrue(status(root/'idle.sdlib')['complete'])

    def test_cancel_queued_backup_does_not_restart_when_foreground_retires(self):
        w,clock,root,snapshot=self.window();dest=root/'backup';dest.mkdir()
        w.idle_backup.settings.setValue('backup/destination',str(dest))
        key=w.tasks.start('Foreground');w.idle_backup.request()
        task=w.background_queue.pending['backup']['task'];w.tasks.cancel(task)
        w.tasks.finish(key,'Completed');clock[0]=100;w.background_queue.pump();w.idle_backup.tick()
        self.assertIsNone(w.idle_backup.thread);self.assertTrue(w.idle_backup.paused)
        self.assertNotIn('backup',w.background_queue.pending)

    def test_cancelling_queued_history_pauses_watcher_without_import(self):
        w,clock,root,snapshot=self.window();source=root/'sources';source.mkdir()
        w.history_refresh.settings.setValue('learning/results_folder',str(source))
        key=w.tasks.start('Foreground');w.history_refresh.watch.schedule();w.history_refresh.watch.dispatch()
        self.assertTrue(w.history_refresh.queued)
        self.assertIn('queued',w.history_refresh.status_line())
        from preparation_status_ui import preparation_rows
        rows={r['key']:r for r in preparation_rows(w)}
        self.assertEqual(rows['history']['state'],'Refresh queued')
        task=w.background_queue.pending['history']['task'];w.tasks.cancel(task)
        w.tasks.finish(key,'Completed');w.background_queue.pump()
        self.assertFalse(w.history_refresh.busy);self.assertTrue(w.history_refresh.watch.paused)
        self.assertNotIn('history',w.background_queue.pending)

    def test_queued_lower_priority_job_does_not_interrupt_backup(self):
        w,clock,root,snapshot=self.window();source=root/'history';dest=root/'backup';source.mkdir();dest.mkdir()
        c=w.idle_backup;c.source=source;c.settings.setValue('backup/destination',str(dest))
        from history_backup import BackupCancelled
        entered=threading.Event()
        def slow(*args,**kwargs):
            entered.set()
            while not kwargs['cancelled']():time.sleep(.002)
            raise BackupCancelled()
        with patch('history_backup.backup_history',side_effect=slow):
            c.request();self.wait(entered.is_set)
            self.enqueue(w,root,snapshot);self.app.processEvents()
            self.assertFalse(c.worker.stop.is_set())
            w.idle_preparation.cancel();c.cancel();self.wait(lambda:c.thread is None)
