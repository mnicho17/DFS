import copy
import json
import unittest
from forecast_checks import check_forecasts, format_checks
from projection_coverage import summarize_projection_coverage
from build_diagnostics import create_build_diagnostic, format_build_report


def player(**kw):
    p=dict(Name='Receiver',Team='SEA',Position='WR',ProjectionSource='Automatic workload estimate',
        BaseProjection=10,HistoricalPPG=10,NFLUsageGames=4,NFLUsageSeason=2025,
        NFLRecentAttempts=0,NFLRecentCarries=0,NFLRecentTargets=8,
        NFLWorkload=dict(attempts=0,carries=0,targets=8,budgets=dict(attempts=0,carries=1,targets=20)))
    p.update(kw);return p


class ForecastCheckTests(unittest.TestCase):
    def test_thresholds_observed_zero_and_short_sample(self):
        p=player(NFLRecentTargets=0);r=check_forecasts([p])
        self.assertEqual(r['counts'],{'opportunity_gap':1})
        self.assertIn('season 2025',r['findings'][0]['message'])
        p['NFLUsageGames']=1
        self.assertNotIn('opportunity_gap',check_forecasts([p])['counts'])
        p['NFLRecentTargets']=None;p['NFLUsageGames']=4
        self.assertNotIn('opportunity_gap',check_forecasts([p])['counts'])
        self.assertEqual(check_forecasts([player(NFLRecentTargets=6)])['review_players'],0)

    def test_manual_specialist_and_missing_history_do_not_create_false_errors(self):
        for source in ('Manual override','Imported forecast'):
            self.assertEqual(check_forecasts([player(ProjectionSource=source,BaseProjection=35)])['findings'],[])
        self.assertEqual(check_forecasts([player(Position='K')])['checked'],0)
        r=check_forecasts([player(NFLUsageGames=0)])
        self.assertEqual(r['review_players'],0);self.assertEqual(r['information_players'],1)
        self.assertIn('missing',r['findings'][0]['message'])

    def test_gap_budget_invalid_and_missing_breakdown(self):
        p=player(BaseProjection=20)
        self.assertIn('history_gap',check_forecasts([p])['counts'])
        p['NFLWorkload']['targets']=21
        self.assertIn('budget_exceeded',check_forecasts([p])['counts'])
        p['NFLWorkload']['targets']=float('nan')
        self.assertIn('invalid_opportunity',check_forecasts([p])['counts'])
        p.pop('NFLWorkload')
        self.assertIn('missing_workload',check_forecasts([p])['counts'])

    def test_no_mutation_deduplication_and_both_report_formats(self):
        players=[player(BaseProjection=20)];original=copy.deepcopy(players)
        coverage=summarize_projection_coverage(players+players)
        self.assertEqual(coverage['forecast_checks']['checked'],1)
        self.assertEqual(players,original)
        for kind in ('classic','showdown'):
            record=create_build_diagnostic(context=dict(sport='NFL',kind=kind),timing_report={},
                sim_report=dict(projection_coverage=coverage))
            restored=json.loads(json.dumps(record));text=format_build_report(restored)
            self.assertIn('Automatic forecast checks',text)
            self.assertIn('Base 20.0 vs historical average 10.0',text)
            self.assertTrue(any('forecast review signals' in w for w in restored['portfolio']['warnings']))

    def test_bounded_report_with_full_saved_details(self):
        r=check_forecasts([player(Name=str(i),BaseProjection=20) for i in range(20)])
        text='\n'.join(format_checks(r))
        self.assertEqual(len(r['findings']),20)
        self.assertIn('14 additional findings',text)
