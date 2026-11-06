"""Fresh Classic audit-sized simulations against merged main 157f11b."""
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
BASE = '157f11b2e3b88287d4b7e2e4f2d0133abfe0392b'


def benchmark():
    source = subprocess.check_output(['git', 'show', BASE + ':nfl_simulation.py'], cwd=ROOT)
    from test_environment import install, network_attempts
    install()
    import nfl_simulation as current
    from optimizers import MultiSportClassicOptimizer
    from test_nfl_logic import _fixture_players
    from test_scenario_cache import result_values
    from scripts.benchmark_scenario_replay import exact
    baseline = types.ModuleType('baseline_classic_field_scoring')
    exec(source.decode('utf-8'), baseline.__dict__)
    players = _fixture_players()
    rows = MultiSportClassicOptimizer(players).build_lineups(80)
    original = copy.deepcopy(players)
    expected = None
    trials = []
    for trial in range(3):
        modes = [('before', baseline.simulate_nfl_contest), ('after', current.simulate_nfl_contest)]
        if trial % 2:
            modes.reverse()
        for mode, simulate in modes:
            working_players, working_rows = copy.deepcopy(players), copy.deepcopy(rows)
            started = time.perf_counter()
            result = simulate(working_rows, working_players, scenarios=2000,
                              field_lineup_count=1000, seed=481516, scenario_cache=False)
            elapsed = time.perf_counter() - started
            report = {k: v for k, v in result['report'].items() if k != 'simulation_profile'}
            value = hashlib.sha256(json.dumps(exact(dict(rows=result_values(result), report=report)),
                sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
            if expected is None:
                expected = value
            if value != expected:
                raise AssertionError('Exact metrics, scenario evidence or field report changed')
            profile = result['report']['simulation_profile']
            if profile['completed_scenarios'] != 2000 or profile['reused_scenarios'] != 0 or profile['field_count'] != 1000:
                raise AssertionError('Incomplete fresh audit-sized simulation')
            if working_players != original:
                raise AssertionError('Player inputs changed')
            record = dict(trial=trial, mode=mode, seconds=elapsed, result_sha256=value, profile=profile)
            trials.append(record)
            print(json.dumps(record), flush=True)
    if network_attempts:
        raise AssertionError('Unexpected network access')
    return dict(baseline=BASE, source_sha256=hashlib.sha256((ROOT / 'nfl_simulation.py').read_bytes()).hexdigest(),
        candidates=len(rows), scenarios=2000, field=1000, seed=481516, exact_results_equal=True, trials=trials,
        median_seconds={mode: statistics.median(r['seconds'] for r in trials if r['mode'] == mode)
                        for mode in ('before', 'after')},
        limitations='Three alternating-order local synthetic fresh Classic simulations, including opponent generation; no scenario replay. This independent change is compared against merged main, without PRs 188-190 optimizations. Not a whole-build or real-slate speedup claim. Observational profile timing excluded from exact parity hashes.')


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
