"""Bounded synthetic refinement arithmetic benchmark against combined PR #192."""
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
BASE='89bb1c0a52c3731adf7d150ac691c47daaa2268d'

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
    rows=ShowdownOptimizer(players,seed=2026,build_style='Balanced').build_lineups(100)
    rows=simulate_showdown(rows,players,scenarios=2500,field_lineup_count=1000,seed=90210,precompiled_rosters=True)['lineups']
    original=copy.deepcopy(rows);trials=[];expected=None
    for trial in range(3):
        modes=[('before',baseline),('after',current)]
        if trial%2:modes.reverse()
        for mode,module in modes:
            working=copy.deepcopy(rows);started=time.perf_counter()
            result=module.select_portfolio(working,20,kind='showdown',rules={'min_unique':1},allow_relaxation=False,automatic_recovery=True,refinement_passes=256,refinement_polish_duplication=True)
            elapsed=time.perf_counter()-started;value=digest(result)
            if not result['report']['refinement_attempts']:raise AssertionError('Refinement was not exercised')
            for field in ('sim_top_hits','sim_top_five_hits','sim_win_hits','sim_scenario_values','sim_metrics'):
                if [getattr(r,field) for r in working]!=[getattr(r,field) for r in original]:raise AssertionError('Candidate evidence changed')
            if working!=original:raise AssertionError('Candidate rows changed')
            if expected is None:expected=value
            if value!=expected:raise AssertionError('Ordered portfolio or structured report changed')
            if len(result['lineups'])!=20:raise AssertionError('Incomplete portfolio')
            record=dict(trial=trial,mode=mode,seconds=elapsed,sha256=value,refinement_attempts=result['report']['refinement_attempts'],refinement_swaps=result['report']['refinement_swaps'])
            trials.append(record);print(json.dumps(record),flush=True)
    if rows!=original or [r.sim_scenario_values for r in rows]!=[r.sim_scenario_values for r in original]:raise AssertionError('Inputs changed')
    from test_environment import network_attempts
    if network_attempts:raise AssertionError('Unexpected network access')
    return dict(baseline=BASE,source_sha256=hashlib.sha256((ROOT/'portfolio_rules.py').read_bytes()).hexdigest(),basis='Synthetic distinct-roster selection only; not full-build or real-slate timing',candidates=len(rows),requested=20,scenarios=2500,field=1000,seed=90210,trials=trials,exact_order_and_report_equal=True,median_seconds={m:statistics.median(t['seconds'] for t in trials if t['mode']==m) for m in ('before','after')},limitations='Selection and refinement timing; generation and simulation excluded. Structured report listings are normalized and derived display text/timing excluded from parity hashes. Synthetic fixtures and local hardware/load; no model quality claim.')

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    destination=parser.parse_args().output.resolve()
    if destination.exists():raise FileExistsError('Output exists')
    receipt=benchmark();destination.write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    print(json.dumps(receipt['median_seconds']))
if __name__=='__main__':main()
