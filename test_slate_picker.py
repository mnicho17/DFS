from test_environment import install
install()
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from PyQt5 import QtCore, QtWidgets
from slate_picker import SlateDiscoveryController, SlatePicker
from slate_catalog import scan


class SlatePickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        from task_dashboard import TaskHub
        from background_queue import BackgroundQueue
        from types import SimpleNamespace
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.owner=QtWidgets.QWidget()
        self.owner.tasks=TaskHub(self.owner)
        self.owner.app_settings=QtCore.QSettings(str(Path(self.tmp.name)/'settings.ini'),QtCore.QSettings.IniFormat)
        self.owner.background_queue=BackgroundQueue(self.owner)
        self.owner.background_queue.timer.stop()
        self.owner.idle_preparation=SimpleNamespace(ready=lambda:False)
        self.owner.players=[{'Name':'Current slate'}]
        self.controller=SlateDiscoveryController(self.owner,Path(self.tmp.name)/'catalog.sqlite')
        self.controller.timer.stop()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.controller.shutdown();self.owner.background_queue.shutdown()
        end=time.monotonic()+3
        while self.controller.thread and time.monotonic()<end:
            self.app.processEvents();time.sleep(.002)
        self.owner.deleteLater();self.app.processEvents()

    def wait(self,predicate):
        end=time.monotonic()+5
        while not predicate() and time.monotonic()<end:
            self.app.processEvents();time.sleep(.002)
        self.assertTrue(predicate())

    def test_picker_browse_without_configured_folder(self):
        dialog=SlatePicker(self.controller,self.owner)
        self.addCleanup(dialog.deleteLater)
        self.assertIn('Browse\u2026',[button.text() for button in dialog.findChildren(QtWidgets.QPushButton)])
        with patch('slate_picker.QtWidgets.QFileDialog.getOpenFileName',return_value=('chosen.csv','')):
            dialog.browse()
        self.assertEqual(dialog.path,'chosen.csv')
        self.assertEqual(dialog.result(),QtWidgets.QDialog.Accepted)

    def test_discovery_waits_for_idle_manual_refresh_runs_detached_and_preserves_slate(self):
        folder=Path(self.tmp.name)/'salaries';folder.mkdir()
        self.owner.app_settings.setValue('learning/salary_folder',str(folder))
        source=self.owner.players;threads=[];entered=threading.Event();release=threading.Event()
        self.addCleanup(release.set)
        def work(*args):
            threads.append(QtCore.QThread.currentThread()==self.app.thread())
            entered.set();release.wait(3)
            return dict(checked=0,reused=0)
        with patch('slate_picker.scan',side_effect=work):
            self.controller.request();self.owner.background_queue.pump()
            self.assertIsNone(self.controller.thread)
            self.controller.request(manual=True);self.owner.background_queue.pump()
            self.wait(entered.is_set)
            self.assertIsNotNone(self.owner.background_queue.active)
            release.set();self.wait(lambda:self.controller.thread is None)
        self.assertEqual(threads,[False])
        self.assertIs(self.owner.players,source)
        self.assertIsNone(self.owner.background_queue.active)

    def test_close_keeps_worker_owned_until_actual_retirement(self):
        folder=Path(self.tmp.name)/'salaries';folder.mkdir()
        self.owner.app_settings.setValue('learning/salary_folder',str(folder))
        entered=threading.Event();release=threading.Event()
        self.addCleanup(release.set)
        def work(*args):
            entered.set();release.wait(3)
            return dict(cancelled=True)
        with patch('slate_picker.scan',side_effect=work):
            self.controller.request(manual=True);self.owner.background_queue.pump();self.wait(entered.is_set)
            self.assertTrue(self.controller.shutdown())
            self.assertIsNotNone(self.controller.thread)
            release.set();self.wait(lambda:self.controller.thread is None)
        self.assertTrue(self.controller.closed)


    def catalog_row(self):
        from slate_catalog import rows,record_preparation
        folder=Path(self.tmp.name)/'salaries';folder.mkdir(exist_ok=True)
        self.owner.app_settings.setValue('learning/salary_folder',str(folder))
        path=folder/'slate.csv'
        path.write_text('Position,Name,ID,Roster Position,Salary,Game Info,TeamAbbrev\n'
            'WR,Example,123,FLEX,5000,SEA@DEN 12/20/2099 08:15PM ET,SEA\n'
            'WR,Example,124,CPT,7500,SEA@DEN 12/20/2099 08:15PM ET,SEA\n')
        scan(self.controller.database,folder)
        row=rows(self.controller.database,folder)[0]
        record_preparation(self.controller.database,dict(digest=row['digest'],state='Needs attention',
            count=3500,target=12000,seconds=1800,ownership_complete=True,pause_code='time_budget',
            pause_reason='30-minute attempt allowance reached.',library='saved.dfslib',updated_at='2099-12-19T10:00:00'))
        return rows(self.controller.database,folder)[0]

    def test_details_show_selected_progress_reason_time_and_saved_paths(self):
        row=self.catalog_row();dialog=SlatePicker(self.controller,self.owner)
        self.addCleanup(dialog.deleteLater)
        dialog.table.selectRow(0)
        text=dialog.details.toPlainText()
        for expected in ('3,500 / 12,000','30.0 of 30','30-minute attempt allowance','saved.dfslib',row['path'],'Captured for preparation'):
            self.assertIn(expected,text)
        self.assertTrue(dialog.resume_button.isEnabled())
        dialog.render()
        self.assertEqual(dialog.selected()['digest'],row['digest'])
        dialog.search.setText('no matching slate')
        self.assertFalse(dialog.resume_button.isEnabled())
        self.assertIn('Select a slate',dialog.details.toPlainText())

    def test_details_show_checkpoint_reuse_timings_and_refresh_reason(self):
        from slate_picker import preparation_details
        row=self.catalog_row()
        row['preparation'].update(checkpoint_reused=True,
            phase_timings=dict(ownership=1.5,candidate_search=60),
            ownership_reuse=dict(status='refreshed',reason='No matching completed estimate.'))
        text=preparation_details(row,self.controller)
        self.assertIn('checkpoint reused',text)
        self.assertIn('Candidate search (accumulated): 60.00 s',text)
        self.assertIn('Refreshed: No matching completed estimate.',text)
        self.assertIn('30-minute attempt allowance',text)

    def test_changed_source_identity_clears_selection_before_retry(self):
        row=self.catalog_row();dialog=SlatePicker(self.controller,self.owner)
        self.addCleanup(dialog.deleteLater);dialog.table.selectRow(0)
        path=Path(row['path']);path.write_text(path.read_text()+'\n')
        scan(self.controller.database,self.controller.folder());dialog.render()
        self.assertIsNone(dialog.selected())
        self.assertFalse(dialog.resume_button.isEnabled())
        self.assertIsNone(self.controller.retry_request)

    def test_resume_queues_only_selected_slate_without_enabling_global_preparation(self):
        row=self.catalog_row();self.owner.app_settings.setValue('build/smart_preparation',False)
        self.controller.paused=True
        dialog=SlatePicker(self.controller,self.owner)
        self.addCleanup(dialog.deleteLater);dialog.table.selectRow(0)
        original=self.owner.players
        dialog.resume_selected()
        self.assertEqual(self.controller.retry_request['digest'],row['digest'])
        self.assertTrue(self.controller.retry_request['reset'])
        self.assertFalse(self.controller.manual)
        self.assertFalse(self.owner.app_settings.value('build/smart_preparation',type=bool))
        self.owner.background_queue.pump()
        self.assertIsNone(self.controller.thread)
        self.assertFalse(dialog.resume_button.isEnabled())
        self.assertIn('close picker',dialog.details.toPlainText())
        self.assertIs(self.owner.players,original)

    def test_active_worker_prevents_duplicate_resume_until_retirement(self):
        row=self.catalog_row();entered=threading.Event();release=threading.Event()
        self.addCleanup(release.set)
        def work(*args):entered.set();release.wait(3);return dict(checked=0,reused=0)
        with patch('slate_picker.scan',side_effect=work):
            self.controller.request(manual=True);self.owner.background_queue.pump();self.wait(entered.is_set)
            dialog=SlatePicker(self.controller,self.owner);self.addCleanup(dialog.deleteLater)
            dialog.table.selectRow(0)
            self.assertFalse(dialog.resume_button.isEnabled())
            self.assertFalse(self.controller.resume(row))
            release.set();self.wait(lambda:self.controller.thread is None)
        self.assertTrue(dialog.resume_button.isEnabled())

    def test_targeted_retry_survives_yield_before_reset_is_consumed(self):
        row=self.catalog_row();self.owner.app_settings.setValue('build/smart_preparation',False)
        self.owner.idle_preparation.ready=lambda:True
        calls=[]
        def prepare(*args,**kwargs):
            calls.append(kwargs)
            if len(calls)==1:return dict(cancelled=True)
            if len(calls)==2:return dict(state='Paused',cancelled=True,retry_consumed=False,digest=row['digest'])
            return dict(state='Needs attention',digest=row['digest'],preparation='Test stop')
        with patch('smart_preparation.prepare_one',side_effect=prepare):
            self.assertTrue(self.controller.resume(row))
            self.wait(lambda:len(calls)>=3 and self.controller.thread is None)
        self.assertEqual([c['retry'] for c in calls],[True,True,True])
        self.assertTrue(all(c['target_digest']==row['digest'] for c in calls))
        self.assertIsNone(self.controller.retry_request)

    def test_targeted_retry_consumes_reset_once_and_stop_does_not_requeue(self):
        row=self.catalog_row();self.owner.app_settings.setValue('build/smart_preparation',False)
        self.owner.idle_preparation.ready=lambda:True
        calls=[];entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        def prepare(*args,**kwargs):
            calls.append(kwargs)
            if len(calls)==1:return dict(state='Queued',retry_consumed=True,digest=row['digest'])
            entered.set();release.wait(3)
            return dict(state='Paused',cancelled=True,retry_consumed=False,digest=row['digest'])
        with patch('smart_preparation.prepare_one',side_effect=prepare):
            self.controller.resume(row);self.wait(entered.is_set)
            self.assertEqual([c['retry'] for c in calls],[True,False])
            self.controller.cancel()
            self.assertIsNotNone(self.controller.thread)
            release.set();self.wait(lambda:self.controller.thread is None)
            self.app.processEvents()
        self.assertTrue(self.controller.paused)
        self.assertIsNone(self.controller.retry_request)
        self.assertNotIn('slate-discovery',self.owner.background_queue.pending)


if __name__=='__main__':unittest.main()
