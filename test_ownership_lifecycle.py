from test_environment import install
install()
import copy
import random
import threading
import time
import unittest
from unittest.mock import patch
from PyQt5 import QtCore, QtWidgets
from main_window import MainWindow, OwnershipSimWorker, _pkey
from test_showdown_performance import _showdown_players
from test_nfl_logic import _fixture_players


class OwnershipLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def window(self):
        w=MainWindow();w.background_queue.timer.stop();w.idle_backup.timer.stop()
        self.app.processEvents()
        w.players=_showdown_players();w.last_sim_report={'preserved':True}
        w.last_showdown=[['previous output']]
        for p in w.players:p['ProjOwnPct']=17.0
        self.addCleanup(w.close)
        return w

    def wait(self,predicate,seconds=8):
        end=time.monotonic()+seconds
        while not predicate() and time.monotonic()<end:
            self.app.processEvents();time.sleep(.003)
        self.assertTrue(predicate())

    def result(self, ps):
        return {'total':{_pkey(p):20.0 for p in ps},
                'cpt':{_pkey(p):5.0 for p in ps},'flex':{_pkey(p):15.0 for p in ps}}

    def slow(self,entered,release,*,error=False,threads=None):
        def simulate(worker):
            if threads is not None:threads.append(QtCore.QThread.currentThread()==self.app.thread())
            entered.set()
            if not release.wait(5):raise AssertionError('Test source was not released')
            if error:raise ValueError('synthetic ownership failure')
            return self.result(worker.players)
        return simulate

    def launch(self,w):
        w._start_ownership_sim(num_sims=40,mode=w._contest_mode(),cap=50000,sport=w._current_sport())
        return w._ownership_run

    def test_success_applies_once_on_gui_only_after_thread_retirement(self):
        w=self.window();entered=threading.Event();release=threading.Event();threads=[];self.addCleanup(release.set)
        calls=[];original=w._on_own_sim_finished
        def apply(result):
            calls.append((QtCore.QThread.currentThread()==self.app.thread(),not w._own_thread.isRunning()))
            original(result)
        with patch.object(OwnershipSimWorker,'_simulate',self.slow(entered,release,threads=threads)), \
             patch.object(w,'_on_own_sim_finished',side_effect=apply):
            run=self.launch(w);self.wait(entered.is_set)
            self.assertTrue(w._snapshot_busy())
            self.assertEqual(w.players[0]['ProjOwnPct'],17)
            release.set();self.wait(lambda:w._ownership_run is None)
        self.assertEqual(threads,[False]);self.assertEqual(calls,[(True,True)])
        self.assertEqual(w.players[0]['ProjOwnPct'],20)
        self.assertEqual(w.players[0]['OwnershipSource'],'Lineup simulation')
        self.assertEqual(w.last_sim_report,{'preserved':True});self.assertEqual(w.last_showdown,[['previous output']])
        self.assertEqual(w.tasks.rows[run.task]['state'],'Completed')
        self.assertIsNone(w._own_thread);self.assertIsNone(w._own_worker)

    def test_duplicate_launch_keeps_original_worker_thread_and_single_task(self):
        w=self.window();entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        with patch.object(OwnershipSimWorker,'_simulate',self.slow(entered,release)):
            run=self.launch(w);self.wait(entered.is_set);thread=w._own_thread;worker=w._own_worker
            self.assertIs(self.launch(w),run)
            self.assertIs(w._own_thread,thread);self.assertIs(w._own_worker,worker)
            self.assertEqual(sum(row['title']=='Ownership estimation' and row['ended'] is None for row in w.tasks.rows.values()),1)
            release.set();self.wait(lambda:w._ownership_run is None)

    def test_replaced_slate_with_same_ids_discards_old_result(self):
        w=self.window();entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        with patch.object(OwnershipSimWorker,'_simulate',self.slow(entered,release)), \
             patch.object(w,'_on_own_sim_finished') as apply:
            run=self.launch(w);self.wait(entered.is_set)
            w.players=copy.deepcopy(w.players);before=copy.deepcopy(w.players)
            release.set();self.wait(lambda:w._ownership_run is None)
        apply.assert_not_called();self.assertEqual(w.players,before)
        self.assertEqual(w.tasks.rows[run.task]['state'],'Discarded')

    def test_inplace_input_changes_discard_without_overwriting_user_edits(self):
        for field,value in [('FlexProjection',99.0),('LockFlex',True),('FadeCpt',True),
                            ('UnknownMetadata',{'nested':[1,2]})]:
            with self.subTest(field=field):
                w=self.window();entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
                with patch.object(OwnershipSimWorker,'_simulate',self.slow(entered,release)), \
                     patch.object(w,'_on_own_sim_finished') as apply:
                    run=self.launch(w);self.wait(entered.is_set)
                    w.players[0][field]=value;before=copy.deepcopy(w.players)
                    release.set();self.wait(lambda:w._ownership_run is None)
                apply.assert_not_called();self.assertEqual(w.players,before)
                self.assertEqual(w.tasks.rows[run.task]['state'],'Discarded')

    def test_changed_mode_sport_cap_or_template_discards_captured_result(self):
        for change in ('mode','sport','cap','template'):
            with self.subTest(change=change):
                w=self.window();entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
                with patch.object(OwnershipSimWorker,'_simulate',self.slow(entered,release)), \
                     patch.object(w,'_on_own_sim_finished') as apply:
                    run=self.launch(w);self.wait(entered.is_set)
                    if change=='mode':w.tabs_lineups.setCurrentIndex(1)
                    elif change=='sport':w.combo_sport.setCurrentText('NBA')
                    elif change=='cap':w.edit_sd_cap.setText('49000')
                    else:w.chk_sd_template_sim.setChecked(not w.chk_sd_template_sim.isChecked())
                    release.set();self.wait(lambda:w._ownership_run is None)
                apply.assert_not_called();self.assertEqual(w.tasks.rows[run.task]['state'],'Discarded')

    def test_stale_progress_does_not_update_new_slate_progress_ui(self):
        w=self.window();entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        with patch.object(OwnershipSimWorker,'_simulate',self.slow(entered,release)):
            run=self.launch(w);self.wait(entered.is_set);w.players=copy.deepcopy(w.players)
            text=w._own_eta.text();value=w._own_progress.value()
            run.progress(39,40,'stale progress')
            self.assertEqual(w._own_eta.text(),text);self.assertEqual(w._own_progress.value(),value)
            release.set();self.wait(lambda:w._ownership_run is None)

    def test_task_cancel_retains_estimates_and_waits_for_worker_to_exit(self):
        w=self.window();entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        before=copy.deepcopy(w.players)
        with patch.object(OwnershipSimWorker,'_simulate',self.slow(entered,release)), \
             patch.object(w,'_on_own_sim_finished') as apply:
            run=self.launch(w);self.wait(entered.is_set)
            w.tasks.cancel(run.task);w.tasks.cancel(run.task)
            self.assertTrue(w._own_worker._cancel);self.assertIs(w._ownership_run,run)
            self.assertIsNone(w.tasks.rows[run.task]['ended'])
            release.set();self.wait(lambda:w._ownership_run is None)
        apply.assert_not_called();self.assertEqual(w.players,before)
        self.assertEqual(w.tasks.rows[run.task]['state'],'Cancelled')

    def test_cancel_after_result_is_queued_still_suppresses_application(self):
        w=self.window();entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        with patch.object(OwnershipSimWorker,'_simulate',self.slow(entered,release)), \
             patch.object(w,'_on_own_sim_finished') as apply:
            run=self.launch(w);self.wait(entered.is_set)
            w._own_worker.finished.connect(lambda result:run.cancel())
            release.set();self.wait(lambda:w._ownership_run is None)
        apply.assert_not_called();self.assertEqual(w.tasks.rows[run.task]['state'],'Cancelled')

    def test_current_error_retires_and_allows_retry(self):
        w=self.window();entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        with patch.object(OwnershipSimWorker,'_simulate',self.slow(entered,release,error=True)), \
             patch('main_window.QtWidgets.QMessageBox.warning') as warning:
            run=self.launch(w);self.wait(entered.is_set);release.set();self.wait(lambda:w._ownership_run is None)
        warning.assert_called_once();self.assertEqual(w.tasks.rows[run.task]['state'],'Failed')
        with patch.object(OwnershipSimWorker,'_simulate',return_value=self.result(w.players)):
            retry=self.launch(w);self.wait(lambda:w._ownership_run is None)
        self.assertEqual(w.tasks.rows[retry.task]['state'],'Completed')

    def test_stale_error_is_discarded_without_popup(self):
        w=self.window();entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        with patch.object(OwnershipSimWorker,'_simulate',self.slow(entered,release,error=True)), \
             patch('main_window.QtWidgets.QMessageBox.warning') as warning:
            run=self.launch(w);self.wait(entered.is_set);w.players=copy.deepcopy(w.players)
            release.set();self.wait(lambda:w._ownership_run is None)
        warning.assert_not_called();self.assertEqual(w.tasks.rows[run.task]['state'],'Discarded')

    def test_cancelled_error_is_not_shown(self):
        w=self.window();entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        with patch.object(OwnershipSimWorker,'_simulate',self.slow(entered,release,error=True)), \
             patch('main_window.QtWidgets.QMessageBox.warning') as warning:
            run=self.launch(w);self.wait(entered.is_set);run.cancel()
            release.set();self.wait(lambda:w._ownership_run is None)
        warning.assert_not_called();self.assertEqual(w.tasks.rows[run.task]['state'],'Cancelled')

    def test_close_waits_for_retirement_and_prevents_new_ownership_jobs(self):
        w=self.window();w.show();entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        with patch.object(OwnershipSimWorker,'_simulate',self.slow(entered,release)), \
             patch.object(w,'_on_own_sim_finished') as apply:
            run=self.launch(w);self.wait(entered.is_set)
            w.close();self.assertTrue(w.isVisible());self.assertTrue(w._own_worker._cancel)
            self.assertIs(self.launch(w),run)
            release.set();self.wait(lambda:w._ownership_run is None);self.wait(lambda:not w.isVisible())
        apply.assert_not_called();self.assertEqual(w.tasks.rows[run.task]['state'],'Cancelled')

    def test_worker_cancellation_before_start_emits_settlement_without_simulation(self):
        worker=OwnershipSimWorker(_showdown_players(),mode='showdown',num_sims=2,salary_cap=50000)
        results=[];errors=[];settled=[]
        worker.finished.connect(results.append);worker.error.connect(errors.append);worker.settled.connect(lambda:settled.append(True))
        worker.cancel()
        with patch.object(worker,'_simulate') as simulate:worker.run()
        simulate.assert_not_called();self.assertEqual(results,[]);self.assertEqual(errors,[]);self.assertEqual(settled,[True])

    def test_detached_inputs_include_nested_data_for_every_sport(self):
        for sport in ('NFL','MLB','NBA','WNBA'):
            with self.subTest(sport=sport):
                ps=_showdown_players();ps[0]['Nested']={'values':[1]}
                before=copy.deepcopy(ps)
                worker=OwnershipSimWorker(ps,mode='classic',num_sims=2,salary_cap=50000,sport=sport)
                ps[0]['Nested']['values'].append(2);ps[0]['FlexProjection']=99
                self.assertEqual(worker.players[0]['Nested'],{'values':[1]})
                self.assertEqual(worker.players[0]['FlexProjection'],before[0]['FlexProjection'])

    def test_real_nfl_classic_and_showdown_results_match_direct_estimator_execution(self):
        for mode,ps in [('classic',_fixture_players()),('showdown',_showdown_players())]:
            with self.subTest(mode=mode):
                w=self.window();w.players=ps;w.tabs_lineups.setCurrentIndex(0 if mode=='showdown' else 1)
                direct=OwnershipSimWorker(w.players,mode=mode,num_sims=40,salary_cap=50000)
                random.seed(31);expected=direct._simulate()
                applied=[];original=w._on_own_sim_finished
                def apply(result):applied.append(copy.deepcopy(result));original(result)
                random.seed(31)
                with patch.object(w,'_on_own_sim_finished',side_effect=apply):
                    self.launch(w);self.wait(lambda:w._ownership_run is None,seconds=15)
                self.assertEqual(applied,[expected])
                self.assertEqual(w.players[0]['ProjOwnPct'],float(expected['total'].get(_pkey(w.players[0]),0)))

    def test_result_queued_before_retirement_still_blocks_another_launch(self):
        w=self.window();entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        attempts=[]
        with patch.object(OwnershipSimWorker,'_simulate',self.slow(entered,release)):
            run=self.launch(w);self.wait(entered.is_set)
            w._own_worker.finished.connect(lambda result:attempts.append(self.launch(w)))
            release.set();self.wait(lambda:w._ownership_run is None)
        self.assertEqual(attempts,[run])
        self.assertEqual(sum(row['title']=='Ownership estimation' for row in w.tasks.rows.values()),1)

    def test_late_old_run_cleanup_cannot_clear_newer_worker_references_or_ui(self):
        w=self.window();entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        marker=object();new_thread=QtCore.QThread(w);new_worker=object()
        try:
            with patch.object(OwnershipSimWorker,'_simulate',self.slow(entered,release)), \
                 patch.object(w,'_on_own_sim_finished') as apply:
                run=self.launch(w);self.wait(entered.is_set)
                w._ownership_run=marker;w._own_thread=new_thread;w._own_worker=new_worker
                w._own_progress.setValue(27);w._own_eta.setText('Newer job')
                release.set();self.wait(lambda:run.retired)
                run.progress(1,40,'late old progress');run.retire()
            apply.assert_not_called();self.assertIs(w._ownership_run,marker)
            self.assertIs(w._own_thread,new_thread);self.assertIs(w._own_worker,new_worker)
            self.assertEqual(w._own_progress.value(),27);self.assertEqual(w._own_eta.text(),'Newer job')
            self.assertEqual(w.tasks.rows[run.task]['state'],'Discarded')
        finally:
            w._ownership_run=w._own_thread=w._own_worker=None
            new_thread.deleteLater()
