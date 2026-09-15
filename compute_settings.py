"""Bounded, portable resource settings for NFL Classic Deep builds."""
from collections.abc import Mapping
import math

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
    result["all_styles"] = value.get("all_styles", False) is True
    result["selection_mode"] = "Individual ranking" if value.get("selection_mode") == "Individual ranking" else "Portfolio selection"
    return result


def deep_candidate_budget(requested, options, alternate_sources=True):
    options = normalize_deep_settings(options)
    default = min(6000, max(1200, requested * 30)) if alternate_sources else min(5000, max(1200, requested * 24))
    # A user-specified ceiling must still accommodate the requested portfolio.
    return max(requested, options["candidates"] or default)


def deep_phase_fractions(options):
    """Reserve SIM time while assigning more exploration time to individual mode."""
    individual = normalize_deep_settings(options)["selection_mode"] == "Individual ranking"
    return (0.60, 0.78) if individual else (0.38, 0.58)


def deep_search_seeds(count):
    original = (1337, 4241, 7919, 12007)
    return original + tuple(12007 + 7919 * i for i in range(1, max(4, min(32, count)) - 3))


def normalize_validation_scenarios(value):
    try:
        return max(250, min(10000, int(value)))
    except (TypeError, ValueError, OverflowError):
        return 750


# Explicit pools make each tier reproducible for the 150-lineup Acer reference.
DEEP_PROFILES = {
    "Baseline — 5 min cap": dict(minutes=5, candidates=4500, shortlist=900, field=2700, seeds=4, screening=600, scenarios=5000),
    "Balanced — 15 min cap": dict(minutes=15, candidates=8000, shortlist=1200, field=4000, seeds=8, screening=750, scenarios=10000),
    "Thorough — 20 min cap": dict(minutes=20, candidates=12000, shortlist=1200, field=4000, seeds=8, screening=1000, scenarios=10000),
    "Extended — 30 min cap": dict(minutes=30, candidates=16000, shortlist=1600, field=6000, seeds=12, screening=1000, scenarios=10000),
    "Maximum — 60 min cap": dict(minutes=60, candidates=20000, shortlist=2000, field=10000, seeds=16, screening=1000, scenarios=10000),
}


def matching_deep_profile(options, scenarios):
    normalized = normalize_deep_settings(options)
    for name, profile in DEEP_PROFILES.items():
        if all(normalized[key] == profile[key] for key in DEEP_CONTROLS) and normalize_validation_scenarios(scenarios) == profile["scenarios"]:
            return name
    return "Custom"


# Uncensored completed runs supplied by the user, September 6, 2026.
# Each tuple contains candidate budget, shortlist, field, screening, validation,
# and observed Generate/SIM/Select seconds. The 300.10s deadline run is excluded.
ACER_RUNS = (
    (4500, 900, 2700, 600, 5000, 74.34, 65.16, 121.55),
    (8000, 1200, 4000, 750, 10000, 138.58, 173.75, 262.37),
    (12000, 1200, 4000, 750, 10000, 221.21, 242.27, 283.67),
    (12000, 1200, 4000, 1000, 10000, 195.70, 250.91, 343.67),
)


def estimate_acer_runtime(options, scenarios):
    """Heuristic reference for 150 NFL Classic entries, not a fitted confidence interval.

    Scale phase costs from the closest measured workload. SIM cost is split
    40/60 between screening and validation, with a field-size adjustment.
    These weights are assumptions, not independently identified measurements.
    Report an intentionally broad +/-25% planning band (wider for extrapolation).
    """
    options = normalize_deep_settings(options)
    workload = (
        deep_candidate_budget(150, options), options["shortlist"] or 900,
        options["field"] or 2700,
        min(options["screening"], normalize_validation_scenarios(scenarios)),
        max(2500, normalize_validation_scenarios(scenarios)),
    )
    anchor = min(ACER_RUNS, key=lambda run: sum(abs(math.log(a / b)) for a, b in zip(workload, run[:5])))
    c, short, field, screen, valid = (a / b for a, b in zip(workload, anchor[:5]))
    seconds = anchor[5] * c + anchor[6] * (0.4 * c * screen + 0.6 * short * valid) * (0.5 + 0.5 * field) + anchor[7] * short
    extrapolated = any(a > max(run[i] for run in ACER_RUNS) or a < min(run[i] for run in ACER_RUNS) for i, a in enumerate(workload))
    uncertainty = 0.4 if extrapolated else 0.25
    cap = options["minutes"] * 60
    expected = min(seconds, cap)
    return {
        "seconds": expected,
        "low_minutes": max(1, math.floor(expected * (1 - uncertainty) / 60)),
        "high_minutes": max(1, math.ceil(min(seconds * (1 + uncertainty), cap * 1.1) / 60)),
        "budget_limited": seconds > cap,
        "extrapolated": extrapolated,
    }
