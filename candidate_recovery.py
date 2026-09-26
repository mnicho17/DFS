"""Generate bounded alternatives before scoring, without changing user inputs."""
from collections import Counter
from copy import deepcopy
import time

from portfolio_rules import player_key, lineup_players, _candidate_signature, _max_count


def expand_candidates(rows, players, requested, rules, *, kind, salary_cap, own_mode,
                      own_weight, build_style, cancelled=lambda: False, seconds=20,
                      max_additions=None, sport='NFL', salary_strategy='Balanced Spend',
                      mlb_stack_pref='Auto', retained=(), automatic=True):
    from optimizers import ShowdownOptimizer, ShowdownLineup, MultiSportClassicOptimizer
    if not rows or requested < 2 or cancelled() or seconds <= 0:
        return []
    allowance = max(0, int(max_additions if max_additions is not None else min(600, requested * 4)))
    deadline = time.perf_counter() + seconds
    stop = lambda: cancelled() or time.perf_counter() >= deadline
    originals = {player_key(p): p for p in players}
    configured = (rules or {}).get('player_constraints') or {}
    active = []
    for p in players:
        merged = dict(p)
        merged.update({k: v for k, v in configured.get(player_key(p), {}).items() if v not in (None, '')})
        for field in ('LockFlex', 'LockCpt', 'FadeFlex', 'FadeCpt'):
            if p.get(field):
                merged[field] = True
        active.append(merged)
    auto = kind == 'showdown' and automatic and (rules or {}).get('balance_ownership', True)
    total_pct, cpt_pct = (80, 35) if requested <= 20 else (78, 32) if requested < 100 else (75, 30)
    seen = {_candidate_signature(lu, kind) for lu in list(rows) + list(retained)}
    extra = []

    def targets(role):
        bank = list({_candidate_signature(lu, kind): lu for lu in list(rows) + list(retained) + extra}.values())
        counts = Counter(player_key(lu['Captain']) for lu in bank) if role == 'captain' else Counter(
            key for lu in bank for key in {player_key(p) for p in lineup_players(lu, kind)})
        found = []
        for p in active:
            key = player_key(p)
            if p.get('LockCpt') or p.get('LockFlex'):
                continue
            value = p.get('MaxCptPct' if role == 'captain' else 'MaxPct')
            if value in (None, '') and auto:
                value = cpt_pct if role == 'captain' else total_pct
            cap = _max_count(value, requested)
            # Necessary coverage bound: enough unique rows must omit this
            # player/role to finish under its cap. A high share alone is OK.
            if cap is not None and cap < requested:
                deficit = requested - cap - (len(bank) - counts[key])
                if counts[key] and deficit > 0 and (role != 'captain' or counts[key] > cap):
                    found.append((deficit, key))
        return sorted(found, key=lambda item: (-item[0], item[1]))

    for role in (('total', 'captain') if kind == 'showdown' else ('total',)):
        attempted = set()
        for _ in range(4):
            options = [(d, k) for d, k in targets(role) if k not in attempted]
            if not options or stop() or len(extra) >= allowance:
                break
            deficit, key = options[0]
            attempted.add(key)
            probe = deepcopy(active)
            for p in probe:
                if player_key(p) == key:
                    if role == 'total':
                        p['FadeFlex'] = True
                    if kind == 'showdown':
                        p['FadeCpt'] = True
            common = dict(salary_cap=salary_cap, own_mode=own_mode, own_weight=own_weight,
                          build_style=build_style, seed=63001 + len(attempted))
            count = min(allowance - len(extra), max(21, min(150, max(requested, deficit))))
            if kind == 'showdown':
                optimizer = ShowdownOptimizer(probe, **common)
                generated = optimizer._build_lineups_fast(num_lineups=count, cancel_callback=stop)
                from showdown_simulation import filter_salary_candidates
                generated = filter_salary_candidates(generated, salary_cap, salary_strategy)
            else:
                optimizer = MultiSportClassicOptimizer(probe, sport=sport, salary_strategy=salary_strategy,
                                                       mlb_stack_pref=mlb_stack_pref, **common)
                generated = optimizer.build_lineups(num_lineups=count, cancel_callback=stop,
                    minimum_unique=int((rules or {}).get('min_unique', 1) or 1),
                    exact_excluded_signatures=seen,
                    excluded_signatures=[_candidate_signature(lu, kind) for lu in retained])
            for lu in generated:
                if cancelled() or len(extra) >= allowance:
                    break
                signature = _candidate_signature(lu, kind)
                if signature in seen:
                    continue
                seen.add(signature)
                if kind == 'showdown':
                    extra.append(ShowdownLineup(originals[player_key(lu['Captain'])],
                                               [originals[player_key(p)] for p in lu['Flex']]))
                else:
                    extra.append([originals[player_key(p)] for p in lu])
    return extra
