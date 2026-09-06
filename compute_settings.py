"""Bounded, portable resource settings for NFL Classic Deep builds."""
from collections.abc import Mapping

# key: (label, default, minimum, maximum, step)
DEEP_CONTROLS = {
    "minutes": ("Time budget (minutes)", 5, 1, 60, 5),
    "candidates": ("Candidate lineups", 0, 0, 20000, 500),
    "shortlist": ("Validation shortlist", 0, 0, 2000, 100),
    "field": ("Sampled opponent lineups", 0, 0, 10000, 500),
    "seeds": ("Independent search seeds", 4, 4, 32, 1),
    "screening": ("Screening scenarios", 600, 250, 1000, 250),
}


def normalize_deep_settings(value=None):
    value = value if isinstance(value, Mapping) else {}
    result = {}
    for key, (_, default, low, high, _) in DEEP_CONTROLS.items():
        try:
            number = int(value.get(key, default))
        except (TypeError, ValueError, OverflowError):
            number = default
        result[key] = max(low, min(high, number))
    return result


def deep_candidate_budget(requested, options, alternate_sources=True):
    options = normalize_deep_settings(options)
    default = min(6000, max(1200, requested * 30)) if alternate_sources else min(5000, max(1200, requested * 24))
    # A user-specified ceiling must still accommodate the requested portfolio.
    return max(requested, options["candidates"] or default)


def deep_search_seeds(count):
    original = (1337, 4241, 7919, 12007)
    return original + tuple(12007 + 7919 * i for i in range(1, max(4, min(32, count)) - 3))
