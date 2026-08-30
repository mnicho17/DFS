from __future__ import annotations

"""Report-only game-day change analysis for an exact saved portfolio."""

from typing import Any, Dict, List, Mapping, Sequence


UNAVAILABLE_STATUSES = {
    "OUT", "O", "IR", "PUP", "NFI", "SUSP", "SUSPENDED", "INACTIVE",
    "PRACTICE SQUAD",
}


def _players(lineup: Any, kind: str) -> List[Mapping[str, Any]]:
    if str(kind or "classic").lower() == "showdown" and isinstance(lineup, Mapping):
        values = [lineup.get("Captain")] + list(lineup.get("Flex") or [])
        return [value for value in values if isinstance(value, Mapping)]
    if isinstance(lineup, Sequence) and not isinstance(lineup, (str, bytes)):
        return [value for value in lineup if isinstance(value, Mapping)]
    return []


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


def _lookup(players: Sequence[Mapping[str, Any]]) -> Dict[str, Mapping[str, Any]]:
    result: Dict[str, Mapping[str, Any]] = {}
    for player in players or []:
        for key in _ordered_match_keys(player):
            result.setdefault(key, player)
    return result


def _current_player(player: Mapping[str, Any], lookup: Mapping[str, Mapping[str, Any]]) -> Mapping[str, Any]:
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


def _snapshot_text(value: Any) -> str:
    if not isinstance(value, (list, tuple)):
        return str(value or "No prior status")
    parts: List[str] = []
    if len(value) > 0 and str(value[0] or "").strip():
        parts.append(str(value[0]).strip())
    if len(value) > 1 and str(value[1] or "").strip():
        parts.append(str(value[1]).strip())
    if len(value) > 2:
        try:
            depth = int(value[2] or 0)
        except (TypeError, ValueError):
            depth = 0
        if depth:
            parts.append(f"depth {depth}")
    if len(value) > 3 and str(value[3] or "").strip():
        parts.append(str(value[3]).strip())
    return " • ".join(parts) or "No designation"


def build_final_lock_report(
    lineups: Sequence[Any],
    *,
    kind: str,
    player_pool: Sequence[Mapping[str, Any]],
    live_summary: Mapping[str, Any],
    used_cached_check: bool = False,
) -> Dict[str, Any]:
    """Map live NFL changes and current unavailable players to saved lineups."""
    selected = list(lineups or [])
    current_lookup = _lookup(player_pool)
    lineup_key_sets = [
        set().union(*(_match_keys(player) for player in _players(lineup, kind)))
        if _players(lineup, kind)
        else set()
        for lineup in selected
    ]

    change_rows: List[Dict[str, Any]] = []
    changed_indexes: set[int] = set()
    for change in live_summary.get("changes") or []:
        change_keys: set[str] = set()
        player_key = str(change.get("player_key") or "").strip().casefold()
        if player_key:
            change_keys.add(player_key)
        name = str(change.get("name") or "").strip().casefold()
        team = str(change.get("team") or "").strip().casefold()
        if name:
            if team:
                change_keys.add(f"name-team:{name}|{team}")
            else:
                change_keys.add(f"name:{name}")
        lineup_numbers = [
            index + 1
            for index, lineup_keys in enumerate(lineup_key_sets)
            if lineup_keys.intersection(change_keys)
        ]
        changed_indexes.update(number - 1 for number in lineup_numbers)
        change_rows.append({
            "name": str(change.get("name") or "Unknown"),
            "team": str(change.get("team") or ""),
            "change": f"{_snapshot_text(change.get('before'))} → {_snapshot_text(change.get('after'))}",
            "availability": str(change.get("availability") or "Updated"),
            "lineup_numbers": lineup_numbers,
        })

    unavailable_indexes: set[int] = set()
    unavailable_names: List[str] = []
    for index, lineup in enumerate(selected):
        for player in _players(lineup, kind):
            current = _current_player(player, current_lookup)
            if _status(current) in UNAVAILABLE_STATUSES or bool(current.get("LiveStatusConflict")):
                unavailable_indexes.add(index)
                unavailable_names.append(str(current.get("Name") or player.get("Name") or "Unknown"))

    affected_indexes = sorted(changed_indexes | unavailable_indexes)
    unavailable_names = list(dict.fromkeys(unavailable_names))
    sleeper_ok = str(live_summary.get("sleeper_state") or "unavailable") == "ok"
    status = "unavailable" if not sleeper_ok else ("attention" if affected_indexes else "ready")
    title = {
        "ready": "Final Lock Check Passed",
        "attention": "Saved Lineups Need Review",
        "unavailable": "Live Status Could Not Be Confirmed",
    }[status]
    report: Dict[str, Any] = {
        "status": status,
        "title": title,
        "checked_at": str(live_summary.get("checked_at") or ""),
        "used_cached_check": bool(used_cached_check),
        "sleeper_matches": int(live_summary.get("sleeper", 0) or 0),
        "player_count": int(live_summary.get("total", len(player_pool)) or 0),
        "changes": change_rows,
        "affected_indexes": affected_indexes,
        "affected_lineups": len(affected_indexes),
        "unavailable_indexes": sorted(unavailable_indexes),
        "unavailable_players": unavailable_names,
        "lineup_count": len(selected),
    }
    report["text"] = format_final_lock_report(report)
    return report


def format_final_lock_report(report: Mapping[str, Any]) -> str:
    lines = [
        f"FINAL LOCK CHECK — {str(report.get('status') or 'attention').upper()}",
        f"{int(report.get('affected_lineups', 0) or 0)}/{int(report.get('lineup_count', 0) or 0)} saved lineup(s) affected",
        "",
    ]
    changes = list(report.get("changes") or [])
    if changes:
        lines.append("Changes since the prior live check:")
        for row in changes:
            lineup_text = ", ".join(str(value) for value in row.get("lineup_numbers") or []) or "none"
            lines.append(f"- {row.get('name')} ({row.get('team')}): {row.get('change')} | lineups {lineup_text}")
    else:
        lines.append("No player-status changes were returned by the final refresh.")
    if report.get("unavailable_players"):
        lines.append("Unavailable players still rostered: " + ", ".join(report.get("unavailable_players") or []))
    if str(report.get("status")) == "unavailable":
        lines.append("Current player status could not be confirmed; cached lineup data remains available for manual review.")
    lines.extend(["", "The final lock check never changes a saved lineup automatically."])
    return "\n".join(lines)

