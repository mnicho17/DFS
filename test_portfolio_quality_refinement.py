"""Quality refinement of preserved portfolios must finish before duplication polish."""
import copy
import threading
import unittest
from unittest.mock import patch

from optimizers import ShowdownLineup
from portfolio_rules import select_portfolio, _candidate_signature


def fixture():
    def player(name, team='A', **extra):
        return dict(Name=name, FlexID=name, FlexNamePlusID=name, CptID='CPT-'+name,
                    Position='WR', Team=team, GameKey='A@B', FlexSalary=6000,
                    CptSalary=9000, FlexProjection=10, CptProjection=15, **extra)

    # One high-ranked lineup consumes two scarce players but supplies only one
    # of the eight required entries. The preserved set uses them separately.
    a=player('scarce-a',MaxPct=12.5,MinPct=12.5)
    b=player('scarce-b',MaxPct=12.5,MinPct=12.5)
    def row(name, anchor, quality, second=None):
        captain=player(name+'-captain',MaxCptPct=12.5)
        flex=[anchor,second or player(name+'-second','B')]
        flex += [player(name+'-fill-'+str(i),'B') for i in range(3)]
        lu=ShowdownLineup(captain,flex)
        lu.sim_metrics=dict(sim_scenarios=100,sim_edge=quality,
            sim_return_index=quality,duplicate_risk=10,
            sim_top_one_pct=quality/10,sim_top_five_pct=quality/5,
            sim_win_rate=quality/100,sim_mean=60+quality/10)
        # Preserve scenario coverage as well as aggregate metrics. Each possible
        # replacement covers its outgoing lineup's paths, so coverage cannot
        # justify keeping the weaker candidate in this fixture.
        lu.sim_top_hits={1,2};lu.sim_top_five_hits={1,2,3}
        lu.sim_win_hits={1};lu.sim_scenario_values={1:1.0,2:1.0}
        return lu

    fallback=[row('required-a',a,70),row('required-b',b,70)]
    upgrades=[]
    for i in range(6):
        anchor=player('lane-'+str(i),MaxPct=12.5)
        fallback.append(row('weak-'+str(i),anchor,20))
        upgrades.append(row('strong-'+str(i),anchor,90))
    trap=row('greedy-trap',a,100,second=b)
    return [trap]+fallback+upgrades,fallback,upgrades


class QualityRefinementTests(unittest.TestCase):
    def select(self, rows, fallback, **kwargs):
        options=dict(rules={'min_unique':2},kind='showdown',fallback_lineups=fallback,
            automatic_recovery=True,allow_relaxation=False,refinement_passes=32,
            refinement_polish_duplication=True)
        options.update(kwargs)
        # A valid fallback already exists; this path must not invoke another
        # solver, relax a cap, or release an incomplete portfolio.
        with patch('portfolio_feasibility.repair',side_effect=AssertionError('unneeded repair')):
            return select_portfolio(rows,8,**options)

    def assert_valid(self, result):
        self.assertEqual(len(result['lineups']),8)
        self.assertTrue(result['report']['compliant'])
        self.assertTrue(result['report']['feasible_shortlist_fallback_used'])
        recovery=result['report']['portfolio_recovery']
        self.assertFalse(recovery['automatic_recovery_ran'])
        self.assertEqual(recovery['starting_automatic_caps'],recovery['effective_automatic_caps'])
        self.assertEqual(result['report']['effective_min_unique'],2)

    def test_fallback_gets_all_six_quality_improvements_before_polishing(self):
        rows,fallback,upgrades=fixture();before=copy.deepcopy(rows)
        result=self.select(rows,fallback)
        self.assert_valid(result)
        selected={_candidate_signature(lu,'showdown') for lu in result['lineups']}
        self.assertTrue(all(_candidate_signature(lu,'showdown') in selected for lu in upgrades))
        self.assertEqual(result['report']['refinement_swaps'],6)
        self.assertEqual(result['report']['duplication_refinement_swaps'],0)
        self.assertEqual(result['report']['refinement_stop_reason'],'duplication local optimum')
        self.assertEqual(rows,before)
        self.assertEqual([lu.sim_metrics for lu in rows],[lu.sim_metrics for lu in before])

    def test_pass_limit_still_bounds_quality_improvement(self):
        rows,fallback,_=fixture()
        result=self.select(rows,fallback,refinement_passes=4)
        self.assert_valid(result)
        self.assertEqual(result['report']['refinement_swaps'],4)
        self.assertEqual(result['report']['refinement_attempts'],4)
        self.assertEqual(result['report']['refinement_stop_reason'],'pass limit')

    def test_retained_entry_is_not_replaced_while_other_quality_improves(self):
        rows,fallback,upgrades=fixture();retained=fallback[-1]
        result=self.select(rows,fallback,retained_lineups=[retained])
        self.assert_valid(result)
        self.assertTrue(any(lu is retained for lu in result['lineups']))
        self.assertEqual(result['report']['refinement_swaps'],5)
        self.assertNotIn(upgrades[-1],result['lineups'])

    def test_deadline_stop_keeps_complete_valid_fallback(self):
        rows,fallback,_=fixture();clock=[0.0]
        def deadline_reached():
            clock[0]+=0.001
            return clock[0]>=0.004
        result=self.select(rows,fallback,refinement_stop_callback=deadline_reached)
        self.assert_valid(result)
        self.assertEqual(result['report']['refinement_stop_reason'],'time budget or cancellation')
        self.assertLess(result['report']['refinement_swaps'],6)
        self.assertLess(clock[0],0.010)

    def test_cancellation_during_refinement_does_not_return_a_portfolio(self):
        rows,fallback,_=fixture();cancelled=threading.Event()
        def stop():
            cancelled.set()
            return True
        with self.assertRaisesRegex(ValueError,'Selection cancelled'):
            self.select(rows,fallback,refinement_stop_callback=stop,
                        selection_cancel_callback=cancelled.is_set)

    def test_player_group_still_blocks_an_otherwise_better_replacement(self):
        rows,fallback,upgrades=fixture()
        blocked=upgrades[0]['Captain']['FlexNamePlusID']
        rules=dict(min_unique=2,groups=[dict(type='never_together',
            player_keys=['lane-0',blocked])])
        result=self.select(rows,fallback,rules=rules)
        self.assert_valid(result)
        self.assertNotIn(upgrades[0],result['lineups'])
        self.assertEqual(result['report']['refinement_swaps'],5)


if __name__=='__main__':unittest.main()
