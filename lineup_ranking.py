"""Comparable simulated finish-rate ordering for Classic and Showdown."""
import math

BUILD_STYLES = ("Strategic", "Balanced", "Contrarian", "Chalk", "Randomized")
RANK_LABEL = "Top 1% → Top 2% → Top 5% → first-place rate → mean points"


def finish_rank(lineup):
    metrics = getattr(lineup, "sim_metrics", {}) or {}
    def number(key):
        try:
            value = float(metrics.get(key, 0) or 0)
            return value if math.isfinite(value) else 0.0
        except (TypeError, ValueError):
            return 0.0
    return (number("sim_scenarios") > 0,) + tuple(number(key) for key in (
        "sim_top_one_pct", "sim_top_two_pct", "sim_top_five_pct", "sim_win_rate", "sim_mean"))


def ranked_lineups(lineups):
    # Stable ties preserve the deterministic candidate order.
    return sorted(lineups, key=finish_rank, reverse=True)


def search_jobs(seeds, style, all_styles=False):
    """Each style receives every seed; callers split one shared budget/deadline."""
    styles = BUILD_STYLES if all_styles else (style,)
    return [(name, seed) for seed in seeds for name in styles]


def finish_tooltip(lineup):
    m = getattr(lineup, "sim_metrics", {}) or {}
    return "\n".join(f"{label}: {float(m.get(key, 0) or 0):.2f}%" for label, key in (
        ("Top 1%", "sim_top_one_pct"), ("Top 2%", "sim_top_two_pct"),
        ("Top 5%", "sim_top_five_pct"), ("First place (including ties)", "sim_win_rate"))) + (
        f"\nMean points: {float(m.get('sim_mean', 0) or 0):.2f}"
        f"\nScenarios: {int(m.get('sim_scenarios', 0) or 0):,}"
        "\nSimulated opponent-field finish rates; not real-world probabilities."
        "\nOrder: " + RANK_LABEL)
