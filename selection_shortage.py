"""Expected portfolio shortages are actionable build stops, not crashes."""

TITLE = "Build stopped: lineup limits"


class PortfolioSelectionShortage(ValueError):
    """No complete compliant portfolio was found in the available search."""


def describe(requested, selected, remaining, meta, conflicts, limits, counts,
             players, min_unique, auto_total_keys, auto_cpt_keys, group_ok):
    from collections import Counter
    blocked = Counter()
    chosen = {id(lu) for lu in selected}
    for lu in remaining:
        m = meta[id(lu)]
        if conflicts.get(id(lu), set()) & chosen:
            blocked[f"Minimum unique {min_unique}"] += 1
        if not m['keys'] or not group_ok(m['keys']):
            blocked['Player group / empty roster'] += 1
        for key in m['keys']:
            cap = limits['total'].get(key)
            if cap is not None and counts['total'][key] >= cap:
                name = players[key].get('Name') or key
                source = 'automatic' if key in auto_total_keys else 'explicit'
                blocked[f"{name}: total cap {cap}/{requested} ({source})"] += 1
        key = m['captain_key']
        cap = limits['captain'].get(key)
        if key and cap is not None and counts['captain'][key] >= cap:
            name = players[key].get('Name') or key
            source = 'automatic' if key in auto_cpt_keys else 'explicit'
            blocked[f"{name}: Captain cap {cap}/{requested} ({source})"] += 1
        for label, field in [('team', 'teams'), ('game', 'games')]:
            cap = limits[label]
            if cap is not None:
                for key in m[field]:
                    if counts[label][key] >= cap:
                        blocked[f"{label.title()} {key}: cap {cap}/{requested}"] += 1
        if m['specialist_captain'] and limits['specialist'] is not None and counts['specialist'] >= limits['specialist']:
            blocked[f"K/DST Captain cap {limits['specialist']}/{requested} (automatic)"] += 1
    lines = [TITLE, '',
        f"The search assembled {len(selected)} of {requested} requested lineups before getting stuck.",
        "No limits were relaxed. The bounded repair did not find a complete portfolio; no partial portfolio was released.",
        '', 'Limits blocking remaining candidates at the greedy stop (counts overlap):']
    lines += [f"- {label}: {count} candidates" for label, count in sorted(blocked.items(), key=lambda item: (-item[1], item[0]))[:8]]
    if not blocked:
        lines.append('- No remaining candidates in this search.')
    lines += ['', 'These counts describe this search, not proof that a full portfolio is impossible.',
        'Next: broaden the candidate pool / Deep shortlist while keeping your limits. More simulation scenarios alone do not add lineup choices.',
        'Alternatively, deliberately change a listed limit. Requesting fewer lineups also recalculates percentage caps and is not guaranteed to solve the shortage.']
    return '\n'.join(lines)
