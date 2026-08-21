from __future__ import annotations

"""Final, report-only checks for the exact lineups about to be exported."""

from collections import Counter
from typing import Any, Dict, List, Mapping, Optional, Sequence

from optimizers import get_roster_slots_for_sport, lineup_slots_for_sport


UNAVAILABLE_STATUSES = {
    "OUT", "O", "IR", "PUP", "NFI", "SUSP", "SUSPENDED", "INACTIVE",
    "PRACTICE SQUAD",
}
REVIEW_STATUSES = {
    "Q", "QUESTIONABLE", "D", "DOUBTFUL", "GTD", "GAME TIME DECISION",
}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _identity(player: Mapping[str, Any]) -> str:
    return str(
        player.get("FlexID")
        or player.get("FlexNamePlusID")
        or player.get("Name")
        or ""
    ).strip()


def _status(player: Mapping[str, Any]) -> str:
    return str(
        player.get("NFLAvailability")
        or player.get("InjuryStatus")
        or player.get("Status")
        or ""
    ).strip().upper()


def _players(lineup: Any, kind: str) -> List[Mapping[str, Any]]:
    if kind == "showdown" and isinstance(lineup, Mapping):
        values = [lineup.get("Captain")] + list(lineup.get("Flex") or [])
        return [value for value in values if isinstance(value, Mapping)]
    if isinstance(lineup, Sequence) and not isinstance(lineup, (str, bytes)):
        return [value for value in lineup if isinstance(value, Mapping)]
    return []


def _salary(lineup: Any, kind: str) -> float:
    if kind == "showdown" and isinstance(lineup, Mapping):
        captain = lineup.get("Captain")
        captain_salary = (
            _number(captain.get("CptSalary"), 1.5 * _number(captain.get("FlexSalary"), 0.0))
            if isinstance(captain, Mapping)
            else 0.0
        )
        return captain_salary + sum(
            _number(player.get("FlexSalary"), 0.0)
            for player in lineup.get("Flex") or []
            if isinstance(player, Mapping)
        )
    return sum(_number(player.get("FlexSalary"), 0.0) for player in _players(lineup, kind))


def _signature(lineup: Any, kind: str) -> tuple[str, ...]:
    if kind == "showdown" and isinstance(lineup, Mapping):
        captain = lineup.get("Captain")
        captain_id = _identity(captain) if isinstance(captain, Mapping) else ""
        flex_ids = sorted(
            _identity(player)
            for player in lineup.get("Flex") or []
            if isinstance(player, Mapping)
        )
        return tuple([f"CPT:{captain_id}"] + [f"FLEX:{value}" for value in flex_ids])
    return tuple(sorted(_identity(player) for player in _players(lineup, kind)))


def _check(key: str, label: str, status: str, summary: str, action: str = "") -> Dict[str, str]:
    return {
        "key": key,
        "label": label,
        "status": status,
        "summary": summary,
        "action": action,
    }


def build_entry_safety_report(
    lineups: Sequence[Any],
    *,
    kind: str,
    sport: str,
    salary_cap: float,
    export_rows: Optional[Sequence[Sequence[Any]]] = None,
    portfolio_report: Optional[Mapping[str, Any]] = None,
    readiness_report: Optional[Mapping[str, Any]] = None,
    min_salary_pct: float = 0.94,
) -> Dict[str, Any]:
    """Audit the exact saved portfolio without changing any lineup or setting."""
    kind_l = str(kind or "classic").strip().lower()
    sport_u = str(sport or "NFL").strip().upper()
    selected = list(lineups or [])
    expected = 6 if kind_l == "showdown" else len(get_roster_slots_for_sport(sport_u))
    cap = max(0.0, _number(salary_cap, 50000.0))
    checks: List[Dict[str, str]] = []

    if not selected:
        checks.append(_check(
            "saved_lineups", "Saved lineups", "block", "No saved lineups are available to export.",
            "Save at least one generated lineup.",
        ))
    else:
        checks.append(_check(
            "saved_lineups", "Saved lineups", "pass", f"{len(selected)} saved lineup(s) selected for export."
        ))

    invalid_indexes: List[int] = []
    duplicate_player_indexes: List[int] = []
    for index, lineup in enumerate(selected, 1):
        lineup_players = _players(lineup, kind_l)
        identities = [_identity(player) for player in lineup_players]
        if len(lineup_players) != expected or any(not value for value in identities):
            invalid_indexes.append(index)
        if len(set(identities)) != len(identities):
            duplicate_player_indexes.append(index)
        if kind_l == "classic" and len(lineup_players) == expected:
            assignment = lineup_slots_for_sport(list(lineup_players), sport_u)
            if not assignment or any(player is None for _, player in assignment):
                invalid_indexes.append(index)

    if export_rows is not None:
        for index, row in enumerate(export_rows, 1):
            if len(row) != expected or any(not str(cell).strip() for cell in row):
                invalid_indexes.append(index)
    invalid_indexes = sorted(set(invalid_indexes))
    duplicate_player_indexes = sorted(set(duplicate_player_indexes))
    roster_blocked = bool(invalid_indexes or duplicate_player_indexes)
    roster_bits = []
    if invalid_indexes:
        roster_bits.append(f"{len(invalid_indexes)} incomplete or position-invalid")
    if duplicate_player_indexes:
        roster_bits.append(f"{len(duplicate_player_indexes)} with a player repeated")
    checks.append(_check(
        "rosters", "Roster validity", "block" if roster_blocked else "pass",
        "; ".join(roster_bits) + "." if roster_bits else f"All {len(selected)} roster(s) have {expected} unique, valid players and export IDs.",
        "Reload the salary file and rebuild the affected saved lineups." if roster_blocked else "",
    ))

    salaries = [_salary(lineup, kind_l) for lineup in selected]
    over_cap = [index for index, salary in enumerate(salaries, 1) if salary > cap + 0.01]
    low_floor = cap * max(0.0, min(1.0, _number(min_salary_pct, 0.94)))
    low_salary = [index for index, salary in enumerate(salaries, 1) if salary < low_floor]
    checks.append(_check(
        "salary_cap", "Salary cap", "block" if over_cap else "pass",
        f"{len(over_cap)} lineup(s) exceed the ${cap:,.0f} cap." if over_cap else f"Every lineup is at or below the ${cap:,.0f} cap.",
        "Remove or rebuild every over-cap lineup." if over_cap else "",
    ))
    checks.append(_check(
        "salary_use", "Salary use", "review" if low_salary else "pass",
        f"{len(low_salary)} lineup(s) use less than ${low_floor:,.0f}." if low_salary else f"Every lineup uses at least ${low_floor:,.0f}.",
        "Confirm the unused salary is intentional and creates enough leverage." if low_salary else "",
    ))

    signatures = [_signature(lineup, kind_l) for lineup in selected]
    signature_counts = Counter(signatures)
    duplicate_entries = sum(count - 1 for count in signature_counts.values() if count > 1)
    checks.append(_check(
        "duplicates", "Duplicate entries", "block" if duplicate_entries else "pass",
        f"{duplicate_entries} duplicate lineup(s) detected." if duplicate_entries else "No duplicate lineups detected.",
        "Remove duplicate saved lineups before export." if duplicate_entries else "",
    ))

    unavailable_names: List[str] = []
    uncertain_names: List[str] = []
    for lineup in selected:
        for player in _players(lineup, kind_l):
            name = str(player.get("Name") or _identity(player) or "Unknown")
            player_status = _status(player)
            if player_status in UNAVAILABLE_STATUSES or bool(player.get("LiveStatusConflict")):
                unavailable_names.append(name)
            elif player_status in REVIEW_STATUSES:
                uncertain_names.append(name)
    unavailable_names = list(dict.fromkeys(unavailable_names))
    uncertain_names = list(dict.fromkeys(uncertain_names))
    checks.append(_check(
        "availability", "Player availability", "block" if unavailable_names else "pass",
        (f"Unavailable player(s) appear in saved lineups: {', '.join(unavailable_names[:8])}."
         if unavailable_names else "No saved lineup contains a player marked unavailable."),
        "Remove the affected lineup or replace the unavailable player." if unavailable_names else "",
    ))
    checks.append(_check(
        "uncertain", "Questionable players", "review" if uncertain_names else "pass",
        (f"Questionable or doubtful player(s) are still used: {', '.join(uncertain_names[:8])}."
         if uncertain_names else "No saved lineup contains a questionable or doubtful player."),
        "Check the latest news and confirm each risk is intentional." if uncertain_names else "",
    ))

    portfolio_warnings = list((portfolio_report or {}).get("warnings") or [])
    checks.append(_check(
        "portfolio_rules", "Portfolio rules", "block" if portfolio_warnings else "pass",
        (f"{len(portfolio_warnings)} saved-portfolio rule violation(s): {portfolio_warnings[0]}"
         if portfolio_warnings else "The saved portfolio satisfies the current exposure, group, concentration, and uniqueness rules."),
        "Repair the saved portfolio or intentionally change the current rule before export." if portfolio_warnings else "",
    ))

    readiness_items = [
        item for item in (readiness_report or {}).get("checks") or []
        if str(item.get("status") or "pass") != "pass"
        and str(item.get("key") or "") not in {"portfolio", "preset_fit", "locks"}
    ]
    checks.append(_check(
        "data_review", "Slate data", "review" if readiness_items else "pass",
        (f"{len(readiness_items)} slate-data item(s) need review: "
         + "; ".join(str(item.get("label") or "Check") for item in readiness_items[:4]) + "."
         if readiness_items else "Slate data has no unresolved readiness findings."),
        "Open Slate Readiness and review the affected data before lock." if readiness_items else "",
    ))

    blockers = sum(check["status"] == "block" for check in checks)
    reviews = sum(check["status"] == "review" for check in checks)
    status = "blocked" if blockers else ("review" if reviews else "ready")
    title = {"blocked": "Blocked", "review": "Review Before Export", "ready": "Ready to Export"}[status]
    report: Dict[str, Any] = {
        "status": status,
        "title": title,
        "sport": sport_u,
        "kind": kind_l,
        "lineup_count": len(selected),
        "blockers": blockers,
        "reviews": reviews,
        "checks": checks,
    }
    report["text"] = format_entry_safety_report(report)
    return report


def format_entry_safety_report(report: Mapping[str, Any]) -> str:
    lines = [
        f"ENTRY SAFETY — {str(report.get('status') or 'review').upper()}",
        f"{report.get('sport', '')} {str(report.get('kind', '')).title()} • "
        f"{int(report.get('lineup_count', 0) or 0)} saved lineup(s)",
        "",
    ]
    for check in report.get("checks") or []:
        lines.append(f"[{str(check.get('status') or 'review').upper()}] {check.get('label')}: {check.get('summary')}")
        if check.get("action"):
            lines.append(f"  Next: {check.get('action')}")
    lines.extend(["", "Entry Safety is report-only. It never changes a lineup or setting."])
    return "\n".join(lines)
