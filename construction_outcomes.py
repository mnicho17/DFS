"""Read-only finish-rate contrasts with exact Captain and salary-band overlap."""
from collections import defaultdict,OrderedDict
import statistics
import analysis_imports as ai
from controlled_construction import Builder,MAX_ENTRIES
from portfolio_pattern_audit import features,METRICS,LABELS,MAX_CACHE,MAX_RESOLUTIONS,PatternAuditLimit

ENDPOINTS=('top1','top5','rank1')
MIN_SIDE=5


def contrast(cells,minimum=MIN_SIDE):
    """Overlap weights n0*n1/(n0+n1); never infer outcomes for missing ranks."""
    out={}
    for i,metric in enumerate(METRICS):
        pairs=[v for (stratum,j),v in cells.items() if j==i and min(v[0][0],v[1][0])>=minimum]
        weights=[v[0][0]*v[1][0]/(v[0][0]+v[1][0]) for v in pairs]
        weight=sum(weights)
        out[metric]=dict(strata=len(pairs),entries=sum(v[0][0]+v[1][0] for v in pairs),
            difference_pp={endpoint:100*sum(w*(v[1][k]/v[1][0]-v[0][k]/v[0][0]) for w,v in zip(weights,pairs))/weight if weight else None for k,endpoint in enumerate(ENDPOINTS,1)})
    return out


def contest_outcomes(conn,key,players,accepted,field_size,user,cancelled):
    if type(accepted) is not int or not 0<accepted<=MAX_ENTRIES:raise PatternAuditLimit('Outcome entry bound exceeded.')
    builder=Builder(players,accepted,field_size)
    if builder.reason:raise ValueError(builder.reason)
    cells=defaultdict(lambda:[[0,0,0,0],[0,0,0,0]])
    cache=OrderedDict();unknown=missing=reads=resolutions=slot_reads=0
    cursor=conn.execute('SELECT entry_id,metadata_complete,rank FROM opponent_entries WHERE contest_key=? ORDER BY entry_id',(key,))
    while block:=cursor.fetchmany(200):
        ai._check(cancelled);reads+=len(block)
        if reads>MAX_ENTRIES:raise PatternAuditLimit('Outcome entry bound exceeded.')
        ids=[r[0] for r in block];raw=defaultdict(list)
        sql='SELECT entry_id,ordinal,role,original_label,player_id,player_name,position,team,salary FROM opponent_entry_slots WHERE contest_key=? AND entry_id IN ('+','.join('?' for _ in ids)+') ORDER BY entry_id,ordinal'
        for eid,*row in conn.execute(sql,[key,*ids]):
            slot_reads+=1
            if slot_reads>6*MAX_ENTRIES:raise PatternAuditLimit('Outcome slot bound exceeded.')
            if len(raw[eid])<8:raw[eid].append(tuple(row))
        for eid,complete,rank in block:
            ranked=type(rank) is int and 1<=rank<=2**53-1
            if not ranked:missing+=1
            rows=raw[eid];identity=tuple(rows)
            if complete!=1 or [r[0] for r in rows]!=list(range(6)):unknown+=1;continue
            if identity not in cache:
                resolutions+=1
                if resolutions>MAX_RESOLUTIONS:raise PatternAuditLimit('Outcome resolution bound reached; narrow the review.')
                slots=[(label,role,dict(id=ident,name=name,position=pos,team=team,salary=salary)) for ordinal,role,label,ident,name,pos,team,salary in rows]
                resolved=builder._resolve(slots)
                cache[identity]=(tuple(resolved[0].split('|')[:2]),features(slots)) if resolved else None
                if len(cache)>MAX_CACHE:cache.popitem(last=False)
            else:cache.move_to_end(identity)
            value=cache[identity]
            if value is None:unknown+=1;continue
            if not ranked:continue
            stratum,pattern=value
            outcomes=(1,int(rank<=(builder.cutoff+99)//100),int(rank<=(builder.cutoff+19)//20),int(rank==1))
            for i,present in enumerate(pattern):
                side=cells[stratum,i][present]
                for k,n in enumerate(outcomes):side[k]+=n
    if reads!=accepted:raise ValueError('Outcome entry count differs from the indexed receipt.')
    ai._check(cancelled)
    return dict(accepted=accepted,unknown=unknown,missing_rank=missing,rank_basis=builder.basis,matched=contrast(cells))


def summarize_outcomes(contests):
    result={}
    for metric in METRICS:
        games=defaultdict(list)
        for c in contests:
            row=c['matched'][metric]
            if row['strata']:games[tuple(c['game'])].append(row['difference_pp'])
        values={e:[statistics.mean(r[e] for r in rows) for rows in games.values()] for e in ENDPOINTS}
        result[metric]=dict(games=len(games),endpoints={e:dict(difference_pp=statistics.mean(v) if v else None,
            omit_one_range=[min(statistics.mean(v[:i]+v[i+1:]) for i in range(len(v))),max(statistics.mean(v[:i]+v[i+1:]) for i in range(len(v)))] if len(v)>=3 else None) for e,v in values.items()})
    return result


def review_outcomes(db_path,cancelled=lambda:False,progress=lambda text:None,*,preset=''):
    from historical_series_review import _inventory
    from showdown_group_review import review_history
    before=_inventory(db_path)
    result=review_history(db_path,cancelled,progress,preset=preset,_outcomes=True)
    if _inventory(db_path)!=before:raise ValueError('Historical index changed during outcome review; rerun.')
    ai._check(cancelled)
    return result


def render(report):
    lines=['CONSTRUCTION FINISH-RATE REVIEW',f"Verified contests: {len(report['contests'])}; excluded: {len(report['excluded'])}.",
        'Feature-present minus feature-absent finish rate, matched within contest, exact Captain ID and salary band.',
        'At least five ranked, resolved entries on each side per stratum. Overlap weights n0*n1/(n0+n1); contests averaged within games, then games equally.',
        'Supplied/observed rank cutoffs; complete field unverified. All rank-one ties count. Missing ranks and unresolved rosters are excluded, never losses.',
        'These are descriptive associations, not causal effects, profitability or rule recommendations. No generation settings change.']
    for preset,summary in report['by_preset'].items():
        lines+=['',preset or 'Unknown entry limit']
        for metric,row in summary.items():
            lines.append(f"{LABELS[metric]}: {row['games']} supported games")
            for endpoint,r in row['endpoints'].items():
                value=r['difference_pp'];lines.append(f"  {endpoint}: {value:+.3f} pp; omit-one {r['omit_one_range']}" if value is not None else f'  {endpoint}: unavailable (no common support)')
    lines+=['','Contest coverage (matched entries differ by feature):']
    for c in report['contests']:
        lines.append(f"{c['name']}: {c['accepted']} entries; {c['unknown']} unresolved; {c['missing_rank']} missing ranks; "+'; '.join(f"{m}: {v['strata']} strata / {v['entries']} matched" for m,v in c['matched'].items()))
    lines+=['Excluded: '+c['name']+': '+c['reason'] for c in report['excluded']]
    return '\n'.join(lines)
