"""Offline construction and QB sensitivity experiment; never changes app settings."""
import argparse
import copy
import hashlib
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_snapshots import load_snapshot, fingerprint
from data_io import read_players_csv
from lineup_ranking import ranked_lineups
from nfl_simulation import player_key
from optimizers import ShowdownOptimizer, ShowdownLineup
from showdown_simulation import active_showdown_players, showdown_signature, simulate_showdown, filter_salary_candidates
from repeatability import model_version


class QBStress:
    """Separate RNG leaves baseline outcomes and opponent identities paired."""
    def __init__(self, players, profile, seed):
        self.keys = sorted(player_key(p) for p in players if p.get('Position') == 'QB')
        self.profile = profile
        self.rng = random.Random(seed + 700001)
        self.digest = hashlib.sha256()
        self.sums = {k: [0.0, 0.0] for k in self.keys}
        self.count = 0

    def __call__(self, outcomes):
        self.digest.update(json.dumps(outcomes, sort_keys=True, separators=(',', ':')).encode())
        result = dict(outcomes)
        for key in self.keys:
            multiplier = .85 if self.profile == 'QB mean -15%' else (
                self.rng.choice((.65, 1.35)) if self.profile == 'QB wider outcomes' else 1.0)
            result[key] *= multiplier
            self.sums[key][0] += outcomes[key]
            self.sums[key][1] += result[key]
        self.count += 1
        return result


def summary(rows):
    n = len(rows)
    qbs = Counter(sum(p.get('Position') == 'QB' for p in [lu['Captain']] + lu['Flex']) for lu in rows)
    captains = Counter(lu['Captain']['Name'] for lu in rows)
    no_qb = sum(lu['Captain'].get('Position') in ('WR', 'TE') and not any(
        p.get('Position') == 'QB' and p.get('Team') == lu['Captain'].get('Team') for p in lu['Flex']) for lu in rows)
    return dict(count=n, qb_counts=dict(qbs), two_qb_pct=100*qbs[2]/max(1,n),
        receiver_captain_without_own_qb=no_qb, captains=captains.most_common(5),
        mean_top1=sum(getattr(lu,'sim_metrics',{}).get('sim_top_one_pct',0) for lu in rows)/max(1,n))


def generate_banks(players, recipe, count, seed, seconds):
    """Equal search time allowances and candidate caps; all generated under frozen inputs."""
    originals = {player_key(p): p for p in players}
    cap = recipe.get('salary_cap',50000)
    def search(style, excluded_qbs, target, job_seed, deadline):
        copied = copy.deepcopy(players)
        for p in copied:
            if player_key(p) in excluded_qbs:
                if p.get('LockCpt') or p.get('LockFlex'):
                    return []
                p['FadeCpt'] = p['FadeFlex'] = True
        rows = ShowdownOptimizer(copied,salary_cap=cap,seed=job_seed,build_style=style,
            own_mode=recipe.get('ownership_mode','Balanced'),own_weight=recipe.get('ownership_weight',.15)).build_lineups(
                num_lineups=target,cancel_callback=lambda:time.monotonic()>=deadline)
        rows = [ShowdownLineup(originals[player_key(lu['Captain'])],
            [originals[player_key(p)] for p in lu['Flex']]) for lu in rows]
        return filter_salary_candidates(rows,cap,recipe.get('salary_strategy','Near Cap'))
    styles = ['Strategic','Balanced','Contrarian','Chalk','Randomized']
    # Same per-job deadline and total allowance for the two policies.
    policies = {'All styles':[(s,set()) for s in styles]}
    qbs = {player_key(p) for p in players if p.get('Position') == 'QB'}
    policies['Broader construction'] = [('Balanced',qbs), *[('Balanced',qbs-{key}) for key in sorted(qbs)],
        ('Strategic',set()),('Randomized',set())]
    banks = {}
    generation = {}
    for label,jobs in policies.items():
        start=time.monotonic();bank={};details=[]
        for i,(style,excluded) in enumerate(jobs):
            target=(count-len(bank)+len(jobs)-i-1)//(len(jobs)-i)
            deadline=start+seconds*(i+1)/len(jobs)
            rows=search(style,excluded,target,seed+i,deadline)
            for lu in rows:bank.setdefault(showdown_signature(lu),lu)
            details.append(dict(style=style,excluded_qbs=sorted(excluded),returned=len(rows)))
        banks[label]=list(bank.values())[:count]
        generation[label]=dict(seconds=time.monotonic()-start,jobs=details,**summary(banks[label]))
    # Equalize counts without selecting by SIM or real results. Random truncation
    # avoids systematically dropping the final construction job.
    common=min(map(len,banks.values()))
    if common < 150: raise ValueError('Fewer than 150 legal candidates per policy; increase search allowance.')
    for label in banks:
        random.Random(seed+500).shuffle(banks[label]);banks[label]=banks[label][:common]
    return banks,generation


def run(snapshot, salary, output, *, count=400, scenarios=1000, field=800, batches=2, seconds=45):
    snap=load_snapshot(snapshot);inputs=snap['inputs'];recipe=inputs['recipe']
    if recipe.get('sport')!='NFL' or recipe.get('contest_kind')!='showdown':
        raise ValueError('NFL Showdown snapshot required')
    if (inputs.get('rules') or {}).get('groups') or (inputs.get('rules') or {}).get('player_constraints'):
        raise ValueError('This diagnostic does not support additional portfolio group/player constraints')
    players=active_showdown_players(inputs['players'])
    raw=read_players_csv(str(salary));by_id={str(p.get('FlexID')):p for p in raw}
    for p in players:
        source=by_id.get(str(p.get('FlexID')))
        if source is None or any(source.get(k)!=p.get(k) for k in ('Name','Team','FlexSalary','CptSalary')):
            raise ValueError('Salary/snapshot identity or salary mismatch: '+str(p.get('Name')))
    banks,generation=generate_banks(players,recipe,count,5100,seconds)
    union={showdown_signature(lu):lu for rows in banks.values() for lu in rows}
    report=dict(status='partial', requested_batches=batches, completed_batches=0,
        input_id=snap['input_id'],model_version=model_version(),salary_sha256=hashlib.sha256(Path(salary).read_bytes()).hexdigest(),
        salary_players_verified=len(players),generation=generation,candidates_per_policy=len(next(iter(banks.values()))),
        candidate_ids={label:fingerprint(sorted(showdown_signature(lu) for lu in rows)) for label,rows in banks.items()},
        caveats=['Diagnostic sensitivity, not historical predictive validation.',
            'Independent top 150 ranking, not a feasible portfolio or submitted entries.',
            'Policies have equal time allowances and final counts, but different candidate identities.',
            'QB stresses apply equally to user candidates and opponents; wider multipliers have expectation 1, not exact sample mean 1.',
            'Post-processing stresses do not recompute specialist events or create a new coherent play-level model.',
            'No actual results are used to generate, score or select candidates.'],batches=[])
    destination=Path(output);destination.parent.mkdir(parents=True,exist_ok=True)
    frozen = dict(snapshot=snap, model_version=report['model_version'],
        policies={label:[dict(lu) for lu in rows] for label,rows in banks.items()})
    destination.with_suffix('.bank.json').write_text(json.dumps(frozen),encoding='utf-8')
    report['evaluated_banks']={label:summary(rows) for label,rows in banks.items()}
    for batch in range(batches):
        seed=90210+batch*100003;checks=[]
        for profile in ['Baseline','QB mean -15%','QB wider outcomes']:
            print(f'{Path(snapshot).name[:12]} batch {batch+1}/{batches}: {profile}',flush=True)
            transform=QBStress(players,profile,seed)
            sim=simulate_showdown(list(union.values()),players,scenarios=scenarios,field_lineup_count=field,
                salary_cap=recipe.get('salary_cap',50000),seed=seed,outcome_transform=transform)
            if sim['report']['scenarios']!=scenarios:raise ValueError('Incomplete simulation')
            ordered=ranked_lineups(sim['lineups']);policies={}
            for label,rows in banks.items():
                keys={showdown_signature(lu) for lu in rows}
                selected=[lu for lu in ordered if showdown_signature(lu) in keys][:150]
                policies[label]=summary(selected)
            checks.append((transform.digest.hexdigest(),sim['report']['sensitivity_field_id']))
            base_keys={showdown_signature(lu) for lu in banks['All styles']}
            broad_keys={showdown_signature(lu) for lu in banks['Broader construction']}
            combined=ordered[:150]
            report['batches'].append(dict(seed=seed,profile=profile,scenarios=scenarios,field=sim['report']['field_lineups'],
                baseline_outcomes_id=checks[-1][0],opponent_field_id=checks[-1][1],policies=policies,
                combined_bank_top150=dict(summary(combined),
                    broader_only=sum(showdown_signature(lu) in broad_keys-base_keys for lu in combined),
                    note='Larger combined bank; not an equal-budget comparison'),
                qb_mean_scores={k:[v/scenarios for v in values] for k,values in transform.sums.items()}))
        if len(set(checks))!=1:raise ValueError('Shared outcomes or opponent identities changed between profiles')
        report['paired_inputs_verified']=True
        report['completed_batches']=batch+1
        report['status']='completed' if batch+1==batches else 'partial'
        destination.write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot');parser.add_argument('salary');parser.add_argument('--output',required=True)
    parser.add_argument('--candidates',type=int,default=400);parser.add_argument('--scenarios',type=int,default=1000)
    parser.add_argument('--field',type=int,default=800);parser.add_argument('--batches',type=int,default=2)
    parser.add_argument('--generation-seconds',type=int,default=45)
    args=parser.parse_args()
    if not (150<=args.candidates<=1200 and 250<=args.scenarios<=5000 and 100<=args.field<=4000 and 1<=args.batches<=5 and 5<=args.generation_seconds<=300):
        parser.error('Arguments outside bounded diagnostic limits')
    run(args.snapshot,args.salary,args.output,count=args.candidates,scenarios=args.scenarios,field=args.field,
        batches=args.batches,seconds=args.generation_seconds)
