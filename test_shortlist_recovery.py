"""Integrated preservation regressions; no production inputs or native settings."""
import copy
import itertools
import time
import unittest
from unittest.mock import patch

from feasible_shortlist import preserve
from main_window import _deep_shortlist, _lineup_signature
from nfl_simulation import SimLineup
from optimizers import ShowdownLineup
from portfolio_rules import _uniqueness_keys, player_key, select_portfolio


def trap_rows(kind, groups):
    """Each high-ranked trap blocks two lower-ranked, compatible alternatives."""
    rows = []
    for group in range(groups):
        players = [dict(FlexID=f'{group}-{i}', Name=f'Fixture {group}-{i}',
                        Team='A' if i % 2 else 'B', Position='WR', GameKey='A@B',
                        FlexSalary=4000, CptSalary=6000, FlexProjection=10, CptProjection=15)
                   for i in range(11 if kind == 'classic' else 8)]
        core, (a,b,c,d) = players[:-4], players[-4:]
        for pair, rate in (([a,b],90),([a,c],30),([b,d],20)):
            lu = (ShowdownLineup(core[0],core[1:]+pair) if kind == 'showdown'
                  else SimLineup(core+pair))
            lu.sim_metrics = dict(sim_scenarios=1000,sim_top_one_pct=rate)
            rows.append(lu)
    return rows


class ShortlistRecoveryTests(unittest.TestCase):
    def capture(self, rows, requested, kind, rules=None, retained=(), seconds=10,
                individual_ranking=True):
        return preserve(rows,_deep_shortlist,requested,requested,kind=kind,
                        rules=rules or {'min_unique':2},retained=list(retained),reserved=[],
                        signature=_lineup_signature,individual_ranking=individual_ranking,
                        deadline=time.perf_counter()+seconds,cancelled=lambda:False)

    def assert_unique(self, rows, kind, minimum=2):
        keys=[_uniqueness_keys(lu,kind) for lu in rows]
        self.assertTrue(all(len(a-b)>=minimum and len(b-a)>=minimum
                            for a,b in itertools.combinations(keys,2)))

    def test_complete_witness_survives_solver_no_result_classic_and_showdown(self):
        # Boundary: a solver that cannot supply a witness must not be the only
        # route. These are real candidates, preservation and final selection.
        for kind in ('classic','showdown'):
            with self.subTest(kind=kind):
                rows=trap_rows(kind,5);before=copy.deepcopy(rows)
                with patch('bounded_solver.solve',return_value=False):
                    short,witness,report=self.capture(rows,10,kind)
                self.assertEqual(len(witness),10)
                self.assertEqual(report['status'],'preserved')
                self.assertEqual(report['method'],'diversity-first coverage')
                self.assert_unique(witness,kind)
                self.assertEqual(rows,before)
                self.assertEqual({_lineup_signature(r) for r in short},
                                 {_lineup_signature(r) for r in witness})
                result=select_portfolio(copy.deepcopy(short),10,kind=kind,
                    rules={'min_unique':2},individual_ranking=True,allow_relaxation=False,
                    fallback_lineups=witness)
                self.assertEqual(len(result['lineups']),10)
                self.assert_unique(result['lineups'],kind)

    def test_600_requested_preserves_diversity_before_truncation(self):
        rows=trap_rows('showdown',300)
        ordinary=_deep_shortlist(rows,600,individual_ranking=True)
        # Every family contributes only its trap and one alternative: at most
        # 300 compatible rows can survive this quality-only truncation.
        self.assertEqual(len(ordinary),600)
        with patch('bounded_solver.solve',return_value=False):
            short,witness,report=self.capture(rows,600,'showdown')
        self.assertEqual(len(witness),600)
        self.assertEqual(report['method'],'diversity-first coverage')
        self.assert_unique(short,'showdown')

    def test_automatic_showdown_caps_remain_hard_during_diversity_search(self):
        rows=trap_rows('showdown',4)
        with patch('bounded_solver.solve',return_value=False):
            _,witness,report=self.capture(rows,8,'showdown',
                rules={'min_unique':2,'balance_ownership':True},individual_ranking=False)
        self.assertEqual(len(witness),8)
        self.assertEqual(report['method'],'diversity-first coverage')
        total= {}
        captain={}
        for lu in witness:
            for p in [lu['Captain']]+lu['Flex']:
                total[player_key(p)]=total.get(player_key(p),0)+1
            key=player_key(lu['Captain'])
            captain[key]=captain.get(key,0)+1
        self.assertTrue(all(count<=6 for count in total.values()))
        self.assertTrue(all(count<=2 for count in captain.values()))

    def test_explicit_zero_caps_groups_and_retained_are_preserved(self):
        rows=trap_rows('classic',3);retained=[rows[1]]
        excluded=player_key(rows[0][-1])
        rules={'min_unique':2,'player_constraints':{excluded:{'MaxPct':0}},
               'groups':[{'type':'at_most_one','player_keys':[player_key(rows[3][-2]),player_key(rows[3][-1])]}]}
        with patch('bounded_solver.solve',return_value=False):
            short,witness,_=self.capture(rows,6,'classic',rules,retained)
        # A zero cap removes the required complementary alternative, so no
        # complete witness is legal. Never publish a partial one as complete.
        self.assertFalse(witness)
        self.assertIn(_lineup_signature(retained[0]),list(map(_lineup_signature,short)))

    def test_captain_swap_does_not_evade_uniqueness_two(self):
        rows=trap_rows('showdown',1)
        first=rows[1]; swapped=ShowdownLineup(first['Flex'][0],[first['Captain']]+first['Flex'][1:])
        swapped.sim_metrics=first.sim_metrics.copy()
        with patch('bounded_solver.solve',return_value=False):
            _,witness,_=self.capture([first,swapped],2,'showdown')
        self.assertFalse(witness)

    def test_retained_conflict_and_impossible_caps_never_form_witness(self):
        for rules,retained in [({'min_unique':2},trap_rows('classic',1)[:2]),
                               ({'min_unique':2,'max_team_pct':50},[])]:
            with self.subTest(rules=rules,retained=bool(retained)), patch('bounded_solver.solve',return_value=False):
                _,witness,_=self.capture(trap_rows('classic',2),4,'classic',rules,retained)
                self.assertFalse(witness)

    def test_cancel_and_expired_budget_skip_new_work(self):
        rows=trap_rows('showdown',3)
        with patch('bounded_solver.solve',side_effect=AssertionError('No solver after cancellation')):
            for cancelled,deadline in [(lambda:True,time.perf_counter()+10),
                                       (lambda:False,time.perf_counter()-1)]:
                _,witness,_=preserve(rows,_deep_shortlist,6,6,kind='showdown',rules={'min_unique':2},
                    retained=[],reserved=[],signature=_lineup_signature,individual_ranking=True,
                    deadline=deadline,cancelled=cancelled)
                self.assertFalse(witness)


if __name__=='__main__':unittest.main()
