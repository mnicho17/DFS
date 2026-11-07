"""Fresh Showdown audit-sized simulations against PR #195."""
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = 'fdc09db02fab35c8f9053c9a83a8196bfd62afc2'


def benchmark():
    source = subprocess.check_output(['git', 'show', BASE + ':showdown_simulation.py'], cwd=ROOT)
    from test_environment import install, network_attempts
    install()
    import showdown_simulation as current
    from optimizers import ShowdownOptimizer
    from test_showdown_performance import _showdown_players
    from test_scenario_cache import result_values
    from scripts.benchmark_scenario_replay import exact
    baseline = types.ModuleType('baseline_showdown_field_scoring')
    exec(source.decode('utf-8'), baseline.__dict__)
    players = _showdown_players()
    rows = ShowdownOptimizer(players).build_lineups(80)
    original = copy.deepcopy(players)
    expected = None
    trials = []
    for trial in range(3):
        modes = [('before', baseline.simulate_showdown), ('after', current.simulate_showdown)]
        if trial % 2:
            modes.reverse()
        for mode, simulate in modes:
            working_players, working_rows = copy.deepcopy(players), copy.deepcopy(rows)
            started = time.perf_counter()
            result = simulate(working_rows, working_players, scenarios=2000,
                              field_lineup_count=4000, seed=481516, scenario_cache=False, precompiled_rosters=True)
            elapsed = time.perf_counter() - started
            report = {k: v for k, v in result['report'].items() if k != 'simulation_profile'}
            value = hashlib.sha256(json.dumps(exact(dict(rows=result_values(result), report=report)),
                sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
            if expected is None:
                expected = value
            if value != expected:
                raise AssertionError('Exact metrics, scenario evidence or field report changed')
            profile = result['report']['simulation_profile']
            if profile['completed_scenarios'] != 2000 or profile['reused_scenarios'] != 0 or profile['field_count'] != 4000:
                raise AssertionError('Incomplete fresh audit-sized simulation')
            if working_players != original:
                raise AssertionError('Player inputs changed')
            record = dict(trial=trial, mode=mode, seconds=elapsed, result_sha256=value, profile=profile)
            trials.append(record)
            print(json.dumps(record), flush=True)
    if network_attempts:
        raise AssertionError('Unexpected network access')
    return dict(baseline=BASE, source_sha256=hashlib.sha256((ROOT / 'showdown_simulation.py').read_bytes()).hexdigest(),
        candidates=len(rows), scenarios=2000, field=4000, seed=481516, exact_results_equal=True, trials=trials,
        median_seconds={mode: statistics.median(r['seconds'] for r in trials if r['mode'] == mode)
                        for mode in ('before', 'after')},
        limitations='Three alternating-order local synthetic fresh Showdown simulations, including opponent generation; no scenario replay. This incremental change is compared against PR #195 with the same existing sampler and candidate scoring. Not a whole-build or real-slate speedup claim. Observational profile timing excluded from exact parity hashes.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    destination = parser.parse_args().output.resolve()
    if destination.exists():
        raise FileExistsError('Output exists')
    receipt = benchmark()
    destination.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
