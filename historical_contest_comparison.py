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




def captain_defense_rate(contest, cohort, position):
    """Joint counts cannot be reconstructed from legacy marginal percentages."""
    if contest.get('version')!=3:return None
    group=contest.get('cohorts',{}).get(cohort,{})
    n=group.get('features',{}).get('Captain position: '+position,0)
    total=group.get('counts',{}).get('supported_rows',0)
    joint=group.get('captain_defense')
    if not isinstance(joint,dict):return None
    yes,no=joint.get(position+' with',0),joint.get(position+' without',0)
    if (not all(type(v) is int and v>=0 for v in (n,total,yes,no)) or
            not 0<n<=total or yes+no!=n):return None
    return 100*yes/n


def captain_defense_report(scope):
    if scope['format']!='showdown' or not scope.get('contests'):return []
    if not any(c.get('version')==3 for c in scope['contests']):
        return ['', 'Opposing FLEX defense within Captain position: unavailable',
            '- Cached summaries contain marginal counts only. Reconcile Historical Coverage once, or use subsequent normal reconciliation, to record joint counts; no values were inferred and earlier broad comparisons remain available.']
    lines=['', 'Opposing FLEX defense within Captain position — equal slate weight',
        '- Compares the share using an opposing FLEX defense within the same recorded Captain position. Each field/cohort pair uses the same contests. Position denominators can differ between comparisons; this is not a controlled experiment or a causal effect.',
        '- Legacy summaries cannot supply joint counts. Reconcile Historical Coverage once, or use summaries produced during subsequent normal reconciliation. Missing/inconsistent counts and absent position coverage stay unavailable. Earlier broad comparisons remain readable.',
        '| Captain | Rank cohort | Field with opposing DST | Cohort with opposing DST | Cohort − field (pp) | Slate blocks / sources | Captain-position source rows field / cohort | Excluded identity / cohort / legacy / conflicting source |',
        '|---|---|---:|---:|---:|---:|---:|---:|']
    for position in ('QB','RB','WR','TE','K','DST'):
        for key,label in (('top1','Rank cutoff 1%'),('top5','Rank cutoff 5%'),('rank1','Rank 1')):
            r=field_comparison(scope['contests'],'',key,captain_position=position)
            cells=(f"{r['field']:.1f}% | {r['comparator']:.1f}% | {r['gap']:+.1f}" if r['slates'] else 'unavailable | unavailable | unavailable')
            lines.append(f"| {position} | {label} | {cells} | {r['slates']} / {r['contests']} | {r['field_rows']:,} / {r['comparator_rows']:,} | {r['excluded_identity']} / {r['excluded_cohort']} / {r['excluded_legacy']} / {r['excluded_conflicting_sources']} |")
    lines.append('- Source rows include duplicate EntryIds; rank cutoffs include ties and do not certify full fields or payouts. Formats/presets can share games; sample counts must not be added. No Captain locks, limits, forecasts or model defaults are changed.')
    return lines


def field_comparison(contests, feature, comparator, *, captain_position=None):
    """Matched field/cohort source rows, regardless of personal participation.

    Identical source receipts count once; contradictory copies are withheld.
    The caller supplies one format/entry-preset scope from verified pairings.
    """
    if comparator not in ('top1','top5','rank1'):
        raise ValueError('Choose a recorded rank cohort for field comparison.')
    if captain_position is not None and captain_position not in ('QB','RB','WR','TE','K','DST'):
        raise ValueError('Invalid Captain position.')
    versions = (3,) if captain_position else (2,3)
    def measured(c,cohort):
        return captain_defense_rate(c,cohort,captain_position) if captain_position else rate(c,cohort,feature)
    def denominator(c,cohort):
        group=c['cohorts'][cohort]
        return group['features']['Captain position: '+captain_position] if captain_position else group['counts']['supported_rows']
    copies = defaultdict(dict)
    missing_source = 0
    for contest in contests:
        if not contest.get('source'):
            missing_source += 1
            continue
        encoded = json.dumps(contest, sort_keys=True)
        copies[contest['source']][encoded] = contest
    repeated = len(contests)-missing_source-sum(len(v) for v in copies.values())
    conflicts = sum(len(v)>1 for v in copies.values())
    unique = [next(iter(v.values())) for _,v in sorted(copies.items()) if len(v)==1]
    legacy = sum(c.get('version') not in versions for c in unique)
    current = [c for c in unique if c.get('version') in versions]
    paired = [c for c in current if measured(c,'field') is not None and measured(c,comparator) is not None]
    blocks, excluded = slate_blocks(paired)
    field, other = [], []
    for block in blocks:
        field.append(statistics.mean(measured(c,'field') for c in block['contests']))
        other.append(statistics.mean(measured(c,comparator) for c in block['contests']))
    included = [c for b in blocks for c in b['contests']]
    return dict(slates=len(blocks),contests=len(included),excluded_identity=excluded+missing_source,
        excluded_cohort=len(current)-len(paired),excluded_legacy=legacy,
        excluded_conflicting_sources=conflicts,repeated_source_copies=repeated,
        field=statistics.mean(field) if field else None,
        comparator=statistics.mean(other) if other else None,
        gap=statistics.mean(b-a for a,b in zip(field,other)) if field else None,
        field_rows=sum(denominator(c,'field') for c in included),
        comparator_rows=sum(denominator(c,comparator) for c in included))


def field_report_lines(scope):
    contests=scope.get('contests',[])
    if not contests:return []
    features=(('Kicker/defense slots: 0','Kicker/defense slots: 1')+FEATURES
        if scope['format']=='showdown' else CLASSIC_FEATURES)
    lines=['', 'Field versus top-finisher construction — equal slate weight',
        '- Uses all contributing contests in this format and entry-preset scope, including contests you did not enter. Each comparison uses the same contests for the field and rank cohort.',
        '- Contests with overlapping games on the same recorded date form one slate block. Average contest rates within each block, then weight blocks equally; these blocks are not a claim of statistical independence.',
        '- Formats and entry presets can share games; do not add their sample counts. Rates describe mapped source rows, including duplicate EntryIds. Rank cutoffs include ties; missing ranks and partial fields can affect the cohort. Rank-1 rows are not verified wins, profit or a construction advantage.',
        '| Construction | Rank cohort | Field | Cohort | Cohort − field (pp) | Slate blocks / sources | Mapped source rows field / cohort | Excluded identity / cohort / legacy / conflicting source |',
        '|---|---|---:|---:|---:|---:|---:|---:|']
    repeats=0
    for feature in features:
        for key,label in (('top1','Rank cutoff 1%'),('top5','Rank cutoff 5%'),('rank1','Rank 1')):
            r=field_comparison(contests,feature,key)
            repeats=max(repeats,r['repeated_source_copies'])
            cells=(f"{r['field']:.1f}% | {r['comparator']:.1f}% | {r['gap']:+.1f}" if r['slates']
                else 'unavailable | unavailable | unavailable')
            lines.append(f"| {feature} | {label} | {cells} | {r['slates']} / {r['contests']} | {r['field_rows']:,} / {r['comparator_rows']:,} | {r['excluded_identity']} / {r['excluded_cohort']} / {r['excluded_legacy']} / {r['excluded_conflicting_sources']} |")
    lines.append(f'- Identical repeated source receipts omitted: {repeats}. Differences are descriptive frequencies, not finish probabilities, payout estimates or tuning recommendations. No generation limits or model defaults are changed.')
    return lines


def report_lines(scope):
    contests = scope.get('contests',[])
    if not contests:
        return []
    features = FEATURES if scope['format']=='showdown' else CLASSIC_FEATURES
    lines = field_report_lines(scope) + captain_defense_report(scope) + ['', 'Contest-by-contest construction comparisons',
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
