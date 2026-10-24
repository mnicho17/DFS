"""Synthetic cold/warm replay benchmark. All caches live in disposable folders."""
from pathlib import Path
import argparse
import copy
import hashlib
import json
import os
import statistics
import sys
import tempfile
import time
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def exact(value):
    from array import array
    if isinstance(value,float):return {'float_hex':value.hex()}
    if isinstance(value,dict):return {'mapping':sorted([[exact(k),exact(v)] for k,v in value.items()],key=lambda pair:json.dumps(pair[0],sort_keys=True))}
    if isinstance(value,(set,frozenset)):return {'set':sorted([exact(v) for v in value],key=lambda item:json.dumps(item,sort_keys=True))}
    if isinstance(value,(list,tuple,array)):return [exact(v) for v in value]
    return value


def benchmark(*,scenarios=1000,field=5000,candidates=50,repeats=3,progress=print):
    from test_showdown_performance import _showdown_players
    from test_scenario_cache import result_values
    from showdown_simulation import simulate_showdown
    from optimizers import ShowdownOptimizer
    for value,limit in ((scenarios,10000),(field,10000),(candidates,150),(repeats,5)):
        if type(value) is not int or not 0<value<=limit:raise ValueError('Benchmark bounds exceeded')
    players=_showdown_players();original=copy.deepcopy(players)
    rows=ShowdownOptimizer(players).build_lineups(candidates)
    if len(rows)!=candidates:raise ValueError('Synthetic candidate target not filled')
    opts=dict(scenarios=scenarios,field_lineup_count=field,seed=90210,capture_distributions=True)
    results=[];expected=None
    for repeat in range(repeats):
        with tempfile.TemporaryDirectory(prefix='dfs-replay-benchmark-') as folder,patch.dict(os.environ,{'DFS_OPTIMIZER_DATA_DIR':folder}):
            receipt=[]
            for label,enabled in (('uncached',False),('cold',True),('warm',True)):
                progress(f'Run {repeat+1}/{repeats}: {label}',flush=True)
                started=time.perf_counter();result=simulate_showdown(copy.deepcopy(rows),copy.deepcopy(players),scenario_cache=enabled,**opts)
                wall=time.perf_counter()-started;report=result['report'];cache=report.get('scenario_cache') or {}
                if report['scenarios']!=scenarios or report['field_lineups']!=field:raise ValueError('Incomplete benchmark run')
                encoded=json.dumps(exact(result_values(result)),sort_keys=True,separators=(',',':'),allow_nan=False).encode()
                digest=hashlib.sha256(encoded).hexdigest()
                if expected is None:expected=digest
                if digest!=expected:raise AssertionError('Exact metrics/hits/distribution mismatch')
                if label=='cold' and cache.get('status')!='saved':raise AssertionError('Cold cache not saved')
                if label=='warm' and (cache.get('status')!='reused' or cache.get('reused_scenarios')!=scenarios):raise AssertionError('Warm run did not replay all scenarios')
                receipt.append(dict(mode=label,wall_seconds=wall,result_sha256=digest,cache_status=cache.get('status','disabled'),
                    reused_scenarios=cache.get('reused_scenarios',0),stored_bytes=cache.get('stored_bytes',0),profile=report.get('simulation_profile')))
            results.append(receipt)
    if players!=original:raise AssertionError('Synthetic inputs changed')
    medians={label:statistics.median(next(row['wall_seconds'] for row in trial if row['mode']==label) for trial in results) for label in ('uncached','cold','warm')}
    return dict(version=1,basis='Synthetic Showdown simulation only; not a whole-build or real-slate speedup claim',
        limitations='Fixed uncached/cold/warm ordering; OS cache, hardware and load can affect timings. Field sampling, full replay verification and candidate scoring remain included; independent ranking and portfolio selection are outside this benchmark.',
        scenarios=scenarios,field=field,candidates=candidates,repeats=repeats,seed=90210,python=sys.version,
        exact_results_equal=True,production_cache_untouched=True,median_seconds=medians,
        warm_vs_uncached_ratio=medians['warm']/medians['uncached'],trials=results)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenarios',type=int,default=1000);parser.add_argument('--field',type=int,default=5000)
    parser.add_argument('--candidates',type=int,default=50);parser.add_argument('--repeats',type=int,default=3)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.output and args.output.exists():raise FileExistsError('Benchmark output already exists')
    result=benchmark(scenarios=args.scenarios,field=args.field,candidates=args.candidates,repeats=args.repeats)
    encoded=json.dumps(result,indent=2,allow_nan=False)
    if args.output:args.output.write_text(encoded,encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='trials'},indent=2))


if __name__=='__main__':main()
