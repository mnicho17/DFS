from test_environment import install
install()
import copy
import unittest
from unittest.mock import patch
from historical_field import distributions, sample_history_field
from historical_ownership import sample_calibrated_history_field, CONSTRUCTION_TOLERANCE
from test_showdown_performance import _showdown_players
from showdown_simulation import showdown_signature
from optimizers import _pkey, _salary, _cpt_salary
from showdown_field import OpponentField


def pool():
    players=_showdown_players()
    n=len(players)
    for i,p in enumerate(players):
        p.update(OwnershipUnits='percent_of_entries',ProjCptOwnPct=100/n,ProjFlexOwnPct=500/n)
    return players


class HistoricalOwnershipTests(unittest.TestCase):
    def test_bounded_calibration_deterministic_legal_and_nonmutating(self):
        players=pool();before=copy.deepcopy(players)
        targets=distributions(sample_history_field(players,300,{
            'Team split':{'3–3':1},'Captain position':{'QB':1},'Quarterbacks':{'1':1},
            'Kicker/defense slots':{'0':1},'Salary left':{'over $1,200':1}},seed=2))
        original=copy.deepcopy(targets)
        a=sample_calibrated_history_field(players,300,targets,seed=17)
        b=sample_calibrated_history_field(players,300,targets,seed=17)
        self.assertEqual([showdown_signature(l) for l in a],[showdown_signature(l) for l in b])
        self.assertEqual(a.ownership_fit,b.ownership_fit)
        self.assertEqual(players,before);self.assertEqual(targets,original)
        fit=a.ownership_fit
        self.assertEqual(fit['passes'],2)
        self.assertLessEqual(fit['after_mae_pp'],fit['before_mae_pp'])
        self.assertLessEqual(fit['after_construction_distance'],fit['before_construction_distance']+CONSTRUCTION_TOLERANCE)
        for lu in a:
            self.assertEqual(len({_pkey(p) for p in [lu['Captain']]+lu['Flex']}),6)
            self.assertEqual(len({p['Team'] for p in [lu['Captain']]+lu['Flex']}),2)
            self.assertLessEqual(_cpt_salary(lu['Captain'])+sum(_salary(p) for p in lu['Flex']),50000)

    def test_invalid_ownership_never_renormalized(self):
        for change in ({'OwnershipUnits':'legacy'},{'ProjCptOwnPct':None},{'ProjFlexOwnPct':float('nan')},
                       {'ProjCptOwnPct':True},{'ProjFlexOwnPct':101},{'ProjCptOwnPct':0}):
            players=pool();players[0].update(change)
            with self.assertRaises(ValueError):sample_calibrated_history_field(players,50,{})

    def test_guard_rejects_ownership_gain_with_construction_loss(self):
        players=pool()
        targets={'Team split':{'3–3':1},'Captain position':{'QB':1},'Quarterbacks':{'1':1},
                 'Kicker/defense slots':{'0':1},'Salary left':{'over $1,200':1}}
        field=sample_history_field(players,50,targets,seed=17)
        with patch('historical_ownership.sample_history_field',return_value=field) as sampler, \
             patch('historical_ownership._error',side_effect=[5,1,1]), \
             patch('historical_ownership._construction_error',side_effect=[.1,.13,.13,.1]):
            result=sample_calibrated_history_field(players,50,targets,seed=17)
        self.assertEqual(result.ownership_fit['after_mae_pp'],5)
        self.assertTrue(all(not r['accepted'] for r in result.ownership_fit['trials']))
        self.assertEqual(sampler.call_count,3)

    def test_cancel_and_incomplete_fields_do_not_calibrate(self):
        players=pool()
        with patch('historical_ownership.sample_history_field',return_value=OpponentField()) as sampler:
            result=sample_calibrated_history_field(players,50,{},cancelled=lambda:True)
        self.assertEqual(result,[]);self.assertEqual(sampler.call_count,1)
        self.assertEqual(result.ownership_fit['status'],'incomplete')

    def test_incomplete_adjustment_preserves_complete_baseline_and_discloses_trial(self):
        players=pool()
        targets={'Team split':{'3–3':1},'Captain position':{'QB':1},'Quarterbacks':{'1':1},
                 'Kicker/defense slots':{'0':1},'Salary left':{'over $1,200':1}}
        initial=sample_history_field(players,50,targets,seed=17)
        with patch('historical_ownership.sample_history_field',side_effect=[initial,OpponentField(initial[:10])]):
            result=sample_calibrated_history_field(players,50,targets,seed=17)
        self.assertEqual(len(result),50)
        self.assertEqual(result.ownership_fit['before_mae_pp'],result.ownership_fit['after_mae_pp'])
        self.assertEqual(result.ownership_fit['trials'],[dict(pass_number=1,returned=10,accepted=False,status='incomplete')])
