import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from nfl_simulation import SimLineup
from repeatability import save_bank, load_bank, candidates, run_repeatability, format_report, save_report, identity


class RepeatabilityTests(unittest.TestCase):
    def bank(self,folder):
        rows=[SimLineup([dict(FlexID=str(i),Name='Player '+str(i))],metrics=dict(sim_scenarios=1000,sim_top_one_pct=200-i)) for i in range(200)]
        result=save_bank(rows,[],kind='classic',salary_cap=50000,field_count=20,folder=folder,input_id='test-input')
        return Path(folder)/(result['bank_id']+'.dfsbank')

    def test_fixed_candidates_fresh_seeds_aggregate_and_no_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            path=self.bank(folder);original=path.read_bytes();seeds=[]
            def sim(rows,players,**kw):
                seeds.append(kw['seed'])
                for i,lu in enumerate(rows):lu.sim_metrics.update(sim_scenarios=1000,sim_top_one_pct=i if len(seeds)==2 else 200-i)
                return dict(lineups=rows,report={'scenarios':1000})
            report=run_repeatability(path,batches=2,scenarios=1000,simulate=sim)
            self.assertEqual(len(set(seeds)),2)
            self.assertEqual(report['rows'][0]['best_rank'],1)
            self.assertEqual(report['rows'][0]['worst_rank'],200)
            self.assertEqual(report['rows'][0]['top50_batches'],1)
            self.assertEqual(report['rows'][0]['mean_top1'],100)
            self.assertEqual(path.read_bytes(),original)
            save_report(Path(folder)/'result.json',report)
            self.assertTrue((Path(folder)/'result.csv').exists())
            self.assertIn('Original #1',format_report(report))

    def test_partial_batch_excluded_and_identity_changes_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path=self.bank(folder)
            report=run_repeatability(path,batches=2,scenarios=1000,simulate=lambda rows,p,**kw:dict(lineups=rows,report={'scenarios':999}))
            self.assertEqual(report['completed_batches'],0)
            self.assertNotIn('mean_top1',report['rows'][0])
            def bad(rows,p,**kw):return dict(lineups=rows[:-1],report={'scenarios':1000})
            with self.assertRaisesRegex(ValueError,'identities'):run_repeatability(path,batches=2,scenarios=1000,simulate=bad)

    def test_tamper_and_version_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path=self.bank(folder)
            with patch('repeatability.model_version',return_value='changed'):
                with self.assertRaisesRegex(ValueError,'code changed'):load_bank(path)
            bank=json.loads(path.read_text());bank['payload']['field_count']=12;path.write_text(json.dumps(bank))
            with self.assertRaisesRegex(ValueError,'modified'):load_bank(path)

    def test_showdown_captain_identity_roundtrip(self):
        from optimizers import ShowdownLineup
        roster=[dict(FlexID=str(i),Name=str(i)) for i in range(6)]
        a=ShowdownLineup(roster[0],roster[1:]);b=ShowdownLineup(roster[1],[roster[0]]+roster[2:])
        for lu in (a,b):lu.sim_metrics=dict(sim_scenarios=1000,sim_top_one_pct=1)
        with tempfile.TemporaryDirectory() as folder:
            info=save_bank([a,b],roster,kind='showdown',salary_cap=50000,field_count=20,folder=folder)
            loaded=candidates(load_bank(Path(folder)/(info['bank_id']+'.dfsbank'))['payload'])
            self.assertEqual([identity(lu,'showdown') for lu in loaded],[identity(lu,'showdown') for lu in (a,b)])

    def test_real_simulators_both_formats(self):
        from test_nfl_logic import _fixture_players
        from test_showdown_performance import _showdown_players
        from nfl_simulation import generate_nfl_field_lineups,simulate_nfl_contest
        from showdown_simulation import generate_showdown_field,simulate_showdown
        for kind,players in [('classic',_fixture_players()),('showdown',_showdown_players())]:
            if kind=='classic':
                raw,_=generate_nfl_field_lineups(players,3,seed=7)
                sim=simulate_nfl_contest
            else:
                raw=generate_showdown_field(players,3,seed=7);sim=simulate_showdown
            scored=sim(raw,players,scenarios=1000,field_lineup_count=20)['lineups']
            with tempfile.TemporaryDirectory() as folder:
                info=save_bank(scored,players,kind=kind,salary_cap=50000,field_count=20,folder=folder)
                path=Path(folder)/(info['bank_id']+'.dfsbank')
                r=run_repeatability(path,batches=2,scenarios=1000)
                self.assertEqual(r['status'],'completed')
                self.assertEqual(len(r['rows']),len(scored))
