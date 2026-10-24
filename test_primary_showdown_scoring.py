"""Primary scorer rollout: exact fixed-sample evidence and unchanged Deep outputs."""
from test_environment import install
install()
from copy import deepcopy
import os
import tempfile
import unittest
from unittest.mock import patch
import showdown_simulation as sim
from optimizers import ShowdownOptimizer
from test_showdown_performance import _showdown_players
from test_ranking_audit_efficiency import values,report_values


def exact(value):
    from array import array
    if isinstance(value,float):return value.hex()
    if isinstance(value,dict):return {k:exact(v) for k,v in value.items()}
    if isinstance(value,(list,tuple,array)):return [exact(v) for v in value]
    if isinstance(value,set):return sorted(value)
    return value


def evidence(result):
    report=deepcopy(report_values(result))
    # Per-capture wall-clock provenance is expected to differ, never the data.
    distribution=report.get('player_distributions')
    if distribution:
        for key in ('started_at','finished_at'):distribution.pop(key,None)
    return report


class PrimaryScoringTests(unittest.TestCase):
    def fixture(self):
        players=_showdown_players();return players,ShowdownOptimizer(players)._build_lineups_fast(12)

    def test_primary_seeds_keep_full_reports_metrics_hits_distributions_and_rng_draws(self):
        players,rows=self.fixture();before=deepcopy(players)
        for seed in (73129,90210):
            result=[];draws=[]
            for compiled in (False,True):
                original=sim._scenario_outcomes
                with patch('showdown_simulation._scenario_outcomes',wraps=original) as draw:
                    r=sim.simulate_showdown(deepcopy(rows),deepcopy(players),scenarios=60,field_lineup_count=50,seed=seed,capture_distributions=True,precompiled_rosters=compiled)
                draws.append(draw.call_count);result.append(r)
            self.assertEqual(draws,[60,60]);self.assertEqual(exact(values(result[0])),exact(values(result[1])))
            self.assertEqual(exact(evidence(result[0])),exact(evidence(result[1])))
        self.assertEqual(before,players)

    def test_primary_cancellation_preserves_same_partial_sample_and_metadata(self):
        players,rows=self.fixture()
        for seed in (73129,90210):
            result=[]
            for compiled in (False,True):
                calls=[];original=sim._scenario_outcomes
                def draw(*args,**kwargs):calls.append(True);return original(*args,**kwargs)
                with patch('showdown_simulation._scenario_outcomes',side_effect=draw):
                    r=sim.simulate_showdown(deepcopy(rows),deepcopy(players),scenarios=60,field_lineup_count=30,seed=seed,precompiled_rosters=compiled,cancel_callback=lambda:len(calls)>=3)
                self.assertEqual(r['report']['scenarios'],3);result.append(r)
            self.assertEqual(exact(values(result[0])),exact(values(result[1])));self.assertEqual(report_values(result[0]),report_values(result[1]))

    def test_primary_seeds_preserve_cold_cache_and_exact_qualified_replay(self):
        players,rows=self.fixture()
        for seed in (73129,90210):
            with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'DFS_OPTIMIZER_DATA_DIR':folder}):
                opts=dict(scenarios=20,field_lineup_count=30,seed=seed,capture_distributions=True,scenario_cache=True)
                old=sim.simulate_showdown(deepcopy(rows),deepcopy(players),**opts)
                self.assertEqual(old['report']['scenario_cache']['status'],'saved')
                with patch('showdown_simulation._scenario_outcomes',side_effect=AssertionError('Must replay qualified frames')):
                    new=sim.simulate_showdown(deepcopy(rows),deepcopy(players),precompiled_rosters=True,**opts)
                self.assertEqual(new['report']['scenario_cache']['status'],'reused')
                self.assertEqual(new['report']['scenario_cache']['reused_scenarios'],20)
                self.assertEqual(exact(values(old)),exact(values(new)))

    def test_historical_source_validation_still_precedes_compilation(self):
        players,rows=self.fixture()
        with patch('showdown_history_field.validate_profile',side_effect=ValueError('Fresh qualification required')) as validate,patch('showdown_simulation._compile_score_roster') as compile:
            with self.assertRaisesRegex(ValueError,'Fresh qualification'):
                sim.simulate_showdown(rows,players,scenarios=20,field_lineup_count=30,field_model='historical-blend-v1',history_profile=None,precompiled_rosters=True)
        validate.assert_called_once();compile.assert_not_called()

    def test_name_fallback_keys_and_legacy_field_keep_exact_results(self):
        players,_=self.fixture()
        for p in players:
            for k in ('FlexID','CptID','FlexNamePlusID'):p.pop(k,None)
        rows=ShowdownOptimizer(players)._build_lineups_fast(8);results=[]
        for compiled in (False,True):
            results.append(sim.simulate_showdown(deepcopy(rows),deepcopy(players),scenarios=30,field_lineup_count=30,field_model='legacy',seed=90210,precompiled_rosters=compiled))
        self.assertEqual(exact(values(results[0])),exact(values(results[1])));self.assertEqual(report_values(results[0]),report_values(results[1]))

    def test_real_deep_selected_portfolios_and_stage_evidence_match_reference_path(self):
        from main_window import LineupBuildWorker,_deep_shortlist
        players=_showdown_players();rows=ShowdownOptimizer(players)._build_lineups_fast(24);before=deepcopy(players);original=sim.simulate_showdown
        for mode in ('Portfolio selection','Individual ranking'):
            results=[]
            for compiled in (False,True):
                worker=LineupBuildWorker(deepcopy(players),kind='showdown',num_lineups=1,salary_cap=50000,salary_strategy='Balanced Spend',sim_enabled=True,compute_mode='Deep',sim_scenarios=2500,deep_time_limit_seconds=300,deep_options=dict(shortlist=24,field=20,screening=250,selection_mode=mode))
                worker.library_candidates=deepcopy(rows)
                def invoke(*args,**kwargs):kwargs['precompiled_rosters']=compiled;return original(*args,**kwargs)
                with patch('showdown_simulation.simulate_showdown',side_effect=invoke):r=sim.run_deep_showdown(worker,_deep_shortlist)
                self.assertEqual(worker.library_candidates,rows)
                self.assertEqual(r['sim_report']['deep_build']['ranking_audit']['status'],'completed')
                results.append(r);worker.deleteLater()
            self.assertEqual(exact(values(results[0])),exact(values(results[1])))
            for key in ('captain_coverage','quarterback_pipeline','defense_pipeline','ownership_leverage'):
                self.assertEqual(results[0]['sim_report'].get(key),results[1]['sim_report'].get(key))
            for key in ('screening_scenarios','validation_scenarios','shortlist_count'):
                self.assertEqual(results[0]['sim_report']['deep_build'][key],results[1]['sim_report']['deep_build'][key])
        self.assertEqual(players,before)
