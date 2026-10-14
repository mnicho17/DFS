import copy
import json
import unittest
from defense_forecast_review import summarize_defense_forecasts, format_defense_forecasts
from projection_coverage import summarize_projection_coverage, format_projection_coverage
from build_diagnostics import create_build_diagnostic, format_build_report


def defense(**kwargs):
    player = dict(Name='Seahawks', Team='SEA', Position='DST', FlexID='11',
                  ProjectionSource='Historical average estimate', HistoricalPPG=8.8,
                  BaseProjection=8.8, FlexProjection=8.8,
                  GameInfo='SEA@DEN 10/15/2026 08:15PM ET')
    player.update(kwargs)
    return player


class DefenseForecastReviewTests(unittest.TestCase):
    def test_historical_forecast_notice_and_missing_context(self):
        result = summarize_defense_forecasts([defense()])
        self.assertEqual(result['review_players'], 1)
        text = '\n'.join(format_defense_forecasts(result))
        self.assertIn('not matchup-calibrated', text)
        self.assertIn('effective FLEX 8.80', text)
        self.assertIn('game total not recorded; spread not recorded', text)

    def test_supplied_and_manual_zero_forecasts_are_not_automatic_reviews(self):
        for source in ('Manual override', 'Imported forecast'):
            result = summarize_defense_forecasts([defense(ProjectionSource=source,
                BaseProjection=0, FlexProjection=0, NFLVegasGameTotal=44, NFLVegasSpread=0)])
            self.assertEqual(result['review_players'], 0)
            text = '\n'.join(format_defense_forecasts(result))
            self.assertIn('base 0.00', text)
            self.assertIn('spread +0.00', text)
            self.assertNotIn('not matchup-calibrated', text)

    def test_signed_spreads_invalid_values_and_unknown_provenance(self):
        p = defense(NFLVegasGameTotal=0, NFLVegasSpread=-3.5, BaseProjection=float('nan'),
                    FlexProjection=True, HistoricalPPG=float('inf'))
        p.pop('ProjectionSource')
        result = summarize_defense_forecasts([p])
        self.assertEqual(result['unknown_players'], 1)
        self.assertIsNone(result['rows'][0]['game_total'])
        self.assertIsNone(result['rows'][0]['effective_forecast'])
        self.assertIn('spread -3.50', '\n'.join(format_defense_forecasts(result)))
        json.dumps(result, allow_nan=False)

    def test_read_only_deterministic_duplicates_preserve_differing_sources(self):
        a = defense(); b = defense(ProjectionSource='Manual override', FlexProjection=5)
        players = [a, b, copy.deepcopy(a)]; original = copy.deepcopy(players)
        first = summarize_defense_forecasts(players)
        self.assertEqual(first, summarize_defense_forecasts(list(reversed(players))))
        self.assertEqual(first['checked'], 1)
        self.assertEqual(len(first['rows']), 2)
        self.assertEqual(players, original)

    def test_classic_and_showdown_saved_reports_surface_separate_review(self):
        players = [defense()]; original = copy.deepcopy(players)
        coverage = summarize_projection_coverage(players)
        self.assertEqual(coverage['forecast_checks']['checked'], 0)
        self.assertEqual(coverage['defense_forecasts']['checked'], 1)
        for kind in ('classic', 'showdown'):
            record = create_build_diagnostic(context=dict(sport='NFL', kind=kind), timing_report={},
                sim_report=dict(projection_coverage=coverage))
            restored = json.loads(json.dumps(record))
            self.assertIn('Defense forecast review', format_build_report(restored))
            self.assertTrue(any('defenses use historical-average estimates' in w for w in restored['portfolio']['warnings']))
        self.assertEqual(players, original)

    def test_bounded_output_and_older_reports_without_defense_details(self):
        result = summarize_defense_forecasts([defense(Name=str(i), FlexID=str(i)) for i in range(20)])
        self.assertEqual(len(result['rows']), 20)
        self.assertIn('14 additional defense records', '\n'.join(format_defense_forecasts(result)))
        coverage = summarize_projection_coverage([defense(Position='WR')])
        coverage.pop('defense_forecasts')
        self.assertNotIn('Defense forecast review', '\n'.join(format_projection_coverage(coverage)))
        self.assertEqual(summarize_defense_forecasts([defense(Position='K')])['checked'], 0)
