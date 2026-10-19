"""Persistent discovery metadata, never evidence or permission to reuse a build."""
from contextlib import contextmanager
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from data_io import read_players_csv

VERSION = 1


@contextmanager
def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=2)
    con.execute('CREATE TABLE IF NOT EXISTS salary_files (path TEXT PRIMARY KEY, signature TEXT, digest TEXT, metadata TEXT, error TEXT, present INTEGER)')
    con.execute('CREATE TABLE IF NOT EXISTS slate_preparation (digest TEXT PRIMARY KEY, payload TEXT)')
    try:
        with con:
            yield con
    finally:
        con.close()


def signature(path):
    s = path.stat()
    return json.dumps([VERSION, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_dev, s.st_ino])


def describe(players):
    from csv_loading import detect_mode, detect_sport
    dates = []
    for p in players:
        match = re.search(r'\b(\d{2}/\d{2}/\d{4})\b', str(p.get('GameInfo', '')))
        if not match:
            return dict(sport=detect_sport(players), kind=detect_mode(players), date='', label='Date unknown', players=len(players))
        try:
            dates.append(dt.datetime.strptime(match[1], '%m/%d/%Y').date().isoformat())
        except ValueError:
            return dict(sport=detect_sport(players), kind=detect_mode(players), date='', label='Date unknown', players=len(players))
    games = sorted({p.get('GameKey') for p in players if p.get('GameKey')})
    sport, kind = detect_sport(players), detect_mode(players)
    label = ', '.join(games[:3]) + (f' +{len(games)-3} games' if len(games)>3 else '')
    return dict(sport=sport, kind=kind, date=min(dates), label=label or sport, players=len(players))


def scan(database, folder, cancelled=lambda: False, progress=lambda text: None):
    root = Path(folder).resolve()
    if not root.is_dir():
        raise ValueError('Choose an available Salary Files folder in Data and Learning.')
    checked = reused = 0
    with connect(database) as con:
        # Do not mark absent until traversal succeeds. Cancellation retains the last catalog.
        seen = set()
        for path in root.rglob('*'):
            if cancelled():
                return dict(cancelled=True, checked=checked, reused=reused)
            if path.suffix.lower() != '.csv' or path.is_symlink() or not path.is_file():
                continue
            resolved = path.resolve()
            if root not in resolved.parents:
                continue
            key = str(resolved)
            seen.add(key)
            before = signature(resolved)
            old = con.execute('SELECT signature FROM salary_files WHERE path=?', (key,)).fetchone()
            if old and old[0] == before:
                con.execute('UPDATE salary_files SET present=1 WHERE path=?', (key,))
                reused += 1
                continue
            digest, metadata, error = '', {}, ''
            try:
                h = hashlib.sha256()
                with resolved.open('rb') as source:
                    while True:
                        if cancelled():
                            return dict(cancelled=True, checked=checked, reused=reused)
                        block = source.read(1024*1024)
                        if not block: break
                        h.update(block)
                digest = h.hexdigest()
                duplicate = con.execute('SELECT metadata FROM salary_files WHERE digest=? AND error=? AND signature LIKE ? LIMIT 1', (digest, '', '['+str(VERSION)+',%')).fetchone()
                metadata = json.loads(duplicate[0]) if duplicate else describe(read_players_csv(str(resolved)))
                if signature(resolved) != before:
                    progress('File still changing; retry later: '+resolved.name)
                    continue
            except (OSError, ValueError, TypeError) as exc:
                error = str(exc)
            if cancelled():
                return dict(cancelled=True, checked=checked, reused=reused)
            if signature(resolved) != before:
                progress('File still changing; retry later: '+resolved.name)
                continue
            con.execute('INSERT OR REPLACE INTO salary_files VALUES (?,?,?,?,?,1)',
                        (key, before, digest, json.dumps(metadata), error))
            checked += 1
            progress('Catalogued '+resolved.name)
        for (key,) in con.execute('SELECT path FROM salary_files').fetchall():
            if root in Path(key).parents and key not in seen:
                con.execute('UPDATE salary_files SET present=0 WHERE path=?', (key,))
    return dict(checked=checked, reused=reused)


def rows(database, folder, *, today=None, all_dates=False):
    if not Path(database).exists() or not folder:
        return []
    today = today or dt.date.today().isoformat()
    root = Path(folder).resolve()
    result, digests = [], set()
    with connect(database) as con:
        stored = con.execute('SELECT path,digest,metadata,error FROM salary_files WHERE present=1 ORDER BY path').fetchall()
        preparations = dict(con.execute('SELECT digest,payload FROM slate_preparation'))
    for path, digest, raw, error in stored:
        if root not in Path(path).parents or error or digest in digests:
            continue
        meta = json.loads(raw)
        if not all_dates and (not meta.get('date') or meta['date'] < today):
            continue
        digests.add(digest)
        result.append(dict(meta, path=path, digest=digest, preparation=json.loads(preparations.get(digest,'{}'))))
    return sorted(result, key=lambda row: (row['date'] or '9999', row['label'], row['path']))


def record_preparation(database, result):
    if result.get('digest'):
        with connect(database) as con:
            con.execute('INSERT OR REPLACE INTO slate_preparation VALUES (?,?)', (result['digest'],json.dumps(result)))
