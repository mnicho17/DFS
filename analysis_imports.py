"""Content-addressed results/salary imports and explicit, revision-bound pairing.

Source files are never moved or edited. These tables supplement existing history;
they do not rewrite old scores, cash, ownership, forecasts or strategy inputs.
"""
from contextlib import closing
import csv
from datetime import date
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import re
import tempfile


class ImportCancelled(Exception):
    pass


def _check(cancelled):
    if cancelled():
        raise ImportCancelled()


def _hash(path, cancelled=lambda: False):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        while block := handle.read(1024 * 1024):
            _check(cancelled)
            digest.update(block)
    _check(cancelled)
    return digest.hexdigest()


def _key(text):
    return re.sub(r'[ _/-]', '', str(text or '')).casefold()


def _name(text):
    return ' '.join(re.sub(r'\s*\(\d+\)\s*$', '', str(text)).casefold().split())


def _dates(text):
    found = set()
    for pattern, us in ((r'(?<!\d)(20\d{2})[-_/](\d{1,2})[-_/](\d{1,2})(?!\d)', False),
                        (r'(?<!\d)(\d{1,2})[-_/](\d{1,2})[-_/](20\d{2})(?!\d)', True)):
        for parts in re.findall(pattern, str(text)):
            a, b, c = map(int, parts)
            try:
                found.add(date(c, a, b).isoformat() if us else date(a, b, c).isoformat())
            except ValueError:
                pass
    return found


def _reader(handle):
    sample = handle.read(8192)
    handle.seek(0)
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=',;\t|')
    except csv.Error:
        try:
            dialect = csv.Sniffer().sniff(sample.splitlines()[0], delimiters=',;\t|')
        except (csv.Error, IndexError):
            dialect = csv.excel
    return csv.reader(handle, dialect)


def _header(cells):
    keys = [_key(c) for c in cells]
    populated = [k for k in keys if k]
    if len(populated) != len(set(populated)):
        raise ValueError('Duplicate CSV columns; the file needs review.')
    return keys


def _kind(header):
    keys = set(header)
    if {'name', 'id', 'salary', 'gameinfo', 'teamabbrev', 'position', 'rosterposition'} <= keys:
        return 'salary'
    if ('entryid' in keys or 'entryname' in keys) and keys & {'rank', 'points', 'winnings', 'actualpoints'}:
        return 'results'
    return None


def _salary_header(reader, cancelled=lambda: False):
    for line, cells in enumerate(reader, 1):
        _check(cancelled)
        keys = [_key(c) for c in cells]
        for offset, key in enumerate(keys):
            if key == 'position' and _kind(keys[offset:]) == 'salary':
                return offset, _header(cells[offset:]), line
        if line >= 50:
            break
    return None


def inspect_kind(path, cancelled=lambda: False):
    with open(path, newline='', encoding='utf-8-sig') as handle:
        reader = _reader(handle)
        cells = next(reader, [])
        kind = _kind([_key(c) for c in cells])
        if kind == 'results':
            _header(cells)
            return kind
        if _salary_header(itertools.chain([cells], reader), cancelled):
            return 'salary'
        return None


def _salary_manifest(path, cancelled):
    rows, ids, dates, formats, games = [], set(), set(), set(), set()
    nfl_teams = set('ARI ATL BAL BUF CAR CHI CIN CLE DAL DEN DET GB HOU IND JAX JAC KC LAC LAR LA LV MIA MIN NE NO NYG NYJ PHI PIT SEA SF TB TEN WAS WSH'.split())
    with open(path, newline='', encoding='utf-8-sig') as handle:
        reader = _reader(handle)
        found = _salary_header(reader, cancelled)
        if not found:
            raise ValueError('No supported salary table found.')
        offset, header, header_line = found
        for index, cells in enumerate(reader, header_line+1):
            _check(cancelled)
            if not any(c.strip() for c in cells):
                continue
            row = dict(zip(header, cells[offset:]))
            name, player_id = row.get('name', '').strip(), row.get('id', '').strip()
            if not name and not player_id and not row.get('salary', '').strip():
                continue  # entry rows may continue after the embedded salary table
            role = row.get('rosterposition', '').strip().upper()
            position, team = row.get('position', '').strip().upper(), row.get('teamabbrev', '').strip().upper()
            if not name or not player_id or (role, player_id) in ids:
                raise ValueError(f'Salary row {index}: missing identity or repeated ID/role.')
            ids.add((role, player_id))
            if position not in {'QB', 'RB', 'WR', 'TE', 'K', 'DST'} or team not in nfl_teams:
                raise ValueError(f'Salary row {index}: only identifiable NFL salaries are supported.')
            if role in ('CPT', 'FLEX'):
                formats.add('showdown')
            elif role and set(role.split('/')) <= {'QB', 'RB', 'WR', 'TE', 'FLEX', 'DST'}:
                formats.add('classic')
            else:
                raise ValueError(f'Salary row {index}: unsupported roster eligibility.')
            try:
                salary = float(row.get('salary', '').replace(',', ''))
                if not math.isfinite(salary) or salary < 0 or not salary.is_integer():
                    raise ValueError()
            except ValueError:
                raise ValueError(f'Salary row {index}: salary is missing or invalid (unknown is not zero).') from None
            game = re.search(r'\b([A-Z]{2,3})@([A-Z]{2,3})\b', row.get('gameinfo', '').upper())
            row_dates = _dates(row.get('gameinfo', ''))
            if not game or team not in game.groups() or len(row_dates) != 1:
                raise ValueError(f'Salary row {index}: game, team or game date is unresolved.')
            dates.update(row_dates)
            games.add(game.group(0))
            rows.append(dict(name=name, id=player_id, role=role, position=position, team=team,
                             opponent=game.group(2) if team == game.group(1) else game.group(1),
                             salary=int(salary), raw=row))
    if not rows or len(formats) != 1:
        raise ValueError('Empty or mixed-format salary file.')
    contest_format = next(iter(formats))
    if contest_format == 'showdown' and (len(games) != 1 or {r['role'] for r in rows} != {'CPT', 'FLEX'}):
        raise ValueError('Showdown needs one game and separate CPT and FLEX salary rows.')
    return dict(version=1, sport='NFL', format=contest_format, dates=sorted(dates), games=sorted(games), players=rows)


def _results_manifest(path, cancelled):
    from opponent_analysis import _roster, _MARKERS
    formats, dates, contests, observed, games, contest_names = set(), set(), set(), set(), set(), set()
    entries = unreadable = 0
    with open(path, newline='', encoding='utf-8-sig') as handle:
        reader = _reader(handle)
        header = _header(next(reader, []))
        for index, cells in enumerate(reader):
            if index % 500 == 0:
                _check(cancelled)
            row = dict(zip(header, cells))
            if not (row.get('entryid') or row.get('entryname')):
                continue
            entries += 1
            for key in ('slatedate', 'date', 'contestdate'):
                dates.update(_dates(row.get(key, '')))
            if row.get('contestid'):
                contests.add(row['contestid'])
            if row.get('contestname'):
                contest_names.add(row['contestname'])
            games.update('@'.join(g) for g in re.findall(r'\b([A-Z]{2,3})\s*@\s*([A-Z]{2,3})\b', row.get('gameinfo', '').upper()))
            text = row.get('lineup') or row.get('roster') or ''
            fmt = 'showdown' if re.search(r'\b(CPT|CAPTAIN)\b', text, re.I) else 'classic'
            if not _roster(text, fmt):
                unreadable += 1
                continue
            formats.add(fmt)
            markers = list(_MARKERS.finditer(text))
            for n, marker in enumerate(markers):
                label = text[marker.end():markers[n+1].start() if n+1 < len(markers) else len(text)].strip()
                role = marker.group(1).upper().replace('CAPTAIN', 'CPT').replace('D/ST', 'DST')
                numeric = re.search(r'\((\d+)\)\s*$', label)
                observed.add((role, _name(label), numeric.group(1) if numeric else ''))
    if not entries:
        raise ValueError('No result entry rows found.')
    filename_dates = _dates(Path(path).name)
    games.update('@'.join(g) for g in re.findall(r'\b([A-Z]{2,3})\s*@\s*([A-Z]{2,3})\b', Path(path).name.upper()))
    if dates and filename_dates and dates != filename_dates:
        dates.update(filename_dates)  # conflicting evidence prevents automatic pairing
    elif not dates:
        dates = filename_dates
    return dict(version=1, format=next(iter(formats)) if len(formats) == 1 else None,
                dates=sorted(dates), games=sorted(games), date_source='CSV/filename (unverified)', contest_ids=sorted(contests),
                contest_names=sorted(contest_names),
                entries=entries, unreadable=unreadable, observed=sorted(observed))


def ensure_tables(conn):
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS analysis_sources (
          hash TEXT PRIMARY KEY, kind TEXT NOT NULL, snapshot TEXT NOT NULL,
          original_name TEXT NOT NULL, source_path TEXT NOT NULL, manifest TEXT NOT NULL,
          import_id TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS analysis_salary_pairs (
          result_hash TEXT PRIMARY KEY REFERENCES analysis_sources(hash),
          salary_hash TEXT NOT NULL REFERENCES analysis_sources(hash),
          basis TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    ''')


def _sources(conn):
    return [dict(hash=h, kind=k, snapshot=p, name=n, source_path=s, manifest=json.loads(m), import_id=i)
            for h, k, p, n, s, m, i in conn.execute(
                'SELECT hash,kind,snapshot,original_name,source_path,manifest,import_id FROM analysis_sources ORDER BY created_at,hash')]


def qualify_pair(result, salary):
    """Return compatibility and exact role/ID/name matches; never guess aliases."""
    r, s = result['manifest'], salary['manifest']
    failures = []
    if not r.get('format') or r['format'] != s['format']:
        failures.append('Roster format differs or is unknown.')
    if len(r['contest_ids']) > 1 or len(r.get('contest_names', [])) > 1:
        failures.append('Results contain multiple contests; split the file first.')
    if not r['observed']:
        failures.append('No readable rosters to validate.')
    if len(r['dates']) > 1 or (r['dates'] and r['dates'] != s['dates']):
        failures.append('Slate dates conflict or are ambiguous.')
    if len(s['dates']) != 1:
        failures.append('Multi-day salary slates need separate support.')
    if r.get('games') and not set(r['games']).issubset(s['games']):
        failures.append('Explicit game/team information conflicts.')
    if failures:
        return dict(compatible=False, automatic=False, reason=' '.join(failures),
                    matched=0, observed=len(r['observed']), matches={})
    matches = {}
    missing = []
    for role, name, player_id in r['observed']:
        eligible = [p for p in s['players'] if _name(p['name']) == name
                    and (not player_id or p['id'] == player_id)
                    and (p['role'] == role if s['format'] == 'showdown' else role in p['role'].split('/'))]
        if len(eligible) != 1:
            missing.append(f'{role} {name}' + (' (ambiguous)' if eligible else ' (missing)'))
        else:
            matches[(role, name, player_id)] = eligible[0]
    if missing:
        failures.append(f'{len(missing)} player/role identities unresolved: ' + ', '.join(missing[:8]))
    # Existing construction readers use a coarser name key. Do not qualify a
    # source whose distinct observed identities would collapse in that reader.
    from learning_db import _normalize_roster_token
    consumer_ids = {}
    for (role, name, player_id), player in matches.items():
        key = ('@cpt:' if role == 'CPT' else '') + _normalize_roster_token(player['name'])
        if key in consumer_ids and consumer_ids[key] != player['id']:
            failures.append('Distinct player IDs collapse to the same analysis name; pairing withheld.')
            break
        consumer_ids[key] = player['id']
    # A matching date and full observed identity coverage establish compatibility,
    # not proof that the salary pool or standings contain every eligible player.
    return dict(compatible=not failures, automatic=not failures and bool(r['dates']),
                reason=' '.join(failures) or ('Date and all observed player/role identities match.' if r['dates'] else
                    'Result date missing. Confirm this exact contest and salary slate.'),
                matched=len(matches), observed=len(r['observed']), matches=matches)


def _verify(source, cancelled=lambda: False):
    if _hash(source['snapshot'], cancelled) != source['hash']:
        raise ValueError(source['name'] + ': saved source changed; pairing withheld. Restore the original snapshot.')


def pairing_rows(db_path=None):
    from learning_db import _connect
    with closing(_connect(db_path)) as conn:
        ensure_tables(conn)
        sources = _sources(conn)
        pairs = dict(conn.execute('SELECT result_hash,salary_hash FROM analysis_salary_pairs'))
        salaries = [s for s in sources if s['kind'] == 'salary']
        output = []
        for result in (s for s in sources if s['kind'] == 'results'):
            candidates = []
            for salary in salaries:
                q = qualify_pair(result, salary)
                candidates.append(dict(hash=salary['hash'], name=salary['name'], compatible=q['compatible'], reason=q['reason']))
            output.append(dict(hash=result['hash'], name=result['name'], salary_hash=pairs.get(result['hash']), candidates=candidates,
                               unreadable=result['manifest']['unreadable']))
        return output


def save_pair(result_hash, salary_hash, *, db_path=None, confirm_date=False, cancelled=lambda: False, automatic=False):
    from learning_db import _connect
    with closing(_connect(db_path)) as conn:
        ensure_tables(conn)
        sources = {s['hash']: s for s in _sources(conn)}
        r, s = sources[result_hash], sources[salary_hash]
        if r['kind'] != 'results' or s['kind'] != 'salary':
            raise ValueError('Select one results source and one salary source.')
        q = qualify_pair(r, s)
        if not q['compatible'] or (not q['automatic'] and not confirm_date):
            raise ValueError(q['reason'])
        _verify(r, cancelled)
        _verify(s, cancelled)
        _check(cancelled)
        with conn:
            old = conn.execute('SELECT salary_hash FROM analysis_salary_pairs WHERE result_hash=?', (result_hash,)).fetchone()
            if old:
                if old[0] != salary_hash:
                    raise ValueError('A different salary revision is already paired. Existing analysis was preserved.')
                return False
            conn.execute('INSERT INTO analysis_salary_pairs(result_hash,salary_hash,basis) VALUES(?,?,?)',
                         (result_hash, salary_hash, 'automatic-date-and-identities' if automatic else
                          'user-selected-compatible-revision' if q['automatic'] else 'user-confirmed-date-and-identities'))
        return True


def _snapshot(path, digest, root, cancelled):
    target = root / digest / path.name
    if target.exists():
        if _hash(target, cancelled) != digest:
            raise ValueError('Saved snapshot changed; restore it before retrying.')
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.copy-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as dest, path.open('rb') as source:
            while block := source.read(1024 * 1024):
                _check(cancelled)
                dest.write(block)
        if _hash(temporary, cancelled) != digest:
            raise ValueError('Source changed during import. Retry with a stable file.')
        _check(cancelled)
        os.replace(temporary, target)
        return target
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def import_folders(results_folder, salary_folder='', *, username='', db_path=None,
                   cancelled=lambda: False, progress=lambda done, total, text: None):
    """Commit complete files individually; duplicates never rerun result analysis.

    Missing configured roots abort before writes. Other per-file errors are
    reported and retried next time. Previously imported results can be cataloged
    explicitly by this scan, without retargeting their legacy history records.
    """
    from learning_db import _connect, history_db_path, init_historical_import_tables, import_historical_result_csvs
    roots = [Path(p) for p in (results_folder, salary_folder) if p]
    if not roots:
        raise ValueError('Choose a results folder and/or a salary folder first.')
    for root in roots:
        if not root.is_dir():
            raise ValueError(f'Folder unavailable: {root}. Reconnect the drive or choose an available folder.')
    result = dict(results_imported=0, salaries_imported=0, duplicates_skipped=0, ignored=0,
                  personal_results_added=0, pairs_added=0, errors=[], cancelled=False, analysis_import_ids=[])
    database = db_path or history_db_path()
    with closing(_connect(database)) as conn:
        init_historical_import_tables(conn)
        ensure_tables(conn)
        try:
            files = set()
            def scan_error(error):
                result['errors'].append(str(error))
            for root in roots:
                for folder, dirs, names in os.walk(root, followlinks=False, onerror=scan_error):
                    _check(cancelled)
                    dirs[:] = sorted(d for d in dirs if not Path(folder, d).is_symlink())
                    for name in sorted(names):
                        path = Path(folder, name)
                        if path.suffix.casefold() == '.csv' and not path.is_symlink():
                            files.add(path.resolve())
            for index, path in enumerate(sorted(files), 1):
                _check(cancelled)
                progress(index, len(files), f'Checking {path.name} ({index}/{len(files)})')
                try:
                    digest = _hash(path, cancelled)
                    if conn.execute('SELECT 1 FROM analysis_sources WHERE hash=?', (digest,)).fetchone():
                        result['duplicates_skipped'] += 1
                        continue
                    kind = inspect_kind(path, cancelled)
                    if not kind:
                        result['ignored'] += 1
                        continue
                    snapshot = _snapshot(path, digest, Path(database).parent / 'analysis_sources', cancelled)
                    manifest = (_salary_manifest if kind == 'salary' else _results_manifest)(snapshot, cancelled)
                    import_id = None
                    if kind == 'results':
                        old = conn.execute("SELECT import_id FROM historical_imports WHERE file_sha256=? AND notes IN ('ok','field_only')", (digest,)).fetchone()
                        if old:
                            import_id = old[0]
                            result['duplicates_skipped'] += 1
                        else:
                            imported = import_historical_result_csvs([str(snapshot)], username=username, db_path=database,
                                archive_files=False, progress_callback=progress, cancel_callback=cancelled, atomic_file=True)
                            if imported['cancelled']:
                                raise ImportCancelled()
                            if imported['errors']:
                                raise ValueError('; '.join(imported['errors']))
                            saved = conn.execute("SELECT import_id FROM historical_imports WHERE file_sha256=? AND notes IN ('ok','field_only')", (digest,)).fetchone()
                            if not saved:
                                raise ValueError('Results were not saved; retry the import.')
                            import_id = saved[0]
                            result['results_imported'] += 1
                            result['personal_results_added'] += imported['personal_results_added']
                            result['analysis_import_ids'].append(import_id)
                    _check(cancelled)
                    with conn:
                        conn.execute('INSERT INTO analysis_sources(hash,kind,snapshot,original_name,source_path,manifest,import_id) VALUES(?,?,?,?,?,?,?)',
                            (digest, kind, str(snapshot), path.name, str(path), json.dumps(manifest), import_id))
                    if kind == 'salary':
                        result['salaries_imported'] += 1
                except ImportCancelled:
                    raise
                except Exception as exc:
                    result['errors'].append(f'{path.name}: {exc}')
            sources = _sources(conn)
            paired = {row[0] for row in conn.execute('SELECT result_hash FROM analysis_salary_pairs')}
            for source in (s for s in sources if s['kind'] == 'results' and s['hash'] not in paired):
                _check(cancelled)
                compatible = [s for s in sources if s['kind'] == 'salary' and qualify_pair(source, s)['compatible']]
                if len(compatible) == 1 and qualify_pair(source, compatible[0])['automatic']:
                    try:
                        progress(0, 0, 'Validating salary match: ' + source['name'])
                        result['pairs_added'] += int(save_pair(source['hash'], compatible[0]['hash'], db_path=database, cancelled=cancelled, automatic=True))
                    except ImportCancelled:
                        raise
                    except Exception as exc:
                        result['errors'].append(str(exc))
            result['unpaired'] = conn.execute("SELECT COUNT(*) FROM analysis_sources s WHERE kind='results' AND NOT EXISTS(SELECT 1 FROM analysis_salary_pairs p WHERE p.result_hash=s.hash)").fetchone()[0]
        except ImportCancelled:
            result['cancelled'] = True
    return result


def paired_metadata(conn, import_id, cancelled=lambda: False):
    """Qualified salary consumer; raw sources stay intact, no ownership defaults."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='analysis_salary_pairs'").fetchone():
        return {}, None
    row = conn.execute('''SELECT s.hash,p.result_hash FROM analysis_salary_pairs p
                          JOIN analysis_sources r ON r.hash=p.result_hash
                          JOIN analysis_sources s ON s.hash=p.salary_hash WHERE r.import_id=?''', (import_id,)).fetchone()
    if not row:
        return {}, None
    sources = {s['hash']: s for s in _sources(conn)}
    salary, result = sources[row[0]], sources[row[1]]
    _verify(salary, cancelled)
    _verify(result, cancelled)
    q = qualify_pair(result, salary)
    if not q['compatible']:
        raise ValueError('Saved salary pairing no longer qualifies: ' + q['reason'])
    from learning_db import _normalize_roster_token
    metadata, collisions = {}, set()
    for (role, name, player_id), p in q['matches'].items():
        key = ('@cpt:' if role == 'CPT' else '') + _normalize_roster_token(p['name'])
        value = dict(name=p['name'], team=p['team'], opponent=p['opponent'], position=p['position'], salary=p['salary'], salary_known=True)
        if key in metadata and metadata[key] != value:
            collisions.add(key)
        metadata[key] = value
    for key in collisions:
        metadata.pop(key, None)
    return metadata, dict(salary_hash=salary['hash'], result_hash=result['hash'], source='explicit saved salary/results pair')


def report_lines(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='analysis_sources'").fetchone():
        return []
    counts = dict(conn.execute('SELECT kind,COUNT(*) FROM analysis_sources GROUP BY kind'))
    pairs = conn.execute('SELECT COUNT(*) FROM analysis_salary_pairs').fetchone()[0]
    return ['', 'Saved salary / results sources',
            f"- Results cataloged: {counts.get('results', 0)}; salary snapshots: {counts.get('salary', 0)}; contest pairings: {pairs}.",
            f"- Results awaiting a salary match: {counts.get('results', 0) - pairs}. Use Review Salary Matches to inspect candidates.",
            '- Pairing validates observed roster identities and source revisions; full eligible-pool coverage and payouts remain unverified.']
