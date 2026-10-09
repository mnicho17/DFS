"""Automatic NFL Showdown ownership from the detailed field's spending prior."""
import copy
import math
from collections import Counter

from optimizers import _pkey
from showdown_field import _sample_field, MODEL
from showdown_simulation import active_showdown_players, validate_showdown_lineup


def estimate_ownership(players, count, *, salary_cap=50000, seed=73129,
                       cancelled=lambda: False, progress=lambda done, total, text: None):
    """Estimate once from projections; never fit to previous ownership forecasts."""
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 100000:
        raise ValueError('Use 1–100000 ownership samples.')
    if not math.isfinite(salary_cap) or salary_cap <= 0:
        raise ValueError('Ownership estimation requires a positive finite salary cap.')
    progress(0, count, 'Sampling legal Showdown opponents with salary bands')
    if cancelled():
        return {}
    pool = active_showdown_players([dict(p, LockCpt=False, LockFlex=False) for p in copy.deepcopy(players)
                                    if not p.get('FadeCpt') and not p.get('FadeFlex')])
    for player in pool:
        for prefix, key in [('Cpt', 'ProjCptOwnPct'), ('Flex', 'ProjFlexOwnPct')]:
            projection = float(player.get(prefix+'Projection') or 0)
            salary = float(player.get(prefix+'Salary') or 0)
            if not math.isfinite(projection) or not math.isfinite(salary) or salary <= 0:
                raise ValueError('Ownership estimation requires finite forecasts and positive slot salaries.')
            score = projection + .35 * projection / (salary / 1000)
            player[key] = math.exp(max(-100, min(100, score / 6)))
        # Relative transient draw weights; not forecast percentages or saved evidence.
        player.pop('OwnershipUnits', None)
        player.pop('_FieldCptWeight', None)
        player.pop('_FieldFlexWeight', None)
    field = _sample_field(pool, count, salary_cap=salary_cap, seed=seed, cancel_callback=cancelled)
    if cancelled():
        return {}
    if len(field) != count:
        raise ValueError(f'Ownership sampling returned {len(field)}/{count} legal entries; no partial estimate applied.')
    captain, flex = Counter(), Counter()
    for lineup in field:
        if cancelled():
            return {}
        validate_showdown_lineup(lineup, pool, salary_cap)
        captain[_pkey(lineup['Captain'])] += 1
        flex.update(_pkey(p) for p in lineup['Flex'])
    keys = [_pkey(p) for p in pool]
    progress(count, count, f'{count:,} legal Showdown opponents sampled')
    return dict(cpt={k:100*captain[k]/count for k in keys},
                flex={k:100*flex[k]/count for k in keys},
                total={k:100*(captain[k]+flex[k])/count for k in keys},
                meta=dict(valid_lineups=count, requested_lineups=count, eligible_keys=keys,
                          role_pool_size=len(pool), model=MODEL, sampling=dict(field.diagnostic),
                          salary_mean=sum(p['Captain']['CptSalary']+sum(x['FlexSalary'] for x in p['Flex']) for p in field)/count))
