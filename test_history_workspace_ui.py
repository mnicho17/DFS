from test_environment import install
install()
import copy
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PyQt5 import QtCore, QtWidgets
from main_window import ResultsLearningDialog
from history_workspace_ui import ExploreHistoryWorker
from opponent_history import index_contest, profile_preview
from test_hindsight_evidence import SourceFixture
from test_opponent_history import results
from test_portfolio_risk_evidence import logical_db, source_bytes


class HistoryWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def dialog(self):
        d=ResultsLearningDialog(); self.addCleanup(d.close); return d

    def fixture(self):
        f=SourceFixture(edit_results=results()); self.addCleanup(f.close)
        index_contest(f.db,f.source['hash']); return f

    def wait(self, d):
        end=time.monotonic()+10
        while d._import_thread is not None and time.monotonic()<end:
            self.app.processEvents(); time.sleep(.005)
        self.assertIsNone(d._import_thread)

    def test_three_views_reuse_existing_tools_without_starting_import_or_search(self):
        with patch('opponent_history.profile_preview',side_effect=AssertionError('No search')), \
             patch('analysis_imports.import_folders',side_effect=AssertionError('No import')):
            d=self.dialog()
            self.assertEqual([d.history_workspace.tabText(i) for i in range(3)],
                             ['Overview','Explore users','Learning coverage'])
            self.assertIsNone(d._import_thread)
            self.assertEqual(d.results_tabs.widget(0),d.coverage)
            self.assertGreaterEqual(d.results_tabs.indexOf(d.risk),0)
            self.assertGreaterEqual(d.results_tabs.indexOf(d.hindsight),0)
            self.assertTrue(d.history_workspace.isAncestorOf(d.report))
            self.assertIn('No reconciled coverage',d.history_attention.text())
            self.assertEqual(d.history_explore.username.text(),'')
            self.assertIsNone(d.history_refresh.thread)

    def test_real_username_search_is_readonly_and_reports_construction_and_entry_counts(self):
        f=self.fixture(); d=self.dialog(); d.db_path=f.db
        before=logical_db(f.db); sources=source_bytes(f.root)
        d.history_explore.username.setText('WINNER (2/50)')
        d.history_explore.cutoff.setDate(QtCore.QDate(2026,9,22))
        d.history_explore.start()
        self.assertFalse(d.history_explore.isEnabled())
        self.assertEqual(d.history_refresh._reports,1)
        self.wait(d)
        self.assertTrue(d.history_explore.isEnabled()); self.assertEqual(d.history_refresh._reports,0)
        self.assertEqual(d.history_explore.table.rowCount(),1)
        self.assertEqual(d.history_explore.table.item(0,2).data(QtCore.Qt.UserRole),50)
        text=d.history_explore.report.toPlainText()
        self.assertIn('constructions:',text); self.assertIn('Contest history:',text)
        self.assertIn('Captain pool',text); self.assertIn('50 entries',d.history_explore.status.text())
        self.assertEqual(logical_db(f.db),before); self.assertEqual(source_bytes(f.root),sources)

    def test_worker_cutoff_format_and_source_conflict_reuse_existing_validation(self):
        f=self.fixture()
        def run(cutoff,fmt='showdown'):
            w=ExploreHistoryWorker(f.db,cutoff,fmt,'Winner'); out=[]
            w.finished.connect(out.append); w.run(); return out[0]['profile']
        self.assertEqual(run('2026-09-21')['observed']['entries'],0)
        self.assertEqual(run('2026-09-22','classic')['observed']['entries'],0)
        self.assertEqual(run('2026-09-22')['observed']['entries'],50)
        f.edit_source('results',lambda rows:rows[1].__setitem__(3,'999'))
        before=logical_db(f.db)
        profile=run('2026-09-22')
        self.assertEqual(profile['observed']['entries'],0); self.assertTrue(profile['excluded'])
        self.assertEqual(logical_db(f.db),before)

    def test_cancelled_and_failed_search_preserve_displayed_analysis(self):
        d=self.dialog(); view=d.history_explore
        view.report.setPlainText('Previous verified search')
        view.finish({'cancelled':True})
        self.assertEqual(view.report.toPlainText(),'Previous verified search')
        view.finish({'error':'Unmapped identity'})
        self.assertEqual(view.report.toPlainText(),'Previous verified search')
        self.assertIn('Previous analysis remains',view.status.text())
        worker=ExploreHistoryWorker('missing.sqlite','2026-09-22','showdown','')
        worker.request_cancel(); out=[]; worker.finished.connect(out.append)
        with patch('opponent_history.profile_preview',return_value={'ignored':True}):worker.run()
        self.assertEqual(out,[{'cancelled':True}])

    def test_shared_busy_controller_prevents_search_and_selection_does_no_work(self):
        d=self.dialog(); view=d.history_explore
        d.history_refresh.thread=object()
        try:
            with patch.object(d,'_start_background_import') as start:view.start()
            start.assert_not_called()
        finally:d.history_refresh.thread=None
        view.table.setRowCount(1); item=QtWidgets.QTableWidgetItem('OtherUser'); view.table.setItem(0,0,item)
        with patch.object(d,'_start_background_import') as start:view.select_user(item)
        start.assert_not_called(); self.assertEqual(view.username.text(),'OtherUser')

    def test_advanced_tools_protect_history_until_dialog_retires(self):
        d=self.dialog()
        with patch('opponent_history_ui.OpponentHistoryDialog') as dialog:
            dialog.return_value.exec_.side_effect=lambda:self.assertEqual(d.history_refresh._reports,1)
            d.history_explore.advanced()
        self.assertEqual(d.history_refresh._reports,0)
        with patch('opponent_analysis_ui.OpponentAnalysisDialog') as dialog:
            dialog.return_value.exec_.side_effect=lambda:self.assertEqual(d.history_refresh._reports,1)
            d.open_opponent_portfolios()
        self.assertEqual(d.history_refresh._reports,0)

    def test_overview_navigation_preserves_coverage_review_actions(self):
        d=self.dialog()
        review=next(b for b in d.findChildren(QtWidgets.QPushButton) if b.text()=='Review learning coverage')
        review.click(); self.assertEqual(d.history_workspace.currentIndex(),2)
        self.assertTrue(d.results_tabs.isAncestorOf(d.coverage.salary_button))
        self.assertFalse(d.coverage.salary_button.isEnabled())  # No contest is selected.
        d._set_import_controls(False)
        self.assertFalse(d.history_explore.isEnabled()); self.assertFalse(d.coverage.isEnabled())
        d._set_import_controls(True)
        self.assertTrue(d.history_explore.isEnabled()); self.assertTrue(d.coverage.isEnabled())

    def test_queued_success_then_cancel_does_not_replace_previous_search(self):
        d=self.dialog(); view=d.history_explore
        view.report.setPlainText('Previous verified search')
        jobs=[]
        with patch.object(d,'_start_background_import',side_effect=lambda worker,callback:jobs.append((worker,callback))):
            view.start()
        worker, callback=jobs[0]
        worker.request_cancel()
        callback({'profile':{'should_not_be_applied':True}})
        self.assertEqual(view.report.toPlainText(),'Previous verified search')
        self.assertIn('cancelled',view.status.text())

    def test_close_cancels_exploration_and_waits_for_retirement(self):
        from analysis_imports import ImportCancelled
        d=self.dialog(); view=d.history_explore
        view.report.setPlainText('Previous verified search')
        def slow(db,cutoff,fmt,username,cancelled):
            while not cancelled():time.sleep(.005)
            raise ImportCancelled()
        with patch('opponent_history.profile_preview',side_effect=slow):
            view.start(); self.assertIsNotNone(d._import_thread)
            d.close(); self.assertIsNotNone(d._import_thread)
            self.wait(d)
        self.assertEqual(view.report.toPlainText(),'Previous verified search')
        self.assertEqual(d.history_refresh._reports,0)

    def test_overview_controls_are_reparented_and_visible_with_settings_collapsed(self):
        d=self.dialog(); d._initial_report_started=True
        d.resize(1120,850); d.show(); self.app.processEvents()
        for widget in (d.report,d.summary,d.history_status,d.history_update_summary,
                       d.history_refresh_button,d.history_cancel_button,d.attach_salary_button):
            self.assertTrue(widget.isVisible(),widget.objectName())
            self.assertTrue(d.history_workspace.isAncestorOf(widget))
        self.assertFalse(d.username_edit.isVisible());self.assertFalse(d.auto_history.isVisible());self.assertFalse(d.import_new_button.isVisible())
        toggle=next(b for b in d.findChildren(QtWidgets.QToolButton) if b.text()=='Sources and account settings')
        toggle.click(); self.app.processEvents();self.assertTrue(d.username_edit.isVisible());self.assertTrue(d.auto_history.isVisible())
        tools=next(b for b in d.findChildren(QtWidgets.QToolButton) if b.text()=='More reports and analysis tools')
        tools.click();self.app.processEvents();self.assertTrue(d.import_new_button.isVisible())

    def status_dialog(self):
        import tempfile
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        d=self.dialog();root=Path(temp.name)
        settings=QtCore.QSettings(str(root/'settings.ini'),QtCore.QSettings.IniFormat)
        d.learning_settings=settings;d.history_refresh.settings=settings
        d.history_refresh.watch.arm();return d,root

    def test_no_folders_action_opens_source_settings_without_importing(self):
        d,_=self.status_dialog();d.update_history_status()
        self.assertEqual(d.history_refresh_button.text(),'Choose source folders…')
        with patch.object(d.history_refresh,'start') as start,patch('analysis_imports.import_folders',side_effect=AssertionError('No implicit import')):
            d.history_refresh_button.click()
        start.assert_not_called();self.assertTrue(d.history_sources_toggle.isChecked())
        self.assertIn('Choose source folders',d.history_update_summary.text())

    def test_monitoring_status_is_read_only_and_never_certifies_qualified_history(self):
        d,root=self.status_dialog();c=d.history_refresh
        c.settings.setValue('learning/results_folder',str(root));c.watch.arm()
        c.text='History refresh complete.\nRecorded counts stay in the detailed log.'
        with patch.object(c,'start',side_effect=AssertionError('No worker')),patch('analysis_imports.import_folders',side_effect=AssertionError('No import')):
            d.update_history_status();d.update_history_status()
        self.assertIn('Watching for changes',d.history_update_summary.text())
        self.assertIn('Last recorded check',d.history_update_summary.text())
        self.assertIn('do not establish current source',d.history_update_summary.text())
        self.assertNotIn('Recorded counts',d.history_update_summary.text())
        self.assertIn('Recorded counts',d.history_status.toPlainText())
        self.assertEqual(d.history_refresh_button.text(),'Check now')

    def test_automatic_off_and_watch_off_have_distinct_states_without_changed_preferences(self):
        from history_workspace_ui import history_update_view
        d,root=self.status_dialog();c=d.history_refresh;c.settings.setValue('learning/results_folder',str(root))
        c.settings.setValue('learning/auto_history_refresh',False)
        self.assertEqual(history_update_view(c)['state'],'Automatic updates off')
        c.settings.setValue('learning/auto_history_refresh',True);c.settings.setValue('learning/watch_history_changes',False)
        self.assertEqual(history_update_view(c)['state'],'Startup checks only; folder monitoring off')
        before={k:c.settings.value(k) for k in c.settings.allKeys()}
        d.update_history_status();self.assertEqual(before,{k:c.settings.value(k) for k in c.settings.allKeys()})

    def test_retry_and_check_now_use_existing_full_refresh_and_keep_auto_preference(self):
        d,root=self.status_dialog();c=d.history_refresh;c.settings.setValue('learning/results_folder',str(root))
        c.settings.setValue('learning/auto_history_refresh',False);c.watch.paused=True
        d.update_history_status();self.assertEqual(d.history_refresh_button.text(),'Retry / Resume')
        with patch.object(c,'start') as start:d.history_refresh_button.click()
        start.assert_called_once_with();self.assertFalse(c.enabled())
        c.watch.paused=False;d.update_history_status()
        with patch.object(c,'start') as start:d.history_refresh_button.click()
        start.assert_called_once_with();self.assertFalse(c.enabled())

    def test_running_stopping_and_report_work_disable_duplicate_checks(self):
        import threading
        from history_workspace_ui import history_update_view
        d,root=self.status_dialog();c=d.history_refresh;c.settings.setValue('learning/results_folder',str(root))
        c.thread=object();c.stop=threading.Event()
        try:
            d.update_history_status();self.assertIn('Checking history',d.history_update_summary.text())
            self.assertFalse(d.history_refresh_button.isEnabled());self.assertTrue(d.history_cancel_button.isEnabled())
            with patch.object(c,'start') as start:d.refresh_username_history()
            start.assert_not_called()
            c.stop.set();d.update_history_status();self.assertIn('Stopping safely',d.history_update_summary.text())
            self.assertFalse(d.history_cancel_button.isEnabled())
        finally:c.thread=None;c.stop=None
        self.assertFalse(history_update_view(c,report_busy=True)['can_check'])

    def test_unarmed_and_closed_watcher_do_not_claim_active_monitoring(self):
        from history_workspace_ui import history_update_view
        d,root=self.status_dialog();c=d.history_refresh;c.settings.setValue('learning/results_folder',str(root/'missing'))
        c.watch.arm();view=history_update_view(c)
        self.assertEqual(view['state'],'Monitoring not armed');self.assertIn('Some paths could not',view['detail'])
        c.watch.shutdown();view=history_update_view(c)
        self.assertEqual(view['state'],'Closing');self.assertFalse(view['can_check']);self.assertFalse(view['can_pause'])

    def test_queue_status_and_pause_route_preserve_existing_controller(self):
        from history_workspace_ui import history_update_view
        d,root=self.status_dialog();c=d.history_refresh;c.settings.setValue('learning/results_folder',str(root));c.watch.pending=True
        d.update_history_status();self.assertEqual(history_update_view(c)['state'],'Update queued')
        self.assertTrue(d.history_cancel_button.isEnabled())
        with patch.object(c.watch,'pause') as pause:d.history_cancel_button.click()
        pause.assert_called_once();c.watch.pending=False
