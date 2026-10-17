"""Bounded discovery only; existing loaders remain the authority for reuse."""
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

MAX_REMEMBERED = 24
MAX_DISCOVERED = 48


def remember_library(settings, path):
    if settings is None:
        return
    try:
        saved = json.loads(str(settings.value('build/known_libraries', '[]')))
        if not isinstance(saved, list):
            saved = []
    except (ValueError, TypeError):
        saved = []
    path = str(Path(path).resolve())
    saved = [path] + [p for p in saved if isinstance(p, str) and p.casefold() != path.casefold()]
    settings.setValue('build/known_libraries', json.dumps(saved[:MAX_REMEMBERED]))


def remembered_libraries(settings):
    try:
        rows = json.loads(str(settings.value('build/known_libraries', '[]')))
        return tuple(p for p in rows[:MAX_REMEMBERED] if isinstance(p, str)) if isinstance(rows, list) else ()
    except (ValueError, TypeError):
        return ()


def discover_libraries(known, folder, players, kind, salary_cap, *, cancelled=lambda: False,
                       progress=lambda text: None, seconds=10):
    """No recursive disk scan, DB writes, forecasts, or partial-coverage opt-in.

    Remembered paths take priority; the standard folder is bounded to 48 entries.
    Every yielded path still needs full validation/loading under current rules.
    """
    from candidate_library import slate_id
    from build_snapshots import validate_snapshot
    deadline = time.monotonic() + seconds
    candidates = list(known[:MAX_REMEMBERED])
    root = Path(folder) if folder else None
    if root and root.is_dir():
        # Do not sort/stat an unbounded directory just to find recent candidates.
        from itertools import islice
        candidates.extend(str(p) for p in islice(root.iterdir(), MAX_DISCOVERED))
    seen = set()
    for raw in candidates:
        if cancelled() or time.monotonic() >= deadline:
            return
        path = Path(raw).resolve()
        key = str(path).casefold()
        if key in seen or path.suffix.lower() not in ('.dfslib', '.sdlib', '.sdfull'):
            continue
        seen.add(key)
        try:
            if not path.is_file():
                continue
            if path.suffix.lower() in ('.sdlib', '.sdfull'):
                if kind != 'showdown':
                    continue
                from showdown_library import _read, _structure
                def header(part):
                    with closing(sqlite3.connect(part.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as con:
                        return _read(con, verify_count=False)
                if path.suffix.lower() == '.sdfull':
                    from showdown_full_library import _read as read_manifest, partition_path
                    state = read_manifest(path)
                    structure = state['structure']
                    if _structure(players, structure['salary_cap'], structure['captains']) != structure:
                        raise ValueError('Different slate or salaries.')
                    for captain in structure['captains']:
                        if cancelled() or time.monotonic() >= deadline:
                            return
                        part = header(partition_path(path, captain))
                        if not part['complete'] or part['structure'] != dict(structure, captains=[captain]):
                            raise ValueError('Partial or incompatible Captain partition.')
                else:
                    state = header(path)
                    structure = state['structure']
                    if not state['complete']:
                        raise ValueError('Partial coverage requires explicit selection.')
                    if _structure(players, structure['salary_cap'], structure['captains']) != structure:
                        raise ValueError('Different slate or salaries.')
                if not 0 < salary_cap <= structure['salary_cap']:
                    raise ValueError('Requested salary cap exceeds prepared coverage.')
                # This is a cheap metadata probe. The existing loader still checks
                # actual row counts, Captain coverage, current rules and cache evidence.
            else:
                with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)) as con:
                    meta = dict(con.execute('SELECT key,value FROM library_meta'))
                snapshot = validate_snapshot(json.loads(meta['snapshot']))
                if meta.get('slate_id') != slate_id(players, kind) or snapshot['inputs']['recipe']['contest_kind'] != kind:
                    continue
            progress('Checking reusable library: ' + path.name)
            yield str(path)
        except (ValueError, OSError, sqlite3.Error, KeyError, TypeError) as exc:
            progress('Library skipped: ' + path.name + ' — ' + str(exc))
