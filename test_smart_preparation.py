from test_environment import install
install()
import datetime as dt
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PyQt5 import QtWidgets
from slate_catalog import scan, rows
from smart_preparation import prepare_one
from test_showdown_library import players


class SmartPreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.folder=self.root/'salaries';self.folder.mkdir()
        self.db=self.root/'catalog.sqlite';self.output=self.root/'candidates'
        self.path=self.folder/'slate.csv'
        self.path.write_text('Position,Name,ID,Roster Position,Salary,Game Info,TeamAbbrev\n'
            'WR,Example,123,FLEX,5000,SEA@DEN 10/20/2026 08:15PM ET,SEA\n'
            'WR,Example,124,CPT,7500,SEA@DEN 10/20/2026 08:15PM ET,SEA\n')
        scan(self.db,self.folder)
        self.today=dt.date(2026,10,19)
        self.ps=players()
        for i,p in enumerate(self.ps):
            p.update(FlexSalary=6000+i*100,CptSalary=9000+i*150,GameInfo='SEA@DEN 10/20/2026 08:15PM ET')
        from csv_loading import CsvLoadWorker
        def prepared(worker):worker.finished.emit(dict(players=copy.deepcopy(self.ps),live_summary={}))
        mock=patch.object(CsvLoadWorker,'run',prepared);mock.start();self.addCleanup(mock.stop)
        mock=patch('candidate_library.code_id',return_value='synthetic-code');mock.start();self.addCleanup(mock.stop)
        mock=patch('candidate_library.preparation_model_id',return_value='synthetic-code');mock.start();self.addCleanup(mock.stop)
        self.own=dict(total={'example':100},cpt={},flex={})
        mock=patch('main_window.OwnershipSimWorker._simulate',return_value=self.own)
        self.estimate=mock.start();self.addCleanup(mock.stop)

    def run_job(self,**kw):
        return prepare_one(self.db,self.folder,self.output,kw.pop('cancelled',lambda:False),lambda text:None,now=self.today,seconds=.1,**kw)

    def test_transient_receipt_lock_keeps_preparation_and_checkpoint_resume_working(self):
        import os
        original=os.replace
        denied=[]
        def replace(source,target):
            if Path(target).parent.name=='discovered' and not denied:
                denied.append(str(target))
                error=PermissionError('Synthetic receipt sharing denial')
                error.winerror=5
                raise error
            return original(source,target)
        with patch('smart_preparation.os.replace',side_effect=replace),patch('smart_preparation.time.sleep'):
            first=self.run_job()
        self.assertEqual(len(denied),1)
        self.assertEqual(first['state'],'Queued')
        second=self.run_job()
        self.assertEqual(second['state'],'Queued')
        self.assertTrue(second['checkpoint_reused'])
        self.assertGreaterEqual(second['count'],first['count'])
        self.assertEqual(self.estimate.call_count,1)
        self.assertEqual(list((self.output/'discovered').glob('*.tmp')),[])

    def test_real_library_partial_checkpoint_resumes_without_repeating_ownership(self):
        from candidate_library import metadata
        result=self.run_job()
        self.assertEqual(result['state'],'Queued')
        self.assertTrue(Path(result['library']).exists())
        self.assertFalse(result['checkpoint_reused'])
        self.assertIn('ownership',result['phase_timings'])
        self.assertIn('candidate_search',result['phase_timings'])
        self.assertIn('reason',result['ownership_reuse'])
        count=metadata(result['library'])['count']
        second=self.run_job()
        self.assertTrue(second['checkpoint_reused'])
        self.assertGreaterEqual(second['phase_timings']['candidate_search'],result['phase_timings']['candidate_search'])
        self.assertEqual(self.estimate.call_count,1)
        self.assertGreaterEqual(metadata(second['library'])['count'],count)
        self.assertEqual(len(list((self.output/'discovered').glob('*.json'))),1)

    def test_completed_smart_library_handoff_bypasses_directory_limit_and_still_validates(self):
        from main_window import LineupBuildWorker
        from build_workflow import prepare_build
        from build_preparation import discover_libraries
        actual=self.root/'slate-catalog.sqlite'
        self.db.rename(actual);self.db=actual
        with patch('smart_preparation.TARGET',1):result=self.run_job()
        self.assertEqual(result['state'],'Prepared candidates')
        library=Path(result['library'])
        junk=[self.output/f'old-{i}.txt' for i in range(49)]
        events=[]
        before=self.db.read_bytes()
        with patch.object(Path,'iterdir',return_value=iter(junk)):
            found=list(discover_libraries((),self.output,self.ps,'showdown',50000,audit=events.append))
        self.assertEqual(found,[str(library.resolve())])
        self.assertIn('directory_limit',events)
        self.assertEqual(self.db.read_bytes(),before)
        worker=LineupBuildWorker(copy.deepcopy(self.ps),kind='showdown',num_lineups=1,
            salary_cap=50000,sim_enabled=True,compute_mode='Deep',library_folder=str(self.output))
        with patch.object(Path,'iterdir',return_value=iter(junk)):
            self.assertTrue(prepare_build(worker))
        self.assertTrue(worker.library_candidates)
        self.assertTrue(worker.library_report['automatic_discovery'])
        self.assertEqual(worker.preparation.rows[2]['discovery']['accepted'],1)
        # A discovery hint never bypasses the model gate in the real loader.
        with patch('candidate_library.preparation_model_id',return_value='changed-model'):
            worker=LineupBuildWorker(copy.deepcopy(self.ps),kind='showdown',num_lineups=1,
                salary_cap=50000,sim_enabled=True,compute_mode='Deep',library_folder=str(self.output))
            self.assertTrue(prepare_build(worker))
        self.assertFalse(worker.candidate_library)
        self.assertIn('App/model incompatibility',worker.preparation.rows[2]['detail'])

    def test_idle_warming_follows_completed_candidates_and_skips_recorded_artifact_on_restart(self):
        from smart_scenario_preparation import request_id
        cache=self.root/'scenario-cache';cache.mkdir()
        key='a'*64;(cache/(key+'.sqlite')).write_bytes(b'scheduling hint only; production still validates')
        outcome=dict(state='Completed',message='Completed cache recorded.',cache=dict(status='saved',
            cache_id=key,requested_scenarios=5000,reused_scenarios=0))
        with patch('smart_preparation.TARGET',1),patch('scenario_cache.cache_folder',return_value=cache), \
             patch('scenario_preparation.scenario_preparation_result',return_value=outcome) as warming:
            first=self.run_job(warm_scenarios=True)
            warming.assert_not_called()
            second=self.run_job(warm_scenarios=True)
            self.assertEqual(second['state'],'Prepared candidates')
            self.assertEqual(second['scenario_preparation']['state'],'Completed')
            self.assertEqual(second['scenario_preparation']['scope'],'Saved Smart setup')
            self.assertNotIn('library',self.run_job(warm_scenarios=True))
            self.assertEqual(warming.call_count,1)
            (cache/(key+'.sqlite')).unlink()
            self.run_job(warm_scenarios=True)
            self.assertEqual(warming.call_count,2)
        self.assertEqual(self.estimate.call_count,1)

    def test_restart_retains_last_captured_warming_setup_without_default_rewarming(self):
        from build_snapshots import create_snapshot
        current=create_snapshot(self.ps,dict(sport='NFL',contest_kind='showdown',nfl_sim_scenarios=10000),{})
        cache=self.root/'scenario-cache';cache.mkdir();key='b'*64
        (cache/(key+'.sqlite')).write_bytes(b'local scheduling receipt only')
        outcome=dict(state='Completed',message='Completed cache recorded.',cache=dict(status='saved',cache_id=key,requested_scenarios=10000))
        with patch('smart_preparation.TARGET',1),patch('scenario_cache.cache_folder',return_value=cache), \
             patch('scenario_preparation.scenario_preparation_result',return_value=outcome) as work:
            self.run_job(warm_scenarios=True)
            self.run_job(warm_scenarios=True,scenario_snapshot=current)
            self.assertNotIn('library',self.run_job(warm_scenarios=True))
            self.assertEqual(work.call_count,1)
            (cache/(key+'.sqlite')).unlink()
            result=self.run_job(warm_scenarios=True)
            self.assertEqual(result['scenario_preparation']['scope'],'Last captured setup')
            self.assertEqual(work.call_args.args[1]['inputs']['recipe']['nfl_sim_scenarios'],10000)

    def test_candidate_work_for_other_slates_precedes_scenario_warming(self):
        other=self.folder/'other.csv';other.write_text(self.path.read_text()+'\n')
        scan(self.db,self.folder)
        with patch('smart_preparation.TARGET',1), \
             patch('scenario_preparation.scenario_preparation_result') as warming:
            self.run_job(warm_scenarios=True)
            self.run_job(warm_scenarios=True)
        warming.assert_not_called()

    def test_completed_target_is_skipped_on_restart(self):
        with patch('smart_preparation.TARGET',1):
            result=self.run_job()
            self.assertEqual(result['state'],'Prepared candidates')
            from slate_catalog import connect
            with connect(self.db) as con:con.execute('DELETE FROM prepared_library_lookup')
            second=self.run_job()
            with connect(self.db) as con:
                self.assertEqual(con.execute('SELECT COUNT(*) FROM prepared_library_lookup').fetchone()[0],1)
        self.assertNotIn('library',second)
        self.assertEqual(self.estimate.call_count,1)

    def test_cancellation_before_start_does_not_estimate_or_create_library(self):
        result=self.run_job(cancelled=lambda:True)
        self.assertTrue(result['cancelled']);self.estimate.assert_not_called()
        self.assertFalse(list(self.output.glob('*.dfslib')))

    def test_missing_or_changed_source_is_never_prepared_from_stale_catalog(self):
        self.path.write_text(self.path.read_text()+'\n')
        result=self.run_job()
        self.assertIn('changed',result['preparation'])
        self.estimate.assert_not_called()
        self.assertFalse(list(self.output.glob('*.dfslib')))

    def test_error_requires_attention_instead_of_repeated_ownership_work(self):
        self.estimate.side_effect=ValueError('No valid ownership entries')
        self.assertIn('error',self.run_job())
        self.assertNotIn('error',self.run_job())
        self.assertEqual(self.estimate.call_count,1)

    def test_more_than_seven_days_ahead_is_not_queued(self):
        self.today=dt.date(2026,10,1)
        self.assertNotIn('library',self.run_job())
        self.estimate.assert_not_called()

    def test_storage_pause_resumes_after_allocation_increase_without_reset(self):
        self.output.mkdir()
        saved=self.output/'existing.bin';saved.write_bytes(b'keep')
        with patch('smart_preparation.BUDGET',1):
            first=self.run_job()
        self.assertEqual(first['state'],'Storage limit')
        second=self.run_job()
        self.assertEqual(second['state'],'Queued')
        self.assertEqual(second['attempt'],first['attempt'])
        self.assertEqual(saved.read_bytes(),b'keep')

    def test_actual_drive_reserve_blocks_even_with_large_allocation(self):
        from types import SimpleNamespace
        with patch('smart_preparation.shutil.disk_usage',return_value=SimpleNamespace(free=1024)),patch('candidate_library.run_search') as search:
            result=self.run_job()
        self.assertEqual(result['state'],'Storage limit')
        self.assertIn('5 GiB',result['pause_reason'])
        search.assert_not_called()

    def test_shared_storage_limit_does_not_delete_existing_data(self):
        self.output.mkdir();existing=self.output/'manual-library';existing.write_bytes(b'keep')
        with patch('smart_preparation.BUDGET',1):result=self.run_job()
        self.assertIn('budget',result['preparation'])
        self.assertEqual(existing.read_bytes(),b'keep')
        self.estimate.assert_not_called()


    def digest(self):
        return rows(self.db,self.folder,all_dates=True)[0]['digest']

    def checkpoint(self):
        from smart_preparation import load
        path=next((self.output/'discovered').glob('*.json'))
        return path,load(path)

    def test_retry_time_exhaustion_retains_candidates_and_ownership(self):
        from smart_preparation import save,load
        from candidate_library import metadata
        first=self.run_job();receipt,state=self.checkpoint()
        before=metadata(first['library'])['count'];capture=state['snapshot']['input_id']
        state.update(state='Needs attention',seconds=1800,total_seconds=1800,pause_code='time_budget')
        save(receipt,state)
        with patch('candidate_library.run_search',side_effect=AssertionError('automatic retry')):
            self.run_job()
        result=self.run_job(target_digest=self.digest(),retry=True)
        self.assertEqual(result['state'],'Queued')
        after=load(receipt)
        self.assertLess(after['seconds'],1800)
        self.assertGreaterEqual(after['total_seconds'],1800)
        self.assertEqual(after['attempt'],2)
        self.assertEqual(after['snapshot']['input_id'],capture)
        self.assertGreaterEqual(metadata(first['library'])['count'],before)
        self.assertEqual(self.estimate.call_count,1)

    def test_retry_of_ownership_error_rechecks_and_recovers(self):
        self.estimate.side_effect=ValueError('Temporary input failure')
        failed=self.run_job();self.assertEqual(failed['pause_code'],'error')
        self.estimate.side_effect=None
        result=self.run_job(target_digest=self.digest(),retry=True)
        self.assertEqual(result['state'],'Queued')
        self.assertTrue(result['ownership_complete'])
        self.assertEqual(self.estimate.call_count,2)
        stored=rows(self.db,self.folder,all_dates=True)[0]['preparation']
        self.assertEqual(stored['count'],result['count'])
        self.assertNotIn('snapshot',stored)
        self.assertNotIn('ownership',stored)

    def test_retry_does_not_bypass_storage_or_delete_saved_work(self):
        first=self.run_job();before=Path(first['library']).read_bytes()
        with patch('smart_preparation.BUDGET',1),patch('candidate_library.run_search') as search:
            result=self.run_job(target_digest=self.digest(),retry=True)
        self.assertEqual(result['pause_code'],'storage');search.assert_not_called()
        self.assertEqual(Path(first['library']).read_bytes(),before)
        self.assertEqual(self.estimate.call_count,1)
        self.assertEqual(self.run_job(target_digest=self.digest(),retry=True)['state'],'Queued')

    def test_retry_changed_source_is_rejected_before_reset_or_search(self):
        from smart_preparation import save,load
        self.run_job();receipt,state=self.checkpoint()
        state.update(state='Needs attention',seconds=1800);save(receipt,state)
        digest=self.digest();self.path.write_text(self.path.read_text()+'\n')
        with patch('candidate_library.run_search') as search:
            result=self.run_job(target_digest=digest,retry=True)
        self.assertEqual(result['pause_code'],'source_changed');search.assert_not_called()
        self.assertEqual(load(receipt)['seconds'],1800)
        self.assertFalse(result['retry_consumed'])

    def test_stale_catalog_selection_does_not_prepare_replacement(self):
        old=self.digest();self.path.write_text(self.path.read_text().replace('10/20/2026','10/21/2026'))
        scan(self.db,self.folder)
        result=self.run_job(target_digest=old,retry=True)
        self.assertEqual(result['pause_code'],'source_changed')
        self.estimate.assert_not_called()
        self.assertNotEqual(self.digest(),old)

    def test_explicit_retry_can_prepare_a_selected_slate_outside_automatic_window(self):
        self.today=dt.date(2026,10,1)
        result=self.run_job(target_digest=self.digest(),retry=True)
        self.assertEqual(result['state'],'Queued')
        self.assertEqual(self.estimate.call_count,1)

    def test_cancel_during_ownership_records_pause_without_partial_ownership(self):
        stopped=[False]
        def estimate():stopped[0]=True;return self.own
        self.estimate.side_effect=estimate
        result=self.run_job(cancelled=lambda:stopped[0],pause_reason=lambda:'Paused by user for this session.')
        self.assertTrue(result['cancelled']);self.assertEqual(result['state'],'Paused')
        self.assertFalse(result['ownership_complete'])
        self.assertIn('Paused by user',rows(self.db,self.folder,all_dates=True)[0]['preparation']['pause_reason'])
        self.assertFalse(list(self.output.glob('*.dfslib')))

    def test_retry_no_progress_keeps_bank_and_grants_one_bounded_attempt(self):
        from smart_preparation import save
        first=self.run_job();receipt,state=self.checkpoint()
        state.update(stagnant=2);save(receipt,state)
        with patch('candidate_library.run_search',return_value=first['count']):
            paused=self.run_job()
            self.assertEqual(paused['pause_code'],'no_progress')
            resumed=self.run_job(target_digest=self.digest(),retry=True)
        self.assertEqual(resumed['state'],'Queued')
        self.assertEqual(resumed['stagnant'],1)
        self.assertEqual(self.estimate.call_count,1)

    def test_corrupt_library_is_retained_and_reported_instead_of_replaced(self):
        first=self.run_job();path=Path(first['library']);path.write_bytes(b'damaged saved library')
        result=self.run_job(target_digest=self.digest(),retry=True)
        self.assertEqual(result['state'],'Needs attention')
        self.assertEqual(result['pause_code'],'error')
        self.assertEqual(path.read_bytes(),b'damaged saved library')

    def test_app_version_change_creates_compatible_work_without_overwriting_old_library(self):
        first=self.run_job();before=Path(first['library']).read_bytes()
        with patch('candidate_library.code_id',return_value='new-code'),patch('candidate_library.preparation_model_id',return_value='new-code'):
            second=self.run_job(target_digest=self.digest(),retry=True)
        self.assertNotEqual(first['library'],second['library'])
        self.assertEqual(Path(first['library']).read_bytes(),before)
        self.assertEqual(self.estimate.call_count,2)


    def test_completed_target_checks_captured_input_identity_and_retains_mismatched_library(self):
        from build_snapshots import create_snapshot
        from candidate_library import run_search
        with patch('smart_preparation.TARGET',1):
            first=self.run_job();receipt,state=self.checkpoint()
            inputs=state['snapshot']['inputs']
            recipe=dict(inputs['recipe'],ownership_weight=.2)
            other=create_snapshot(inputs['players'],recipe,inputs['rules'])
            replacement=self.output/'other-library.dfslib'
            run_search(replacement,other,seconds=.1,candidate_limit=1,compatibility=True)
            copied=replacement.read_bytes();Path(first['library']).write_bytes(copied)
            result=self.run_job(target_digest=self.digest(),retry=True)
        self.assertEqual(result['pause_code'],'error')
        self.assertIn('captured inputs',result['error'])
        self.assertEqual(Path(first['library']).read_bytes(),copied)


    def test_cooperative_yield_does_not_count_as_a_completed_no_progress_batch(self):
        from smart_preparation import save
        first=self.run_job();receipt,state=self.checkpoint()
        state.update(stagnant=2);save(receipt,state)
        stopped=[False]
        def search(*args,**kwargs):stopped[0]=True;return first['count']
        with patch('candidate_library.run_search',side_effect=search):
            result=self.run_job(cancelled=lambda:stopped[0])
        self.assertEqual(result['state'],'Paused')
        self.assertEqual(result['stagnant'],2)
        self.assertEqual(result['count'],first['count'])


if __name__=='__main__':unittest.main()
