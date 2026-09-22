import bisect
import copy
import unittest
from unittest.mock import patch

from nfl_simulation import _percentile_rank, _percentile_rank_sorted, simulate_nfl_contest
from optimizers import MultiSportClassicOptimizer
from test_nfl_logic import _fixture_players


def original_rank(values, value):
    if len(values)<=1:return .5
    ordered=sorted(values)
    return ((bisect.bisect_left(ordered,value)+bisect.bisect_right(ordered,value)-1)/2)/(len(ordered)-1)


class ClassicRatingScaleTests(unittest.TestCase):
    def test_sorted_ranking_preserves_ties_and_boundaries(self):
        for values in ([],[3],[3,3,3],[1,2,2,3,4],[float(i%37) for i in range(100000)]):
            ordered=sorted(values)
            for value in (-1,1,2,3,36,100):
                self.assertEqual(_percentile_rank_sorted(ordered,value),original_rank(values,value))
                self.assertEqual(_percentile_rank(values,value),original_rank(values,value))

    def test_simulation_ratings_match_original_algorithm_and_report_finalization(self):
        players=_fixture_players();rows=MultiSportClassicOptimizer(players).build_lineups(4)
        # Repeated candidates deliberately exercise tied ratings in a larger bank.
        bank=[copy.deepcopy(row) for _ in range(80) for row in rows]
        progress=[]
        kwargs=dict(scenarios=3,field_lineup_count=20,seed=12)
        fast=simulate_nfl_contest(bank,players,progress_callback=lambda a,b,c:progress.append((a,b,c)),**kwargs)
        with patch('nfl_simulation._percentile_rank_sorted',side_effect=original_rank):
            legacy=simulate_nfl_contest(bank,players,**kwargs)
        self.assertEqual([r.sim_metrics for r in fast['lineups']],[r.sim_metrics for r in legacy['lineups']])
        self.assertTrue(any('Finalizing candidate ratings' in text for _,_,text in progress))


if __name__=='__main__':unittest.main()
