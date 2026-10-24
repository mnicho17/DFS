"""Exact opponent occurrence and floating-point arithmetic parity."""
from test_environment import install
install()
from array import array
import copy
import os
import tempfile
import unittest
from unittest.mock import patch
import showdown_simulation as sim
from optimizers import ShowdownOptimizer
from nfl_simulation import player_key
from test_showdown_performance import _showdown_players
from test_ranking_audit_efficiency import values, report_values
from test_contest_strategy import profile

class OpponentScoreReuseTests(unittest.TestCase):
    def tiny(self):
        return dict(Captain=dict(FlexID='c'), Flex=[dict(FlexID=str(i)) for i in range(5)])

    def test_repeated_occurrences_share_score_but_keep_all_ranks(self):
        row=self.tiny(); other=copy.deepcopy(row); other['Captain']=dict(FlexID='d')
        bank=[row]*70+[other]*30; outcomes=dict(c=1., d=2., **{str(i):float(i) for i in range(5)})
        compiled=sim._compile_score_bank(bank)
        self.assertEqual(len(compiled[0]),2)
        with patch.object(sim,'_score_compiled_roster',wraps=sim._score_compiled_roster) as score:
            ranked=sim._score_compiled_bank(compiled,outcomes)
        self.assertEqual(score.call_count,2)
        expected=sorted(sim.showdown_score(row,outcomes) for row in bank)
        self.assertEqual(array('d',ranked).tobytes(),array('d',expected).tobytes())
        self.assertEqual(len(ranked),100)

    def test_flex_permutations_keep_order_sensitive_arithmetic(self):
        row=self.tiny(); other=copy.deepcopy(row); other['Flex'][1],other['Flex'][2]=other['Flex'][2],other['Flex'][1]
        outcomes=dict(c=1.25, **{'0':1e16,'1':1.,'2':-1e16,'3':2.,'4':.25})
        bank=[row,other,row,other]; compiled=sim._compile_score_bank(bank)
        self.assertEqual(len(compiled[0]),2)
        self.assertNotEqual(compiled[0][0][1],compiled[0][1][1])
        expected=sorted(sim.showdown_score(row,outcomes) for row in bank)
        self.assertEqual(array('d',sim._score_compiled_bank(compiled,outcomes)).tobytes(),array('d',expected).tobytes())

    def test_captain_assignment_stays_distinct(self):
        row=self.tiny(); other=copy.deepcopy(row)
        other['Captain'],other['Flex'][0]=other['Flex'][0],other['Captain']
        compiled=sim._compile_score_bank([row,other,row])
        self.assertEqual(len(compiled[0]),2)
        outcomes=dict(c=20., **{str(i):float(i) for i in range(5)})
        self.assertEqual(sim._score_compiled_bank(compiled,outcomes),sorted(sim.showdown_score(r,outcomes) for r in [row,other,row]))

    def test_full_simulation_primary_and_audit_seeds_match_reference(self):
        players=_showdown_players(); original=copy.deepcopy(players)
        rows=ShowdownOptimizer(players)._build_lineups_fast(6)
        for seed in (73129,90210,481516):
            opts=dict(scenarios=40,field_lineup_count=60,seed=seed)
            reference=sim.simulate_showdown(copy.deepcopy(rows),players,**opts)
            reused=sim.simulate_showdown(copy.deepcopy(rows),players,precompiled_rosters=True,**opts)
            self.assertEqual(values(reused),values(reference))
            self.assertEqual(report_values(reused),report_values(reference))
        self.assertEqual(players,original)

    def test_reference_cache_replays_without_scoring_opponents(self):
        players=_showdown_players();rows=ShowdownOptimizer(players)._build_lineups_fast(4)
        opts=dict(scenarios=15,field_lineup_count=30,seed=90210,scenario_cache=True)
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'DFS_OPTIMIZER_DATA_DIR':folder}):
            reference=sim.simulate_showdown(copy.deepcopy(rows),players,**opts)
            self.assertEqual(reference['report']['scenario_cache']['status'],'saved')
            with patch.object(sim,'_score_compiled_bank',side_effect=AssertionError('warm must replay')),patch.object(sim,'_scenario_outcomes',side_effect=AssertionError('no new outcomes')):
                reused=sim.simulate_showdown(copy.deepcopy(rows),players,precompiled_rosters=True,**opts)
            self.assertEqual(reused['report']['scenario_cache']['reused_scenarios'],15)
            self.assertEqual(values(reused),values(reference))

    def test_cancellation_keeps_same_partial_sample(self):
        players=_showdown_players();rows=ShowdownOptimizer(players)._build_lineups_fast(4);results=[]
        draw=sim._scenario_outcomes
        for compiled in (False,True):
            calls=[]
            def tracked(*args,**kwargs): calls.append(1);return draw(*args,**kwargs)
            with patch.object(sim,'_scenario_outcomes',side_effect=tracked):
                r=sim.simulate_showdown(copy.deepcopy(rows),players,scenarios=50,field_lineup_count=30,seed=90210,precompiled_rosters=compiled,cancel_callback=lambda:len(calls)>=3)
            self.assertEqual(r['report']['scenarios'],3);results.append(r)
        self.assertEqual(values(results[0]),values(results[1]))
        self.assertEqual(report_values(results[0]),report_values(results[1]))

    def test_duplicate_opponents_keep_full_tie_split_payout(self):
        players=_showdown_players();rows=ShowdownOptimizer(players)._build_lineups_fast(4)
        field=[copy.deepcopy(rows[0]) for _ in range(30)]
        outcomes={player_key(p):1. for p in players};results=[]
        for compiled in (False,True):
            with patch.object(sim,'_scenario_outcomes',return_value=outcomes):
                r=sim.simulate_showdown(copy.deepcopy(rows),players,scenarios=5,field_lineup_count=30,diagnostic_field=field,contest_profile=profile(),precompiled_rosters=compiled)
            for row in r['lineups']: self.assertEqual(row.sim_metrics['sim_expected_payout'],8.)
            self.assertEqual(r['report']['field_lineups'],30);results.append(r)
        self.assertEqual(values(results[0]),values(results[1]))
        self.assertEqual(report_values(results[0]),report_values(results[1]))

if __name__=='__main__':unittest.main()
