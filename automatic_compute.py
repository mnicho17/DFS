"""Bounded profile planning; recorded preparation is never reuse authorization."""
from compute_settings import DEEP_PROFILES

CAPS = (5, 15, 20, 30, 60)


def select_profile(count, cap, prepared=False):
    if type(count) is not int or count < 1:
        raise ValueError('A positive entry count is required.')
    if type(cap) is not int or cap not in CAPS:
        raise ValueError('Choose an existing maximum time budget.')
    # Conservative planning tiers, not measured completion or quality estimates.
    desired = 5 if prepared or count <= 20 else 15 if count <= 150 else 20
    eligible = [(name, p) for name, p in DEEP_PROFILES.items() if p['minutes'] <= min(cap, desired)]
    name, profile = eligible[-1]
    evidence = 'Matching completed preparation recorded; files still require validation' if prepared else 'No matching completed preparation recorded'
    return dict(profile=name, cap=cap, minutes=profile['minutes'], requested=count,
                prepared=bool(prepared), reason=f'{count} entries; {evidence}. Planning tier capped at {cap} minutes.')


def plan(window, count):
    cap = getattr(window, '_automatic_compute_cap', None)
    if cap is None or getattr(window, '_snapshot_replay', False) or window._current_sport() != 'NFL':
        return None
    from preparation_readiness import current_view
    view = current_view(window)
    record = view.get('record', {})
    prepared = (view.get('match') == 'Matches captured inputs' and record.get('complete') is True
                and record.get('screening_complete') is True and record.get('saved', 0) > 0)
    return select_profile(count, cap, prepared)


def apply(window, count, *, repair=False):
    if repair:
        return None
    decision = plan(window, count)
    if decision is None:
        return None
    # The ordinary profile handler remains the only writer of compute settings.
    window._applying_automatic_compute = True
    try:
        window.combo_compute_profile.setCurrentText(decision['profile'])
        window.combo_compute_profile.activated[str].emit(decision['profile'])
    finally:
        window._applying_automatic_compute = False
    return dict(decision)


def summary(decision):
    if not decision:
        return ''
    return f"Automatic compute: {decision['profile']} (chosen maximum {decision['cap']} min). {decision['reason']}"
