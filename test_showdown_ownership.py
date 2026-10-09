import copy
import unittest
from unittest.mock import patch
from test_showdown_performance import _showdown_players
from showdown_ownership import estimate_ownership
from showdown_field import ownership_salary_check
from main_window import OwnershipSimWorker


class ShowdownOwnershipTests(unittest.TestCase):
    def test_legal_sample_denominators_and_salary_identity(self):
        players=_showdown_players(); before=copy.deepcopy(players)
        result=estimate_ownership(players,100,seed=31)
        self.assertEqual(players,before)
        self.assertEqual(result['meta']['valid_lineups'],100)
        self.assertAlmostEqual(sum(result['cpt'].values()),100)
        self.assertAlmostEqual(sum(result['flex'].values()),500)
        self.assertAlmostEqual(sum(result['total'].values()),600)
        from optimizers import _pkey
        for p in players:
            k=_pkey(p)
            p.update(ProjCptOwnPct=result['cpt'].get(k,0),ProjFlexOwnPct=result['flex'].get(k,0),OwnershipUnits='percent_of_entries')
        counts=result['meta']['sampling']['salary_band_targets']
        check=ownership_salary_check(players,list(counts.values()),100,50000)
        self.assertAlmostEqual(check['ownership_implied_mean'],result['meta']['salary_mean'],places=2)

    def test_previous_ownership_external_inputs_and_feedback_are_not_used(self):
        players=_showdown_players();expected=estimate_ownership(players,80,seed=3)
        for p in players:
            p.update(ProjCptOwnPct=99,ProjFlexOwnPct=99,ProjOwnPct=99,OwnershipUnits='percent_of_entries',OwnershipSource='Manual',_FieldCptWeight=10,_FieldFlexWeight=.1)
        before=copy.deepcopy(players)
        with patch('ownership_strategy.fit_field',side_effect=AssertionError('feedback in estimator')):
            actual=estimate_ownership(players,80,seed=3)
        self.assertEqual(expected,actual);self.assertEqual(players,before)

    def test_worker_uses_shared_sampler_for_both_legacy_template_settings(self):
        players=_showdown_players()
        with patch('showdown_ownership.estimate_ownership',return_value={'total':{'sentinel':10}}) as estimator:
            for value in (False,True):
                worker=OwnershipSimWorker(players,mode='showdown',num_sims=80,salary_cap=50000,template_sim=value)
                self.assertEqual(worker._simulate(),{'total':{'sentinel':10}})
            self.assertEqual(estimator.call_count,2)

    def test_cancelled_and_short_samples_are_not_applied(self):
        players=_showdown_players()
        self.assertEqual(estimate_ownership(players,80,cancelled=lambda:True),{})
        with patch('showdown_ownership._sample_field',return_value=[]):
            with self.assertRaisesRegex(ValueError,'no partial estimate'):estimate_ownership(players,80)
        calls=[False,True]
        with patch('showdown_ownership._sample_field',return_value=[]):
            self.assertEqual(estimate_ownership(players,80,cancelled=lambda:calls.pop(0)),{})

    def test_only_legal_rosters_are_counted(self):
        players=_showdown_players()
        with patch('showdown_ownership._sample_field',return_value=[dict(Captain=players[0],Flex=[players[0]]*5)]):
            with self.assertRaises(ValueError):estimate_ownership(players,1)

    def test_invalid_sampling_budget_and_forecasts_fail(self):
        for n in (0,True,100001):
            with self.assertRaises(ValueError):estimate_ownership(_showdown_players(),n)
        players=_showdown_players();players[0]['FlexProjection']=float('nan')
        with self.assertRaises(ValueError):estimate_ownership(players,80)

    def test_opponent_estimate_uses_same_pool_despite_personal_locks_and_fades(self):
        players=_showdown_players();expected=estimate_ownership(players,80,seed=3)
        players[0].update(LockCpt=True,LockFlex=True,FadeCpt=True)
        players[1]['FadeFlex']=True
        before=copy.deepcopy(players)
        self.assertEqual(expected,estimate_ownership(players,80,seed=3))
        self.assertEqual(players,before)
