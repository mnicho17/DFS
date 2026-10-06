"""Opt-in construction sampler. Never used by the default SIM/build path."""
from collections import Counter
import math
import random
from optimizers import ShowdownLineup, _pkey, _salary, _cpt_salary, _showdown_cpt_own, _showdown_flex_own
from showdown_field import OpponentField

MODEL = 'historical-construction-experiment-v1'
CATEGORIES = ('Team split', 'Captain position', 'Quarterbacks', 'Kicker/defense slots', 'Salary left')


def labels(lineup, cap=50000):
    players = [lineup['Captain']] + list(lineup['Flex'])
    teams = Counter(p['Team'] for p in players)
    unused = cap-_cpt_salary(players[0])-sum(_salary(p) for p in players[1:])
    return {'Team split':'–'.join(map(str, sorted(teams.values(), reverse=True))),
            'Captain position':players[0]['Position'],
            'Quarterbacks':str(sum(p['Position']=='QB' for p in players)),
            'Kicker/defense slots':str(sum(p['Position'] in ('K','DST') for p in players)),
            'Salary left':'over cap' if unused<0 else '$0–200' if unused<=200 else
                '$201–700' if unused<=700 else '$701–1,200' if unused<=1200 else 'over $1,200'}


def distributions(entries, cap=50000):
    counts = {category:Counter() for category in CATEGORIES}
    for entry in entries:
        for category, label in labels(entry, cap).items():
            counts[category][label] += 1
    return {category:{label:n/len(entries) for label,n in values.items()} if entries else {}
            for category,values in counts.items()}


def sample_history_field(pool, count, priors, *, salary_cap=50000, seed=0, cancelled=lambda:False):
    """Fit marginal construction weights over bounded, freshly legal proposals.

    Opponent entries may repeat (duplication exists in real fields). Sampling never
    modifies input players or copies historical athlete IDs onto the current slate.
    Infeasible/undersampled targets are disclosed; no exact quota is promised.
    """
    if not math.isfinite(salary_cap) or salary_cap<=0 or not 0<=count<=10000:
        raise ValueError('Use a positive finite salary cap and 0–10000 opponents.')
    field = OpponentField()
    field.diagnostic = dict(model=MODEL, requested=count, proposals=0, attempts=0,
                            unavailable_targets={}, construction_targets=priors)
    if not count or len(pool)<6:
        return field
    if len({_pkey(p) for p in pool})!=len(pool):
        raise ValueError('The supplied slate has duplicate athlete identities.')
    if len({p.get('Team') for p in pool})!=2 or any(not p.get('Team') or p.get('Position') not in ('QB','RB','WR','TE','K','DST') for p in pool):
        raise ValueError('An identified two-team NFL Showdown salary pool is required.')
    if any(not math.isfinite(_salary(p)) or _salary(p)<0 or not math.isfinite(_cpt_salary(p)) or _cpt_salary(p)<0 for p in pool):
        raise ValueError('All supplied salary identities must have known finite salaries.')
    targets = {}
    for category in CATEGORIES:
        values = priors.get(category, {})
        if not values or any(not math.isfinite(v) or v<0 for v in values.values()) or sum(values.values())<=0:
            raise ValueError('Missing or invalid historical construction targets: '+category)
        total = sum(values.values())
        targets[category] = {key:value/total for key,value in values.items()}
    rng = random.Random(seed)
    captains = [i for i,p in enumerate(pool) if _cpt_salary(p)>0]
    if not captains:
        return field
    def weights(fn):
        values = [max(0, fn(p)) for p in pool]
        return [max(.05, x) for x in values] if any(values) else [max(.1, float(p.get('FlexProjection') or 0))**1.3 for p in pool]
    cw,fw = weights(_showdown_cpt_own),weights(_showdown_flex_own)
    proposals,features = [],[]
    for attempt in range(max(200, count*100)):
        if attempt%100==0 and cancelled():
            return field
        field.diagnostic['attempts'] = attempt+1
        c = rng.choices(captains, weights=[cw[i] for i in captains])[0]
        available = [i for i in range(len(pool)) if i!=c]
        flex = []
        for _ in range(5):
            i = rng.choices(available, weights=[fw[j] for j in available])[0]
            available.remove(i);flex.append(pool[i])
        if _cpt_salary(pool[c])+sum(_salary(p) for p in flex)>salary_cap or len({p['Team'] for p in [pool[c]]+flex})!=2:
            continue
        entry = ShowdownLineup(pool[c], flex)
        proposals.append(entry);features.append(labels(entry, salary_cap))
        if len(proposals)>=max(500, count*20):
            break
    field.diagnostic['proposals'] = len(proposals)
    if not proposals or cancelled():
        return field
    fitted = [1.0]*len(proposals)
    # A fixed number of tempered fitting passes bounds runtime and avoids hard
    # zeroing a legal construction merely because it was absent from history.
    for _ in range(12):
        if cancelled():
            return field
        for category in CATEGORIES:
            actual = Counter()
            for row,weight in zip(features,fitted):actual[row[category]]+=weight
            total = sum(actual.values())
            for i,row in enumerate(features):
                desired = targets[category].get(row[category], 0)
                ratio = (desired+.005)/(actual[row[category]]/total+.005)
                fitted[i] *= max(.5,min(2,ratio**.5))
            scale = sum(fitted)/len(fitted)
            fitted = [weight/scale for weight in fitted]
    for category in CATEGORIES:
        available = {row[category] for row in features}
        missing = {label:weight for label,weight in targets[category].items() if weight and label not in available}
        if missing:field.diagnostic['unavailable_targets'][category]=missing
    if cancelled():return field
    field.extend(rng.choices(proposals, weights=fitted, k=count))
    field.diagnostic['construction_actual'] = distributions(field,salary_cap)
    return field
