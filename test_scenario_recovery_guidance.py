"""Recorded cache blockers route to existing audit; no automatic retirement."""
from test_environment import install
install()
import copy
import unittest
from unittest.mock import patch
from PyQt5 import QtWidgets
from scenario_recovery_guidance import storage_blocked,warming_blocked,cache_row,GUIDANCE
from preparation_status_ui import preparation_rows,PreparationStatusDialog

BLOCKED=dict(status='not saved',reason='Cache storage limit reached; protected or unrecognized files were preserved.')


class GuidanceTests(unittest.TestCase):
    def test_only_recorded_storage_failure_is_actionable(self):
        self.assertTrue(storage_blocked(BLOCKED))
        for cache in (None,{},True,dict(status='saved',reason=BLOCKED['reason']),dict(status='not saved',reason='Cache is busy'),dict(status='not saved',reason=True)):
            self.assertFalse(storage_blocked(cache))

    def test_old_and_new_warming_receipts_and_stale_flags(self):
        self.assertTrue(warming_blocked(dict(cache_status='not saved',message='Validation cache not saved. Cache storage limit reached.')))
        self.assertTrue(warming_blocked(dict(cache_status='not saved',cache_storage_blocked=True)))
        for record in ({},dict(cache_status='saved',cache_storage_blocked=True),dict(cache_status='not saved',cache_storage_blocked=1),dict(cache_status='not saved',message='Cancelled')):
            self.assertFalse(warming_blocked(record))

    def test_guidance_never_certifies_legacy_identity_or_current_storage(self):
        row=cache_row(BLOCKED,'Last build')
        self.assertEqual(row['state'],'Storage blocked (recorded)')
        for text in ('previous recorded outcome','not current-slate validation','if the audit identifies legacy','verifies a recoverable copy','Retry / Resume'):self.assertIn(text,row['detail'])
        self.assertEqual(row['action'],'Review cache storage')

    def test_successes_and_other_cache_failures_do_not_claim_storage_block(self):
        for status in ('saved','reused','disabled','unavailable'):
            row=cache_row(dict(status=status,reason='Changed model'),'Last build')
            self.assertNotIn('Storage blocked',row['state']);self.assertNotIn('Archive and free',row['detail'])

    def test_guidance_inputs_are_not_modified(self):
        before=copy.deepcopy(BLOCKED);cache_row(BLOCKED,'Last build');self.assertEqual(before,BLOCKED)


class RecoveryNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    def window(self):
        from main_window import MainWindow
        w=MainWindow();w.idle_backup.timer.stop();w.slate_discovery.timer.stop();self.addCleanup(w.close);return w
    def test_preparation_summary_uses_recorded_data_without_cache_scan_or_archive(self):
        w=self.window();w.last_sim_report=dict(scenario_cache=copy.deepcopy(BLOCKED))
        before=copy.deepcopy(w.players);recipe=copy.deepcopy(w._current_build_recipe())
        with patch('scenario_cache_storage.inventory',side_effect=AssertionError('No scan')),patch('legacy_scenario_archive.validate_and_archive',side_effect=AssertionError('No archive')):
            row=next(r for r in preparation_rows(w) if r['key']=='scenario-cache')
        self.assertEqual(row['state'],'Storage blocked (recorded)');self.assertEqual(w.players,before);self.assertEqual(w._current_build_recipe(),recipe)

    def test_current_record_without_cache_does_not_borrow_older_blocker(self):
        w=self.window();w.last_build_diagnostic=dict(sim=dict(scenario_cache=BLOCKED));w.last_sim_report=dict(scenarios=100)
        self.assertNotIn('scenario-cache',{r['key'] for r in preparation_rows(w)})
        w.last_sim_report={};self.assertIn('scenario-cache',{r['key'] for r in preparation_rows(w)})

    def test_smart_preparation_outcome_has_separate_historical_row(self):
        w=self.window();w.slate_discovery.result=dict(scenario_preparation=dict(cache_status='not saved',cache_storage_blocked=True))
        row=next(r for r in preparation_rows(w) if r['key']=='scenario-warming')
        self.assertIn('Last Smart preparation',row['detail']);self.assertEqual(row['action'],'Review cache storage')

    def test_selected_tool_opens_audit_without_archiving_or_retrying(self):
        w=self.window();w.last_sim_report=dict(scenario_cache=BLOCKED);dialog=PreparationStatusDialog(w);self.addCleanup(dialog.deleteLater)
        for i in range(dialog.table.topLevelItemCount()):
            item=dialog.table.topLevelItem(i)
            if item.text(0)=='Scenario replay — last build':dialog.table.setCurrentItem(item);break
        with patch('storage_audit_ui.open_storage_audit') as opened,patch('legacy_scenario_archive.validate_and_archive') as archive,patch.object(w,'on_prepare_this_slate') as retry:
            dialog.open_tool()
        opened.assert_called_once_with(w);archive.assert_not_called();retry.assert_not_called()

    def picker(self,w,record):
        from slate_picker import SlatePicker
        row=dict(path='synthetic-salary.csv',digest='synthetic',sport='NFL',kind='showdown',label='Synthetic slate',date='2026-10-24',preparation=dict(scenario_preparation=record))
        with patch('slate_picker.rows',return_value=[row]):dialog=SlatePicker(w.slate_discovery,w)
        self.addCleanup(dialog.deleteLater);dialog.table.selectRow(0);return dialog

    def test_slate_picker_recovery_closes_without_loading_csv_or_resuming(self):
        w=self.window();before=copy.deepcopy(w.players);dialog=self.picker(w,dict(cache_status='not saved',cache_storage_blocked=True))
        self.assertFalse(dialog.cache_storage.isHidden());self.assertIn(GUIDANCE,dialog.details.toPlainText())
        with patch('storage_audit_ui.open_storage_audit') as opened,patch.object(w.slate_discovery,'resume') as resume:
            dialog.review_cache_storage()
        opened.assert_called_once_with(w);resume.assert_not_called();self.assertEqual(dialog.path,'');self.assertEqual(dialog.result(),QtWidgets.QDialog.Rejected);self.assertEqual(w.players,before)

    def test_new_success_hides_action_and_stale_click_is_ignored(self):
        w=self.window();dialog=self.picker(w,dict(cache_status='saved',cache_storage_blocked=True))
        self.assertTrue(dialog.cache_storage.isHidden())
        with patch('storage_audit_ui.open_storage_audit') as opened:dialog.review_cache_storage()
        opened.assert_not_called()

    def test_empty_selection_never_offers_recovery(self):
        w=self.window();dialog=self.picker(w,{})
        dialog.table.clearSelection();dialog.table.setCurrentCell(-1,-1);dialog.update_details()
        self.assertTrue(dialog.cache_storage.isHidden())
        with patch('storage_audit_ui.open_storage_audit') as opened:dialog.review_cache_storage()
        opened.assert_not_called()
