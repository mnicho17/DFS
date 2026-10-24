"""Selector parity against merged main 708aa15; immutable totals stay per call."""
from test_environment import install
install()
import copy
import hashlib
import json
import unittest
from portfolio_rules import select_portfolio, _candidate_signature
from scripts.benchmark_scenario_replay import exact
from test_portfolio_quality_refinement import fixture

BASELINE = {'normal': 'afc2bbd2645fae519d574ad8a623a2ba2fa029f6a23d6170b151f558b6477e70', 'refined': '057c8809600b2df938fcc3233311bb0efa37e34246cf593fd8efc94aed760ab5', 'retained': 'abc0233471f3b70b0d0bb939166a0873ea37cb861b4f2c288b89eab5aa111689', 'zero': 'afc2bbd2645fae519d574ad8a623a2ba2fa029f6a23d6170b151f558b6477e70', 'empty': 'afc2bbd2645fae519d574ad8a623a2ba2fa029f6a23d6170b151f558b6477e70', 'classic': 'abbd05f1bc76aa0e64442a2207e0e4c554c24f72e04ae81729fdf696b4504272'}

def case(label):
    rows,fallback,_=fixture()
    for n,row in enumerate(rows):
        row.sim_scenario_values={i: (0.0 if i%7==0 else -1.0 if i%5==0 else (n+1)/(i+1)) for i in range(80)}
    options=dict(kind='showdown',rules={'min_unique':2},allow_relaxation=False,
                 automatic_recovery=True,fallback_lineups=fallback)
    if label in ('normal','zero','empty'):
        options.update(automatic_recovery=False,fallback_lineups=None,rules={'min_unique':1})
        for row in rows:
            for player in [row['Captain']]+row['Flex']:
                for key in ('MaxPct','MinPct','MaxCptPct'):player.pop(key,None)
    if label=='refined': options.update(refinement_passes=8,refinement_polish_duplication=True)
    if label=='retained': options.update(retained_lineups=[fallback[-1]],refinement_passes=4)
    if label=='zero':
        for row in rows: row.sim_scenario_values={0:0.,1:-1.,2:-2.}
    if label=='empty':
        for row in rows: row.sim_scenario_values={}
    return rows,options

def digest(result):
    report=copy.deepcopy(result['report']);report.pop('refinement_seconds',None);report.pop('text',None)
    def ordered(value):
        if isinstance(value,dict):return {k:ordered(v) for k,v in value.items()}
        if isinstance(value,list):return sorted([ordered(v) for v in value],key=lambda v:json.dumps(exact(v),sort_keys=True))
        return value
    report=ordered(report)
    value=dict(signatures=[_candidate_signature(r,result['report']['kind']) for r in result['lineups']],report=report)
    return hashlib.sha256(json.dumps(exact(value),sort_keys=True,separators=(',',':')).encode()).hexdigest()

def classic_case():
    from test_nfl_logic import _fixture_players
    from optimizers import MultiSportClassicOptimizer
    from nfl_simulation import simulate_nfl_contest
    players=_fixture_players()
    rows=MultiSportClassicOptimizer(players).build_lineups(12)
    return simulate_nfl_contest(rows,players,scenarios=30,field_lineup_count=30,seed=90210)['lineups']

class PortfolioValueTotalTests(unittest.TestCase):
    def test_classic_simulated_portfolio_matches_original(self):
        rows=classic_case();before=copy.deepcopy(rows)
        result=select_portfolio(rows,4,kind='classic',rules={'min_unique':1},allow_relaxation=False)
        self.assertEqual(digest(result),BASELINE['classic'])
        self.assertEqual(rows,before)
        self.assertEqual([r.sim_scenario_values for r in rows],[r.sim_scenario_values for r in before])

    def test_original_order_and_complete_report_across_selection_paths(self):
        for label in ('normal','refined','retained','zero','empty'):
            with self.subTest(label=label):
                rows,options=case(label);before=copy.deepcopy(rows)
                result=select_portfolio(rows,8,**options)
                self.assertEqual(digest(result),BASELINE[label])
                self.assertEqual(rows,before)
                self.assertEqual([r.sim_scenario_values for r in rows],[r.sim_scenario_values for r in before])

    def test_new_call_recalculates_changed_values(self):
        rows,options=case('normal')
        select_portfolio(rows,8,**options)
        for n,row in enumerate(rows):row.sim_scenario_values={0:float(n),1:float(20-n)}
        repeated=select_portfolio(rows,8,**options)
        fresh=select_portfolio(copy.deepcopy(rows),8,**copy.deepcopy(options))
        self.assertEqual(digest(repeated),digest(fresh))

    def test_unused_values_are_not_eagerly_converted(self):
        rows,options=case('normal');options.update(automatic_recovery=False,fallback_lineups=None)
        for row in rows:
            row.sim_metrics.pop('sim_edge')
            row.sim_scenario_values={0:'unused'}
        result=select_portfolio(rows,8,**options)
        self.assertTrue(result['lineups'])

    def test_individual_ranking_keeps_values_unused(self):
        rows,options=case('normal');options.update(individual_ranking=True)
        for row in rows:row.sim_scenario_values={0:'unused'}
        self.assertEqual(len(select_portfolio(rows,8,**options)['lineups']),8)

if __name__=='__main__':unittest.main()
