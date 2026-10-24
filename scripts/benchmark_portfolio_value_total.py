"""Bounded synthetic selection benchmark against trusted merged main 708aa15."""
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
BASE='708aa1511859aa5edeb9b89fc92e386a9663e497'

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
    rows=simulate_showdown(rows,players,scenarios=500,field_lineup_count=100,seed=90210,precompiled_rosters=True)['lineups']
    original=copy.deepcopy(rows);trials=[];expected=None
    for trial in range(3):
        modes=[('before',baseline),('after',current)]
        if trial%2:modes.reverse()
        for mode,module in modes:
            working=copy.deepcopy(rows);started=time.perf_counter()
            result=module.select_portfolio(working,100,kind='showdown',rules={'min_unique':1},allow_relaxation=False)
            elapsed=time.perf_counter()-started;value=digest(result)
            if expected is None:expected=value
            if value!=expected:raise AssertionError('Ordered portfolio or structured report changed')
            if len(result['lineups'])!=100:raise AssertionError('Incomplete portfolio')
            record=dict(trial=trial,mode=mode,seconds=elapsed,sha256=value)
            trials.append(record);print(json.dumps(record),flush=True)
    if rows!=original or [r.sim_scenario_values for r in rows]!=[r.sim_scenario_values for r in original]:raise AssertionError('Inputs changed')
    return dict(baseline=BASE,source_sha256=hashlib.sha256((ROOT/'portfolio_rules.py').read_bytes()).hexdigest(),basis='Synthetic distinct-roster selection only; not full-build or real-slate timing',candidates=len(rows),requested=100,scenarios=500,field=100,seed=90210,trials=trials,exact_order_and_report_equal=True,median_seconds={m:statistics.median(t['seconds'] for t in trials if t['mode']==m) for m in ('before','after')},limitations='Selection-only timing; no refinement, generation or simulation included. Structured report listings are normalized and derived display text/timing excluded from parity hashes. Synthetic fixtures and local hardware/load; no model quality claim.')

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    destination=parser.parse_args().output.resolve()
    if destination.exists():raise FileExistsError('Output exists')
    receipt=benchmark();destination.write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    print(json.dumps(receipt['median_seconds']))
if __name__=='__main__':main()
