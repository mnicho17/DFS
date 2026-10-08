import copy
import unittest
from unittest.mock import patch
from exposure_cap_diagnostics import compare,text
from optimizers import ShowdownLineup
from portfolio_rules import select_portfolio,player_key


def bank():
    rows=[]
    qb=dict(Name='QB',FlexID='qb',Position='QB',Team='A',FlexProjection=20,FlexSalary=8000)
    for i in range(16):
        captain=dict(Name=f'C{i}',FlexID=f'c{i}',Position='WR',Team='B',FlexProjection=15,FlexSalary=5000,CptSalary=7500)
        flex=[dict(Name=f'{i}-F{j}',FlexID=f'{i}-{j}',Position='WR',Team='A',FlexProjection=10,FlexSalary=5000) for j in range(5)]
        if i<12:flex[0]=qb
        else:flex[0]['Position']='K';flex[1]['Position']='DST'
        lu=ShowdownLineup(captain,flex)
        lu.sim_metrics=dict(sim_scenarios=1000,sim_top_one_pct=16-i,sim_edge=90,sim_return_index=85,duplicate_risk=30)
        rows.append(lu)
    return rows,qb


class CapDiagnosticsTests(unittest.TestCase):
    def test_pressure_counts_roles_effective_caps_and_no_mutation(self):
        rows,qb=bank();before=copy.deepcopy(rows);key=player_key(qb)
        result=compare(rows,rows[12:16],4,{'total':{key:3},'captain':{}},
            {'total':{key:4},'captain':{}},{key:qb})
        self.assertEqual(result['ranked'],dict(lineups=4,zero_qb=0,two_plus_specialists=0))
        self.assertEqual(result['selected'],dict(lineups=4,zero_qb=4,two_plus_specialists=4))
        r=result['players'][0]
        self.assertEqual((r['ranked_count'],r['selected_count'],r['starting_cap'],r['effective_cap'],r['ranked_excess']),(4,0,3,4,0))
        self.assertIn('does not isolate any one cause',' '.join(text(result)))
        self.assertEqual(rows,before)
        self.assertEqual([r.sim_metrics for r in rows],[r.sim_metrics for r in before])

    def test_incomplete_scoring_does_not_invent_ranked_comparison(self):
        rows,_=bank();rows[-1].sim_metrics={}
        self.assertEqual(compare(rows,rows[:4],4,{}, {},{})['status'],'unavailable')
        self.assertEqual(text(compare([],[],4,{}, {},{})),[])

    def test_selector_report_does_not_change_selected_lineups(self):
        rows,qb=bank();rules=dict(min_unique=1,balance_ownership=True)
        result=select_portfolio(rows,4,kind='showdown',rules=rules,allow_relaxation=False)
        diagnostic=result['report']['exposure_cap_diagnostic']
        self.assertEqual(diagnostic['status'],'available')
        pressure=next(r for r in diagnostic['players'] if r['key']==player_key(qb) and r['role']=='total')
        self.assertEqual(pressure['starting_cap'],3)
        self.assertEqual(pressure['ranked_count'],4)
        self.assertIn('Automatic exposure-cap pressure',result['report']['text'])
        with patch('exposure_cap_diagnostics.compare',return_value=None):
            control=select_portfolio(rows,4,kind='showdown',rules=rules,allow_relaxation=False)
        self.assertEqual(result['lineups'],control['lineups'])

    def test_manual_cap_is_not_reported_as_automatic(self):
        rows,qb=bank();qb['MaxPct']=50
        result=select_portfolio(rows,4,kind='showdown',rules=dict(min_unique=1,balance_ownership=True),allow_relaxation=False)
        self.assertFalse(any(r['key']==player_key(qb) and r['role']=='total' for r in result['report']['exposure_cap_diagnostic']['players']))

    def test_captain_pressure_is_separate_from_total(self):
        rows,_=bank();key=player_key(rows[0]['Captain'])
        result=compare(rows,rows[:4],4,{'captain':{key:0}},{'captain':{key:0}},{key:rows[0]['Captain']})
        self.assertEqual(result['players'][0]['role'],'captain')
        self.assertEqual(result['players'][0]['ranked_excess'],1)
