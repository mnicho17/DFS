"""Metadata-only shortcut for successfully completed automatic history refreshes.

This is a scheduling checkpoint, never an evidence qualification authority.
Explicit refreshes and downstream evidence consumers retain full validation.
"""
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

VERSION = 1


def stamp(path):
    s = path.stat()
    return [str(path.resolve()), s.st_size, s.st_mtime_ns, s.st_ctime_ns,
            s.st_dev, s.st_ino]


def inputs(worker, *, sources_only=False):
    from analysis_imports import _check
    rows, watched = [], []
    roots = [Path(p).resolve() for p in (worker.results_folder, worker.salary_folder) if p]
    if not roots:
        raise ValueError('No history folders configured')
    for root in roots:
        if not root.is_dir():
            raise ValueError('History folder unavailable')
        for folder, dirs, names in os.walk(root, followlinks=False,
                onerror=lambda exc: (_ for _ in ()).throw(exc)):
            _check(worker.cancelled.is_set)
            dirs[:] = sorted(d for d in dirs if not Path(folder, d).is_symlink())
            watched.append(str(Path(folder).resolve()))
            for name in sorted(names):
                path = Path(folder, name)
                if path.suffix.casefold() == '.csv' and not path.is_symlink():
                    rows.append(stamp(path))
                    watched.append(str(path.resolve()))
    # Saved immutable evidence must also remain present and unchanged.
    evidence = Path(worker.db_path).parent / 'analysis_sources'
    if not sources_only and evidence.exists():
        for folder, dirs, names in os.walk(evidence, followlinks=False,
                onerror=lambda exc: (_ for _ in ()).throw(exc)):
            _check(worker.cancelled.is_set)
            dirs.sort()
            for name in sorted(names):
                rows.append(stamp(Path(folder, name)))
    code = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob('*.py')):
        if not path.name.startswith('test_'):
            code.update(path.name.encode())
            code.update(path.read_bytes())
    packaged = stamp(Path(sys.executable)) if getattr(sys, 'frozen', False) else None
    payload = [VERSION, worker.username, list(map(str, roots)), sorted(rows), code.hexdigest(), packaged]
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest(), watched[:4096]


def database(worker):
    # Read committed catalog state (including WAL) without invalidating for unrelated
    # build/report writes in the same database. Never open/create a missing DB.
    from contextlib import closing
    path = Path(worker.db_path).resolve()
    state = []
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as conn:
        conn.execute('BEGIN')
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('analysis_sources', 'analysis_salary_pairs',
                      'historical_imports', 'opponent_contests', 'analysis_date_corrections'):
            if table in tables:
                rows = sorted(json.dumps(list(row), default=str) for row in
                              conn.execute('SELECT * FROM ' + table))
                state.append([table, rows])
            else:
                state.append([table, None])
    return hashlib.sha256(json.dumps(state).encode()).hexdigest()


def cache_path(worker):
    return Path(str(Path(worker.db_path).resolve()) + '.refresh-complete.json')


def read(worker, fingerprint):
    try:
        path = cache_path(worker)
        if path.stat().st_size > 1024 * 1024:
            return None
        record = json.loads(path.read_text(encoding='utf-8'))
        if record['inputs'] == fingerprint and record['database'] == database(worker):
            result = record['result']
            if isinstance(result, dict):
                return dict(result, unchanged_checkpoint=True)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        pass
    return None


def save(worker, fingerprint, result, source_fingerprint=None):
    if (result.get('cancelled') or result.get('errors') or result.get('failed') or
            result.get('username_history', {}).get('errors')):
        return
    after, _ = inputs(worker)
    if source_fingerprint is None:
        stable = after == fingerprint
    else:
        stable = inputs(worker, sources_only=True)[0] == source_fingerprint
    if not stable or worker.cancelled.is_set():
        return
    # Only counters and watcher paths are retained; no reports or source contents.
    summary = {key: result.get(key, 0) for key in
               ('unpaired', 'duplicates_skipped')}
    summary.update(results_imported=0, salaries_imported=0, pairs_added=0,
                   errors=[], cancelled=False, watch_paths=result.get('watch_paths', []))
    record = dict(inputs=after, database=database(worker), result=summary)
    import tempfile
    target = cache_path(worker)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent,
                prefix=target.name, suffix='.tmp', delete=False) as handle:
            temporary = handle.name
            json.dump(record, handle)
        os.replace(temporary, target)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
