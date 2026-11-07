"""Strict outcome-sign summaries for diagnostic audit reports only."""
import math
import statistics


def outcome_sign_subgroups(games, profile):
    """Average within each represented game, then equally across games.

    Missing/invalid outcomes are errors, never zeros. Negative scores remain
    separate from strictly positive outcomes; empty categories are unavailable.
    """
    groups = {label: [] for label in ('actual_zero', 'actual_positive', 'actual_negative')}
    for game in games:
        current = {label: [] for label in groups}
        for row in game['rows']:
            actual = row['actual']
            if isinstance(actual, bool) or not isinstance(actual, (int, float)) or not math.isfinite(actual):
                raise ValueError('Finite numeric actual outcome required for sign summary.')
            score = row['profiles'][profile]['crps']
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
                raise ValueError('Finite numeric CRPS required for sign summary.')
            label = 'actual_zero' if actual == 0 else 'actual_positive' if actual > 0 else 'actual_negative'
            current[label].append(score)
        for label, values in current.items():
            groups[label].append(values)
    result = {}
    for label, values in groups.items():
        present = [statistics.mean(game) for game in values if game]
        result[label] = dict(games=len(present), observations=sum(map(len, values)),
                             crps=statistics.mean(present) if present else None)
    return result
