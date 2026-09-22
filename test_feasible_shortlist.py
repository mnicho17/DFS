import copy
import itertools
import time
import unittest
from unittest.mock import patch
from nfl_simulation import SimLineup
from main_window import _deep_shortlist, _lineup_signature
from feasible_shortlist import preserve
from portfolio_rules import select_portfolio
from portfolio_feasibility import conflict_index
from selection_shortage import PortfolioSelectionShortage


class FeasibleShortlistTests(unittest.TestCase):
    def rows(self):
        a,b,c,d=[dict(FlexID=k,Name=k,Team=k,FlexProjection=10,MaxPct=50 if k in 'ab' else None) for k in 'abcd']
        return [SimLineup(ps,metrics=dict(sim_scenarios=10,sim_top_one_pct=rate))
                for ps,rate in [([a,b],90),([a,c],30),([b,d],20)]]

    def test_preserves_compliant_alternatives_discarded_by_quality_shortlist(self):
        rows=self.rows()
        with self.assertRaises(PortfolioSelectionShortage):
            select_portfolio(_deep_shortlist(rows,2,individual_ranking=True),2,
                individual_ranking=True,allow_relaxation=False)
        short,witness,report=preserve(rows,_deep_shortlist,2,2,kind='classic',rules={},retained=[],reserved=[],
            signature=_lineup_signature,individual_ranking=True,deadline=time.perf_counter()+5,cancelled=lambda:False)
        self.assertEqual(report['status'],'preserved')
        self.assertEqual({_lineup_signature(lu) for lu in short},{('a','c'),('b','d')})
        result=select_portfolio(short,2,individual_ranking=True,allow_relaxation=False,fallback_lineups=witness)
        self.assertEqual(len(result['lineups']),2)

    def test_validated_copies_supply_fallback_without_another_solver(self):
        rows=self.rows();witness=rows[1:]
        with patch('portfolio_feasibility.repair',side_effect=AssertionError('unneeded solver')):
            result=select_portfolio(copy.deepcopy(rows),2,individual_ranking=True,
                allow_relaxation=False,fallback_lineups=witness)
        self.assertTrue(result['report']['feasible_shortlist_fallback_used'])
        self.assertEqual(len(result['lineups']),2)
        # A stale set cannot evade a newly tightened cap.
        changed=copy.deepcopy(rows)
        for lu in changed:
            for p in lu:
                if p['FlexID']=='b':p['MaxPct']=0
        with self.assertRaises(PortfolioSelectionShortage):
            select_portfolio(changed,2,individual_ranking=True,allow_relaxation=False,fallback_lineups=witness)

    def test_subset_conflicts_match_pairwise_definition(self):
        for size in (6,7,9):
            rows=list(itertools.combinations(range(size+2),size))
            for minimum in (1,2,3):
                edges,groups=conflict_index(rows,set,minimum)
                for i,a in enumerate(rows):
                    for b in rows[:i]:
                        expected=len(set(a)-set(b))<minimum
                        self.assertEqual(id(b) in edges.get(id(a),set()),expected)
                if groups is not None:
                    self.assertTrue(all(all(id(b) in edges[id(a)] for a,b in itertools.combinations(
                        [r for r in rows if id(r) in group],2)) for group in groups))

    def test_retained_reservations_take_priority_and_cancel_skips_solver(self):
        rows=self.rows()
        with patch('portfolio_rules.select_portfolio',side_effect=AssertionError('cancelled preflight')):
            short,witness,report=preserve(rows,_deep_shortlist,1,1,kind='classic',rules={},retained=[rows[2]],
                reserved=[_lineup_signature(rows[0])],signature=_lineup_signature,individual_ranking=True,
                deadline=time.perf_counter()-1,cancelled=lambda:True)
        self.assertEqual(_lineup_signature(short[0]),_lineup_signature(rows[2]))
        self.assertFalse(witness)

    def test_report_discloses_preservation_and_recovery(self):
        from build_diagnostics import create_build_diagnostic, format_build_report
        record = create_build_diagnostic(context={'sport':'NFL','kind':'showdown','settings':{}},
            timing_report={'compute_mode':'Deep','portfolio_feasibility':{
                'status':'preserved','lineups':150,'searched':12000,'seconds':1.3}},
            portfolio_report={'feasible_shortlist_fallback_used':True},
            sim_report={}, lineups=[])
        self.assertEqual(record['portfolio_feasibility']['lineups'],150)
        self.assertTrue(record['feasible_shortlist_fallback_used'])
        text=format_build_report(record)
        self.assertIn('Portfolio feasibility before shortlist: preserved',text)
        self.assertIn('no limits were changed',text)


if __name__=='__main__':unittest.main()
