from test_environment import install
install()
import copy
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from build_snapshots import create_snapshot
from smart_preparation import save,load,source_digest
from smart_scenario_preparation import pending,warm,request_snapshot,request_id
from test_showdown_performance import _showdown_players


class SmartScenarioTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.source=self.root/'salary.csv';self.source.write_text('original')
        self.snapshot=create_snapshot(_showdown_players(),dict(sport='NFL',contest_kind='showdown'),{})
        self.state=dict(state='Prepared candidates',snapshot=self.snapshot,count=12000)
        self.row=dict(path=str(self.source),digest=source_digest(self.source,lambda:False))
        self.library=self.root/'library.dfslib';self.library.write_text('retained')
        self.receipt=self.root/'receipt.json';self.catalog=self.root/'catalog.sqlite'

    def run_warm(self,**kwargs):
        return warm(self.catalog,self.receipt,self.state,self.row,self.library,self.snapshot,
                    'Saved Smart setup',kwargs.pop('cancelled',lambda:False),lambda text:None,**kwargs)

    def test_real_detached_pipeline_saves_and_reuses_without_mutating_captured_inputs(self):
        import os
        from PyQt5 import QtWidgets
        from candidate_library import run_search
        from scenario_preparation import scenario_preparation_result
        app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        snapshot=create_snapshot(_showdown_players(),dict(sport='NFL',contest_kind='showdown',
            salary_strategy='Flexible',requested_lineups=1,nfl_sim_scenarios=2500,
            deep_compute=dict(minutes=1,field=20,screening=250,shortlist=4)),{})
        before=copy.deepcopy(snapshot)
        library=self.root/'real.dfslib'
        run_search(library,snapshot,candidate_limit=4,seconds=1)
        with patch.dict(os.environ,{'DFS_OPTIMIZER_DATA_DIR':str(self.root)}):
            first=scenario_preparation_result(library,snapshot,seconds=60)
            second=scenario_preparation_result(library,snapshot,seconds=60)
        self.assertEqual(first['state'],'Completed',first['message'])
        self.assertEqual(second['state'],'Completed',second['message'])
        self.assertEqual(second['cache']['status'],'reused')
        self.assertEqual(second['cache']['reused_scenarios'],2500)
        self.assertEqual(snapshot,before)

    def test_current_snapshot_must_match_exact_slate_and_is_detached(self):
        current=copy.deepcopy(self.snapshot)
        current=create_snapshot(current['inputs']['players'],dict(sport='NFL',contest_kind='showdown',nfl_sim_scenarios=10000),{})
        captured,scope=request_snapshot(self.snapshot,current)
        self.assertEqual(scope,'Current captured setup')
        current['inputs']['recipe']['nfl_sim_scenarios']=1
        self.assertEqual(captured['inputs']['recipe']['nfl_sim_scenarios'],10000)
        other=copy.deepcopy(self.snapshot['inputs']['players']);other[0]['GameInfo']='different date'
        other=create_snapshot(other,dict(sport='NFL',contest_kind='showdown'),{})
        captured,scope=request_snapshot(self.snapshot,other)
        self.assertEqual(scope,'Saved Smart setup')
        self.assertEqual(captured,self.snapshot)

    def test_time_allowance_stops_after_three_attempts_and_explicit_retry_resets(self):
        outcome=dict(state='Paused',message='Time allowance reached.',cache={})
        with patch('scenario_preparation.scenario_preparation_result',return_value=outcome) as work:
            for _ in range(3):result=self.run_warm()
            self.assertEqual(result['scenario_preparation']['state'],'Needs attention')
            self.assertFalse(pending(self.state,self.snapshot))
            self.run_warm();self.assertEqual(work.call_count,3)
            self.run_warm(retry=True);self.assertEqual(work.call_count,4)
            self.assertEqual(self.state['scenario_preparation']['attempts'],1)
        self.assertEqual(self.library.read_text(),'retained')

    def test_yield_retains_candidate_and_does_not_consume_attempt(self):
        cancelled=[False]
        def compute(*args,**kwargs):
            cancelled[0]=True
            return dict(state='Paused',message='Yielded',cache={})
        with patch('scenario_preparation.scenario_preparation_result',side_effect=compute):
            result=self.run_warm(cancelled=lambda:cancelled[0],pause_reason=lambda:'Foreground build requested.')
        self.assertTrue(result['cancelled'])
        self.assertEqual(result['scenario_preparation']['attempts'],0)
        self.assertIn('Foreground',result['scenario_preparation']['message'])
        self.assertEqual(result['state'],'Prepared candidates')
        self.assertEqual(load(self.receipt)['scenario_preparation']['state'],'Paused')

    def test_changed_source_prevents_warming_and_errors_retain_candidate(self):
        self.source.write_text('changed')
        with patch('scenario_preparation.scenario_preparation_result') as work:self.run_warm()
        work.assert_not_called()
        self.source.write_text('original')
        with patch('scenario_preparation.scenario_preparation_result',side_effect=OSError('unavailable')):
            result=self.run_warm(retry=True)
        self.assertEqual(result['scenario_preparation']['state'],'Needs attention')
        self.assertEqual(self.library.read_text(),'retained')

    def test_warming_changes_neither_snapshot_nor_scores_and_reports_exact_allowance(self):
        before=copy.deepcopy(self.snapshot);captured=[]
        def work(path,snapshot,**kwargs):
            captured.append(kwargs)
            self.assertEqual(snapshot,before)
            return dict(state='Needs attention',message='No complete cache; current setup retained.',cache={})
        with patch('scenario_preparation.scenario_preparation_result',side_effect=work):
            result=self.run_warm()
        self.assertEqual(self.snapshot,before)
        self.assertEqual(captured[0]['seconds'],600)
        self.assertEqual(result['state'],'Prepared candidates')
        self.assertFalse(pending(self.state,self.snapshot))
        self.assertEqual(load(self.receipt)['snapshot'],before)

    def test_cumulative_allowance_stops_even_when_yielded_attempts_do_not_count(self):
        self.state['scenario_preparation']=dict(request_id=request_id(self.snapshot),
            state='Paused',attempts=0,seconds=1790)
        with patch('scenario_preparation.scenario_preparation_result') as work:
            result=self.run_warm()
        work.assert_not_called()
        self.assertEqual(result['scenario_preparation']['state'],'Needs attention')
        self.assertEqual(result['errors'],1)

    def test_abrupt_termination_charges_reserved_allowance_before_resuming(self):
        self.state['scenario_preparation']=dict(request_id=request_id(self.snapshot),
            state='Running',attempts=2,seconds=1200,reserved_seconds=600)
        self.assertFalse(pending(self.state,self.snapshot))
        self.assertEqual(self.state['scenario_preparation']['seconds'],1800)
        self.assertNotIn('reserved_seconds',self.state['scenario_preparation'])
        self.assertEqual(self.state['scenario_preparation']['state'],'Needs attention')

    def test_settings_build_and_crash_budgets_do_not_silently_skip_new_work(self):
        identity=request_id(self.snapshot)
        self.state['scenario_preparation']=dict(request_id=identity,state='Running',attempts=3,seconds=2)
        self.assertFalse(pending(self.state,self.snapshot))
        self.assertEqual(self.state['scenario_preparation']['state'],'Needs attention')
        changed=create_snapshot(self.snapshot['inputs']['players'],dict(sport='NFL',contest_kind='showdown',nfl_sim_scenarios=10000),{})
        self.assertTrue(pending(self.state,changed))
        with patch('candidate_library.code_id',return_value='new-build'):
            self.assertTrue(pending(self.state,self.snapshot))
        self.assertTrue(pending(self.state,self.snapshot,retry=True))

    def test_storage_blocker_is_recorded_and_reset_without_changing_retry_policy(self):
        blocked=dict(state='Needs attention',message='Cache storage limit reached',cache=dict(status='not saved',reason='Cache storage limit reached'))
        with patch('scenario_preparation.scenario_preparation_result',return_value=blocked):result=self.run_warm()
        self.assertTrue(result['scenario_preparation']['cache_storage_blocked'])
        self.assertFalse(pending(self.state,self.snapshot))
        self.assertEqual(load(self.receipt)['scenario_preparation']['cache_status'],'not saved')
        other=dict(state='Needs attention',message='Busy cache',cache=dict(status='not saved',reason='Another job is recording scenarios'))
        with patch('scenario_preparation.scenario_preparation_result',return_value=other):result=self.run_warm(retry=True)
        self.assertFalse(result['scenario_preparation']['cache_storage_blocked'])
        self.assertEqual(result['scenario_preparation']['attempts'],1)
        self.assertEqual(self.library.read_text(),'retained')

    def test_real_storage_blocker_explains_verified_archive_action_without_cleanup(self):
        import os
        from PyQt5 import QtWidgets
        from candidate_library import run_search
        from scenario_preparation import scenario_preparation_result
        app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        snapshot=create_snapshot(_showdown_players(),dict(sport='NFL',contest_kind='showdown',salary_strategy='Flexible',requested_lineups=1,nfl_sim_scenarios=2500,deep_compute=dict(minutes=1,field=20,screening=250,shortlist=4)),{})
        before=copy.deepcopy(snapshot);library=self.root/'blocked.dfslib';run_search(library,snapshot,candidate_limit=4,seconds=1)
        with patch.dict(os.environ,{'DFS_OPTIMIZER_DATA_DIR':str(self.root)}),patch('scenario_cache.STORE_LIMIT',0),patch('legacy_scenario_archive.validate_and_archive') as archive:
            result=scenario_preparation_result(library,snapshot,seconds=60)
        archive.assert_not_called();self.assertEqual(result['state'],'Needs attention')
        self.assertIn('Review cache storage',result['message']);self.assertIn('verifies a recoverable copy',result['message'])
        self.assertEqual(result['cache']['status'],'not saved');self.assertEqual(snapshot,before)
