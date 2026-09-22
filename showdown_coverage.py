"""Bounded candidate exploration around overrepresented capped players."""
import copy
import logging
import time
from collections import Counter


def expand_capped_candidates(rows, players, requested, rules, *, salary_cap,
                             own_mode, own_weight, build_style, cancelled=lambda: False,
                             seconds=20, max_extra=600):
    from optimizers import ShowdownOptimizer, ShowdownLineup
    from portfolio_rules import player_key, lineup_players, _candidate_signature, _pct
    if not rows or requested < 2 or cancelled():
        return []
    deadline = time.perf_counter() + max(0, seconds)
    counts = Counter(k for row in rows for k in {player_key(p) for p in lineup_players(row, 'showdown')})
    captain_counts = Counter(player_key(row['Captain']) for row in rows)
    automatic = 80 if requested <= 20 else 78 if requested < 100 else 75
    automatic_captain = 35 if requested <= 20 else 32 if requested < 100 else 30
    targets = []
    for player in players:
        key = player_key(player)
        if player.get('LockFlex') or player.get('LockCpt'):
            continue
        constraint = dict(player)
        constraint.update({k:v for k,v in rules.get('player_constraints', {}).get(key, {}).items()
                           if v not in (None, '')})
        if constraint.get('LockFlex') or constraint.get('LockCpt'):
            continue
        cap = _pct(constraint.get('MaxPct'), automatic if rules.get('balance_ownership', True) else 100)
        share = counts[key] / len(rows) * 100
        total_target = share > float(cap) and 0 < float(cap) < 100
        if total_target:
            targets.append((share-float(cap), key, player.get('Name', key), 'total'))
        captain_cap = _pct(constraint.get('MaxCptPct'), automatic_captain if rules.get('balance_ownership', True) else 100)
        captain_share = captain_counts[key] / len(rows) * 100
        if not total_target and captain_share > captain_cap and 0 < captain_cap < 100:
            targets.append((captain_share-captain_cap, key, player.get('Name', key), 'captain'))
    originals = {player_key(p): p for p in players}
    seen = {_candidate_signature(row, 'showdown') for row in rows}
    extra = []
    for _, key, name, role in sorted(targets, reverse=True)[:4]:
        if cancelled() or time.perf_counter() >= deadline or len(extra) >= max_extra:
            break
        probe = copy.deepcopy(players)
        for player in probe:
            if player_key(player) == key:
                player['FadeCpt'] = True
                if role == 'total':
                    player['FadeFlex'] = True
        opt = ShowdownOptimizer(probe, salary_cap=salary_cap, own_mode=own_mode,
                                own_weight=own_weight, build_style=build_style)
        # Force the bounded sampler even for a small output request.
        candidates = opt._build_lineups_fast(num_lineups=max(21, min(150, requested)),
            cancel_callback=lambda: cancelled() or time.perf_counter() >= deadline)
        added = 0
        for row in candidates:
            sig = _candidate_signature(row, 'showdown')
            if sig in seen or len(extra) >= max_extra:
                continue
            seen.add(sig)
            extra.append(ShowdownLineup(originals[player_key(row['Captain'])],
                         [originals[player_key(p)] for p in row['Flex']]))
            added += 1
        logging.getLogger('dfs.opt').info('Exposure coverage: %d additional candidates excluding %s from %s; original tags and caps preserved.', added, name, role)
    return extra
