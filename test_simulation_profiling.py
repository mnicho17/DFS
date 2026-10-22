from test_environment import install
install()
import copy
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from simulation_profiling import FIELDS,begin_session,end_session,snapshot,clean
from test_showdown_performance import _showdown_players
from showdown_simulation import simulate_showdown
from optimizers import ShowdownOptimizer


class SimulationProfileTests(unittest.TestCase):
    def fixture(self):
        players=_showdown_players();return players,ShowdownOptimizer(players).build_lineups(4)

    def test_components_reconcile_and_warm_cache_skips_draws_and_opponent_scoring(self):
        players,rows=self.fixture();original=copy.deepcopy(players)
        with tempfile.TemporaryDirectory() as raw,patch.dict('os.environ',{'DFS_OPTIMIZER_DATA_DIR':raw}):
            cold=simulate_showdown(rows,players,scenarios=12,field_lineup_count=30,scenario_cache=True)
            cold_metrics=copy.deepcopy([lu.sim_metrics for lu in cold['lineups']])
            warm=simulate_showdown(rows,players,scenarios=12,field_lineup_count=30,scenario_cache=True)
        for result in (cold,warm):
            profile=result['report']['simulation_profile'];times=profile['timings']
            self.assertTrue(all(math.isfinite(v) and v>=0 for v in times.values()))
            self.assertAlmostEqual(sum(v for k,v in times.items() if k!='total_seconds'),times['total_seconds'],places=7)
            self.assertEqual(profile['completed_scenarios'],12);self.assertGreater(times['candidates_seconds'],0)
        warm_profile=warm['report']['simulation_profile']
        self.assertEqual(warm_profile['reused_scenarios'],12)
        self.assertEqual(warm_profile['timings']['outcomes_seconds'],0)
        self.assertEqual(warm_profile['timings']['opponents_seconds'],0)
        self.assertEqual(cold_metrics,[lu.sim_metrics for lu in warm['lineups']])
        self.assertEqual(players,original)

    def test_session_captures_screening_validation_audit_and_resets_between_workers(self):
        from compute_ledger import finish_worker
        players,rows=self.fixture();token=begin_session()
        try:
            for seed in (73129,90210,481516):simulate_showdown(rows,players,scenarios=3,field_lineup_count=20,seed=seed)
            payload={};finish_worker(payload)
            profile=payload['timing_report']['simulation_profile']
            self.assertEqual(set(profile),{'screening','validation','ranking_audit'})
            for row in profile.values():self.assertEqual(row['completed_scenarios'],3);self.assertEqual(row['calls'],1)
            snapshot()['screening']['calls']=99
            self.assertEqual(profile['screening']['calls'],1)
        finally:end_session(token)
        self.assertEqual(snapshot(),{})

    def test_delivery_failure_does_not_fail_simulation_or_add_stop_checks(self):
        players,rows=self.fixture();checks=[];baseline_checks=[]
        simulate_showdown(rows,players,scenarios=3,field_lineup_count=20,cancel_callback=lambda:baseline_checks.append(1) or False)
        with patch('simulation_profiling.Clock.report',side_effect=OSError('diagnostics unavailable')):
            result=simulate_showdown(rows,players,scenarios=3,field_lineup_count=20,cancel_callback=lambda:checks.append(1) or False)
        self.assertEqual(result['report']['scenarios'],3)
        self.assertNotIn('simulation_profile',result['report'])
        self.assertEqual(checks,baseline_checks)

    def test_old_missing_and_malformed_profiles_do_not_invent_timings_or_leak_values(self):
        self.assertEqual(clean(None),{})
        self.assertEqual(clean({'private':{'secret':'value'}}),{})
        cleaned=clean({'validation':dict(calls=1,timings=dict(outcomes_seconds=float('nan'),other_seconds=-1,secret='value',total_seconds=2))})
        self.assertEqual(cleaned['validation']['timings'],{'total_seconds':2.0})
        self.assertEqual(clean({'validation':{'timings':'bad'}}),{'validation':{'timings':{}}})

    def test_build_report_saves_captured_profile_without_backfilling(self):
        from build_diagnostics import create_build_diagnostic,format_build_report,save_build_diagnostic,load_build_history
        profile={'validation':dict(calls=1,completed_scenarios=12,reused_scenarios=12,candidate_evaluations=48,timings=dict(total_seconds=1,candidates_seconds=.8))}
        record=create_build_diagnostic(context={},timing_report={'simulation_profile':profile})
        profile['validation']['timings']['candidates_seconds']=999
        text=format_build_report(record)
        self.assertIn('candidate scores/metrics 0.800',text);self.assertNotIn('999',text)
        with tempfile.TemporaryDirectory() as raw:
            path=str(Path(raw)/'history.json');save_build_diagnostic(record,path=path)
            self.assertEqual(format_build_report(load_build_history(path=path)[0]),text)
        self.assertNotIn('SIM profile ',format_build_report(create_build_diagnostic(context={},timing_report={})))
