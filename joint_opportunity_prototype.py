"""Diagnostic finite opportunity allocation. No normal build invokes this model.

Latent carries/targets are shared across all recorded workload participants and
an unused reserve. Only qualified rotation scores are transformed. QB/starter
scores are still the original simulator draws, so this is not a complete joint
football scoring model. Counts do not model yards/TDs/bonuses/fumbles as events.
"""
import bisect
import hashlib
import math
import random
from collections import Counter,defaultdict
from rotation_zero_prototype import _number,eligible

VERSION='joint-opportunity-prototype-v1'


def no_success(budget,expected):
    budget=_number(budget);expected=_number(expected)
    if expected>budget:raise ValueError('Expected opportunities exceed budget')
    if not budget:return 1.
    n=math.floor(budget);fraction=budget-n;miss=1-expected/budget
    return miss**n*((1-fraction)+fraction*miss)


def allocate(players,sampled_workloads,rng):
    """Randomized integer budgets and multinomial participants plus reserve.

    Every target is assigned once and can yield at most one catch. Every carry
    is assigned once. Expected counts match the shared sampled workload exactly.
    """
    from nfl_simulation import player_key
    keys=[player_key(p) for p in players]
    if len(keys)!=len(set(keys)):raise ValueError('Ambiguous prototype player identities')
    groups=defaultdict(list);work={};counts={};budgets=[]
    for p in sorted(players,key=player_key):
        w=sampled_workloads.get(id(p))
        if w is None:
            if eligible(p):raise ValueError('Eligible player missing shared sampled workload')
            continue
        if w.get('version')!='workload-v2':
            if eligible(p):raise ValueError('Unsupported workload version')
            continue
        team=str(p.get('Team') or '').strip().upper();pos=p.get('Position')
        if not team or w.get('team')!=team or w.get('position')!=pos:
            raise ValueError('Workload participant identity mismatch')
        catch=_number(w['efficiency'][1])
        if catch>1:raise ValueError('Invalid catch rate')
        key=player_key(p);work[key]=w;counts[key]=dict(carries=0,targets=0,catches=0)
        groups[(team,pos)].append(key)
    for (team,pos),keys in sorted(groups.items()):
        for metric in ('carries','targets'):
            recorded=[_number(work[k]['budgets'][metric]) for k in keys]
            budget=recorded[0]
            if any(v!=budget for v in recorded):raise ValueError('Conflicting shared budgets')
            base=[_number(work[k][metric]) for k in keys]
            if sum(base)>budget:raise ValueError('Participant opportunities exceed shared budget')
            if budget>10000:raise ValueError('Diagnostic opportunity budget exceeds allowance')
            total=math.floor(budget)+int(rng.random()<budget-math.floor(budget))
            cumulative=[];running=0.
            for value in base:
                running+=value/budget if budget else 0.;cumulative.append(running)
            reserve=0
            for _ in range(total):
                selected=bisect.bisect_right(cumulative,rng.random())
                if selected==len(keys):reserve+=1;continue
                key=keys[selected];counts[key][metric]+=1
                if metric=='targets' and rng.random()<_number(work[key]['efficiency'][1]):
                    counts[key]['catches']+=1
            budgets.append(dict(team=team,position=pos,metric=metric,total=total,reserve=reserve,
                assigned=sum(counts[k][metric] for k in keys),expected_budget=budget))
    return counts,work,budgets


class JointOpportunityPrototype:
    def __init__(self,*,scoring='counts'):
        if scoring not in {'counts','occupancy'}:raise ValueError('Unknown joint scoring mode')
        self.scoring=scoring;self.counts=Counter();self.last_allocation=None

    def apply(self,players,sampled_workloads,outcomes,rng):
        from nfl_simulation import player_key
        stream=random.Random(hashlib.sha256((VERSION+repr(rng.getstate())).encode()).digest())
        counts,work,budgets=allocate(players,sampled_workloads,stream)
        result=dict(outcomes)
        for p in sorted(players,key=player_key):
            if not eligible(p):continue
            key=player_key(p);w=work[key];draw=counts[key];score=_number(outcomes[key])
            carries=_number(w['carries']);targets=_number(w['targets']);catch=_number(w['efficiency'][1])
            if self.scoring=='occupancy':
                q=no_success(w['budgets']['carries'],carries)*no_success(w['budgets']['targets'],targets*catch)
                if q>=1:
                    if score:raise ValueError('Positive score without expected successful opportunities')
                    result[key]=0.
                else:result[key]=score/(1-q) if draw['carries']+draw['catches'] else 0.
            else:
                rush=_number(w['efficiency'][0])*.1+_number(w['rates']['rushing_td_per_carry'])*6
                receive=1+_number(w['efficiency'][2])*.1+_number(w['rates']['receiving_td_per_catch'])*6
                expected=carries*rush+targets*catch*receive
                realized=draw['carries']*rush+draw['catches']*receive
                if not expected:
                    if score:raise ValueError('Positive score without expected successful opportunities')
                    result[key]=0.
                else:result[key]=score*realized/expected
            if not math.isfinite(result[key]):raise ValueError('Nonfinite prototype score')
            self.counts['eligible_draws']+=1;self.counts['zero_gates']+=int(result[key]==0)
        self.last_allocation=dict(players=counts,budgets=budgets)
        return result
