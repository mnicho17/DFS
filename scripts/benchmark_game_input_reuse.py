"""Fresh audit-sized simulations against pinned PR 189; disposable inputs."""
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
BASE = '463cacd3d179b090786d5903a7f7f77250ddde90'


def benchmark():
    sources = {name: subprocess.check_output(
        ['git', 'show', BASE + ':' + name + '.py'], cwd=ROOT).decode('utf-8')
        for name in ('nfl_simulation', 'showdown_simulation')}
    from test_environment import install, network_attempts
    install()
    import nfl_simulation as classic
    import showdown_simulation as showdown
    from optimizers import MultiSportClassicOptimizer, ShowdownOptimizer
    from test_nfl_logic import _fixture_players
    from test_showdown_performance import _showdown_players
    from test_scenario_cache import result_values
    from scripts.benchmark_scenario_replay import exact
    baseline = {}
    for name, source in sources.items():
        module = types.ModuleType('baseline_' + name)
        exec(source, module.__dict__)
        baseline[name] = module
    # Showdown imports its sampler from nfl_simulation. Pin that dependency too,
    # so the before trial executes the pinned sampler rather than current code.
    baseline['showdown_simulation']._scenario_outcomes = baseline['nfl_simulation']._scenario_outcomes
    records = []
    for kind, fixture, optimizer, before, after in (
        ('classic', _fixture_players, MultiSportClassicOptimizer,
         baseline['nfl_simulation'].simulate_nfl_contest, classic.simulate_nfl_contest),
        ('showdown', _showdown_players, ShowdownOptimizer,
         baseline['showdown_simulation'].simulate_showdown, showdown.simulate_showdown),
    ):
        players = fixture()
        rows = optimizer(players).build_lineups(80)
        original = copy.deepcopy(players)
        expected = None
        trials = []
        options = dict(scenarios=2000, field_lineup_count=1000, seed=481516,
                       scenario_cache=False)
        if kind == 'showdown':
            options['precompiled_rosters'] = True
        for trial in range(3):
            modes = [('before', before), ('after', after)]
            if trial % 2:
                modes.reverse()
            for mode, simulate in modes:
                working_players = copy.deepcopy(players)
                working_rows = copy.deepcopy(rows)
                started = time.perf_counter()
                result = simulate(working_rows, working_players, **options)
                elapsed = time.perf_counter() - started
                report = {k: v for k, v in result['report'].items() if k != 'simulation_profile'}
                payload = dict(candidates=result_values(result), report=report)
                digest = hashlib.sha256(json.dumps(exact(payload), sort_keys=True,
                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()
                if expected is None:
                    expected = digest
                if digest != expected:
                    raise AssertionError('Exact metrics, hits, values or report changed')
                profile = result['report']['simulation_profile']
                if profile['reused_scenarios'] != 0 or profile['field_count'] != 1000:
                    raise AssertionError('Fresh field/sample contract changed')
                if report['scenarios'] != 2000:
                    raise AssertionError('Fresh audit sample incomplete')
                if working_players != original:
                    raise AssertionError('Player inputs changed')
                row = dict(trial=trial, mode=mode, seconds=elapsed, result_sha256=digest,
                           profile=result['report'].get('simulation_profile'))
                trials.append(row)
                print(json.dumps(dict(kind=kind, **row)), flush=True)
        records.append(dict(kind=kind, candidates=len(rows), trials=trials,
            median_seconds={mode: statistics.median(r['seconds'] for r in trials
                if r['mode'] == mode) for mode in ('before', 'after')}))
    if network_attempts:
        raise AssertionError('Unexpected network access')
    return dict(baseline=BASE, scenarios=2000, field=1000, seed=481516,
        source_sha256={name: hashlib.sha256((ROOT / (name + '.py')).read_bytes()).hexdigest()
                       for name in sources}, exact_results_equal=True, records=records,
        limitations='Three alternating-order local synthetic trials per format. Fresh audit-sized simulation only, including opponent generation; not a whole-build or real-slate speedup claim. No scenario replay. Observational profile timing excluded from exact parity hashes.')


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
