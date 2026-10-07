"""Resumable exhaustive roster storage; no forecasts or SIM scores are cached.

This is a backend foundation, separate from the existing searched libraries.
Current build constraints are applied on read, never frozen into preparation.
"""
from contextlib import closing
import copy
import json
import math
import random
import hashlib
from pathlib import Path
import sqlite3
import struct
import time

from build_snapshots import fingerprint
from nfl_eligibility import apply_qb_eligibility, eligible_players, unavailable
from optimizers import ShowdownLineup
from portfolio_rules import player_key, normalize_rules, _group_ok

SCHEMA = 3
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


def _structure(players, salary_cap, captain_keys=None):
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
    keys={r['key'] for r in rows}
    scope=sorted(keys if captain_keys is None else set(captain_keys))
    if not scope or not set(scope).issubset(keys):
        raise ValueError('Prepared Captain identities must belong to this slate.')
    return dict(schema=SCHEMA,salary_cap=_number(salary_cap),players=rows,captains=scope)


def _read(con, *, verify_count=True):
    try:
        state = json.loads(con.execute('SELECT payload FROM preparation WHERE id=1').fetchone()[0])
        structure = state['structure']
        if structure['schema'] != SCHEMA or state['identity'] != fingerprint(structure):
            raise ValueError('Library structural identity is invalid.')
        n = len(structure['players'])
        count=len(structure['captains'])
        cursor = state['cursor']
        if not isinstance(cursor, list) or len(cursor) != 2:
            raise ValueError('Library cursor is invalid.')
        captain, flex = cursor
        if type(captain) is not int or not 0 <= captain <= count:
            raise ValueError('Library cursor is invalid.')
        if captain < count and (len(flex) != 5 or any(type(i) is not int for i in flex)
                            or sorted(set(flex)) != flex or not 0 <= flex[0] <= flex[-1] < n-1):
            raise ValueError('Library cursor is invalid.')
        if state['complete'] != (captain == count):
            raise ValueError('Library completion marker is invalid.')
        expected = captain * math.comb(n-1, 5)
        if captain < count:
            previous = -1
            for i, index in enumerate(flex):
                expected += sum(math.comb(n-2-j, 4-i) for j in range(previous+1, index))
                previous = index
        if state['checked'] != expected or not 0 <= state['saved'] <= expected:
            raise ValueError('Library progress does not match its cursor.')
        if verify_count and con.execute('SELECT count(*) FROM rosters').fetchone()[0] != state['saved']:
            raise ValueError('Library saved count is invalid.')
        return state
    except (sqlite3.Error, TypeError, KeyError, IndexError, json.JSONDecodeError) as exc:
        raise ValueError('Unsupported or damaged Showdown library.') from exc


def status(path):
    if Path(path).suffix=='.sdfull':
        from showdown_full_library import status as full_status
        return full_status(path)
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as con:
        con.execute('BEGIN')
        state = _read(con)
        count = state['saved']
        return dict(identity=state['identity'], checked=state['checked'], saved=count,
                    complete=state['complete'], cursor=state['cursor'],
                    captains=state['structure']['captains'],
                    structural_total=len(state['structure']['captains']) *
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
            max_candidates=5_000_000, cancelled=lambda: False, progress=lambda value: None,
            captain_keys=None):
    """Atomically checkpoint rosters and the next combination; safe to resume.

    Preparation includes temporarily unavailable/faded players deliberately.
    Reading applies current eligibility, forecasts, locks, fades and groups.
    Limits pause preparation; only exhausting the cursor proves completion.
    """
    structure = _structure(players,salary_cap,captain_keys)
    if not math.isfinite(float(seconds)) or not 0 < seconds <= 12*3600:
        raise ValueError('Preparation time must be positive and at most 12 hours.')
    if type(batch_size) is not int or not 1 <= batch_size <= 10000:
        raise ValueError('Batch size must be 1–10,000.')
    if type(max_candidates) is not int or not 1 <= max_candidates <= 50_000_000:
        raise ValueError('Candidate limit must be 1–50,000,000.')
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
        captain_indices=[next(i for i,p in enumerate(rows) if p['key']==key) for key in structure['captains']]
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
            position, indices = state['cursor']
            captain=captain_indices[position]
            pool = [i for i in range(n) if i != captain]
            flex = [pool[i] for i in indices]
            salary = rows[captain]['cpt_salary'] + sum(rows[i]['flex_salary'] for i in flex)
            if salary <= structure['salary_cap'] and len({rows[i]['team'] for i in [captain, *flex]}) == 2:
                pending.append((captain, struct.pack('<5H', *flex)))
                state['saved'] += 1
            state['checked'] += 1; since_commit += 1
            state['cursor'] = _advance(position,indices,n)
            state['complete'] = state['cursor'][0] == len(captain_indices)
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
                    allow_partial=False, cancelled=lambda: False, captain_key=None,
                    _validated_info=None,_after_roster=None):
    """Stream fresh player objects; never reuse stored forecasts or SIM outputs."""
    if Path(path).suffix=='.sdfull':
        from showdown_full_library import iter_candidates as full_stream
        yield from full_stream(path,players,salary_cap=salary_cap,salary_floor=salary_floor,rules=rules,
            allow_partial=allow_partial,cancelled=cancelled,captain_key=captain_key,_validated_info=_validated_info,
            _after_roster=_after_roster)
        return
    current = copy.deepcopy(players)
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as con:
        con.execute('BEGIN')
        # Bounded loading counts the entire library before and after sampling.
        # Repeating that count for every Captain can exhaust short scan slices.
        state = _read(con, verify_count=_validated_info is None)
        if _validated_info is not None and dict(saved=state['saved'],complete=state['complete'],
                identity=state['identity'],captains=state['structure']['captains']) != _validated_info:
            raise ValueError('The library changed during loading. Pause preparation before building.')
        if not state['complete'] and not allow_partial:
            raise ValueError('Library is incomplete. Resume preparation or explicitly allow partial coverage.')
        if _structure(current,state['structure']['salary_cap'],state['structure']['captains']) != state['structure']:
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
                     p['key'] in state['structure']['captains'] and
                     (not captains or p['key'] in captains) and
                     (captain_key is None or p['key'] == captain_key)]
        if not requested:
            return
        query = ('SELECT captain,flex FROM rosters WHERE captain IN (' +
                 ','.join('?' for _ in requested) + ')')
        arguments=list(requested)
        if _after_roster is not None:
            indices_by_key={p['key']:i for i,p in enumerate(prepared)}
            try:
                after_cpt=indices_by_key[_after_roster[0]]
                after_flex=sorted(indices_by_key[k] for k in _after_roster[1])
                if len(after_flex)!=5 or len(set(after_flex))!=5 or after_cpt in after_flex:raise ValueError
                encoded_after=struct.pack('<5H',*after_flex)
            except (KeyError,IndexError,TypeError,ValueError) as exc:
                raise ValueError('Screening resume roster is invalid.') from exc
            query+=' AND (captain>? OR (captain=? AND flex>?))'
            arguments.extend((after_cpt,after_cpt,encoded_after))
        # Skip excluded players and salary-invalid prefixes inside SQLite, before
        # spending the bounded scan allowance constructing Python rosters.
        flex_values=[(struct.pack('<H',i),float(lookup[p['key']]['FlexSalary']))
                     for i,p in enumerate(prepared) if p['key'] in lookup and not lookup[p['key']].get('FadeFlex')]
        # Keep malformed records visible to the existing corruption checks.
        query+=' AND ((1=1'
        placeholders=','.join('?' for _ in flex_values) or 'NULL'
        for offset in (1,3,5,7,9):
            query+=f' AND substr(flex,{offset},2) IN ({placeholders})'
            arguments.extend(encoded for encoded,_ in flex_values)
        salary_terms=['CASE captain '+' '.join('WHEN ? THEN ?' for _ in requested)+' ELSE 0 END']
        for i in requested:arguments.extend((i,float(lookup[prepared[i]['key']]['CptSalary'])))
        for offset in (1,3,5,7,9):
            salary_terms.append(f'CASE substr(flex,{offset},2) '+
                (' '.join('WHEN ? THEN ?' for _ in flex_values) or 'WHEN NULL THEN 0')+' ELSE 0 END')
            for encoded,value in flex_values:arguments.extend((encoded,value))
        query+=' AND ('+salary_terms[0]+'+('+'+'.join(salary_terms[1:])+')) BETWEEN ? AND ?'
        arguments.extend((float(salary_floor),cap))
        malformed=["typeof(flex)!='blob'",'length(flex)!=10']
        malformed += [f'substr(flex,{offset},2)>=substr(flex,{offset+2},2)' for offset in (1,3,5,7)]
        malformed += [f'substr(flex,{offset+1},1)!=?' for offset in (1,3,5,7,9)]
        arguments.extend([b'\x00']*5)
        malformed.append('substr(flex,9,1)>=?');arguments.append(bytes([len(prepared)]))
        malformed += [f'substr(flex,{offset},2)=CAST(char(captain,0) AS BLOB)' for offset in (1,3,5,7,9)]
        query+=') OR '+' OR '.join(malformed)+')'
        query+=' ORDER BY captain,flex'
        # Filtered queries may inspect many rejected rows before yielding one.
        con.set_progress_handler(lambda: int(bool(cancelled())),1000)
        try:
            for captain_index, encoded in con.execute(query, arguments):
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
        except sqlite3.OperationalError as exc:
            if str(exc)=='interrupted' and cancelled():return
            raise
        finally:
            con.set_progress_handler(None,0)



def is_prepared_library(path):
    if not Path(path).is_file(): return False
    if Path(path).suffix=='.sdfull':
        from showdown_full_library import _read as read_full
        read_full(path)
        return True
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as con:
        return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='preparation'").fetchone() is not None


def validate_library(path,players,*,salary_cap=50000,allow_partial=False):
    if Path(path).suffix=='.sdfull':
        from showdown_full_library import validate as full_validate
        return full_validate(path,players,salary_cap=salary_cap,allow_partial=allow_partial)
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as con:
        con.execute('BEGIN');state=_read(con)
        if _structure(players,state['structure']['salary_cap'],state['structure']['captains']) != state['structure']:
            raise ValueError('Salary, player identity or slate changed; use a new library.')
        if _number(salary_cap)>state['structure']['salary_cap']:
            raise ValueError('Requested salary cap exceeds the prepared library.')
        if not state['complete'] and not allow_partial:
            raise ValueError('Library is incomplete; explicitly allow partial coverage or resume preparation.')
        return dict(saved=state['saved'],complete=state['complete'],identity=state['identity'],captains=state['structure']['captains'])


def load_bounded(path,players,*,limit=20000,seconds=30,salary_cap=50000,
                 salary_strategy='Near Cap',rules=None,allow_partial=False,
                 seed=73129,cancelled=lambda:False,progress=lambda text:None):
    """Bounded Captain-stratified reservoir sample; not full-library SIM."""
    started=time.monotonic()
    if type(limit) is not int or not 1<=limit<=20000:
        raise ValueError('Screening candidate budget must be 1–20,000.')
    if not math.isfinite(float(seconds)) or not 0<seconds<=60:
        raise ValueError('Library scan allowance must be positive and at most 60 seconds.')
    info=validate_library(path,players,salary_cap=salary_cap,allow_partial=allow_partial)
    from showdown_simulation import active_showdown_players,salary_floor
    from optimizers import attach_showdown_metrics
    current=active_showdown_players(copy.deepcopy(players))
    locked={player_key(p) for p in current if p.get('LockCpt')}
    captains=sorted(player_key(p) for p in current if not p.get('FadeCpt') and not p.get('LockFlex')
                    and (not locked or player_key(p) in locked))
    if not captains or limit<len(captains):
        raise ValueError('The screening budget must cover every eligible Captain.')
    if not set(captains).issubset(info['captains']):
        raise ValueError('This library does not cover the current Captain pool. Lock only prepared Captains or prepare a broader library.')
    if not info['complete']:
        # Preparation advances Captain by Captain. Explicit partial opt-in must
        # not silently discard Captains that have not been reached yet.
        if Path(path).suffix=='.sdfull':
            from showdown_full_library import states
            _,parts=states(path)
            missing=[key for key in captains if not parts.get(key) or not parts[key]['saved']]
        else:
            with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as con:
                state=_read(con,verify_count=False)
                indices={p['key']:i for i,p in enumerate(state['structure']['players'])}
                missing=[key for key in captains if con.execute(
                    'SELECT 1 FROM rosters WHERE captain=? LIMIT 1',(indices[key],)).fetchone() is None]
        if missing:
            raise ValueError(f'Partial library has no saved rosters for {len(missing)} current Captains '
                f'(including {", ".join(missing[:3])}). Resume preparation with a higher stored-roster limit, '
                'or explicitly lock a covered Captain pool. No Captain choices were changed.')
    output=[];coverage=[];scanned=0
    for index,key in enumerate(captains):
        quota=limit//len(captains)+int(index<limit%len(captains))
        deadline=None
        limited=[False]
        def stop():
            nonlocal deadline
            if cancelled(): return True
            now=time.monotonic()
            if deadline is None:
                deadline=now+seconds/len(captains)
            limited[0]=now>=deadline
            return limited[0]
        rng=random.Random(int.from_bytes(hashlib.sha256(f'{seed}:{key}'.encode()).digest()[:8],'big'))
        sample=[];seen=0
        for row in iter_candidates(path,players,salary_cap=salary_cap,
                salary_floor=salary_floor(salary_cap,salary_strategy),rules=rules,
                allow_partial=allow_partial,cancelled=stop,captain_key=key,_validated_info=info):
            seen+=1
            if len(sample)<quota: sample.append(row)
            else:
                replace=rng.randrange(seen)
                if replace<quota: sample[replace]=row
            if seen%1000==0:
                progress(f'Prepared library: {scanned+seen:,} valid rosters scanned; Captain {index+1}/{len(captains)}')
        if cancelled():
            raise InterruptedError('Prepared library loading cancelled; no candidates released.')
        output.extend(sample);scanned+=seen
        coverage.append(dict(captain=key,valid_seen=seen,sampled=len(sample),scan_complete=not limited[0]))
    if locked and any(not c['sampled'] for c in coverage):
        raise ValueError('A locked Captain has no sampled candidates. Resume preparation or broaden current rules; this is not proof of infeasibility.')
    if not output:
        raise ValueError('No prepared candidates passed current rules within the scan allowance. Broaden rules or increase the scan allowance; this is not proof of infeasibility.')
    latest=validate_library(path,players,salary_cap=salary_cap,allow_partial=allow_partial)
    if latest!=info:
        raise ValueError('The library changed during loading. Pause preparation before building.')
    report=dict(type='prepared_showdown',saved=info['saved'],accepted=len(output),
                preparation_complete=info['complete'],valid_seen=scanned,
                scan_complete=all(c['scan_complete'] for c in coverage),captain_sampling=coverage,
                sampling='Captain-balanced reservoir; no stored scores',seed=seed,
                loading_seconds=time.monotonic()-started)
    output=attach_showdown_metrics(output,salary_cap)
    for row in output:
        row.candidate_source='prepared_roster_library'
        row.sim_metrics['candidate_source']='prepared_roster_library'
    return output,report
