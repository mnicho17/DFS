import random
import unittest
from nfl_specialists import sample_game, defense_score, kicker_score, points_allowed_score
from nfl_simulation import _scenario_outcomes
from test_showdown_performance import _showdown_players


class SpecialistEventTests(unittest.TestCase):
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
