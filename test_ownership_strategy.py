import copy
import tempfile
import unittest
from ownership_strategy import fit_field, leverage_report, exposures, accuracy_lines
from nfl_simulation import SimLineup
from field_diagnostics import summarize_field


class OwnershipStrategyTests(unittest.TestCase):
    def test_ownership_estimation_does_not_fit_to_its_previous_forecast(self):
        from unittest.mock import patch
        from test_nfl_logic import _fixture_players
        from nfl_simulation import simulate_nfl_field_ownership
        with patch('ownership_strategy.fit_field',side_effect=AssertionError('feedback in estimator')):
            result=simulate_nfl_field_ownership(_fixture_players(),10)
        self.assertEqual(result['meta']['valid_lineups'],10)

    def test_feedback_improves_without_mutating_forecasts_or_accepting_worse_field(self):
        players=[dict(FlexID=str(i),Name=str(i),ProjOwnPct=90,OwnershipUnits='percent_of_entries') for i in range(10)]
        before=copy.deepcopy(players);initial=[players[:9] for _ in range(10)]
        better=[[p for j,p in enumerate(players) if j!=i] for i in range(10)]
        result=fit_field(initial,players,lambda p,n:better if n==1 else initial)
        self.assertEqual(result.ownership_fit['after_mae_pp'],0)
        self.assertGreater(result.ownership_fit['before_mae_pp'],0)
        self.assertEqual(result,better);self.assertEqual(players,before)
        for p in players:p.pop('OwnershipUnits')
        self.assertEqual(fit_field(initial,players,lambda p,n:self.fail()).ownership_fit['status'],'skipped')

    def test_leverage_denominators_and_zero_unknown_distinction(self):
        a=dict(FlexID='a',Name='A',Team='BUF',ProjOwnPct=0,OwnershipUnits='percent_of_entries')
        b=dict(FlexID='b',Name='B',Team='BUF',ProjOwnPct=4)
        rows=[SimLineup([a],metrics={'sim_scenarios':1000,'sim_top_one_pct':5}),SimLineup([b],metrics={'sim_scenarios':1000,'sim_top_one_pct':1})]
        report=leverage_report(rows,[rows[0]],[a,b],summarize_field([[a]],[a,b]))
        self.assertEqual(report['rows'][0]['contender_pct'],50)
        self.assertEqual(report['rows'][0]['gap_pp'],50)
        self.assertEqual(report['rows'][0]['your_pct'],100)
        self.assertIsNone(report['rows'][1]['gap_pp'])

    def test_real_showdown_matching_preserves_legal_rosters_and_slot_totals(self):
        from test_showdown_performance import _showdown_players
        from showdown_simulation import active_showdown_players,generate_showdown_field,validate_showdown_lineup
        from ownership_estimates import quick_ownership
        from optimizers import _pkey
        p=active_showdown_players(_showdown_players());own=quick_ownership(p,mode='showdown',sport='NFL')
        for v in p:
            k=_pkey(v);v.update(ProjOwnPct=own['total'][k],ProjCptOwnPct=own['cpt'][k],ProjFlexOwnPct=own['flex'][k],OwnershipUnits='percent_of_entries')
        original=copy.deepcopy(p);field=generate_showdown_field(p,80,seed=11)
        self.assertEqual(len(field),80);self.assertEqual(p,original)
        self.assertLessEqual(field.ownership_fit['after_mae_pp'],field.ownership_fit['before_mae_pp'])
        for lu in field:validate_showdown_lineup(lu,p,50000)
        counts=exposures(field,True)
        self.assertAlmostEqual(sum(v for (s,k),v in counts.items() if s=='Captain'),100)
        self.assertAlmostEqual(sum(v for (s,k),v in counts.items() if s=='FLEX'),500)

    def test_export_records_correct_showdown_slot_values(self):
        import json,sqlite3
        from learning_db import record_export
        from optimizers import ShowdownLineup
        p=[dict(Name=str(i),Team='A' if i<3 else 'B',FlexID=str(i),CptID='c'+str(i),Position='WR',FlexSalary=5000,CptSalary=7500,FlexProjection=10,CptProjection=15,ProjOwnPct=40,ProjFlexOwnPct=30,ProjCptOwnPct=10,OwnershipUnits='percent_of_entries') for i in range(6)]
        lu=ShowdownLineup(p[0],p[1:])
        with tempfile.TemporaryDirectory() as folder:
            db=folder+'/history.sqlite'
            record_export(kind='showdown',sport='NFL',lineups=[lu],rows=[['c0','1','2','3','4','5']],salary_cap=50000,export_path='test.csv',validation={},db_path=db)
            from contextlib import closing
            with closing(sqlite3.connect(db)) as c:rows=c.execute('select slot,ownership,context_json from lineup_players').fetchall()
            for slot,own,raw in rows:
                self.assertEqual(own,10 if slot=='CPT' else 30)
                self.assertEqual(json.loads(raw)['OwnershipSlot'],'Captain' if slot=='CPT' else 'FLEX')
