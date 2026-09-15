"""Experimental salary-band prior for legal Showdown opponent entries."""
import random
from collections import Counter
from optimizers import ShowdownLineup, _salary, _cpt_salary, _showdown_cpt_own, _showdown_flex_own

MODEL = 'showdown-salary-bands-v2'
# Unspent fractions of the salary cap; broad heuristic, not fitted to winners.
BANDS = ((.01, .60), (.03, .25), (.06, .10), (.15, .05))


class OpponentField(list):
    pass


def sample_field(pool, count, *, salary_cap=50000, seed=0, cancel_callback=None):
    from ownership_strategy import fit_field
    original = _sample_field(pool,count,salary_cap=salary_cap,seed=seed,cancel_callback=cancel_callback)
    return fit_field(original,pool,
        lambda adjusted,attempt:_sample_field(adjusted,count,salary_cap=salary_cap,seed=seed+attempt*104729,cancel_callback=cancel_callback),
        showdown=True,cancelled=cancel_callback or (lambda:False))


def _sample_field(pool, count, *, salary_cap=50000, seed=0, cancel_callback=None):
    count = max(0, int(count)); rng = random.Random(seed)
    field = OpponentField()
    field.diagnostic = dict(model=MODEL, requested=count, attempts=0, fallback_entries=0,
                            salary_band_targets={}, salary_band_counts={})
    if count == 0 or len(pool) < 6:
        return field
    def weights(fn):
        values = [max(0, fn(p)) for p in pool]
        return [max(.05, x) for x in values] if any(values) else [max(.1, float(p.get('FlexProjection') or 0))**1.3 for p in pool]
    cweights = [w*float(p.get('_FieldCptWeight',1)) for w,p in zip(weights(_showdown_cpt_own),pool)]
    fweights = [w*float(p.get('_FieldFlexWeight',1)) for w,p in zip(weights(_showdown_flex_own),pool)]
    captains = [i for i,p in enumerate(pool) if _cpt_salary(p)>0]
    if not captains:
        return field
    cw = [cweights[i] for i in captains]
    salaries = [_salary(p) for p in pool]; cals = [_cpt_salary(p) for p in pool]
    teams = [p.get('Team') for p in pool]
    targets = [int(count*w) for _,w in BANDS]
    for i in sorted(range(4), key=lambda i: (-(count*BANDS[i][1]-targets[i]), i))[:count-sum(targets)]:
        targets[i] += 1
    accepted = [0]*4; overflow = []
    labels = [f'{int((BANDS[i-1][0] if i else 0)*100)}–{int(edge*100)}% unused' for i,(edge,_) in enumerate(BANDS)]
    field.diagnostic['salary_band_targets'] = dict(zip(labels, targets))
    for attempt in range(max(200, count*40)):
        if len(field)>=count or (cancel_callback and cancel_callback()):
            break
        field.diagnostic['attempts'] = attempt+1
        c = rng.choices(captains, weights=cw, k=1)[0]
        available = list(range(len(pool))); available.remove(c); flex = []
        band_target = rng.choices(range(4), weights=[max(0, target-done) for target,done in zip(targets,accepted)], k=1)[0]
        for _ in range(4):
            j = rng.choices(available, weights=[fweights[i] for i in available], k=1)[0]
            flex.append(j); available.remove(j)
        subtotal = cals[c]+sum(salaries[i] for i in flex)
        def legal_band(j):
            unused = (salary_cap-subtotal-salaries[j])/salary_cap
            if unused < 0 or unused > .15 or len({teams[i] for i in [c]+flex+[j]}) != 2:
                return None
            return next(i for i,(edge,_) in enumerate(BANDS) if unused <= edge)
        finishers = [j for j in available if legal_band(j) == band_target]
        if finishers:
            flex.append(rng.choices(finishers,weights=[fweights[j] for j in finishers],k=1)[0])
        else:
            # Preserve a legal shortage proposal, but do not pretend it met the target band.
            flex.append(rng.choices(available,weights=[fweights[j] for j in available],k=1)[0])
        salary = cals[c]+sum(salaries[i] for i in flex)
        unused = (salary_cap-salary)/salary_cap
        if unused < 0 or unused > .15 or len({teams[i] for i in [c]+flex}) != 2:
            continue
        band = next(i for i,(edge,_) in enumerate(BANDS) if unused <= edge)
        entry = ShowdownLineup(pool[c], [pool[i] for i in flex])
        if accepted[band] < targets[band]:
            field.append(entry); accepted[band] += 1
        elif len(overflow)<count*4:
            overflow.append(entry)
    if len(field)<count and not (cancel_callback and cancel_callback()):
        # Shortage fallback uses actual legal proposals, never copies to fill a quota.
        extra = rng.sample(overflow, min(count-len(field), len(overflow)))
        field.extend(extra); field.diagnostic['fallback_entries'] = len(extra)
    rng.shuffle(field)
    counts = Counter()
    for lu in field:
        unused=(salary_cap-_cpt_salary(lu['Captain'])-sum(_salary(p) for p in lu['Flex']))/salary_cap
        counts[labels[next(i for i,(edge,_) in enumerate(BANDS) if unused<=edge)]] += 1
    field.diagnostic['salary_band_counts'] = dict(counts)
    return field
