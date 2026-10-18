from test_environment import install
install()
import copy
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PyQt5 import QtCore, QtWidgets
from build_snapshots import create_snapshot
from preparation_readiness import ReadinessRecords,signature,current_view,SETTING,LIMIT
from test_showdown_library import players
from main_window import MainWindow
from portfolio_rules import player_key


def snapshot(rows=None, rules=None):
    return create_snapshot(rows or players(),dict(sport='NFL',contest_kind='showdown',salary_cap=50000,
        requested_lineups=2,nfl_sim_scenarios=250,salary_strategy='Flexible',deep_compute=dict(candidates=16,field=20,screening=250)),rules or {})


class ReadinessRecordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name).resolve()
        self.settings=QtCore.QSettings(str(self.root/'records.ini'),QtCore.QSettings.IniFormat)
        self.store=ReadinessRecords(self.settings);self.snapshot=snapshot();self.sig=signature(self.snapshot)

    def begin(self,**kw):return self.store.begin(self.snapshot,**kw)
    def view(self,snap=None):return self.store.view(signature(snap or self.snapshot))

    def test_absent_record_does_not_claim_coverage_or_readiness(self):
        view=self.view();self.assertEqual(view['state'],'Not recorded');self.assertEqual(view['coverage'],'Unknown')
        self.assertNotIn('100%',view['detail'])

    def test_partial_roster_denominator_and_partitions_are_recorded_separately(self):
        key=self.begin(path='library.sdfull')
        self.store.update(key,dict(checked=21,saved=18,complete=False,completed_partitions=1,partition_count=8))
        self.store.finish(key,'Paused','storage budget',dict(pause_reason='storage budget'))
        view=self.view();self.assertIn('21/168',view['coverage']);self.assertIn('12.5%',view['coverage'])
        self.assertIn('1/8 Captain partitions',view['coverage']);self.assertNotIn('Complete',view['coverage'])
        self.assertIn('storage budget',view['reason'])

    def test_unknown_full_screening_total_and_batch_are_not_completed_progress(self):
        key=self.begin(full_screen=True)
        self.store.update(key,dict(screened=10,screening_total=None,screening_complete=False,
            batch_candidates=8,batch_scenarios=125,batch_scenarios_total=250,screening_phase='Simulating batch'))
        view=self.view();self.assertIn('10/unknown',view['screening']);self.assertNotIn('%',view['screening'])
        self.assertIn('125/250 scenarios (not committed',view['detail']);self.assertIn('All currently legal',view['detail'])
        self.store.finish(key,'Completed','done',dict(screened=18,screening_total=18,screening_complete=True))
        self.assertNotIn('not committed',self.view()['detail']);self.assertIn('100.0%',self.view()['screening'])

    def test_reused_bank_is_not_described_as_exhaustive_screening(self):
        key=self.begin();self.store.finish(key,'Completed','done',dict(complete=True,screening_complete=True,screening_reused=16))
        view=self.view();self.assertIn('16/16',view['screening']);self.assertIn('Reusable Deep bank',view['detail'])
        self.assertNotIn('All currently legal rosters',view['detail'])

    def test_forecasts_and_salary_changes_distinguish_rosters_from_screening(self):
        key=self.begin();self.store.finish(key,'Completed','done',dict(complete=True,screening_complete=True,screened=16,screening_total=16))
        rows=players();rows[0]['FlexProjection']+=2
        changed=self.view(snapshot(rows));self.assertEqual(changed['match'],'Roster inputs match; screening inputs changed')
        self.assertEqual(changed['state'],'Needs update');self.assertIn('Complete (recorded)',changed['coverage'])
        rows[0]['FlexSalary']+=1
        self.assertEqual(self.view(snapshot(rows))['match'],'Roster inputs / Captain coverage changed')

    def test_observation_times_only_reuse_existing_normalization(self):
        key=self.begin();self.store.finish(key,'Completed','done')
        rows=players();rows[0]['LiveStatusUpdatedAt']='later';rows[0]['LiveStatusChanged']=True
        self.assertEqual(self.view(snapshot(rows))['match'],'Matches captured inputs')
        rows[0]['UnrecognizedTimestamp']='later'
        self.assertIn('screening inputs changed',self.view(snapshot(rows))['match'])

    def test_pool_expansion_and_player_order_do_not_hide_screening_changes(self):
        rows=players();rows[0]['LockCpt']=True;self.snapshot=snapshot(rows)
        key=self.begin(path='scoped.sdlib',scope=[player_key(rows[0])]);self.store.finish(key,'Completed','done',dict(complete=True))
        self.assertEqual(self.view(snapshot(players()))['match'],'Roster inputs / Captain coverage changed')
        key=self.store.begin(snapshot(players()));self.store.finish(key,'Completed','done')
        self.assertEqual(self.view(snapshot(list(reversed(players()))))['match'],'Roster inputs match; screening inputs changed')

    def test_portfolio_only_zero_qb_limit_is_not_a_screening_change(self):
        key=self.begin();self.store.finish(key,'Completed','done')
        self.assertEqual(self.view(snapshot(rules=dict(max_zero_qb_pct=0)))['match'],'Matches captured inputs')
        self.assertIn('screening inputs changed',self.view(snapshot(rules=dict(min_unique=3)))['match'])

    def test_other_game_does_not_inherit_previous_slate_progress(self):
        key=self.begin();self.store.finish(key,'Completed','done',dict(checked=168,complete=True))
        rows=players()
        for row in rows:row['GameInfo']='A@B 10/18/2026 08:15PM ET'
        view=self.view(snapshot(rows));self.assertEqual(view['state'],'Not recorded');self.assertNotIn('168',view['detail'])

    def test_restart_retains_recorded_progress_without_running_or_verifying_work(self):
        key=self.begin();self.store.update(key,dict(checked=42,saved=39,complete=False));self.store.save()
        before=str(self.settings.value(SETTING))
        restored=ReadinessRecords(self.settings);view=restored.view(self.sig)
        self.assertEqual(view['state'],'Recorded');self.assertIn('42/168',view['coverage']);self.assertFalse(restored.active)
        self.assertIn('no job was resumed',view['reason']);self.assertEqual(str(self.settings.value(SETTING)),before)
        self.assertIn('files, model version, historical sources',view['detail'])

    def test_record_bound_and_overlapping_workers_do_not_mix_counts(self):
        first=self.begin(path='first.sdlib');second=self.begin(path='second.sdlib')
        self.store.update(first,dict(checked=21));self.store.update(second,dict(checked=42));self.store.finish(first,'Paused','user')
        self.assertEqual(self.store.get(second)['checked'],42);self.assertIn(second,self.store.active)
        for _ in range(LIMIT+4):
            key=self.begin();self.store.finish(key,'Completed','done')
        self.assertLessEqual(len(self.store.records),LIMIT+1);self.assertIsNotNone(self.store.get(second))

    def test_malformed_and_oversized_persistence_is_not_treated_as_evidence(self):
        for raw in ('{broken','{}','x'*300000,json.dumps([dict(id='bad')])):
            self.settings.setValue(SETTING,raw);restored=ReadinessRecords(self.settings)
            self.assertEqual(restored.view(self.sig)['state'],'Not recorded')

    def test_captured_metadata_does_not_mutate_inputs_or_settings_on_read(self):
        before=copy.deepcopy(self.snapshot);key=self.begin();raw=self.settings.value(SETTING)
        for _ in range(3):self.view()
        self.assertEqual(self.snapshot,before);self.assertEqual(self.settings.value(SETTING),raw)

    def test_roster_limit_pause_reason_comes_from_actual_stop(self):
        from showdown_library import prepare
        result=prepare(self.root/'limited.sdlib',players(),max_candidates=4)
        self.assertEqual(result['pause_reason'],'stored roster limit');self.assertEqual(result['saved'],4)
        self.assertFalse(result['complete'])

    def test_deadline_and_cancel_pause_reasons_do_not_require_extra_clock_reads(self):
        from showdown_library import prepare
        with patch('showdown_library.time.monotonic',side_effect=[0,2]):
            timed=prepare(self.root/'timed.sdlib',players(),seconds=1)
        self.assertEqual(timed['pause_reason'],'time allowance reached')
        stopped=prepare(self.root/'cancelled.sdlib',players(),cancelled=lambda:True)
        self.assertEqual(stopped['pause_reason'],'cancelled at checkpoint');self.assertEqual(stopped['checked'],0)

    def test_screening_cancel_reason_counts_committed_batches_only(self):
        from showdown_library import prepare
        from showdown_screening import prepare_screening
        rows=players()
        for i,p in enumerate(rows):p['FlexSalary']=6500+i*200;p['CptSalary']=p['FlexSalary']*1.5
        library=self.root/'screen.sdlib';prepare(library,rows);stop=[False]
        result=prepare_screening(library,rows,limit=16,salary_strategy='Flexible',batch_size=5,
            screening=dict(scenarios=250,field_lineup_count=20,seed=73129),folder=self.root/'cache',
            cancelled=lambda:stop[0],progress=lambda value:stop.__setitem__(0,True))
        self.assertEqual(result['screened'],5);self.assertFalse(result['screening_complete'])
        self.assertEqual(result['pause_reason'],'cancelled at checkpoint')


    def test_restart_preserves_attention_reason_without_resuming_failed_work(self):
        key=self.begin();self.store.finish(key,'Needs attention','History source changed; review the mapping.')
        restored=ReadinessRecords(self.settings);view=restored.view(self.sig)
        self.assertEqual(view['state'],'Needs attention');self.assertIn('History source changed',view['reason'])
        self.assertIn('no job was resumed',view['reason']);self.assertFalse(restored.active)



class ReadinessSummaryUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name).resolve()
        env=patch.dict(os.environ,{'DFS_OPTIMIZER_DATA_DIR':str(self.root/'data')});env.start();self.addCleanup(env.stop)
        self.w=MainWindow();self.addCleanup(self.w.close)
        self.w.idle_backup.timer.stop();self.w.background_queue.timer.stop();self.w.slate_readiness_timer.stop()
        self.clock=[0.0];self.w.idle_backup.clock=lambda:self.clock[0];self.w.idle_backup.last_input=0
        self.w.players=players()
        for i,p in enumerate(self.w.players):p['FlexSalary']=6500+i*200;p['CptSalary']=p['FlexSalary']*1.5
        self.w.spin_sd.setValue(2);self.w.combo_salary_strategy.setCurrentText('Flexible')
        self.w.spin_nfl_sim_scenarios.setValue(250);self.w.deep_compute_settings.update(candidates=16,field=20,screening=250)
        self.snapshot=self.w._capture_snapshot(calibration={},contest={})

    def wait(self,predicate):
        end=time.monotonic()+15
        while not predicate() and time.monotonic()<end:self.app.processEvents();time.sleep(.005)
        self.assertTrue(predicate())

    def test_summary_is_source_free_and_does_not_capture_or_mutate_current_inputs(self):
        key=self.w.slate_readiness.begin(self.snapshot);before=copy.deepcopy(self.w.players)
        with patch.object(self.w,'_capture_snapshot',side_effect=AssertionError('no capture')),patch('sqlite3.connect',side_effect=AssertionError('no database')),patch('candidate_library.code_id',side_effect=AssertionError('no code scan')),patch('slate_preparation.plan_preparation',side_effect=AssertionError('no planning')):
            view=current_view(self.w)
            self.w.slate_readiness_timer.timeout.emit()
        self.assertEqual(view['match'],'Matches captured inputs');self.assertEqual(self.w.players,before)
        self.assertIn('Matches captured inputs',self.w.lbl_slate_preparation.text())

    def test_queue_wait_reasons_cover_idle_foreground_and_backup(self):
        self.w.idle_preparation.enqueue(self.root/'queued.sdlib',self.snapshot,60,1000,None,8*1024**3,False,False)
        self.assertIn('idle app time',current_view(self.w)['reason'])
        key=self.w.tasks.start('Generate');self.assertIn('foreground',current_view(self.w)['reason'])
        self.w.tasks.finish(key,'Completed')
        queue=self.w.background_queue;queue.active=dict(key='backup',title='History backup',priority=20,owned=set(),cancel=None,yielding=False)
        try:self.assertIn('History backup',current_view(self.w)['reason'])
        finally:queue.active=None
        self.w.idle_preparation.cancel();self.assertIn('user',current_view(self.w)['reason'])

    def test_real_automatic_work_records_completed_coverage_and_screening(self):
        self.w.idle_preparation.prepare_slate(self.snapshot,[],self.root/'libraries')
        self.clock[0]=61;self.w.background_queue.pump()
        self.wait(lambda:self.w.idle_preparation.thread is None)
        view=current_view(self.w);self.assertEqual(view['state'],'Completed',view['detail'])
        self.assertIn('168/168',view['coverage']);self.assertIn('Complete (recorded)',view['screening'])
        self.assertEqual(view['match'],'Matches captured inputs')
        self.w.players[0]['FlexProjection']+=1
        view=current_view(self.w);self.assertEqual(view['state'],'Needs update');self.assertIn('screening inputs changed',view['match'])

    def test_preparation_dialog_selects_this_slate_and_reading_does_not_start_work(self):
        key=self.w.slate_readiness.begin(self.snapshot);self.w.slate_readiness.update(key,dict(checked=21,saved=18))
        from preparation_status_ui import PreparationStatusDialog
        with patch('slate_preparation.plan_preparation',side_effect=AssertionError('no work on open')):
            dialog=PreparationStatusDialog(self.w);self.addCleanup(dialog.close)
            dialog.show();self.app.processEvents()
            self.assertIn('Coverage:',dialog.details.toPlainText());self.assertIn('Screening:',dialog.details.toPlainText())
            self.assertIn('Matches captured inputs',dialog.details.toPlainText())
            self.assertIsNone(self.w.idle_preparation.thread)
        dialog.close();self.assertFalse(dialog.timer.isActive())

    def test_foreground_advanced_preparation_also_updates_readiness(self):
        from showdown_library_ui import PreparationDialog
        dialog=PreparationDialog(self.snapshot,self.w);self.addCleanup(dialog.close)
        dialog.path.setText(str(self.root/'advanced.sdlib'));dialog.begin()
        self.wait(lambda:dialog.thread is None)
        view=current_view(self.w);self.assertIn('Complete (recorded)',view['coverage'])
        self.assertEqual(view['screening'],'Not requested');self.assertEqual(view['state'],'Completed')

    def test_existing_audit_is_unchanged_and_other_slate_is_not_shown(self):
        from slate_readiness import audit_slate
        self.assertTrue(callable(audit_slate))
        key=self.w.slate_readiness.begin(self.snapshot);self.w.slate_readiness.finish(key,'Completed','done',dict(checked=168,complete=True))
        for p in self.w.players:p['GameInfo']='A@B 10/18/2026 08:15PM ET'
        self.assertEqual(current_view(self.w)['state'],'Not recorded')
