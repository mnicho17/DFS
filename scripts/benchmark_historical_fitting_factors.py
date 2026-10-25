"""Offline historical-field fitting benchmark against trusted merged main b93853f."""
from pathlib import Path
import argparse
import copy
import hashlib
import json
import random
import statistics
import subprocess
import sys
import time
import types
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
BASE='b93853f5ce3e6b70b5466ddf67d9f7ad2b58123f'


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def benchmark():
    source=subprocess.check_output(['git','show',BASE+':historical_field.py'],cwd=ROOT).decode('utf-8')
    from test_environment import install,network_attempts
    install()
    import historical_field as current
    from optimizers import _pkey
    from test_field_history_validation import priors
    from test_showdown_performance import _showdown_players
    baseline=types.ModuleType('historical_fitting_reference')
    exec(compile(source,'<trusted-main-historical-field>','exec'),baseline.__dict__)
    trials=[];summaries=[]
    for case,count in (('default',2000),('specialists',5000)):
        players=_showdown_players();target=priors();options=dict(seed=90211)
        if case=='specialists':
            for i in (6,24):players[i]['Position']='TE'
            for i in (14,32):players[i]['Position']='K'
            for i in (16,34):players[i]['Position']='DST'
            target['Captain position'].update(K=.15,DST=.05)
            options.update(salary_cap=48000,draw_weights={(slot,_pkey(p)):.4+(i%7)*.3
                for slot in ('Captain','FLEX') for i,p in enumerate(players)})
        original=copy.deepcopy((players,target,options));expected=None
        for repeat in range(3):
            modes=[('before',baseline),('after',current)]
            if repeat%2:modes.reverse()
            for mode,module in modes:
                instances=[];real_random=random.Random
                class ObservedRandom(real_random):
                    def __init__(self,*args,**kwargs):
                        super().__init__(*args,**kwargs);instances.append(self)
                with patch('random.Random',ObservedRandom):
                    start=time.perf_counter()
                    field=module.sample_history_field(players,count,target,**options)
                    elapsed=time.perf_counter()-start
                check=dict(rosters=digest([(_pkey(row['Captain']),tuple(_pkey(p) for p in row['Flex'])) for row in field]),
                           diagnostic=digest(field.diagnostic),rng=digest(instances[0].getstate()),returned=len(field))
                if expected is None:expected=check
                if check!=expected:raise AssertionError('Ordered outputs, diagnostics or RNG state changed')
                if (players,target,options)!=original:raise AssertionError('Inputs changed')
                row=dict(case=case,count=count,repeat=repeat,mode=mode,seconds=elapsed,**check)
                trials.append(row);print(json.dumps(row),flush=True)
        group=[row for row in trials if row['case']==case]
        summaries.append(dict(case=case,requested=count,medians={mode:statistics.median(row['seconds'] for row in group if row['mode']==mode) for mode in ('before','after')}))
    if network_attempts:raise AssertionError(network_attempts)
    return dict(baseline=BASE,source_sha256=hashlib.sha256((ROOT/'historical_field.py').read_bytes()).hexdigest(),
        scope='Synthetic 36-player fixture, fixed seed 90211, 2000 default or 5000 specialist opponents. Sampler-only timings, not whole builds or historical quality evidence.',
        exact_order_diagnostics_and_rng_equal=True,inputs_unchanged=True,trials=trials,summary=summaries)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    output=parser.parse_args().output.resolve()
    if output.exists():raise FileExistsError(output)
    result=benchmark()
    output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result['summary']))

if __name__=='__main__':main()
