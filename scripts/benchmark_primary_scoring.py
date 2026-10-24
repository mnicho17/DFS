"""Synthetic comparison of existing reference/precompiled Showdown scoring."""
from pathlib import Path
import argparse,copy,hashlib,json,os,statistics,sys,tempfile,time
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def exact(value):
    from array import array
    if isinstance(value,float):return {'float_hex':value.hex()}
    if isinstance(value,dict):return {'mapping':sorted([[exact(k),exact(v)] for k,v in value.items()],key=lambda pair:json.dumps(pair[0],sort_keys=True))}
    if isinstance(value,(set,frozenset)):return {'set':sorted([exact(v) for v in value],key=lambda item:json.dumps(item,sort_keys=True))}
    if isinstance(value,(list,tuple,array)):return [exact(v) for v in value]
    return value


def benchmark(scenarios=1000,field=5000,candidates=50,repeats=3):
    from test_showdown_performance import _showdown_players
    from test_scenario_cache import result_values
    from showdown_simulation import simulate_showdown
    from optimizers import ShowdownOptimizer
    for value,limit in ((scenarios,10000),(field,10000),(candidates,150),(repeats,5)):
        if type(value) is not int or not 0<value<=limit:raise ValueError('Benchmark bounds exceeded')
    players=_showdown_players();before=copy.deepcopy(players);rows=ShowdownOptimizer(players).build_lineups(candidates)
    if len(rows)!=candidates:raise ValueError('Synthetic candidate target not filled')
    trials=[];expected=None
    with tempfile.TemporaryDirectory(prefix='dfs-primary-scoring-benchmark-') as folder,patch.dict(os.environ,{'DFS_OPTIMIZER_DATA_DIR':folder}):
        for n in range(repeats):
            trial=[]
            for compiled in (False,True):
                print(f'Trial {n+1}/{repeats}: compiled={compiled}',flush=True)
                started=time.perf_counter()
                r=simulate_showdown(copy.deepcopy(rows),copy.deepcopy(players),scenarios=scenarios,field_lineup_count=field,seed=90210,capture_distributions=True,scenario_cache=False,precompiled_rosters=compiled)
                elapsed=time.perf_counter()-started
                digest=hashlib.sha256(json.dumps(exact(result_values(r)),sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
                if expected is None:expected=digest
                if digest!=expected:raise AssertionError('Exact scoring output mismatch')
                if r['report']['scenarios']!=scenarios or r['report']['field_lineups']!=field:raise AssertionError('Incomplete benchmark')
                trial.append(dict(compiled=compiled,wall_seconds=elapsed,result_sha256=digest,profile=r['report']['simulation_profile']))
            trials.append(trial)
    if players!=before:raise AssertionError('Synthetic inputs changed')
    return dict(version=1,basis='Synthetic uncached Showdown simulation, not a whole-build or real-slate speedup claim',
        scenarios=scenarios,field=field,candidates=candidates,repeats=repeats,seed=90210,python=sys.version,
        exact_results_equal=True,production_cache_untouched=True,trials=trials,
        median_seconds={str(flag):statistics.median(row['wall_seconds'] for t in trials for row in t if row['compiled']==flag) for flag in (False,True)},
        limitations='Fixed reference/compiled ordering, synthetic players and local hardware/load; outcome draws, field sampling, ranking and candidate scoring stay unchanged.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name,default in (('scenarios',1000),('field',5000),('candidates',50),('repeats',3)):parser.add_argument('--'+name,type=int,default=default)
    parser.add_argument('--output',type=Path);args=parser.parse_args()
    output=args.output.resolve() if args.output else None
    if output and output.exists():raise FileExistsError('Output already exists')
    result=benchmark(args.scenarios,args.field,args.candidates,args.repeats)
    if output:output.write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='trials'},indent=2))


if __name__=='__main__':main()
