import copy
import tempfile
import unittest
from pathlib import Path

from data_io import read_players_csv
from nfl_auto_data import _reapply_context_adjustments
from projection_sources import initialize_projection, prepare_nfl_projections


class ProjectionSourceTests(unittest.TestCase):
    def load(self, text):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'players.csv'
            path.write_text(text, encoding='utf-8')
            return read_players_csv(str(path))

    def test_csv_forecast_over_history_in_either_order_and_explicit_zero(self):
        for header, row in [('AvgPointsPerGame,Projection', '0,12'), ('Projection,AvgPointsPerGame', '12,0')]:
            p = self.load(f'Name,Position,Team,Salary,{header}\nRookie,RB,SEA,7200,{row}\n')[0]
            self.assertEqual(p['HistoricalPPG'], 0)
            p['NFLRoleScore'] = .35
            _reapply_context_adjustments([p])
            self.assertEqual(p['FlexProjection'], 12)
            self.assertEqual(p['CptProjection'], 18)
            self.assertEqual(p['ProjectionSource'], 'Imported forecast')
        p = self.load('Name,Position,Salary,AvgPointsPerGame,Projection\nA,RB,4000,10,0\n')[0]
        self.assertEqual(p['FlexProjection'], 0)
        self.assertEqual(p['ProjectionSource'], 'Imported forecast')

    def test_rookie_recomputes_without_feedback_and_unknown_role_not_promoted(self):
        players = self.load('Name,Position,Team,Salary,AvgPointsPerGame\nRookie,RB,SEA,7200,0\nPeer1,RB,SEA,4200,6\nPeer2,RB,NE,6000,12\n')
        for p in players:
            p['NFLDepthOrder'] = 1
            p['NFLRoleScore'] = .35
        _reapply_context_adjustments(players)
        rookie = players[0]
        self.assertAlmostEqual(rookie['FlexProjection'], 12.05)
        self.assertEqual(rookie['ProjectionSource'], 'Comparable-player estimate')
        before = copy.deepcopy(players)
        _reapply_context_adjustments(players)
        self.assertEqual(before, players)
        rookie['NFLDepthOrder'] = 0
        _reapply_context_adjustments(players)
        self.assertEqual(rookie['FlexProjection'], 0)
        self.assertEqual(rookie['ProjectionSource'], 'Missing forecast')

    def test_override_survives_refresh_and_clear_restores_source(self):
        p = {'Position': 'RB', 'FlexSalary': 7000, 'NFLDepthOrder': 1, 'NFLRoleScore': .35}
        initialize_projection(p, historical=0, imported=11)
        p['ManualProjection'] = 15
        for _ in range(3):
            _reapply_context_adjustments([p])
        self.assertEqual(p['FlexProjection'], 15)
        self.assertEqual(p['CptProjection'], 22.5)
        p['ManualProjection'] = None
        _reapply_context_adjustments([p])
        self.assertEqual(p['FlexProjection'], 11)

    def test_missing_and_nonfinite_forecasts_are_not_zero_forecasts(self):
        for value in ('', 'nan', 'inf', '-1'):
            p = self.load(f'Name,Salary,AvgPointsPerGame,Projection\nA,4000,8,{value}\n')[0]
            self.assertEqual(p['ProjectionSource'], 'Historical average estimate')
            self.assertEqual(p['FlexProjection'], 8)

    def test_legacy_snapshots_unchanged_by_source_preparation(self):
        p = {'Name': 'Old', 'BaseProjection': .35, 'FlexProjection': .35, 'CptProjection': .525}
        before = copy.deepcopy(p)
        prepare_nfl_projections([p])
        self.assertEqual(p, before)

    def test_showdown_uses_flex_forecast_not_captain_row(self):
        players = self.load('Name,Position,Roster Position,Salary,AvgPointsPerGame,Projection\nA,RB,CPT,9000,0,99\nA,RB,FLEX,6000,0,10\n')
        self.assertEqual(len(players), 1)
        self.assertEqual(players[0]['CptProjection'], 15)


if __name__ == '__main__':
    unittest.main()
