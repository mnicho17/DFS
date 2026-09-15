import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from build_snapshots import create_snapshot
from candidate_library import run_search,metadata,load_candidates,connect
from nfl_eligibility import apply_qb_eligibility,eligible_players
from ownership_estimates import quick_ownership
from test_nfl_logic import _fixture_players
from test_showdown_performance import _showdown_players
from optimizers import ShowdownOptimizer,MultiSportClassicOptimizer

class LongSearchTests(unittest.TestCase):
    def test_qb_injury_promotion_and_missing_depth_are_conservative(self):
        players=[dict(Name='Starter',Team='SEA',Position='QB',NFLDepthOrder=1,Status='Questionable'),
                 dict(Name='Backup',Team='SEA',Position='QB',NFLDepthOrder=2),
                 dict(Name='Third',Team='SEA',Position='QB',NFLDepthOrder=3),
                 dict(Name='Unknown',Team='SEA',Position='QB'),
                 dict(Name='Rotation WR',Team='SEA',Position='WR',NFLDepthOrder=5)]
        for status,expected in [('Questionable','Starter'),('Doubtful','Starter'),('OUT','Backup')]:
            players[0]['Status']=status
            apply_qb_eligibility(players)
            self.assertEqual([p['Name'] for p in eligible_players(players) if p['Position']=='QB'],[expected])
        players[1]['Status']='IR';apply_qb_eligibility(players)
        self.assertTrue(players[2]['NFLQBEligible'])
        players[0]['Status']='Active';players[2]['LockCpt']=True;apply_qb_eligibility(players)
        with self.assertRaisesRegex(ValueError,'locked quarterback'):eligible_players(players)
        isolated=[dict(Name='Backup',Team='SEA',Position='QB',NFLDepthOrder=2)]
        self.assertEqual(eligible_players(isolated),[])
        self.assertNotIn('NFLQBEligible',players[-1])

    def test_ownership_is_roster_exposure_not_normalized_weights(self):
        for kind,players,total in [('classic',_fixture_players(),900),('showdown',_showdown_players(),600)]:
            result=quick_ownership(players,mode=kind,sport='NFL')
            self.assertAlmostEqual(sum(result['total'].values()),total)
            self.assertTrue(all(0<=v<=100 for v in result['total'].values()))
            if kind=='showdown':
                self.assertAlmostEqual(sum(result['cpt'].values()),100)
                self.assertAlmostEqual(sum(result['flex'].values()),500)
                for k,v in result['total'].items():self.assertAlmostEqual(v,result['cpt'][k]+result['flex'][k])

    def test_both_libraries_resume_and_rehydrate_current_inputs(self):
        for kind,players,opt in [('classic',_fixture_players(),MultiSportClassicOptimizer),('showdown',_showdown_players(),ShowdownOptimizer)]:
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as tmp:
                snapshot=create_snapshot(players,dict(sport='NFL',contest_kind=kind,salary_strategy='Flexible'),{})
                path=Path(tmp)/'bank.dfslib'
                calls=[]
                actual=opt(players).build_lineups(4)
                self.assertTrue(actual)
                def batch(*args,**kwargs):calls.append(1);return actual
                with patch.object(opt,'build_lineups',side_effect=batch):
                    run_search(path,snapshot,seconds=20,cancelled=lambda:len(calls)>=1)
                    run_search(path,snapshot,seconds=20,cancelled=lambda:len(calls)>=2)
                meta=metadata(path)
                self.assertEqual(meta['batches'],2)
                self.assertEqual(meta['count'],len(actual))
                with connect(path) as con:self.assertEqual(len({row[0] for row in con.execute('SELECT seed FROM batches')}),2)
                current=copy.deepcopy(players)
                for p in current:p['FlexProjection']=123;p['CptProjection']=184.5
                rows,report=load_candidates(path,current,kind=kind,salary_cap=50000,salary_strategy='Flexible')
                self.assertEqual(report['accepted'],len(actual))
                roster=rows[0] if kind=='classic' else [rows[0]['Captain']]+rows[0]['Flex']
                self.assertTrue(all(p['FlexProjection']==123 for p in roster))
                self.assertFalse(getattr(rows[0],'sim_metrics',{}))
                wrong=copy.deepcopy(current);wrong[0]['GameInfo']='NEXT WEEK'
                with self.assertRaisesRegex(ValueError,'different player slate'):load_candidates(path,wrong,kind=kind,salary_cap=50000)
                for p in current:p['Status']='OUT'
                with self.assertRaisesRegex(ValueError,'No saved candidates'):load_candidates(path,current,kind=kind,salary_cap=50000)
                changed=copy.deepcopy(snapshot);changed['inputs']['players'][0]['FlexProjection']+=1
                from build_snapshots import fingerprint
                changed['input_id']=fingerprint(changed['inputs'])
                with self.assertRaisesRegex(ValueError,'different inputs'):run_search(path,changed)

    def test_library_runs_through_classic_and_showdown_simulation(self):
        from main_window import LineupBuildWorker
        for kind,players,opt in [('classic',_fixture_players(),MultiSportClassicOptimizer),('showdown',_showdown_players(),ShowdownOptimizer)]:
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'bank.dfslib'
                snapshot=create_snapshot(players,dict(sport='NFL',contest_kind=kind),{})
                done=[]
                actual=opt(players).build_lineups(4)
                def batch(*a,**kw):done.append(1);return actual
                with patch.object(opt,'build_lineups',side_effect=batch):
                    run_search(path,snapshot,cancelled=lambda:bool(done))
                worker=LineupBuildWorker(players,kind=kind,num_lineups=2,salary_cap=50000,
                    salary_strategy='Balanced Spend',sim_enabled=True,sim_scenarios=250,
                    compute_mode='Deep',deep_time_limit_seconds=5,
                    deep_options=dict(field=200,screening=100,selection_mode='Individual ranking'),candidate_library=str(path))
                results=[];errors=[]
                worker.finished.connect(results.append);worker.error.connect(errors.append);worker.run()
                self.assertFalse(errors,errors)
                self.assertTrue(results)
                self.assertTrue(results[0]['lineups'])
                self.assertEqual(worker.library_report['accepted'],len(actual))
                for row in results[0]['lineups']:
                    self.assertGreater(getattr(row,'sim_metrics',{}).get('sim_scenarios',0),0)
