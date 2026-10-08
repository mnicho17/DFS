import random
import unittest
import copy
import statistics
from nfl_specialists import sample_game, defense_score, kicker_score, points_allowed_score
from nfl_simulation import _scenario_outcomes
from test_showdown_performance import _showdown_players


class SpecialistEventTests(unittest.TestCase):
    def specialist_pool(self):
        pool = _showdown_players()
        for p in pool:
            p['GameKey'] = 'ARI@CAR'
            p['Opponent'] = 'CAR' if p['Team'] == 'ARI' else 'ARI'
        for team, projection in [('ARI', 1.5), ('CAR', 5.3)]:
            for pos, mean in [('DST', projection), ('K', 9.0)]:
                pool.append(dict(Name=team+pos, Team=team, Position=pos, FlexID=team+pos,
                    GameKey='ARI@CAR', Opponent='CAR' if team=='ARI' else 'ARI',
                    FlexProjection=mean, FlexSalary=3000))
        return pool

    def test_projection_defense_is_retained_and_kicker_events_still_run(self):
        from nfl_simulation import player_key, _position
        pool = self.specialist_pool(); original = copy.deepcopy(pool)
        a, b = random.Random(77), random.Random(77)
        kicker_changed = False
        for _ in range(100):
            legacy = _scenario_outcomes(a, pool, specialist_model='legacy')
            current = _scenario_outcomes(b, pool)
            for p in pool:
                key = player_key(p)
                if _position(p) != 'K':
                    self.assertEqual(legacy[key], current[key])
                else:
                    self.assertIsInstance(current[key], float)
                    self.assertEqual(current[key], int(current[key]))
                    kicker_changed |= legacy[key] != current[key]
            self.assertEqual(a.getstate(), b.getstate())
        self.assertTrue(kicker_changed); self.assertEqual(pool, original)

    def test_low_defense_forecast_does_not_receive_event_model_floor(self):
        from nfl_simulation import player_key, _position
        pool = self.specialist_pool(); rng = random.Random(73129)
        values = {player_key(p):[] for p in pool if _position(p)=='DST'}
        for _ in range(4000):
            outcomes = _scenario_outcomes(rng, pool)
            for key in values: values[key].append(outcomes[key])
        for p in pool:
            if _position(p) != 'DST': continue
            scores = values[player_key(p)]
            self.assertAlmostEqual(statistics.mean(scores), p['FlexProjection'], delta=.3)
            self.assertGreaterEqual(min(scores), -4)
            self.assertGreater(max(scores), p['FlexProjection'] + 3)

    def test_points_allowed_boundaries(self):
        for points, expected in [(0,10),(1,7),(6,7),(7,4),(13,4),(14,1),(20,1),(21,0),(27,0),(28,-1),(34,-1),(35,-4),(70,-4)]:
            self.assertEqual(points_allowed_score(points), expected)

    def test_scoring_and_defensive_touchdown_exclusion(self):
        a = dict(sacks=3,takeaways=2,defensive_touchdowns=1)
        b = dict(offensive_touchdowns=1,extra_points=2,field_goals=[32,45,53],defensive_touchdowns=1)
        self.assertEqual(kicker_score(b),14)
        # 17 allowed, not 23: defensive TD excluded, PAT included.
        self.assertEqual(defense_score(a,b),14)

    def test_shared_event_budget_and_integer_scores(self):
        rng=random.Random(81)
        for _ in range(1000):
            events=sample_game(rng,['A','B'],{'A':1,'B':-1},0,{'A':12,'B':8},{'A':11,'B':7})
            self.assertEqual(events['A']['drives'],events['B']['drives'])
            for team,other in [('A','B'),('B','A')]:
                e,o=events[team],events[other]
                self.assertLessEqual(e['offensive_touchdowns']+len(e['field_goals'])+o['takeaways'],e['drives'])
                self.assertLessEqual(e['defensive_touchdowns'],e['takeaways'])
                self.assertLessEqual(e['extra_points'],e['offensive_touchdowns']+e['defensive_touchdowns'])
                self.assertGreaterEqual(defense_score(e,o),-4)
                self.assertIsInstance(kicker_score(e),int)

    def test_paired_offense_and_random_stream_unchanged(self):
        pool=_showdown_players()
        a,b=random.Random(77),random.Random(77)
        for _ in range(20):
            old=_scenario_outcomes(a,pool,specialist_model='legacy')
            new=_scenario_outcomes(b,pool)
            from nfl_simulation import player_key, _position
            for p in pool:
                if _position(p) not in {'DST','K'}:
                    self.assertEqual(old[player_key(p)],new[player_key(p)])
            self.assertEqual(a.getstate(),b.getstate())

    def test_more_opponent_scoring_cannot_help_points_allowed_component(self):
        defense=dict(sacks=2,takeaways=1,defensive_touchdowns=0)
        scores=[defense_score(defense,dict(offensive_touchdowns=t,extra_points=t,field_goals=[])) for t in range(8)]
        self.assertEqual(scores,sorted(scores,reverse=True))
