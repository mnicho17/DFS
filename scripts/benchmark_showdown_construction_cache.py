"""Bounded offline construction benchmark against trusted merged main 4eab227."""
from pathlib import Path
import argparse
import copy
import hashlib
import json
import statistics
import subprocess
import sys
import time
import types
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
BASE='4eab227090a2ac401e2cb1622927f2edf4d9b3ed'

def benchmark():
    source=subprocess.check_output(['git','show',BASE+':optimizers.py'],cwd=ROOT).decode('utf-8')
    from test_environment import install,network_attempts
    install()
    import optimizers as current
    from test_showdown_performance import _showdown_players
    baseline=types.ModuleType('construction_reference');sys.modules[baseline.__name__]=baseline;exec(source,baseline.__dict__)
    players=_showdown_players()
    for p in players[:5]:p['LockCpt']=True
    original=copy.deepcopy(players);trials=[]
    for style in ('Strategic','Balanced','Randomized'):
        options=dict(seed=35764,own_mode='Balanced',own_weight=.15,build_style=style,minimum_salary=47500)
        bank=baseline.ShowdownOptimizer(copy.deepcopy(players),**options)._build_lineups_fast(100)
        excluded={(current._pkey(r['Captain']),tuple(sorted(current._pkey(p) for p in r['Flex']))) for r in bank}
        for late in (False,True):
            expected=None;expected_rng=None
            for repeat in range(3):
                modes=[('before',baseline),('after',current)]
                if repeat%2:modes.reverse()
                for mode,module in modes:
                    inputs=copy.deepcopy(players);optimizer=module.ShowdownOptimizer(inputs,**options)
                    start=time.perf_counter();rows=optimizer._build_lineups_fast(12,excluded_signatures=excluded if late else ())
                    elapsed=time.perf_counter()-start
                    signatures=[(current._pkey(r['Captain']),tuple(current._pkey(p) for p in r['Flex'])) for r in rows]
                    digest=hashlib.sha256(json.dumps(signatures,separators=(',',':')).encode()).hexdigest()
                    rng=optimizer.rng.getstate()
                    if expected is None:expected=digest;expected_rng=rng
                    if digest!=expected or rng!=expected_rng:raise AssertionError('Ordered outputs or RNG state changed')
                    if inputs!=original:raise AssertionError('Inputs changed')
                    record=dict(style=style,excluded=len(excluded) if late else 0,repeat=repeat,mode=mode,seconds=elapsed,returned=len(rows),sha256=digest)
                    trials.append(record);print(json.dumps(record),flush=True)
    if network_attempts:raise AssertionError(network_attempts)
    summary=[]
    for style in ('Strategic','Balanced','Randomized'):
        for late in (False,True):
            group=[r for r in trials if r['style']==style and bool(r['excluded'])==late]
            summary.append(dict(style=style,previous_bank=late,medians={m:statistics.median(r['seconds'] for r in group if r['mode']==m) for m in ('before','after')}))
    return dict(baseline=BASE,source_sha256=hashlib.sha256((ROOT/'optimizers.py').read_bytes()).hexdigest(),basis='Synthetic 36-player fixture, five Captain locks, Near Cap, seed 35764, twelve requested rows. Late case excludes up to 100 original fixture outputs; not real-slate or whole-build timing',trials=trials,summary=summary,exact_order_and_rng_equal=True)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    output=parser.parse_args().output.resolve()
    if output.exists():raise FileExistsError(output)
    result=benchmark();output.write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result['summary']))
if __name__=='__main__':main()
