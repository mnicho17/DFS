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


def _ordered_match_keys(player: Mapping[str, Any]) -> List[str]:
    keys = [
        str(player.get(field) or "").strip().casefold()
        for field in ("FlexID", "CptID", "FlexNamePlusID", "CptNamePlusID")
        if str(player.get(field) or "").strip()
    ]
    name = str(player.get("Name") or "").strip().casefold()
    team = str(player.get("Team") or "").strip().casefold()
    if name:
        if team:
            keys.append(f"name-team:{name}|{team}")
        keys.append(f"name:{name}")
    return list(dict.fromkeys(keys))


def _match_keys(player: Mapping[str, Any]) -> set[str]:
    return set(_ordered_match_keys(player))


def _belongs_to_current_pool(
    player: Mapping[str, Any],
    lookup: Mapping[str, Mapping[str, Any]],
) -> bool:
    id_keys = [
        str(player.get(field) or "").strip().casefold()
        for field in ("FlexID", "CptID", "FlexNamePlusID", "CptNamePlusID")
        if str(player.get(field) or "").strip()
    ]
    keys = id_keys or _ordered_match_keys(player)
    return any(key in lookup for key in keys)


def _player_lookup(players: Sequence[Mapping[str, Any]]) -> Dict[str, Mapping[str, Any]]:
    lookup: Dict[str, Mapping[str, Any]] = {}
    for player in players or []:
        for key in _ordered_match_keys(player):
            lookup.setdefault(key, player)
    return lookup


def _current_player(
    player: Mapping[str, Any],
    lookup: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any]:
    for key in _ordered_match_keys(player):
        if key in lookup:
            return lookup[key]
    return player


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


def _expected_export_row(lineup: Any, kind: str, sport: str) -> List[str]:
    """Return the exact DraftKings IDs required by each exported roster slot."""
    if kind == "showdown" and isinstance(lineup, Mapping):
        captain = lineup.get("Captain")
        captain_id = (
            str(captain.get("CptID") or "").strip()
            if isinstance(captain, Mapping)
            else ""
        )
        flex_ids = [
            str(player.get("FlexID") or "").strip()
            for player in lineup.get("Flex") or []
            if isinstance(player, Mapping)
        ]
        return [captain_id] + flex_ids

    if isinstance(lineup, Sequence) and not isinstance(lineup, (str, bytes)):
        assignment = lineup_slots_for_sport(list(lineup), sport)
        return [
            str(player.get("FlexID") or "").strip()
            if isinstance(player, Mapping)
            else ""
            for _, player in assignment
        ]
    return []


def _check(
    key: str,
    label: str,
    status: str,
    summary: str,
    action: str = "",
    *,
    lineup_indexes: Optional[Sequence[int]] = None,
) -> Dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "status": status,
        "summary": summary,
        "action": action,
        "lineup_indexes": sorted(set(int(value) for value in (lineup_indexes or []) if int(value) >= 0)),
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
    player_pool: Optional[Sequence[Mapping[str, Any]]] = None,
    min_salary_pct: float = 0.94,
) -> Dict[str, Any]:
    """Audit the exact saved portfolio without changing any lineup or setting."""
    kind_l = str(kind or "classic").strip().lower()
    sport_u = str(sport or "NFL").strip().upper()
    selected = list(lineups or [])
    expected = 6 if kind_l == "showdown" else len(get_roster_slots_for_sport(sport_u))
    cap = max(0.0, _number(salary_cap, 50000.0))
    checks: List[Dict[str, Any]] = []
    current_lookup = _player_lookup(list(player_pool or []))

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
    missing_pool_indexes: List[int] = []
    team_diversity_indexes: List[int] = []
    showdown_game_indexes: List[int] = []
    missing_salary_indexes: List[int] = []
    export_id_indexes: List[int] = []
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
        if current_lookup and any(
            not _belongs_to_current_pool(player, current_lookup)
            for player in lineup_players
        ):
            missing_pool_indexes.append(index)

        current_players = [_current_player(player, current_lookup) for player in lineup_players]
        if kind_l == "showdown" and isinstance(lineup, Mapping):
            captain = lineup.get("Captain")
            current_captain = (
                _current_player(captain, current_lookup)
                if isinstance(captain, Mapping)
                else {}
            )
            flex_players = [
                _current_player(player, current_lookup)
                for player in lineup.get("Flex") or []
                if isinstance(player, Mapping)
            ]
            if (
                _number(current_captain.get("CptSalary"), 0.0) <= 0.0
                or any(_number(player.get("FlexSalary"), 0.0) <= 0.0 for player in flex_players)
            ):
                missing_salary_indexes.append(index)
        elif any(_number(player.get("FlexSalary"), 0.0) <= 0.0 for player in current_players):
            missing_salary_indexes.append(index)

        expected_export = _expected_export_row(lineup, kind_l, sport_u)
        if (
            len(expected_export) != expected
            or any(not value for value in expected_export)
            or len(set(expected_export)) != len(expected_export)
        ):
            export_id_indexes.append(index)

        teams = {
            str(player.get("Team") or "").strip().upper()
            for player in current_players
            if str(player.get("Team") or "").strip()
        }
        missing_team = any(not str(player.get("Team") or "").strip() for player in current_players)
        invalid_team_count = len(teams) != 2 if kind_l == "showdown" else len(teams) < 2
        if len(lineup_players) == expected and (missing_team or invalid_team_count):
            team_diversity_indexes.append(index)

        if kind_l == "showdown" and len(lineup_players) == expected:
            games = {
                str(player.get("GameKey") or player.get("GameInfo") or "").strip().upper()
                for player in current_players
                if str(player.get("GameKey") or player.get("GameInfo") or "").strip()
            }
            if len(games) > 1:
                showdown_game_indexes.append(index)

    if export_rows is not None:
        for index, row in enumerate(export_rows, 1):
            if len(row) != expected or any(not str(cell).strip() for cell in row):
                invalid_indexes.append(index)
            cells = [str(cell).strip() for cell in row if str(cell).strip()]
            if len(set(cells)) != len(cells):
                duplicate_player_indexes.append(index)
            expected_row = (
                _expected_export_row(selected[index - 1], kind_l, sport_u)
                if index <= len(selected)
                else []
            )
            if [str(cell).strip() for cell in row] != expected_row:
                export_id_indexes.append(index)
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
        lineup_indexes=[value - 1 for value in invalid_indexes + duplicate_player_indexes],
    ))
    checks.append(_check(
        "export_ids", "DraftKings slot IDs", "block" if export_id_indexes else "pass",
        (f"{len(set(export_id_indexes))} lineup(s) have a missing, repeated, or wrong slot-specific DraftKings ID."
         if export_id_indexes else "Every roster slot uses the correct DraftKings ID, including the Captain ID in Showdown."),
        "Reload the exact salary file and replace the affected lineup before export." if export_id_indexes else "",
        lineup_indexes=[value - 1 for value in export_id_indexes],
    ))
    checks.append(_check(
        "salary_data", "Salary data", "block" if missing_salary_indexes else "pass",
        (f"{len(set(missing_salary_indexes))} lineup(s) contain a roster slot without a valid salary."
         if missing_salary_indexes else "Every roster slot has a valid salary from the current slate."),
        "Reload the exact DraftKings salary file and replace the affected lineup." if missing_salary_indexes else "",
        lineup_indexes=[value - 1 for value in missing_salary_indexes],
    ))
    checks.append(_check(
        "slate_membership", "Current slate membership", "block" if missing_pool_indexes else "pass",
        (f"{len(set(missing_pool_indexes))} lineup(s) contain a player who is not in the currently loaded slate."
         if missing_pool_indexes else "Every rostered player belongs to the currently loaded slate."),
        "Reload the exact DraftKings slate file and replace the affected lineups." if missing_pool_indexes else "",
        lineup_indexes=[value - 1 for value in missing_pool_indexes],
    ))
    checks.append(_check(
        "team_diversity", "Team diversity", "block" if team_diversity_indexes else "pass",
        (f"{len(set(team_diversity_indexes))} lineup(s) fail the requirement to roster athletes from both or multiple teams."
         if team_diversity_indexes else "Every lineup includes valid team diversity for its contest type."),
        "Replace the affected lineup; Classic and Showdown cannot use athletes from only one team." if team_diversity_indexes else "",
        lineup_indexes=[value - 1 for value in team_diversity_indexes],
    ))
    checks.append(_check(
        "showdown_game", "Showdown game", "block" if showdown_game_indexes else "pass",
        (f"{len(set(showdown_game_indexes))} Showdown lineup(s) combine athletes from more than one game."
         if showdown_game_indexes else "Every Showdown lineup is confined to one game."),
        "Reload the exact Showdown slate and replace the affected lineup." if showdown_game_indexes else "",
        lineup_indexes=[value - 1 for value in showdown_game_indexes],
    ))

    salaries = [_salary(lineup, kind_l) for lineup in selected]
    over_cap = [index for index, salary in enumerate(salaries, 1) if salary > cap + 0.01]
    low_floor = cap * max(0.0, min(1.0, _number(min_salary_pct, 0.94)))
    low_salary = [index for index, salary in enumerate(salaries, 1) if salary < low_floor]
    checks.append(_check(
        "salary_cap", "Salary cap", "block" if over_cap else "pass",
        f"{len(over_cap)} lineup(s) exceed the ${cap:,.0f} cap." if over_cap else f"Every lineup is at or below the ${cap:,.0f} cap.",
        "Remove or rebuild every over-cap lineup." if over_cap else "",
        lineup_indexes=[value - 1 for value in over_cap],
    ))
    checks.append(_check(
        "salary_use", "Salary use", "review" if low_salary else "pass",
        f"{len(low_salary)} lineup(s) use less than ${low_floor:,.0f}." if low_salary else f"Every lineup uses at least ${low_floor:,.0f}.",
        "Confirm the unused salary is intentional and creates enough leverage." if low_salary else "",
        lineup_indexes=[value - 1 for value in low_salary],
    ))

    signatures = [_signature(lineup, kind_l) for lineup in selected]
    signature_counts = Counter(signatures)
    duplicate_entries = sum(count - 1 for count in signature_counts.values() if count > 1)
    first_signature_index: Dict[tuple[str, ...], int] = {}
    duplicate_entry_indexes: List[int] = []
    for index, signature in enumerate(signatures):
        if signature in first_signature_index:
            duplicate_entry_indexes.append(index)
        else:
            first_signature_index[signature] = index
    checks.append(_check(
        "duplicates", "Duplicate entries", "block" if duplicate_entries else "pass",
        f"{duplicate_entries} duplicate lineup(s) detected." if duplicate_entries else "No duplicate lineups detected.",
        "Remove duplicate saved lineups before export." if duplicate_entries else "",
        lineup_indexes=duplicate_entry_indexes,
    ))

    unavailable_names: List[str] = []
    uncertain_names: List[str] = []
    unavailable_indexes: List[int] = []
    uncertain_indexes: List[int] = []
    for index, lineup in enumerate(selected):
        for player in _players(lineup, kind_l):
            current = _current_player(player, current_lookup)
            name = str(current.get("Name") or player.get("Name") or _identity(player) or "Unknown")
            player_status = _status(current)
            if player_status in UNAVAILABLE_STATUSES or bool(current.get("LiveStatusConflict")):
                unavailable_names.append(name)
                unavailable_indexes.append(index)
            elif player_status in REVIEW_STATUSES:
                uncertain_names.append(name)
                uncertain_indexes.append(index)
    unavailable_names = list(dict.fromkeys(unavailable_names))
    uncertain_names = list(dict.fromkeys(uncertain_names))
    checks.append(_check(
        "availability", "Player availability", "block" if unavailable_names else "pass",
        (f"Unavailable player(s) appear in saved lineups: {', '.join(unavailable_names[:8])}."
         if unavailable_names else "No saved lineup contains a player marked unavailable."),
        "Remove the affected lineup or replace the unavailable player." if unavailable_names else "",
        lineup_indexes=unavailable_indexes,
    ))
    checks.append(_check(
        "uncertain", "Questionable players", "review" if uncertain_names else "pass",
        (f"Questionable or doubtful player(s) are still used: {', '.join(uncertain_names[:8])}."
         if uncertain_names else "No saved lineup contains a questionable or doubtful player."),
        "Check the latest news and confirm each risk is intentional." if uncertain_names else "",
        lineup_indexes=uncertain_indexes,
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
    blocked_lineup_indexes = sorted({
        int(index)
        for check in checks
        if check.get("status") == "block"
        for index in check.get("lineup_indexes") or []
    })
    review_lineup_indexes = sorted({
        int(index)
        for check in checks
        if check.get("status") == "review"
        for index in check.get("lineup_indexes") or []
    })
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
        "blocked_lineup_indexes": blocked_lineup_indexes,
        "review_lineup_indexes": review_lineup_indexes,
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
