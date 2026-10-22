from test_environment import install
install()
import copy
import threading
import unittest
from unittest.mock import patch
from PyQt5 import QtWidgets
from main_window import MainWindow
from preparation_status_ui import preparation_rows, PreparationStatusDialog


class PreparationStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def window(self):
        w = MainWindow()
        self.addCleanup(w.close)
        return w

    def rows(self,w):
        return {row['key']: row for row in preparation_rows(w)}

    def test_recorded_reuse_and_timing_rows_do_not_refresh_or_certify_current_inputs(self):
        w=self.window()
        w.last_csv_load_timing=dict(wait=1.25,parse=.2,preparation=2,gui_commit=.1,total=3.5)
        w.last_ownership_reuse=dict(status='reused',reason='Completed estimate matches refreshed inputs and settings.',seconds=.03)
        with patch('ownership_cache.estimate',side_effect=AssertionError('No estimation')):
            rows=self.rows(w)
        self.assertIn('Background wait: 1.25 s',rows['csv-timing']['detail'])
        self.assertIn('CSV load total: 3.50 s',rows['csv-timing']['detail'])
        self.assertEqual(rows['ownership-reuse']['state'],'Reused')
        self.assertIn('Ownership: 0.03 s',rows['ownership-reuse']['detail'])
        self.assertIn('Later player or setting edits',rows['ownership-reuse']['detail'])
        w.last_ownership_reuse=dict(status='refreshed',reason='Saved ownership unavailable or invalid; recalculated.')
        self.assertIn('invalid',self.rows(w)['ownership-reuse']['detail'])

    def test_older_and_invalid_timing_metadata_is_not_presented_as_zero_work(self):
        from preparation_audit import timing_text
        self.assertIn('not recorded',timing_text(None))
        self.assertIn('not recorded',timing_text(dict(total=float('nan'),wait=-1,parse=True)))

    def test_copy_last_build_report_uses_saved_audit_after_another_slate_is_loaded(self):
        from build_diagnostics import create_build_diagnostic, format_build_report
        from preparation_audit import capture_build_audit
        w=self.window()
        w.last_csv_load_timing=dict(total=1.25)
        w.last_ownership_reuse=dict(status='reused',reason='Matching completed estimate.',seconds=.02)
        context=dict(preparation_audit=capture_build_audit(w))
        w.last_build_diagnostic=create_build_diagnostic(context=context,timing_report={})
        expected=format_build_report(w.last_build_diagnostic)
        w.players=[dict(Name='Different slate')]
        w.last_csv_load_timing=dict(total=999)
        w.last_ownership_reuse=dict(status='refreshed',reason='Different slate.')
        with patch('ownership_cache.estimate',side_effect=AssertionError('No source work')):
            w.copy_last_build_report()
        self.assertEqual(self.app.clipboard().text(),expected)
        self.assertIn('CSV load total: 1.25 s',expected)
        self.assertNotIn('Different slate',expected)

    def test_empty_state_is_not_ready_and_collection_does_no_source_work(self):
        w = self.window()
        before = copy.deepcopy(w._current_build_recipe())
        with patch('build_preparation.discover_libraries',side_effect=AssertionError('No discovery')), \
             patch.object(w.history_refresh,'start',side_effect=AssertionError('No refresh')):
            rows = self.rows(w)
        self.assertEqual(rows['readiness']['state'],'Load a slate')
        self.assertEqual(rows['history']['state'],'No recorded refresh')
        self.assertEqual(w._current_build_recipe(),before)

    def test_automatic_library_configuration_is_not_reuse_evidence(self):
        w = self.window()
        w.combo_compute_profile.activated[str].emit('Baseline — 5 min cap')
        row = self.rows(w)['library']
        self.assertEqual(row['state'],'Automatic discovery configured')
        self.assertIn('No library is verified',row['detail'])
        w.app_settings.setValue('build/auto_library',False)
        self.assertEqual(self.rows(w)['library']['state'],'Automatic discovery configured')

    def test_selected_partial_library_requires_deep_and_stays_explicit(self):
        w = self.window()
        w._candidate_library='external.sdlib'; w._candidate_library_allow_partial=True
        row = self.rows(w)['library']
        self.assertIn('Deep + SIM required',row['state'])
        self.assertIn('Explicit partial coverage',row['detail'])
        self.assertEqual(w._candidate_library,'external.sdlib')

    def test_snapshot_replay_and_previous_build_are_separate_from_current(self):
        w = self.window(); w._snapshot_replay=True
        w.last_sim_report=dict(candidate_library=dict(library_path='older.sdfull',accepted=100))
        rows=self.rows(w)
        self.assertEqual(rows['inputs']['state'],'Snapshot replay')
        self.assertEqual(rows['library']['state'],'Automatic discovery off for replay')
        self.assertIn('previous build',rows['previous']['detail'])
        self.assertIn('older.sdfull',rows['previous']['detail'])

    def test_history_record_does_not_claim_current_freshness_and_live_stop_is_visible(self):
        w=self.window(); controller=w.history_refresh
        controller.text='History refresh complete.'
        self.assertEqual(self.rows(w)['history']['state'],'Last recorded refresh')
        self.assertIn('not proof',self.rows(w)['history']['detail'])
        controller.thread=object(); controller.stop=threading.Event()
        try:
            self.assertEqual(self.rows(w)['history']['state'],'Refresh underway')
            controller.stop.set()
            self.assertEqual(self.rows(w)['history']['state'],'Stopping safely')
        finally: controller.thread=None

    def test_requested_field_profile_and_blocker_actions_are_not_silently_applied(self):
        w=self.window(); w.chk_showdown_history.setChecked(True)
        self.assertEqual(self.rows(w)['field']['state'],'Deep + SIM required')
        w.combo_compute_profile.activated[str].emit('Baseline — 5 min cap')
        self.assertEqual(self.rows(w)['field']['state'],'Profile missing')
        w.showdown_field_profile={'recorded':'profile'}
        self.assertIn('sources rechecked',self.rows(w)['field']['state'])
        w.players=[{}]
        w.last_readiness_report=dict(title='Blocked',blockers=1,reviews=0,checks=[dict(label='Identity',status='block',summary='Conflicting IDs',action='Review sources')])
        row=self.rows(w)['readiness']
        self.assertIn('1 blockers',row['state'])
        self.assertIn('Review sources',row['detail'])

    def test_dialog_tracks_concurrent_work_and_routes_selected_tool_explicitly(self):
        w=self.window(); dialog=PreparationStatusDialog(w)
        self.addCleanup(dialog.deleteLater)
        key=w.tasks.start('Build')
        w.tasks.update(key,'Checking compatible screening evidence')
        dialog.refresh()
        rows={row['key']:row for row in dialog.rows}
        self.assertIn('1 tasks underway',rows['tasks']['state'])
        self.assertIn('Checking compatible',rows['tasks']['detail'])
        for index in range(dialog.table.topLevelItemCount()):
            item=dialog.table.topLevelItem(index)
            if item.text(0)=='Candidate library': dialog.table.setCurrentItem(item); break
        before=copy.deepcopy(w._current_build_recipe())
        dialog.open_tool()
        self.assertTrue(w.action_show_build_controls.isChecked())
        self.assertEqual(w._current_build_recipe(),before)
        self.assertIsNotNone(w.findChild(QtWidgets.QPushButton,'preparationStatusButton'))

    def test_opening_summary_does_not_refresh_or_modify_loaded_inputs(self):
        from test_nfl_logic import _fixture_players
        from preparation_status_ui import open_preparation_status
        w=self.window(); w.players=_fixture_players()
        before=copy.deepcopy(w.players)
        recipe=copy.deepcopy(w._current_build_recipe())
        with patch.object(w,'_run_live_nfl_check',side_effect=AssertionError('No implicit live refresh')), \
             patch.object(w.history_refresh,'start',side_effect=AssertionError('No implicit history refresh')), \
             patch.object(PreparationStatusDialog,'exec_',return_value=QtWidgets.QDialog.Rejected):
            open_preparation_status(w)
        self.assertEqual(w.players,before)
        self.assertEqual(w._current_build_recipe(),recipe)

    def test_current_ownership_is_separate_from_prior_reuse_and_never_estimates(self):
        w=self.window();w.players=[dict(Name='Current player',OwnershipSource='Quick roster-slot estimate')]
        w.last_ownership_reuse=dict(status='reused',reason='Earlier slate record')
        before=copy.deepcopy(w.players)
        with patch('ownership_cache.estimate',side_effect=AssertionError('No cache lookup')),patch.object(w,'_start_ownership_sim',side_effect=AssertionError('No estimate')):
            rows=self.rows(w)
        self.assertIn('Quick roster-slot estimate',rows['ownership']['detail'])
        self.assertNotIn('Earlier slate record',rows['ownership']['detail'])
        self.assertIn('Earlier slate record',rows['ownership-reuse']['detail'])
        self.assertEqual(w.players,before)

    def test_review_ownership_routes_to_original_controls_without_expanding_or_running(self):
        w=self.window();d=PreparationStatusDialog(w);self.addCleanup(d.deleteLater)
        before=copy.deepcopy(w._current_build_recipe());players=copy.deepcopy(w.players)
        for index in range(d.table.topLevelItemCount()):
            item=d.table.topLevelItem(index)
            if item.text(0)=='Current ownership':d.table.setCurrentItem(item);break
        with patch.object(w,'_start_ownership_sim',side_effect=AssertionError('No estimate')),patch.object(w,'_capture_snapshot',side_effect=AssertionError('No capture')):
            d.open_tool()
        self.assertEqual(d.result(),QtWidgets.QDialog.Accepted)
        self.assertTrue(w.action_show_build_controls.isChecked())
        self.assertTrue(w.tabs_workspace_controls.currentWidget().isAncestorOf(w.ownership_status_label))
        self.assertTrue(w.ownership_manual_panel.isHidden());self.assertEqual(w.players,players)
        self.assertEqual(w._current_build_recipe(),before)

    def test_ownership_navigation_finds_data_page_after_tab_reordering(self):
        from ownership_ui import show_ownership_controls
        w=self.window();tabs=w.tabs_workspace_controls
        index=next(i for i in range(tabs.count()) if tabs.widget(i).isAncestorOf(w.ownership_status_label))
        page=tabs.widget(index);title=tabs.tabText(index);tabs.removeTab(index);tabs.insertTab(0,page,title)
        show_ownership_controls(w)
        self.assertIs(tabs.currentWidget(),page);self.assertTrue(w.ownership_manual_panel.isHidden())
