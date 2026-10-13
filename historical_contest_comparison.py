"""Descriptive matched-contest and equal-slate comparisons; no model tuning."""
from collections import defaultdict
import datetime as dt
import json
import statistics


FEATURES = ('Kicker/defense slots: 2','Kicker/defense slots: 3','Kicker/defense slots: 4',
            'Kickers: 2','Defenses: 2','Captain position: K','Captain position: DST',
            'Captain with opposing FLEX defense: yes','Team split: 3–3','Team split: 4–2','Team split: 5–1')
CLASSIC_FEATURES = ('Defenses: 1','QB same-team WR/TE: 0','QB same-team WR/TE: 1',
                    'QB same-team WR/TE: 2','QB same-team WR/TE: 3','QB same-team WR/TE: 4',
                    'FLEX position: RB','FLEX position: WR','FLEX position: TE')


def personal_observations(conn, username, *, limit=200000):
    """Read stored personal rosters, never source CSVs or current build settings."""
    from opponent_analysis import username_key, _roster
    selected = username_key(username)
    result = dict(complete=True, imports={},receipts={})
    if not selected:
        return result
    for index,(ident,name,encoded) in enumerate(conn.execute('SELECT import_id,entry_name,raw_json FROM historical_results ORDER BY result_id')):
        if index >= limit:
            return dict(complete=False,imports={},receipts={})
        if username_key(str(name or '')) != selected:
            continue
        group = result['imports'].setdefault(ident,dict(rows=0,readable=0,captains=set()))
        group['rows'] += 1
        try:
            raw = json.loads(encoded or '{}')
            raw = {str(k).lower().replace(' ','').replace('_',''):v for k,v in raw.items()}
            text = raw.get('lineup') or raw.get('roster') or ''
            roster = _roster(text,'showdown') if isinstance(text,str) else None
        except (ValueError,TypeError,AttributeError):
            roster = None
        if roster:
            group['readable'] += 1
            group['captains'].add(roster[2])
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if {'opponent_contests','opponent_user_contest_stats'} <= tables:
        from opponent_history import VERSION
        rows = conn.execute('''SELECT c.result_hash,c.salary_hash,c.audit_json,s.entries,s.readable,s.stats_json
            FROM opponent_contests c JOIN opponent_user_contest_stats s ON s.contest_key=c.contest_key
            WHERE c.version=? AND s.user_key=?''',(VERSION,'dk:'+selected))
        for source,salary,audit,entries,readable,encoded in rows:
            try:
                audit=json.loads(audit);stats=json.loads(encoded)
                pool=stats['captain_pool']
                if (audit.get('conflicting_entry_ids_excluded',0) or not entries or readable!=entries or
                        stats.get('constructions',{}).get('known_entries')!=entries or
                        not isinstance(pool,int) or not 0 < pool <= entries):
                    continue
                result['receipts'][(source,salary)] = dict(rows=entries,readable=readable,captain_pool=pool)
            except (ValueError,TypeError,KeyError,AttributeError):
                continue
    return result


def context(kind, cohort, observation, complete=True, *, verified=False):
    if kind != 'showdown':
        return 'Classic settings unknown', None
    counts = cohort.get('counts',{})
    n = counts.get('entry_rows',0)
    if (not verified or not complete or not n or not observation or observation['rows'] != n or
            observation['readable'] != n or counts.get('supported_rows',0) != n):
        return 'Captain coverage/settings unknown', None
    pool = observation.get('captain_pool',len(observation.get('captains',())))
    if n == 1:
        return 'Single submitted lineup; settings unknown', pool
    if pool == 1:
        return 'Single Captain; user-assumed lock', pool
    return 'Multiple Captains; settings unknown', pool


def contest_record(data, pair, payload, username, observations):
    from opponent_analysis import username_key
    cohorts = {k:v for k,v in payload.get('cohorts',{}).items() if not k.startswith('user:')}
    personal = payload.get('cohorts',{}).get('user:'+username_key(username)) if username.strip() else None
    if personal:
        cohorts['yours'] = personal
    salary = data['salary_evidence']
    receipt = observations.get('receipts',{}).get((data['results_evidence']['source_hash'],salary['revision_hash']))
    label,pool = context(salary['format'],personal or {},receipt,observations.get('complete',True),verified=receipt is not None)
    dates = salary.get('dates') or []
    games = tuple(sorted(set(salary.get('games') or [])))
    day = dates[0] if len(dates)==1 else None
    try:
        dt.date.fromisoformat(day)
    except (ValueError,TypeError):
        day = None
    return dict(name=pair.get('name') or 'Unnamed source', source=data['results_evidence']['source_hash'],
                date=day,games=games,context=label,captain_pool=pool,cohorts=cohorts,
                rank_basis=payload.get('rank_basis','unavailable'),version=payload['version'])


def rate(contest, cohort, feature):
    group = contest['cohorts'].get(cohort,{})
    n = group.get('counts',{}).get('supported_rows',0)
    return 100*group.get('features',{}).get(feature,0)/n if n else None


def slate_blocks(contests):
    """Connected game overlap on the same date counts once, not per contest."""
    blocks = []
    excluded = 0
    for contest in sorted(contests,key=lambda c:c['source']):
        if not contest['date'] or not contest['games']:
            excluded += 1
            continue
        games = set(contest['games'])
        overlaps = [b for b in blocks if b['date']==contest['date'] and games.intersection(b['games'])]
        block = dict(date=contest['date'],games=games,contests=[contest])
        for old in overlaps:
            block['games'].update(old['games'])
            block['contests'].extend(old['contests'])
            blocks.remove(old)
        blocks.append(block)
    return blocks,excluded


def matched_summary(contests, feature, comparator):
    paired = [c for c in contests if rate(c,'yours',feature) is not None and rate(c,comparator,feature) is not None]
    blocks,excluded = slate_blocks(paired)
    yours,other = [],[]
    for block in blocks:
        yours.append(statistics.mean(rate(c,'yours',feature) for c in block['contests']))
        other.append(statistics.mean(rate(c,comparator,feature) for c in block['contests']))
    return dict(slates=len(blocks),contests=len(paired)-excluded,excluded=excluded,
                yours=statistics.mean(yours) if yours else None,
                comparator=statistics.mean(other) if other else None,
                gap=statistics.mean(a-b for a,b in zip(yours,other)) if yours else None)


def report_lines(scope):
    contests = scope.get('contests',[])
    if not contests:
        return []
    features = FEATURES if scope['format']=='showdown' else CLASSIC_FEATURES
    lines = ['', 'Contest-by-contest construction comparisons',
             '- Captain-lock labels use your single-Captain instruction and require complete personal coverage in the normalized opponent index bound to the current results/salary revisions. They are assumptions rather than build receipts. Missing/stale indexing remains unknown. Multiple Captains do not prove a normal SIM build. A single submitted lineup does not establish lock settings.']
    for c in sorted(contests,key=lambda c:(c['date'] or '',c['name'],c['source'])):
        n = c['cohorts'].get('yours',{}).get('counts',{}).get('supported_rows',0)
        lines.append(f"- {c['name']} | {c['date'] or 'date unknown'} | {c['context']} | personal supported rows {n:,}; Captain pool {c['captain_pool'] if c['captain_pool'] is not None else 'unknown'}.")
        lines.append(f"  Rank basis: {c['rank_basis']}.")
        if c['version']==1:
            lines.append('  Older summary has no cohort evidence; omitted from matched comparisons.')
            continue
        lines += ['| Construction | Field | Observed rank cutoff 1% | Your submitted rows | Yours − field (pp) |',
                  '|---|---:|---:|---:|---:|']
        for feature in features:
            values = [rate(c,k,feature) for k in ('field','top1','yours')]
            cells = [f'{v:.1f}%' if v is not None else 'unavailable' for v in values]
            gap = values[2]-values[0] if values[2] is not None and values[0] is not None else None
            lines.append('| '+feature+' | '+' | '.join(cells)+f" | {f'{gap:+.1f}' if gap is not None else 'unavailable'} |")
    lines += ['', 'Equal-slate matched comparison — report only',
              '- Contests sharing games on the same recorded salary date form one block (including overlapping Classic slates). Average contest rates within each block, then give each block equal weight. Missing date/game identity is excluded. Blocks are evidence groups, not a claim of statistical independence.',
              '- Yours and comparator use the same contributing contests for each comparison. Field and rank-cutoff comparisons can have different coverage. Context categories remain separate; no normal-SIM label is inferred.']
    grouped = defaultdict(list)
    for c in contests:
        if c['cohorts'].get('yours',{}).get('counts',{}).get('supported_rows',0):
            grouped[c['context']].append(c)
    for label,group in sorted(grouped.items()):
        lines.append(f'- Context: {label}.')
        lines += ['| Construction | Comparator | Yours | Comparator rate | Difference (pp) | Slate blocks / contests | Excluded identity |',
                  '|---|---|---:|---:|---:|---:|---:|']
        for feature in features:
            for comparator in ('field','top1'):
                result = matched_summary(group,feature,comparator)
                if result['slates']:
                    cells = f"{result['yours']:.1f}% | {result['comparator']:.1f}% | {result['gap']:+.1f}"
                else:
                    cells = 'unavailable | unavailable | unavailable'
                lines.append(f"| {feature} | {'Field' if comparator=='field' else 'Observed rank cutoff 1%'} | {cells} | {result['slates']} / {result['contests']} | {result['excluded']} |")
    lines.append('- Frequencies and matched percentage-point gaps are descriptive. Source rows include duplicate EntryIds; slate blocks, tied rank-1 rows and missing payouts do not establish independent wins, profit or a causal construction advantage. No generation limits are changed.')
    return lines
