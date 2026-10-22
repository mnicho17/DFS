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

    def test_one_preparation_shortcut_opens_read_only_screen_without_capturing(self):
        w=self.window()
        buttons=w.findChildren(QtWidgets.QPushButton,'preparationStatusButton')
        self.assertEqual(len(buttons),1);self.assertIs(w.btn_prepare_slate,buttons[0])
        self.assertTrue(w.simple_build_panel.isAncestorOf(buttons[0]))
        with patch.object(w,'_capture_snapshot',side_effect=AssertionError('Opening must not capture')),patch.object(PreparationStatusDialog,'exec_',return_value=QtWidgets.QDialog.Rejected) as opened:
            buttons[0].click()
        opened.assert_called_once();self.assertIsNone(w.idle_preparation.request_data)
        actions=[a.text() for menu in w.findChildren(QtWidgets.QMenu) for a in menu.actions()]
        self.assertIn('Preparation...',actions)
        for old in ('Prepare this slate','Prepare Showdown Roster Library...','Long Search / Resume...'):
            self.assertNotIn(old,actions)
    def test_summary_exposes_coverage_screening_match_and_waiting_without_scans(self):
        w=self.window()
        with patch('build_preparation.discover_libraries',side_effect=AssertionError('No scan')):
            d=PreparationStatusDialog(w)
        self.addCleanup(d.deleteLater);self.addCleanup(d.reject)
        for label in ('Roster coverage:','Screening:','Inputs:','Paused / waiting:'):self.assertIn(label,d.slate_summary.text())
        self.assertFalse(d.prepare.isEnabled());self.assertIn('Load an NFL',d.next_step.text())
    def test_retry_recaptures_current_inputs_through_existing_action_once(self):
        from preparation_readiness import empty
        from test_nfl_logic import _fixture_players
        w=self.window();w.players=_fixture_players();w.idle_preparation.paused=True
        with patch('preparation_readiness.current_view',return_value=empty('Paused','Saved checkpoint')):
            d=PreparationStatusDialog(w)
            self.addCleanup(d.deleteLater);self.addCleanup(d.reject)
            self.assertEqual(d.prepare.text(),'Retry / Resume')
            with patch.object(w,'on_prepare_this_slate') as prepare:d.prepare.click()
            prepare.assert_called_once()
    def test_inflight_or_busy_work_cannot_start_second_preparation(self):
        from test_nfl_logic import _fixture_players
        w=self.window();w.players=_fixture_players();d=PreparationStatusDialog(w)
        self.addCleanup(d.deleteLater);self.addCleanup(d.reject)
        with patch.object(w,'_snapshot_busy',return_value=True),patch.object(w,'on_prepare_this_slate') as prepare:
            d.refresh();self.assertFalse(d.prepare.isEnabled());d.prepare_current();prepare.assert_not_called()
        w.idle_preparation.request_data={'captured':'other slate'}
        try:
            with patch.object(w,'on_prepare_this_slate') as prepare:
                d.refresh();self.assertFalse(d.prepare.isEnabled());d.prepare_current();prepare.assert_not_called()
                self.assertIn('captured preparation job',d.next_step.text())
        finally:w.idle_preparation.request_data=None
    def test_snapshot_and_classic_never_silently_queue_or_change_recipe(self):
        from test_nfl_logic import _fixture_players
        w=self.window();w.players=_fixture_players();d=PreparationStatusDialog(w)
        self.addCleanup(d.deleteLater);self.addCleanup(d.reject)
        for replay,index in ((True,0),(False,1)):
            w._snapshot_replay=replay;w.tabs_lineups.setCurrentIndex(index)
            before=copy.deepcopy(w._current_build_recipe())
            with patch.object(w,'on_prepare_this_slate') as prepare:
                d.refresh();self.assertFalse(d.prepare.isEnabled());d.prepare_current();prepare.assert_not_called()
            self.assertEqual(w._current_build_recipe(),before)
    def test_run_now_targets_only_existing_queued_preparation_and_keeps_queue_guards(self):
        w=self.window();d=PreparationStatusDialog(w)
        self.addCleanup(d.deleteLater);self.addCleanup(d.reject)
        queue=w.background_queue
        with patch.object(queue,'run_now') as run:
            d.run_queued();run.assert_not_called()
            queue.request('preparation','Preparation',30,lambda:None,lambda:None,ready=lambda:False)
            task=queue.pending['preparation']['task']
            from preparation_readiness import empty
            view=empty('Queued','Waiting');view.update(record={'id':'captured'},match='Matches captured inputs')
            w.idle_preparation.readiness_key='captured'
            try:
                with patch('preparation_readiness.current_view',return_value=view):
                    d.refresh();self.assertTrue(d.run_now.isEnabled());d.run_queued();run.assert_called_once_with(task)
                    run.reset_mock();view['match']='Roster inputs / Captain coverage changed'
                    d.refresh();self.assertFalse(d.run_now.isEnabled());d.run_queued();run.assert_not_called()
            finally:queue.cancel('preparation')
    def test_specialist_tools_remain_explicit_and_route_to_existing_handlers(self):
        w=self.window()
        for name in ('on_prepare_showdown_library','on_long_search'):
            d=PreparationStatusDialog(w);self.addCleanup(d.deleteLater)
            before=copy.deepcopy(w._current_build_recipe())
            with patch.object(w,name) as handler:d.specialist(name)
            handler.assert_called_once();self.assertEqual(w._current_build_recipe(),before)

    def test_ui_prepare_queues_one_frozen_request_and_updates_without_extra_work(self):
        from test_nfl_logic import _fixture_players
        from build_snapshots import create_snapshot
        w=self.window();w.players=_fixture_players()
        snapshot=create_snapshot(w.players,w._current_build_recipe(),w._portfolio_rules())
        before=copy.deepcopy(snapshot)
        d=PreparationStatusDialog(w);self.addCleanup(d.deleteLater);self.addCleanup(d.reject)
        with patch.object(w,'_capture_snapshot',return_value=snapshot) as capture,patch('slate_preparation.plan_preparation',side_effect=AssertionError('No GUI scan')):
            d.prepare.click();first=w.idle_preparation.request_data;d.prepare.click()
            self.assertIs(w.idle_preparation.request_data,first);capture.assert_called_once()
        self.assertIsNotNone(first);self.assertFalse(d.prepare.isEnabled())
        snapshot['inputs']['players'][0]['Name']='Later edit'
        self.assertEqual(first['snapshot'],before)

    def test_tasks_navigation_closes_modal_preparation_and_stops_refresh_timer(self):
        w=self.window();d=PreparationStatusDialog(w);self.addCleanup(d.deleteLater)
        with patch.object(w.task_dashboard,'show') as show:d.tasks.click()
        show.assert_called_once();self.assertEqual(d.result(),QtWidgets.QDialog.Accepted)
        self.assertFalse(d.timer.isActive())
