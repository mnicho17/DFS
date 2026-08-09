from __future__ import annotations

import math
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


def player_key(player: Dict[str, Any]) -> str:
    return (
        str(player.get("FlexNamePlusID") or "").strip()
        or str(player.get("FlexID") or "").strip()
        or str(player.get("Name") or "").strip()
    )


def _pct(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value in (None, ""):
        return default
    try:
        return max(0.0, min(100.0, float(value)))
    except Exception:
        return default


def _min_count(value: Any, total: int) -> int:
    pct = _pct(value, 0.0) or 0.0
    if pct <= 0.0:
        return 0
    return max(1, int(math.ceil((pct / 100.0) * max(1, total) - 1e-9)))


def _max_count(value: Any, total: int) -> Optional[int]:
    pct = _pct(value)
    if pct is None:
        return None
    if pct <= 0.0:
        return 0
    return max(1, int(math.floor((pct / 100.0) * max(1, total) + 1e-9)))


def normalize_rules(rules: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    raw = dict(rules or {})
    groups = []
    for item in raw.get("groups") or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "").strip().lower()
        if kind not in ("at_least_one", "never_together"):
            continue
        keys = sorted({str(key).strip() for key in item.get("player_keys") or [] if str(key).strip()})
        if not keys:
            continue
        groups.append({
            "type": kind,
            "player_keys": keys,
            "label": str(item.get("label") or "").strip(),
        })
    constraints: Dict[str, Dict[str, Any]] = {}
    for key, item in (raw.get("player_constraints") or {}).items():
        stable_key = str(key or "").strip()
        if not stable_key or not isinstance(item, dict):
            continue
        constraints[stable_key] = {
            "Name": str(item.get("Name") or stable_key),
            "FlexNamePlusID": stable_key,
            "MinPct": item.get("MinPct"),
            "MaxPct": item.get("MaxPct"),
            "MinCptPct": item.get("MinCptPct"),
            "MaxCptPct": item.get("MaxCptPct"),
        }
    return {
        "min_unique": max(1, min(8, int(raw.get("min_unique", 1) or 1))),
        "max_team_pct": _pct(raw.get("max_team_pct"), 100.0) or 100.0,
        "max_game_pct": _pct(raw.get("max_game_pct"), 100.0) or 100.0,
        "balance_ownership": bool(raw.get("balance_ownership", True)),
        "groups": groups,
        "player_constraints": constraints,
    }


def lineup_players(lineup: Any, kind: str) -> List[Dict[str, Any]]:
    if str(kind or "classic").lower() == "showdown":
        captain = (lineup or {}).get("Captain") or {}
        flex = list((lineup or {}).get("Flex") or [])
        return ([captain] if captain else []) + flex
    return list(lineup or [])


def lineup_captain(lineup: Any, kind: str) -> Optional[Dict[str, Any]]:
    if str(kind or "classic").lower() != "showdown":
        return None
    return (lineup or {}).get("Captain") or None


def _team(player: Dict[str, Any]) -> str:
    return str(player.get("Team") or "").strip().upper()


def _game(player: Dict[str, Any]) -> str:
    raw = str(player.get("GameKey") or player.get("GameInfo") or "").strip().upper()
    if raw:
        return raw
    team = _team(player)
    opponent = str(player.get("Opponent") or "").strip().upper()
    return "@".join(sorted([team, opponent])) if team and opponent else ""


def _projection(lineup: Any, kind: str) -> float:
    if str(kind or "classic").lower() == "showdown":
        captain = lineup_captain(lineup, kind) or {}
        return float(captain.get("CptProjection", 0.0) or 0.0) + sum(
            float(player.get("FlexProjection", 0.0) or 0.0)
            for player in (lineup or {}).get("Flex") or []
        )
    return sum(float(player.get("FlexProjection", 0.0) or 0.0) for player in lineup_players(lineup, kind))


def _ownership(lineup: Any, kind: str) -> float:
    players = lineup_players(lineup, kind)
    if not players:
        return 0.0
    values = []
    captain = lineup_captain(lineup, kind)
    for player in players:
        if captain is player:
            values.append(float(player.get("ProjCptOwnPct", player.get("ProjOwnPct", 0.0)) or 0.0))
        else:
            values.append(float(player.get("ProjOwnPct", player.get("ProjFlexOwnPct", 0.0)) or 0.0))
    return sum(values) / len(values)


def _candidate_signature(lineup: Any, kind: str) -> Tuple[str, ...]:
    keys = sorted(player_key(player) for player in lineup_players(lineup, kind) if player_key(player))
    captain = lineup_captain(lineup, kind)
    return tuple(keys + ([f"CPT:{player_key(captain)}"] if captain else []))


def _group_ok(keys: set[str], groups: Sequence[Dict[str, Any]]) -> bool:
    for group in groups:
        overlap = len(keys.intersection(group["player_keys"]))
        if group["type"] == "at_least_one" and overlap < 1:
            return False
        if group["type"] == "never_together" and overlap > 1:
            return False
    return True


def _lineup_sets(lineup: Any, kind: str) -> Tuple[set[str], set[str], set[str]]:
    players = lineup_players(lineup, kind)
    return (
        {player_key(player) for player in players if player_key(player)},
        {_team(player) for player in players if _team(player)},
        {_game(player) for player in players if _game(player)},
    )


def select_portfolio(
    candidates: Iterable[Any],
    requested: int,
    *,
    rules: Optional[Dict[str, Any]] = None,
    kind: str = "classic",
) -> Dict[str, Any]:
    """Choose a deterministic, constraint-aware portfolio from generated candidates.

    Maximums and player groups remain hard rules. Minimum exposure is prioritized
    during selection and reported as a shortfall when the candidate pool cannot
    satisfy it. Lineup uniqueness relaxes one player at a time only when needed so
    an aggressive setting cannot freeze or crash a large build.
    """
    requested = max(1, int(requested or 1))
    normalized = normalize_rules(rules)
    kind = str(kind or "classic").lower()

    unique_candidates: Dict[Tuple[str, ...], Any] = {}
    for lineup in candidates or []:
        signature = _candidate_signature(lineup, kind)
        if signature and signature not in unique_candidates:
            unique_candidates[signature] = lineup
    pool = list(unique_candidates.values())

    player_lookup: Dict[str, Dict[str, Any]] = {
        key: dict(value) for key, value in normalized["player_constraints"].items()
    }
    for lineup in pool:
        for player in lineup_players(lineup, kind):
            key = player_key(player)
            if key:
                if key in player_lookup:
                    merged = dict(player)
                    merged.update({
                        field: value
                        for field, value in player_lookup[key].items()
                        if value not in (None, "")
                    })
                    player_lookup[key] = merged
                else:
                    player_lookup[key] = player

    min_total = {key: _min_count(player.get("MinPct"), requested) for key, player in player_lookup.items()}
    max_total = {key: _max_count(player.get("MaxPct"), requested) for key, player in player_lookup.items()}
    min_cpt = {key: _min_count(player.get("MinCptPct"), requested) for key, player in player_lookup.items()}
    max_cpt = {key: _max_count(player.get("MaxCptPct"), requested) for key, player in player_lookup.items()}
    max_team = _max_count(normalized["max_team_pct"], requested)
    max_game = _max_count(normalized["max_game_pct"], requested)

    selected: List[Any] = []
    selected_key_sets: List[set[str]] = []
    total_counts: Counter[str] = Counter()
    cpt_counts: Counter[str] = Counter()
    team_counts: Counter[str] = Counter()
    game_counts: Counter[str] = Counter()
    warnings: List[str] = []
    current_min_unique = normalized["min_unique"]

    def admissible(lineup: Any, min_unique: int) -> bool:
        keys, teams, games = _lineup_sets(lineup, kind)
        if not keys or not _group_ok(keys, normalized["groups"]):
            return False
        for previous in selected_key_sets:
            if len(keys - previous) < min_unique:
                return False
        for key in keys:
            limit = max_total.get(key)
            if limit is not None and total_counts[key] >= limit:
                return False
        captain = lineup_captain(lineup, kind)
        captain_key = player_key(captain) if captain else ""
        if captain_key:
            limit = max_cpt.get(captain_key)
            if limit is not None and cpt_counts[captain_key] >= limit:
                return False
        if max_team is not None and any(team_counts[team] >= max_team for team in teams):
            return False
        if max_game is not None and any(game_counts[game] >= max_game for game in games):
            return False
        return True

    def score(lineup: Any) -> float:
        keys, teams, games = _lineup_sets(lineup, kind)
        captain = lineup_captain(lineup, kind)
        captain_key = player_key(captain) if captain else ""
        deficit_bonus = sum(500.0 for key in keys if total_counts[key] < min_total.get(key, 0))
        if captain_key and cpt_counts[captain_key] < min_cpt.get(captain_key, 0):
            deficit_bonus += 650.0
        concentration_penalty = sum(total_counts[key] for key in keys) * 0.08
        team_penalty = sum(team_counts[team] for team in teams) * 0.04
        game_penalty = sum(game_counts[game] for game in games) * 0.03
        ownership_penalty = _ownership(lineup, kind) * 0.018 if normalized["balance_ownership"] else 0.0
        overlap_penalty = max((len(keys.intersection(previous)) for previous in selected_key_sets), default=0) * 0.10
        return (
            _projection(lineup, kind)
            + deficit_bonus
            - concentration_penalty
            - team_penalty
            - game_penalty
            - ownership_penalty
            - overlap_penalty
        )

    remaining = list(pool)
    while len(selected) < requested and remaining:
        eligible = [lineup for lineup in remaining if admissible(lineup, current_min_unique)]
        if not eligible:
            if current_min_unique > 1:
                current_min_unique -= 1
                warnings.append(f"Minimum unique players relaxed to {current_min_unique} to finish the portfolio.")
                continue
            break
        chosen = max(eligible, key=lambda lineup: (score(lineup), _candidate_signature(lineup, kind)))
        remaining.remove(chosen)
        keys, teams, games = _lineup_sets(chosen, kind)
        selected.append(chosen)
        selected_key_sets.append(keys)
        total_counts.update(keys)
        team_counts.update(teams)
        game_counts.update(games)
        captain = lineup_captain(chosen, kind)
        if captain:
            cpt_counts[player_key(captain)] += 1

    if len(selected) < requested:
        warnings.append(
            f"Built {len(selected)} of {requested} requested lineups; hard exposure, group, team, or game limits exhausted the feasible candidate pool."
        )

    report = portfolio_report(selected, normalized, kind=kind, requested=requested)
    for warning in warnings:
        if warning not in report["warnings"]:
            report["warnings"].append(warning)
    report["text"] = _report_text(report)
    return {"lineups": selected, "report": report, "candidate_count": len(pool)}


def portfolio_report(
    lineups: Sequence[Any],
    rules: Optional[Dict[str, Any]] = None,
    *,
    kind: str = "classic",
    requested: Optional[int] = None,
) -> Dict[str, Any]:
    normalized = normalize_rules(rules)
    kind = str(kind or "classic").lower()
    total = len(lineups or [])
    target = max(1, int(requested or total or 1))
    total_counts: Counter[str] = Counter()
    cpt_counts: Counter[str] = Counter()
    team_counts: Counter[str] = Counter()
    game_counts: Counter[str] = Counter()
    names: Dict[str, str] = {}
    key_sets: List[set[str]] = []
    players_by_key: Dict[str, Dict[str, Any]] = {
        key: dict(value) for key, value in normalized["player_constraints"].items()
    }
    group_violations = 0

    for lineup in lineups or []:
        keys, teams, games = _lineup_sets(lineup, kind)
        key_sets.append(keys)
        total_counts.update(keys)
        team_counts.update(teams)
        game_counts.update(games)
        if not _group_ok(keys, normalized["groups"]):
            group_violations += 1
        captain = lineup_captain(lineup, kind)
        if captain:
            cpt_counts[player_key(captain)] += 1
        for player in lineup_players(lineup, kind):
            key = player_key(player)
            if key:
                if key not in players_by_key:
                    players_by_key[key] = player
                names.setdefault(key, str(player.get("Name") or key))

    for key, player in players_by_key.items():
        names.setdefault(key, str(player.get("Name") or key))

    warnings: List[str] = []
    for key, player in players_by_key.items():
        minimum = _min_count(player.get("MinPct"), target)
        maximum = _max_count(player.get("MaxPct"), target)
        if total_counts[key] < minimum:
            warnings.append(f"{names[key]} total exposure is {total_counts[key]}/{target}; minimum is {minimum}.")
        if maximum is not None and total_counts[key] > maximum:
            warnings.append(f"{names[key]} total exposure exceeds its maximum ({total_counts[key]}/{target} > {maximum}).")
        if kind == "showdown":
            minimum_cpt = _min_count(player.get("MinCptPct"), target)
            maximum_cpt = _max_count(player.get("MaxCptPct"), target)
            if cpt_counts[key] < minimum_cpt:
                warnings.append(f"{names[key]} captain exposure is {cpt_counts[key]}/{target}; minimum is {minimum_cpt}.")
            if maximum_cpt is not None and cpt_counts[key] > maximum_cpt:
                warnings.append(f"{names[key]} captain exposure exceeds its maximum ({cpt_counts[key]}/{target} > {maximum_cpt}).")

    team_limit = _max_count(normalized["max_team_pct"], target)
    game_limit = _max_count(normalized["max_game_pct"], target)
    if team_limit is not None:
        for team, count in team_counts.items():
            if count > team_limit:
                warnings.append(f"{team} team exposure exceeds {normalized['max_team_pct']:.0f}% ({count}/{target}).")
    if game_limit is not None:
        for game, count in game_counts.items():
            if count > game_limit:
                warnings.append(f"{game} game exposure exceeds {normalized['max_game_pct']:.0f}% ({count}/{target}).")
    if group_violations:
        warnings.append(f"{group_violations} lineup(s) violate a player-group rule.")

    min_observed_unique: Optional[int] = None
    for index, keys in enumerate(key_sets):
        for previous in key_sets[:index]:
            observed = min(len(keys - previous), len(previous - keys))
            min_observed_unique = observed if min_observed_unique is None else min(min_observed_unique, observed)
    if min_observed_unique is not None and min_observed_unique < normalized["min_unique"]:
        warnings.append(
            f"Observed minimum uniqueness is {min_observed_unique}; requested {normalized['min_unique']}."
        )

    player_rows = []
    for key, count in total_counts.most_common():
        player_rows.append({
            "key": key,
            "name": names.get(key, key),
            "count": count,
            "pct": (count / max(1, total)) * 100.0,
            "cpt_count": cpt_counts[key],
            "cpt_pct": (cpt_counts[key] / max(1, total)) * 100.0,
        })

    report = {
        "lineup_count": total,
        "requested": target,
        "rules": normalized,
        "players": player_rows,
        "teams": [{"name": key, "count": value, "pct": value / max(1, total) * 100.0} for key, value in team_counts.most_common()],
        "games": [{"name": key, "count": value, "pct": value / max(1, total) * 100.0} for key, value in game_counts.most_common()],
        "min_observed_unique": min_observed_unique,
        "warnings": warnings,
        "compliant": not warnings and total >= target,
    }
    report["text"] = _report_text(report)
    return report


def _report_text(report: Dict[str, Any]) -> str:
    rules = report.get("rules") or {}
    lines = [
        f"Portfolio summary: {int(report.get('lineup_count', 0))} lineup(s)",
        (
            f"Rules: {int(rules.get('min_unique', 1))} unique player(s) | "
            f"team <= {float(rules.get('max_team_pct', 100.0)):.0f}% | "
            f"game <= {float(rules.get('max_game_pct', 100.0)):.0f}% | "
            f"groups {len(rules.get('groups') or [])}"
        ),
        "",
        "Top player exposure:",
    ]
    for row in (report.get("players") or [])[:12]:
        captain = f" | CPT {float(row.get('cpt_pct', 0.0)):.1f}%" if float(row.get("cpt_pct", 0.0)) > 0 else ""
        lines.append(f"- {row.get('name')}: {float(row.get('pct', 0.0)):.1f}%{captain}")
    lines.append("")
    warnings = report.get("warnings") or []
    if warnings:
        lines.append("Needs attention:")
        lines.extend(f"- {warning}" for warning in warnings[:12])
    else:
        lines.append("All configured portfolio rules are satisfied.")
    return "\n".join(lines)
