"""Read-only finish-rate contrasts with exact Captain and salary-band overlap."""
from collections import defaultdict,OrderedDict
import statistics
import analysis_imports as ai
from controlled_construction import Builder,MAX_ENTRIES,MAX_CELLS
from portfolio_pattern_audit import features as base_features,METRICS as BASE_METRICS,LABELS as BASE_LABELS,MAX_CACHE,MAX_RESOLUTIONS,PatternAuditLimit
from showdown_group_review import flags

ENDPOINTS=('top1','top5','rank1')
MIN_SIDE=5
SUPPORT_MINIMUMS=(5,10)
RULE_METRICS=('two_kickers_pct','two_defenses_pct','two_quarterbacks_pct','qb_opposing_dst_pct')
METRICS=BASE_METRICS+RULE_METRICS[:3]
RULE_INDICES=tuple(METRICS.index(metric) for metric in RULE_METRICS)
LABELS={**BASE_LABELS,'two_kickers_pct':'Two or more kickers','two_defenses_pct':'Two or more defenses','two_quarterbacks_pct':'Two or more quarterbacks'}


def outcome_features(slots):
    value=flags(slots)
    return base_features(slots)+tuple(int(value[k]) for k in ('kickers','defenses','quarterbacks'))


def contrast(cells,minimum=MIN_SIDE,*,metrics=METRICS):
    """Overlap weights n0*n1/(n0+n1); never infer outcomes for missing ranks."""
    out={}
    for i,metric in enumerate(metrics):
        pairs=[v for (stratum,j),v in cells.items() if j==i and min(v[0][0],v[1][0])>=minimum]
        weights=[v[0][0]*v[1][0]/(v[0][0]+v[1][0]) for v in pairs]
        weight=sum(weights)
        out[metric]=dict(strata=len(pairs),entries=sum(v[0][0]+v[1][0] for v in pairs),
            difference_pp={endpoint:100*sum(w*(v[1][k]/v[1][0]-v[0][k]/v[0][0]) for w,v in zip(weights,pairs))/weight if weight else None for k,endpoint in enumerate(ENDPOINTS,1)})
    return out


def contest_outcomes(conn,key,players,accepted,field_size,user,cancelled,*,_features=None,_metrics=None,_rule_metrics=None,_observed=False):
    feature_fn=_features or outcome_features
    metrics=METRICS if _metrics is None else _metrics
    rule_metrics=RULE_METRICS if _rule_metrics is None else _rule_metrics
    rule_indices=tuple(metrics.index(m) for m in rule_metrics)
    observed={m:[[0,0,0,0],[0,0,0,0]] for m in metrics}
    if type(accepted) is not int or not 0<accepted<=MAX_ENTRIES:raise PatternAuditLimit('Outcome entry bound exceeded.')
    builder=Builder(players,accepted,field_size)
    if builder.reason:raise ValueError(builder.reason)
    cells=defaultdict(lambda:[[0,0,0,0],[0,0,0,0]]);conditional=defaultdict(lambda:[[0,0,0,0],[0,0,0,0]])
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
                cache[identity]=(tuple(resolved[0].split('|')[:2]),feature_fn(slots)) if resolved else None
                if len(cache)>MAX_CACHE:cache.popitem(last=False)
            else:cache.move_to_end(identity)
            value=cache[identity]
            if value is None:unknown+=1;continue
            if not ranked:continue
            stratum,pattern=value
            outcomes=(1,int(rank<=(builder.cutoff+99)//100),int(rank<=(builder.cutoff+19)//20),int(rank==1))
            for i,present in enumerate(pattern):
                if present is None:continue
                if present not in (0,1):raise ValueError('Outcome feature is not binary.')
                if _observed:
                    for k,n in enumerate(outcomes):observed[metrics[i]][present][k]+=n
                side=cells[stratum,i][present]
                for k,n in enumerate(outcomes):side[k]+=n
            rules=tuple(pattern[i] for i in rule_indices)
            for i,present in enumerate(rules):
                fixed=rules[:i]+rules[i+1:]
                side=conditional[(stratum,fixed),i][present]
                for k,n in enumerate(outcomes):side[k]+=n
            if len(cells)+len(conditional)>MAX_CELLS:
                raise PatternAuditLimit('Outcome comparison-cell bound reached; no partial report published.')
    if reads!=accepted:raise ValueError('Outcome entry count differs from the indexed receipt.')
    ai._check(cancelled)
    checks={str(n):dict(matched=contrast(cells,n,metrics=metrics),other_rules_fixed=contrast(conditional,n,metrics=rule_metrics)) for n in SUPPORT_MINIMUMS}
    return dict(accepted=accepted,unknown=unknown,missing_rank=missing,rank_basis=builder.basis,matched=checks['5']['matched'],other_rules_fixed=checks['5']['other_rules_fixed'],support_checks=checks,**({'observed':observed} if _observed else {}))


def _game_summary(contests,metric,comparison,minimum=None):
    games=defaultdict(list)
    for c in contests:
        source=c if minimum is None else c.get('support_checks',{}).get(str(minimum),{})
        row=source.get(comparison,{}).get(metric)
        if row and row['strata']:games[tuple(c['game'])].append(row['difference_pp'])
    values={e:[statistics.mean(r[e] for r in rows) for _,rows in sorted(games.items())] for e in ENDPOINTS}
    return dict(games=len(games),game_differences=[dict(game=list(game),difference_pp={e:statistics.mean(r[e] for r in rows) for e in ENDPOINTS}) for game,rows in sorted(games.items())],
        endpoints={e:dict(difference_pp=statistics.mean(v) if v else None,
        higher_games=sum(x>0 for x in v),lower_games=sum(x<0 for x in v),equal_games=sum(x==0 for x in v),
        omit_one_range=[min(statistics.mean(v[:i]+v[i+1:]) for i in range(len(v))),max(statistics.mean(v[:i]+v[i+1:]) for i in range(len(v)))] if len(v)>=3 else None) for e,v in values.items()})


def summarize_outcomes(contests):
    result={m:_game_summary(contests,m,'matched') for m in METRICS}
    for metric in RULE_METRICS:result[metric]['other_rules_fixed']=_game_summary(contests,metric,'other_rules_fixed')
    for metric,row in result.items():
        row['support_checks']={str(n):dict(matched=_game_summary(contests,metric,'matched',n),**({'other_rules_fixed':_game_summary(contests,metric,'other_rules_fixed',n)} if metric in RULE_METRICS else {})) for n in SUPPORT_MINIMUMS}
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
    lines.append('Fixed sensitivity checks require at least five and ten observed entries on each side per comparison stratum, from the same verified scan. Counts include legitimate repeated lineups; they are not independent users or a significance test. Missing older support checks remain unavailable.')
    lines.append('Individual grouping comparisons also hold the other three grouping flags fixed. Unsupported combinations remain unavailable; they are not inferred from the primary comparison.')
    for preset,summary in report['by_preset'].items():
        lines+=['',preset or 'Unknown entry limit']
        for metric,row in summary.items():
            lines.append(f"{LABELS[metric]}: {row['games']} supported games")
            for label,detail in [('Captain/salary matched',row),('Other three grouping conditions also fixed',row.get('other_rules_fixed'))]:
                if detail is None:continue
                lines.append(f"  {label}: {detail['games']} supported games")
                for endpoint,r in detail['endpoints'].items():
                    value=r['difference_pp'];counts=f"higher/lower/equal games {r.get('higher_games','?')}/{r.get('lower_games','?')}/{r.get('equal_games','?')}"
                    lines.append(f"    {endpoint}: {value:+.3f} pp; {counts}; omit-one {r['omit_one_range']}" if value is not None else f'    {endpoint}: unavailable (no common support)')
                for game in detail.get('game_differences',[]):
                    lines.append('    '+ ' / '.join(game['game'])+': '+', '.join(f"{e} {v:+.3f} pp" for e,v in game['difference_pp'].items()))
            lines.append('  Fixed per-side support checks:')
            for n in SUPPORT_MINIMUMS:
                checks=row.get('support_checks',{}).get(str(n))
                if checks is None:
                    lines.append(f'    {n} entries per side: unavailable (no saved support check)');continue
                for mode,detail in checks.items():
                    lines.append(f"    {n} entries per side / {mode}: {detail['games']} games")
                    for e,v in detail['endpoints'].items():
                        gap=v['difference_pp'];text='unavailable' if gap is None else f'{gap:+.3f} pp'
                        lines.append(f"      {e}: {text}; higher/lower/equal {v['higher_games']}/{v['lower_games']}/{v['equal_games']}; omit-one {v['omit_one_range']}")
    lines+=['','Contest coverage (matched entries differ by feature):']
    for c in report['contests']:
        lines.append(f"{c['name']}: {c['accepted']} entries; {c['unknown']} unresolved; {c['missing_rank']} missing ranks; "+'; '.join(f"{m}: {v['strata']} strata / {v['entries']} matched" for m,v in c['matched'].items()))
        for metric,v in c.get('other_rules_fixed',{}).items():lines.append(f"  Other grouping flags fixed — {metric}: {v['strata']} strata / {v['entries']} matched")
        for n,checks in c.get('support_checks',{}).items():
            for mode,metrics in checks.items():
                lines.append(f'  {n} per side / {mode}: '+ '; '.join(f"{m}: {v['strata']} strata / {v['entries']} matched" for m,v in metrics.items()))
    lines+=['Excluded: '+c['name']+': '+c['reason'] for c in report['excluded']]
    return '\n'.join(lines)
