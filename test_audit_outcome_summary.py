"""Regression checks for diagnostic zero/positive/negative audit categories."""
from test_environment import install
install()
import copy
import unittest
from scripts.audit_outcome_summary import outcome_sign_subgroups


def row(actual, crps):
    return dict(actual=actual, profiles={'baseline': dict(crps=crps)})


class OutcomeSignSummaryTests(unittest.TestCase):
    def test_negative_is_not_positive_and_input_is_unchanged(self):
        games=[dict(rows=[row(-1,9),row(0,3),row(2,6),row(-0.0,5)])]
        before=copy.deepcopy(games)
        result=outcome_sign_subgroups(games,'baseline')
        self.assertEqual(result['actual_positive'],dict(games=1,observations=1,crps=6))
        self.assertEqual(result['actual_negative'],dict(games=1,observations=1,crps=9))
        self.assertEqual(result['actual_zero'],dict(games=1,observations=2,crps=4))
        self.assertEqual(games,before)

    def test_games_are_equally_weighted_and_empty_games_excluded(self):
        games=[dict(rows=[row(1,2)]),dict(rows=[row(1,8),row(2,10),row(3,12)]),dict(rows=[])]
        result=outcome_sign_subgroups(games,'baseline')
        self.assertEqual(result['actual_positive'],dict(games=2,observations=4,crps=6))
        self.assertEqual(result['actual_negative'],dict(games=0,observations=0,crps=None))

    def test_absent_category_does_not_add_a_zero_game(self):
        result=outcome_sign_subgroups([dict(rows=[row(0,4)]),dict(rows=[row(2,8)])],'baseline')
        self.assertEqual(result['actual_zero'],dict(games=1,observations=1,crps=4))
        self.assertEqual(result['actual_positive']['crps'],8)

    def test_empty_input_is_unavailable(self):
        self.assertTrue(all(v==dict(games=0,observations=0,crps=None)
                            for v in outcome_sign_subgroups([],'baseline').values()))

    def test_unknown_or_nonfinite_actual_is_rejected(self):
        for actual in (None,True,'0',float('nan'),float('inf'),-float('inf')):
            with self.subTest(actual=actual),self.assertRaises(ValueError):
                outcome_sign_subgroups([dict(rows=[row(actual,1)])],'baseline')
        with self.assertRaises(KeyError):
            outcome_sign_subgroups([dict(rows=[dict(profiles={'baseline':dict(crps=1)})])],'baseline')

    def test_invalid_metric_or_missing_profile_is_rejected(self):
        for score in (None,True,'1',float('nan'),float('inf')):
            with self.subTest(score=score),self.assertRaises(ValueError):
                outcome_sign_subgroups([dict(rows=[row(1,score)])],'baseline')
        with self.assertRaises(KeyError):
            outcome_sign_subgroups([dict(rows=[row(1,2)])],'missing')


if __name__=='__main__':unittest.main()
