"""Bounded roster-slot ownership estimates; explicitly heuristic, not field observations."""
from optimizers import _pkey

def allocate(weights, total, capacities=None):
    caps = capacities or [100.0] * len(weights)
    result = [0.0] * len(weights)
    remaining = min(float(total), sum(caps))
    active = set(range(len(weights)))
    while active and remaining > 1e-9:
        denom = sum(max(1e-9, weights[i]) for i in active)
        clipped = [i for i in active if remaining * max(1e-9, weights[i]) / denom >= caps[i] - result[i]]
        if not clipped:
            for i in active:
                result[i] += remaining * max(1e-9, weights[i]) / denom
            break
        for i in clipped:
            remaining -= caps[i] - result[i]
            result[i] = caps[i]
            active.remove(i)
    return result

def quick_ownership(players, *, mode, sport):
    from nfl_eligibility import apply_qb_eligibility, unavailable
    if sport == 'NFL':
        apply_qb_eligibility(players)
    pool = [p for p in players if float(p.get('FlexSalary') or 0) > 0
            and not unavailable(p) and p.get('NFLQBEligible') is not False]
    keys = [_pkey(p) for p in pool]
    weights = [max(1e-6, float(p.get('FlexProjection') or 0) *
                   (1 + 350 / float(p['FlexSalary']))) for p in pool]
    if mode == 'showdown':
        cpt = allocate([w ** 1.35 for w in weights], 100)
        flex = allocate([w ** .85 for w in weights], 500, [100-v for v in cpt])
        return dict(total=dict(zip(keys, [a+b for a,b in zip(cpt,flex)])),
                    cpt=dict(zip(keys,cpt)), flex=dict(zip(keys,flex)))
    values = [0.0] * len(pool)
    if sport == 'NFL':
        for pos, total in {'QB':100, 'RB':250, 'WR':350, 'TE':100, 'DST':100}.items():
            indices = [i for i,p in enumerate(pool) if str(p.get('Position')).upper().replace('D/ST','DST') == pos]
            for i,value in zip(indices, allocate([weights[i] for i in indices], total)):
                values[i] = value
    else:
        values = allocate(weights, {'MLB':10, 'NBA':8, 'WNBA':6}.get(sport,6)*100)
    total = dict(zip(keys,values))
    return dict(total=total,cpt={},flex=total)
