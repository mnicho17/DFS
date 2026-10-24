"""Boundary parity frozen from main ae1698e before normalizing private copies."""
from test_environment import install
install()
import copy
import builtins
import hashlib
import json
from unittest.mock import patch
import unittest
from portfolio_rules import select_portfolio
from scripts.benchmark_scenario_replay import exact
from test_portfolio_value_total import case, classic_case, digest

BASELINE = {'classic_mixed': ['b0f4102d25b199a292f072df6fd0d834a8bb3002cca3a7297d67a86bbb77c024', '9ff8f5d137e84fa6c5e8ab9b806bf14210ff0254f6d0135cae7183817c7034ed'], 'classic_large': ['b0f4102d25b199a292f072df6fd0d834a8bb3002cca3a7297d67a86bbb77c024', '5d068a57b0568eb801d54f75c04c4d0cabf79352e7b6add342541df2faf0b1e0'], 'classic_nonpositive': ['b0f4102d25b199a292f072df6fd0d834a8bb3002cca3a7297d67a86bbb77c024', 'adcad21dedc825320d611aa9dc856692c9d4bab004ee1eae274cd969a832acd2'], 'showdown_mixed': ['5f6e35d0744af29337a05f7bed58159020e3881ec8b7998be01d4c7e8262cb1d', '9adfec171beb4c7b74fd66b2d5e7c2c2af52c01a1f0f115437b6176b4408a26b'], 'showdown_large': ['5f6e35d0744af29337a05f7bed58159020e3881ec8b7998be01d4c7e8262cb1d', '8aa1a28edc05b3cb9f2fc502025b6f609495adc1b1aa5f7727ee94a16b4d51fb'], 'showdown_nonpositive': ['5f6e35d0744af29337a05f7bed58159020e3881ec8b7998be01d4c7e8262cb1d', '46b0565159fb5f41d2181d6cdec3e1f610d289a399d314101615eaeb26b7647a']}

def boundary_case(label,kind):
    rows,options=case('normal')
    if kind=='classic':rows=classic_case();options.update(kind='classic')
    for n,row in enumerate(rows):
        if label=='mixed':
            row.sim_scenario_values={7:'2.5',1:-0.0,19:-3.75,0:0.0,13:(n+1)/7,3:1e-300}
        elif label=='large':
            row.sim_scenario_values={19:1e300,2:(n+1)*1e290,0:-1e300,7:1e-300}
        else:
            row.sim_scenario_values={19:-float(n+1),2:-0.0,7:'0.0'}
    options.update(refinement_passes=2,retained_lineups=[rows[-1]])
    return rows,options

def captured_select(rows,options):
    keys=[]
    def observed(*args,**kwargs):
        if 'key' in kwargs:
            original=kwargs['key']
            def record(row):
                value=original(row);keys.append(value);return value
            kwargs['key']=record
        return builtins.max(*args,**kwargs)
    with patch('portfolio_rules.max',side_effect=observed,create=True):
        result=select_portfolio(rows,4,**options)
    if not keys:raise AssertionError('Greedy scores were not observed')
    encoded=json.dumps(exact(keys),sort_keys=True,separators=(',',':')).encode()
    return result,hashlib.sha256(encoded).hexdigest()

class PortfolioValueNormalizationTests(unittest.TestCase):
    def test_boundary_values_keep_original_order_reports_and_input_evidence(self):
        for kind in ('classic','showdown'):
            for label in ('mixed','large','nonpositive'):
                with self.subTest(kind=kind,label=label):
                    rows,options=boundary_case(label,kind)
                    before=copy.deepcopy(rows)
                    result,scores=captured_select(rows,options)
                    self.assertEqual([digest(result),scores],BASELINE[kind+'_'+label])
                    self.assertEqual(exact([r.sim_scenario_values for r in rows]),exact([r.sim_scenario_values for r in before]))
                    self.assertEqual([r.sim_metrics for r in rows],[r.sim_metrics for r in before])
                    self.assertTrue(any(r is rows[-1] for r in result['lineups']))

    def test_invalid_used_value_still_fails_without_mutating_inputs(self):
        rows,options=case('normal')
        for row in rows:row.sim_scenario_values={0:1.5,1:'invalid'}
        before=copy.deepcopy(rows)
        with self.assertRaises(ValueError):select_portfolio(rows,4,**options)
        self.assertEqual([r.sim_scenario_values for r in rows],[r.sim_scenario_values for r in before])

if __name__=='__main__':unittest.main()
