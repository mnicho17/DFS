"""Versioned, source-bound field history. No optimizer or forecast mutation."""
from collections import Counter, defaultdict
from contextlib import closing
from datetime import date
import json
import hashlib
import heapq
from pathlib import Path
import re
import sqlite3
import tempfile

import analysis_imports as ai
from opponent_analysis import username_key
from opponent_construction import analyze_saved_contest, saved_contests

VERSION = 2


def ensure_tables(conn):
    # All names are isolated from existing history. Migrations belong here.
    statements = [
        '''CREATE TABLE IF NOT EXISTS opponent_contests (
        contest_key TEXT PRIMARY KEY, platform TEXT NOT NULL, external_id TEXT,
        result_hash TEXT NOT NULL, salary_hash TEXT NOT NULL, name TEXT NOT NULL,
        format TEXT NOT NULL, start_date TEXT, end_date TEXT, field_size INTEGER,
        complete INTEGER NOT NULL, version INTEGER NOT NULL, audit_json TEXT NOT NULL,
        indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''',
        '''CREATE TABLE IF NOT EXISTS opponent_users (
        user_key TEXT PRIMARY KEY, platform TEXT NOT NULL, username TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS opponent_entries (
        contest_key TEXT NOT NULL REFERENCES opponent_contests(contest_key) ON DELETE CASCADE,
        entry_id TEXT NOT NULL, user_key TEXT NOT NULL REFERENCES opponent_users(user_key),
        rank INTEGER, points REAL, signature_json TEXT, metadata_complete INTEGER NOT NULL,
        PRIMARY KEY(contest_key,entry_id))''',
        '''CREATE TABLE IF NOT EXISTS opponent_entry_slots (
        contest_key TEXT NOT NULL, entry_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
        role TEXT NOT NULL, original_label TEXT NOT NULL, player_id TEXT,
        player_name TEXT, position TEXT, team TEXT, salary INTEGER,
        PRIMARY KEY(contest_key,entry_id,ordinal),
        FOREIGN KEY(contest_key,entry_id) REFERENCES opponent_entries(contest_key,entry_id) ON DELETE CASCADE)''',
        '''CREATE TABLE IF NOT EXISTS opponent_user_contest_stats (
        contest_key TEXT NOT NULL REFERENCES opponent_contests(contest_key) ON DELETE CASCADE,
        user_key TEXT NOT NULL REFERENCES opponent_users(user_key), entries INTEGER NOT NULL,
        readable INTEGER NOT NULL, best_rank INTEGER, top_one_pct_entries INTEGER,
        stats_json TEXT NOT NULL, PRIMARY KEY(contest_key,user_key))''',
        '''CREATE TABLE IF NOT EXISTS opponent_profile_versions (
        profile_id INTEGER PRIMARY KEY, cutoff TEXT NOT NULL, format TEXT NOT NULL,
        username_filter TEXT NOT NULL, version INTEGER NOT NULL, evidence_json TEXT NOT NULL,
        profile_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''',
        'CREATE INDEX IF NOT EXISTS opponent_entries_user ON opponent_entries(user_key,contest_key)',
        'CREATE INDEX IF NOT EXISTS opponent_entries_identity ON opponent_entries(entry_id,contest_key)',
        'CREATE INDEX IF NOT EXISTS opponent_stats_user ON opponent_user_contest_stats(user_key,contest_key)',
        'CREATE INDEX IF NOT EXISTS opponent_contests_dates ON opponent_contests(format,end_date)',
    ]
    for sql in statements:
        conn.execute(sql)


def _connect(db_path, readonly=False):
    path = Path(db_path).resolve()
    if not path.is_file():
        raise ValueError('History database is missing; import and map results first.')
    conn = sqlite3.connect(path.as_uri() + ('?mode=ro' if readonly else '?mode=rw'), uri=True, timeout=10)
    conn.execute('PRAGMA foreign_keys=ON')
    if readonly:
        conn.execute('PRAGMA query_only=ON')
    return conn


def _lookup(players):
    lookup = defaultdict(list)
    for p in players:
        for role in p['role'].split('/'):
            lookup[ai._name(p['name']), role].append(p)
    return lookup


def _metadata(slots, lookup):
    resolved = []
    for label, role in slots:
        candidates = lookup[ai._name(label), role]
        explicit = re.search(r'\((\d+)\)\s*$', label)
        if explicit:
            candidates = [p for p in candidates if p['id'] == explicit[1]]
        resolved.append((label, role, candidates[0] if len(candidates) == 1 else None))
    return resolved


def _stage_report(path, report, key, success_basis, success_size, cancelled, progress):
    """Bounded normalized-row batches; this database is never user-visible."""
    lookup=_lookup(report['history_evidence']['players']);top=Counter()
    with closing(sqlite3.connect(path)) as stage,stage:
        stage.execute('CREATE TABLE entries(contest_key TEXT,entry_id TEXT PRIMARY KEY,user_key TEXT,rank INTEGER,points REAL,signature_json TEXT,metadata_complete INTEGER)')
        stage.execute('CREATE TABLE slots(contest_key TEXT,entry_id TEXT,ordinal INTEGER,role TEXT,original_label TEXT,player_id TEXT,player_name TEXT,position TEXT,team TEXT,salary INTEGER)')
        stage.execute('CREATE TABLE users(user_key TEXT PRIMARY KEY,platform TEXT,username TEXT)')
        stage.execute('CREATE TABLE stats(contest_key TEXT,user_key TEXT,entries INTEGER,readable INTEGER,best_rank INTEGER,top_one_pct_entries INTEGER,stats_json TEXT)')
        raw=report['history_entries']
        for offset in range(0,len(raw),1000):
            ai._check(cancelled);entries=[];slots=[]
            for entry in raw[offset:offset+1000]:
                user='dk:'+entry['username_key'];metadata=_metadata(entry['slots'],lookup)
                known=bool(metadata) and all(p for _,_,p in metadata)
                entries.append((key,entry['entry_id'],user,entry['rank'],entry['points'],json.dumps(entry['signature']) if entry['signature'] else None,int(known)))
                for ordinal,(label,role,p) in enumerate(metadata):
                    slots.append((key,entry['entry_id'],ordinal,role,label,*(p.get(f) if p else None for f in ('id','name','position','team','salary'))))
                if success_size and entry['rank'] is not None and entry['rank']<=max(1,success_size*.01):top[user]+=1
            stage.executemany('INSERT INTO entries VALUES(?,?,?,?,?,?,?)',entries)
            stage.executemany('INSERT INTO slots VALUES(?,?,?,?,?,?,?,?,?,?)',slots)
            if offset%10000==0:
                stage.commit();progress(f'Preparing field history: {min(offset+1000,len(raw)):,}/{len(raw):,} entries…')
        members=report['portfolios']
        for offset in range(0,len(members),1000):
            ai._check(cancelled);users=[];stats=[]
            for p in members[offset:offset+1000]:
                user='dk:'+p['username_key'];users.append((user,'draftkings',p['username']))
                summary={k:v for k,v in p.items() if k not in ('pairs','lineups','players','captain_groups')}
                summary['history_success_basis']=success_basis
                stats.append((key,user,p['entries'],p['readable_rosters'],p['best_rank'],top[user] if success_basis else None,json.dumps(summary)))
            stage.executemany('INSERT INTO users VALUES(?,?,?)',users)
            stage.executemany('INSERT INTO stats VALUES(?,?,?,?,?,?,?)',stats)
            stage.commit()
        ai._check(cancelled)


def index_contest(db_path, result_hash, cancelled=lambda: False, progress=lambda text: None):
    # Refreshes re-verify immutable evidence, but need not rebuild unchanged rows.
    with closing(_connect(db_path,True)) as conn:
        conn.execute('BEGIN')
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='opponent_contests'").fetchone():
            old=conn.execute('SELECT contest_key,salary_hash FROM opponent_contests WHERE result_hash=? AND version=?',(result_hash,VERSION)).fetchone()
            if old:
                pair=conn.execute('SELECT salary_hash FROM analysis_salary_pairs WHERE result_hash=?',(result_hash,)).fetchone()
                if pair and pair[0]==old[1]:
                    sources={s['hash']:s for s in ai._sources(conn)}
                    result,salary=sources.get(result_hash),sources.get(old[1])
                    if not result or not salary:raise ValueError('Indexed sources are missing; review the saved mapping.')
                    ai._verify(result,cancelled);ai._verify(salary,cancelled)
                    qualification=ai.qualify_pair(result,salary)
                    if not qualification['compatible']:raise ValueError(qualification['reason'])
                    counts=conn.execute('SELECT COUNT(*),COUNT(DISTINCT user_key) FROM opponent_entries WHERE contest_key=?',(old[0],)).fetchone()
                    ai._check(cancelled)
                    return dict(contest_key=old[0],entries=counts[0],users=counts[1],unchanged=True)
    report = analyze_saved_contest(db_path, result_hash, cancelled, progress, capture_entries=True)
    evidence = report['history_evidence']
    result, salary = evidence['result'], evidence['salary']
    ids = result['manifest']['contest_ids']
    external = ids[0] if len(ids) == 1 else None
    entry_ids=sorted(e['entry_id'] for e in report['history_entries'])
    key = 'dk:contest:' + external if external else 'dk:entries:' + hashlib.sha256(json.dumps(entry_ids).encode()).hexdigest()
    dates = evidence['salary']['manifest']['dates']
    complete = report['supplied_field_size'] == report['audit']['accepted_entries'] and not report['audit']['conflicting_entry_ids_excluded']
    field_size = report['supplied_field_size']
    observed_ranked = (field_size is None and not report['audit']['conflicting_entry_ids_excluded'] and
                       all(e['rank'] is not None and e['rank']<=len(entry_ids) for e in report['history_entries']))
    success_basis='supplied complete field' if complete else 'observed entries; completeness unverified' if observed_ranked else None
    success_size=field_size if complete else len(entry_ids) if observed_ranked else None
    count=len(report['history_entries']);user_count=len(report['portfolios'])
    record=(key,'draftkings',external,result_hash,salary['hash'],result['name'],report['format'],min(dates) if dates else None,max(dates) if dates else None,field_size,int(complete),VERSION,json.dumps(report['audit']))
    # Prepare outside the user's write transaction. Temporary batches avoid a
    # second in-memory copy of millions of roster slots.
    with tempfile.TemporaryDirectory(prefix='dfs-field-stage-') as directory:
        staged=Path(directory)/'prepared.sqlite'
        _stage_report(staged,report,key,success_basis,success_size,cancelled,progress)
        del report,evidence,entry_ids
        ai._check(cancelled);progress(f'Publishing verified contest: {count:,} entries, {user_count:,} users…')
        with closing(_connect(db_path)) as conn,conn:
            conn.execute('ATTACH DATABASE ? AS field_stage',(str(staged),))
            conn.set_progress_handler(lambda:int(cancelled()),10000)
            try:
                conn.execute('BEGIN IMMEDIATE');ensure_tables(conn)
                current=conn.execute('SELECT salary_hash FROM analysis_salary_pairs WHERE result_hash=?',(result_hash,)).fetchone()
                if not current or current[0]!=salary['hash']:raise ValueError('Salary association changed; retry after reviewing the mapping.')
                ai._verify(result,cancelled);ai._verify(salary,cancelled)
                old=conn.execute('SELECT result_hash,salary_hash,version FROM opponent_contests WHERE contest_key=?',(key,)).fetchone()
                if old and old[0]!=result_hash:raise ValueError('A different export of this contest is already indexed. Review the conflicting source; it was not counted twice.')
                if old==(result_hash,salary['hash'],VERSION):return dict(contest_key=key,entries=count,users=user_count,unchanged=True)
                overlap=conn.execute('SELECT e.contest_key FROM field_stage.entries s JOIN opponent_entries e ON e.entry_id=s.entry_id JOIN opponent_contests c ON c.contest_key=e.contest_key WHERE e.contest_key<>? AND (c.external_id IS NULL OR ? IS NULL) LIMIT 1',(key,external)).fetchone()
                if overlap:raise ValueError('EntryIds overlap another indexed export and a contest ID is absent. Review the sources; no second copy was indexed.')
                conn.execute('DELETE FROM opponent_contests WHERE contest_key=?',(key,))
                conn.execute('INSERT INTO opponent_contests(contest_key,platform,external_id,result_hash,salary_hash,name,format,start_date,end_date,field_size,complete,version,audit_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',record)
                conn.execute('INSERT INTO opponent_users SELECT * FROM field_stage.users WHERE 1 ON CONFLICT(user_key) DO UPDATE SET username=excluded.username')
                for target,table in (('opponent_entries','entries'),('opponent_entry_slots','slots'),('opponent_user_contest_stats','stats')):
                    conn.execute('INSERT INTO '+target+' SELECT * FROM field_stage.'+table)
                ai._verify(result,cancelled);ai._verify(salary,cancelled);ai._check(cancelled)
            except sqlite3.OperationalError:
                ai._check(cancelled);raise
            finally:conn.set_progress_handler(None,0)
    return dict(contest_key=key,entries=count,users=user_count,unchanged=False)


def sync_saved(db_path, cancelled=lambda: False, progress=lambda text: None):
    results, errors = [], []
    contests = saved_contests(db_path)
    for i,c in enumerate(contests):
        ai._check(cancelled)
        progress(f"Indexing mapped contest {i+1}/{len(contests)}: {c['name']}")
        try:
            results.append(index_contest(db_path,c['result_hash'],cancelled,progress))
        except ai.ImportCancelled:
            raise
        except Exception as exc:
            errors.append(dict(name=c['name'],error=str(exc)))
    return dict(contests=results, errors=errors,
                note='Each completed contest is committed atomically. Cancellation preserves completed contests; the active contest rolls back.')


def profile_preview(db_path, cutoff, contest_format='showdown', username='', cancelled=lambda: False, *, entry_band='', min_contests=3, min_successes=2, allow_observed_fields=False):
    """Strict prior-date eligibility, verified sources, whole-user historical cohorts."""
    cutoff = date.fromisoformat(cutoff).isoformat()
    if contest_format not in ('showdown','classic'):
        raise ValueError('Choose Showdown or Classic.')
    if entry_band not in ('','1','2–5','6–20','21–150','151+'):
        raise ValueError('Choose a supported entry-count band.')
    if not isinstance(min_contests,int) or not isinstance(min_successes,int) or min_contests<1 or not 1<=min_successes<=min_contests:
        raise ValueError('Successful contest minimum must be between 1 and the observed contest minimum.')
    with closing(_connect(db_path,True)) as conn:
        conn.execute('BEGIN')
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='opponent_contests'").fetchone():
            raise ValueError('Index saved mapped contests before viewing username history.')
        contests = conn.execute('SELECT contest_key,result_hash,salary_hash,external_id,complete,end_date,name FROM opponent_contests WHERE format=? AND end_date<? AND version=? ORDER BY end_date,contest_key',
                                (contest_format,cutoff,VERSION)).fetchall()
        sources = {s['hash']:s for s in ai._sources(conn)}
        pairs = dict(conn.execute('SELECT result_hash,salary_hash FROM analysis_salary_pairs'))
        eligible, excluded, evidence = [], [], []
        details={c[0]:dict(date=c[5],name=c[6]) for c in contests}
        for key,r,s,external,complete,_,_ in contests:
            ai._check(cancelled)
            try:
                if pairs.get(r) != s or r not in sources or s not in sources:
                    raise ValueError('Contest identity or saved association is unresolved.')
                ai._verify(sources[r],cancelled); ai._verify(sources[s],cancelled)
                eligible.append(key);evidence.append(dict(contest_key=key,result_hash=r,salary_hash=s,
                                                          identity_basis='explicit contest ID' if external else 'verified export entry-set identity; no overlapping indexed export'))
            except ai.ImportCancelled:
                raise
            except Exception as exc:
                excluded.append(dict(contest_key=key,reason=str(exc)))
        def records(wanted=None):
            seen=0
            ordered=sorted(wanted) if wanted is not None else None
            blocks=[None] if ordered is None else [ordered[i:i+500] for i in range(0,len(ordered),500)]
            for key in eligible:
                for block in blocks:
                    sql='SELECT user_key,stats_json,top_one_pct_entries FROM opponent_user_contest_stats WHERE contest_key=?'
                    args=[key]
                    if block is not None:
                        sql+=' AND user_key IN ('+','.join('?' for _ in block)+')';args+=block
                    for user,payload,top in conn.execute(sql+' ORDER BY user_key',args):
                        if seen%500==0:ai._check(cancelled)
                        seen+=1;p=json.loads(payload)
                        if entry_band and p['entry_band']!=entry_band:continue
                        if not allow_observed_fields and p.get('history_success_basis')!='supplied complete field':top=None
                        yield key,user,p,top

        def accumulator():
            return dict(contests=set(),users=set(),entries=0,known=0,tables=defaultdict(Counter))

        def include(acc,key,user,p):
            acc['contests'].add(key);acc['users'].add(user);acc['entries']+=p['entries']
            c=p.get('constructions') or {};acc['known']+=c.get('known_entries',0)
            for category,values in c.get('tables',{}).items():
                acc['tables'][category].update({v['label']:v['entries'] for v in values})

        def finish(acc):
            return dict(contests=len(acc['contests']),users=len(acc['users']),entries=acc['entries'],
                        known_construction_entries=acc['known'],
                        distributions={cat:dict(counts=dict(sorted(counts.items())),denominator=sum(counts.values())) for cat,counts in sorted(acc['tables'].items())})

        selected='dk:'+username_key(username) if username.strip() else None
        field_acc,user_acc,success_acc=accumulator(),accumulator(),accumulator()
        user_counts={};timeline=[]
        for key,user,p,top in records():
            include(field_acc,key,user,p)
            row=user_counts.setdefault(user,[p['username'],0,0,0,0,0])
            row[1]+=1;row[2]+=top is not None;row[3]+=p['entries'];row[4]+=top or 0;row[5]+=bool(top)
            if user==selected:
                include(user_acc,key,user,p)
                timeline.append(dict(contest_key=key,**details[key],username=p['username'],entries=p['entries'],best_rank=p['best_rank'],
                                     top_one_pct_entries=top,constructions=p['constructions'],mean_points=p['mean_points'],
                                     captain_pool=p['captain_pool'],mean_shared_players=p['mean_shared_players']))
        successful={user for user,row in user_counts.items() if row[2]>=min_contests and row[5]>=min_successes}
        for key,user,p,top in records(successful):include(success_acc,key,user,p)
        baseline=finish(field_acc);observed=finish(user_acc) if selected else dict(baseline)
        success_summary=finish(success_acc)
        users=(dict(username=row[0],contests=row[1],qualifying_contests=row[2],entries=row[3],
                    top_one_pct_entries=row[4] if row[2] else None,top_one_pct_contests=row[5] if row[2] else None,
                    in_successful_cohort=user in successful)
               for user,row in user_counts.items() if selected is None or user==selected)
        users=heapq.nsmallest(1000,users,key=lambda u:(-(u['top_one_pct_contests'] or 0),-u['qualifying_contests'],u['username'].casefold()))
        for source in evidence:
            ai._verify(sources[source['result_hash']],cancelled);ai._verify(sources[source['salary_hash']],cancelled)
    # A user's sparse history blends with field construction, never old athlete IDs.
    weight=observed['known_construction_entries']/(observed['known_construction_entries']+100) if selected else 1.0
    priors={}
    for category,base in baseline['distributions'].items():
        own=observed['distributions'].get(category,dict(counts={},denominator=0))
        w=weight if own['denominator'] else 0
        priors[category]={label:w*own['counts'].get(label,0)/max(1,own['denominator'])+(1-w)*count/max(1,base['denominator'])
                          for label,count in base['counts'].items()}
    ai._check(cancelled)
    return dict(version=VERSION,cutoff=cutoff,format=contest_format,username=username,entry_band=entry_band,min_contests=min_contests,min_successes=min_successes,allow_observed_fields=allow_observed_fields,baseline=baseline,
                observed=observed,successful_users=success_summary,
                success_definition=f"At least {min_contests} {'ranked observed' if allow_observed_fields else 'complete observed'} contests and a top-1% entry in at least {min_successes}. {'Observed-entry denominators can represent incomplete fields. ' if allow_observed_fields else ''}Descriptive historical cohort; not proof of skill or profit.",
                user_history_weight=weight,construction_priors=priors,evidence=evidence,excluded=excluded,users=users,users_total=len(user_counts),
                timeline=timeline,
                limitations=['Only saved mapped imports are observed; absent contests/users are unknown.',
                             'Cutoff excludes its entire calendar date; timestamps of availability are not established by results CSVs.',
                             'Successful-user selection uses historical outcomes and can contain selection bias. All eligible entries of those users are retained.',
                             'Entry-weighted construction priors are a preview, not a change to ownership, projections or SIM.',
                             'Missing entry fees/payouts do not establish profitability; cross-slate points are not comparable.',
                             'Username normalization is platform-specific; renames and alternate accounts are not inferred.'])


def save_profile(db_path, profile, cancelled=lambda:False):
    # Persist a freshly verified preview, not an arbitrary edited input payload.
    fresh=profile_preview(db_path,profile['cutoff'],profile['format'],profile['username'],cancelled,
                          entry_band=profile.get('entry_band',''),min_contests=profile.get('min_contests',3),min_successes=profile.get('min_successes',2),allow_observed_fields=profile.get('allow_observed_fields',False))
    with closing(_connect(db_path)) as conn,conn:
        ensure_tables(conn)
        cursor=conn.execute('INSERT INTO opponent_profile_versions(cutoff,format,username_filter,version,evidence_json,profile_json) VALUES(?,?,?,?,?,?)',
                            (fresh['cutoff'],fresh['format'],username_key(fresh['username']),VERSION,json.dumps(fresh['evidence']),json.dumps(fresh)))
        ai._check(cancelled)
        return cursor.lastrowid


def render_preview(profile):
    lines=[f"Username history / field preview before {profile['cutoff']} ({profile['format']})",
           f"Search: {profile['username'] or 'Entire imported field'}",
           f"Observed entry-count band: {profile['entry_band'] or 'All'}",
           f"Field: {profile['baseline']['contests']} contests, {profile['baseline']['users']} users, {profile['baseline']['entries']:,} entries.",
           f"Selection: {profile['observed']['contests']} contests, {profile['observed']['entries']:,} entries; {profile['observed']['known_construction_entries']:,} known constructions.",
           f"Successful historical cohort: {profile['successful_users']['users']} users. {profile['success_definition']}",
           f"User history weight: {profile['user_history_weight']:.1%}; remainder uses the field baseline."]
    for scope in ('baseline','observed','successful_users'):
        lines.append('\n'+{'baseline':'Whole field','observed':'Selected username / field','successful_users':'Successful historical users'}[scope]+' constructions:')
        for category,data in profile[scope]['distributions'].items():
            n=data['denominator']
            lines.append(category+f' (n={n:,}): '+', '.join(f'{label} {count/n:.1%}' for label,count in data['counts'].items()))
    lines.append('\nPreview construction priors (entry-weighted):')
    for category,values in profile['construction_priors'].items():
        lines.append(category+': '+', '.join(f'{label} {pct:.1%}' for label,pct in values.items()))
    lines.append('\nObserved users, sorted by contests with a top-1% entry (not a skill ranking):')
    selected=username_key(profile['username'])
    shown=[u for u in profile['users'] if not selected or username_key(u['username'])==selected]
    for user in shown[:30]:
        entries=user['top_one_pct_entries'] if user['top_one_pct_entries'] is not None else 'unknown'
        contests=user['top_one_pct_contests'] if user['top_one_pct_contests'] is not None else 'unknown'
        lines.append(f"{user['username']}: {user['entries']:,} entries, {user['contests']} contests ({user['qualifying_contests']} qualifying coverage), {entries} top-1% entries across {contests} contests; successful cohort: {user['in_successful_cohort']}")
    if len(shown)>30:lines.append('Showing 30 usernames. All indexed usernames are searchable.')
    lines.append('\nContest history:')
    for row in profile['timeline'][:200]:
        lines.append(f"{row['date']} {row['name']} — {row['username']}: {row['entries']} entries, best rank {row['best_rank']}, top-1% entries {row['top_one_pct_entries'] if row['top_one_pct_entries'] is not None else 'unknown'}, Captain pool {row['captain_pool']}, mean shared players {row['mean_shared_players']}")
    if len(profile['timeline'])>200:
        lines.append('Showing the first 200 user-contest rows. Search an exact username to narrow the history.')
    lines.extend('\n'+note for note in profile['limitations'])
    lines.extend('Excluded '+r['contest_key']+': '+r['reason'] for r in profile['excluded'])
    return '\n'.join(lines)
