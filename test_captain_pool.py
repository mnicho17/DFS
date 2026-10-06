from test_environment import install
install()
from collections import Counter
from copy import deepcopy
import unittest
from captain_pool import prepare_captain_pool
from optimizers import ShowdownOptimizer
from portfolio_rules import select_portfolio,player_key
from portfolio_recovery import explicit_lineup_ok
from test_showdown_performance import _showdown_players
from test_portfolio_recovery import bank
from specialist_diagnostics import compare


class CaptainPoolTests(unittest.TestCase):
    def test_single_captain_reconciles_manual_limits_without_mutating_inputs(self):
        players=_showdown_players();players[1].update(LockCpt=True,MaxPct=10,MaxCptPct=0)
        rules={'player_constraints':{player_key(players[1]):{'MaxPct':20,'MaxCptPct':5}}}
        before=deepcopy((players,rules))
        output,effective,changes=prepare_captain_pool(players,rules,50)
        self.assertEqual((players,rules),before)
        c=effective['player_constraints'][player_key(players[1])]
        self.assertEqual((c['MaxPct'],c['MinCptPct'],c['MaxCptPct']),(100,100,100))
        self.assertTrue(any(c['before']==20 for c in changes))
        rows=ShowdownOptimizer(output,seed=13,build_style='Balanced').build_lineups(50)
        result=select_portfolio(rows,50,kind='showdown',rules=effective,automatic_recovery=True,allow_relaxation=False)
        self.assertEqual(len(result['lineups']),50)
        self.assertTrue(all(player_key(r['Captain'])==player_key(players[1]) for r in result['lineups']))

    def test_multiple_captains_generate_and_select_only_pool_with_exact_shares(self):
        players=_showdown_players()
        for i in (1,19):players[i]['LockCpt']=True
        output,rules,changes=prepare_captain_pool(players,{},10)
        rows=ShowdownOptimizer(output,seed=13,build_style='Balanced').build_lineups(100)
        result=select_portfolio(rows,10,kind='showdown',rules=rules,automatic_recovery=True,allow_relaxation=False)
        counts=Counter(player_key(r['Captain']) for r in result['lineups'])
        self.assertEqual(counts,{player_key(players[1]):5,player_key(players[19]):5})
        self.assertTrue(all(explicit_lineup_ok({player_key(p) for p in r['Flex']}|{player_key(r['Captain'])},
                                             player_key(r['Captain']),{player_key(p):p for p in output}) for r in rows))

    def test_retained_captains_are_preserved_when_allocating_shares(self):
        players=_showdown_players()
        for i in (1,19):players[i]['LockCpt']=True
        retained=[dict(Captain=players[1]) for _ in range(7)]
        _,rules,_=prepare_captain_pool(players,{},10,retained)
        c=rules['player_constraints']
        self.assertEqual(c[player_key(players[1])]['MinCptPct'],70)
        self.assertEqual(c[player_key(players[19])]['MinCptPct'],30)

    def test_no_lock_is_unchanged_and_invalid_pool_fails_clearly(self):
        players=_showdown_players();rules={'min_unique':3}
        self.assertEqual(prepare_captain_pool(players,rules,10),(players,rules,[]))
        for p in players[:2]:p['LockCpt']=True
        with self.assertRaises(ValueError):prepare_captain_pool(players,{},1)
        players[0]['FadeCpt']=True
        with self.assertRaises(ValueError):prepare_captain_pool(players,{},10)

    def test_specialist_diagnostics_do_not_change_lineups_or_scores(self):
        rows=bank(n=10,captain=5,specialist=3);before=deepcopy(rows)
        d=compare(rows,rows[:2]);self.assertEqual(rows,before)
        self.assertEqual(d['candidates']['counts']['specialist_captains'],3)
        self.assertEqual(d['selected']['counts']['specialist_captains'],2)

    def test_small_exact_solver_obeys_captain_pool(self):
        players=_showdown_players()
        for i in (1,19):players[i]['LockCpt']=True
        rows=ShowdownOptimizer(players,seed=15,build_style='Balanced')._build_lineups_pulp(4)
        allowed={player_key(players[i]) for i in (1,19)}
        self.assertEqual(len(rows),4)
        self.assertTrue(all(player_key(r['Captain']) in allowed for r in rows))

    def test_worker_reports_adjustments_on_completed_build(self):
        from main_window import LineupBuildWorker
        players=_showdown_players();players[1].update(LockCpt=True,MaxPct=10,MaxCptPct=5)
        worker=LineupBuildWorker(players,kind='showdown',num_lineups=8,salary_cap=50000,
                                build_style='Balanced',sim_enabled=False,salary_strategy='Balanced Spend')
        results=[];errors=[]
        worker.finished.connect(results.append);worker.error.connect(errors.append)
        worker.run()
        self.assertFalse(errors,errors);self.assertEqual(len(results),1)
        self.assertEqual(len(results[0]['lineups']),8)
        report=results[0]['portfolio_report']
        self.assertTrue(report['captain_pool_adjustments'])
        self.assertIn('Captain pool adjustments',report['text'])
