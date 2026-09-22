import copy
import random
import unittest
from nfl_workload import TEAM_BUDGET, prepare_workloads, sample_workloads
from projection_sources import initialize_projection
from nfl_auto_data import _reapply_context_adjustments
from nfl_simulation import _scenario_outcomes
from build_snapshots import create_snapshot, validate_snapshot


def roster():
    result = []
    for team, opponent in [('SEA', 'NE'), ('NE', 'SEA')]:
        for pos, depths in [('QB', 2), ('RB', 3), ('WR', 4), ('TE', 3)]:
            for depth in range(1, depths + 1):
                p = dict(Name=f'{team} {pos}{depth}', Team=team, Opponent=opponent,
                         GameKey='NE@SEA', Position=pos, NFLDepthOrder=depth,
                         FlexSalary=6000, NFLAvailability='ACTIVE', NFLUsageGames=0)
                initialize_projection(p, historical=0)
                result.append(p)
    return result


class WorkloadTests(unittest.TestCase):
    def test_team_budgets_and_sampled_shares_are_conserved(self):
        players = roster()
        _reapply_context_adjustments(players)
        for team in ('SEA', 'NE'):
            for metric, budget in TEAM_BUDGET.items():
                total = sum(p['NFLWorkload'][metric] for p in players if p['Team'] == team)
                self.assertLessEqual(total, budget)
                self.assertGreater(total, budget * .85)
        rng = random.Random(10)
        for _ in range(30):
            samples = sample_workloads(rng, players)
            for team in ('SEA', 'NE'):
                for metric, budget in TEAM_BUDGET.items():
                    self.assertLessEqual(sum(samples[id(p)][metric] for p in players if p['Team'] == team), budget + 1e-9)

    def test_out_starter_promotes_backup_and_return_restores(self):
        players = roster()
        _reapply_context_adjustments(players)
        starter, backup = [p for p in players if p['Team'] == 'SEA' and p['Position'] == 'RB'][:2]
        baseline = backup['FlexProjection']
        starter['NFLAvailability'] = 'OUT'
        _reapply_context_adjustments(players)
        self.assertGreater(backup['FlexProjection'], baseline)
        self.assertNotIn('WorkloadProjection', starter)
        starter['NFLAvailability'] = 'ACTIVE'
        _reapply_context_adjustments(players)
        self.assertAlmostEqual(backup['FlexProjection'], baseline)

    def test_recent_usage_blends_without_zeroing_rookies_or_exceeding_budget(self):
        players = roster()
        rb = next(p for p in players if p['Name'] == 'SEA RB2')
        _reapply_context_adjustments(players)
        before = rb['NFLWorkload']['carries']
        rb.update(NFLUsageGames=4, NFLRecentCarries=18, NFLRecentTargets=6)
        _reapply_context_adjustments(players)
        self.assertGreater(rb['NFLWorkload']['carries'], before)
        rookie = next(p for p in players if p['Name'] == 'SEA RB1')
        self.assertGreater(rookie['FlexProjection'], 5)
        first = copy.deepcopy(players)
        _reapply_context_adjustments(players)
        self.assertEqual(players, first)

    def test_unknown_role_and_small_pool_do_not_receive_entire_offense(self):
        p = roster()[2]
        _reapply_context_adjustments([p])
        self.assertLess(p['NFLWorkload']['carries'], TEAM_BUDGET['carries'] * .7)
        p['NFLDepthOrder'] = 0
        _reapply_context_adjustments([p])
        self.assertNotIn('WorkloadProjection', p)
        self.assertEqual(p['FlexProjection'], 0)

    def test_overrides_and_snapshot_assumptions_both_contests(self):
        players = roster()
        players[2]['ManualProjection'] = 20
        players[3]['ImportedProjection'] = 11
        _reapply_context_adjustments(players)
        self.assertEqual(players[2]['FlexProjection'], 20)
        self.assertEqual(players[3]['FlexProjection'], 11)
        self.assertEqual(players[2]['CptProjection'], 30)
        for mode in ('classic', 'showdown'):
            snapshot = create_snapshot(players, {'sport': 'NFL', 'contest_kind': mode}, {})
            self.assertEqual(validate_snapshot(snapshot)['inputs']['players'], players)
        before = copy.deepcopy(players)
        a = _scenario_outcomes(random.Random(12), players)
        self.assertEqual(a, _scenario_outcomes(random.Random(12), players))
        self.assertNotEqual(a, _scenario_outcomes(random.Random(13), players))
        self.assertEqual(players, before)
