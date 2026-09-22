import copy
import unittest
from unittest.mock import patch
from showdown_coverage import expand_capped_candidates


class CoverageTests(unittest.TestCase):
    def test_fast_showdown_finishes_without_classic_only_report(self):
        from main_window import LineupBuildWorker
        players=[dict(FlexID=str(i),Name=str(i),Team='A' if i%2 else 'B',
            Position='RB',FlexSalary=7000,CptSalary=10500,FlexProjection=10,
            CptProjection=15,NFLDepthOrder=1) for i in range(6)]
        row=dict(Captain=players[0],Flex=players[1:])
        worker=LineupBuildWorker(players,kind='showdown',num_lineups=1,salary_cap=50000,
            sim_enabled=True,compute_mode='Fast',portfolio_rules={'balance_ownership':False})
        results=[];errors=[]
        worker.finished.connect(results.append);worker.error.connect(errors.append)
        with patch('main_window.ShowdownOptimizer.build_lineups',return_value=[row]), \
             patch('main_window.compare_nfl_lineups_to_preset',side_effect=AssertionError('Classic-only comparison')):
            worker.run()
        self.assertEqual(errors,[])
        self.assertEqual(len(results),1)
        self.assertEqual(len(results[0]['lineups']),1)
        self.assertNotIn('preset_comparison',results[0]['sim_report'])

    def run_probe(self, rows, players, rules=None, **kwargs):
        return expand_capped_candidates(rows, players, 150, rules or {}, salary_cap=50000,
            own_mode='Balanced', own_weight=.15, build_style='Strategic', **kwargs)

    def test_target_exclusion_is_temporary_and_keeps_original_players(self):
        players=[dict(FlexID=str(i),Name=str(i),MaxPct=50 if i==0 else 100) for i in range(7)]
        rows=[dict(Captain=players[0],Flex=players[1:6])]
        before=copy.deepcopy(players)
        def sample(opt, **kwargs):
            self.assertTrue(opt.players[0]['FadeFlex'])
            self.assertTrue(opt.players[0]['FadeCpt'])
            return [dict(Captain=opt.players[1],Flex=opt.players[2:])]*2
        class Fake:
            def __init__(self, ps, **kwargs): self.players=ps
            _build_lineups_fast=sample
        with patch('optimizers.ShowdownOptimizer',Fake):
            result=self.run_probe(rows,players)
        self.assertEqual(len(result),1)
        self.assertIs(result[0]['Captain'],players[1])
        self.assertEqual(players,before)
        self.assertNotIn(players[0],result[0]['Flex'])

    def test_locked_uncapped_cancelled_and_zero_budget_skip_exploration(self):
        players=[dict(FlexID=str(i),Name=str(i),LockFlex=True) for i in range(6)]
        rows=[dict(Captain=players[0],Flex=players[1:])]
        with patch('optimizers.ShowdownOptimizer',side_effect=AssertionError('unneeded search')):
            self.assertEqual(self.run_probe(rows,players),[])
            for p in players:p.pop('LockFlex')
            self.assertEqual(self.run_probe(rows,players,{'balance_ownership':False}),[])
            self.assertEqual(self.run_probe(rows,players,cancelled=lambda:True),[])
            self.assertEqual(self.run_probe(rows,players,seconds=0),[])


if __name__=='__main__':unittest.main()
