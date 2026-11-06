"""Controlled component-swap Deep benchmark; production code is never modified."""
from pathlib import Path
import argparse
import copy
from contextlib import ExitStack
import hashlib
import importlib
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = '157f11b2e3b88287d4b7e2e4f2d0133abfe0392b'
SIM = '08cd3d3bd729affe64db8eb9f829d58b20d0fa27'
SELECT = '8f8c24a9052ddd812b89d402fcc2555c398658b6'


def digest(value):
    from scripts.benchmark_scenario_replay import exact
    return hashlib.sha256(json.dumps(exact(value), sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def verify_profile(profile, warm):
    for phase in ('screening', 'validation', 'ranking_audit'):
        row = profile.get(phase) or {}
        count, reused = row.get('completed_scenarios'), row.get('reused_scenarios')
        if type(count) is not int or count <= 0 or type(reused) is not int:
            raise ValueError('Incomplete phase evidence: ' + phase)
        expected = count if warm and phase != 'ranking_audit' else 0
        if reused != expected:
            raise ValueError('Reuse contract failed: ' + phase)
        if phase == 'ranking_audit' and count != 2000:
            raise ValueError('Independent audit must finish 2,000 fresh scenarios')


def summarize(records, repeats):
    if type(repeats) is not int or not 1 <= repeats <= 5:
        raise ValueError('Unsupported repeat count')
    if len({r['result_digest'] for r in records}) != 1:
        raise ValueError('Exact combined parity is required')
    warm = [r for r in records if r['mode'] == 'warm_replay']
    keys = [(r['repeat'], r['engine']) for r in warm]
    expected = {(n, engine) for n in range(repeats) for engine in ('before', 'after')}
    if len(keys) != len(expected) or set(keys) != expected:
        raise ValueError('Each trial needs exactly one before and after warm build')
    result = {}
    for engine in ('before', 'after'):
        rows = [r for r in warm if r['engine'] == engine]
        result[engine] = dict(wall_seconds=statistics.median(r['wall_seconds'] for r in rows),
            stage_seconds={k: statistics.median(r['stages'][k] for r in rows)
                           for k in rows[0]['stages']},
            simulation_phase_seconds={phase: statistics.median(
                r['simulation_profile'][phase]['timings']['total_seconds'] for r in rows)
                for phase in ('screening', 'validation', 'ranking_audit')})
    return result


def load_components(*, integrated=False):
    modules, hashes = {}, {}
    for label, commit, names in (
        ('before', BASE, ('nfl_simulation', 'showdown_simulation', 'portfolio_rules')),
        ('after', SIM, ('nfl_simulation', 'showdown_simulation')),
        ('after', SELECT, ('portfolio_rules',)),
    ):
        for name in names:
            source = subprocess.check_output(['git', 'show', commit + ':' + name + '.py'], cwd=ROOT)
            module = types.ModuleType('controlled_' + label + '_' + name)
            exec(source.decode('utf-8'), module.__dict__)
            modules.setdefault(label, {})[name] = module
            hashes[label + ':' + name] = dict(commit=commit, sha256=hashlib.sha256(source).hexdigest())
    if integrated:
        names = ('nfl_simulation', 'showdown_simulation', 'portfolio_rules')
        status = subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--',
                                 *[name + '.py' for name in names]], cwd=ROOT)
        if status.returncode:
            raise ValueError('Integrated application source must be committed before measurement')
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip()
        modules['after'] = {name: importlib.import_module(name) for name in names}
        for name in names:
            hashes['after:' + name] = dict(commit=commit,
                sha256=hashlib.sha256((ROOT / (name + '.py')).read_bytes()).hexdigest(),
                source='native committed application')
    for group in modules.values():
        group['showdown_simulation']._scenario_outcomes = group['nfl_simulation']._scenario_outcomes
    return modules, hashes


def prepare_library(path, snapshot, *, candidates, seconds):
    from candidate_library import run_search
    if not 0 < seconds <= 120:
        raise ValueError('Benchmark preparation must be between zero and 120 seconds')
    return run_search(path, snapshot, seconds=seconds, candidate_limit=candidates,
                      batch_size=min(candidates, 1000))


def run_case(kind, root, components, *, candidates, shortlist, lineups, field, repeats,
             preparation_seconds=3):
    from build_snapshots import create_snapshot, fingerprint
    from candidate_library import roster_keys
    from test_nfl_logic import _fixture_players
    from test_showdown_performance import _showdown_players
    from main_window import LineupBuildWorker
    import main_window, showdown_simulation, portfolio_rules
    players = (_fixture_players if kind == 'classic' else _showdown_players)()
    frozen = fingerprint(players)
    library = root / (kind + '.dfslib')
    snapshot = create_snapshot(players, dict(sport='NFL', contest_kind=kind, salary_cap=50000,
        salary_strategy='Balanced Spend', requested_lineups=lineups), {})
    prepared_candidates = prepare_library(library, snapshot, candidates=candidates, seconds=preparation_seconds)
    library_hash = hashlib.sha256(library.read_bytes()).hexdigest()
    expected = None
    records = []

    def row(lu):
        return dict(signature=roster_keys(lu, kind), metrics=copy.deepcopy(lu.sim_metrics),
            top=sorted(lu.sim_top_hits), top_five=sorted(lu.sim_top_five_hits),
            wins=sorted(lu.sim_win_hits), values=dict(lu.sim_scenario_values))

    def without_timestamps(value):
        if isinstance(value, dict):
            return {k: without_timestamps(v) for k, v in value.items()
                    if k not in ('started_at', 'finished_at')}
        if isinstance(value, (list, tuple)):
            return [without_timestamps(v) for v in value]
        return value

    def build(engine, mode, repeat):
        nonlocal expected
        group = components[engine]
        simulate = (group['nfl_simulation'].simulate_nfl_contest if kind == 'classic'
                    else group['showdown_simulation'].simulate_showdown)
        select = group['portfolio_rules'].select_portfolio
        captures, results, errors, selections = [], [], [], []
        def observed_sim(*args, **kwargs):
            result = simulate(*args, **kwargs)
            report = {k: copy.deepcopy(v) for k, v in result['report'].items()
                      if k not in ('simulation_profile', 'scenario_cache')}
            captures.append(dict(rows=[row(lu) for lu in result['lineups']], report=report))
            return result
        def observed_select(*args, **kwargs):
            selections.append(True)
            return select(*args, **kwargs)
        cache = root / (kind + '-' + engine + '-cache')
        worker = LineupBuildWorker(copy.deepcopy(players), kind=kind, num_lineups=lineups,
            salary_cap=50000, salary_strategy='Balanced Spend', sim_enabled=True,
            sim_scenarios=2500, compute_mode='Deep', deep_time_limit_seconds=1200,
            deep_options=dict(candidates=candidates, shortlist=shortlist, field=field, screening=250),
            candidate_library=str(library), scenario_cache=True, compute_telemetry=False)
        worker.finished.connect(results.append)
        worker.error.connect(errors.append)
        started = time.perf_counter()
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {'DFS_OPTIMIZER_DATA_DIR': str(cache)}))
            stack.enter_context(patch.object(main_window, 'simulate_nfl_contest', observed_sim))
            stack.enter_context(patch.object(showdown_simulation, 'simulate_showdown', observed_sim))
            for module in (main_window, showdown_simulation, portfolio_rules):
                stack.enter_context(patch.object(module, 'select_portfolio', observed_select))
            worker.run()
        elapsed = time.perf_counter() - started
        if errors or len(results) != 1:
            raise RuntimeError(str(errors or 'No completed build'))
        result = results[0]
        if result.get('cancelled') or len(result['lineups']) != lineups or not selections:
            raise RuntimeError('Incomplete portfolio or selector not exercised')
        profile = result['timing_report'].get('simulation_profile', {})
        verify_profile(profile, mode == 'warm_replay')
        portfolio = copy.deepcopy(result['portfolio_report'])
        portfolio.pop('refinement_seconds', None)
        portfolio.pop('text', None)
        audit = copy.deepcopy(result['sim_report']['deep_build']['ranking_audit'])
        for key in ('seconds', 'available_seconds'):
            audit.pop(key, None)
        if audit.get('status') != 'completed':
            raise RuntimeError('Independent audit did not complete')
        payload = without_timestamps(dict(stages=captures, selected=[row(lu) for lu in result['lineups']],
                                         portfolio=portfolio, audit=audit))
        value = digest(payload)
        if expected is None:
            expected = value
        if value != expected:
            raise AssertionError('Stage scores, ordered portfolio or structured evidence changed')
        if fingerprint(players) != frozen or hashlib.sha256(library.read_bytes()).hexdigest() != library_hash:
            raise AssertionError('Frozen input or candidate library changed')
        timing = result['timing_report']
        record = dict(engine=engine, mode=mode, repeat=repeat, wall_seconds=elapsed, result_digest=value,
            stages={k: timing[k] for k in ('generation_seconds', 'simulation_seconds', 'selection_seconds', 'total_seconds')},
            simulation_profile=profile, audit=audit, selector_calls=len(selections))
        records.append(record)
        print(json.dumps(dict(kind=kind, **record)), flush=True)

    for engine in ('before', 'after'):
        build(engine, 'cold_record', -1)
    for repeat in range(repeats):
        order = ('before', 'after') if repeat % 2 == 0 else ('after', 'before')
        for engine in order:
            build(engine, 'warm_replay', repeat)
    return dict(kind=kind, prepared_candidates=prepared_candidates, preparation_seconds=preparation_seconds,
        requested_candidates=candidates, requested_shortlist=shortlist,
        requested_lineups=lineups, requested_field=field, requested_scenarios=2500,
        frozen_library_sha256=library_hash, summary=summarize(records, repeats), records=records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', choices=('classic', 'showdown', 'both'), default='both')
    for name, default in (('candidates', 1000), ('shortlist', 200), ('lineups', 20), ('field', 1000), ('repeats', 3)):
        parser.add_argument('--' + name, type=int, default=default)
    parser.add_argument('--preparation-seconds', type=float, default=3)
    parser.add_argument('--integrated', action='store_true', help='Measure native combined application functions as the after engine')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not (20 <= args.candidates <= 2000 and 20 <= args.shortlist <= 2000
            and 1 <= args.lineups <= min(150, args.candidates, args.shortlist)
            and 1 <= args.field <= 10000 and 1 <= args.repeats <= 5
            and 0 < args.preparation_seconds <= 120):
        raise ValueError('Benchmark bounds exceeded')
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError('Output exists')
    from test_environment import install, network_attempts
    install()
    from PyQt5 import QtCore
    app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
    components, sources = load_components(integrated=args.integrated)
    with tempfile.TemporaryDirectory(prefix='dfs-combined-warm-') as raw:
        results = [run_case(kind, Path(raw), components, candidates=args.candidates,
            shortlist=args.shortlist, lineups=args.lineups, field=args.field, repeats=args.repeats,
            preparation_seconds=args.preparation_seconds)
            for kind in (('classic', 'showdown') if args.kind == 'both' else (args.kind,))]
    if network_attempts:
        raise AssertionError('Unexpected network access')
    receipt = dict(sources=sources, results=results, exact_combined_parity=True, integrated_application=args.integrated,
        limitations=('Native combined application functions for the after engine. ' if args.integrated else 'Pinned component swap, not a merged application integration build. ') + 'Controlled benchmark in one host app revision. Pinned original simulator/sampler/selector versus either PRs 188-190 components or the native combined application (see integrated_application and source hashes). One shared frozen candidate bank per format; separate disposable caches per engine, one cold preparation then alternating paired warm trials. Capture overhead is included equally. Synthetic local timings are not production-slate latency. Nested phase times are included in simulation time, not additive stages. Audit remains 2,000 fresh scenarios each build.')
    output.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
