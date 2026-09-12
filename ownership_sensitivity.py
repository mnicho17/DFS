"""Paired ownership stress tests; frozen scoring inputs and candidate identities."""
import copy
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean
from build_snapshots import fingerprint
from repeatability import candidates, identity, atomic_json, model_version
from ownership_estimates import allocate
from ownership_strategy import exposures
from lineup_ranking import ranked_lineups
from optimizers import _pkey

PROFILES=('Baseline','Higher ownership for favorites','Concentrated field')
SENSITIVITY_VERSION = 'paired-ownership-v1'


def load_sensitivity_bank(path):
    # Old data-only banks are usable: all profiles, including baseline, are
    # recalculated under one current model. No old scores enter comparisons.
    if Path(path).stat().st_size>100*1024*1024:raise ValueError('Bank exceeds 100 MB')
    bank=json.loads(Path(path).read_text(encoding='utf-8'))
    p=bank.get('payload')
    if bank.get('schema')!=1 or bank.get('bank_id')!=fingerprint(p):raise ValueError('Invalid or modified bank')
    if p['kind'] not in ('classic','showdown') or not 1<=len(p['rows'])<=5000:raise ValueError('Unsupported bank')
    return bank


def ownership_profiles(payload):
    players=payload['players'];showdown=payload['kind']=='showdown'
    slots=[('Captain','ProjCptOwnPct',100),('FLEX','ProjFlexOwnPct',500)] if showdown else [('Total','ProjOwnPct',900)]
    for _,key,total in slots:
        vals=[]
        for p in players:
            try:v=float(p.get(key))
            except (TypeError,ValueError):raise ValueError('Complete slot-specific ownership is required')
            if p.get('OwnershipUnits')!='percent_of_entries' or not math.isfinite(v) or not 0<=v<=100:
                raise ValueError('Ownership percentages must have explicit units and be between 0 and 100')
            vals.append(v)
        if abs(sum(vals)-total)>max(2,len(players)*.051):raise ValueError('Ownership totals do not match roster slots')
    if showdown and any(float(p['ProjCptOwnPct'])+float(p['ProjFlexOwnPct'])>100.1 for p in players):
        raise ValueError('Combined Captain/FLEX ownership exceeds 100%')
    leaders=ranked_lineups(candidates(payload))[:150]
    counts=exposures(leaders,showdown);favorites={}
    for slot,key,_ in slots:
        ordered=sorted(players,key=lambda p: (-(counts.get((slot,_pkey(p)),0)-float(p[key])),_pkey(p)))
        favorites[slot]={_pkey(p) for p in ordered[:5] if counts.get((slot,_pkey(p)),0)>float(p[key])}
    profiles={PROFILES[0]:copy.deepcopy(players)}
    for name in PROFILES[1:]:
        altered=copy.deepcopy(players)
        for slot,key,total in slots:
            groups=defaultdict(list)
            for i,p in enumerate(players):groups['all' if showdown else str(p.get('Position'))].append(i)
            for indices in groups.values():
                original=[float(players[i][key]) for i in indices]
                weights=[v**1.35 if name==PROFILES[2] else v+(max(5,v) if _pkey(players[i]) in favorites[slot] else 0) for i,v in zip(indices,original)]
                target=total if showdown else sum(original)
                caps=[100-float(altered[i]['ProjCptOwnPct']) for i in indices] if showdown and slot=='FLEX' else None
                values=allocate(weights,target,caps)
                for i,v in zip(indices,values):altered[i][key]=v
        if showdown:
            for p in altered:p['ProjOwnPct']=p['ProjCptOwnPct']+p['ProjFlexOwnPct']
        profiles[name]=altered
    changes={}
    for name,altered in profiles.items():
        changes[name]=[dict(player=p.get('Name',_pkey(p)),slot=slot,before=float(p[key]),after=float(q[key]))
            for p,q in zip(players,altered) for slot,key,_ in slots if abs(float(q[key])-float(p[key]))>.001]
    return profiles,changes


def run_sensitivity(path,*,batches=3,scenarios=5000,cancelled=lambda:False,progress=lambda s:None,simulate=None):
    if not 1<=batches<=5 or not 1000<=scenarios<=10000:raise ValueError('Use 1–5 batches and 1,000–10,000 scenarios')
    bank=load_sensitivity_bank(path);p=bank['payload'];profiles,changes=ownership_profiles(p)
    ref=candidates(p);keys=[identity(lu,p['kind']) for lu in ref]
    if len(set(keys))!=len(keys):raise ValueError('Duplicate candidate identities')
    observations={name:[] for name in PROFILES};fields=[];seeds=[]
    for batch in range(batches):
        if cancelled():break
        seed=910003+batch*100003;paired={};batch_fields={}
        for name,opponents in profiles.items():
            if cancelled():break
            progress(f'Batch {batch+1}/{batches}: {name}')
            kwargs=dict(scenarios=scenarios,field_lineup_count=p['field_count'],salary_cap=p['salary_cap'],seed=seed,
                opponent_players=copy.deepcopy(opponents),cancel_callback=cancelled,
                progress_callback=lambda a,b,c:progress(f'Batch {batch+1}/{batches} — {name}: {a:,}/{b:,} {c}'))
            if simulate:result=simulate(candidates(p),copy.deepcopy(p['players']),**kwargs)
            elif p['kind']=='classic':
                from nfl_simulation import simulate_nfl_contest
                result=simulate_nfl_contest(candidates(p),copy.deepcopy(p['players']),field_config=copy.deepcopy(p['field_config']),**kwargs)
            else:
                from showdown_simulation import simulate_showdown
                result=simulate_showdown(candidates(p),copy.deepcopy(p['players']),**kwargs)
            if cancelled() or result.get('report',{}).get('scenarios')!=scenarios:break
            ordered=ranked_lineups(result.get('lineups') or [])
            found=[identity(lu,p['kind']) for lu in ordered]
            if len(found)!=len(keys) or set(found)!=set(keys):raise ValueError('Candidate identities changed')
            scored={identity(lu,p['kind']):dict(rank=i,top1=float(lu.sim_metrics['sim_top_one_pct']),
                mean=float(lu.sim_metrics['sim_mean']),matches=float(lu.sim_metrics.get('field_exact_matches',0))) for i,lu in enumerate(ordered,1)}
            if any(lu.sim_metrics.get('sim_scenarios')!=scenarios for lu in ordered):raise ValueError('Incomplete candidate scores')
            if PROFILES[0] in paired and any(abs(scored[k]['mean']-paired[PROFILES[0]][k]['mean'])>1e-8 for k in keys):
                raise ValueError('Player scoring changed across ownership profiles; comparison rejected')
            diag=result.get('report',{}).get('field_diagnostic') or {}
            if diag.get('candidate_fallback'):raise ValueError('Independent opponent field unavailable')
            paired[name]=scored;batch_fields[name]=diag
        if len(paired)!=3:break
        for name in PROFILES:observations[name].append(paired[name])
        fields.append(batch_fields);seeds.append(seed)
    rows=[]
    for i,(key,lu) in enumerate(zip(keys,ref),1):
        roster=[lu['Captain']]+lu['Flex'] if p['kind']=='showdown' else list(lu)
        label=' | '.join(('CPT: ' if p['kind']=='showdown' and j==0 else '')+str(v.get('Name','')) for j,v in enumerate(roster))
        for name in PROFILES:
            obs=[b[key] for b in observations[name]]
            row=dict(saved_rank=i,lineup=label,profile=name)
            if obs:
                base=mean(b[key]['top1'] for b in observations[PROFILES[0]])
                row.update(mean_top1=mean(v['top1'] for v in obs),change_pp=mean(v['top1'] for v in obs)-base,
                    mean_rank=mean(v['rank'] for v in obs),best_rank=min(v['rank'] for v in obs),worst_rank=max(v['rank'] for v in obs),
                    top150_batches=sum(v['rank']<=150 for v in obs),mean_sample_matches=mean(v['matches'] for v in obs),
                    batch_results=obs)
            rows.append(row)
    return dict(status='completed' if len(seeds)==batches else 'incomplete',sensitivity_version=SENSITIVITY_VERSION,bank_id=bank['bank_id'],input_id=p['input_id'],
        kind=p['kind'],saved_model=p['model_version'],current_model=model_version(),completed_batches=len(seeds),requested_batches=batches,
        scenarios=scenarios,opponents=p['field_count'],candidate_count=len(ref),seeds=seeds,changes=changes,fields=fields,rows=rows)


def format_sensitivity(r):
    n=r['completed_batches'];lines=['DFS Ownership Sensitivity',f"Status: {r['status']}; matched batches {n}/{r['requested_batches']}",
        f"Bank ID: {r['bank_id']}",f"Input ID: {r['input_id']}",
        f"{r['kind'].title()}: {r['candidate_count']} fixed candidates; {r['scenarios']} scenarios/profile/batch; {r['opponents']} sampled opponents.",
        'Baseline is recalculated under the current model. Old saved ranks label candidates only. Scoring inputs and outcome seeds are identical across profiles; incomplete three-profile batches are excluded.',
        'Favorites: up to five positive contender-minus-field gaps per slot from saved top150. Raw weights increase by max(5 percentage points, original ownership), then redistribute within Classic positions or Showdown slots.',
        'Concentrated field: ownership weights raised to power 1.35, then redistributed. Showdown Captain/FLEX total 100/500%, with combined player ownership capped at 100%. Classic position totals preserved.',
        'These are hypothetical ownership stresses, not forecasts. Matching to targets is approximate. Sample roster matches are not full-contest duplication predictions; zero matches do not establish uniqueness. No output ranks or exposures are changed.']
    lines.append('Ranks use top1, top2, top5, first-place rate and mean points; exact ties preserve bank order. Top150 uses the entire bank when smaller than 150. Repeating these settings uses the same seed sequence.')
    if n:
        lines += ['', 'Actual sampled fields (averages across completed batches):']
        for name in PROFILES:
            fields=[b[name] for b in r['fields'] if b[name].get('available')]
            if not fields:
                lines.append(f'- {name}: diagnostics unavailable.')
                continue
            keys=('captain_ownership','flex_ownership') if r['kind']=='showdown' else ('ownership',)
            gaps=[abs(row['gap_pp']) for f in fields for key in keys for row in f.get(key,[]) if row.get('gap_pp') is not None]
            mae=f'{mean(gaps):.2f} pp' if gaps else 'unavailable'
            lines.append(f"- {name}: {mean(f['entries'] for f in fields):.0f} opponents; ownership target MAE {mae}; salary ${mean(f['salary_mean'] for f in fields):,.0f}; repeated copies {mean(f['repeated_entry_pct'] for f in fields):.2f}%.")
    for name in PROFILES[1:]:
        lines+=['',name+' — largest target changes:']
        for c in sorted(r['changes'][name],key=lambda c:-abs(c['after']-c['before']))[:10]:
            lines.append(f"- {c['slot']} {c['player']}: {c['before']:.2f}% → {c['after']:.2f}%")
    if n:
        base=sorted([x for x in r['rows'] if x['profile']==PROFILES[0]],key=lambda x:-x['mean_top1'])[:15]
        lines+=['','Current baseline leaders and ownership sensitivity:']
        for b in base:
            lines.append(f"- Saved #{b['saved_rank']}: baseline top1 {b['mean_top1']:.2f}%, mean rank {b['mean_rank']:.1f}. {b['lineup']}")
            for x in [x for x in r['rows'] if x['saved_rank']==b['saved_rank'] and x['profile']!=PROFILES[0]]:
                lines.append(f"  {x['profile']}: top1 {x['mean_top1']:.2f}% ({x['change_pp']:+.2f} pp); mean rank {x['mean_rank']:.1f}; top150 {x['top150_batches']}/{n}; sample matches {x['mean_sample_matches']:.2f}.")
        lines+=['','Largest top1 declines under either stress:']
        declines=sorted([x for x in r['rows'] if x['profile']!=PROFILES[0] and x['change_pp']<0],key=lambda x:x['change_pp'])[:10]
        if not declines:lines.append('- No declines in the completed batches.')
        for x in declines:
            lines.append(f"- Saved #{x['saved_rank']}, {x['profile']}: {x['change_pp']:+.2f} pp; {x['lineup']}")
    lines.append('Privacy: player names and bank IDs included; no account settings or source paths. Full per-candidate data and field diagnostics saved with this report.')
    return '\n'.join(lines)


def save_sensitivity(path,report):
    atomic_json(path,report);Path(path).with_suffix('.txt').write_text(format_sensitivity(report),encoding='utf-8')
    with Path(path).with_suffix('.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=list(report['rows'][0]));w.writeheader();w.writerows(report['rows'])
