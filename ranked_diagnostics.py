"""Bounded summaries of each 150-entry block in canonical SIM order."""
from collections import Counter
import math

from lineup_ranking import ranked_lineups
from portfolio_rules import player_key


def ranked_group_summaries(lineups, kind, salary_cap=50000, salary_strategy="Near Cap"):
    rows = ranked_lineups(list(lineups or []))
    if not any((getattr(lu, "sim_metrics", {}) or {}).get("sim_scenarios", 0) for lu in rows):
        return []
    groups = []
    for offset in range(0, len(rows), 150):
        block = rows[offset:offset + 150]
        total, captains, labels = Counter(), Counter(), {}
        flag_counts = Counter()
        flagged = salary_exceptions = scored = 0
        values = {key: [] for key in ("sim_top_one_pct", "sim_top_two_pct", "sim_top_five_pct", "sim_win_rate", "sim_mean", "duplicate_risk")}
        for lu in block:
            metrics = getattr(lu, "sim_metrics", {}) or {}
            if metrics.get("sim_scenarios", 0) > 0:
                scored += 1
                for key in values:
                    try:
                        value = float(metrics[key])
                    except (KeyError, ValueError, TypeError):
                        continue
                    if math.isfinite(value):
                        values[key].append(value)
            captain = lu.get("Captain") if kind == "showdown" else None
            roster = [captain] + list(lu.get("Flex", [])) if captain else list(lu)
            roster = [p for p in roster if p]
            for p in roster:
                key = player_key(p)
                total[key] += 1
                labels[key] = f"{p.get('Name') or 'Unknown'} [{p.get('Team') or '?'} {p.get('Position') or '?'}]"
            if captain:
                captains[player_key(captain)] += 1
                from optimizers import showdown_correlation_flags
                flags = metrics.get("showdown_correlation_flags")
                if flags is None:
                    flags = showdown_correlation_flags(captain, lu.get("Flex", []))
                flag_counts.update(flags)
                flagged += bool(flags)
            salary = sum(float(p.get("FlexSalary", 0) or 0) for p in roster)
            if captain:
                salary += float(captain.get("CptSalary", 1.5 * float(captain.get("FlexSalary", 0) or 0)) or 0) - float(captain.get("FlexSalary", 0) or 0)
            strategy = str(salary_strategy).lower()
            tolerance = 500 if "max" in strategy else 2500 if kind == "showdown" else 1000
            if ("near cap" in strategy or "max" in strategy) and salary_cap - salary > tolerance:
                salary_exceptions += 1
        def exposures(counts):
            return [dict(label=labels[key], count=count, pct=count / len(block) * 100)
                    for key, count in counts.most_common(5)]
        groups.append(dict(start=offset + 1, end=offset + len(block), count=len(block), scored=scored,
            metrics={key: dict(mean=sum(items)/len(items), low=min(items), high=max(items)) for key, items in values.items() if items},
            flagged_lineups=flagged if kind == "showdown" else None, flag_counts=dict(flag_counts),
            salary_exceptions=salary_exceptions, exposures=exposures(total), captain_exposures=exposures(captains)))
    return groups


def format_ranked_groups(groups):
    if not groups:
        return []
    lines = ["", "Ranked groups (canonical SIM order, independent of table sorting)",
             "- Each group uses its own exposure denominator; groups are not separately optimized."]
    for group in groups:
        metrics = group['metrics']
        lines.append(f"- Ranks {group['start']:,}–{group['end']:,}: {group['count']} lineups; {group['scored']} with SIM results")
        rates = []
        for key, label in (("sim_top_one_pct", "Top 1%"), ("sim_top_two_pct", "Top 2%"), ("sim_top_five_pct", "Top 5%"), ("sim_win_rate", "First")):
            if key in metrics:
                rates.append(f"{label} average {metrics[key]['mean']:.2f}%")
        if rates:
            lines.append("  - " + "; ".join(rates))
        if 'sim_top_one_pct' in metrics:
            m=metrics['sim_top_one_pct']
            lines.append(f"  - Individual top-1% rates range from {m['low']:.2f}% to {m['high']:.2f}%")
        if 'sim_mean' in metrics:
            lines.append(f"  - Average mean points: {metrics['sim_mean']['mean']:.2f}")
        if group['flagged_lineups'] is not None:
            lines.append(f"  - Correlation flags: {group['flagged_lineups']}/{group['count']} lineups; {sum(group['flag_counts'].values())} flags")
            if group['flag_counts']:
                lines.append("  - Flag types: " + "; ".join(f"{name} ({count})" for name,count in group['flag_counts'].items()))
        lines.append(f"  - Salary-strategy exceptions: {group['salary_exceptions']}/{group['count']}")
        for key, label in (('exposures', 'Highest player exposures'), ('captain_exposures', 'Highest Captain exposures')):
            if group[key]:
                lines.append(f"  - {label}: " + "; ".join(f"{r['label']} {r['count']}/{group['count']} ({r['pct']:.1f}%)" for r in group[key]))
    return lines
