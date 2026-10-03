"""DC-02A: bounded, observational compute receipts. Never a cache authority.

Hooks consume existing results and cancellation checks; they never request work,
draw randomness, inspect historical results, or extend a deadline. Candidate
signatures exist only in memory. On disk there are aggregates and hashes only.
"""
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import lru_cache, wraps
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import uuid

SCHEMA = 1
MAX_RUNS = 250
MAX_BATCHES = 256
MAX_CHECKPOINTS = 64
MAX_IDENTITIES = 200000
STYLES = {'Strategic', 'Balanced', 'Contrarian', 'Chalk', 'Randomized'}
SOURCES = {'optimizer', 'exposure_recovery', 'captain_coverage', 'field_shaped', 'scenario_built'}
_current = ContextVar('compute_ledger', default=None)
_source = ContextVar('compute_source', default=None)
_batch = ContextVar('compute_batch', default=None)
_phases = ContextVar('compute_phases', default=())


def safe(fn, *args, **kwargs):
    """Telemetry capability or storage failure must not fail the build."""
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None


def digest(value):
    from build_snapshots import fingerprint
    return fingerprint(value)


@lru_cache(maxsize=1)
def app_code_id():
    from candidate_library import code_id
    return code_id()


def now():
    return datetime.now(timezone.utc).isoformat()


def cpu_time():
    # Thread CPU excludes unrelated UI/background work. Missing means unknown.
    return safe(time.thread_time)


def memory_bytes():
    """Current process resident bytes, sampled at boundaries (not a true peak)."""
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
                    'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                    'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        counters = Counters(); counters.cb = ctypes.sizeof(counters)
        if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return int(counters.WorkingSetSize)
        return None
    statm = Path('/proc/self/statm')
    if statm.exists():
        return int(statm.read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE')
    return None


def folder():
    override = os.environ.get('DFS_OPTIMIZER_DATA_DIR')
    base = Path(override) if override else Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'DFS Optimizer'
    return base / 'compute-ledger'


def atomic_write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.compute-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, sort_keys=True, separators=(',', ':'), allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def read_receipt(path):
    if Path(path).stat().st_size > 2 * 1024 * 1024:
        raise ValueError('Compute receipt exceeds size limit')
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if (value.get('schema_version') != SCHEMA or value.get('atomic_complete') is not True
            or value.get('resumable') is not False or not value.get('run_id')):
        raise ValueError('Incomplete or unsupported compute receipt')
    return value


def retain(root, limit=MAX_RUNS):
    """Delete only recognized ledger receipts in this dedicated directory."""
    receipts = []
    for path in Path(root).glob('compute-*.json'):
        if not re.fullmatch(r'compute-[0-9a-f]{32}\.json', path.name) or path.is_symlink():
            continue
        value = safe(read_receipt, path)
        if value and value['run_id'] == path.stem[8:]:
            receipts.append((value.get('started_at', ''), path.name, path))
    for _, _, path in sorted(receipts)[:-limit] if limit else sorted(receipts):
        path.unlink()


def compatibility_ids(players, recipe, rules, code, *, calibration=None, contest=None):
    """Describe validity separately from scores, without authorizing any reuse.

    Existing snapshot fingerprints and exact library/cache checks remain the
    authority. These are diagnostic identities, not substitutes for those checks.
    """
    from candidate_library import slate_id
    legality = ('ID', 'CptID', 'Name', 'Team', 'Opponent', 'GameInfo', 'GameKey',
        'Position', 'Roster Position', 'Pos', 'FlexSalary', 'CptSalary', 'Salary',
        'LockFlex', 'LockCpt', 'FadeFlex', 'FadeCpt', 'MinPct', 'MaxPct', 'MaxCptPct',
        'NFLQBEligible', 'NFLDepthOrder', 'NFLActive', 'NFLAvailability', 'NFLRosterStatus',
        'ProjectionSource', 'Status', 'InjuryStatus')
    kind = recipe.get('contest_kind', 'classic')
    candidate = dict(slate=slate_id(players, kind), code=code, rules=rules,
        players=[{key: p[key] for key in legality if key in p} for p in players],
        recipe={key: recipe.get(key) for key in ('sport', 'contest_kind', 'salary_cap', 'salary_strategy',
            'requested_lineups', 'retained_signatures', 'build_style')})
    simulation = dict(players=players, recipe=recipe, rules=rules, code=code,
        calibration=calibration or {}, contest=contest or {})
    return digest(candidate), digest(simulation)


def signatures(rows, kind):
    from candidate_library import roster_keys
    return [tuple(roster_keys(row, kind)) for row in rows]


def portfolio_hash(rows, kind):
    # Order is intentional: a reordered ranked portfolio is a different receipt.
    return digest(signatures(rows, kind))


def portfolio_overlap(previous, current):
    a, b = set(previous), set(current)
    return len(a & b) / len(a | b) if a or b else 1.0


class Ledger:
    def __init__(self, *, players, recipe, rules, input_id='', purpose='normal',
                 calibration=None, contest=None, root=None, clock=None):
        self.clock = clock or time.perf_counter
        self.start = self.clock(); self.cpu_start = cpu_time()
        self.root = Path(root) if root is not None else folder()
        code = app_code_id()
        candidate, simulation = compatibility_ids(players, recipe, rules, code,
            calibration=calibration, contest=contest)
        from compute_settings import normalize_deep_settings
        self.data = dict(schema_version=SCHEMA, run_id=uuid.uuid4().hex, started_at=now(),
            finished_at=None, input_id=input_id or simulation, code_id=code,
            candidate_compatibility_id=candidate, simulation_compatibility_id=simulation,
            simulation_cache_ids=[], purpose=purpose if purpose in ('normal', 'saved_repair', 'preparation', 'historical_reconciliation') else 'unknown',
            sport=recipe.get('sport') if recipe.get('sport') in ('NFL', 'MLB', 'NBA', 'WNBA', 'NHL') else 'unknown',
            format=recipe.get('contest_kind') if recipe.get('contest_kind') in ('classic', 'showdown') else 'unknown',
            mode=recipe.get('compute_mode') if recipe.get('compute_mode') in ('Fast', 'Deep') else 'unknown',
            build_style=recipe.get('build_style') if recipe.get('build_style') in STYLES else 'unknown',
            salary_strategy=recipe.get('salary_strategy') if recipe.get('salary_strategy') in (
                'Near Cap', 'Max Spend', 'Balanced Spend', 'Flexible', 'Leverage') else 'other',
            requested_lineups=recipe.get('requested_lineups'),
            requested_budgets=dict(deep_time_limit_seconds=recipe.get('deep_time_limit_seconds'),
                deep_compute=normalize_deep_settings(recipe.get('deep_compute')),
                nfl_sim_scenarios=recipe.get('nfl_sim_scenarios')),
            status='running', cancelled=False, wall_seconds=None, cpu_seconds=None,
            cpu_basis='worker_thread', start_observed_memory_bytes=None,
            end_observed_memory_bytes=None, peak_observed_memory_bytes=None, memory_samples=0,
            final_candidate_count=None, final_lineup_count=None, phases={}, batches=[],
            batch_count=0, completed_batch_count=0, omitted_batch_details=0, sources={}, completed_source_state={},
            reuse=dict(loaded=0, accepted=0, rejected=0, generated=0, deduplicated=0,
                       resumed_batches=0, reused_scenarios=0, library_status='not_requested'),
            checkpoints=[], omitted_checkpoints=0, atomic_complete=True, resumable=False)
        self.seen = set(); self.athletes = set(); self.identity_limit_reached = False
        self.previous_portfolio = None; self.next_checkpoint = 30; self.last_memory = -float('inf')
        self.completed_digest = hashlib.sha256()
        self.retention_checked = False
        self.sample_memory()
        self.data['start_observed_memory_bytes'] = self.data['peak_observed_memory_bytes']

    def sample_memory(self, force=False):
        elapsed = self.clock() - self.start
        if not force and elapsed - self.last_memory < 5:
            return
        self.last_memory = elapsed
        value = safe(memory_bytes)
        if value is not None:
            self.data['memory_samples'] += 1
            self.data['end_observed_memory_bytes'] = value
            self.data['peak_observed_memory_bytes'] = max(value, self.data['peak_observed_memory_bytes'] or 0)

    def phase(self, name, wall, cpu, incoming=None, outgoing=None, scenarios=None, complete=True, started_at=None):
        item = self.data['phases'].setdefault(name, dict(calls=0, completed_calls=0,
            started_at=started_at, completed_at=None, status='completed',
            unique_input_count=None, unique_output_count=None,
            wall_seconds=0.0, cpu_seconds=0.0, input_count=0, output_count=0, scenarios=0))
        item['completed_at'] = now()
        if not complete:
            item['status'] = 'incomplete'
        item['calls'] += 1; item['completed_calls'] += int(complete)
        for key, value in dict(wall_seconds=wall, cpu_seconds=cpu, input_count=incoming,
                               output_count=outgoing, scenarios=scenarios).items():
            item[key] = None if value is None or item[key] is None else item[key] + value
        self.boundary(name)

    def batch(self, rows, *, source, style=None, seed=None, requested=None, wall=0, cpu=None,
              attempted=None, legal=None, interrupted=False, attempt_basis=None, started_at=None):
        source = source if source in SOURCES else 'other'
        style = style if style in STYLES else None
        seed = seed if isinstance(seed, int) else None
        keys = signatures(rows, self.data['format'])
        new = duplicate = 0
        athlete_new = 0
        for key in keys:
            if key in self.seen:
                duplicate += 1
            elif len(self.seen) < MAX_IDENTITIES:
                self.seen.add(key); new += 1
            else:
                self.identity_limit_reached = True
            athletes = tuple(sorted(key))
            if athletes not in self.athletes and len(self.athletes) < MAX_IDENTITIES:
                self.athletes.add(athletes); athlete_new += 1
        self.data['batch_count'] += 1
        item = dict(batch_id=self.data['batch_count'], source=source, style=style, seed=seed,
            requested=requested, attempted=attempted, attempt_basis=attempt_basis,
            legal_trials=legal, returned=len(rows), new=None if self.identity_limit_reached else new,
            duplicate=None if self.identity_limit_reached else duplicate,
            rejected_trials=(attempted - legal if attempted is not None and legal is not None else None),
            athlete_sets_new=(athlete_new if self.data['format'] == 'showdown' and not self.identity_limit_reached else None),
            captain_assignments_new=(new if self.data['format'] == 'showdown' and not self.identity_limit_reached else None),
            started_at=started_at, completed_at=now(), wall_seconds=wall, cpu_seconds=cpu,
            stop_reason='cancel_or_deadline_observed' if interrupted else 'returned', completed=not interrupted)
        if not interrupted:
            self.data['completed_batch_count'] += 1
            self.completed_digest.update(str(item['batch_id']).encode('ascii') + b'\n')
            self.data['completed_source_state'][source] = dict(batch_id=item['batch_id'], style=style, seed=seed)
        if len(self.data['batches']) < MAX_BATCHES:
            self.data['batches'].append(item)
        else:
            self.data['omitted_batch_details'] += 1
        total = self.data['sources'].setdefault(source, dict(batches=0, completed_batches=0,
            requested=0, attempted=0, returned=0, new=0, duplicate=0, legal_trials=0,
            rejected_trials=0, athlete_sets_new=0, captain_assignments_new=0, wall_seconds=0.0,
            cpu_seconds=0.0, archetypes={}))
        total['batches'] += 1; total['completed_batches'] += int(not interrupted)
        for key in ('requested', 'attempted', 'returned', 'new', 'duplicate', 'legal_trials',
                    'rejected_trials', 'athlete_sets_new', 'captain_assignments_new', 'wall_seconds', 'cpu_seconds'):
            value = item[key]
            total[key] = None if value is None or total[key] is None else total[key] + value
        for row in rows:
            # Only known generator archetypes, never arbitrary names/paths.
            value = getattr(row, 'candidate_archetype', None)
            if value in ('Ceiling', 'Balanced', 'Leverage', 'Low-Dup'):
                total['archetypes'][value] = total['archetypes'].get(value, 0) + 1
        self.data['reuse']['generated'] += len(rows)
        self.data['reuse']['deduplicated'] = None if self.identity_limit_reached else len(self.seen)
        self.phase('generation', wall, cpu, requested, len(rows), complete=not interrupted, started_at=started_at)

    def boundary(self, phase, portfolio=None, force=False):
        self.sample_memory()
        elapsed = max(0, self.clock() - self.start)
        if not force and elapsed < self.next_checkpoint:
            return
        item = dict(elapsed_seconds=elapsed, phase=phase, timestamp=now(),
            completed_batch_count=self.data['completed_batch_count'],
            completed_batch_digest=self.completed_digest.hexdigest(),
            completed_source_state={k: dict(v) for k, v in self.data['completed_source_state'].items()},
            candidate_count=None if self.identity_limit_reached else len(self.seen),
            duplicate_returned=sum(v.get('duplicate') or 0 for v in self.data['sources'].values()) if not self.identity_limit_reached else None,
            screened_count=(self.data['phases'].get('shortlisting') or {}).get('output_count'),
            portfolio_hash=None, portfolio_count=None, overlap_previous=None)
        if portfolio is not None:
            keys = signatures(portfolio, self.data['format'])
            item.update(portfolio_hash=digest(keys), portfolio_count=len(keys),
                overlap_previous=(portfolio_overlap(self.previous_portfolio, keys)
                                  if self.previous_portfolio is not None else None))
            self.previous_portfolio = keys
        if len(self.data['checkpoints']) >= MAX_CHECKPOINTS:
            # Preserve the first checkpoint and the most recent bounded history.
            self.data['checkpoints'].pop(1); self.data['omitted_checkpoints'] += 1
        self.data['checkpoints'].append(item)
        self.next_checkpoint = next((n for n in (30, 60, 120, 300) if n > elapsed),
                                    (int(elapsed // 300) + 1) * 300)
        self.persist()

    def persist(self):
        atomic_write(self.root / ('compute-' + self.data['run_id'] + '.json'), self.data)
        if not self.retention_checked:
            safe(retain, self.root)
            self.retention_checked = True

    def finish(self, payload=None, status=None):
        if self.data['status'] != 'running':
            return
        payload = payload or {}
        self.data['cancelled'] = bool(payload.get('cancelled'))
        self.data['status'] = status or ('cancelled' if self.data['cancelled'] else 'completed')
        self.data['stop_reason'] = ('cancelled' if self.data['cancelled'] else 'error' if status == 'failed' else 'pipeline_returned')
        self.data['finished_at'] = now()
        self.data['final_lineup_count'] = len(payload['lineups']) if 'lineups' in payload else None
        self.data['final_candidate_count'] = payload.get('candidate_count')
        self.data['effective_mode'] = (payload.get('timing_report') or {}).get('compute_mode', self.data['mode'])
        self.sample_memory(force=True)
        self.data['wall_seconds'] = max(0, self.clock() - self.start)
        end_cpu = cpu_time()
        self.data['cpu_seconds'] = end_cpu - self.cpu_start if end_cpu is not None and self.cpu_start is not None else None
        self.boundary('finished', payload.get('lineups'), force=True)
        safe(retain, self.root)

    def summary(self):
        result = {key: self.data[key] for key in ('schema_version', 'run_id', 'purpose', 'status',
            'wall_seconds', 'cpu_seconds', 'cpu_basis', 'peak_observed_memory_bytes', 'memory_samples',
            'batch_count', 'completed_batch_count', 'final_candidate_count', 'final_lineup_count', 'reuse')}
        result['duplicate_returned'] = None if self.identity_limit_reached else sum(
            v.get('duplicate') or 0 for v in self.data['sources'].values())
        return result


def elapsed_cpu(start):
    end = cpu_time()
    return end - start if end is not None and start is not None else None


def recipe_for(worker):
    return dict(sport=worker.sport, contest_kind=worker.kind, salary_cap=worker.salary_cap,
        salary_strategy=worker.salary_strategy, requested_lineups=worker.num_lineups,
        build_style=worker.build_style, compute_mode=worker.compute_mode,
        deep_time_limit_seconds=worker.deep_time_limit_seconds, deep_compute=worker.deep_options,
        nfl_sim_scenarios=worker.sim_scenarios, ownership_mode=worker.own_mode,
        ownership_weight=worker.own_weight, field_preset=worker.field_preset,
        retained_signatures=signatures(worker.retained_lineups, worker.kind))


def instrument_worker(fn):
    @wraps(fn)
    def wrapped(worker, *args, **kwargs):
        ledger = (safe(Ledger, players=worker.players, recipe=recipe_for(worker), rules=worker.portfolio_rules,
            input_id=getattr(worker, 'build_input_id', ''),
            purpose=getattr(worker, 'build_purpose', 'saved_repair' if worker.repair_source == 'saved' else 'normal'),
            calibration=worker.field_calibration, contest=worker.contest_profile)
            if worker.compute_telemetry else None)
        token = _current.set(ledger)
        try:
            return fn(worker, *args, **kwargs)
        finally:
            if ledger and ledger.data['status'] == 'running':
                # The existing worker owns error/cancellation delivery; no callbacks are added.
                event = worker._cancel_event
                cancelled = isinstance(event, threading.Event) and event.is_set()
                safe(ledger.finish, dict(cancelled=cancelled), status='cancelled' if cancelled else 'failed')
            _current.reset(token)
    return wrapped


def finish_worker(payload):
    ledger = _current.get()
    if ledger:
        safe(ledger.finish, payload)
        summary = safe(ledger.summary)
        if summary:
            payload.setdefault('sim_report', {})['compute_ledger'] = summary


def source(name):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            token = _source.set(name)
            try:
                return fn(*args, **kwargs)
            finally:
                _source.reset(token)
        return wrapped
    return decorate


def trial_counts(attempted, legal, basis='roster_construction'):
    item = _batch.get()
    if item is not None:
        item.update(attempted=attempted, legal=legal, attempt_basis=basis)


def observe_cancel(kwargs, observation):
    # Wrap only checks the algorithm already makes. Never call the callback here.
    result = dict(kwargs)
    for key in ('cancel_callback', 'selection_cancel_callback', 'refinement_stop_callback'):
        callback = kwargs.get(key)
        if callback:
            def check(callback=callback):
                value = callback()
                if value:
                    observation['interrupted'] = True
                return value
            result[key] = check
    return result


def generator(name='optimizer', method=True, candidate_only=False):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            ledger = _current.get()
            if not ledger or _batch.get() is not None or (candidate_only and not kwargs.get('candidate_mode')):
                return fn(*args, **kwargs)
            observation = {}; token = _batch.set(observation)
            start = time.perf_counter(); cpu = cpu_time(); stamp = now()
            try:
                result = fn(*args, **observe_cancel(kwargs, observation))
            finally:
                _batch.reset(token)
            wall = time.perf_counter() - start
            rows = result[0] if isinstance(result, tuple) else result
            owner = args[0] if method else None
            safe(ledger.batch, rows, source=_source.get() or name,
                style=getattr(owner, 'build_style', None), seed=getattr(owner, '_compute_seed', kwargs.get('seed')),
                requested=kwargs.get('num_lineups', kwargs.get('count', args[1] if len(args) > 1 else (10 if method else None))),
                wall=wall, cpu=elapsed_cpu(cpu), started_at=stamp, **observation)
            return result
        return wrapped
    return decorate


def phase(name, simulation=False, shortlist=False, selection=False, output_size=None):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            ledger = _current.get()
            actual = ({73129: 'screening_sim', 90210: 'validation_sim', 481516: 'ranking_audit_sim'}.get(
                kwargs.get('seed'), name) if simulation else name)
            if not ledger or actual in _phases.get():
                return fn(*args, **kwargs)
            token = _phases.set(_phases.get() + (actual,))
            start = time.perf_counter(); cpu = cpu_time(); observation = {}; stamp = now()
            options = observe_cancel(kwargs, observation)
            progress = options.get('progress_callback')
            if simulation and progress:
                def report(done, total, text):
                    # SIM already reports at coarse scenario boundaries. Do not
                    # add a timer, extra scenario, or cancellation check.
                    safe(ledger.boundary, actual)
                    return progress(done, total, text)
                options['progress_callback'] = report
            try:
                result = fn(*args, **options)
            except Exception:
                safe(ledger.phase, actual, time.perf_counter()-start, elapsed_cpu(cpu),
                     len(args[0]) if args and isinstance(args[0], (list, tuple, dict)) else None,
                     None, None, False, stamp)
                raise
            finally:
                _phases.reset(token)
            rows = result.get('lineups') if isinstance(result, dict) else result[0] if shortlist else result
            incoming = len(args[0]) if args and isinstance(args[0], (list, tuple, dict)) else None
            outgoing = safe(output_size, result) if output_size else len(rows) if hasattr(rows, '__len__') else None
            scenarios = result.get('report', {}).get('scenarios') if simulation else None
            safe(ledger.phase, actual, time.perf_counter()-start, elapsed_cpu(cpu), incoming, outgoing,
                 scenarios, not observation.get('interrupted', False), stamp)
            if rows is not None and (simulation or shortlist or selection):
                safe(observe_stage_sources, rows, actual)
            if selection and rows is not None and not kwargs.get('feasibility_only'):
                safe(ledger.boundary, actual, rows, force=True)
            return result
        return wrapped
    return decorate


def library_loaded(report, rows):
    ledger = _current.get()
    if ledger:
        ledger.data['reuse'].update(loaded=report['accepted'] + report['rejected'],
            accepted=report['accepted'], rejected=report['rejected'], library_status='accepted')
        ledger.data['candidate_library_input_id'] = report['input_id']
        # Loaded candidates participate in subsequent generated-vs-reused dedup counts.
        ledger.seen.update(signatures(rows, ledger.data['format']))
        ledger.data['reuse']['deduplicated'] = len(ledger.seen)


def instrument_library(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        ledger = _current.get()
        try:
            result = fn(*args, **kwargs)
        except Exception:
            if ledger:
                ledger.data['reuse'].update(library_status='rejected', loaded=None, accepted=0, rejected=None)
            raise
        safe(library_loaded, result[1], result[0])
        return result
    return wrapped


def cache_observation(key, report):
    ledger = _current.get()
    if ledger:
        if key and key not in ledger.data['simulation_cache_ids'] and len(ledger.data['simulation_cache_ids']) < 16:
            ledger.data['simulation_cache_ids'].append(key)
        ledger.data['reuse']['reused_scenarios'] += report.get('reused_scenarios', 0)


def observe_dedup(incoming, outgoing, wall=None):
    ledger = _current.get()
    if ledger:
        ledger.phase('deduplication', wall, None, incoming, outgoing)
        ledger.data['phases']['deduplication']['unique_output_count'] = outgoing


def candidate_budget(value):
    ledger = _current.get()
    if ledger:
        ledger.data['effective_candidate_budget'] = value


def instrument_reconciliation(fn):
    """An explicit reconciliation operation gets its own, separately typed run.

    Read-only qualified_contests/derive_contests calls create no files. Their
    phase decorators observe only when a reconciliation context already exists.
    """
    @wraps(fn)
    def wrapped(*args, **kwargs):
        # UI jobs pin their telemetry destination alongside the database when
        # created; later profile/environment changes cannot redirect the write.
        options = dict(kwargs)
        ledger_root = options.pop('ledger_root', None)
        ledger = safe(Ledger, players=[], recipe={}, rules={}, purpose='historical_reconciliation', root=ledger_root)
        if ledger:
            ledger.data.update(input_id=None, candidate_compatibility_id=None, simulation_compatibility_id=None)
        token = _current.set(ledger)
        try:
            result = fn(*args, **options)
            if ledger:
                from historical_identity import coverage
                safe(lambda: ledger.data.update(historical_identity=coverage(result),
                     input_id=digest([item.evidence for item in result])))
                safe(ledger.finish)
            return result
        except Exception as exc:
            from analysis_imports import ImportCancelled
            if ledger:
                cancelled = isinstance(exc, ImportCancelled)
                safe(ledger.finish, dict(cancelled=cancelled), status='cancelled' if cancelled else 'failed')
            raise
        finally:
            _current.reset(token)
    return wrapped


def observe_stage_sources(rows, stage):
    """Bounded survivor counts from existing provenance; no score comparisons."""
    ledger = _current.get()
    if ledger:
        counts = {}
        for row in rows:
            name = getattr(row, 'candidate_source', '')
            if name in ('optimizer', 'field_shaped', 'scenario_built'):
                counts[name] = counts.get(name, 0) + 1
        ledger.data.setdefault('stage_sources', {})[stage] = counts


def instrument_search(fn):
    @wraps(fn)
    def wrapped(path, snapshot, *args, **kwargs):
        inputs = snapshot.get('inputs', {})
        recipe = dict(inputs.get('recipe', {}), compute_mode='Deep',
                      deep_time_limit_seconds=kwargs.get('seconds', 3600))
        ledger = safe(Ledger, players=inputs.get('players', []), recipe=recipe,
                      rules=inputs.get('rules', {}), input_id=snapshot.get('input_id', ''), purpose='preparation')
        token = _current.set(ledger)
        observation = {}
        options = dict(kwargs)
        callback = options.get('cancelled', lambda: False)
        def cancelled():
            value = callback()
            if value:
                observation['cancelled'] = True
            return value
        options['cancelled'] = cancelled
        try:
            result = fn(path, snapshot, *args, **options)
            if ledger:
                safe(ledger.finish, dict(candidate_count=result, cancelled=observation.get('cancelled', False)))
            return result
        finally:
            if ledger and ledger.data['status'] == 'running':
                safe(ledger.finish, status='failed')
            _current.reset(token)
    return wrapped


def resumed(saved_keys, batches):
    ledger = _current.get()
    if ledger:
        count = len(saved_keys)
        ledger.data['reuse'].update(loaded=count, accepted=count, resumed_batches=batches,
                                    library_status='resumed' if batches else 'new')
        ledger.seen.update(tuple(keys) for keys in saved_keys[:MAX_IDENTITIES])
        ledger.athletes.update(tuple(sorted(keys)) for keys in saved_keys[:MAX_IDENTITIES])
        ledger.identity_limit_reached = count > MAX_IDENTITIES
        ledger.data['reuse']['deduplicated'] = None if ledger.identity_limit_reached else len(ledger.seen)


def summary_lines(summary):
    """Allowlisted, compact shareable summary; no raw inputs or paths."""
    if not summary:
        return []
    def number(key, suffix):
        value = summary.get(key)
        return 'unknown' if value is None else f'{float(value):.2f}{suffix}'
    memory = summary.get('peak_observed_memory_bytes')
    observed = 'unknown' if memory is None else f'{memory / 1024**2:.1f} MiB'
    reuse = summary.get('reuse') or {}
    return ['Compute ledger (observational)',
        f"- Wall: {number('wall_seconds', 's')}; worker CPU: {number('cpu_seconds', 's')}; peak observed process memory: {observed}",
        f"- Candidates: {reuse.get('generated', 0)} returned by generators; {reuse.get('deduplicated', 0)} unique observed; {summary.get('duplicate_returned')} duplicate returned.",
        f"- Batches: {summary.get('completed_batch_count', 0)}/{summary.get('batch_count', 0)} completed; library accepted: {reuse.get('accepted', 0)}; rejected: {reuse.get('rejected', 0)}; reused scenarios: {reuse.get('reused_scenarios', 0)}"]
