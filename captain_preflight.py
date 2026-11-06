"""Captured candidate-bank coverage, not a portfolio feasibility proof."""
from collections import Counter
from portfolio_rules import player_key, _candidate_signature, _min_count, normalize_rules
from captain_pool import allocation_targets


def inspect(rows, retained, players, rules, requested, cancelled=lambda:False):
    normalized=normalize_rules(rules)
    targets=allocation_targets(normalized,requested)
    configured={player_key(p):dict(p) for p in players}
    for key,row in normalized['player_constraints'].items():
        configured.setdefault(key,{}).update({k:v for k,v in row.items() if v is not None})
    names={player_key(p):str(p.get('Name') or player_key(p)) for p in players}
    locked={player_key(p) for p in players if p.get('LockCpt')}
    locked.update(k for k,p in configured.items() if p.get('LockCpt'))
    focus=locked | {k for k,p in configured.items() if _min_count(p.get('MinCptPct'),requested)>0}
    seen=set();counts=Counter();retained_seen=set()
    for group,items in (('retained',retained),('candidates',rows)):
        for i,row in enumerate(items):
            if i%200==0 and cancelled():raise ValueError('Candidate coverage cancelled')
            signature=_candidate_signature(row,'showdown')
            if not signature:continue
            if group=='retained':retained_seen.add(signature)
            if signature in seen:continue
            seen.add(signature);counts[player_key(row.get('Captain') or {})]+=1
    if cancelled():raise ValueError('Candidate coverage cancelled')
    details=[]
    for key in sorted(focus):
        target=targets.get(key,_min_count(configured.get(key,{}).get('MinCptPct'),requested))
        details.append(dict(player=names.get(key,str(configured.get(key,{}).get('Name') or key)),
            available=counts[key],target=target,target_source='generated allocation' if key in targets else 'manual minimum' if target else 'pool membership',
            shortfall=max(0,target-counts[key])))
    return dict(requested=requested,distinct_candidates=len(seen),retained_distinct=len(retained_seen),captains=details)


def summary(capture):
    short=[r for r in capture['captains'] if r['shortfall']]
    return f"Candidate coverage: {capture['distinct_candidates']:,} distinct signatures; {len(short)} Captain target(s) short. Counts do not prove portfolio feasibility."


def lines(stages):
    if not stages:return []
    result=['','CAPTAIN CANDIDATE COVERAGE BY STAGE',
        'Counts include distinct Captain/FLEX signatures and retained rows. Other rules and joint feasibility are not evaluated here; a short stage is not proof of slate infeasibility. Stage counts do not establish a rejection cause.']
    labels={'library_loaded':'Accepted library bank before scoring','generated':'Generated/expanded bank before selection','salary_eligible':'Before screening, after salary filtering','shortlisted':'After shortlisting','validated':'Independent validation output','selection_bank':'Available to final selection','selected':'Selected output'}
    for stage in list(labels) + sorted(set(stages)-set(labels)):
        if stage not in stages:continue
        capture=stages[stage]
        result.append(f"- {labels.get(stage,stage)}: {capture['distinct_candidates']:,} distinct signatures for {capture['requested']:,} requested; {capture['retained_distinct']:,} retained.")
        for row in capture['captains']:
            result.append(f"  {row['player']}: {row['available']:,} available; target {row['target']:,} ({row['target_source']}); shortfall {row['shortfall']:,}.")
    result.append('Targets are initial build targets. App-generated allocations may redistribute automatically, including to zero. Manual minimums and uniqueness remain enforced. Missing stages were not captured; no values are inferred.')
    return result
