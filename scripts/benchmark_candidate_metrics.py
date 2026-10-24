"""Synthetic candidate-metric load test against a trusted local baseline commit."""
from pathlib import Path
import argparse
import copy
import hashlib
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import types
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
BASE='08a44adec231bec4c2a9166872e50d65fc50b856'

def benchmark(*,scenarios=1000,candidates=2000,field=100,repeats=3,baseline=BASE):
    for value,limit in ((scenarios,2000),(candidates,2000),(field,10000),(repeats,5)):
        if type(value) is not int or not 0<value<=limit:raise ValueError('Benchmark bounds exceeded')
    source=subprocess.check_output(['git','show',baseline+':showdown_simulation.py'],cwd=ROOT).decode('utf-8')
    from test_environment import install
    install()
    import showdown_simulation as current
    from test_showdown_performance import _showdown_players
    from optimizers import ShowdownOptimizer
    from scripts.benchmark_scenario_replay import exact
    from test_scenario_cache import result_values
    reference=types.ModuleType('reference_candidate_metrics');exec(source,reference.__dict__)
    players=_showdown_players();original=copy.deepcopy(players)
    base=ShowdownOptimizer(players)._build_lineups_fast(min(50,candidates))
    if not base:raise ValueError('Synthetic fixture has no lineups')
    rows=[copy.deepcopy(base[i%len(base)]) for i in range(candidates)]
    trials=[];expected=None
    with tempfile.TemporaryDirectory(prefix='dfs-candidate-metrics-') as folder,patch.dict(os.environ,{'DFS_OPTIMIZER_DATA_DIR':folder}):
        for n in range(repeats):
            modes=[('before',reference),('after',current)]
            if n%2:modes.reverse()
            for mode,module in modes:
                print(f'Trial {n+1}/{repeats}: {mode}',flush=True)
                started=time.perf_counter()
                result=module.simulate_showdown(copy.deepcopy(rows),copy.deepcopy(players),scenarios=scenarios,field_lineup_count=field,seed=90210,precompiled_rosters=True,scenario_cache=False)
                elapsed=time.perf_counter()-started
                digest=hashlib.sha256(json.dumps(exact(result_values(result)),sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
                if expected is None:expected=digest
                if digest!=expected:raise AssertionError('Exact metric/hit/scenario-value mismatch')
                if result['report']['scenarios']!=scenarios or result['report']['field_lineups']!=field:raise AssertionError('Incomplete benchmark')
                trials.append(dict(trial=n,mode=mode,seconds=elapsed,sha256=digest,profile=result['report']['simulation_profile']))
    if players!=original:raise AssertionError('Synthetic inputs changed')
    return dict(version=1,basis='Synthetic metric-loop load test with repeated legal fixture rosters; not candidate quality or whole-build evidence',baseline=baseline,source_sha256=hashlib.sha256((ROOT/'showdown_simulation.py').read_bytes()).hexdigest(),python=sys.version,scenarios=scenarios,candidates=candidates,distinct_fixture_rosters=len(base),field=field,seed=90210,repeats=repeats,exact_results_equal=True,production_data_untouched=True,trials=trials,
        median_seconds={m:statistics.median(t['seconds'] for t in trials if t['mode']==m) for m in ('before','after')},
        median_candidate_seconds={m:statistics.median(t['profile']['timings']['candidates_seconds'] for t in trials if t['mode']==m) for m in ('before','after')},
        limitations='Alternating before/after order, uncached simulation, synthetic fallback ownership and local hardware/load. Setup/scoring/result assembly included; generation, portfolio selection and independent audit excluded. Baseline file executes against shared current dependencies; this isolates the metric-loop execution change, not a complete historical checkout.')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name,value in (('scenarios',1000),('candidates',2000),('field',100),('repeats',3)):parser.add_argument('--'+name,type=int,default=value)
    parser.add_argument('--baseline-ref',default=BASE);parser.add_argument('--output',type=Path)
    args=parser.parse_args();destination=args.output.resolve() if args.output else None
    if destination and destination.exists():raise FileExistsError('Output already exists')
    result=benchmark(scenarios=args.scenarios,candidates=args.candidates,field=args.field,repeats=args.repeats,baseline=args.baseline_ref)
    if destination:destination.write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='trials'},indent=2))

if __name__=='__main__':main()
