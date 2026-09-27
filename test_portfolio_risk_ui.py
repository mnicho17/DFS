"""Real Qt signal delivery, cancellation retirement and measured risk viewing."""
from test_environment import install
install()
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PyQt5 import QtCore, QtWidgets, QtTest
import portfolio_risk_ui as ui
from portfolio_risk import calculate, summary
from test_portfolio_risk import make_capture
from test_portfolio_risk_evidence import logical_db, source_bytes, repeat_archive
import test_historical_identity as fixtures


class PortfolioRiskQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        env=patch.dict(os.environ,{'DFS_OPTIMIZER_DATA_DIR':self.tmp.name});env.start();self.addCleanup(env.stop)
        from main_window import ResultsLearningDialog
        self.dialog=ResultsLearningDialog();self.risk=self.dialog.risk
        self.addCleanup(self.cleanup)
        self.f=fixtures.HistoricalIdentityTests();self.f.setUp();self.addCleanup(self.f.tearDown)
        self.f.fixture();self.f.snapshot();self.archive=self.f.archive();self.f.one(True)
        self.dialog.db_path=str(self.f.db);self.dialog.coverage.db_path=str(self.f.db)
        self.risk.db_path=str(self.f.db);self.risk.reload();self.risk.source.setCurrentIndex(1)

    def cleanup(self):
        self.dialog.reject();self.drain();self.dialog.deleteLater();self.app.processEvents()

    def pump_until(self,predicate,seconds=15):
        end=time.monotonic()+seconds
        while not predicate() and time.monotonic()<end:
            self.app.processEvents();time.sleep(.002)
        self.assertTrue(predicate())

    def drain(self):
        self.pump_until(lambda:self.dialog._import_thread is None)
        self.app.processEvents()

    def start(self):
        self.dialog.start_portfolio_risk(self.risk.request())

    def queued(self):
        retired=[]
        with patch.object(self.dialog,'_on_import_thread_finished',side_effect=retired.append):
            self.start();job=self.dialog._import_job
            self.pump_until(lambda:bool(retired))
            self.assertEqual(retired,[job]);self.assertIn('report',job['completion'][1])
            self.assertFalse(self.risk.isEnabled());self.assertFalse(self.dialog.import_new_button.isEnabled())
        return job

    def test_completed_queued_result_cancel_retirement_suppresses_readonly_apply(self):
        before=logical_db(self.f.db);files=source_bytes(self.f.root)
        job=self.queued();token=self.risk.cancel_token
        self.dialog.cancel_import();self.assertTrue(token.is_set())
        self.assertFalse(self.risk.isEnabled())
        self.dialog._on_import_thread_finished(job);self.drain()
        self.assertIsNone(self.risk.report);self.assertFalse(self.risk.copy_button.isEnabled())
        self.assertTrue(self.risk.isEnabled());self.assertIn('Cancelled',self.risk.status.text())
        self.assertEqual(before,logical_db(self.f.db));self.assertEqual(files,source_bytes(self.f.root))

    def test_ordinary_once_stale_duplicate_callbacks_cannot_apply(self):
        with patch.object(self.risk,'finish',wraps=self.risk.finish) as finish:
            first=self.queued();self.dialog._on_import_thread_finished(first)
            self.assertEqual(finish.call_count,1);self.assertTrue(self.risk.copy_button.isEnabled())
            second=self.queued();completion=second['completion']
            self.dialog._job_completed(first,lambda _:self.fail('stale result'),{})
            self.dialog._job_completed(second,lambda _:self.fail('duplicate result'),{})
            self.dialog._on_import_thread_finished(first)
            self.assertIs(second['completion'],completion);self.assertFalse(self.risk.isEnabled())
            self.dialog._on_import_thread_finished(second);self.dialog._on_import_thread_finished(second)
            self.drain();self.assertEqual(finish.call_count,2)

    def test_source_change_invalidates_queued_capture_and_copy(self):
        self.start();self.drain();prior=self.risk.report
        job=self.queued();self.risk.source.setCurrentIndex(0)
        self.assertTrue(self.risk.cancel_token.is_set());self.assertFalse(self.risk.isEnabled())
        self.dialog._on_import_thread_finished(job);self.drain()
        self.assertIs(self.risk.report,prior);self.assertFalse(self.risk.copy_button.isEnabled())
        self.assertIn('Prior completed output',self.risk.status.text())

    def test_assumption_change_invalidates_queued_capture_and_copy(self):
        self.start();self.drain();prior=self.risk.report
        job=self.queued();self.risk.target_a.setCurrentIndex(1)
        self.assertTrue(self.risk.cancel_token.is_set())
        self.dialog._on_import_thread_finished(job);self.drain()
        self.assertIs(self.risk.report,prior);self.assertFalse(self.risk.copy_button.isEnabled())

    def held_close(self,escape):
        entered,release=threading.Event(),threading.Event();original=ui.capture
        def held(*args,**kwargs):
            entered.set();release.wait(5);return original(*args,**kwargs)
        with patch.object(ui,'capture',side_effect=held):
            self.dialog.show();self.start()
            try:
                self.pump_until(entered.is_set)
                if escape:QtTest.QTest.keyClick(self.dialog,QtCore.Qt.Key_Escape)
                else:self.dialog.close()
                self.assertFalse(self.risk.isEnabled());self.assertTrue(self.risk.cancel_token.is_set())
            finally:
                release.set();self.drain()
        self.assertIsNone(self.risk.report);self.assertFalse(self.dialog.isVisible())

    def test_close_during_real_work_retires_before_closing(self):self.held_close(False)
    def test_escape_during_real_work_retires_before_closing(self):self.held_close(True)

    def test_error_preserves_prior_output_without_matching_copy(self):
        self.start();self.drain();prior=self.risk.report
        before=logical_db(self.f.db);files=source_bytes(self.f.root)
        with patch.object(ui,'capture',side_effect=ValueError('Synthetic source changed')):
            self.start();self.drain()
        self.assertIs(self.risk.report,prior);self.assertFalse(self.risk.copy_button.isEnabled())
        self.assertIn('Synthetic source changed',self.risk.status.text());self.assertTrue(self.risk.isEnabled())
        self.assertEqual(before,logical_db(self.f.db));self.assertEqual(files,source_bytes(self.f.root))

    def test_job_pins_database_root_source_and_detached_assumptions(self):
        request=self.risk.request();worker=ui.RiskWorker(self.f.db,request);self.addCleanup(worker.deleteLater)
        request['selection']['archive_id']='changed';request['targets'].append('changed')
        self.assertEqual(worker.history_root,str(self.f.root));self.assertNotEqual(worker.request,request)
        self.assertEqual(worker.request['targets'],[])
        worker.request_cancel();finished=[];worker.finished.connect(finished.append)
        with patch.object(ui,'capture',wraps=ui.capture):worker.run()
        self.assertEqual(finished,[{'cancelled':True}])

    def test_source_choice_default_targets_and_explicit_multiple_archive_choice(self):
        self.assertEqual(self.risk.target_a.currentData(),None);self.assertEqual(self.risk.target_b.currentData(),None)
        self.assertIsNotNone(self.risk.archive.currentData())
        self.f.archive('second.zip',created_at='2026-09-21T18:12:00-04:00');self.f.one(True)
        self.risk.reload();self.risk.source.setCurrentIndex(1)
        self.assertIsNone(self.risk.archive.currentData());self.assertFalse(self.risk.run_button.isEnabled())
        self.risk.archive.setCurrentIndex(2);self.assertTrue(self.risk.run_button.isEnabled())

    def test_pool_zero_exposure_is_selectable_numeric_sorting_privacy_and_exact_copy(self):
        report=calculate(make_capture(('AB','A','B','')),['A','B'],['O4'])
        request=self.risk.request()
        # Populate the scoped identity pool through a complete worker-style result.
        self.risk.finish(dict(report=calculate(make_capture(('AB',))),fingerprint=request['fingerprint'],seconds=.1))
        self.risk.target_a.setCurrentIndex(self.risk.target_a.findData('A'))
        self.risk.target_b.setCurrentIndex(self.risk.target_b.findData('B'))
        self.assertGreater(self.risk.target_a.findData('O5'),0)
        self.risk.finish(dict(report=report,fingerprint=self.risk.request()['fingerprint'],seconds=.1))
        self.risk.copy_summary();copied=self.app.clipboard().text()
        self.assertEqual(copied,summary(report));self.assertNotIn('PRIVATE_',copied);self.assertNotIn('Private Athlete',copied)
        self.risk.details.setChecked(True);self.risk.copy_summary();self.assertIn('Private Athlete',self.app.clipboard().text())
        self.risk._table(self.risk.exposures,['Numeric'],[[2],[100],[10],[None]])
        self.risk.exposures.sortItems(0,QtCore.Qt.DescendingOrder)
        self.assertEqual([self.risk.exposures.item(i,0).text() for i in range(4)],['100','10','2','Unavailable'])
        self.risk.target_a.setCurrentIndex(0);self.app.clipboard().setText('unchanged');self.risk.copy_summary()
        self.assertEqual(self.app.clipboard().text(),'unchanged')

    def test_real_1000_occurrence_worker_view_measures_event_loop_responsiveness(self):
        repeat_archive(self.archive,1000);self.f.one(True);self.risk.reload();self.risk.source.setCurrentIndex(1)
        before=logical_db(self.f.db);files=source_bytes(self.f.root)
        self.dialog.show();self.dialog.results_tabs.setCurrentWidget(self.risk)
        ticks=[];timer=QtCore.QTimer();timer.setInterval(5);timer.timeout.connect(lambda:ticks.append(time.monotonic()))
        entered=[];original=ui.capture
        def checked(*args,**kwargs):
            entered.append(QtCore.QThread.currentThread() is not self.app.thread())
            return original(*args,**kwargs)
        started=time.monotonic();timer.start()
        with patch.object(ui,'capture',side_effect=checked):
            self.start();self.drain()
        timer.stop();elapsed=time.monotonic()-started
        self.assertIsNotNone(self.risk.report,self.risk.status.text())
        self.assertEqual(self.risk.report.data['N'],1000);self.assertEqual(entered,[True])
        self.assertGreaterEqual(len(ticks),2)
        gaps=[b-a for a,b in zip([started,*ticks],[*ticks,time.monotonic()])]
        self.assertLess(max(gaps),2.0)
        self.assertEqual(before,logical_db(self.f.db));self.assertEqual(files,source_bytes(self.f.root))
        print(f'RL-06 real 1000-occurrence Qt capture: {elapsed:.3f}s, {len(ticks)} timer ticks, max gap {max(gaps):.3f}s')
