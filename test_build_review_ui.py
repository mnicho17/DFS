from test_environment import install
install()
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PyQt5 import QtCore,QtWidgets
from main_window import BuildDiagnosticsDialog,MainWindow
from build_diagnostics import create_build_diagnostic,save_build_diagnostic,load_build_history,format_build_report
from build_review_ui import review_views,report_text,export_review


class BuildReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def record(self,marker='one',selected=2):
        return create_build_diagnostic(context=dict(sport='NFL',kind='showdown',input_id=marker,
            requested_count=2,settings=dict(sim_enabled=True,compute_mode='Deep'),portfolio_rules={'min_unique':2}),
            timing_report=dict(selected_count=selected,requested_count=2,
                build_preparation=[dict(stage='library',state='Completed',detail=marker)]),
            portfolio_report=dict(warnings=['Recorded warning '+marker]),sim_report={})

    def dialog(self,parent=None):
        d=BuildDiagnosticsDialog(parent);self.addCleanup(d.close);return d

    def test_selected_views_are_detached_and_missing_evidence_is_unknown(self):
        r=self.record();r['sim'].pop('validation_scenarios',None)
        before=copy.deepcopy(r);views=review_views(r)
        self.assertIn('Input ID: one',views['overview']);self.assertIn(r['portfolio']['warnings'][0],views['limits'])
        self.assertIn('Per-lineup snapshots are not recorded',views['lineups'])
        self.assertIn('No sampled-field comparison',views['comparisons'])
        self.assertIn('Independent validation scenarios: not recorded',views['compute'])
        self.assertEqual(r,before)
        empty=review_views({})
        self.assertTrue(all(v=='No build receipt selected.' for v in empty.values()))

    def test_tabs_copy_and_selected_history_never_borrow_current_inputs(self):
        # Use an explicit history source so this test is independent of other saved runs.
        r=self.record('old-run');w=MainWindow();self.addCleanup(w.close)
        w.last_build_diagnostic={};w.last_sim_report={'poison':'new-run'};w.players=[{'Name':'New player'}]
        before=copy.deepcopy((w.players,w.last_sim_report))
        with patch('main_window.load_build_history',return_value=[r]):d=self.dialog(w)
        self.assertEqual(d.review_tabs.count(),6)
        self.assertIn('old-run',d.review_panels['overview'].toPlainText())
        self.assertNotIn('new-run',' '.join(t.toPlainText() for t in d.review_panels.values()))
        d.copy_selected_report();self.assertEqual(QtWidgets.QApplication.clipboard().text(),format_build_report(r))
        self.assertEqual((w.players,w.last_sim_report),before)

    def test_comparison_and_selection_reset_use_existing_exact_report(self):
        a,b=self.record('first',1),self.record('second',2)
        with patch('main_window.load_build_history',return_value=[a,b]):d=self.dialog()
        d.history_list.item(0).setSelected(True);d.history_list.item(1).setSelected(True)
        self.assertTrue(d.compare_button.isEnabled());d.compare_selected()
        self.assertEqual(d.review_panels['comparisons'].toPlainText(),d.report.toPlainText())
        self.assertIs(d.review_tabs.currentWidget(),d.review_panels['comparisons'])
        d.history_list.setCurrentRow(0);d._show_selected()
        self.assertEqual(d.report.toPlainText(),format_build_report(a))
        self.assertIn('No sampled-field',d.review_panels['comparisons'].toPlainText())

    def test_failed_attempt_is_separate_and_cannot_compare_with_completed_receipt(self):
        w=MainWindow();self.addCleanup(w.close);w.last_build_diagnostic={}
        failure=dict(record_type='failed_attempt',failure_message='Lineup limits\nPenix cap 39/50',
                     input_id='failed-input',requested_count=50)
        w._last_build_failure=failure
        with patch('main_window.load_build_history',return_value=[self.record('previous')]):d=self.dialog(w)
        self.assertIn('failed-input',d.report.toPlainText())
        self.assertNotIn('previous',d.report.toPlainText())
        self.assertIn('No completed result',d.review_panels['lineups'].toPlainText())
        self.assertIn('Penix cap',d.review_panels['limits'].toPlainText())
        d.history_list.item(0).setSelected(True);d.history_list.item(1).setSelected(True)
        self.assertFalse(d.compare_button.isEnabled())
        d.compare_selected();self.assertIn('Failed Build Attempt',d.report.toPlainText())

    def test_export_matches_copy_and_cancellation_does_not_write(self):
        with patch('main_window.load_build_history',return_value=[self.record()]):d=self.dialog()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'review.txt'
            with patch('PyQt5.QtWidgets.QFileDialog.getSaveFileName',return_value=(str(path),'Text report')):export_review(d)
            self.assertEqual(path.read_text(encoding='utf-8'),d.report.toPlainText().strip())
            before=path.read_bytes()
            with patch('PyQt5.QtWidgets.QFileDialog.getSaveFileName',return_value=('','')):export_review(d)
            self.assertEqual(path.read_bytes(),before)
        with patch('PyQt5.QtCore.QSaveFile') as save, \
             patch('PyQt5.QtWidgets.QFileDialog.getSaveFileName',return_value=('chosen.txt','')):
            save.return_value.open.return_value=False;save.return_value.errorString.return_value='unavailable'
            export_review(d)
        self.assertIn('failed',d.note.text());save.return_value.write.assert_not_called()

    def test_unsaved_latest_receipt_is_visible_without_rewriting_history(self):
        w=MainWindow();self.addCleanup(w.close);w.last_build_diagnostic=self.record('in-memory')
        with patch('main_window.load_build_history',return_value=[]),patch('build_diagnostics.save_build_diagnostic') as save:
            d=self.dialog(w)
        self.assertEqual(d.history_list.count(),1);self.assertIn('in-memory',d.report.toPlainText())
        self.assertFalse(d.clear_button.isEnabled());save.assert_not_called()

    def test_failure_capture_preserves_previous_results_and_rejects_stale_receipt(self):
        from main_window import BuildReceipt
        w=MainWindow();self.addCleanup(w.close);old=self.record('completed');w.last_build_diagnostic=old
        w.last_showdown=[{'retained':'same'}];before=copy.deepcopy(w.last_showdown)
        receipt=BuildReceipt(kind='showdown',sport='NFL',requested=50,
            context=dict(kind='showdown',sport='NFL',requested_count=50,input_id='failed-input'))
        w._build_receipt=receipt
        with patch('main_window.show_build_error'):w._on_lineup_build_error('Lineup limits\nblocked',receipt=receipt)
        self.assertEqual(w._last_build_failure['input_id'],'failed-input')
        self.assertEqual(w.last_build_diagnostic,old);self.assertEqual(w.last_showdown,before)
        captured=copy.deepcopy(w._last_build_failure)
        stale=BuildReceipt(kind='classic',sport='NFL',requested=1,context={})
        with patch('main_window.show_build_error'):w._on_lineup_build_error('stale',receipt=stale)
        self.assertEqual(w._last_build_failure,captured)

    def test_review_entry_point_and_current_tools_remain_explicit(self):
        w=MainWindow();self.addCleanup(w.close)
        button=w.findChild(QtWidgets.QPushButton,'reviewBuildButton');self.assertIsNotNone(button)
        with patch('main_window.BuildDiagnosticsDialog') as dialog:button.click()
        dialog.return_value.exec_.assert_called_once()
        with patch('main_window.load_build_history',return_value=[]):d=self.dialog(w)
        with patch.object(w,'on_portfolio_summary') as current:d.current_tools_button.click()
        current.assert_called_once();self.assertFalse(d.export_button.isEnabled())

    def test_recorded_lineups_exposures_and_adjustments_survive_review_without_writes(self):
        from test_learning_results import _showdown_lineup
        lineup=_showdown_lineup()
        r=create_build_diagnostic(context=dict(kind='showdown',sport='NFL',requested_count=1,salary_cap=50000),
            timing_report=dict(selected_count=1),lineups=[lineup],
            portfolio_report=dict(captain_pool_adjustments=[dict(player='Alpha One',field='max_total_pct',before=78,after=100)]))
        with tempfile.TemporaryDirectory() as folder,patch.dict('os.environ',{'DFS_OPTIMIZER_DATA_DIR':folder}):
            save_build_diagnostic(r)
            from build_diagnostics import build_history_path
            path=Path(build_history_path());before=path.read_bytes()
            d=self.dialog()
            self.assertIn('Alpha One',d.review_panels['lineups'].toPlainText())
            self.assertIn('78 → 100',d.review_panels['limits'].toPlainText())
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual(d.selected_record()['lineup_details'],r['lineup_details'])

    def test_cancelled_error_does_not_create_a_failed_attempt(self):
        from main_window import BuildReceipt
        w=MainWindow();self.addCleanup(w.close)
        receipt=BuildReceipt(kind='showdown',sport='NFL',requested=1,context={})
        receipt.cancelled=True;w._build_receipt=receipt
        with patch('main_window.show_build_error'):w._on_lineup_build_error('late cancelled error',receipt=receipt)
        self.assertFalse(getattr(w,'_last_build_failure',None))

    def test_review_tabs_and_selected_content_are_visible_at_laptop_size(self):
        with patch('main_window.load_build_history',return_value=[self.record()]):d=self.dialog()
        d.resize(1280,800);d.show();self.app.processEvents()
        self.assertTrue(d.review_tabs.isVisible())
        self.assertTrue(d.review_panels['overview'].isVisible())
        self.assertGreater(d.review_tabs.width(),500)
        d.review_tabs.setCurrentWidget(d.report);self.app.processEvents()
        self.assertTrue(d.report.isVisible())

    def test_cancelled_repair_application_is_explicit_and_sim_off_is_not_sim_scoring(self):
        r=self.record();r['status']='cancelled';r['settings']['sim_enabled']=False
        r['application']={'status':'rejected','reason':'saved entries changed'}
        text=review_views(r)['overview']
        self.assertIn('cancelled',text)
        self.assertIn('Saved-repair application: rejected (saved entries changed)',text)
        self.assertIn('quality estimate (SIM off)',text)
        self.assertNotIn('average SIM edge',text)
