"""Resumable exhaustive roster storage; no forecasts or SIM scores are cached.

This is a backend foundation, separate from the existing searched libraries.
Current build constraints are applied on read, never frozen into preparation.
"""
from contextlib import closing
import copy
import json
import math
from pathlib import Path
import sqlite3
import struct
import time

from build_snapshots import fingerprint
from nfl_eligibility import apply_qb_eligibility, eligible_players, unavailable
from optimizers import ShowdownLineup
from portfolio_rules import player_key, normalize_rules, _group_ok

SCHEMA = 2
MAX_BYTES = 2 * 1024**3


def _number(value):
    try:
        if isinstance(value, bool):
            raise ValueError
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError('Salaries and salary caps must be finite and positive.') from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError('Salaries and salary caps must be finite and positive.')
    return result


def _structure(players, salary_cap):
    rows = []
    for p in players:
        if not p.get('FlexID') or not p.get('CptID') or not player_key(p):
            raise ValueError('Explicit FLEX and Captain identities are required.')
        rows.append(dict(key=player_key(p), flex_id=str(p['FlexID']), cpt_id=str(p['CptID']),
                         team=str(p.get('Team') or '').strip(),
                         game=str(p.get('GameInfo') or p.get('GameKey') or '').strip(),
                         flex_salary=_number(p.get('FlexSalary')), cpt_salary=_number(p.get('CptSalary'))))
    rows.sort(key=lambda r: r['key'])
    if not 6 <= len(rows) <= 64:
        raise ValueError('Preparation requires 6–64 Showdown players.')
    for field in ('key', 'flex_id', 'cpt_id'):
        if len({r[field] for r in rows}) != len(rows):
            raise ValueError('Duplicate or conflicting player identities; preparation withheld.')
    if len({r['team'] for r in rows}) != 2 or any(not r['team'] or not r['game'] for r in rows):
        raise ValueError('One two-team Showdown slate with explicit game information is required.')
    if len({r['game'] for r in rows}) != 1:
        raise ValueError('Mixed games or slate dates; preparation withheld.')
    return dict(schema=SCHEMA, salary_cap=_number(salary_cap), players=rows)


def _read(con):
    try:
        state = json.loads(con.execute('SELECT payload FROM preparation WHERE id=1').fetchone()[0])
        structure = state['structure']
        if structure['schema'] != SCHEMA or state['identity'] != fingerprint(structure):
            raise ValueError('Library structural identity is invalid.')
        n = len(structure['players'])
        cursor = state['cursor']
        if not isinstance(cursor, list) or len(cursor) != 2:
            raise ValueError('Library cursor is invalid.')
        captain, flex = cursor
        if type(captain) is not int or not 0 <= captain <= n:
            raise ValueError('Library cursor is invalid.')
        if captain < n and (len(flex) != 5 or any(type(i) is not int for i in flex)
                            or sorted(set(flex)) != flex or not 0 <= flex[0] <= flex[-1] < n-1):
            raise ValueError('Library cursor is invalid.')
        if state['complete'] != (captain == n):
            raise ValueError('Library completion marker is invalid.')
        expected = captain * math.comb(n-1, 5)
        if captain < n:
            previous = -1
            for i, index in enumerate(flex):
                expected += sum(math.comb(n-2-j, 4-i) for j in range(previous+1, index))
                previous = index
        if state['checked'] != expected or not 0 <= state['saved'] <= expected:
            raise ValueError('Library progress does not match its cursor.')
        if con.execute('SELECT count(*) FROM rosters').fetchone()[0] != state['saved']:
            raise ValueError('Library saved count is invalid.')
        return state
    except (sqlite3.Error, TypeError, KeyError, IndexError, json.JSONDecodeError) as exc:
        raise ValueError('Unsupported or damaged Showdown library.') from exc


def status(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as con:
        con.execute('BEGIN')
        state = _read(con)
        count = state['saved']
        return dict(identity=state['identity'], checked=state['checked'], saved=count,
                    complete=state['complete'], cursor=state['cursor'],
                    structural_total=len(state['structure']['players']) *
                    math.comb(len(state['structure']['players'])-1, 5))


def _advance(captain, flex, n):
    flex = list(flex)
    for i in range(4, -1, -1):
        if flex[i] < n - 6 + i:
            flex[i] += 1
            for j in range(i+1, 5):
                flex[j] = flex[j-1]+1
            return [captain, flex]
    return [captain+1, [0, 1, 2, 3, 4]]


def prepare(path, players, *, salary_cap=50000, seconds=3600, batch_size=2000,
            max_candidates=5_000_000, cancelled=lambda: False, progress=lambda value: None):
    """Atomically checkpoint rosters and the next combination; safe to resume.

    Preparation includes temporarily unavailable/faded players deliberately.
    Reading applies current eligibility, forecasts, locks, fades and groups.
    Limits pause preparation; only exhausting the cursor proves completion.
    """
    structure = _structure(players, salary_cap)
    if not math.isfinite(float(seconds)) or not 0 < seconds <= 12*3600:
        raise ValueError('Preparation time must be positive and at most 12 hours.')
    if type(batch_size) is not int or not 1 <= batch_size <= 10000:
        raise ValueError('Batch size must be 1–10,000.')
    if type(max_candidates) is not int or not 1 <= max_candidates <= 5_000_000:
        raise ValueError('Candidate limit must be 1–5,000,000.')
    path = Path(path)
    existed = path.exists()
    if existed:
        status(path)  # Reject unrelated databases before changing them.
    deadline = time.monotonic()+seconds
    with closing(sqlite3.connect(path, timeout=15)) as con:
        if not existed:
            with con:
                con.execute('CREATE TABLE preparation(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')
                con.execute('CREATE TABLE rosters(captain INTEGER NOT NULL, flex BLOB NOT NULL, PRIMARY KEY(captain,flex)) WITHOUT ROWID')
                state = dict(identity=fingerprint(structure), structure=structure,
                             cursor=[0, [0, 1, 2, 3, 4]], checked=0, saved=0, complete=False)
                con.execute('INSERT INTO preparation VALUES (1,?)', (json.dumps(state),))
        state = _read(con)
        if state['structure'] != structure:
            raise ValueError('Salary, player identity or slate changed; use a new library.')
        rows = structure['players']; n = len(rows)
        pending = []; since_commit = 0
        committed_payload = json.dumps(state)

        def commit():
            nonlocal since_commit, committed_payload
            payload = json.dumps(state)
            with con:
                con.executemany('INSERT INTO rosters VALUES (?,?)', pending)
                updated = con.execute('UPDATE preparation SET payload=? WHERE id=1 AND payload=?',
                                      (payload, committed_payload)).rowcount
                if updated != 1:
                    raise ValueError('Another preparation changed this library; resume with one writer.')
            committed_payload = payload
            pending.clear(); since_commit = 0
            progress(dict(checked=state['checked'], saved=state['saved'], complete=state['complete']))

        while not state['complete']:
            if cancelled() or time.monotonic() >= deadline or state['saved'] >= max_candidates:
                break
            captain, indices = state['cursor']
            pool = [i for i in range(n) if i != captain]
            flex = [pool[i] for i in indices]
            salary = rows[captain]['cpt_salary'] + sum(rows[i]['flex_salary'] for i in flex)
            if salary <= structure['salary_cap'] and len({rows[i]['team'] for i in [captain, *flex]}) == 2:
                pending.append((captain, struct.pack('<5H', *flex)))
                state['saved'] += 1
            state['checked'] += 1; since_commit += 1
            state['cursor'] = _advance(captain, indices, n)
            state['complete'] = state['cursor'][0] == n
            if since_commit >= batch_size:
                commit()
                size = con.execute('PRAGMA page_count').fetchone()[0] * con.execute('PRAGMA page_size').fetchone()[0]
                if size >= MAX_BYTES:
                    break
        if since_commit:
            commit()
    result = status(path)
    result['cancelled'] = bool(cancelled())
    return result


def iter_candidates(path, players, *, salary_cap=50000, salary_floor=0, rules=None,
                    allow_partial=False, cancelled=lambda: False):
    """Stream fresh player objects; never reuse stored forecasts or SIM outputs."""
    current = copy.deepcopy(players)
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as con:
        con.execute('BEGIN')
        state = _read(con)
        if not state['complete'] and not allow_partial:
            raise ValueError('Library is incomplete. Resume preparation or explicitly allow partial coverage.')
        if _structure(current, state['structure']['salary_cap']) != state['structure']:
            raise ValueError('Salary, player identity or slate changed; use a new library.')
        cap = _number(salary_cap)
        if cap > state['structure']['salary_cap'] or not math.isfinite(float(salary_floor)) or not 0 <= salary_floor <= cap:
            raise ValueError('Requested salary range is outside the prepared library.')
        eligible = eligible_players(apply_qb_eligibility(current))
        lookup = {player_key(p): p for p in eligible if not unavailable(p)}
        locked = {player_key(p) for p in current if p.get('LockFlex')}
        captains = {player_key(p) for p in current if p.get('LockCpt')}
        groups = normalize_rules(rules)['groups']
        prepared = state['structure']['players']
        requested = [i for i,p in enumerate(prepared) if p['key'] in lookup and
                     (not captains or p['key'] in captains)]
        if not requested:
            return
        query = ('SELECT captain,flex FROM rosters WHERE captain IN (' +
                 ','.join('?' for _ in requested) + ') ORDER BY captain,flex')
        for captain_index, encoded in con.execute(query, requested):
            if cancelled():
                return
            try:
                indices = list(struct.unpack('<5H', encoded))
            except (struct.error, TypeError) as exc:
                raise ValueError('Library roster identity is damaged.') from exc
            if sorted(set(indices)) != indices or captain_index in indices or indices[-1] >= len(prepared):
                raise ValueError('Library roster identity is damaged.')
            keys = [prepared[captain_index]['key'], *(prepared[i]['key'] for i in indices)]
            if any(k not in lookup for k in keys):
                continue
            cpt, *flex = [lookup[k] for k in keys]
            if cpt.get('FadeCpt') or keys[0] in locked or (captains and keys[0] not in captains):
                continue
            if any(p.get('FadeFlex') for p in flex) or not locked.issubset(set(keys[1:])) or not _group_ok(set(keys), groups):
                continue
            salary = float(cpt['CptSalary']) + sum(float(p['FlexSalary']) for p in flex)
            if salary_floor <= salary <= cap and len({p['Team'] for p in [cpt, *flex]}) == 2:
                yield ShowdownLineup(cpt, flex)
