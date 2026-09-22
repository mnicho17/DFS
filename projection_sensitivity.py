"""Explicit scoring stresses on frozen candidates and identical opponent rosters."""
import copy
import csv
import hashlib
import json
import math
import random
from collections import Counter
from pathlib import Path
from statistics import mean
from repeatability import candidates, identity, model_version, atomic_json
from ownership_sensitivity import load_sensitivity_bank
from lineup_ranking import ranked_lineups
from nfl_simulation import player_key

PROFILES = ('Baseline', 'Lower production for favorites', 'Wider limited-history outcomes')
VERSION = 'projection-stress-v3'
from usage_history import history_evidence

def roster(lu, kind):
    return [lu['Captain']] + list(lu['Flex']) if kind == 'showdown' else list(lu)

def prepare_targets(payload):
    reference = candidates(payload)
    if payload['kind']=='classic':
        from nfl_simulation import build_nfl_role_pool
        pool=build_nfl_role_pool(payload['players'],preserve_locks=False)
    else:
        from showdown_simulation import active_showdown_players
        pool=active_showdown_players(payload['players'])
    players = {player_key(p): p for p in pool}
    for lu in reference:
        for p in roster(lu, payload['kind']): players.setdefault(player_key(p), p)
    skills = {k:p for k,p in players.items() if p.get('Position') in ('QB','RB','WR','TE')}
    leaders = reference[:150]
    counts = Counter(k for lu in leaders for k in {player_key(p) for p in roster(lu,payload['kind'])})
    selected = sorted((k for k in skills if counts[k]), key=lambda k:(-counts[k],k))[:5]
    lower = [dict(key=k, player=skills[k].get('Name',k), reason='High saved top150 exposure',
                  exposure_pct=100*counts[k]/max(1,len(leaders)), multiplier=.85) for k in selected]
    wider = []
    for k,p in sorted(skills.items()):
        evidence = history_evidence(p)
        if not evidence['stress']: continue
        wider.append(dict(key=k, player=p.get('Name',k), usage_games=p.get('NFLUsageGames'),
                          low=.75, high=1.25, **evidence))
    return {PROFILES[0]:[], PROFILES[1]:lower, PROFILES[2]:wider}

def comparison_targets(payload, individual=True):
    targets = prepare_targets(payload)
    if individual:
        for i, target in enumerate(targets[PROFILES[1]], 1):
            targets[f"Lower production: {target['player']} (individual {i})"] = [dict(target)]
    return targets


class OutcomeStress:
    def __init__(self, profile, targets, seed):
        self.profile=profile; self.targets={p['key'] for p in targets}
        self.rng=random.Random(seed+700001); self.digest=hashlib.sha256(); self.count=0
        self.sums={k:[0.0,0.0,0] for k in self.targets}
    def __call__(self, outcomes):
        self.digest.update(json.dumps(outcomes,sort_keys=True,allow_nan=False,separators=(',',':')).encode())
        result=dict(outcomes); self.count+=1
        for k in sorted(self.targets):
            if k not in outcomes: continue
            scale = .85 if self.profile!=PROFILES[2] else (.75 if self.rng.random()<.5 else 1.25)
            result[k]=outcomes[k]*scale
            sums=self.sums[k]; sums[0]+=outcomes[k]; sums[1]+=result[k]; sums[2]+=1
        return result
    def evidence(self):
        return dict(base_outcome_id=self.digest.hexdigest(),scenarios=self.count,
                    player_means={k:dict(baseline=a/n,stressed=b/n) for k,(a,b,n) in self.sums.items() if n})

def run_projection(path, *, batches=3, scenarios=5000, cancelled=lambda:False, progress=lambda s:None, simulate=None, individual=True):
    if not 1<=batches<=5 or not 1000<=scenarios<=10000: raise ValueError('Use 1–5 batches and 1,000–10,000 scenarios.')
    bank=load_sensitivity_bank(path); p=bank['payload']; targets=comparison_targets(p, individual); profiles=tuple(targets)
    reference=candidates(p); keys=[identity(lu,p['kind']) for lu in reference]
    if len(set(keys))!=len(keys): raise ValueError('Duplicate candidate identities.')
    observations={name:[] for name in profiles}; evidence=[]; seeds=[]; fields=[]
    for batch in range(batches):
        if cancelled(): break
        seed=1200007+batch*100003; paired={}; proof={}; diagnostics={}; field_id=None; base_id=None
        for name in profiles:
            if cancelled(): break
            progress(f'Batch {batch+1}/{batches}: {name}')
            transform=OutcomeStress(name,targets[name],seed)
            kw=dict(scenarios=scenarios,field_lineup_count=p['field_count'],salary_cap=p['salary_cap'],seed=seed,
                    outcome_transform=transform,cancel_callback=cancelled,
                    progress_callback=lambda a,b,c:progress(f'Batch {batch+1}/{batches} — {name}: {a:,}/{b:,}'))
            if simulate: result=simulate(candidates(p),copy.deepcopy(p['players']),**kw)
            elif p['kind']=='classic':
                from nfl_simulation import simulate_nfl_contest
                result=simulate_nfl_contest(candidates(p),copy.deepcopy(p['players']),field_config=copy.deepcopy(p['field_config']),**kw)
            else:
                from showdown_simulation import simulate_showdown
                result=simulate_showdown(candidates(p),copy.deepcopy(p['players']),**kw)
            report=result.get('report',{})
            if cancelled() or report.get('scenarios')!=scenarios: break
            ev=transform.evidence(); current_field=report.get('sensitivity_field_id')
            ev['opponent_field_id']=current_field
            if ev['scenarios']!=scenarios or not current_field: raise ValueError('Incomplete paired-outcome evidence.')
            if name==PROFILES[0]: field_id=current_field; base_id=ev['base_outcome_id']
            elif current_field!=field_id or ev['base_outcome_id']!=base_id:
                raise ValueError('Opponent rosters or underlying outcomes changed; comparison rejected.')
            diag=report.get('field_diagnostic') or {}
            if diag.get('candidate_fallback'): raise ValueError('Independent opponent field unavailable.')
            ordered=ranked_lineups(result.get('lineups') or [])
            found=[identity(lu,p['kind']) for lu in ordered]
            if len(found)!=len(keys) or set(found)!=set(keys): raise ValueError('Candidate identities changed.')
            scored={}
            for i,lu in enumerate(ordered,1):
                m=lu.sim_metrics
                if m.get('sim_scenarios')!=scenarios or any(not math.isfinite(float(m[k])) for k in ('sim_mean','sim_top_one_pct')):
                    raise ValueError('Incomplete or invalid candidate scores.')
                scored[identity(lu,p['kind'])]=dict(rank=i,top1=float(m['sim_top_one_pct']),mean=float(m['sim_mean']))
            paired[name]=scored; proof[name]=ev; diagnostics[name]=diag
        if len(paired)!=len(profiles): break
        for name in profiles: observations[name].append(paired[name])
        evidence.append(proof); fields.append(diagnostics); seeds.append(seed)
    rows=[]
    for i,(key,lu) in enumerate(zip(keys,reference),1):
        label=' | '.join(('CPT: ' if p['kind']=='showdown' and j==0 else '')+str(v.get('Name','')) for j,v in enumerate(roster(lu,p['kind'])))
        for name in profiles:
            obs=[batch[key] for batch in observations[name]]; row=dict(saved_rank=i,lineup=label,profile=name)
            if obs:
                base=mean(b[key]['top1'] for b in observations[PROFILES[0]])
                row.update(mean_top1=mean(v['top1'] for v in obs),change_pp=mean(v['top1'] for v in obs)-base,
                           mean_points=mean(v['mean'] for v in obs),mean_rank=mean(v['rank'] for v in obs),
                           best_rank=min(v['rank'] for v in obs),worst_rank=max(v['rank'] for v in obs),
                           top150_batches=sum(v['rank']<=150 for v in obs),batch_results=obs)
            rows.append(row)
    return dict(comparison_type='projection',sensitivity_version=VERSION,status='completed' if len(seeds)==batches else 'incomplete',
                bank_id=bank['bank_id'],input_id=p['input_id'],kind=p['kind'],saved_model=p['model_version'],current_model=model_version(),
                completed_batches=len(seeds),requested_batches=batches,scenarios=scenarios,opponents=p['field_count'],candidate_count=len(reference),
                profiles=list(profiles),individual_tests=individual,seeds=seeds,targets=targets,evidence=evidence,fields=fields,rows=rows)

def format_projection(r):
    lines=['DFS Projection Sensitivity',f"Status: {r['status']}; matched batches {r['completed_batches']}/{r['requested_batches']}",
           f"Bank ID: {r['bank_id']}",f"Input ID: {r['input_id']}",f"Model: {r['current_model']} | Stress: {r['sensitivity_version']}",
           f"{r['kind'].title()}: {r['candidate_count']} fixed candidates; {r['scenarios']} scenarios/profile/batch; {r['opponents']} requested opponents.",
           'Every batch uses identical opponent rosters and underlying outcomes, verified by fingerprints. Stressed player scores apply equally to candidates and opponents; Captain receives 1.5x the same player outcome.',
           'Lower production: the five most-used QB/RB/WR/TE players in the saved top150 receive 15% lower simulated points, as a workload-shortfall proxy. Touches, game scripts, teammate shares and specialist effects are not recalculated.',
           'Wider outcomes: explicit rookies or players with missing full-season evidence / fewer than four matched games across available current and prior seasons receive independent 0.75x or 1.25x score multipliers with equal probability per scenario. Conditional expected scores are preserved, but finite-sample means can vary. Missing history does not establish rookie status. Counts cover available regular-season rows, not career games. The recent four-week form window is separate. Old banks retain their recorded inputs; reload salaries and build a new bank to capture full-season evidence.',
           'These are explicit hypothetical assumptions, not fitted projection corrections or historical validation. Inputs, ownership, selected outputs and limits remain unchanged. No joint ownership-plus-projection stress is implied.',
           'Ranks use simulated finish-rate order within this bank; exact ties preserve bank order. Repeating settings repeats seeds. Only batches completing every requested profile count. Individual reductions use the same 15% scoring proxy, one favorite at a time; effects are not additive because contest ranks are nonlinear.']
    profiles=r.get('profiles',list(PROFILES))
    lines.append(f'- Profiles per batch: {len(profiles)}; simulations requested: {len(profiles)*r["requested_batches"]}.')
    for name in profiles[1:]:
        targets=r['targets'][name]; lines+=['',f'{name}: {len(targets)} targeted players']
        for v in targets[:30]: lines.append(f"- {v['player']}: {v['reason']}"+(f"; saved contender exposure {v['exposure_pct']:.1f}%" if 'exposure_pct' in v else f"; recent-window games {v['usage_games']}; current-season games {v.get('current_games')}; prior-season games {v.get('prior_games')}"))
        if len(targets)>30: lines.append(f'- {len(targets)-30} additional targets in the saved JSON report.')
        if not targets: lines.append('- No eligible targets: this profile is identical to baseline.')
    if r['completed_batches']:
        leaders=sorted((x for x in r['rows'] if x['profile']==PROFILES[0]),key=lambda x:-x['mean_top1'])[:15]
        lines+=['','Current baseline leaders and projection sensitivity:']
        for b in leaders:
            lines.append(f"- Saved #{b['saved_rank']}: baseline top1 {b['mean_top1']:.2f}%; mean points {b['mean_points']:.2f}. {b['lineup']}")
            for x in (x for x in r['rows'] if x['saved_rank']==b['saved_rank'] and x['profile'] in PROFILES[1:]):
                lines.append(f"  {x['profile']}: top1 {x['mean_top1']:.2f}% ({x['change_pp']:+.2f} pp); mean points {x['mean_points']:.2f}; rank {x['best_rank']}–{x['worst_rank']}; top150 {x['top150_batches']}/{r['completed_batches']}.")
            individual_rows=[x for x in r['rows'] if x['saved_rank']==b['saved_rank'] and x['profile'] not in PROFILES]
            if individual_rows:
                worst=min(individual_rows,key=lambda x:x['mean_top1'])
                lines.append(f"  Largest individual decline: {worst['profile']}: top1 {worst['mean_top1']:.2f}% ({worst['change_pp']:+.2f} pp); rank {worst['best_rank']}–{worst['worst_rank']}." if worst['change_pp']<0 else '  No individual-player test reduced this lineup’s average top1 rate.')
        lines+=['','Individual-player dependencies among baseline top150:']
        leader_ranks={x['saved_rank'] for x in sorted((x for x in r['rows'] if x['profile']==PROFILES[0]),key=lambda x:-x['mean_top1'])[:150]}
        for name in profiles[3:]:
            subset=[x for x in r['rows'] if x['profile']==name and x['saved_rank'] in leader_ranks]
            if subset:
                worst=min(subset,key=lambda x:x['change_pp'])
                lines.append(f"- {name}: average top1 change {mean(x['change_pp'] for x in subset):+.2f} pp across {len(subset)} baseline leaders; largest decline saved #{worst['saved_rank']}: {worst['change_pp']:+.2f} pp.")
        lines+=['','Largest top1 declines across tested profiles:']
        declines=sorted((x for x in r['rows'] if x['profile']!=PROFILES[0] and x['change_pp']<0),key=lambda x:x['change_pp'])[:10]
        for x in declines:lines.append(f"- Saved #{x['saved_rank']}, {x['profile']}: {x['change_pp']:+.2f} pp; {x['lineup']}")
        if not declines:lines.append('- No declines in completed batches.')
    lines.append('Privacy: player names and bank/model IDs included; no account settings or source paths. Full target reasons, per-player mean changes, per-lineup batch scores and paired evidence save locally.')
    return '\n'.join(lines)

def save_projection(path, report):
    atomic_json(path,report); Path(path).with_suffix('.txt').write_text(format_projection(report),encoding='utf-8')
    with Path(path).with_suffix('.csv').open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=list(report['rows'][0])); writer.writeheader(); writer.writerows(report['rows'])
