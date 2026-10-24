"""Exact metrics frozen from merged main 08a44ad, plus boundary/tie checks."""
from test_environment import install
install()
import copy
import hashlib
import json
import os
import tempfile
import unittest
from unittest.mock import patch
import showdown_simulation as sim
from nfl_simulation import player_key
from optimizers import ShowdownOptimizer
from scripts.benchmark_scenario_replay import exact
from test_scenario_cache import result_values
from test_showdown_performance import _showdown_players
from test_contest_strategy import profile

BASELINE = {'seed_73129': 'd293fbb27338cc0bf6af250b4695ca3fc613266bc18d0ca291994df1545eab65', 'seed_90210': '48ba5475eb3bb1e702a8fe1a5a9868f9d2dd56450e97d7eedcc4165c8ddc7ff9', 'seed_481516': '6ece24ab50f10da635da6c495c4ea9e02b1e148f3619722873e8c375af6ed0b9', 'field_1': 'a2aca05a6388c0e9290226570a3934381e0b0da251c0d22f4200a8366223240d', 'field_2': '6d3e022dcebcf3c6c9848d0f373b0f45f2a1f6384fdda3eb6d11bdb4b3339ba8', 'DOUBLE_UP': 'b34ff6157745d0d022fd117e85c8b0c877b3e8a2dffe8625fe2d47a31d7cb546', 'MULTIPLIER': 'c40afd3c5b554c54884678d541c002e724161cf96dc9815c99a1607043bf54d7', 'TOURNAMENT': 'aab47867c9edc1e6fe9d755d158b39c2167a6f6a32d77c9fcbc36c5fafde906c', 'cancelled': 'c9e092e28b64ad469a5d52a6b62b19278c9af5cfa70a23601803ab1f20ea0733'}

class CandidateMetricTests(unittest.TestCase):
    def setUp(self):
        self.players=_showdown_players()
        self.rows=ShowdownOptimizer(self.players)._build_lineups_fast(6)

    def check_result(self, result, label):
        encoded=json.dumps(exact(result_values(result)),sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        self.assertEqual(hashlib.sha256(encoded).hexdigest(),BASELINE[label])

    def run_case(self, **options):
        before=copy.deepcopy(self.players)
        result=sim.simulate_showdown(copy.deepcopy(self.rows),self.players,**options)
        self.assertEqual(self.players,before)
        return result

    def test_primary_and_audit_seeds_match_original_metrics_and_hits(self):
        for seed in (73129,90210,481516):
            for compiled in (False,True):
                with self.subTest(seed=seed,compiled=compiled):
                    r=self.run_case(scenarios=40,field_lineup_count=60,seed=seed,precompiled_rosters=compiled)
                    self.assertEqual(r['report']['scenarios'],40)
                    self.check_result(r,'seed_'+str(seed))

    def test_one_and_two_opponent_percentile_boundaries_match_main(self):
        for count in (1,2):
            for compiled in (False,True):
                with self.subTest(count=count,compiled=compiled):
                    r=self.run_case(scenarios=40,field_lineup_count=count,seed=90210,precompiled_rosters=compiled)
                    self.assertEqual(r['report']['field_lineups'],count)
                    self.check_result(r,'field_'+str(count))

    def test_all_contest_objectives_keep_original_payout_metrics(self):
        for objective,payouts in (('DOUBLE_UP','1-4=20'),('MULTIPLIER','1-2=50'),('TOURNAMENT','1=70\n2-4=10')):
            for compiled in (False,True):
                with self.subTest(objective=objective,compiled=compiled):
                    r=self.run_case(scenarios=20,field_lineup_count=30,seed=90210,contest_profile=profile(objective,payouts),precompiled_rosters=compiled)
                    self.check_result(r,objective)

    def test_cancelled_sample_keeps_original_values(self):
        draw=sim._scenario_outcomes
        for compiled in (False,True):
            calls=[]
            def tracked(*args,**kwargs): calls.append(1);return draw(*args,**kwargs)
            with patch.object(sim,'_scenario_outcomes',side_effect=tracked):
                r=self.run_case(scenarios=50,field_lineup_count=30,seed=90210,precompiled_rosters=compiled,cancel_callback=lambda:len(calls)>=3)
            self.assertEqual(r['report']['scenarios'],3)
            self.check_result(r,'cancelled')

    def tied_case(self, points, count):
        outcomes={player_key(p):points for p in self.players}
        field=[copy.deepcopy(self.rows[0]) for _ in range(count)]
        for compiled in (False,True):
            with patch.object(sim,'_scenario_outcomes',return_value=outcomes):
                r=self.run_case(scenarios=7,field_lineup_count=count,diagnostic_field=field,precompiled_rosters=compiled)
            for row in r['lineups']:
                for hits in (row.sim_top_hits,row.sim_top_five_hits,row.sim_win_hits): self.assertEqual(hits,set(range(7)))
                self.assertEqual(row.sim_metrics['sim_cash_rate'],100.)
                self.assertEqual(row.sim_metrics['sim_bust_rate'],0.)
                self.assertEqual(row.sim_metrics['sim_mean'],6.5*points)
                self.assertEqual(row.sim_scenario_values,{i:16. for i in range(7)})

    def test_singleton_zero_score_ties_include_every_hit(self): self.tied_case(0.,1)
    def test_negative_score_ties_keep_original_win_and_bust_boundaries(self): self.tied_case(-1.,2)

    def test_cold_and_warm_cache_keep_original_metrics_without_new_outcomes(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'DFS_OPTIMIZER_DATA_DIR':folder}):
            options=dict(scenarios=40,field_lineup_count=60,seed=90210,precompiled_rosters=True,scenario_cache=True)
            cold=self.run_case(**options)
            self.assertEqual(cold['report']['scenario_cache']['status'],'saved')
            with patch.object(sim,'_scenario_outcomes',side_effect=AssertionError('must replay')),patch.object(sim,'_score_compiled_bank',side_effect=AssertionError('opponents must replay')):
                warm=self.run_case(**options)
            self.assertEqual(warm['report']['scenario_cache']['reused_scenarios'],40)
            self.check_result(cold,'seed_90210');self.check_result(warm,'seed_90210')

if __name__=='__main__':unittest.main()
