"""Frozen synthetic, offline worker benchmark; no favorable-overhead assertion.

Each observation uses a fresh child, fixed hash/RNG seeds and disposable data.
Measures real generation/SIM/selection; records exact candidate and score hashes.
Run: python scripts/benchmark_compute_ledger.py --output <json> --repeats 3
"""
import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def peak_process_memory():
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ('peak', 'current', 'a', 'b', 'c', 'd', 'e', 'f')]
        kernel = ctypes.WinDLL('kernel32'); kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL('psapi')
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        value = Counters(); value.cb = ctypes.sizeof(value)
        if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(value), value.cb):
            return int(value.peak)
    else:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)
    return None


def observation(kind, mode, enabled):
    from test_environment import install, network_attempts
    install()
    from contextlib import ExitStack
    from unittest.mock import patch
    import main_window
    import showdown_simulation
    from build_snapshots import fingerprint
    from candidate_library import roster_keys
    from optimizers import ShowdownOptimizer, MultiSportClassicOptimizer
    from test_nfl_logic import _fixture_players
    from test_showdown_performance import _showdown_players
    from test_co01_parity import stable
    import compute_ledger as cl

    candidates, stages, results, errors = [], [], [], []
    depth = [0]
    def capture_generator(real):
        def call(*args, **kwargs):
            depth[0] += 1
            try:
                result = real(*args, **kwargs)
            finally:
                depth[0] -= 1
            if depth[0] == 0:
                rows = result[0] if isinstance(result, tuple) else result
                candidates.append([roster_keys(row, kind) for row in rows])
            return result
        return call
    def capture_sim(real):
        def call(rows, *args, **kwargs):
            result = real(rows, *args, **kwargs)
            stages.append(dict(inputs=[roster_keys(row, kind) for row in rows],
                scored=[dict(roster=roster_keys(row, kind), scores=stable(row.sim_metrics)) for row in result['lineups']]))
            return result
        return call
    players = _fixture_players() if kind == 'classic' else _showdown_players()
    original = fingerprint(players)
    worker = main_window.LineupBuildWorker(players, kind=kind, num_lineups=4, salary_cap=50000,
        salary_strategy='Balanced Spend', sim_enabled=True, sim_scenarios=250,
        compute_mode=mode, compute_telemetry=enabled, deep_time_limit_seconds=300,
        deep_options=dict(candidates=40, shortlist=20, field=80, screening=250))
    worker.finished.connect(results.append); worker.error.connect(errors.append)
    start = time.perf_counter(); cpu = time.process_time()
    with ExitStack() as stack:
        for owner, name in ((ShowdownOptimizer, 'build_lineups'), (ShowdownOptimizer, '_build_lineups_fast'),
            (MultiSportClassicOptimizer, 'build_lineups'), (main_window, 'generate_nfl_field_lineups'),
            (main_window, 'generate_nfl_scenario_lineups')):
            stack.enter_context(patch.object(owner, name, capture_generator(getattr(owner, name))))
        for owner, name in ((main_window, 'simulate_nfl_contest'), (showdown_simulation, 'simulate_showdown')):
            stack.enter_context(patch.object(owner, name, capture_sim(getattr(owner, name))))
        worker.run()
    elapsed = time.perf_counter() - start; cpu_elapsed = time.process_time() - cpu
    assert not errors, errors
    assert len(results) == 1 and len(results[0]['lineups']) == 4
    assert fingerprint(players) == original
    assert not network_attempts, network_attempts
    result = results[0]
    summary = result.get('sim_report', {}).get('compute_ledger')
    if enabled:
        assert summary, 'Telemetry silently failed'
        receipt = cl.read_receipt(cl.folder() / ('compute-' + summary['run_id'] + '.json'))
        assert receipt['status'] == 'completed' and receipt['final_lineup_count'] == 4
        assert receipt['batch_count'] > 0
    return dict(kind=kind, mode=mode, enabled=enabled, wall_seconds=elapsed, cpu_seconds=cpu_elapsed,
        peak_process_memory_bytes=peak_process_memory(), candidate_signature=fingerprint(candidates),
        scored_signature=fingerprint(stages), final_signature=cl.portfolio_hash(result['lineups'], kind),
        final_count=len(result['lineups']), candidate_count=result['candidate_count'], network_attempts=network_attempts,
        ledger_summary=summary)


def benchmark(output, repeats):
    measurements = []
    for kind in ('classic', 'showdown'):
        for mode in ('Fast', 'Deep'):
            for repeat in range(repeats):
                # Alternate order to reduce consistent warm-system bias.
                for enabled in ((False, True) if repeat % 2 == 0 else (True, False)):
                    run = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--child',
                        kind, mode, str(int(enabled))], env=dict(os.environ, PYTHONHASHSEED='0'),
                        capture_output=True, text=True, timeout=240)
                    if run.returncode:
                        raise RuntimeError(run.stdout + run.stderr)
                    value = json.loads(run.stdout.split('DC02_RESULT=')[-1])
                    measurements.append(value)
                    print(f"{kind} {mode} enabled={enabled}: {value['wall_seconds']:.3f}s", flush=True)
    comparisons = []
    for kind in ('classic', 'showdown'):
        for mode in ('Fast', 'Deep'):
            rows = [r for r in measurements if r['kind'] == kind and r['mode'] == mode]
            for key in ('candidate_signature', 'scored_signature', 'final_signature', 'candidate_count', 'final_count'):
                assert len({r[key] for r in rows}) == 1, (kind, mode, key, rows)
            comparison = dict(kind=kind, mode=mode, exact_parity=True)
            for key in ('wall_seconds', 'cpu_seconds', 'peak_process_memory_bytes'):
                disabled = statistics.median(r[key] for r in rows if not r['enabled'])
                enabled = statistics.median(r[key] for r in rows if r['enabled'])
                comparison[key] = dict(disabled=disabled, enabled=enabled,
                    difference=enabled-disabled, difference_pct=100*(enabled-disabled)/disabled)
            comparisons.append(comparison)
    Path(output).write_text(json.dumps(dict(schema=1, repeats=repeats, measurements=measurements,
        comparisons=comparisons), indent=2), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--child', nargs=3)
    parser.add_argument('--output')
    parser.add_argument('--repeats', type=int, default=3)
    options = parser.parse_args()
    if options.child:
        kind, mode, enabled = options.child
        print('DC02_RESULT=' + json.dumps(observation(kind, mode, bool(int(enabled)))))
    else:
        if not options.output or not 1 <= options.repeats <= 10:
            parser.error('--output and 1..10 repeats required')
        benchmark(options.output, options.repeats)
