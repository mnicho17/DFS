"""Read-only, source-qualified descriptive grouping review; never a model input."""
from collections import Counter,defaultdict
from contextlib import closing
import json,statistics
import analysis_imports as ai
from controlled_construction import Builder,MAX_ENTRIES,MAX_SIGNATURES
from opponent_history import _connect,VERSION
from showdown_common_groups import RULES

FEATURES={**RULES,'all_four':'Meets all four restrictions'}
COHORTS=('field','top1','top5','rank1','user')


def flags(slots):
    players=[p for _,_,p in slots]
    kinds=Counter(p['position'] for p in players)
    opposing=any(q['position']=='QB' and d['position']=='DST' and q['team']!=d['team'] for q in players for d in players)
    found=dict(kickers=kinds['K']>=2,defenses=kinds['DST']>=2,quarterbacks=kinds['QB']>=2,qb_dst=opposing)
    return {**found,'all_four':not any(found.values())}


def contest_rows(conn,key,players,accepted,field_size,user,cancelled):
    builder=Builder(players,accepted,field_size)
    total=conn.execute('SELECT COUNT(*) FROM opponent_entries WHERE contest_key=?',(key,)).fetchone()[0]
    if total!=accepted or not 0<total<=MAX_ENTRIES:raise ValueError('Indexed entry coverage differs or exceeds the review bound.')
    valid="typeof(rank)='integer' AND rank BETWEEN 1 AND 9007199254740991"
    sql=f'''SELECT signature_json,metadata_complete,MIN(entry_id),COUNT(*),
        SUM(CASE WHEN {valid} THEN 1 ELSE 0 END),
        SUM(CASE WHEN {valid} AND rank<=? THEN 1 ELSE 0 END),
        SUM(CASE WHEN {valid} AND rank<=? THEN 1 ELSE 0 END),
        SUM(CASE WHEN {valid} AND rank=1 THEN 1 ELSE 0 END),
        SUM(CASE WHEN {valid} AND user_key=? THEN 1 ELSE 0 END)
        FROM opponent_entries WHERE contest_key=? GROUP BY signature_json,metadata_complete
        ORDER BY signature_json,metadata_complete'''
    stats={cohort:dict(known=0,features=Counter()) for cohort in COHORTS}
    unknown=missing=0
    for i,(sig,known,eid,n,ranked,top1,top5,rank1,own) in enumerate(conn.execute(sql,((builder.cutoff+99)//100,(builder.cutoff+19)//20,user,key))):
        ai._check(cancelled)
        if i>=MAX_SIGNATURES:raise ValueError('Roster review bound reached; no partial contest comparison published.')
        missing+=n-ranked
        try:parsed=json.loads(sig);readable=known==1 and isinstance(parsed,list) and len(parsed)==6
        except (ValueError,TypeError):readable=False
        slots=[]
        if readable:
            for role,label,ident,name,pos,team,salary in conn.execute('SELECT role,original_label,player_id,player_name,position,team,salary FROM opponent_entry_slots WHERE contest_key=? AND entry_id=? ORDER BY ordinal LIMIT 10',(key,eid)):
                slots.append((label,role,dict(id=ident,name=name,position=pos,team=team,salary=salary)))
        if not readable or builder._resolve(slots) is None:unknown+=n;continue
        features=flags(slots)
        for cohort,count in zip(COHORTS,(ranked,top1,top5,rank1,own)):
            stats[cohort]['known']+=count
            stats[cohort]['features'].update({k:count*value for k,value in features.items()})
    return dict(cohorts=stats,unknown=unknown,missing_rank=missing,cutoff=builder.cutoff,rank_basis=builder.basis)


def summarize(contests):
    result={}
    for feature in FEATURES:
        row={}
        for cohort in COHORTS:
            n=sum(c['cohorts'][cohort]['known'] for c in contests)
            count=sum(c['cohorts'][cohort]['features'].get(feature,0) for c in contests)
            row[cohort]=dict(count=count,denominator=n,pct=100*count/n if n else None)
        gaps=defaultdict(list)
        for c in contests:
            field,top=c['cohorts']['field'],c['cohorts']['top1']
            if field['known'] and top['known']:
                gaps[tuple(c['game'])].append(100*(top['features'].get(feature,0)/top['known']-field['features'].get(feature,0)/field['known']))
        slate_gaps=[statistics.mean(v) for _,v in sorted(gaps.items())]
        row['slates']=len(slate_gaps)
        row['equal_slate_gap']=statistics.mean(slate_gaps) if slate_gaps else None
        row['omit_one_range']=[min(statistics.mean(slate_gaps[:i]+slate_gaps[i+1:]) for i in range(len(slate_gaps))),max(statistics.mean(slate_gaps[:i]+slate_gaps[i+1:]) for i in range(len(slate_gaps)))] if len(slate_gaps)>=3 else None
        result[feature]=row
    return result


def review_history(db_path,cancelled=lambda:False,progress=lambda text:None,*,username='',preset=''):
    from opponent_analysis import username_key
    from learning_db import _infer_field_preset
    contests=[];excluded=[];verified={};manifests={}
    with closing(_connect(db_path,True)) as conn:
        conn.execute('BEGIN')
        conn.set_progress_handler(lambda:int(cancelled()),10000)
        sources={s['hash']:s for s in ai._sources(conn)}
        pairs=dict(conn.execute('SELECT result_hash,salary_hash FROM analysis_salary_pairs'))
        rows=conn.execute("SELECT contest_key,result_hash,salary_hash,name,start_date,end_date,field_size,complete,audit_json FROM opponent_contests WHERE format='showdown' AND version=? ORDER BY end_date,contest_key",(VERSION,)).fetchall()
        if len(rows)>100:raise ValueError('Review supports up to 100 indexed Showdown contests; no partial history published.')
        for i,(key,r,s,name,start,end,size,complete,audit) in enumerate(rows):
            ai._check(cancelled);progress(f'Reviewing grouping constructions {i+1}/{len(rows)}: {name}')
            band=_infer_field_preset(name)
            if preset and band!=preset:continue
            try:
                if r not in sources or s not in sources or pairs.get(r)!=s:raise ValueError('Saved salary association is unresolved.')
                for digest in (r,s):
                    if digest not in verified:ai._verify(sources[digest],cancelled);verified[digest]=sources[digest]
                if not ai.qualify_pair(sources[r],sources[s])['compatible']:raise ValueError('Saved salary association does not qualify.')
                if s not in manifests:manifests[s]=ai._salary_manifest(sources[s]['snapshot'],cancelled)
                manifest=manifests[s]
                if len(manifest['dates'])!=1 or len(manifest['games'])!=1 or start!=end or end!=manifest['dates'][0]:raise ValueError('Exact single-game date is unresolved.')
                audit=json.loads(audit);accepted=audit.get('accepted_entries')
                if type(accepted) is not int or audit.get('conflicting_entry_ids_excluded')!=0:raise ValueError('Entry identity conflicts or invalid indexing receipt.')
                values=contest_rows(conn,key,manifest['players'],accepted,size,'dk:'+username_key(username) if username.strip() else '',cancelled)
                contests.append(dict(name=name,source=r,salary_source=s,date=end,game=[end,manifest['games'][0]],preset=band,complete_field=bool(complete),**values))
            except ai.ImportCancelled:raise
            except Exception as exc:
                ai._check(cancelled);excluded.append(dict(name=name,reason=str(exc)))
        conn.set_progress_handler(None,0)
    # Recheck current associations and immutable sources before publishing a report.
    with closing(_connect(db_path,True)) as conn:
        current=dict(conn.execute('SELECT result_hash,salary_hash FROM analysis_salary_pairs'))
        for c in contests:
            if current.get(c['source'])!=c['salary_source']:raise ValueError('Salary association changed during review; rerun.')
    for source in verified.values():ai._verify(source,cancelled)
    ai._check(cancelled)
    return dict(contests=contests,excluded=excluded,summary=summarize(contests),username=username,
        by_preset={p:summarize([c for c in contests if c['preset']==p]) for p in sorted({c['preset'] for c in contests})})


def render(report):
    def pct(v):return 'unavailable' if v['pct'] is None else f"{v['pct']:.1f}% ({v['count']:,}/{v['denominator']:,})"
    contests=report['contests']
    lines=[f"SHOWDOWN GROUPING REVIEW — {len(contests)} verified indexed contests; {len({tuple(c['game']) for c in contests})} games",'Presence of a construction, not a recommendation to block it. Recorded standings only; time spent in first place is not recorded.',
        'Top 1% / 5% use supplied field-size rank cutoffs when available, otherwise observed accepted-entry count. Export completeness is unverified unless explicitly recorded; these are descriptive rank-cutoff cohorts, not ROI or causal effects.',
        f"Unknown construction entries excluded: {sum(c['unknown'] for c in contests):,}; missing-rank entries excluded from comparisons: {sum(c['missing_rank'] for c in contests):,}."]
    if contests:lines.append(f"Dates: {min(c['date'] for c in contests)} through {max(c['date'] for c in contests)}; complete-field receipts: {sum(c['complete_field'] for c in contests)}/{len(contests)}.")
    for heading,summary in [('All selected contests',report['summary']),*[(p,s) for p,s in report['by_preset'].items()]]:
        lines+=['',heading]
        for feature,label in FEATURES.items():
            row=summary[feature];label=('2+ kickers' if feature=='kickers' else '2+ defenses' if feature=='defenses' else '2+ quarterbacks' if feature=='quarterbacks' else 'QB with opposing defense' if feature=='qb_dst' else label)
            gap=row['equal_slate_gap'];gap_text=f"{gap:+.2f} pp across {row['slates']} games" if gap is not None else 'unavailable'
            lines += [label+':',f"  Field {pct(row['field'])}; top 1% {pct(row['top1'])}; top 5% {pct(row['top5'])}; rank 1/ties {pct(row['rank1'])}",f"  Equal-game top-1% minus field: {gap_text}; omit-one-game range: {row['omit_one_range'] or 'unavailable'}." ]
            if report.get('username'):lines.append(f"  Your ranked entries: {pct(row['user'])}")
    if report['excluded']:lines+=['','Excluded contests:']+[c['name']+': '+c['reason'] for c in report['excluded']]
    lines+=['','Construction does not prove which rules a user selected. Captain-locked portfolios are not evidence of normal SIM behavior.','Entry counts weight duplicate roster entries intentionally; index validation removes repeated EntryId copies. Games receive equal weight in the gap statistic. Entry-limit groups are shown separately. No defaults, forecasts, SIM or saved rules are changed.']
    return '\n'.join(lines)
