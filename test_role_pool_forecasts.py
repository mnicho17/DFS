import unittest
from nfl_simulation import build_nfl_role_pool
from nfl_eligibility import eligible_players


def player(name, depth, source='Automatic workload estimate', points=8):
    return dict(Name=name, FlexID=name, Team='SEA', Position='WR', NFLDepthOrder=depth,
                FlexSalary=4000, FlexProjection=points, ProjectionSource=source)


class RolePoolForecastTests(unittest.TestCase):
    def test_verified_rotation_precedes_unknown_depth(self):
        players = [player('Unknown', 0, 'Historical average estimate', 20)]
        players += [player('WR'+str(i), i) for i in range(1, 5)]
        self.assertEqual({p['Name'] for p in build_nfl_role_pool(players)}, {'WR1','WR2','WR3'})

    def test_missing_is_not_zero_or_rookie_estimate(self):
        players = [player('Missing',0,'Missing forecast',0), player('Rookie',2),
                   player('Manual zero',0,'Manual override',0)]
        self.assertEqual({p['Name'] for p in eligible_players(players)}, {'Rookie','Manual zero'})

    def test_missing_lock_requires_forecast(self):
        p=player('Missing',0,'Missing forecast',0)
        p['LockCpt']=True
        with self.assertRaisesRegex(ValueError, 'needs a forecast'):
            eligible_players([p])

    def test_partial_depth_and_legacy_inputs_remain_supported(self):
        players=[player('WR1',1),player('Unknown',0),player('Deep',8)]
        players[1].pop('ProjectionSource')
        self.assertEqual(len(build_nfl_role_pool(players)),3)
