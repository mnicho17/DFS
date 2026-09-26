"""Transactional candidate libraries for bounded, resumable NFL searches."""
from contextlib import contextmanager
import copy
import hashlib
import json
import math
import sqlite3
import sys
import time
from pathlib import Path
from build_snapshots import validate_snapshot, fingerprint
from nfl_eligibility import apply_qb_eligibility, eligible_players, unavailable
from portfolio_rules import player_key, _group_ok, normalize_rules
from optimizers import (ShowdownOptimizer, ShowdownLineup, MultiSportClassicOptimizer,
    lineup_is_complete_for_sport, _salary_floor_for_strategy)

STYLES = ('Strategic', 'Balanced', 'Contrarian', 'Chalk', 'Randomized')
MAX_CANDIDATES = 100000

def candidate_generation_id(snapshot):
    """Objective-neutral compatibility; the full input ID remains provenance.

    Only recorded intent is excluded. All player, salary, game, roster, rules,
    calibration and other recipe inputs still participate in this fingerprint.
    """
    inputs = validate_snapshot(snapshot)['inputs']
    inputs['recipe'].pop('contest_objective', None)
    inputs['contest'].pop('objective', None)
    inputs['contest'].pop('contest_objective', None)
    return fingerprint(inputs)

def code_id():
    if getattr(sys, 'frozen', False):
        digest=hashlib.sha256()
        with open(sys.executable,'rb') as handle:
            for chunk in iter(lambda:handle.read(1024*1024),b''):
                digest.update(chunk)
        return digest.hexdigest()
    root = Path(__file__).resolve().parent
    return fingerprint({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in sorted(root.glob('*.py')) if not p.name.startswith('test_')})

def slate_id(players, kind):
    # Exact slate IDs/game dates prevent accidental reuse across contests/weeks.
    return fingerprint(dict(kind=kind, players=sorted((player_key(p), str(p.get('CptID') or ''),
        str(p.get('GameInfo') or p.get('GameKey') or ''), str(p.get('Team') or '')) for p in players)))

@contextmanager
def connect(path):
    con = sqlite3.connect(str(path), timeout=15)
    con.execute('PRAGMA busy_timeout=15000')
    try:
        with con:
            yield con
    finally:
        con.close()

def initialize(path, snapshot):
    snapshot = validate_snapshot(snapshot)
    if Path(path).exists():
        metadata(path)  # Do not add tables to an unrelated existing database.
    with connect(path) as con:
        con.execute('CREATE TABLE IF NOT EXISTS library_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        con.execute('CREATE TABLE IF NOT EXISTS candidates (signature TEXT PRIMARY KEY, roster TEXT NOT NULL, batch INTEGER NOT NULL, style TEXT NOT NULL, seed INTEGER NOT NULL)')
        con.execute('CREATE TABLE IF NOT EXISTS batches (id INTEGER PRIMARY KEY, style TEXT NOT NULL, seed INTEGER NOT NULL, count INTEGER NOT NULL, elapsed REAL NOT NULL)')
        meta = dict(con.execute('SELECT key,value FROM library_meta'))
        if meta:
            saved = metadata(path)
            if (saved['candidate_generation_id'] != candidate_generation_id(snapshot)
                    or saved.get('code_id') != code_id()
                    or saved['slate_id'] != slate_id(snapshot['inputs']['players'], snapshot['inputs']['recipe']['contest_kind'])):
                raise ValueError('This library uses different inputs or app code. Resume using the original version, or start a new library.')
        else:
            con.executemany('INSERT INTO library_meta VALUES (?,?)', dict(schema='1',input_id=snapshot['input_id'],
                candidate_generation_id=candidate_generation_id(snapshot),
                code_id=code_id(),snapshot=json.dumps(snapshot),
                slate_id=slate_id(snapshot['inputs']['players'],snapshot['inputs']['recipe']['contest_kind'])).items())

def metadata(path):
    if not Path(path).is_file():
        raise ValueError('Candidate library was not found.')
    with connect(path) as con:
        meta = dict(con.execute('SELECT key,value FROM library_meta'))
        if meta.get('schema') != '1':
            raise ValueError('Unsupported candidate library.')
        meta['count'] = con.execute('SELECT count(*) FROM candidates').fetchone()[0]
        meta['batches'] = con.execute('SELECT count(*) FROM batches').fetchone()[0]
        meta['snapshot'] = validate_snapshot(json.loads(meta['snapshot']))
        original = meta['snapshot']
        generation_id = candidate_generation_id(original)
        if (meta.get('input_id') != original['input_id']
                or meta.get('candidate_generation_id', generation_id) != generation_id
                or meta.get('slate_id') != slate_id(original['inputs']['players'], original['inputs']['recipe']['contest_kind'])):
            raise ValueError('Candidate library identity does not match its original snapshot.')
        # Derive old metadata without modifying the database or original snapshot.
        # initialize/load still require exactly the same code ID.
        meta['candidate_generation_id'] = generation_id
        return meta

def roster_keys(lineup, kind):
    if kind == 'showdown':
        return [player_key(lineup['Captain'])] + sorted(player_key(p) for p in lineup['Flex'])
    return sorted(player_key(p) for p in lineup)

def run_search(path, snapshot, *, seconds=3600, cancelled=lambda:False, progress=lambda text:None, batch_size=200,
               candidate_limit=MAX_CANDIDATES):
    if not math.isfinite(float(seconds)) or not 0 < float(seconds) <= 12*3600:
        raise ValueError('Search time must be greater than zero and at most 12 hours.')
    if isinstance(candidate_limit, bool) or int(candidate_limit) != candidate_limit or not 1 <= candidate_limit <= MAX_CANDIDATES:
        raise ValueError('Candidate target must be between 1 and 100,000.')
    if isinstance(batch_size, bool) or int(batch_size) != batch_size or not 1 <= batch_size <= 1000:
        raise ValueError('Batch size must be between 1 and 1,000.')
    initialize(path,snapshot)
    inputs = snapshot['inputs']; recipe=inputs['recipe']; kind=recipe['contest_kind']
    players = eligible_players(apply_qb_eligibility(copy.deepcopy(inputs['players'])))
    cap=float(recipe.get('salary_cap') or 50000)
    deadline=time.monotonic()+max(1,float(seconds))
    stop=lambda: cancelled() or time.monotonic() >= deadline
    with connect(path) as con:
        index=con.execute('SELECT COALESCE(MAX(id),-1)+1 FROM batches').fetchone()[0]
        count=con.execute('SELECT count(*) FROM candidates').fetchone()[0]
        saved_keys=[json.loads(row[0]) for row in con.execute('SELECT roster FROM candidates')]
        exclusions=({(keys[0],tuple(keys[1:])) for keys in saved_keys} if kind=='showdown'
                    else {tuple(keys) for keys in saved_keys})
        stagnant=0
        while not stop() and count < candidate_limit:
            style=STYLES[index % len(STYLES)]; seed=1337+index*104729
            start=time.monotonic()
            progress(f'Batch {index+1}: {style}; {count:,}/{candidate_limit:,} saved candidates')
            kwargs=dict(salary_cap=cap,seed=seed,own_mode=recipe.get('ownership_mode','Balanced'),
                        own_weight=float(recipe.get('ownership_weight') or 0),build_style=style)
            opt=ShowdownOptimizer(players,**kwargs) if kind=='showdown' else MultiSportClassicOptimizer(
                players,sport='NFL',salary_strategy=recipe.get('salary_strategy','Near Cap'),**kwargs)
            # Short batches bound lost work on power failure. Repeated seeds are avoided on resume.
            exclusion_args=({'excluded_signatures':exclusions} if kind=='showdown'
                            else {'exact_excluded_signatures':exclusions})
            rows=opt.build_lineups(num_lineups=min(batch_size,candidate_limit-count),cancel_callback=stop,
                                  **exclusion_args)
            added=0
            with con:
                for row in rows:
                    if count+added >= candidate_limit:break
                    keys=roster_keys(row,kind)
                    encoded=json.dumps(keys,separators=(',',':'))
                    added+=con.execute('INSERT OR IGNORE INTO candidates VALUES (?,?,?,?,?)',
                                (encoded,encoded,index,style,seed)).rowcount
                    exclusions.add((keys[0],tuple(keys[1:])) if kind=='showdown' else tuple(keys))
                con.execute('INSERT INTO batches VALUES (?,?,?,?,?)',(index,style,seed,added,time.monotonic()-start))
            count=con.execute('SELECT count(*) FROM candidates').fetchone()[0]
            index+=1
            stagnant=stagnant+1 if added==0 else 0
            if stagnant >= len(STYLES):
                progress('Stopped after five styles added no new candidates. Saved work is retained; this does not prove the slate is exhausted.')
                break
    progress(f'Search paused/completed: {count:,} unique candidates saved. Load the library and build to simulate current outcomes.')
    return count

def load_candidates(path, players, *, kind, salary_cap, salary_strategy='Near Cap', rules=None):
    meta=metadata(path)
    if meta.get('code_id') != code_id():
        raise ValueError('This library uses different app code. Use the original version, or start a new library.')
    if slate_id(players,kind) != meta['slate_id']:
        raise ValueError('Candidate library belongs to a different player slate or contest type. Load the matching slate first.')
    current=apply_qb_eligibility(copy.deepcopy(players))
    lookup={player_key(p):p for p in eligible_players(current)}
    locked={player_key(p) for p in current if p.get('LockFlex')}
    captains={player_key(p) for p in current if p.get('LockCpt')}
    groups=normalize_rules(rules)['groups']
    rows=[]; rejected=0
    with connect(path) as con:
        for (encoded,) in con.execute('SELECT roster FROM candidates ORDER BY signature LIMIT ?', (MAX_CANDIDATES,)):
            keys=json.loads(encoded)
            if len(keys) != (6 if kind=='showdown' else 9) or len(set(keys)) != len(keys) or any(k not in lookup for k in keys):
                rejected+=1;continue
            roster=[lookup[k] for k in keys]
            if any(unavailable(p) or p.get('FadeFlex') for p in roster) or not locked.issubset(set(keys)) or not _group_ok(set(keys),groups):
                rejected+=1;continue
            if kind=='showdown':
                if roster[0].get('FadeCpt') or (captains and captains != {keys[0]}) or keys[0] in locked:
                    rejected+=1;continue
                salary=float(roster[0].get('CptSalary') or 0)+sum(float(p.get('FlexSalary') or 0) for p in roster[1:])
                if len({p.get('Team') for p in roster}) != 2:
                    rejected+=1;continue
                lineup=ShowdownLineup(roster[0],roster[1:])
            else:
                salary=sum(float(p.get('FlexSalary') or 0) for p in roster)
                if not lineup_is_complete_for_sport(roster,'NFL'):
                    rejected+=1;continue
                lineup=roster
            floor=(_salary_floor_for_strategy(salary_cap,salary_strategy,'NFL')
                   if kind == 'classic' and any(v in salary_strategy.lower() for v in ('near', 'max')) else 0)
            if salary > salary_cap or salary < floor or any(float(p.get('FlexSalary') or 0)<=0 for p in roster):
                rejected+=1;continue
            rows.append(lineup)
    if not rows:
        raise ValueError('No saved candidates satisfy the current player status, salary and lineup rules. Clear the library to generate fresh candidates.')
    return rows,dict(saved=meta['count'],accepted=len(rows),rejected=rejected,input_id=meta['input_id'])
