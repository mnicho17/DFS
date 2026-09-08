import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from nfl_auto_data import _fetch_nflverse_season_rows, fetch_recent_usage_with_fallback
from nfl_pipeline import prepare_nfl_slate
from nfl_workload import sample_workloads, forecast
from nfl_auto_data import _reapply_context_adjustments
from test_nfl_workload import roster
from test_nfl_logic import _fixture_players
from test_showdown_performance import _showdown_players
import random


class PipelineTests(unittest.TestCase):
    def test_weekly_url_schema_and_prior_season_fallback(self):
        missing = Mock()
        missing.raise_for_status.side_effect = RuntimeError('404')
        good = Mock(content=b'season,week,season_type,player_name\n2025,18,REG,Example\n2025,19,POST,Other\n')
        with patch('nfl_auto_data.requests.get', side_effect=[missing, good]) as get:
            rows, season = fetch_recent_usage_with_fallback(2026)
        self.assertEqual(season, 2025)
        self.assertEqual(len(rows), 1)
        self.assertIn('stats_player/stats_player_week_2025.csv', get.call_args.args[0])
        bad = Mock(content=b'<html>not statistics</html>')
        with patch('nfl_auto_data.requests.get', return_value=bad):
            self.assertEqual(_fetch_nflverse_season_rows(2025), [])

    def test_no_usage_keeps_veteran_differences_and_rookie_role(self):
        players = roster()
        qbs = [p for p in players if p['Position'] == 'QB' and p['NFLDepthOrder'] == 1]
        qbs[0]['HistoricalPPG'], qbs[1]['HistoricalPPG'] = 22, 15
        _reapply_context_adjustments(players)
        self.assertGreater(qbs[0]['FlexProjection'], qbs[1]['FlexProjection'])
        self.assertEqual(qbs[0]['NFLWorkload']['workload_weight'], .25)
        rookie = next(p for p in players if p['Name'] == 'SEA RB1')
        self.assertGreater(rookie['FlexProjection'], 5)
        self.assertEqual(rookie['NFLWorkload']['workload_weight'], 1)

    def test_v1_snapshot_sampling_remains_supported(self):
        players = roster()
        _reapply_context_adjustments(players)
        for p in players:
            w = p['NFLWorkload']
            w['version'] = 'workload-v1'
            w.pop('workload_weight', None)
            w.pop('history_anchor', None)
        sampled = sample_workloads(random.Random(7), players)
        self.assertTrue(sampled)
        self.assertGreater(forecast(next(iter(sampled.values()))), 0)

    def test_server_prepares_both_formats_with_ownership_and_provenance(self):
        for mode, fixture in [('classic', _fixture_players), ('showdown', _showdown_players)]:
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'slate.csv'
                players = fixture()
                with path.open('w', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow(['Name', 'Position', 'Team', 'Salary', 'Projection', 'ID', 'Game Info'])
                    for i, p in enumerate(players):
                        writer.writerow([p['Name'],p['Position'],p['Team'],p['FlexSalary'],p['FlexProjection'],i+1,p.get('GameKey', '')])
                prepared = prepare_nfl_slate(path, mode=mode, ownership_sims=20,
                    context=dict(fetch_external=False, usage_rows=[], weather_by_game={}))
                self.assertEqual(prepared['summary']['usage_state'], 'unavailable')
                self.assertAlmostEqual(sum(p['ProjOwnPct'] for p in prepared['players']), 900 if mode == 'classic' else 600)
                self.assertTrue(prepared['preparation']['source_hashes'])
                self.assertTrue(all('ProjOwnPct' in p for p in prepared['role_pool']))
