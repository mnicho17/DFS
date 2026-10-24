"""Bounded synthetic selection normalization benchmark against merged main ae1698e."""
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
BASE='ae1698e00681f102299897f9cb6baddcb21cc160'

def benchmark():
    source=subprocess.check_output(['git','show',BASE+':portfolio_rules.py'],cwd=ROOT).decode('utf-8')
    from test_environment import install
    install()
    import portfolio_rules as current
    from test_showdown_performance import _showdown_players
    from optimizers import ShowdownOptimizer
    from showdown_simulation import simulate_showdown
    from test_portfolio_value_total import digest
    baseline=types.ModuleType('reference_value_total');exec(source,baseline.__dict__)
    players=_showdown_players()
    rows=ShowdownOptimizer(players)._build_lineups_fast(600)
    base=rows
    cases=[]
    for scenarios in (100,500):
        scored=simulate_showdown(copy.deepcopy(base),players,scenarios=scenarios,field_lineup_count=100,seed=90210,precompiled_rosters=True)['lineups']
        for requested in (20,150):cases.append((scenarios,requested,scored))
    receipts=[]
    for scenarios,requested,rows in cases:
        receipts.append(measure(rows,scenarios,requested,baseline,current,digest))
    return dict(baseline=BASE,source_sha256=hashlib.sha256((ROOT/'portfolio_rules.py').read_bytes()).hexdigest(),basis='Synthetic distinct-roster selection only; not full-build or real-slate timing',workloads=receipts,limitations='Three alternating trials per workload; timed selection only, no refinement. Structured report listing order normalized; derived text and timing excluded. Local hardware/load affect timings. No quality claim.')

def measure(rows,scenarios,requested,baseline,current,digest):
    original=copy.deepcopy(rows);trials=[];expected=None
    for trial in range(3):
        modes=[('before',baseline),('after',current)]
        if trial%2:modes.reverse()
        for mode,module in modes:
            working=copy.deepcopy(rows);started=time.perf_counter()
            result=module.select_portfolio(working,requested,kind='showdown',rules={'min_unique':1},allow_relaxation=False)
            elapsed=time.perf_counter()-started;value=digest(result)
            if working!=original or [r.sim_scenario_values for r in working]!=[r.sim_scenario_values for r in original]:raise AssertionError('Selector inputs changed')
            if expected is None:expected=value
            if value!=expected:raise AssertionError('Ordered portfolio or structured report changed')
            if len(result['lineups'])!=requested:raise AssertionError('Incomplete portfolio')
            record=dict(scenarios=scenarios,requested=requested,trial=trial,mode=mode,seconds=elapsed,sha256=value)
            trials.append(record);print(json.dumps(record),flush=True)
    if rows!=original or [r.sim_scenario_values for r in rows]!=[r.sim_scenario_values for r in original]:raise AssertionError('Inputs changed')
    return dict(scenarios=scenarios,candidates=len(rows),requested=requested,trials=trials,exact_order_and_report_equal=True,median_seconds={m:statistics.median(t['seconds'] for t in trials if t['mode']==m) for m in ('before','after')})

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    destination=parser.parse_args().output.resolve()
    if destination.exists():raise FileExistsError('Output exists')
    receipt=benchmark();destination.write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    print(json.dumps([{k:v for k,v in row.items() if k!='trials'} for row in receipt['workloads']]))
if __name__=='__main__':main()
