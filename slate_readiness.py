from __future__ import annotations

"""Slate-level preflight checks for lineup generation.

The audit is deliberately report-only.  It explains incomplete or stale inputs
without silently changing projections, ownership, locks, or player eligibility.
"""

import math
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


INACTIVE_STATUSES = {"OUT", "IR", "PUP", "NFI", "SUSP", "SUSPENDED", "INACTIVE"}
REVIEW_STATUSES = {"Q", "QUESTIONABLE", "D", "DOUBTFUL", "GTD", "GAME TIME DECISION"}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _status(player: Mapping[str, Any]) -> str:
    return str(
        player.get("NFLAvailability")
        or player.get("InjuryStatus")
        or player.get("Status")
        or ""
    ).strip().upper()


def _position(player: Mapping[str, Any]) -> str:
    raw = str(player.get("Position") or "").strip().upper().replace("D/ST", "DST")
    return raw.split("/")[0].split(",")[0]


def _position_tokens(player: Mapping[str, Any]) -> set[str]:
    raw = str(player.get("Position") or "").strip().upper().replace("D/ST", "DST")
    return {token.strip() for token in raw.replace("/", ",").split(",") if token.strip()}


def _eligible_players(players: Iterable[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    return [
        player for player in players
        if _number(player.get("FlexSalary"), 0.0) > 0
        and not bool(player.get("FadeFlex"))
        and _status(player) not in INACTIVE_STATUSES
    ]


def _parse_datetime(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _check(
    key: str,
    label: str,
    status: str,
    summary: str,
    action: str = "",
    *,
    weight: float = 1.0,
    details: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "status": status,
        "summary": summary,
        "action": action,
        "weight": max(0.0, float(weight)),
        "details": dict(details or {}),
    }


def _roster_check(
    eligible: Sequence[Mapping[str, Any]], sport: str, mode: str
) -> Dict[str, Any]:
    if mode == "showdown":
        teams = {str(player.get("Team") or "").strip().upper() for player in eligible}
        teams.discard("")
        viable = len(eligible) >= 6 and len(teams) >= 2
        return _check(
            "roster_pool", "Roster pool", "pass" if viable else "block",
            f"{len(eligible)} eligible players across {len(teams)} teams.",
            "Load a complete single-game salary file with at least six active players from both teams." if not viable else "",
            weight=2.0,
        )

    requirements = {
        "NFL": {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "DST": 1},
        "MLB": {"P": 2, "C": 1, "1B": 1, "2B": 1, "3B": 1, "SS": 1, "OF": 3},
        "NBA": {"PG": 1, "SG": 1, "SF": 1, "PF": 1, "C": 1},
        "WNBA": {"G": 2, "F": 3},
    }.get(sport, {})
    counts = Counter(token for player in eligible for token in _position_tokens(player))
    missing = [f"{pos} {counts[pos]}/{needed}" for pos, needed in requirements.items() if counts[pos] < needed]
    roster_size = {"NFL": 9, "MLB": 10, "NBA": 8, "WNBA": 6}.get(sport, 6)
    viable = len(eligible) >= roster_size and not missing
    summary = f"{len(eligible)} eligible players"
    if requirements:
        summary += "; " + ", ".join(f"{pos} {counts[pos]}" for pos in requirements)
    if missing:
        summary += ". Missing: " + ", ".join(missing)
    else:
        summary += "."
    return _check(
        "roster_pool", "Roster pool", "pass" if viable else "block", summary,
        "Unfade active players or load a complete salary file before generating." if not viable else "",
        weight=2.0,
        details={"counts": dict(counts)},
    )


def _portfolio_checks(
    lineups: Sequence[Any],
    sport: str,
    mode: str,
    salary_cap: float,
    field_preset: Mapping[str, Any],
    sim_report: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    if not lineups:
        return [_check(
            "portfolio", "Generated portfolio", "review",
            "No generated portfolio is available yet.",
            "Run Generate, then reopen Slate Readiness to compare the portfolio with the contest preset.",
            weight=0.65,
        )]

    expected = 6 if mode == "showdown" else {"NFL": 9, "MLB": 10, "NBA": 8, "WNBA": 6}.get(sport, 6)
    normalized: List[Sequence[Mapping[str, Any]]] = []
    salaries: List[float] = []
    for lineup in lineups:
        if mode == "showdown" and isinstance(lineup, Mapping):
            captain = lineup.get("Captain")
            flex = list(lineup.get("Flex") or [])
            players = [captain] + flex
            normalized.append([player for player in players if isinstance(player, Mapping)])
            salaries.append(
                _number(captain.get("CptSalary"), 0.0) if isinstance(captain, Mapping) else 0.0
            )
            salaries[-1] += sum(_number(player.get("FlexSalary"), 0.0) for player in flex if isinstance(player, Mapping))
        elif isinstance(lineup, Sequence):
            players = [player for player in lineup if isinstance(player, Mapping)]
            normalized.append(players)
            salaries.append(sum(_number(player.get("FlexSalary"), 0.0) for player in players))
    invalid = sum(len(lineup) != expected for lineup in normalized)
    avg_salary = sum(salaries) / max(1, len(salaries))
    min_target = salary_cap * _number(field_preset.get("min_salary_pct"), 0.94)
    low_salary = sum(salary < min_target for salary in salaries)
    low_salary_rate = low_salary / max(1, len(salaries))
    status = "block" if invalid else ("review" if low_salary_rate > 0.20 else "pass")
    preset_label = str(field_preset.get("name") or "selected")
    summary = (
        f"{len(normalized)} lineups; average salary ${avg_salary:,.0f}; "
        f"{low_salary} below the {preset_label} field floor (${min_target:,.0f})."
    )
    if invalid:
        summary += f" {invalid} incomplete lineups detected."
    checks = [_check(
        "portfolio", "Generated portfolio", status, summary,
        "Review low-salary strategy or incomplete lineup constraints before export." if status != "pass" else "",
        weight=1.25,
    )]

    comparison = dict(sim_report.get("preset_comparison") or {})
    if comparison:
        fit_score = _number(comparison.get("fit_score"), 0.0)
        fit_status = "pass" if fit_score >= 75.0 else "review"
        checks.append(_check(
            "preset_fit", "Contest-preset fit", fit_status,
            str(comparison.get("summary") or f"Generated field fit is {fit_score:.0f}/100."),
            "Review the salary, stack, bring-back, and FLEX differences before relying on SIM Edge." if fit_status != "pass" else "",
            weight=1.0,
            details=comparison,
        ))
    return checks


def audit_slate(
    players: Sequence[Mapping[str, Any]],
    *,
    sport: str = "NFL",
    mode: str = "classic",
    salary_cap: float = 50000.0,
    field_preset: Optional[Mapping[str, Any]] = None,
    live_summary: Optional[Mapping[str, Any]] = None,
    generated_lineups: Optional[Sequence[Any]] = None,
    sim_report: Optional[Mapping[str, Any]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Return a report-only readiness score and actionable checks."""
    sport_u = str(sport or "NFL").strip().upper()
    mode_l = str(mode or "classic").strip().lower()
    preset = dict(field_preset or {})
    preset.setdefault("name", "")
    live = dict(live_summary or {})
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    checks: List[Dict[str, Any]] = []

    if not players:
        checks.append(_check(
            "salary_file", "Salary file", "block", "No slate is loaded.",
            "Load a DraftKings salary CSV.", weight=2.0,
        ))
        eligible: List[Mapping[str, Any]] = []
    else:
        duplicate_ids = len(players) - len({
            str(player.get("FlexID") or player.get("FlexNamePlusID") or player.get("Name") or "").strip()
            for player in players
        })
        status = "review" if duplicate_ids else "pass"
        checks.append(_check(
            "salary_file", "Salary file", status,
            f"{len(players)} player rows loaded" + (f"; {duplicate_ids} duplicate identities." if duplicate_ids else "."),
            "Remove duplicate player rows or reload the original DraftKings file." if duplicate_ids else "",
            weight=1.5,
        ))
        eligible = _eligible_players(players)
        checks.append(_roster_check(eligible, sport_u, mode_l))

    if players:
        projection_count = sum(_number(player.get("FlexProjection"), 0.0) > 0 for player in eligible)
        projection_pct = projection_count / max(1, len(eligible)) * 100.0
        projection_status = "pass" if projection_pct >= 90.0 else ("review" if projection_pct >= 70.0 else "block")
        checks.append(_check(
            "projections", "Projections", projection_status,
            f"Positive projections for {projection_count}/{len(eligible)} eligible players ({projection_pct:.0f}%).",
            "Add projections or remove non-starters before generation." if projection_status != "pass" else "",
            weight=2.0,
            details={
                "player_names": [
                    str(player.get("Name") or "") for player in eligible
                    if _number(player.get("FlexProjection"), 0.0) <= 0
                ],
            },
        ))

        ownership_count = sum(_number(player.get("ProjOwnPct"), 0.0) > 0 for player in eligible)
        ownership_pct = ownership_count / max(1, len(eligible)) * 100.0
        ownership_total = sum(max(0.0, _number(player.get("ProjOwnPct"), 0.0)) for player in eligible)
        expected_total = 600.0 if mode_l == "showdown" else ({"NFL": 900.0, "MLB": 1000.0, "NBA": 800.0, "WNBA": 600.0}.get(sport_u, 0.0))
        total_sane = not expected_total or 0.65 * expected_total <= ownership_total <= 1.35 * expected_total
        own_status = "pass" if ownership_pct >= 80.0 and total_sane else "review"
        checks.append(_check(
            "ownership", "Ownership", own_status,
            f"Ownership for {ownership_count}/{len(eligible)} eligible players ({ownership_pct:.0f}%); pool total {ownership_total:.0f}%"
            + (f" vs about {expected_total:.0f}% expected." if expected_total else "."),
            "Run Recalc Own% (Sim) or import better ownership estimates." if own_status != "pass" else "",
            weight=1.25,
            details={
                "player_names": [
                    str(player.get("Name") or "") for player in eligible
                    if _number(player.get("ProjOwnPct"), 0.0) <= 0
                ],
            },
        ))

        locked_conflicts = [
            player for player in players
            if (bool(player.get("LockFlex")) or bool(player.get("LockCpt")))
            and (_status(player) in INACTIVE_STATUSES or bool(player.get("LiveStatusConflict")))
        ]
        checks.append(_check(
            "locks", "Locks and availability", "block" if locked_conflicts else "pass",
            f"{len(locked_conflicts)} locked unavailable player(s)." if locked_conflicts else "No locked unavailable players.",
            "Unlock or manually resolve: " + ", ".join(str(player.get("Name") or "Unknown") for player in locked_conflicts[:6]) if locked_conflicts else "",
            weight=2.0,
            details={
                "player_names": [str(player.get("Name") or "") for player in locked_conflicts],
            },
        ))

    if sport_u == "NFL" and players:
        checked_at = _parse_datetime(live.get("checked_at"))
        age_minutes = (current - checked_at).total_seconds() / 60.0 if checked_at else None
        sleeper_ok = str(live.get("sleeper_state") or "") == "ok"
        matched = int(_number(live.get("sleeper"), 0.0))
        coverage = matched / max(1, len(players)) * 100.0
        fresh = age_minutes is not None and age_minutes <= 20.0
        live_status = "pass" if sleeper_ok and coverage >= 75.0 and fresh else "review"
        age_text = "not checked" if age_minutes is None else (f"{max(0.0, age_minutes):.0f} minutes old")
        checks.append(_check(
            "live_status", "Player news and roles", live_status,
            f"Sleeper match {matched}/{len(players)} ({coverage:.0f}%); {age_text}.",
            "Run Game-Day Check immediately before lock." if live_status != "pass" else "",
            weight=1.5,
            details={"age_minutes": age_minutes, "coverage_pct": coverage},
        ))

        uncertain = [player for player in eligible if _status(player) in REVIEW_STATUSES]
        deep_backups = [
            player for player in eligible
            if int(_number(player.get("NFLDepthOrder"), 0.0)) >= 3
            and _position(player) not in {"DST", "K"}
        ]
        role_status = "review" if uncertain or deep_backups else "pass"
        checks.append(_check(
            "roles", "Starter certainty", role_status,
            f"{len(uncertain)} questionable/doubtful and {len(deep_backups)} active depth-order 3+ players remain eligible.",
            "Review the flagged players and fade speculative backups that are not part of your build." if role_status != "pass" else "",
            weight=1.0,
            details={
                "uncertain": [str(player.get("Name") or "") for player in uncertain[:12]],
                "deep_backups": [str(player.get("Name") or "") for player in deep_backups[:12]],
                "player_names": list(dict.fromkeys(
                    [str(player.get("Name") or "") for player in uncertain + deep_backups]
                )),
            },
        ))

        odds_state = str(live.get("odds_state") or "not_configured")
        odds_games = int(_number(live.get("odds_matched_games", live.get("odds_games")), 0.0))
        slate_games = len({str(player.get("GameKey") or "").strip() for player in players if player.get("GameKey")})
        odds_ok = odds_state == "ok" and (not slate_games or odds_games >= max(1, int(math.ceil(slate_games * 0.6))))
        odds_label = {
            "not_configured": "No Vegas API key is configured.",
            "no_games": "The odds source returned no posted NFL lines.",
            "invalid_key": "The saved Vegas API key was rejected.",
            "unavailable": "Vegas lines are temporarily unavailable.",
            "error": "The Vegas check failed.",
        }.get(odds_state, f"Vegas lines matched {odds_games}/{slate_games or odds_games} slate games.")
        checks.append(_check(
            "vegas", "Vegas context", "pass" if odds_ok else "review", odds_label,
            "Open Live Data Settings or retry after books post lines." if not odds_ok else "",
            weight=0.75,
        ))

    checks.extend(_portfolio_checks(
        list(generated_lineups or []), sport_u, mode_l, float(salary_cap or 50000.0), preset, dict(sim_report or {})
    ))

    weights = sum(float(item["weight"]) for item in checks) or 1.0
    values = {"pass": 1.0, "review": 0.55, "block": 0.0}
    score = round(sum(float(item["weight"]) * values.get(str(item["status"]), 0.0) for item in checks) / weights * 100.0)
    blocker_count = sum(item["status"] == "block" for item in checks)
    review_count = sum(item["status"] == "review" for item in checks)
    overall = "blocked" if blocker_count else ("review" if review_count else "ready")
    title = {"blocked": "Blocked", "review": "Review", "ready": "Ready"}[overall]
    checks_by_key = {str(item.get("key") or ""): item for item in checks}

    def source_row(name: str, check_key: str, freshness: str) -> Dict[str, str]:
        item = checks_by_key.get(check_key, {})
        confidence = {"pass": "High", "review": "Medium", "block": "Low"}.get(
            str(item.get("status") or "review"), "Medium"
        )
        return {"name": name, "confidence": confidence, "freshness": freshness}

    sources = [
        source_row("Salary file", "salary_file", "Current loaded slate" if players else "Not loaded"),
        source_row("Projections", "projections", "Current loaded values" if players else "Not loaded"),
        source_row("Ownership", "ownership", "Current loaded/simulated values" if players else "Not loaded"),
    ]
    if sport_u == "NFL" and players:
        live_check = checks_by_key.get("live_status", {})
        age_minutes = (live_check.get("details") or {}).get("age_minutes")
        live_freshness = "Not checked" if age_minutes is None else f"{max(0.0, _number(age_minutes)):.0f} minutes old"
        sources.append(source_row("Player news / roles", "live_status", live_freshness))
        sources.append(source_row("Vegas", "vegas", str(live.get("odds_state") or "Not checked")))
    report: Dict[str, Any] = {
        "status": overall,
        "title": title,
        "score": int(score),
        "sport": sport_u,
        "mode": mode_l,
        "players": len(players),
        "eligible_players": len(eligible),
        "preset": str(preset.get("name") or ""),
        "checks": checks,
        "blockers": blocker_count,
        "reviews": review_count,
        "sources": sources,
        "checked_at": current.isoformat().replace("+00:00", "Z"),
    }
    report["text"] = format_readiness_report(report)
    return report


def format_readiness_report(report: Mapping[str, Any]) -> str:
    status = str(report.get("status") or "review").upper()
    lines = [
        f"SLATE READINESS — {status} ({int(_number(report.get('score'), 0.0))}/100)",
        f"{report.get('sport', '')} {str(report.get('mode', '')).title()} • "
        f"{int(_number(report.get('players'), 0.0))} players • "
        f"{int(_number(report.get('eligible_players'), 0.0))} eligible"
        + (f" • {report.get('preset')} preset" if report.get("preset") else ""),
        "",
    ]
    if report.get("sources"):
        lines.append("SOURCE CONFIDENCE")
        for source in report.get("sources") or []:
            lines.append(
                f"[{source.get('confidence', 'Medium').upper()}] "
                f"{source.get('name')}: {source.get('freshness')}"
            )
        lines.append("")
    icons = {"pass": "PASS", "review": "REVIEW", "block": "BLOCK"}
    for item in report.get("checks") or []:
        lines.append(f"[{icons.get(str(item.get('status')), 'REVIEW')}] {item.get('label')}: {item.get('summary')}")
        if item.get("action"):
            lines.append(f"  Next: {item.get('action')}")
    lines.extend([
        "",
        "Readiness is a preflight report, not a projection guarantee. It never changes players or settings.",
    ])
    return "\n".join(lines)
