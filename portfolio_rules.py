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


def _sim_metrics(lineup: Any) -> Dict[str, Any]:
    value = getattr(lineup, "sim_metrics", None)
    return value if isinstance(value, dict) else {}


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

    # If generation returned no surplus candidates and there are no hard
    # portfolio rules, every candidate must be selected. Avoid repeatedly
    # rescoring the same shrinking pool (and all prior overlaps) 150 times.
    # The pairwise check keeps the configured uniqueness contract intact.
    unrestricted_full_pool = (
        len(pool) <= requested
        and not normalized["groups"]
        and not any(value > 0 for value in min_total.values())
        and not any(value is not None for value in max_total.values())
        and not any(value > 0 for value in min_cpt.values())
        and not any(value is not None for value in max_cpt.values())
        and (max_team is None or max_team >= requested)
        and (max_game is None or max_game >= requested)
    )
    if unrestricted_full_pool:
        key_sets = [_lineup_sets(lineup, kind)[0] for lineup in pool]
        min_unique = normalized["min_unique"]
        pairwise_unique = all(
            len(key_sets[i] - key_sets[j]) >= min_unique
            for i in range(len(key_sets))
            for j in range(i)
        )
        if pairwise_unique:
            report = portfolio_report(pool, normalized, kind=kind, requested=requested)
            if len(pool) < requested:
                warning = (
                    f"Built {len(pool)} of {requested} requested lineups; "
                    "the generator exhausted its unique candidate pool."
                )
                if warning not in report["warnings"]:
                    report["warnings"].append(warning)
                report["text"] = _report_text(report)
            return {"lineups": pool, "report": report, "candidate_count": len(pool)}

    # Cache every immutable candidate property once.  A 150-lineup SIM build
    # scores hundreds of candidates at each selection step; re-reading nine
    # player dictionaries millions of times dominated the v1 selector.
    candidate_meta: Dict[int, Dict[str, Any]] = {}
    for lineup in pool:
        keys, teams, games = _lineup_sets(lineup, kind)
        captain = lineup_captain(lineup, kind)
        candidate_meta[id(lineup)] = {
            "keys": keys,
            "teams": teams,
            "games": games,
            "projection": _projection(lineup, kind),
            "ownership": _ownership(lineup, kind),
            "signature": _candidate_signature(lineup, kind),
            "captain_key": player_key(captain) if captain else "",
            "sim": _sim_metrics(lineup),
            "top_hits": set(getattr(lineup, "sim_top_hits", set()) or set()),
            "top_five_hits": set(getattr(lineup, "sim_top_five_hits", set()) or set()),
            "win_hits": set(getattr(lineup, "sim_win_hits", set()) or set()),
            "scenario_values": dict(getattr(lineup, "sim_scenario_values", {}) or {}),
        }

    selected: List[Any] = []
    selected_candidate_ids: set[int] = set()
    total_counts: Counter[str] = Counter()
    cpt_counts: Counter[str] = Counter()
    team_counts: Counter[str] = Counter()
    game_counts: Counter[str] = Counter()
    warnings: List[str] = []
    current_min_unique = normalized["min_unique"]
    sim_top_counts: Counter[int] = Counter()
    sim_top_five_counts: Counter[int] = Counter()
    sim_win_counts: Counter[int] = Counter()
    sim_value_counts: Counter[int] = Counter()

    uniqueness_cache: Dict[int, Dict[int, set[int]]] = {}

    def uniqueness_conflicts(min_unique: int) -> Dict[int, set[int]]:
        if min_unique <= 1:
            return {}
        if min_unique not in uniqueness_cache:
            conflicts: Dict[int, set[int]] = {id(lineup): set() for lineup in pool}
            for index, lineup in enumerate(pool):
                lineup_id = id(lineup)
                keys = candidate_meta[lineup_id]["keys"]
                for previous in pool[:index]:
                    previous_id = id(previous)
                    if len(keys - candidate_meta[previous_id]["keys"]) < min_unique:
                        conflicts[lineup_id].add(previous_id)
                        conflicts[previous_id].add(lineup_id)
            uniqueness_cache[min_unique] = conflicts
        return uniqueness_cache[min_unique]

    current_uniqueness_conflicts = uniqueness_conflicts(current_min_unique)

    def diminishing(hits: set[int], counts: Counter[int], weight: float) -> float:
        if not hits:
            return 0.0
        return weight * sum(1.0 / (1.0 + counts[hit]) for hit in hits) / len(hits)

    def admissible(lineup: Any, min_unique: int) -> bool:
        meta = candidate_meta[id(lineup)]
        keys = meta["keys"]
        teams = meta["teams"]
        games = meta["games"]
        if not keys or not _group_ok(keys, normalized["groups"]):
            return False
        if min_unique > 1 and current_uniqueness_conflicts.get(id(lineup), set()).intersection(selected_candidate_ids):
            return False
        for key in keys:
            limit = max_total.get(key)
            if limit is not None and total_counts[key] >= limit:
                return False
        captain_key = meta["captain_key"]
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
        meta = candidate_meta[id(lineup)]
        keys = meta["keys"]
        teams = meta["teams"]
        games = meta["games"]
        captain_key = meta["captain_key"]
        deficit_bonus = sum(500.0 for key in keys if total_counts[key] < min_total.get(key, 0))
        if captain_key and cpt_counts[captain_key] < min_cpt.get(captain_key, 0):
            deficit_bonus += 650.0
        concentration_penalty = sum(total_counts[key] for key in keys) * 0.08
        team_penalty = sum(team_counts[team] for team in teams) * 0.04
        game_penalty = sum(game_counts[game] for game in games) * 0.03
        ownership_penalty = meta["ownership"] * 0.018 if normalized["balance_ownership"] else 0.0
        # Exposure concentration already captures repeated player overlap and
        # is much cheaper than comparing every candidate to every selected set.
        overlap_penalty = 0.0
        sim = meta["sim"]
        sim_edge = _pct(sim.get("sim_edge"), None)
        if sim_edge is None:
            quality_score = meta["projection"]
            scenario_bonus = 0.0
            duplicate_penalty = 0.0
        else:
            # V2 uses both individual tournament strength and marginal value to
            # the portfolio.  Scenario bonuses diminish after a game script is
            # already covered, so 150 variants of the same ceiling outcome do
            # not crowd out different winning paths.
            return_index = _pct(sim.get("sim_return_index"), 0.0) or 0.0
            duplicate_risk = _pct(sim.get("duplicate_risk"), 0.0) or 0.0
            quality_score = (
                0.30 * meta["projection"]
                + 0.72 * sim_edge
                + 0.28 * return_index
            )
            top_hits = meta["top_hits"]
            top_five_hits = meta["top_five_hits"]
            win_hits = meta["win_hits"]
            values = meta["scenario_values"]

            if values:
                total_value = sum(max(0.0, float(value)) for value in values.values())
                value_ratio = sum(
                    max(0.0, float(value)) / (1.0 + sim_value_counts[scenario])
                    for scenario, value in values.items()
                ) / max(1e-9, total_value)
            else:
                value_ratio = 0.0
            scenario_bonus = (
                34.0 * value_ratio
                + diminishing(top_hits, sim_top_counts, 18.0)
                + diminishing(win_hits, sim_win_counts, 10.0)
                + diminishing(top_five_hits, sim_top_five_counts, 6.0)
            )
            duplicate_penalty = duplicate_risk * 0.12
        return (
            quality_score
            + scenario_bonus
            + deficit_bonus
            - concentration_penalty
            - team_penalty
            - game_penalty
            - ownership_penalty
            - overlap_penalty
            - duplicate_penalty
        )

    remaining = list(pool)
    while len(selected) < requested and remaining:
        eligible = [lineup for lineup in remaining if admissible(lineup, current_min_unique)]
        if not eligible:
            if current_min_unique > 1:
                current_min_unique -= 1
                current_uniqueness_conflicts = uniqueness_conflicts(current_min_unique)
                warnings.append(f"Minimum unique players relaxed to {current_min_unique} to finish the portfolio.")
                continue
            break
        chosen = max(eligible, key=lambda lineup: (score(lineup), candidate_meta[id(lineup)]["signature"]))
        remaining.remove(chosen)
        chosen_meta = candidate_meta[id(chosen)]
        keys = chosen_meta["keys"]
        teams = chosen_meta["teams"]
        games = chosen_meta["games"]
        selected.append(chosen)
        selected_candidate_ids.add(id(chosen))
        total_counts.update(keys)
        team_counts.update(teams)
        game_counts.update(games)
        if chosen_meta["captain_key"]:
            cpt_counts[chosen_meta["captain_key"]] += 1
        chosen_top_hits = chosen_meta["top_hits"]
        chosen_top_five_hits = chosen_meta["top_five_hits"]
        chosen_win_hits = chosen_meta["win_hits"]
        chosen_values = chosen_meta["scenario_values"]
        sim_top_counts.update(chosen_top_hits)
        sim_top_five_counts.update(chosen_top_five_hits)
        sim_win_counts.update(chosen_win_hits)
        sim_value_counts.update(chosen_values.keys())

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

    sim_rows = [_sim_metrics(lineup) for lineup in lineups or []]
    sim_rows = [row for row in sim_rows if row]
    sim_summary: Dict[str, Any] = {}
    if sim_rows:
        scenario_count = max(int(row.get("sim_scenarios", 0) or 0) for row in sim_rows)
        top_counts: Counter[int] = Counter()
        top_five_counts: Counter[int] = Counter()
        win_counts: Counter[int] = Counter()
        for lineup in lineups or []:
            top_counts.update(set(getattr(lineup, "sim_top_hits", set()) or set()))
            top_five_counts.update(set(getattr(lineup, "sim_top_five_hits", set()) or set()))
            win_counts.update(set(getattr(lineup, "sim_win_hits", set()) or set()))

        def average(field: str) -> float:
            return sum(float(row.get(field, 0.0) or 0.0) for row in sim_rows) / len(sim_rows)

        sim_summary = {
            "scenario_count": scenario_count,
            "average_edge": average("sim_edge"),
            "average_top_one_pct": average("sim_top_one_pct"),
            "average_top_five_pct": average("sim_top_five_pct"),
            "average_cash_rate": average("sim_cash_rate"),
            "average_bust_rate": average("sim_bust_rate"),
            "average_return_index": average("sim_return_index"),
            "average_duplicate_risk": average("duplicate_risk"),
            "top_one_scenarios_covered": len(top_counts),
            "top_five_scenarios_covered": len(top_five_counts),
            "win_scenarios_covered": len(win_counts),
            "exclusive_top_one_scenarios": sum(1 for count in top_counts.values() if count == 1),
            "top_one_coverage_pct": len(top_counts) / max(1, scenario_count) * 100.0,
            "top_five_coverage_pct": len(top_five_counts) / max(1, scenario_count) * 100.0,
        }

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
        "sim_summary": sim_summary,
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
    sim = report.get("sim_summary") or {}
    if sim:
        lines[3:3] = [
            "",
            (
                f"SIM portfolio: edge {float(sim.get('average_edge', 0.0)):.0f} | "
                f"top 1% {float(sim.get('average_top_one_pct', 0.0)):.2f}% | "
                f"return index {float(sim.get('average_return_index', 0.0)):.0f}"
            ),
            (
                f"Scenario coverage: top 1% in {int(sim.get('top_one_scenarios_covered', 0))}/"
                f"{int(sim.get('scenario_count', 0))} | representative wins in "
                f"{int(sim.get('win_scenarios_covered', 0))}"
            ),
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
