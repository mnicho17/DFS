"""Real Qt delivery/retirement and real bounded CBC process cancellation."""
from test_environment import install
install()
import copy
import os
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PyQt5 import QtCore,QtWidgets,QtTest
import bounded_solver
import hindsight_ui as ui
import historical_identity as hi
from test_hindsight_evidence import SourceFixture,standings_entries,EXPLICIT_SHOWDOWN
from test_portfolio_risk_evidence import logical_db,source_bytes


class HindsightQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        env=patch.dict(os.environ,{'DFS_OPTIMIZER_DATA_DIR':self.tmp.name});env.start();self.addCleanup(env.stop)
        self.f=SourceFixture();self.addCleanup(self.f.close);self.f.snapshot();hi.reconcile(self.f.db)
        from main_window import ResultsLearningDialog
        self.dialog=ResultsLearningDialog();self.view=self.dialog.hindsight;self.addCleanup(self.cleanup)
        self.dialog.db_path=str(self.f.db);self.dialog.coverage.db_path=str(self.f.db)
        self.view.db_path=str(self.f.db);self.view.reload();self.view.source.setCurrentIndex(1)
    def cleanup(self):
        self.dialog.reject();self.drain();self.dialog.deleteLater();self.app.processEvents()
    def until(self,predicate,seconds=20):
        end=time.monotonic()+seconds
        while not predicate() and time.monotonic()<end:self.app.processEvents();time.sleep(.002)
        self.assertTrue(predicate())
    def drain(self):self.until(lambda:self.dialog._import_thread is None);self.app.processEvents()
    def start(self):self.dialog.start_hindsight(self.view.request())
    def queued(self):
        retired=[]
        with patch.object(self.dialog,'_on_import_thread_finished',side_effect=retired.append):
            self.start();job=self.dialog._import_job;self.until(lambda:bool(retired))
            self.assertEqual(retired,[job]);self.assertIn('report',job['completion'][1])
            for widget in (self.view,self.dialog.risk,self.dialog.coverage,self.dialog.import_new_button):self.assertFalse(widget.isEnabled())
        return job

    def test_completed_queued_cancel_retirement_suppresses_application(self):
        before=logical_db(self.f.db);files=source_bytes(self.f.root);job=self.queued();token=self.view.cancel_token
        self.dialog.cancel_import();self.assertTrue(token.is_set());self.assertFalse(self.view.isEnabled())
        self.dialog._on_import_thread_finished(job);self.drain()
        self.assertIsNone(self.view.report);self.assertFalse(self.view.copy_button.isEnabled());self.assertTrue(self.view.isEnabled())
        self.assertEqual(before,logical_db(self.f.db));self.assertEqual(files,source_bytes(self.f.root))

    def test_success_once_stale_and_duplicate_callbacks_do_not_touch_new_job(self):
        with patch.object(self.view,'finish',wraps=self.view.finish) as finish:
            first=self.queued();self.dialog._on_import_thread_finished(first);self.assertEqual(finish.call_count,1)
            second=self.queued();completion=second['completion']
            self.dialog._job_completed(first,lambda _:self.fail('stale'),{})
            self.dialog._job_completed(second,lambda _:self.fail('duplicate'),{})
            self.dialog._on_import_thread_finished(first);self.assertIs(second['completion'],completion)
            self.assertFalse(self.view.isEnabled());self.dialog._on_import_thread_finished(second)
            self.dialog._on_import_thread_finished(second);self.drain();self.assertEqual(finish.call_count,2)

    def test_source_and_assumption_changes_keep_prior_report_and_disable_copy(self):
        self.start();self.drain();prior=self.view.report
        for change in (lambda:self.view.compare.setChecked(True),lambda:self.view.source.setCurrentIndex(0)):
            job=self.queued();change();self.assertTrue(self.view.cancel_token.is_set())
            self.dialog._on_import_thread_finished(job);self.drain()
            self.assertIs(self.view.report,prior);self.assertFalse(self.view.copy_button.isEnabled())

    def held_capture(self,escape):
        entered,release=threading.Event(),threading.Event();original=ui.capture
        def held(*args,**kwargs):entered.set();release.wait(5);return original(*args,**kwargs)
        with patch.object(ui,'capture',side_effect=held):
            self.dialog.show();self.start()
            try:
                self.until(entered.is_set)
                if escape:QtTest.QTest.keyClick(self.dialog,QtCore.Qt.Key_Escape)
                else:self.dialog.close()
                self.assertFalse(self.view.isEnabled());self.assertTrue(self.view.cancel_token.is_set())
            finally:release.set();self.drain()
        self.assertIsNone(self.view.report);self.assertFalse(self.dialog.isVisible())
    def test_close_during_capture_waits_for_retirement(self):self.held_capture(False)
    def test_escape_during_capture_waits_for_retirement(self):self.held_capture(True)

    def close_solver(self,escape):
        original=bounded_solver.subprocess.Popen;children=[];entered=threading.Event();folders=[]
        def waiting_cbc(args,**kwargs):
            # Run the actual model in CBC, then keep that actual CBC process
            # awaiting input so GUI cancellation deterministically reaches it.
            kwargs['stdin']=subprocess.PIPE
            child=original([*args,'-'],**kwargs);children.append(child)
            from pathlib import Path
            folders.append(Path(args[1]).parent);entered.set();return child
        before=logical_db(self.f.db);files=source_bytes(self.f.root)
        with patch.object(bounded_solver.subprocess,'Popen',side_effect=waiting_cbc):
            self.dialog.show();self.start();self.until(entered.is_set)
            self.assertIsNone(children[0].poll())
            if escape:QtTest.QTest.keyClick(self.dialog,QtCore.Qt.Key_Escape)
            else:self.dialog.close()
            self.assertFalse(self.view.isEnabled());self.drain()
        self.assertIsNotNone(children[0].poll());children[0].stdin.close()
        self.assertTrue(all(not folder.exists() for folder in folders));self.assertIsNone(self.view.report)
        self.assertEqual(before,logical_db(self.f.db));self.assertEqual(files,source_bytes(self.f.root))
    def test_close_during_actual_cbc_reaps_child_and_temporary_files(self):self.close_solver(False)
    def test_escape_during_actual_cbc_reaps_child_and_temporary_files(self):self.close_solver(True)

    def test_solver_error_keeps_prior_completed_ui_and_original_sources(self):
        self.start();self.drain();prior=self.view.report;before=logical_db(self.f.db);files=source_bytes(self.f.root)
        with patch.object(bounded_solver,'solve',side_effect=RuntimeError('synthetic failure')):self.start();self.drain()
        self.assertIs(self.view.report,prior);self.assertFalse(self.view.copy_button.isEnabled())
        self.assertIn('Prior completed output retained',self.view.status.text())
        self.assertEqual(before,logical_db(self.f.db));self.assertEqual(files,source_bytes(self.f.root))

    def test_request_database_root_and_inputs_are_detached(self):
        request=self.view.request();worker=ui.HindsightWorker(self.f.db,request);self.addCleanup(worker.deleteLater)
        original=copy.deepcopy(worker.request);request['selection']['contest_id']='changed';request['seconds']=0
        self.assertEqual(worker.request,original);self.assertEqual(worker.history_root,str(self.f.root))

    def test_real_worker_excludes_conflicting_entry_copies_from_report_and_ui(self):
        bad=EXPLICIT_SHOWDOWN.replace('(1100)','(9999)')
        other=EXPLICIT_SHOWDOWN.replace('CPT A (1100) FLEX B (101)','FLEX A (100) CPT B (1101)')
        for order in ((EXPLICIT_SHOWDOWN,bad),(bad,EXPLICIT_SHOWDOWN),(EXPLICIT_SHOWDOWN,other)):
            with self.subTest(order=order):
                self.f=SourceFixture(edit_results=lambda rows:standings_entries(rows,[('1000','70',text) for text in order]))
                self.addCleanup(self.f.close);self.addCleanup(self.drain)
                hi.reconcile(self.f.db)
                self.dialog.db_path=str(self.f.db);self.dialog.coverage.db_path=str(self.f.db)
                self.view.db_path=str(self.f.db);self.view.reload();self.view.source.setCurrentIndex(1)
                before=logical_db(self.f.db);files=source_bytes(self.f.root)
                with patch.object(self.view,'finish',wraps=self.view.finish) as finish:
                    self.start();self.drain();self.assertEqual(finish.call_count,1)
                    result=finish.call_args.args[0];self.assertIn('report',result)
                    self.assertFalse(result.get('error'));self.assertFalse(result.get('cancelled'))
                report=self.view.report.data;o=report['capture']['observed']
                self.assertEqual(o['coverage']['conflicting_entry_ids'],1)
                self.assertEqual(o['coverage']['accepted_entries'],0);self.assertIsNone(o['highest'])
                self.assertIsNone(report['gaps']['observed_units'])
                if bad in order:
                    self.assertEqual(report['scopes']['supplied']['status'],'unavailable_evidence')
                    self.assertEqual(report['scopes']['supplied']['blockers'],['qualified_exact_salary_revision_required'])
                else:self.assertEqual(report['scopes']['supplied']['points'],'70')
                overview=self.view.overview.toPlainText()
                self.assertIn('0 accepted entries; 1 conflicting IDs; 0 reported scores',overview)
                self.assertIn('Supplied-pool minus validated highest reported score: Unavailable; compatible exact scores are required',overview)
                self.assertNotIn('Observed highest witness',[self.view.lineups.item(i,0).text() for i in range(self.view.lineups.rowCount())])
                self.assertTrue(self.view.copy_button.isEnabled());self.view.copy_summary()
                self.assertIn('Highest reported score in supplied entries: Unavailable.',self.app.clipboard().text())
                self.assertEqual(before,logical_db(self.f.db));self.assertEqual(files,source_bytes(self.f.root))

    def test_copy_is_aggregate_by_default_and_points_sort_numerically(self):
        self.view.compare.setChecked(True);self.start();self.drain();self.view.copy_summary()
        self.assertIn('Supplied-pool minus snapshot-local optimum: 0 points.',self.view.overview.toPlainText())
        self.assertIn('Field coverage is partial or unverified.',self.view.overview.toPlainText())
        text=self.app.clipboard().text();self.assertNotIn('CPT A',text)
        for secret in (str(self.f.root),self.f.source['hash'],'Synthetic_User'):self.assertNotIn(secret,text)
        self.view.private.setChecked(True);self.view.copy_summary();self.assertIn('CPT A',self.app.clipboard().text())
        self.view.lineups.sortItems(1,QtCore.Qt.DescendingOrder)
        self.assertEqual(float(self.view.lineups.item(0,1).text()),70)

    def test_open_switch_and_cached_reload_never_capture_or_solve(self):
        with patch.object(ui,'capture',side_effect=AssertionError('capture')),patch.object(ui,'calculate',side_effect=AssertionError('solve')):
            self.dialog.results_tabs.setCurrentWidget(self.view);self.view.reload();self.app.processEvents()
        self.assertIsNone(self.view.report)

    def test_real_worker_and_cbc_keep_qt_timer_responsive(self):
        ticks=[];timer=QtCore.QTimer();timer.setInterval(5);timer.timeout.connect(lambda:ticks.append(time.monotonic()))
        timer.start();started=time.monotonic();self.start();self.drain();timer.stop()
        r=self.view.report.data['scopes']['supplied'];self.assertEqual(r['status'],'optimal');self.assertGreater(len(ticks),0)
        gap=max((b-a for a,b in zip(ticks,ticks[1:])),default=0)
        print(f"RL-07A real Qt/CBC: {time.monotonic()-started:.3f}s, {len(ticks)} ticks, max gap {gap:.3f}s; CBC {r['solver']['solver_version']}; ties {r['ties']['status']}")
