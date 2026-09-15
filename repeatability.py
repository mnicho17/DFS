"""Frozen-shortlist diagnostics. Never updates application rankings or forecasts."""
import copy
import csv
import datetime
import hashlib
import json
import os
import tempfile
from pathlib import Path
from statistics import mean
from build_snapshots import fingerprint
from lineup_ranking import ranked_lineups, finish_rank
from nfl_simulation import player_key, SimLineup


def model_version():
    import sys
    if getattr(sys, 'frozen', False):
        from candidate_library import code_id
        return code_id()
    root = Path(__file__).parent
    names = ('repeatability.py', 'nfl_simulation.py', 'showdown_simulation.py',
             'showdown_field.py', 'nfl_specialists.py', 'nfl_workload.py',
             'nfl_kickers.py', 'nfl_eligibility.py', 'optimizers.py', 'lineup_ranking.py', 'ownership_strategy.py', 'projection_sensitivity.py', 'usage_history.py')
    return fingerprint({n: hashlib.sha256((root/n).read_text(encoding='utf-8').encode()).hexdigest() for n in names})


def atomic_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(value, f, allow_nan=False)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp): os.remove(temp)


def identity(lu, kind):
    if kind == 'showdown':
        return ('CPT:'+player_key(lu['Captain']),)+tuple(sorted(player_key(p) for p in lu['Flex']))
    return tuple(sorted(player_key(p) for p in lu))


def save_bank(reference, players, *, kind, salary_cap, field_count, field_config=None, input_id='', folder=None):
    """Store the exact independently validated shortlist, prior to selection."""
    if not reference or min(int(getattr(lu, 'sim_metrics', {}).get('sim_scenarios', 0)) for lu in reference) < 1000:
        return {'status':'not saved', 'reason':'No independently validated shortlist'}
    rows=[]
    for lu in ranked_lineups(reference):
        roster = {'Captain':lu['Captain'], 'Flex':lu['Flex']} if kind=='showdown' else list(lu)
        rows.append({'roster':copy.deepcopy(roster), 'metrics':dict(lu.sim_metrics)})
    if len({identity(lu,kind) for lu in reference}) != len(reference):
        raise ValueError('Shortlist contains duplicate identities')
    payload=dict(kind=kind, players=copy.deepcopy(players), rows=rows, salary_cap=salary_cap,
                 field_count=field_count, field_config=copy.deepcopy(field_config or {}),
                 input_id=input_id, model_version=model_version())
    bank=dict(schema=1, bank_id=fingerprint(payload), payload=payload,
              created_at=datetime.datetime.now().astimezone().isoformat())
    if folder is None:
        from build_diagnostics import build_history_path
        folder=Path(build_history_path()).parent/'ranking-banks'
    path=Path(folder)/(bank['bank_id']+'.dfsbank')
    atomic_json(path,bank)
    return dict(status='saved', bank_id=bank['bank_id'], candidates=len(rows))


def capture_bank(*args, **kwargs):
    # A disk failure must not discard the user's completed build.
    try: return save_bank(*args, **kwargs)
    except Exception as exc: return dict(status='not saved', reason=str(exc))


def load_bank(path):
    if Path(path).stat().st_size > 100*1024*1024: raise ValueError('Bank exceeds 100 MB')
    bank=json.loads(Path(path).read_text(encoding='utf-8'))
    if bank.get('schema')!=1 or bank.get('bank_id')!=fingerprint(bank.get('payload')):
        raise ValueError('Invalid or modified ranking bank')
    p=bank['payload']
    if p['kind'] not in ('classic','showdown') or not 1<=len(p['rows'])<=5000:
        raise ValueError('Unsupported ranking bank')
    if p['model_version']!=model_version():
        raise ValueError('Simulation code changed. Run Deep again to save a bank for this version.')
    return bank


def candidates(payload):
    if payload['kind']=='classic':
        return [SimLineup(copy.deepcopy(r['roster']),metrics=r['metrics']) for r in payload['rows']]
    from optimizers import ShowdownLineup
    result=[]
    for r in payload['rows']:
        roster=copy.deepcopy(r['roster']);lu=ShowdownLineup(roster['Captain'],roster['Flex'])
        lu.sim_metrics=dict(r['metrics']);result.append(lu)
    return result


def run_repeatability(path, *, batches=5, scenarios=5000, cancelled=lambda:False, progress=lambda s:None, simulate=None):
    if not 2<=batches<=10 or not 1000<=scenarios<=10000:
        raise ValueError('Use 2–10 batches and 1,000–10,000 scenarios per batch')
    bank=load_bank(path);p=bank['payload'];reference=candidates(p)
    keys=[identity(lu,p['kind']) for lu in reference]
    if len(set(keys))!=len(keys): raise ValueError('Duplicate candidates in bank')
    observations={k:[] for k in keys};completed=[];ties=[]
    for batch in range(batches):
        if cancelled(): break
        seed=730001+batch*100003
        progress(f'Batch {batch+1}/{batches}: {scenarios:,} fresh scenarios')
        kwargs=dict(scenarios=scenarios,field_lineup_count=p['field_count'],salary_cap=p['salary_cap'],
                    seed=seed,cancel_callback=cancelled,
                    progress_callback=lambda a,b,c:progress(f'Batch {batch+1}/{batches}: {a:,}/{b:,} — {c}'))
        if simulate is not None:
            result=simulate(candidates(p),copy.deepcopy(p['players']),**kwargs)
        elif p['kind']=='classic':
            from nfl_simulation import simulate_nfl_contest
            result=simulate_nfl_contest(candidates(p),copy.deepcopy(p['players']),field_config=p['field_config'],**kwargs)
        else:
            from showdown_simulation import simulate_showdown
            result=simulate_showdown(candidates(p),copy.deepcopy(p['players']),**kwargs)
        ordered=ranked_lineups(result.get('lineups') or [])
        result_keys=[identity(lu,p['kind']) for lu in ordered]
        if cancelled() or int(result.get('report',{}).get('scenarios',0))!=scenarios: break
        if len(result_keys)!=len(keys) or set(result_keys)!=set(keys):
            raise ValueError('Batch candidate identities changed; comparison stopped')
        if any(int(lu.sim_metrics.get('sim_scenarios',0))!=scenarios for lu in ordered):
            raise ValueError('Incomplete candidate scores; comparison stopped')
        ties.append({str(k):len(ordered)>k and finish_rank(ordered[k-1])==finish_rank(ordered[k]) for k in (50,150)})
        for rank,lu in enumerate(ordered,1):
            observations[identity(lu,p['kind'])].append((rank,float(lu.sim_metrics['sim_top_one_pct'])))
        completed.append(seed)
    rows=[]
    for original,(key,lu) in enumerate(zip(keys,reference),1):
        obs=observations[key]
        roster=[lu['Captain']]+lu['Flex'] if p['kind']=='showdown' else lu
        row=dict(original_rank=original, original_top1=float(lu.sim_metrics.get('sim_top_one_pct',0)),
                 reference_scenarios=int(lu.sim_metrics.get('sim_scenarios',0)),
                 lineup=' | '.join(('CPT: ' if p['kind']=='showdown' and i==0 else '')+str(v.get('Name',''))+' ['+str(v.get('Team',''))+' '+str(v.get('Position',''))+']' for i,v in enumerate(roster)))
        if obs:
            ranks=[r for r,_ in obs];rates=[v for _,v in obs]
            row.update(mean_top1=mean(rates),min_top1=min(rates),max_top1=max(rates),
                       batch_ranks=ranks,batch_top1=rates,
                       best_rank=min(ranks),worst_rank=max(ranks),mean_rank=mean(ranks),
                       top50_batches=sum(r<=50 for r in ranks),top150_batches=sum(r<=150 for r in ranks))
        rows.append(row)
    return dict(bank_id=bank['bank_id'],input_id=p['input_id'],kind=p['kind'],model_version=p['model_version'],
                status='completed' if len(completed)==batches else 'incomplete',requested_batches=batches,
                completed_batches=len(completed),scenarios_per_batch=scenarios,opponents=p['field_count'],
                seeds=completed,boundary_ties=ties,rows=rows)


def format_report(report):
    n=report['completed_batches']
    lines=['DFS Ranking Repeatability',f"Status: {report['status']}; completed batches: {n}/{report['requested_batches']}",
           f"Bank ID: {report['bank_id']}",f"Input ID: {report['input_id']}",
           f"{report['kind'].title()}: {len(report['rows']):,} identical candidates; {report['scenarios_per_batch']:,} scenarios and {report['opponents']:,} opponents per batch.",
           'Fresh scenarios and opponent fields per batch. Repeating this check uses the same recorded seeds.',
           'Diagnostic only: original output order and forecasts are unchanged. This measures sampling sensitivity, not historical accuracy.',
           'Rank ties use the saved candidate order. Top-50/150 groups are capped at the bank size. Partial batches are excluded.']
    if n:
        lines+=['','Leaders by average top-1% across completed batches (up to 20):']
        for r in sorted(report['rows'],key=lambda r:(-r['mean_top1'],r['original_rank']))[:20]:
            lines.append(f"Original #{r['original_rank']}: mean top-1% {r['mean_top1']:.2f}% (range {r['min_top1']:.2f}–{r['max_top1']:.2f}%); ranks {r['best_rank']}–{r['worst_rank']}; top50 {r['top50_batches']}/{n}; top150 {r['top150_batches']}/{n}. {r['lineup']}")
        lines+=['','Original top-50 leaders with the largest worst rank (up to 10):']
        for r in sorted(report['rows'][:50],key=lambda r:-r['worst_rank'])[:10]:
            lines.append(f"Original #{r['original_rank']}: ranks {r['best_rank']}–{r['worst_rank']}; top50 {r['top50_batches']}/{n}. {r['lineup']}")
        lines.append('Boundary ties by batch: '+json.dumps(report['boundary_ties']))
    lines.append('Privacy: includes player names and saved input IDs; excludes account settings and file paths.')
    return '\n'.join(lines)


def save_report(path, report):
    atomic_json(path,report)
    Path(path).with_suffix('.txt').write_text(format_report(report),encoding='utf-8')
    with Path(path).with_suffix('.csv').open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=list(report['rows'][0]));writer.writeheader();writer.writerows(report['rows'])
