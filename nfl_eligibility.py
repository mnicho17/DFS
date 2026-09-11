"""Quarterback eligibility independent of projections, salary and personal fades."""
from collections import defaultdict

UNAVAILABLE = {'OUT', 'O', 'IR', 'PUP', 'NFI', 'SUSP', 'SUSPENDED', 'INACTIVE', 'RESERVE', 'INJURED_RESERVE'}

def unavailable(p):
    return p.get('NFLActive') is False or any(str(p.get(k) or '').strip().upper() in UNAVAILABLE
        for k in ('NFLAvailability', 'InjuryStatus', 'Status', 'NFLRosterStatus'))

def depth(p):
    try:
        return max(0, int(float(p.get('NFLDepthOrder') or 0)))
    except (ValueError, TypeError, OverflowError):
        return 0

def apply_qb_eligibility(players):
    """Annotate, never change manual fades. Only one verified active QB per team.

    Questionable/doubtful is not confirmation of absence. A missing QB1 cannot
    establish an injury. Locks, price and historical PPG cannot promote a backup.
    """
    groups = defaultdict(list)
    for p in players:
        if str(p.get('Position') or '').upper() == 'QB':
            groups[str(p.get('Team') or '').strip().upper()].append(p)
    for team, group in groups.items():
        winner = None
        starters = [p for p in group if depth(p) == 1]
        if team and len(starters) == 1:
            if not unavailable(starters[0]):
                winner = starters[0]
            else:
                candidates = [p for p in group if depth(p) > 1 and not unavailable(p)]
                if candidates:
                    first = min(depth(p) for p in candidates)
                    next_up = [p for p in candidates if depth(p) == first]
                    # Require evidence for every earlier depth slot, not just QB1.
                    earlier_out = all(any(depth(p) == d and unavailable(p) for p in group)
                                      for d in range(1, first))
                    if len(next_up) == 1 and earlier_out:
                        winner = next_up[0]
        for p in group:
            p['NFLQBEligible'] = p is winner
            p['NFLQBReason'] = ('Starting quarterback' if p is winner and depth(p) == 1 else
                'Next quarterback; earlier depth slots confirmed unavailable' if p is winner else
                'Quarterback unavailable' if unavailable(p) else
                'Backup quarterback excluded' if depth(p) > 1 else
                'Starting quarterback unverified; refresh depth chart')
    return players

def eligible_players(players, *, reject_locks=True):
    players = [dict(p) for p in players]
    if not all("NFLQBEligible" in p for p in players if str(p.get("Position") or "").upper() == "QB"):
        apply_qb_eligibility(players)
    excluded = [p for p in players if p.get('NFLQBEligible') is False]
    if reject_locks:
        locked = [p for p in excluded if p.get('LockFlex') or p.get('LockCpt')]
        if locked:
            raise ValueError('A locked quarterback is not eligible: ' + ', '.join(str(p.get('Name')) for p in locked))
    return [p for p in players if p.get('NFLQBEligible') is not False]
