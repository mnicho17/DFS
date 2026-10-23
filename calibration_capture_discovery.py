"""Automatic report-only calibration from independently qualified pregame captures.

This index never chooses a user's forecast/build, runs a SIM, or tunes a model.
Metadata checkpoints reuse completed qualification; changed evidence is withheld.
"""
from collections import Counter, defaultdict
from contextlib import closing
import datetime as dt
from functools import lru_cache
import hashlib
import json
import re
from pathlib import Path
import sqlite3

from analysis_imports import _check, _sources
from build_snapshots import validate_snapshot
from distribution_build_selection import _paths, catalog
from distribution_calibration import qualified, summarize
from scoring_distributions import validate_distribution

VERSION = 1
MAX_IMPORTS = 500
MAX_ROWS = 200000
MAX_SNAPSHOT_BYTES = 256000000
MAX_READ_BYTES = 256000000


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


@lru_cache(maxsize=1)
def _code():
    from candidate_library import code_id
    return code_id()


def _exists(conn, table):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _stamp(path):
    if path.is_symlink() or getattr(path, 'is_junction', lambda: False)() or not path.is_file():
        raise ValueError('Calibration evidence must be a regular, unlinked file.')
    stat = path.stat()
    return [stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_dev, stat.st_ino]


def evidence_files(root, cancelled=lambda: False):
    """Bounded metadata only; no source hashing or snapshot parsing on a warm check."""
    root = Path(root)
    files = {}
    for folder in ('snapshots', 'scoring-distributions'):
        for path in _paths(root / folder):
            _check(cancelled)
            files[folder + '/' + path.name] = _stamp(path)
    return files


def _state(conn):
    if not _exists(conn, 'calibration_discovery_state'):
        return None
    row = conn.execute('SELECT payload,digest FROM calibration_discovery_state WHERE id=1').fetchone()
    if row:
        try:
            value = json.loads(row[0])
            if _digest(value) == row[1] and value.get('version') == VERSION:
                return value
        except (ValueError, TypeError):
            pass
    return None


def _inventory(root, files, previous, cancelled):
    from review_build_evidence import Reader, snapshot
    headers = {}; total = 0
    old = previous.get('headers', {}) if previous and previous.get('code') == _code() else {}
    for relative, stamp in sorted(files.items()):
        _check(cancelled)
        if not relative.startswith('snapshots/'):
            continue
        cached = old.get(relative)
        if cached and cached['stamp'] == stamp:
            headers[relative] = cached
            continue
        reader = Reader(lambda: _check(cancelled))
        snap = snapshot(reader.data(Path(root) / relative))
        total += reader.bytes
        if total > MAX_SNAPSHOT_BYTES:
            raise ValueError('Calibration snapshot inventory read budget exceeded; no partial index.')
        headers[relative] = dict(stamp=stamp, input_id=snap['raw']['input_id'],
                                 kind=snap['kind'], day=snap['day'], games=sorted({m.group(0)
                for p in snap['players'] if (m := re.search(r'\b[A-Z]{2,3}@[A-Z]{2,3}\b', str(p.get('GameInfo', '')).upper()))}))
    return dict(version=VERSION, code=_code(), root=str(Path(root).resolve()), files=files, headers=headers)


def _records(conn):
    if not _exists(conn, 'analysis_salary_pairs') or not _exists(conn, 'analysis_sources'):
        return {}
    sources = {s['hash']: s for s in _sources(conn)}
    groups = defaultdict(list)
    for pair in conn.execute('SELECT * FROM analysis_salary_pairs ORDER BY result_hash'):
        result, salary = sources.get(pair[0]), sources.get(pair[1])
        if result and result.get('import_id'):
            groups[result['import_id']].append(dict(pair=list(pair), result=result, salary=salary))
    if len(groups) > MAX_IMPORTS:
        raise ValueError('Calibration import inventory limit exceeded; no partial index.')
    return dict(groups)


def _key(records, inventory):
    scope = None
    if len(records) == 1 and records[0]['salary']:
        manifest = records[0]['salary']['manifest']
        dates = manifest.get('dates', [])
        if len(dates) == 1:
            scope = (manifest.get('format'), dates[0], manifest.get('games', []))
    headers = inventory['headers']
    relevant = {path: h for path, h in headers.items()
                if scope is None or ((h['kind'], h['day']) == scope[:2]
                    and (not h.get('games') or not scope[2] or set(h['games']) <= set(scope[2])))}
    identities = {h['input_id'] for h in relevant.values()}
    files = {path: stamp for path, stamp in inventory['files'].items()
             if path in relevant or (path.startswith('scoring-distributions/')
                 and path.split('/', 1)[1].split('-', 1)[0] in identities)}
    sources = []
    for record in records:
        for source in (record['result'], record['salary']):
            if source:
                sources.append([source['hash'], _stamp(Path(source['snapshot']))])
    return _digest([VERSION, inventory['code'], inventory['root'], records, sources, files])


def _cached(conn):
    if not _exists(conn, 'calibration_capture_evidence'):
        return {}
    result = {}
    for ident, key, raw, digest in conn.execute('SELECT import_id,fingerprint,payload,digest FROM calibration_capture_evidence'):
        try:
            payload = json.loads(raw)
            if (_digest(payload) == digest and payload.get('version') == VERSION
                    and payload.get('status') in ('available', 'no qualifying captures', 'unavailable')
                    and isinstance(payload.get('rows'), list) and len(payload['rows']) <= MAX_ROWS):
                result[ident] = (key, payload)
        except (ValueError, TypeError):
            pass
    return result


def _checked_bytes(path, digest, budget, cancelled, maximum):
    _check(cancelled)
    if _stamp(path)[0] > maximum:
        raise OverflowError('Calibration file read budget exceeded; no partial index.')
    blocks = []; checksum = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            _check(cancelled); budget[0] += len(block)
            if budget[0] > MAX_READ_BYTES or sum(map(len, blocks)) + len(block) > maximum:
                raise OverflowError('Calibration evidence read budget exceeded; no partial index.')
            checksum.update(block); blocks.append(block)
    if checksum.hexdigest() != digest:
        raise ValueError('Calibration evidence changed during qualification.')
    return b''.join(blocks)


def _discover(conn, ident, root, records, cancelled):
    if len(records) != 1:
        raise ValueError('Ambiguous result import identity; no pairing order was chosen.')
    data = catalog(conn, ident, root, cancelled)
    seen = {}; rejected = Counter(r['reason'] for r in data['rejected'])
    budget = [0]; checked_files = {}; snapshots = {}
    from learning_db import _normalize_roster_token
    for candidate in data['candidates']:
        _check(cancelled)
        cp, sp = Path(candidate['capture_path']), Path(candidate['snapshot_path'])
        snapshot_key = (str(sp), candidate['snapshot_digest'])
        if snapshot_key not in snapshots:
            raw = _checked_bytes(sp, candidate['snapshot_digest'], budget, cancelled, 32_000_000)
            snapshots[snapshot_key] = validate_snapshot(json.loads(raw.decode('utf-8')))
            checked_files[str(sp)] = (candidate['snapshot_digest'], 32_000_000)
        raw = _checked_bytes(cp, candidate['capture_digest'], budget, cancelled, 5 * 1024 * 1024)
        value = validate_distribution(json.loads(raw.decode('utf-8')))
        checked_files[str(cp)] = (candidate['capture_digest'], 5 * 1024 * 1024)
        payload = value['payload']; snap = snapshots[snapshot_key]
        lookup = defaultdict(list)
        for player in snap['inputs']['players']:
            lookup[_normalize_roster_token(player.get('Name'))].append(player)
        for row in payload['players']:
            _check(cancelled)
            actual = data['scores'].get(row['name'])
            if actual is None:
                continue
            matches = lookup[row['name']]
            if len(matches) != 1:
                raise ValueError('Ambiguous exact-snapshot player identity.')
            evidence = dict(row, actual=actual, kind=data['kind'], model=payload['model_version'],
                captured_at=payload['finished_at'], scenarios=payload['scenarios'],
                capture_id=value['capture_id'], input_id=payload['input_id'],
                result_hash=candidate['result_hash'], salary_hash=candidate['salary_hash'],
                snapshot_digest=candidate['snapshot_digest'], capture_digest=candidate['capture_digest'])
            if not qualified(evidence):
                raise ValueError('Invalid qualified calibration comparison.')
            key = (evidence['kind'], evidence['model'], evidence['game'], evidence['name'])
            rank = (dt.datetime.fromisoformat(evidence['captured_at']), evidence['capture_id'])
            if key not in seen or rank > seen[key][0]:
                seen[key] = (rank, evidence)
            if len(seen) > MAX_ROWS:
                raise OverflowError('Calibration observation budget exceeded; no partial index.')
    for filename, (digest, maximum) in checked_files.items():
        _checked_bytes(Path(filename), digest, budget, cancelled, maximum)
    rows = [seen[key][1] for key in sorted(seen)]
    return dict(version=VERSION, status='available' if rows else 'no qualifying captures',
        rows=rows, captures=len(data['candidates']), rejected=dict(rejected),
        checked_at=dt.datetime.now(dt.timezone.utc).isoformat())


def refresh(db_path, *, cancelled=lambda: False, progress=lambda text: None):
    """Compute detached evidence, then publish atomically after a final input check."""
    path = Path(db_path).resolve(); root = path.parent
    _check(cancelled)
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as reader:
        reader.execute('BEGIN')
        previous = _state(reader); cached = _cached(reader); records = _records(reader)
        files = evidence_files(root, cancelled)
        inventory = _inventory(root, files, previous, cancelled)
        keys = {}; payloads = {}; reused = 0; unavailable = 0; total = 0
        for index, (ident, record) in enumerate(sorted(records.items()), 1):
            _check(cancelled)
            progress(f'Calibration captures: checking result {index}/{len(records)}')
            key = _key(record, inventory); keys[ident] = key
            old = cached.get(ident)
            if old and old[0] == key and old[1]['status'] != 'unavailable':
                payload = old[1]; reused += 1
            else:
                try:
                    payload = _discover(reader, ident, root, record, cancelled)
                except ValueError as exc:
                    payload = dict(version=VERSION, status='unavailable', reason=str(exc),
                        rows=[], captures=0, rejected={})
            unavailable += payload['status'] == 'unavailable'
            total += len(payload['rows'])
            if total > MAX_ROWS:
                raise OverflowError('Calibration observation budget exceeded; no partial index.')
            payloads[ident] = payload
    _check(cancelled)
    changed = sum(cached.get(i) != (keys[i], p) for i, p in payloads.items())
    removed = len(set(cached) - set(records))
    with closing(sqlite3.connect(path)) as writer:
        with writer:
            writer.execute('BEGIN IMMEDIATE')
            if _records(writer) != records or evidence_files(root, cancelled) != files:
                raise ValueError('Calibration inputs changed; no new evidence published. Retry history refresh.')
            if any(_key(record, inventory) != keys[i] for i, record in records.items()):
                raise ValueError('Calibration sources changed; no new evidence published. Retry history refresh.')
            _check(cancelled)
            if changed or removed or previous != inventory or not _exists(writer, 'calibration_capture_evidence'):
                writer.execute('CREATE TABLE IF NOT EXISTS calibration_capture_evidence(import_id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,payload TEXT NOT NULL,digest TEXT NOT NULL)')
                writer.execute('CREATE TABLE IF NOT EXISTS calibration_discovery_state(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL,digest TEXT NOT NULL)')
                writer.execute('DELETE FROM calibration_capture_evidence')
                for ident, payload in sorted(payloads.items()):
                    _check(cancelled)
                    writer.execute('INSERT INTO calibration_capture_evidence VALUES (?,?,?,?)',
                        (ident, keys[ident], _json(payload), _digest(payload)))
                writer.execute('INSERT OR REPLACE INTO calibration_discovery_state VALUES (1,?,?)',
                               (_json(inventory), _digest(inventory)))
                _check(cancelled)
    return dict(changed=changed+removed, imports=len(records), reused=reused, unavailable=unavailable,
                captures=sum(p['captures'] for p in payloads.values()), rows=total)


def read(conn):
    """Metadata-checked, read-only report view. Never initializes or repairs the index."""
    path = next((r[2] for r in conn.execute('PRAGMA database_list') if r[1] == 'main'), '')
    empty = dict(status='not checked', checked=0, stale=0, unavailable=0, empty=0, rows=[], reasons={})
    if not path or not _exists(conn, 'calibration_capture_evidence'):
        return empty
    state = _state(conn); cached = _cached(conn)
    try:
        if (not state or state['code'] != _code() or state['root'] != str(Path(path).resolve().parent)
                or evidence_files(Path(path).parent) != state['files']):
            return dict(empty, status='needs refresh', stale=len(cached))
        records = _records(conn)
        rows = []; stale = len(set(cached)-set(records)); unavailable = 0; checked = 0; no_captures = 0; reasons = Counter()
        for ident, record in records.items():
            old = cached.get(ident)
            if not old or _key(record, state) != old[0]:
                stale += 1; continue
            payload = old[1]; checked += 1
            no_captures += payload['status'] == 'no qualifying captures'
            if payload['status'] == 'unavailable':
                unavailable += 1; reasons[payload['reason']] += 1
            reasons.update(payload['rejected']); rows.extend(payload['rows'])
        return dict(status='checked', checked=checked, stale=stale,
                    unavailable=unavailable, empty=no_captures, rows=rows, reasons=dict(reasons))
    except (OSError, ValueError, KeyError, TypeError):
        return dict(empty, status='needs refresh', stale=len(cached))


def report_lines(conn):
    data = read(conn)
    lines = ['', 'Automatic calibration capture discovery — report only']
    if data['status'] != 'checked':
        return lines + ['- '+('Evidence changed; next history refresh will recheck captures.'
            if data['status'] == 'needs refresh' else 'Not checked yet; history refresh discovers saved pregame captures automatically.')]
    summary = summarize(data['rows'])
    lines.append(f"- {data['checked']} results checked; {data['stale']} awaiting refresh; {data['unavailable']} unavailable. "
        f"{summary['independent_games']} scheduled games; {summary['unique_player_games']} distinct player-game outcomes.")
    if data['empty']:
        lines.append(f"- {data['empty']} results have no qualifying pregame capture. Forecast snapshots alone cannot supply historical SIM ranges.")
    lines.append('- Separate research evidence from all qualified pregame captures; saved forecast/build choices and submitted-build validation are unchanged. Outcomes overlap other reports; do not add their sample counts. No simulations rerun or model defaults updated.')
    lines.append(f"- Repeated/conflicting comparisons excluded: {summary['excluded_repeated_or_conflicting']}; conflicting player-game outcomes: {summary['conflicting_player_games']}. Models and formats stay separate; each game has equal weight.")
    for reason, count in sorted(data['reasons'].items()):
        lines.append(f'- {count} excluded/unavailable checks: {reason}')
    groups = [g for g in summary['groups'] if g['axis'] == 'position']
    for group in groups[:80]:
        p, g = group['player_weighted'], group['equal_game_weighted']
        lines.append(f"- {group['kind']} / model {group['model'][:12]} / {group['label']}: {p['games']} games, {p['player_games']} player-games; actual-minus-SIM bias {g['actual_minus_mean_bias']:+.2f}; MAE {g['mean_score_mae']:.2f}; inside p10-p90 {g['inside_p10_p90_pct']:.1f}%; interval score {g['mean_p10_p90_interval_score']:.2f}.")
    if len(groups) > 80:
        lines.append(f'- {len(groups)-80} additional position/model groups retained in the evidence index.')
    lines.append('- Pregame timing and source receipts qualify observations, not predictive accuracy. Within-game outcomes are correlated; small samples do not justify automatic tuning.')
    return lines