"""Explicit Showdown portfolio construction rule; no simulation changes."""
import math


def maximum(value):
    if value in (None, ''):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError('Zero-QB maximum must be a percentage from 0 to 100.') from None
    if isinstance(value, bool) or not math.isfinite(number) or not 0 <= number <= 100:
        raise ValueError('Zero-QB maximum must be a percentage from 0 to 100.')
    return number


def count_limit(pct, requested):
    return None if pct is None else math.floor(requested * pct / 100 + 1e-9)


def is_zero(lineup):
    from optimizers import _position_tokens
    players = [lineup['Captain']] + list(lineup['Flex'])
    if any(not (_position_tokens(p) & {'QB', 'RB', 'WR', 'TE', 'K', 'DST'}) for p in players):
        raise ValueError('Zero-QB maximum requires complete roster position metadata.')
    return not any('QB' in _position_tokens(p) for p in players)


def text(report):
    rule = report.get('zero_qb_rule')
    if not rule:
        return []
    return [f"Zero-QB maximum (explicit): {rule['count']}/{rule['requested']} selected; "
            f"cap {rule['limit']}/{rule['requested']} ({rule['pct']:g}%). QB counts at Captain or FLEX; this limit is not relaxed."]
