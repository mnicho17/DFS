import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ownership_sensitivity import ownership_profiles, run_sensitivity, load_sensitivity_bank, save_sensitivity, format_sensitivity, PROFILES
from repeatability import save_bank, identity
from nfl_simulation import SimLineup


def fixture(kind):
    from test_nfl_logic import _fixture_players
    from test_showdown_performance import _showdown_players
    from nfl_simulation import build_nfl_role_pool
    from showdown_simulation import active_showdown_players
    from ownership_estimates import quick_ownership
    from optimizers import _pkey
    p=build_nfl_role_pool(_fixture_players(),preserve_locks=False) if kind=='classic' else active_showdown_players(_showdown_players())
    own=quick_ownership(p,mode=kind,sport='NFL')
    for v in p:
        k=_pkey(v);v.update(ProjOwnPct=own['total'][k],OwnershipUnits='percent_of_entries')
        if kind=='showdown':v.update(ProjCptOwnPct=own['cpt'][k],ProjFlexOwnPct=own['flex'][k])
    return p


class OwnershipSensitivityTests(unittest.TestCase):
    def bank(self, folder):
        players=[dict(FlexID=str(i),Name=str(i),Position='WR',ProjOwnPct=90,OwnershipUnits='percent_of_entries') for i in range(10)]
        rows=[SimLineup([p for j,p in enumerate(players) if j!=i],metrics=dict(sim_scenarios=1000,sim_top_one_pct=10-i,sim_mean=90)) for i in range(10)]
        info=save_bank(rows,players,kind='classic',salary_cap=50000,field_count=20,folder=folder)
        return Path(folder)/(info['bank_id']+'.dfsbank')

    def test_profiles_preserve_slots_scoring_and_input(self):
        for kind in ('classic','showdown'):
            p=fixture(kind);payload=dict(kind=kind,players=p,rows=[]);before=copy.deepcopy(payload)
            profiles,changes=ownership_profiles(payload)
            for name,players in profiles.items():
                self.assertAlmostEqual(sum(v['ProjOwnPct'] for v in players),900 if kind=='classic' else 600)
                if kind=='showdown':
                    self.assertAlmostEqual(sum(v['ProjCptOwnPct'] for v in players),100)
                    self.assertAlmostEqual(sum(v['ProjFlexOwnPct'] for v in players),500)
                for a,b in zip(p,players):
                    self.assertLessEqual(b['ProjOwnPct'],100.00001)
                    self.assertEqual({k:v for k,v in a.items() if not k.startswith('Proj') or k=='ProjectionSource'},
                                     {k:v for k,v in b.items() if not k.startswith('Proj') or k=='ProjectionSource'})
            self.assertTrue(changes[PROFILES[2]])
            self.assertEqual(payload,before)
            p[0].pop('OwnershipUnits')
            with self.assertRaisesRegex(ValueError,'explicit units'):ownership_profiles(payload)

    def test_paired_seeds_old_bank_integrity_and_saved_report(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch('repeatability.model_version',return_value='old-version'):path=self.bank(folder)
            original=path.read_bytes();calls=[]
            def sim(rows,players,**kw):
                calls.append(kw['seed'])
                for i,lu in enumerate(rows):lu.sim_metrics.update(sim_scenarios=1000,sim_top_one_pct=10-i-(len(calls)-1)%3,field_exact_matches=2)
                return dict(lineups=rows,report={'scenarios':1000})
            r=run_sensitivity(path,batches=2,scenarios=1000,simulate=sim)
            self.assertEqual(calls[:3],[calls[0]]*3);self.assertEqual(calls[3:],[calls[3]]*3);self.assertNotEqual(calls[0],calls[3])
            self.assertEqual(r['rows'][1]['change_pp'],-1)
            self.assertEqual(r['saved_model'],'old-version');self.assertEqual(path.read_bytes(),original)
            save_sensitivity(Path(folder)/'report.json',r)
            self.assertTrue((Path(folder)/'report.csv').exists());self.assertIn('hypothetical',format_sensitivity(r))
            b=json.loads(path.read_text());b['payload']['salary_cap']=60000;path.write_text(json.dumps(b))
            with self.assertRaisesRegex(ValueError,'modified'):load_sensitivity_bank(path)

    def test_partial_pairs_rejected_and_scoring_identity_guards(self):
        with tempfile.TemporaryDirectory() as folder:
            path=self.bank(folder);calls=[]
            def partial(rows,p,**kw):
                calls.append(1)
                return dict(lineups=rows,report={'scenarios':999 if len(calls)==5 else 1000})
            r=run_sensitivity(path,batches=2,scenarios=1000,simulate=partial)
            self.assertEqual(r['completed_batches'],1);self.assertEqual(r['status'],'incomplete')
            def changed(rows,p,**kw):
                rows[0].sim_metrics['sim_mean']+=len(calls);calls.append(1)
                return dict(lineups=rows,report={'scenarios':1000})
            with self.assertRaisesRegex(ValueError,'scoring changed'):run_sensitivity(path,batches=1,scenarios=1000,simulate=changed)
            with self.assertRaisesRegex(ValueError,'identities'):
                run_sensitivity(path,batches=1,scenarios=1000,simulate=lambda rows,p,**kw:dict(lineups=rows[:-1],report={'scenarios':1000}))
            r=run_sensitivity(path,batches=1,scenarios=1000,cancelled=lambda:True)
            self.assertEqual(r['completed_batches'],0)
            calls.clear()
            def shortage(rows,p,**kw):
                calls.append(1)
                return dict(lineups=rows,report={'scenarios':1000,'field_lineups':20 if len(calls)==1 else 19})
            with self.assertRaisesRegex(ValueError,'sample size changed'):
                run_sensitivity(path,batches=1,scenarios=1000,simulate=shortage)

    def test_real_simulators_identical_outcomes_and_different_fields(self):
        from nfl_simulation import generate_nfl_field_lineups,simulate_nfl_contest
        from showdown_simulation import generate_showdown_field,simulate_showdown
        import nfl_simulation,showdown_simulation
        for kind,module,sim in [('classic',nfl_simulation,simulate_nfl_contest),('showdown',showdown_simulation,simulate_showdown)]:
            p=fixture(kind);before=copy.deepcopy(p)
            raw=generate_nfl_field_lineups(p,3,seed=7)[0] if kind=='classic' else generate_showdown_field(p,3,seed=7)
            scored=sim(raw,p,scenarios=1000,field_lineup_count=20)['lineups']
            with tempfile.TemporaryDirectory() as folder:
                info=save_bank(scored,p,kind=kind,salary_cap=50000,field_count=20,folder=folder)
                path=Path(folder)/(info['bank_id']+'.dfsbank');payload=load_sensitivity_bank(path)['payload']
                profiles,_=ownership_profiles(payload);samples=[];diagnostics=[]
                outcome_fn=module._scenario_outcomes
                for opponents in (profiles[PROFILES[0]],profiles[PROFILES[2]]):
                    batch=[]
                    def capture(*args,**kwargs):
                        v=outcome_fn(*args,**kwargs);batch.append(copy.deepcopy(v));return v
                    with patch.object(module,'_scenario_outcomes',side_effect=capture):
                        result=sim(raw,p,scenarios=12,field_lineup_count=40,seed=44,opponent_players=opponents)
                    samples.append(batch);diagnostics.append(result['report']['field_diagnostic'])
                self.assertEqual(len(samples[0]),12);self.assertEqual(samples[0],samples[1])
                self.assertNotEqual(diagnostics[0]['ownership'],diagnostics[1]['ownership'])
                r=run_sensitivity(path,batches=1,scenarios=1000)
                self.assertEqual(r['status'],'completed');self.assertEqual(len(r['rows']),len(scored)*3)
                self.assertEqual(p,before)
