"""Reusable draw tables preserve weighted choices and remain call-local/bounded."""
from test_environment import install
install()
import copy
from itertools import accumulate
import random
import unittest
from unittest.mock import patch
import historical_field as history
from optimizers import _pkey,_showdown_cpt_own,_showdown_flex_own
from test_field_history_validation import priors
from test_historical_fitting_factors import ordered_rosters
from test_showdown_performance import _showdown_players


class HistoricalDrawTablesTests(unittest.TestCase):
    def test_cached_tables_match_every_weighted_draw_and_rng_state(self):
        real_random=random.Random
        for fallback in (False,True):
            players=_showdown_players()
            if fallback:
                for p in players:p.update(ProjOwnPct=0,ProjCptOwnPct=0,ProjFlexOwnPct=0)
            players[-1]['CptSalary']=0
            multipliers={(slot,_pkey(p)):.4+(i%7)*.3 for slot in ('Captain','FLEX') for i,p in enumerate(players)}
            captains=[i for i,p in enumerate(players) if p['CptSalary']>0]
            def weights(fn,slot):
                raw=[max(0,fn(p)) for p in players]
                values=[max(.05,x) for x in raw] if any(raw) else [max(.1,float(p.get('FlexProjection') or 0))**1.3 for p in players]
                return [value*multipliers[(slot,_pkey(p))] for value,p in zip(values,players)]
            cw,fw=weights(_showdown_cpt_own,'Captain'),weights(_showdown_flex_own,'FLEX')
            checks=[];tables={};owner=self
            class ObservedRandom(real_random):
                def choices(self,population,weights=None,*,cum_weights=None,k=1):
                    if population and type(population[0]) is int:
                        expected=[(cw if population==captains else fw)[i] for i in population]
                        if cum_weights is not None:
                            owner.assertEqual(tuple(x.hex() for x in cum_weights),tuple(x.hex() for x in accumulate(expected)))
                            tables.setdefault(tuple(population),set()).add(id(cum_weights))
                        clone=real_random();clone.setstate(self.getstate())
                        choice=clone.choices(population,weights=expected,k=k)
                        actual=super().choices(population,weights=weights,cum_weights=cum_weights,k=k)
                        owner.assertEqual(actual,choice);owner.assertEqual(self.getstate(),clone.getstate())
                        checks.append(1);return actual
                    return super().choices(population,weights=weights,cum_weights=cum_weights,k=k)
            with self.subTest(fallback=fallback),patch('historical_field.random.Random',ObservedRandom):
                field=history.sample_history_field(players,20,priors(),seed=90211,draw_weights=multipliers)
            self.assertEqual(len(field),20);self.assertGreater(len(checks),1000)
            # A single cached object per stable population, throughout this call.
            self.assertTrue(tables);self.assertTrue(all(len(ids)==1 for ids in tables.values()))

    def test_disabling_first_flex_cache_keeps_all_outputs_and_cancellation_checks(self):
        results=[];players=_showdown_players();before=copy.deepcopy(players)
        for limit in (128,0):
            checks=[]
            with patch.object(history,'FIRST_FLEX_CACHE_MAX_PLAYERS',limit):
                field=history.sample_history_field(players,80,priors(),seed=413,cancelled=lambda:checks.append(1) or False)
            results.append((ordered_rosters(field),field.diagnostic,len(checks)))
        self.assertEqual(results[0],results[1]);self.assertEqual(players,before)

    def test_large_pools_skip_quadratic_first_flex_cache(self):
        source=_showdown_players();players=[]
        for i in range(129):
            p=copy.deepcopy(source[i%len(source)])
            p.update(FlexID=str(i),CptID='c'+str(i),FlexNamePlusID='Player '+str(i),Name='Player '+str(i))
            players.append(p)
        real_random=random.Random;tables=[]
        class ObservedRandom(real_random):
            def choices(self,population,weights=None,*,cum_weights=None,k=1):
                if cum_weights is not None:tables.append(len(population))
                return super().choices(population,weights=weights,cum_weights=cum_weights,k=k)
        with patch('historical_field.random.Random',ObservedRandom):
            field=history.sample_history_field(players,1,priors(),seed=33)
        self.assertEqual(len(field),1);self.assertTrue(tables);self.assertEqual(set(tables),{129})
