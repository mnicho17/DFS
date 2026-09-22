"""Bounded candidate exploration around overrepresented capped players."""
import copy
import logging
import time
from collections import Counter


def expand_capped_candidates(rows, players, requested, rules, *, salary_cap,
                             own_mode, own_weight, build_style, cancelled=lambda: False,
                             seconds=20):
    from optimizers import ShowdownOptimizer, ShowdownLineup
    from portfolio_rules import player_key, lineup_players, _candidate_signature, _pct
    if not rows or requested < 2 or cancelled():
        return []
    deadline = time.perf_counter() + max(0, seconds)
    counts = Counter(k for row in rows for k in {player_key(p) for p in lineup_players(row, 'showdown')})
    automatic = 80 if requested <= 20 else 78 if requested < 100 else 75
    targets = []
    for player in players:
        key = player_key(player)
        if player.get('LockFlex') or player.get('LockCpt'):
            continue
        cap = _pct(player.get('MaxPct'), automatic if rules.get('balance_ownership', True) else 100)
        share = counts[key] / len(rows) * 100
        if share > float(cap) and 0 < float(cap) < 100:
            targets.append((share-float(cap), key, player.get('Name', key)))
    originals = {player_key(p): p for p in players}
    seen = {_candidate_signature(row, 'showdown') for row in rows}
    extra = []
    for _, key, name in sorted(targets, reverse=True)[:4]:
        if cancelled() or time.perf_counter() >= deadline:
            break
        probe = copy.deepcopy(players)
        for player in probe:
            if player_key(player) == key:
                player['FadeFlex'] = player['FadeCpt'] = True
        opt = ShowdownOptimizer(probe, salary_cap=salary_cap, own_mode=own_mode,
                                own_weight=own_weight, build_style=build_style)
        # Force the bounded sampler even for a small output request.
        candidates = opt._build_lineups_fast(num_lineups=max(21, min(150, requested)),
            cancel_callback=lambda: cancelled() or time.perf_counter() >= deadline)
        added = 0
        for row in candidates:
            sig = _candidate_signature(row, 'showdown')
            if sig in seen:
                continue
            seen.add(sig)
            extra.append(ShowdownLineup(originals[player_key(row['Captain'])],
                         [originals[player_key(p)] for p in row['Flex']]))
            added += 1
        logging.getLogger('dfs.opt').info('Exposure coverage: %d additional candidates without %s; original tags and caps preserved.', added, name)
    return extra
