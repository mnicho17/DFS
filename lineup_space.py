from __future__ import annotations

"""Fast, structural lineup-space estimates for the workspace dashboard.

The counts deliberately ignore salary, correlation, exposure, and uniqueness
rules.  They are useful as an immediate measure of how much the player pool has
shrunk, without pretending to predict how many optimizer-valid lineups exist.
"""

import math
from collections import Counter
from typing import Any, Dict, Mapping, Optional, Sequence


INACTIVE_STATUSES = {"OUT", "O", "IR", "PUP", "NFI", "SUSP", "SUSPENDED", "INACTIVE"}


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


def _position_tokens(player: Mapping[str, Any]) -> list[str]:
    raw = str(player.get("Position") or "").strip().upper().replace("D/ST", "DST")
    return [token.strip() for token in raw.replace("/", ",").split(",") if token.strip()]


def _base_eligible(player: Mapping[str, Any]) -> bool:
    return (
        _number(player.get("FlexSalary"), 0.0) > 0
        and not bool(player.get("FadeFlex"))
        and _status(player) not in INACTIVE_STATUSES
    )


def _choose(total: int, selected: int) -> int:
    if total < 0 or selected < 0 or selected > total:
        return 0
    return math.comb(total, selected)


def format_compact_count(value: int) -> str:
    """Return a short human-readable integer without losing the full value."""
    count = max(0, int(value or 0))
    for threshold, suffix in (
        (10**15, "Q"),
        (10**12, "T"),
        (10**9, "B"),
        (10**6, "M"),
        (10**3, "K"),
    ):
        if count >= threshold:
            scaled = count / threshold
            digits = 0 if scaled >= 100 else (1 if scaled >= 10 else 2)
            rendered = f"{scaled:.{digits}f}"
            if "." in rendered:
                rendered = rendered.rstrip("0").rstrip(".")
            return rendered + suffix
    return f"{count:,}"


def _nfl_classic_count(players: Sequence[Mapping[str, Any]]) -> tuple[int, bool, str]:
    usable = {"QB", "RB", "WR", "TE", "DST"}
    multi_position = any(
        len([token for token in _position_tokens(player) if token in usable]) != 1
        for player in players
    )
    if multi_position:
        locked = sum(bool(player.get("LockFlex")) for player in players)
        return (
            _choose(len(players) - locked, 9 - locked),
            False,
            "Upper bound because at least one NFL player has multi-position eligibility.",
        )

    counts = Counter(_position_tokens(player)[0] for player in players)
    locked_counts = Counter(
        _position_tokens(player)[0] for player in players if bool(player.get("LockFlex"))
    )
    locked_total = sum(locked_counts.values())
    if locked_total > 9:
        return 0, True, "Too many players are locked for a nine-player NFL roster."

    shapes = (
        {"QB": 1, "RB": 3, "WR": 3, "TE": 1, "DST": 1},
        {"QB": 1, "RB": 2, "WR": 4, "TE": 1, "DST": 1},
        {"QB": 1, "RB": 2, "WR": 3, "TE": 2, "DST": 1},
    )
    total = 0
    for shape in shapes:
        ways = 1
        for position in usable:
            required = shape[position] - locked_counts[position]
            unlocked = counts[position] - locked_counts[position]
            ways *= _choose(unlocked, required)
            if not ways:
                break
        total += ways
    return total, True, "Exact NFL roster-shape count before salary and strategy rules."


def _showdown_count(players: Sequence[Mapping[str, Any]]) -> tuple[int, bool, str]:
    flex_players = list(players)
    flex_locks = [player for player in flex_players if bool(player.get("LockFlex"))]
    captain_locks = [
        player for player in flex_players
        if bool(player.get("LockCpt"))
        and not bool(player.get("FadeCpt"))
        and _number(player.get("CptSalary"), 0.0) > 0
    ]
    if len(captain_locks) > 1 or len(flex_locks) > 5:
        return 0, True, "Captain or FLEX locks exceed the Showdown roster limits."

    captain_candidates = [
        player for player in flex_players
        if not bool(player.get("FadeCpt")) and _number(player.get("CptSalary"), 0.0) > 0
    ]
    if captain_locks:
        captain_candidates = captain_locks

    total = 0
    for captain in captain_candidates:
        if captain in flex_locks:
            continue
        unlocked_flex = [
            player for player in flex_players
            if player is not captain and player not in flex_locks
        ]
        total += _choose(len(unlocked_flex), 5 - len(flex_locks))
    return total, False, "Structural CPT/FLEX count before salary and the two-team rule."


def calculate_lineup_space(
    players: Sequence[Mapping[str, Any]],
    *,
    sport: str = "NFL",
    mode: str = "classic",
    requested: int = 0,
    loaded_total: Optional[int] = None,
    pool_label: str = "eligible",
) -> Dict[str, Any]:
    """Return a compact lineup-space report for the current build pool."""
    sport_u = str(sport or "NFL").strip().upper()
    mode_l = str(mode or "classic").strip().lower()
    loaded = max(len(players), int(loaded_total if loaded_total is not None else len(players)))

    if mode_l == "showdown":
        eligible = [player for player in players if _base_eligible(player)]
        count, exact, explanation = _showdown_count(eligible)
        roster_size = 6
    elif sport_u == "NFL":
        eligible = [
            player for player in players
            if _base_eligible(player)
            and any(token in {"QB", "RB", "WR", "TE", "DST"} for token in _position_tokens(player))
        ]
        unavailable_locks = [
            player for player in players
            if bool(player.get("LockFlex")) and player not in eligible
        ]
        if unavailable_locks:
            count, exact = 0, True
            explanation = "A locked player is faded, inactive, or not eligible for an NFL roster slot."
        else:
            count, exact, explanation = _nfl_classic_count(eligible)
        roster_size = 9
    else:
        eligible = [player for player in players if _base_eligible(player)]
        locked = sum(bool(player.get("LockFlex")) for player in eligible)
        roster_size = {"MLB": 10, "NBA": 8, "WNBA": 6}.get(sport_u, 6)
        count = _choose(len(eligible) - locked, roster_size - locked)
        exact = False
        explanation = "Upper bound before position assignment, salary, and strategy rules."

    locked_count = sum(bool(player.get("LockFlex")) for player in eligible)
    return {
        "sport": sport_u,
        "mode": mode_l,
        "loaded": loaded,
        "eligible": len(eligible),
        "omitted": max(0, loaded - len(eligible)),
        "locked": locked_count,
        "roster_size": roster_size,
        "structural_combinations": int(count),
        "compact_combinations": format_compact_count(int(count)),
        "exact": bool(exact),
        "requested": max(0, int(requested or 0)),
        "pool_label": str(pool_label or "eligible"),
        "explanation": explanation,
    }
