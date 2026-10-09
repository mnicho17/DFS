"""Transactional candidate libraries for bounded, resumable NFL searches."""
from contextlib import contextmanager
from compute_ledger import instrument_library, instrument_search

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
CLASSIC_ROSTER_FORMAT = 'nfl-classic-player-keys-v1'

def candidate_generation_id(snapshot):
    """Objective-neutral compatibility; the full input ID remains provenance.

    Recorded objective and the explicit zero-QB portfolio maximum are excluded;
    neither changes candidate generation. All other player, salary, game, roster,
    rules, calibration and recipe inputs participate in this fingerprint.
    """
    inputs = validate_snapshot(snapshot)['inputs']
    inputs["recipe"].pop("max_zero_qb_pct", None)
    inputs["rules"].pop("max_zero_qb_pct", None)
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
        con.execute('CREATE TABLE IF NOT EXISTS coverage_batches (batch INTEGER PRIMARY KEY, quarterback TEXT NOT NULL)')
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
            if snapshot['inputs']['recipe']['contest_kind'] == 'classic':
                con.execute('INSERT INTO library_meta VALUES (?,?)', ('roster_format', CLASSIC_ROSTER_FORMAT))

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


def classic_expansion_pool(players, index):
    """Interleave general search with QB-focused batches; retain all manual flags."""
    quarterbacks=sorted((p for p in players if str(p.get('Position') or '').upper() == 'QB'
                         and not unavailable(p) and not p.get('FadeFlex')),key=player_key)
    if any(p.get('LockFlex') for p in quarterbacks):
        return players, None, index % len(STYLES), len(STYLES)
    width=len(quarterbacks)+1
    offset=index % width
    style=(index // width) % len(STYLES)
    if offset == 0:
        return players, None, style, width*len(STYLES)
    target=player_key(quarterbacks[offset-1])
    pool=copy.deepcopy(players)
    for player in pool:
        if str(player.get('Position') or '').upper() == 'QB' and player_key(player) != target:
            player['FadeFlex']=True
    return pool, target, style, width*len(STYLES)


def valid_classic_candidate(row, players, cap, strategy, rules):
    """Check a generated roster against the frozen search pool before checkpointing."""
    keys=roster_keys(row,'classic')
    lookup={player_key(p):p for p in players}
    if len(keys) != 9 or len(set(keys)) != 9 or any(key not in lookup for key in keys):
        return False
    roster=[lookup[key] for key in keys]
    if not lineup_is_complete_for_sport(roster,'NFL'):
        return False
    if any(unavailable(p) or p.get('FadeFlex') or p.get('NFLQBEligible') is False for p in roster):
        return False
    if not {player_key(p) for p in players if p.get('LockFlex')}.issubset(keys):
        return False
    if not _group_ok(set(keys),normalize_rules(rules)['groups']):
        return False
    salaries=[float(p.get('FlexSalary') or 0) for p in roster]
    floor=(_salary_floor_for_strategy(cap,strategy,'NFL')
           if any(v in strategy.lower() for v in ('near','max')) else 0)
    return (all(math.isfinite(s) and s > 0 for s in salaries) and floor <= sum(salaries) <= cap
            and len({p.get('Team') for p in roster}) >= 2)

@instrument_search
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
        from compute_ledger import safe, resumed
        saved_keys=[json.loads(row[0]) for row in con.execute('SELECT roster FROM candidates')]
        safe(resumed, saved_keys, con.execute('SELECT count(*) FROM batches').fetchone()[0])
        exclusions=({(keys[0],tuple(keys[1:])) for keys in saved_keys} if kind=='showdown'
                    else {tuple(keys) for keys in saved_keys})
        stagnant=0
        while not stop() and count < candidate_limit:
            batch_players=players; target=None; cycle=len(STYLES); style_index=index % len(STYLES)
            expanded=kind == 'classic' and recipe.get('classic_coverage_expansion')
            if expanded:
                batch_players,target,style_index,cycle=classic_expansion_pool(players,index)
            style=STYLES[style_index]; seed=1337+index*104729
            start=time.monotonic()
            focus=f'; Quarterback coverage: {target}' if target else ''
            progress(f'Batch {index+1}: {style}{focus}; {count:,}/{candidate_limit:,} saved candidates')
            kwargs=dict(salary_cap=cap,seed=seed,own_mode=recipe.get('ownership_mode','Balanced'),
                        own_weight=float(recipe.get('ownership_weight') or 0),build_style=style)
            opt=ShowdownOptimizer(players,**kwargs) if kind=='showdown' else MultiSportClassicOptimizer(
                batch_players,sport='NFL',salary_strategy=recipe.get('salary_strategy','Near Cap'),**kwargs)
            # Short batches bound lost work on power failure. Repeated seeds are avoided on resume.
            exclusion_args=({'excluded_signatures':exclusions} if kind=='showdown'
                            else {'exact_excluded_signatures':exclusions})
            rows=opt.build_lineups(num_lineups=min(batch_size,25 if target else batch_size,candidate_limit-count),cancel_callback=stop,
                                  **exclusion_args)
            added=0
            with con:
                for row in rows:
                    if count+added >= candidate_limit:break
                    if expanded and not valid_classic_candidate(row,batch_players,cap,
                            recipe.get('salary_strategy','Near Cap'),inputs['rules']):
                        continue
                    keys=roster_keys(row,kind)
                    encoded=json.dumps(keys,separators=(',',':'))
                    added+=con.execute('INSERT OR IGNORE INTO candidates VALUES (?,?,?,?,?)',
                                (encoded,encoded,index,style,seed)).rowcount
                    exclusions.add((keys[0],tuple(keys[1:])) if kind=='showdown' else tuple(keys))
                con.execute('INSERT INTO batches VALUES (?,?,?,?,?)',(index,style,seed,added,time.monotonic()-start))
                if target:
                    con.execute('INSERT INTO coverage_batches VALUES (?,?)',(index,target))
            count=con.execute('SELECT count(*) FROM candidates').fetchone()[0]
            index+=1
            stagnant=stagnant+1 if added==0 else 0
            if stagnant >= cycle:
                progress('Stopped after all scheduled pools and five styles added no new candidates. Saved work is retained; this does not prove the slate is exhausted.')
                break
    progress(f'Search paused/completed: {count:,} unique candidates saved. Load the library and build to simulate current outcomes.')
    return count

@instrument_library
def load_candidates(path, players, *, kind, salary_cap, salary_strategy='Near Cap', rules=None):
    meta=metadata(path)
    current_code=code_id()
    portable_classic = (kind == 'classic' and
        meta['snapshot']['inputs']['recipe']['contest_kind'] == 'classic' and
        meta.get('roster_format') == CLASSIC_ROSTER_FORMAT)
    if meta.get('roster_format') and not portable_classic:
        raise ValueError('Unsupported candidate roster format.')
    if not portable_classic and meta.get('code_id') != current_code:
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
                if roster[0].get('FadeCpt') or (captains and keys[0] not in captains) or keys[0] in locked:
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
    report=dict(saved=meta['count'],accepted=len(rows),rejected=rejected,input_id=meta['input_id'],
                code_changed=meta.get('code_id') != current_code)
    if kind == 'classic':
        report['coverage'] = classic_coverage(rows, list(lookup.values()))
    return rows,report


def classic_coverage(rows, players):
    """Describe accepted candidates, never impose portfolio or sampling quotas."""
    from collections import Counter
    from optimizers import _nfl_lineup_features
    quarterbacks=Counter()
    stacks=Counter()
    for row in rows:
        for player in row:
            if str(player.get('Position') or '').upper() == 'QB':
                quarterbacks[player_key(player)] += 1
        features=_nfl_lineup_features(row)
        stacks[f"QB+{features['qb_stack']} / BB{features['bringback']}"] += 1
    eligible={player_key(p):str(p.get('Name') or player_key(p)) for p in players
              if str(p.get('Position') or '').upper() == 'QB' and not unavailable(p)
              and not p.get('FadeFlex')}
    locked_qbs={player_key(p) for p in players if p.get('LockFlex')
                and str(p.get('Position') or '').upper() == 'QB'}
    if locked_qbs:
        eligible={key:name for key,name in eligible.items() if key in locked_qbs}
    missing=[dict(key=key,name=eligible[key]) for key in sorted(set(eligible)-set(quarterbacks))]
    return dict(quarterbacks=dict(sorted(quarterbacks.items())),
                stack_shapes=dict(sorted(stacks.items())),uncovered_quarterbacks=missing,
                exhaustive=False)


def coverage_text(report):
    coverage=report.get('coverage')
    if not coverage:
        return ''
    missing=coverage['uncovered_quarterbacks']
    return (f"Classic coverage: {len(coverage['quarterbacks'])} Quarterbacks; "
            f"{len(coverage['stack_shapes'])} stack shapes; {len(missing)} eligible Quarterbacks without candidates. "
            + ('Missing: '+', '.join(p['name'] for p in missing)+'. ' if missing else '')
            + 'This is a sampled library; coverage does not prove portfolio feasibility.')
