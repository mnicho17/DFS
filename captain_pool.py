"""Visible, build-local exposure reconciliation for an explicit Captain pool."""
from copy import deepcopy
from portfolio_rules import player_key


def prepare_captain_pool(players, rules, requested, retained=()):
    if not any(p.get('LockCpt') for p in players):
        return players, rules or {}, []
    players, rules = deepcopy(players), deepcopy(rules or {})
    pool = sorted([p for p in players if p.get('LockCpt')], key=player_key)
    if not pool:
        return players, rules, []
    if len(pool) > requested:
        raise ValueError('Request at least one lineup per selected Captain, or reduce the Captain pool.')
    changes = []
    configured = dict(rules.get('player_constraints') or {})
    rules['player_constraints'] = configured
    quotas = {player_key(p): 0 for p in pool}
    for row in retained:
        key = player_key(row.get('Captain') or {})
        if key not in quotas:
            raise ValueError('A retained lineup has a Captain outside the selected pool.')
        quotas[key] += 1
    for _ in range(requested - sum(quotas.values())):
        key = min(quotas, key=lambda k: (quotas[k], k))
        quotas[key] += 1
    for player in pool:
        if player.get('FadeCpt') or player.get('LockFlex'):
            raise ValueError('Captain pool conflicts with a fade or FLEX lock: '+str(player.get('Name')))
        count = quotas[player_key(player)]
        pct = 100 * count / requested
        # Candidate generation requests surplus rows. Only the final selector
        # enforces exact integer quotas; generation must not exhaust them early.
        values = dict(MaxPct=100, MaxCptPct=100, MinCptPct=0)
        if len(pool) == 1:
            values.update(MinPct=100, MaxFlexPct=0)
        override = dict(configured.get(player_key(player), {}))
        original = dict(player)
        original.update(override)
        for field, after in values.items():
            before = override.get(field, player.get(field))
            if before != after:
                changes.append(dict(player=player.get('Name'), field='Generation '+field, before=before, after=after))
            player[field] = after
            override[field] = after
        for field in ('MinCptPct','MaxCptPct'):
            changes.append(dict(player=player.get('Name'), field='Portfolio '+field,
                                before=original.get(field), after=pct))
        override.update(LockCpt=True, MinCptPct=pct, MaxCptPct=pct)
        configured[player_key(player)] = override
        changes.append(dict(player=player.get('Name'), field='Captain lineups', before=None, after=count))
    selected = {player_key(p) for p in pool}
    for player in players:
        if player_key(player) in selected:
            continue
        override = dict(configured.get(player_key(player), {}))
        for field in ('MinCptPct', 'MaxCptPct'):
            before = override.get(field, player.get(field))
            if before not in (None, '', 0):
                changes.append(dict(player=player.get('Name'), field=field, before=before, after=0))
            player[field] = override[field] = 0
        configured[player_key(player)] = override
    return players, rules, changes


def adjustment_text(changes):
    if not changes:
        return ''
    return 'Captain pool adjustments (this build):\n'+'\n'.join(
        f"- {c['player']}: {c['field']} {c['before'] if c['before'] is not None else 'default'} → {c['after']}"
        for c in changes)
