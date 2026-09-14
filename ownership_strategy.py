"""Ownership matching and candidate-relative leverage; no forecast training."""
import copy
import math
from collections import Counter
from optimizers import _pkey
from lineup_ranking import ranked_lineups


class MatchedField(list):
    pass


def exposures(entries, showdown):
    counts=Counter()
    for lu in entries:
        if showdown:
            counts[('Captain',_pkey(lu['Captain']))]+=1
            counts.update(('FLEX',_pkey(p)) for p in lu['Flex'])
        else:counts.update(('Total',_pkey(p)) for p in lu)
    return {k:100*v/max(1,len(entries)) for k,v in counts.items()}


def fit_field(initial, players, sample, *, showdown=False, cancelled=lambda:False):
    """At most two fresh legal fields; keep only a lower ownership-error field.

    Adjust draw weights, never clone entries to enforce ownership. Original
    forecasts and roster constraints are unchanged. Targets may be infeasible.
    """
    best=MatchedField(initial);best.diagnostic=dict(getattr(initial,'diagnostic',{}) or {})
    fields=[('Captain','ProjCptOwnPct','_FieldCptWeight',100),('FLEX','ProjFlexOwnPct','_FieldFlexWeight',500)] if showdown else [('Total','ProjOwnPct','_FieldOwnWeight',900)]
    targets={};reason=''
    for slot,key,_,total in fields:
        values=[]
        for p in players:
            try:v=float(p.get(key))
            except (TypeError,ValueError):reason='missing slot ownership';break
            if p.get('OwnershipUnits')!='percent_of_entries' or not math.isfinite(v) or not 0<=v<=100:
                reason='missing or unverified ownership units';break
            targets[(slot,_pkey(p))]=v;values.append(v)
        if reason:break
        if abs(sum(values)-total)>max(2,len(players)*.051):reason='ownership totals do not match roster slots';break
    info=dict(status='skipped',reason=reason or 'empty field',passes=0)
    best.ownership_fit=info
    if reason or not best:return best
    def error(entries):
        actual=exposures(entries,showdown)
        return sum(abs(actual.get(k,0)-v) for k,v in targets.items())/len(targets)
    def salary_gap(entries):
        diagnostic = getattr(entries, 'diagnostic', {}) or {}
        targets = diagnostic.get('salary_band_targets') or {}
        counts = diagnostic.get('salary_band_counts') or {}
        return sum(max(0, value-counts.get(key,0)) for key,value in targets.items())
    start=error(best);score=start;working=copy.deepcopy(players);current=best
    for attempt in range(2):
        if cancelled():break
        actual=exposures(current,showdown)
        for p in working:
            for slot,_,weight,_ in fields:
                ratio=(targets[(slot,_pkey(p))]+.2)/(actual.get((slot,_pkey(p)),0)+.2)
                p[weight]=max(.1,min(10,float(p.get(weight,1))*max(.5,min(2,ratio**.6))))
        trial=sample(working,attempt+1);info['passes']+=1
        if cancelled() or len(trial)!=len(initial):break
        current=trial;trial_error=error(trial)
        if trial_error<score and (not showdown or salary_gap(trial)<=salary_gap(best)):
            best=MatchedField(trial);best.diagnostic=dict(getattr(trial,'diagnostic',{}) or {});score=trial_error
    info.update(status='completed',reason='',before_mae_pp=round(start,3),after_mae_pp=round(score,3),targets=len(targets))
    best.ownership_fit=info
    return best


def leverage_report(candidates, selected, players, field, *, showdown=False):
    valid=[lu for lu in candidates if getattr(lu,'sim_metrics',{}).get('sim_scenarios',0)>0]
    leaders=ranked_lineups(valid)[:min(150,len(valid))]
    contender=exposures(leaders,showdown);ours=exposures(selected,showdown)
    rows=[]
    slots=[('Captain','captain_ownership','ProjCptOwnPct'),('FLEX','flex_ownership','ProjFlexOwnPct')] if showdown else [('Total','ownership','ProjOwnPct')]
    for slot,fieldkey,ownkey in slots:
        sampled={r.get('player_key'):r['sampled_pct'] for r in field.get(fieldkey,[])}
        for p in players:
            key=_pkey(p);v=p.get(ownkey)
            try:v=float(v) if p.get('OwnershipUnits')=='percent_of_entries' else None
            except (TypeError,ValueError):v=None
            if v is not None and (not math.isfinite(v) or not 0<=v<=100):v=None
            c=contender.get((slot,key),0) if leaders else None
            rows.append(dict(player=f"{p.get('Name',key)} [{p.get('Team','')} {p.get('Position','')}]",slot=slot,
                field_pct=v,sampled_pct=sampled.get(key),contender_pct=c,your_pct=ours.get((slot,key),0) if selected else None,
                gap_pp=c-v if c is not None and v is not None else None,
                confidence='Uncalibrated estimate' if v is not None else 'Ownership unavailable',
                source=p.get('OwnershipSource') or 'Not recorded'))
    return dict(candidate_count=len(valid),contender_count=len(leaders),selected_count=len(selected),rows=rows,
        note='Contenders are the top simulated 150 (or smaller bank), not optimal-lineup probabilities. Gaps depend on the searched candidates and model. Your exposure uses all selected outputs. No automatic exposure changes; high gaps are review signals, not proven value.')


def format_leverage(report):
    if not report:return []
    lines=['','Ownership and candidate leverage',f"- Contenders: {report['contender_count']}/{report['candidate_count']} scored candidates; your exposure: {report['selected_count']} outputs.",'- '+report['note']]
    for slot in sorted({r['slot'] for r in report['rows']}):
        rows=[r for r in report['rows'] if r['slot']==slot and r['gap_pp'] is not None]
        for title,group in [('Above field',sorted(rows,key=lambda r:-r['gap_pp'])[:5]),('Below field',sorted(rows,key=lambda r:r['gap_pp'])[:5])]:
            lines.append(f'- {slot} — {title}:')
            for r in group:
                lines.append(f"  {r['player']}: field {r['field_pct']:.1f}%; contender {r['contender_pct']:.1f}%; difference {r['gap_pp']:+.1f} pp; your exposure {r['your_pct'] or 0:.1f}%. {r['confidence']}.")
    return lines


def accuracy_lines(pairs, total):
    lines=['  Ownership forecast accuracy (earliest matched export per player/slot; pre-lock timing unverified):']
    for slot in sorted({r['slot'] for r in pairs.values()}):
        rows=[r for r in pairs.values() if r['slot']==slot]
        errors=[r['predicted']-r['actual'] for r in rows]
        lines.append(f"    {slot}: {len(rows)} unique players; MAE {sum(abs(e) for e in errors)/len(errors):.2f} pp; bias {sum(errors)/len(errors):+.2f} pp (forecast minus actual).")
        for r in sorted(rows,key=lambda r:abs(r['predicted']-r['actual']),reverse=True)[:5]:
            lines.append(f"      {r['player']}: forecast {r['predicted']:.2f}%; actual {r['actual']:.2f}%; source {r['source']}.")
    lines.append(f'    Comparable coverage: {len(pairs)}/{total} player/slot results. Unknown units or ambiguous legacy Showdown FLEX values excluded. Repeated lineup appearances count once; contests sharing a slate are not independent evidence. No tuning applied.')
    return lines
