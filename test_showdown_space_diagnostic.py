"""Aggregate outer bounds must never change selection or claim full feasibility."""
from test_environment import install
install()
from copy import deepcopy
import itertools
import os
import random
import tempfile
import unittest
from unittest.mock import patch
from showdown_space_diagnostic import observe,clean,lines
from test_showdown_performance import _showdown_players
from nfl_simulation import player_key


class CandidateSpaceTests(unittest.TestCase):
    def pool(self):
        rows=_showdown_players()
        return rows[:6]+rows[18:24]

    def reference(self,players):
        result=set()
        for captain in players:
            if captain.get('FadeCpt') or captain.get('LockFlex'):continue
            if any(p.get('LockCpt') for p in players) and not captain.get('LockCpt'):continue
            for flex in itertools.combinations([p for p in players if p is not captain],5):
                if any(p.get('FadeFlex') for p in flex):continue
                if any(p.get('LockFlex') and p not in flex for p in players):continue
                salary=captain['CptSalary']+sum(p['FlexSalary'] for p in flex)
                if not 20000<=salary<=40000:continue
                if len({p['Team'] for p in (captain,)+flex})!=2:continue
                result.add(('CPT:'+player_key(captain),)+tuple(sorted(player_key(p) for p in flex)))
        return result

    def test_reference_locks_fades_and_reversed_input_have_same_bound(self):
        players=self.pool();players[0]['LockCpt']=True;players[6]['LockCpt']=True
        players[1]['LockFlex']=True;players[7]['FadeFlex']=True;players[2]['FadeCpt']=True
        before=deepcopy(players);state=random.getstate();valid=self.reference(players)
        observed=sorted(valid)[::3]
        for pool,known in ((players,observed),(list(reversed(players)),list(reversed(observed)))):
            result=observe(pool,known,salary_cap=40000,minimum_salary=20000,target=20000)
            self.assertEqual(result['status'],'complete')
            self.assertEqual(result['upper_bound'],len(valid))
            self.assertEqual(result['observed'],len(observed))
            self.assertEqual(result['remaining_upper_bound'],len(valid)-len(observed))
            self.assertIn('exceeds this bound',' '.join(lines(result)))
            self.assertIn('not proof of portfolio feasibility',' '.join(lines(result)))
        self.assertEqual(before,players);self.assertEqual(state,random.getstate())

    def test_missing_conflicting_or_fractional_identity_prices_withhold_bound(self):
        for change in ('missing','duplicate','fractional','one team','conflicting lock'):
            players=self.pool()
            if change=='missing':players[0].pop('CptID')
            elif change=='duplicate':players[0]['FlexID']=players[1]['FlexID']
            elif change=='fractional':players[0]['FlexSalary']+=.5
            elif change=='one team':
                for p in players:p['Team']='A'
            else:players[0].update(LockFlex=True,LockCpt=True)
            result=observe(players,[],salary_cap=40000,minimum_salary=20000,target=20000)
            self.assertEqual(result['status'],'unavailable');self.assertNotIn('upper_bound',result)

    def test_bounds_cancellation_and_out_of_scope_observations_publish_no_partial_count(self):
        options=dict(salary_cap=40000,minimum_salary=20000,target=20000)
        for kwargs,status in ((dict(max_combinations=1),'size bound'),(dict(seconds=0),'time bound'),(dict(cancelled=lambda:True),'cancelled')):
            result=observe(self.pool(),[],**options,**kwargs)
            self.assertEqual(result['status'],status);self.assertNotIn('upper_bound',result)
        ticks=iter([0,0,0,2,2])
        result=observe(self.pool(),[],**options,clock=lambda:next(ticks))
        self.assertEqual(result['status'],'time bound');self.assertGreater(result['examined'],0)
        self.assertNotIn('upper_bound',result)
        result=observe(self.pool(),[('CPT:unknown','a','b','c','d','e')],**options)
        self.assertEqual(result['status'],'unavailable');self.assertNotIn('upper_bound',result)

    def test_complete_zero_and_cleaner_reject_inconsistent_or_private_values(self):
        result=observe(self.pool(),[],salary_cap=1,minimum_salary=0,target=100)
        self.assertEqual(result['status'],'complete');self.assertEqual(result['upper_bound'],0)
        result['private']='C:/secret/result.csv'
        self.assertNotIn('secret',str(clean(result)))
        result['observed']=1
        self.assertEqual(clean(result),dict(status='unavailable'))
        self.assertNotIn('upper_bound',clean(dict(status='time bound',upper_bound=99)))
        self.assertEqual(clean(dict(status='private path')),{});self.assertEqual(lines(None),[])

    def test_saved_report_keeps_captured_counts_without_private_fields_or_legacy_backfill(self):
        from build_diagnostics import create_build_diagnostic,format_build_report,save_build_diagnostic,load_build_history
        data=dict(status='complete',upper_bound=4701,observed=4650,remaining_upper_bound=51,target=20000,seconds=.4,private='secret')
        record=create_build_diagnostic(context=dict(kind='showdown'),timing_report=dict(compute_mode='Deep'),sim_report=dict(deep_build=dict(candidate_space=data)))
        data['upper_bound']=99999
        text=format_build_report(record)
        self.assertIn('4,701',text);self.assertIn('51 additional',text);self.assertNotIn('secret',text)
        with tempfile.TemporaryDirectory() as folder:
            path=os.path.join(folder,'history.json');save_build_diagnostic(record,path=path)
            self.assertEqual(format_build_report(load_build_history(path=path)[0]),text)
        old=create_build_diagnostic(context={},timing_report={})
        self.assertNotIn('Candidate-space diagnostic',format_build_report(old))

    def test_real_deep_observation_preserves_selected_portfolio_and_stage_evidence(self):
        from main_window import LineupBuildWorker,_deep_shortlist
        from optimizers import ShowdownOptimizer
        import showdown_simulation as sim
        from test_primary_showdown_scoring import exact,values
        players=_showdown_players();rows=ShowdownOptimizer(players)._build_lineups_fast(24)
        results=[]
        for enabled in (False,True):
            worker=LineupBuildWorker(deepcopy(players),kind='showdown',num_lineups=1,salary_cap=50000,salary_strategy='Balanced Spend',sim_enabled=True,compute_mode='Deep',sim_scenarios=2500,deep_time_limit_seconds=300,deep_options=dict(shortlist=24,field=20,screening=250))
            worker.library_candidates=deepcopy(rows)
            with patch('showdown_space_diagnostic.observe',side_effect=observe if enabled else lambda *a,**kw:dict(status='unavailable')) as observation:
                result=sim.run_deep_showdown(worker,_deep_shortlist)
                self.assertEqual(observation.call_count,1)
            self.assertEqual(worker.library_candidates,rows);results.append(result);worker.deleteLater()
        self.assertEqual(exact(values(results[0])),exact(values(results[1])))
        for result in results:result['portfolio_report'].pop('refinement_seconds',None)
        self.assertEqual(results[0]['portfolio_report'],results[1]['portfolio_report'])
        for key in ('captain_coverage','quarterback_pipeline','defense_pipeline','ownership_leverage'):
            self.assertEqual(results[0]['sim_report'].get(key),results[1]['sim_report'].get(key))
        for result in results:
            for key in ('seconds','available_seconds'):result['sim_report']['deep_build']['ranking_audit'].pop(key,None)
        for key in ('screening_scenarios','validation_scenarios','shortlist_count','ranking_audit'):
            self.assertEqual(results[0]['sim_report']['deep_build'][key],results[1]['sim_report']['deep_build'][key])
