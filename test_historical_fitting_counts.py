"""Additional frozen fitting evidence captured from PR163 before counter reuse."""
from test_environment import install
install()
import copy
import hashlib
import json
from pathlib import Path
import random
import unittest
from unittest.mock import patch
from historical_field import sample_history_field
from test_historical_fitting_factors import ordered_rosters
from test_showdown_performance import _showdown_players
from test_field_history_validation import priors


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def capture(case,sampler=sample_history_field):
    players=_showdown_players();target=priors();seed=1945
    if case=='reverse':
        players.reverse()
        for i,p in enumerate(players):
            if i%7==0:p['Position']='K'
            if i%11==0:p['Position']='DST'
        target['Captain position'].update(K=.1,DST=.1)
        target['Salary left']['unseen band']=.3
    elif case=='fallback':
        seed=120
        for i,p in enumerate(players):
            p.update(ProjOwnPct=0,ProjCptOwnPct=0,ProjFlexOwnPct=0,FlexProjection=.000001 if i%2 else 9.337)
        target['Quarterbacks']={'0':.9,'1':.1,'2':0}
    else:raise ValueError(case)
    before=copy.deepcopy((players,target));instances=[];record={};checks=[];real_random=random.Random
    class ObservedRandom(real_random):
        def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);self.draws=0;instances.append(self)
        def choices(self,population,weights=None,*,cum_weights=None,k=1):
            self.draws+=1
            if population and isinstance(population[0],dict):
                record.update(proposals=digest(ordered_rosters(population)),weights=digest([float(w).hex() for w in weights]))
            return super().choices(population,weights=weights,cum_weights=cum_weights,k=k)
    with patch('random.Random',ObservedRandom):
        field=sampler(players,60,target,seed=seed,cancelled=lambda:checks.append(1) or False)
    assert (players,target)==before
    record.update(rosters=digest(ordered_rosters(field)),diagnostic=digest(field.diagnostic),rng=digest(instances[0].getstate()),draws=instances[0].draws,cancel_checks=len(checks),returned=len(field))
    return record


class HistoricalFittingCountsTests(unittest.TestCase):
    def baseline(self,case):
        return json.loads((Path(__file__).parent/'tests'/'fixtures'/'historical-fitting-counts.json').read_text(encoding='utf-8'))[case]
    def test_reversed_specialist_pool_and_unavailable_target_keep_original_fit(self):
        self.assertEqual(capture('reverse'),self.baseline('reverse'))
    def test_projection_fallback_and_zero_targets_keep_original_fit(self):
        self.assertEqual(capture('fallback'),self.baseline('fallback'))
