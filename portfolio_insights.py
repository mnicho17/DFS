from __future__ import annotations

"""Aggregate, user-facing analysis of a generated lineup portfolio."""

import math
from collections import Counter
from typing import Any, Dict, List, Mapping, Optional, Sequence

from optimizers import lineup_grade_for_sport


SOURCE_LABELS = {
    "optimizer": "Optimizer",
    "field_shaped": "Field-shaped",
    "scenario_built": "Scenario-built",
}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _position(player: Mapping[str, Any]) -> str:
    raw = str(player.get("Position") or "").strip().upper().replace("D/ST", "DST")
    return raw.split("/")[0].split(",")[0]


def _team(player: Mapping[str, Any]) -> str:
    return str(player.get("Team") or "").strip().upper()


def _opponent(player: Mapping[str, Any]) -> str:
    direct = str(player.get("Opponent") or player.get("Opp") or "").strip().upper()
    if direct:
        return direct
    game = str(player.get("GameKey") or player.get("GameInfo") or "").split()[0].upper()
    if "@" not in game:
        return ""
    away, home = game.split("@", 1)
    team = _team(player)
    return home if team == away else away if team == home else ""


def _players(lineup: Any, kind: str) -> List[Dict[str, Any]]:
    if str(kind or "classic").lower() == "showdown":
        captain = dict((lineup or {}).get("Captain") or {})
        flex = [dict(player) for player in (lineup or {}).get("Flex") or []]
        return ([captain] if captain else []) + flex
    return [dict(player) for player in lineup or []]


def _salary(lineup: Any, kind: str) -> float:
    if str(kind or "classic").lower() == "showdown":
        captain = (lineup or {}).get("Captain") or {}
        return _number(captain.get("CptSalary"), _number(captain.get("FlexSalary")) * 1.5) + sum(
            _number(player.get("FlexSalary")) for player in (lineup or {}).get("Flex") or []
        )
    return sum(_number(player.get("FlexSalary")) for player in lineup or [])


def _ownership(players: Sequence[Mapping[str, Any]]) -> List[float]:
    return [_number(player.get("ProjOwnPct"), _number(player.get("ProjFlexOwnPct"))) for player in players]


def _source(lineup: Any, metrics: Mapping[str, Any]) -> str:
    raw = str(
        getattr(lineup, "candidate_source", "")
        or metrics.get("candidate_source")
        or "optimizer"
    ).strip().lower()
    return raw if raw in SOURCE_LABELS else "optimizer"


def _archetype(lineup: Any, metrics: Mapping[str, Any]) -> str:
    return str(
        getattr(lineup, "candidate_archetype", "")
        or metrics.get("candidate_archetype")
        or ""
    ).strip()


def _nfl_construction(players: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    quarterbacks = [player for player in players if _position(player) == "QB"]
    if len(quarterbacks) != 1:
        return {"stack": "No single QB", "stack_count": 0, "bringback": "n/a", "flex": "n/a"}
    qb = quarterbacks[0]
    qb_team = _team(qb)
    opponent = _opponent(qb)
    stack_count = sum(
        1 for player in players
        if _team(player) == qb_team and _position(player) in {"WR", "TE"}
    )
    bringback = any(
        _team(player) == opponent and _position(player) in {"RB", "WR", "TE"}
        for player in players
    ) if opponent else False
    positions = Counter(_position(player) for player in players)
    excess = {
        "RB": positions["RB"] - 2,
        "WR": positions["WR"] - 3,
        "TE": positions["TE"] - 1,
    }
    flex = max(excess, key=excess.get)
    if excess[flex] <= 0:
        flex = "n/a"
    return {
        "stack": f"QB+{stack_count}",
        "stack_count": stack_count,
        "bringback": "Yes" if bringback else "No",
        "flex": flex,
    }


def _distribution(counter: Counter[str], total: int, labels: Optional[Mapping[str, str]] = None) -> str:
    if not counter:
        return "n/a"
    mapping = dict(labels or {})
    return ", ".join(
        f"{mapping.get(key, key)} {count} ({count / max(1, total) * 100.0:.0f}%)"
        for key, count in counter.most_common()
    )


def build_portfolio_insights(
    lineups: Sequence[Any],
    *,
    sport: str = "NFL",
    kind: str = "classic",
    salary_cap: float = 50000.0,
    field_preset: str = "",
    source_label: str = "generated",
    portfolio_report: Optional[Mapping[str, Any]] = None,
    sim_report: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Summarize construction, tournament quality, concentration, and source mix."""
    selected = list(lineups or [])
    sport_u = str(sport or "NFL").strip().upper()
    kind_l = str(kind or "classic").strip().lower()
    cap = max(1.0, _number(salary_cap, 50000.0))
    portfolio = dict(portfolio_report or {})
    sim = dict(sim_report or {})

    grade_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    archetype_counts: Counter[str] = Counter()
    stack_counts: Counter[str] = Counter()
    bringback_counts: Counter[str] = Counter()
    flex_counts: Counter[str] = Counter()
    salary_bands: Counter[str] = Counter()
    duplication_bands: Counter[str] = Counter()
    scenario_hits: set[int] = set()
    salaries: List[float] = []
    ownership_totals: List[float] = []
    sub_five_counts: List[int] = []
    twenty_plus_counts: List[int] = []
    edges: List[float] = []
    leverage_values: List[float] = []
    duplication_values: List[float] = []
    rows: List[Dict[str, Any]] = []

    for index, lineup in enumerate(selected, start=1):
        players = _players(lineup, kind_l)
        metrics = getattr(lineup, "sim_metrics", None)
        metrics = dict(metrics) if isinstance(metrics, dict) else {}
        salary = _salary(lineup, kind_l)
        salaries.append(salary)
        ownership = _ownership(players)
        ownership_total = sum(ownership)
        ownership_totals.append(ownership_total)
        sub_five_counts.append(sum(value < 5.0 for value in ownership))
        twenty_plus_counts.append(sum(value >= 20.0 for value in ownership))

        grade_info: Dict[str, Any] = {}
        if kind_l == "classic":
            try:
                grade_info = dict(lineup_grade_for_sport(lineup, sport_u, cap) or {})
            except Exception:
                grade_info = {}
        grade = str(grade_info.get("grade") or ("SIM" if metrics else "n/a"))
        grade_counts[grade] += 1

        source = _source(lineup, metrics)
        source_counts[source] += 1
        archetype = _archetype(lineup, metrics)
        if archetype:
            archetype_counts[archetype] += 1

        construction = _nfl_construction(players) if sport_u == "NFL" and kind_l == "classic" else {
            "stack": str(grade_info.get("stack_shape") or "n/a"),
            "stack_count": 0,
            "bringback": "n/a",
            "flex": "n/a",
        }
        stack_counts[str(construction["stack"])] += 1
        bringback_counts[str(construction["bringback"])] += 1
        flex_counts[str(construction["flex"])] += 1

        left = max(0.0, cap - salary)
        if left <= 500.0:
            salary_band = "$0-$500 left"
        elif left <= 1500.0:
            salary_band = "$501-$1,500 left"
        elif left <= 3000.0:
            salary_band = "$1,501-$3,000 left"
        else:
            salary_band = "$3,000+ left"
        salary_bands[salary_band] += 1

        edge = _number(metrics.get("sim_edge"))
        leverage = _number(metrics.get("sim_leverage"))
        duplicate = _number(metrics.get("duplicate_risk"))
        if metrics:
            edges.append(edge)
            leverage_values.append(leverage)
            duplication_values.append(duplicate)
            duplication_bands[
                "Low (<35)" if duplicate < 35.0 else "Moderate (35-69)" if duplicate < 70.0 else "High (70+)"
            ] += 1
        scenario_hits.update(set(getattr(lineup, "sim_top_hits", set()) or set()))

        rows.append({
            "number": index,
            "grade": grade,
            "source": SOURCE_LABELS[source],
            "archetype": archetype or "—",
            "salary": salary,
            "salary_left": left,
            "stack": construction["stack"],
            "bringback": construction["bringback"],
            "flex": construction["flex"],
            "ownership": ownership_total,
            "edge": edge if metrics else None,
            "leverage": leverage if metrics else None,
            "duplication": duplicate if metrics else None,
            "top_one_pct": _number(metrics.get("sim_top_one_pct")) if metrics else None,
            "return_index": _number(metrics.get("sim_return_index")) if metrics else None,
            "top_scenarios": len(set(getattr(lineup, "sim_top_hits", set()) or set())),
        })

    total = len(selected)
    average_salary = sum(salaries) / max(1, len(salaries)) if salaries else 0.0
    average_left = max(0.0, cap - average_salary) if salaries else 0.0
    scenario_total = max(
        [int(_number((getattr(lineup, "sim_metrics", {}) or {}).get("sim_scenarios"))) for lineup in selected]
        or [int(_number((portfolio.get("sim_summary") or {}).get("scenario_count")))]
    )
    fit = dict(sim.get("preset_comparison") or {})

    review_flags: List[str] = []
    for warning in portfolio.get("warnings") or []:
        text = str(warning or "").strip()
        if text and text not in review_flags:
            review_flags.append(text)
    weak_grades = grade_counts["C"] + grade_counts["D"]
    if total and weak_grades / total >= 0.40:
        review_flags.append(
            f"{weak_grades}/{total} lineups are C or D grades; review whether diversification displaced too much slate-relative quality."
        )
    high_dup = sum(value >= 70.0 for value in duplication_values)
    if duplication_values and high_dup / len(duplication_values) >= 0.25:
        review_flags.append(
            f"{high_dup}/{len(duplication_values)} SIM lineups have high duplication risk; inspect their leverage before entry."
        )
    low_salary = sum(value < cap - 2000.0 for value in salaries)
    if salaries and low_salary / len(salaries) >= 0.20:
        review_flags.append(
            f"{low_salary}/{len(salaries)} lineups leave more than $2,000; confirm that the unused salary is intentional leverage."
        )
    unstacked = stack_counts.get("QB+0", 0) + stack_counts.get("No single QB", 0)
    if sport_u == "NFL" and kind_l == "classic" and total and unstacked:
        review_flags.append(f"{unstacked}/{total} NFL lineups do not have a standard QB pass-catcher stack.")
    top_players = list(portfolio.get("players") or [])
    concentrated = [row for row in top_players if _number(row.get("pct")) >= 70.0]
    if concentrated:
        review_flags.append(
            f"{len(concentrated)} player exposure(s) reach 70% or more; confirm those cores match your risk tolerance."
        )

    source_generated = dict((sim.get("candidate_sources") or {}).get("generated") or {})
    preset_text = str(field_preset or fit.get("preset") or sim.get("field_preset") or "n/a")
    lines = [
        "DFS Optimizer Portfolio Insights",
        f"Scope: {total} {source_label} {sport_u} {kind_l.title()} lineup{'s' if total != 1 else ''}",
        f"Contest preset: {preset_text}",
        "",
        "Quality",
        f"- Grades: {_distribution(grade_counts, total)}",
        f"- Average salary: ${average_salary:,.0f} (${average_left:,.0f} left)",
        f"- Salary bands: {_distribution(salary_bands, total)}",
    ]
    if edges:
        lines.extend([
            f"- Average SIM Edge: {sum(edges) / len(edges):.1f}/100",
            f"- Average leverage: {sum(leverage_values) / len(leverage_values):.1f}/100",
            f"- Average duplication risk: {sum(duplication_values) / len(duplication_values):.1f}/100",
            f"- Duplication bands: {_distribution(duplication_bands, len(duplication_values))}",
        ])
    if fit.get("available"):
        lines.append(f"- Preset fit: {_number(fit.get('fit_score')):.0f}/100 — {fit.get('summary') or ''}")

    lines.extend([
        "",
        "Candidate sources",
        f"- Selected: {_distribution(source_counts, total, SOURCE_LABELS)}",
    ])
    if source_generated:
        generated_counter = Counter({str(key): int(_number(value)) for key, value in source_generated.items()})
        lines.append(
            f"- Unique candidate bank: {_distribution(generated_counter, sum(generated_counter.values()), SOURCE_LABELS)}"
        )
    if archetype_counts:
        lines.append(f"- Scenario archetypes selected: {_distribution(archetype_counts, sum(archetype_counts.values()))}")

    lines.extend([
        "",
        "Construction",
        f"- QB stacks: {_distribution(stack_counts, total)}",
        f"- Bring-backs: {_distribution(bringback_counts, total)}",
        f"- FLEX mix: {_distribution(flex_counts, total)}",
        f"- Ownership: average combined {sum(ownership_totals) / max(1, len(ownership_totals)):.1f}%; "
        f"average {sum(sub_five_counts) / max(1, len(sub_five_counts)):.1f} plays below 5% and "
        f"{sum(twenty_plus_counts) / max(1, len(twenty_plus_counts)):.1f} plays at 20%+",
    ])
    if scenario_total:
        lines.extend([
            "",
            "Scenario coverage",
            f"- Top-1% paths covered: {len(scenario_hits)}/{scenario_total} ({len(scenario_hits) / max(1, scenario_total) * 100.0:.1f}%)",
            "- Coverage rewards different strong game scripts; it does not mean every covered path is equally likely.",
        ])
    if top_players:
        leaders = ", ".join(
            f"{row.get('name')}: {_number(row.get('pct')):.0f}%" for row in top_players[:6]
        )
        lines.extend(["", "Concentration", f"- Highest player exposure: {leaders or 'n/a'}"])

    lines.extend(["", "Review flags"])
    if review_flags:
        lines.extend(f"- {flag}" for flag in review_flags)
    else:
        lines.append("- No automatic review flags. Continue with normal player-news and pre-lock checks.")
    lines.extend([
        "",
        "Interpretation: These are slate-relative portfolio patterns, not a prediction or guarantee. "
        "Review individual lineups, news, exposure, and contest fit before entry.",
    ])

    return {
        "lineup_count": total,
        "status": "Ready" if not review_flags else f"Review {len(review_flags)} flag{'s' if len(review_flags) != 1 else ''}",
        "grade_counts": dict(grade_counts),
        "source_counts": dict(source_counts),
        "archetype_counts": dict(archetype_counts),
        "scenario_count": scenario_total,
        "scenario_coverage": len(scenario_hits),
        "review_flags": review_flags,
        "lineup_rows": rows,
        "text": "\n".join(lines),
    }
