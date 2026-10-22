"""Explicit SIM validation associations; never alter submitted or forecast evidence."""
from collections import Counter
from contextlib import closing
import datetime as dt
import hashlib
import itertools
import csv
import re
import json
from pathlib import Path
import sqlite3

from analysis_imports import _check, _sources, paired_metadata
from historical_identity import _snapshot_compatible
from learning_db import _normalize_roster_token
from review_build_evidence import Reader, snapshot, MAX_DIRECTORY_ENTRIES, MAX_FILES
from results_snapshot_learning import match_snapshot, forecast_group
from scoring_distributions import game_identity, load_distribution


def _scores(source, salary, cancelled):
    """Read immutable side-table outcomes without first/last-record identity resolution."""
    from results_audit import _number
    from analysis_imports import _verify
    path = Path(source['snapshot'])
    if path.stat().st_size > 512_000_000:
        raise ValueError('Result score-source read budget exceeded.')
    values = {}
    with path.open(newline='', encoding='utf-8-sig') as handle:
        reader = csv.DictReader(handle)
        if not {'Player', 'FPTS'} <= set(reader.fieldnames or []):
            raise ValueError('No recorded actual player-score table.')
        for index, row in enumerate(reader):
            if index % 500 == 0:
                _check(cancelled)
            if index >= 5_000_000:
                raise ValueError('Result score-source row budget exceeded.')
            name = _normalize_roster_token(row.get('Player'))
            score = _number(row.get('FPTS'))
            if not name or score is None:
                continue
            role = str(row.get('Roster Position') or '').strip().upper().replace('CAPTAIN', 'CPT').replace('D/ST', 'DST')
            explicit = re.search(r'\((\d+)\)\s*$', str(row.get('Player') or ''))
            if explicit:
                matches = [p for p in salary['players'] if _normalize_roster_token(p['name']) == name and p['role'] == role]
                if len(matches) != 1 or matches[0]['id'] != explicit[1]:
                    raise ValueError('Conflicting explicit actual-score player identity.')
            key = ('@cpt:' if role == 'CPT' else '') + name
            values.setdefault(key, set()).add(score)
            if len(values) > 5000:
                raise ValueError('Actual player-score table limit exceeded.')
    _verify(source, cancelled)
    if any(max(v)-min(v) > .02 for v in values.values()):
        raise ValueError('Conflicting actual player scores; no record order was chosen.')
    scores = {k: min(v) for k, v in values.items()}
    for key, score in scores.items():
        if key.startswith('@cpt:') and key[5:] in scores and abs(score - 1.5*scores[key[5:]]) > .02:
            raise ValueError('Captain actual score differs from 1.5x base score.')
    return scores


def _paths(folder):
    if not folder.exists():
        return []
    if folder.is_symlink() or getattr(folder, 'is_junction', lambda: False)():
        raise ValueError('SIM evidence directory cannot be a link.')
    paths = list(itertools.islice(folder.iterdir(), MAX_DIRECTORY_ENTRIES + 1))
    if len(paths) > MAX_DIRECTORY_ENTRIES:
        raise ValueError('SIM evidence inventory limit exceeded; no partial selection.')
    return sorted(p for p in paths if p.suffix == '.json')


def catalog(conn, import_id, folder, cancelled=lambda: False):
    """Read and qualify all available captures against one immutable salary/results pair."""
    root = Path(folder)
    _, receipt = paired_metadata(conn, import_id, cancelled)
    if not receipt:
        raise ValueError('Save a qualified salary/results pairing before choosing a SIM build.')
    sources = {s['hash']: s for s in _sources(conn)}
    source, salary = sources[receipt['result_hash']], sources[receipt['salary_hash']]
    manifest = salary['manifest']
    scores = _scores(source, manifest, cancelled)
    observed = {k.removeprefix('@cpt:') for k in scores}
    kind = manifest['format']
    dates = manifest['dates']
    if len(dates) != 1:
        raise ValueError('A single scheduled slate date is required.')
    snapshots = {}
    total = 0
    counts = Counter()
    for path in _paths(root / 'snapshots'):
        _check(cancelled)
        reader = Reader(lambda: _check(cancelled))
        snap = snapshot(reader.data(path))
        total += reader.bytes
        counts[(snap['kind'], snap['day'])] += 1
        if total > 256_000_000 or counts[(snap['kind'], snap['day'])] > MAX_FILES:
            raise ValueError('SIM snapshot read budget exceeded; no partial selection.')
        if snap['kind'] != kind or snap['day'] != dates[0]:
            continue
        matched, _ = match_snapshot(root, dates[0], kind, observed, source['name'],
                                    snapshots=[snap['raw']], check=lambda: _check(cancelled))
        if matched and _snapshot_compatible(snap, manifest):
            snapshots.setdefault(matched['input_id'], {}).setdefault(reader.receipts[str(path)], (snap, path, reader.receipts[str(path)]))
    rows, rejected = [], []
    capture_count = 0
    for path in _paths(root / 'scoring-distributions'):
        _check(cancelled)
        # Only files for qualified immutable inputs can be candidate evidence.
        ident = path.name.split('-', 1)[0]
        if ident not in snapshots:
            continue
        capture_count += 1
        if capture_count > MAX_FILES:
            raise ValueError('SIM capture count limit exceeded; no partial selection.')
        if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
            raise ValueError('SIM capture cannot be a link.')
        try:
            size = path.stat().st_size
            total += size
            if total > 256_000_000:
                raise OverflowError("SIM capture read budget exceeded; no partial selection.")
            if size > 5 * 1024 * 1024:
                raise ValueError('SIM capture read budget exceeded.')
            raw = path.read_bytes()
            value = load_distribution(path)
            # Detect replacement during qualification, even when both revisions are valid.
            digest = hashlib.sha256(raw).hexdigest()
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError('SIM capture changed during qualification.')
            p = value['payload']
            if p['input_id'] != ident or p['kind'] != kind:
                raise ValueError('SIM input/format identity differs.')
            if path.name != ident + '-' + value['capture_id'] + '.json':
                raise ValueError('SIM capture filename identity differs.')
            if len(snapshots[ident]) != 1:
                raise ValueError('Conflicting snapshot copies; choose no capture.')
            snap, snap_path, snap_digest = next(iter(snapshots[ident].values()))
            start = dt.datetime.fromisoformat(p['started_at'])
            finish = dt.datetime.fromisoformat(p['finished_at'])
            if not start.tzinfo or not finish.tzinfo or not snap['created'] <= start <= finish < snap['earliest']:
                raise ValueError('SIM must complete after its snapshot and before the earliest kickoff.')
            lookup = {_normalize_roster_token(player['Name']): player for player in snap['players']}
            names = [r['name'] for r in p['players']]
            if not names or len(names) != len(set(names)):
                raise ValueError('Missing or ambiguous captured player identity.')
            for row in p['players']:
                player = lookup.get(row['name'])
                if (not player or row['game'] != game_identity(player) or
                        row['position'] != player.get('Position') or row['role'] != forecast_group(player)):
                    raise ValueError('Captured player/game identity differs from the exact snapshot.')
            covered = sum(r['name'] in scores for r in p['players'])
            if not covered:
                raise ValueError('No actual-score coverage for this capture.')
            rows.append(dict(import_id=import_id, input_id=ident, capture_id=value['capture_id'],
                snapshot_digest=snap_digest, capture_digest=digest, **receipt,
                recorded_at=snap['raw']['created_at'], finished_at=p['finished_at'],
                model=p['model_version'], scenarios=p['scenarios'], covered=covered,
                players=len(p['players']), snapshot_path=str(snap_path), capture_path=str(path)))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            rejected.append(dict(input_id=ident, reason=str(exc)))
    _check(cancelled)
    return dict(import_id=import_id, name=source['name'], kind=kind, scores=scores,
                candidates=sorted(rows, key=lambda r: (r['finished_at'], r['capture_id']), reverse=True),
                rejected=rejected)


def saved_choice(conn, import_id):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='distribution_build_choices'").fetchone():
        return None
    row = conn.execute('SELECT payload FROM distribution_build_choices WHERE import_id=?', (import_id,)).fetchone()
    return json.loads(row[0]) if row else None


def resolve_choice(conn, import_id, folder, kind, scores, cancelled=lambda: False):
    choice = saved_choice(conn, import_id)
    if not choice:
        return None
    data = catalog(conn, import_id, folder, cancelled)
    if data['kind'] != kind or data['scores'] != scores:
        raise ValueError('Saved SIM choice does not match this exact result-score source.')
    keys = ('import_id', 'input_id', 'capture_id', 'snapshot_digest', 'capture_digest', 'result_hash', 'salary_hash')
    matches = [r for r in data['candidates'] if all(r[k] == choice.get(k) for k in keys)]
    if len(matches) != 1:
        raise ValueError('Saved SIM choice no longer qualifies; review it. No alternate build was selected.')
    return matches[0]


def save_choice(db_path, import_id, choice, cancelled=lambda: False):
    from distribution_validation import compare_distributions
    with closing(sqlite3.connect(db_path)) as conn:
        data = catalog(conn, import_id, Path(db_path).resolve().parent, cancelled)
        keys = ('import_id', 'input_id', 'capture_id', 'snapshot_digest', 'capture_digest', 'result_hash', 'salary_hash')
        matches = [r for r in data['candidates'] if all(r[k] == choice.get(k) for k in keys)]
        if len(matches) != 1:
            raise ValueError('Selected build changed or no longer qualifies; reopen the review.')
        selected = matches[0]
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            _check(cancelled)
            conn.execute('CREATE TABLE IF NOT EXISTS distribution_build_choices(import_id TEXT PRIMARY KEY,payload TEXT)')
            conn.execute('INSERT OR REPLACE INTO distribution_build_choices VALUES (?,?)',
                         (import_id, json.dumps({k: selected[k] for k in keys})))
            result = compare_distributions(conn, import_id, selected['input_id'], data['kind'],
                                           data['scores'], Path(db_path).resolve().parent, cancelled=cancelled)
            if result['status'] != 'matched':
                raise ValueError(result['reason'])
            _check(cancelled)
        return dict(committed=True, message=f"SIM build selected: {len(result['rows'])} player comparisons. Forecast snapshot unchanged.")
