"""Exact sampling parity frozen before optimization at main b93853f."""
from test_environment import install
install()
import copy
import hashlib
import json
import random
import unittest
from unittest.mock import patch
from historical_field import sample_history_field
from optimizers import _pkey
from test_field_history_validation import priors
from test_showdown_performance import _showdown_players


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def ordered_rosters(rows):
    return [(_pkey(row['Captain']),tuple(_pkey(p) for p in row['Flex'])) for row in rows]


def capture(case):
    players=_showdown_players();target=priors()
    options=dict(seed=17);count=150;cancel_on=None
    if case=='specialists':
        for i in (6,24):players[i]['Position']='TE'
        for i in (14,32):players[i]['Position']='K'
        for i in (16,34):players[i]['Position']='DST'
        target['Captain position'].update(K=.15,DST=.05)
        target['Quarterbacks']={'0':0,'1':.9,'2':.1}
        options.update(seed=29,salary_cap=48000);count=80
    elif case=='fractional':
        for i,p in enumerate(players):
            p['FlexSalary']+=(i%3)*.125
            p['CptSalary']=p['FlexSalary']*1.5+.0625
        options.update(seed=413,salary_cap=49999.875);count=80
    elif case=='single':count=1
    elif case=='weighted':
        options.update(seed=901,draw_weights={(slot,_pkey(p)):.4+(i%7)*.3
            for slot in ('Captain','FLEX') for i,p in enumerate(players)})
        count=50
    elif case=='unavailable':
        target['Captain position']={'K':1};options['seed']=4;count=50
    elif case=='cancel_proposals':cancel_on=2;count=50
    elif case=='cancel_fitting':cancel_on=17;count=50
    elif case=='empty':count=0
    elif case!='default':raise ValueError(case)
    original=copy.deepcopy((players,target,options))
    calls=[];instances=[];record={};real_random=random.Random
    class ObservedRandom(real_random):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs);instances.append(self);self.draws=0
        def choices(self,population,weights=None,*,cum_weights=None,k=1):
            self.draws+=1
            if population and isinstance(population[0],dict):
                record.update(proposals=digest(ordered_rosters(population)),
                    weights=digest([float(w).hex() for w in weights]),
                    before_draw=digest(self.getstate()))
            return super().choices(population,weights=weights,cum_weights=cum_weights,k=k)
    def cancelled():
        calls.append(1)
        return cancel_on is not None and len(calls)>=cancel_on
    with patch('historical_field.random.Random',ObservedRandom):
        field=sample_history_field(players,count,target,cancelled=cancelled,**options)
    assert (players,target,options)==original
    record.update(rosters=digest(ordered_rosters(field)),diagnostic=digest(field.diagnostic),
        returned=len(field),cancel_checks=len(calls),draws=instances[0].draws if instances else 0,
        rng=digest(instances[0].getstate()) if instances else None)
    return record


BASELINE = {'default': {'proposals': 'cf8778221cdb9da83273367fd790d6a95fc35029b79f5d945de6cc753830c540',
             'weights': 'c450ef60a9efb8a0ed51b2847a15c419100b47e35b7c19e3d507a3effca3a183',
             'before_draw': 'a258dfa1df6898abbb9dfe8b8103847f5edad764b90a953cdc1f7465f0f5c02e',
             'rosters': '6e64daf75b39014741fda7dedd58a8618299603bb32b5531a9e611d47c7210c7',
             'diagnostic': 'c961753a92a3e8f3e37cb84e36167936a699ae7403f4bfdffb285d941231f72f',
             'returned': 150,
             'cancel_checks': 45,
             'draws': 18427,
             'rng': 'e71e78399b64d4b0565ff31558f47171975f8886b24220cd92e6faaa9d7d56b5'},
 'specialists': {'proposals': '07d874c0cb7cafa53edd4326959b1da5f34abb36fb8003ff3b416b81112ee4c0',
                 'weights': 'b8e6f451e73ea088da9cfa478792a44299e5b977fe0ffdddeec1eb5023400884',
                 'before_draw': '86416d53974f835d06e10faee5a943d8a7592b073ef1f66158385e734f442ca8',
                 'rosters': 'fb7d9a8afb3bd544b18f99f42b2512effa43f19ad9c935ef9b6438fdd656438c',
                 'diagnostic': '568c53f4284fd63b1e08db42d567cc4734aa14ddfc90d209c47aaea48c0112bb',
                 'returned': 80,
                 'cancel_checks': 31,
                 'draws': 10123,
                 'rng': '9498897d369e5a5e64cb71169f6579e40d324fe462d1a8fc9e4e1521d5b719c2'},
 'weighted': {'proposals': 'b55366b0678cf9920930121cb311e467c2814cad5a5f68e823e5308b9ee6cfbd',
              'weights': '2f8dfb4b9bd0351c47692700806c179ad17484b035511f6fd98761b1c8c91f8a',
              'before_draw': 'f0bc36530e01cf9bf1df100adf0be051f6781fa0042029b7a0d610fa6bb79adc',
              'rosters': '6d9809ee6eee5b36ce30eab9eed70df0ece8c22b465ac82776d3907aad90ece2',
              'diagnostic': 'd80df842769d45aaaa2f1dd09a53af87f0b212080c54f6f090c1873e84520645',
              'returned': 50,
              'cancel_checks': 25,
              'draws': 6163,
              'rng': 'a160872731bacffeab01721b702f613c3127029408ad42954d4a648b231a6d6a'},
 'unavailable': {'proposals': '6edb6cb4d886361d9cae3d15009dcc8e1104b809cb09d2acddc0691dff300ae8',
                 'weights': '437224f48780e6b6a8c282ee382128cc0c21349a5e18ac904a9394fe8fd8c15a',
                 'before_draw': 'b78e7eb2218bc7fd7afebf3d31dc73d0553143e5aaeb33d9c490866693fac557',
                 'rosters': 'a3d197a1e314b1f4766d4fbe76b6feceb3f5138301e1df70ce61136d8e5c87fd',
                 'diagnostic': '2a04b3010aae962a6deb3268a2af8a5f04a3e5c775c83f8cb1dd65b246e212e6',
                 'returned': 50,
                 'cancel_checks': 25,
                 'draws': 6217,
                 'rng': '9c7ae231990967b3605e77d121a2f945ea7c82532c170c0b5ecd5e89b47be3b6'},
 'cancel_proposals': {'rosters': '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945',
                      'diagnostic': 'a7c432cf9fc305abce7e6df23654b3fe22a96d8fd56985f783ee9a36d2e033a4',
                      'returned': 0,
                      'cancel_checks': 2,
                      'draws': 600,
                      'rng': '88e7c9f62a8f930304815698a2d538210a49298de5faba124bc7ecee62260e2d'},
 'cancel_fitting': {'rosters': '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945',
                    'diagnostic': 'd8ade0c7802a171c14a9e407413f258db9a07d01113854aa4da3ab59a8bc4d13',
                    'returned': 0,
                    'cancel_checks': 17,
                    'draws': 6132,
                    'rng': '31b2b7c848137fc532cfeec9ea96bad94da940ac4d61c30b315127c5659732f1'},
 'empty': {'rosters': '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945',
           'diagnostic': '0209ec1a8bf6a81817aa243722f51ca88a522abd641d983b845ac986a6594af8',
           'returned': 0,
           'cancel_checks': 0,
           'draws': 0,
           'rng': None},
 'fractional': {'proposals': 'a615af41bbff1f93d7011576440f9c01fb86cb82b34e60bd434542ccd11ec37e',
                'weights': '4e2dc70b68c4f0c12be05c5f69a1e8ccc6e0e4fbce11ea1d908dd3839aa420d3',
                'before_draw': '0a856daac20888696199cc06011e7db2ce928c8c8bf1c1b17bf5eefe23c6dce1',
                'rosters': '836687716531adeec7520f65ed0e3858941f7b9ed760c6c2a9de3c4d5e36b8af',
                'diagnostic': '9a28c990ec30842b82c9c2544fd7f5b7526dbd90705408dff320a7cdcf973d68',
                'returned': 80,
                'cancel_checks': 31,
                'draws': 9907,
                'rng': '54f59d0284e39113352bbe32e26e2ac8f5e27483191cb02dfb0c667c6306abac'},
 'single': {'proposals': '14e01b506c3dad21654e58d22615b81f17ed4b62163763b3017a14e92265a5b1',
            'weights': '7a9ef4f82bc70c63f8b088259ef132929f278a9cd3f641115480c7186c18c15f',
            'before_draw': 'c4d08902716fb7d7f8bf85b5a215d8b6bb0b03dc88139ad5b2e12589b4d8a3f8',
            'rosters': 'ad21a3bf37e16e0827588ec5a8feaa54e8f0a5879ebd0170d25d8fc8ab7c3d67',
            'diagnostic': '055e2c023115dcbced7c5b863a227278e2d5aeda39c8e634db316a320f258d1b',
            'returned': 1,
            'cancel_checks': 16,
            'draws': 1201,
            'rng': 'fe0c5193338f5205a7a22538a3e1b1a6f53554d97bfa728e8519e8cb2e16e5b4'}}


class HistoricalFittingFactorsTests(unittest.TestCase):
    def test_default_fitted_weights_order_and_random_state_match_main(self):
        self.assertEqual(capture('default'),BASELINE['default'])
    def test_specialists_zero_targets_and_custom_salary_cap_match_main(self):
        self.assertEqual(capture('specialists'),BASELINE['specialists'])
    def test_ownership_draw_multipliers_match_main(self):
        self.assertEqual(capture('weighted'),BASELINE['weighted'])
    def test_unavailable_targets_remain_disclosed(self):
        self.assertEqual(capture('unavailable'),BASELINE['unavailable'])
    def test_cancellation_during_proposals_and_fitting_keeps_checkpoints(self):
        for case in ('cancel_proposals','cancel_fitting'):
            with self.subTest(case=case):self.assertEqual(capture(case),BASELINE[case])
    def test_empty_request_keeps_original_diagnostic(self):
        self.assertEqual(capture('empty'),BASELINE['empty'])


class HistoricalFeatureColumnsTests(unittest.TestCase):
    def test_fractional_salary_features_keep_exact_sampling_weights(self):
        self.assertEqual(capture('fractional'),BASELINE['fractional'])
    def test_one_opponent_with_many_proposals_keeps_original_result(self):
        self.assertEqual(capture('single'),BASELINE['single'])
    def test_feature_data_does_not_leak_between_distinct_pools(self):
        for case in ('default','specialists','default'):
            with self.subTest(case=case):self.assertEqual(capture(case),BASELINE[case])
