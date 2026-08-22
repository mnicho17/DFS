from __future__ import annotations

import math
import time
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
    retained_lineups: Optional[Sequence[Any]] = None,
    refinement_passes: int = 0,
    refinement_stop_callback: Optional[Any] = None,
    refinement_polish_duplication: bool = False,
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

    retained_by_signature: Dict[Tuple[str, ...], Any] = {}
    for lineup in retained_lineups or []:
        signature = _candidate_signature(lineup, kind)
        if signature and signature not in retained_by_signature and len(retained_by_signature) < requested:
            retained_by_signature[signature] = lineup
    retained = list(retained_by_signature.values())

    unique_candidates: Dict[Tuple[str, ...], Any] = {}
    for lineup in candidates or []:
        signature = _candidate_signature(lineup, kind)
        if signature and signature not in retained_by_signature and signature not in unique_candidates:
            unique_candidates[signature] = lineup
    pool = list(unique_candidates.values())
    all_lineups = retained + pool

    player_lookup: Dict[str, Dict[str, Any]] = {
        key: dict(value) for key, value in normalized["player_constraints"].items()
    }
    for lineup in all_lineups:
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
        not retained
        and len(pool) <= requested
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
            return {
                "lineups": pool,
                "report": report,
                "candidate_count": len(pool),
                "retained_count": 0,
            }

    # Cache every immutable candidate property once.  A 150-lineup SIM build
    # scores hundreds of candidates at each selection step; re-reading nine
    # player dictionaries millions of times dominated the v1 selector.
    candidate_meta: Dict[int, Dict[str, Any]] = {}
    for lineup in all_lineups:
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

    selected: List[Any] = list(retained)
    selected_candidate_ids: set[int] = {id(lineup) for lineup in retained}
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

    for lineup in retained:
        meta = candidate_meta[id(lineup)]
        total_counts.update(meta["keys"])
        team_counts.update(meta["teams"])
        game_counts.update(meta["games"])
        if meta["captain_key"]:
            cpt_counts[meta["captain_key"]] += 1
        sim_top_counts.update(meta["top_hits"])
        sim_top_five_counts.update(meta["top_five_hits"])
        sim_win_counts.update(meta["win_hits"])
        sim_value_counts.update(meta["scenario_values"].keys())

    uniqueness_cache: Dict[int, Dict[int, set[int]]] = {}

    def uniqueness_conflicts(min_unique: int) -> Dict[int, set[int]]:
        if min_unique <= 1:
            return {}
        if min_unique not in uniqueness_cache:
            conflicts: Dict[int, set[int]] = {id(lineup): set() for lineup in all_lineups}
            for index, lineup in enumerate(all_lineups):
                lineup_id = id(lineup)
                keys = candidate_meta[lineup_id]["keys"]
                for previous in all_lineups[:index]:
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
            quality_floor_penalty = 0.0
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
            # Scenario coverage should diversify strong lineups, not rescue a
            # weak candidate merely because its path is unusual. Below the
            # B-grade boundary, require increasingly more marginal scenario
            # value before the lineup can enter the final portfolio.
            quality_floor_penalty = max(0.0, 72.0 - sim_edge) * 1.25
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
            - quality_floor_penalty
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

    # Deep builds have enough candidates to benefit from a small local-search
    # pass after the greedy portfolio is complete.  Only non-retained lineups
    # can be swapped.  Every hard maximum, player group, and uniqueness rule is
    # rechecked before a change is accepted, while minimum-exposure shortfalls
    # retain the same strong priority used during greedy selection.
    refinement_swaps = 0
    duplication_refinement_swaps = 0
    refinement_attempts = 0
    refinement_stop_reason = "disabled" if not refinement_passes else "pass limit"
    refinement_passes = max(0, min(256, int(refinement_passes or 0)))
    refinement_started = time.perf_counter()
    retained_ids = {id(lineup) for lineup in retained}

    def fixed_quality(lineup: Any) -> float:
        meta = candidate_meta[id(lineup)]
        sim = meta["sim"]
        sim_edge = _pct(sim.get("sim_edge"), None)
        if sim_edge is None:
            return meta["projection"]
        return_index = _pct(sim.get("sim_return_index"), 0.0) or 0.0
        duplicate_risk = _pct(sim.get("duplicate_risk"), 0.0) or 0.0
        return (
            0.30 * meta["projection"]
            + 0.72 * sim_edge
            + 0.28 * return_index
            - 0.12 * duplicate_risk
            - max(0.0, 72.0 - sim_edge) * 1.25
        )

    def tournament_metrics(lineup: Any) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        sim = candidate_meta[id(lineup)]["sim"]
        return (
            _pct(sim.get("sim_edge"), None),
            _pct(sim.get("sim_return_index"), None),
            _pct(sim.get("duplicate_risk"), None),
        )

    def duplication_risk(lineup: Any, default: float = 100.0) -> float:
        value = tournament_metrics(lineup)[2]
        return default if value is None else value

    def counter_swap_delta(
        counts: Counter[str],
        outgoing: set[str],
        incoming: set[str],
        weight: float,
    ) -> float:
        delta = 0.0
        for key in outgoing.union(incoming):
            before = counts[key]
            after = before - int(key in outgoing) + int(key in incoming)
            delta -= weight * float(after * after - before * before)
        return delta

    def scenario_swap_delta(
        counts: Counter[int],
        outgoing: set[int],
        incoming: set[int],
        weight: float,
    ) -> float:
        touched = outgoing.union(incoming)
        if not touched:
            return 0.0
        scale = 100.0 / max(1, len(counts))
        delta = 0.0
        for scenario in touched:
            before = max(0, counts[scenario])
            after = max(0, before - int(scenario in outgoing) + int(scenario in incoming))
            delta += math.log1p(after) - math.log1p(before)
        return weight * scale * delta

    def swap_is_admissible(outgoing: Any, incoming: Any) -> bool:
        out_meta = candidate_meta[id(outgoing)]
        in_meta = candidate_meta[id(incoming)]
        keys = in_meta["keys"]
        if not keys or not _group_ok(keys, normalized["groups"]):
            return False
        other_selected = selected_candidate_ids - {id(outgoing)}
        if current_min_unique > 1 and current_uniqueness_conflicts.get(id(incoming), set()).intersection(other_selected):
            return False
        for key in out_meta["keys"].union(keys):
            limit = max_total.get(key)
            after = total_counts[key] - int(key in out_meta["keys"]) + int(key in keys)
            if limit is not None and after > limit:
                return False
        out_captain = out_meta["captain_key"]
        in_captain = in_meta["captain_key"]
        for key in {out_captain, in_captain} - {""}:
            limit = max_cpt.get(key)
            after = cpt_counts[key] - int(key == out_captain) + int(key == in_captain)
            if limit is not None and after > limit:
                return False
        for team in out_meta["teams"].union(in_meta["teams"]):
            after = team_counts[team] - int(team in out_meta["teams"]) + int(team in in_meta["teams"])
            if max_team is not None and after > max_team:
                return False
        for game in out_meta["games"].union(in_meta["games"]):
            after = game_counts[game] - int(game in out_meta["games"]) + int(game in in_meta["games"])
            if max_game is not None and after > max_game:
                return False
        return True

    def swap_score_delta(outgoing: Any, incoming: Any, *, duplication_focus: bool = False) -> float:
        out_meta = candidate_meta[id(outgoing)]
        in_meta = candidate_meta[id(incoming)]
        duplication_bonus = 0.0
        if duplication_focus:
            out_edge, out_return, out_duplication = tournament_metrics(outgoing)
            in_edge, in_return, in_duplication = tournament_metrics(incoming)
            if None in (
                out_edge,
                out_return,
                out_duplication,
                in_edge,
                in_return,
                in_duplication,
            ):
                return float("-inf")
            assert out_edge is not None and out_return is not None and out_duplication is not None
            assert in_edge is not None and in_return is not None and in_duplication is not None
            # The remaining-time phase has one narrow job: lower duplication
            # without quietly trading away tournament strength.  A replacement
            # must improve duplicate risk, stay close on both component scores,
            # and retain the combined Edge/Return signal.
            if in_duplication >= out_duplication - 0.25:
                return float("-inf")
            if in_edge < out_edge - 1.0 or in_return < out_return - 1.5:
                return float("-inf")
            out_strength = 0.70 * out_edge + 0.30 * out_return
            in_strength = 0.70 * in_edge + 0.30 * in_return
            if in_strength < out_strength - 0.10:
                return float("-inf")
            duplication_bonus = (out_duplication - in_duplication) * 0.45
        delta = fixed_quality(incoming) - fixed_quality(outgoing)
        touched_players = out_meta["keys"].union(in_meta["keys"])
        before_shortfall = sum(max(0, min_total.get(key, 0) - total_counts[key]) for key in touched_players)
        after_shortfall = sum(
            max(
                0,
                min_total.get(key, 0)
                - (
                    total_counts[key]
                    - int(key in out_meta["keys"])
                    + int(key in in_meta["keys"])
                ),
            )
            for key in touched_players
        )
        delta += 500.0 * float(before_shortfall - after_shortfall)
        delta += counter_swap_delta(total_counts, out_meta["keys"], in_meta["keys"], 0.08)
        delta += counter_swap_delta(team_counts, out_meta["teams"], in_meta["teams"], 0.04)
        delta += counter_swap_delta(game_counts, out_meta["games"], in_meta["games"], 0.03)
        delta += scenario_swap_delta(sim_top_counts, out_meta["top_hits"], in_meta["top_hits"], 18.0)
        delta += scenario_swap_delta(sim_win_counts, out_meta["win_hits"], in_meta["win_hits"], 10.0)
        delta += scenario_swap_delta(
            sim_top_five_counts,
            out_meta["top_five_hits"],
            in_meta["top_five_hits"],
            6.0,
        )
        delta += scenario_swap_delta(
            sim_value_counts,
            set(out_meta["scenario_values"]),
            set(in_meta["scenario_values"]),
            14.0,
        )
        return delta + duplication_bonus

    def apply_swap(outgoing: Any, incoming: Any) -> None:
        out_meta = candidate_meta[id(outgoing)]
        in_meta = candidate_meta[id(incoming)]
        index = selected.index(outgoing)
        selected[index] = incoming
        selected_candidate_ids.remove(id(outgoing))
        selected_candidate_ids.add(id(incoming))
        for counts, out_values, in_values in (
            (total_counts, out_meta["keys"], in_meta["keys"]),
            (team_counts, out_meta["teams"], in_meta["teams"]),
            (game_counts, out_meta["games"], in_meta["games"]),
            (sim_top_counts, out_meta["top_hits"], in_meta["top_hits"]),
            (sim_top_five_counts, out_meta["top_five_hits"], in_meta["top_five_hits"]),
            (sim_win_counts, out_meta["win_hits"], in_meta["win_hits"]),
            (sim_value_counts, set(out_meta["scenario_values"]), set(in_meta["scenario_values"])),
        ):
            counts.subtract(out_values)
            counts.update(in_values)
        if out_meta["captain_key"]:
            cpt_counts[out_meta["captain_key"]] -= 1
        if in_meta["captain_key"]:
            cpt_counts[in_meta["captain_key"]] += 1
        remaining.remove(incoming)
        remaining.append(outgoing)

    standard_refinement_finished = False
    for pass_index in range(refinement_passes):
        if refinement_stop_callback and refinement_stop_callback():
            refinement_stop_reason = "time budget or cancellation"
            break
        refinement_attempts += 1
        movable = [lineup for lineup in selected if id(lineup) not in retained_ids]
        if not movable or not remaining:
            refinement_stop_reason = "no movable alternatives"
            break
        duplication_focus = bool(
            refinement_polish_duplication
            and (standard_refinement_finished or pass_index >= 3)
        )
        if duplication_focus:
            outgoing_pool = sorted(
                movable,
                key=lambda lineup: (
                    duplication_risk(lineup, 0.0),
                    -fixed_quality(lineup),
                ),
                reverse=True,
            )[: min(45, len(movable))]
            incoming_pool = sorted(
                remaining,
                key=lambda lineup: (
                    fixed_quality(lineup) - 0.45 * duplication_risk(lineup),
                    candidate_meta[id(lineup)]["signature"],
                ),
                reverse=True,
            )[: min(350, len(remaining))]
        else:
            outgoing_pool = sorted(movable, key=fixed_quality)[: min(30, len(movable))]
            incoming_pool = sorted(remaining, key=fixed_quality, reverse=True)[: min(250, len(remaining))]
        best_delta = 0.15
        best_pair: Optional[Tuple[Any, Any]] = None
        stop_requested = False
        for outgoing in outgoing_pool:
            if refinement_stop_callback and refinement_stop_callback():
                stop_requested = True
                break
            for incoming_index, incoming in enumerate(incoming_pool):
                if (
                    refinement_stop_callback
                    and incoming_index % 24 == 0
                    and refinement_stop_callback()
                ):
                    stop_requested = True
                    break
                if not swap_is_admissible(outgoing, incoming):
                    continue
                delta = swap_score_delta(
                    outgoing,
                    incoming,
                    duplication_focus=duplication_focus,
                )
                if delta > best_delta:
                    best_delta = delta
                    best_pair = (outgoing, incoming)
            if stop_requested:
                break
        if stop_requested:
            refinement_stop_reason = "time budget or cancellation"
            break
        if best_pair is None:
            if refinement_polish_duplication and not duplication_focus:
                standard_refinement_finished = True
                continue
            refinement_stop_reason = (
                "duplication local optimum" if duplication_focus else "local optimum"
            )
            break
        apply_swap(*best_pair)
        refinement_swaps += 1
        if duplication_focus:
            duplication_refinement_swaps += 1
    else:
        refinement_stop_reason = "pass limit"

    if len(selected) < requested:
        warnings.append(
            f"Built {len(selected)} of {requested} requested lineups; hard exposure, group, team, or game limits exhausted the feasible candidate pool."
        )

    report = portfolio_report(selected, normalized, kind=kind, requested=requested)
    report["refinement_swaps"] = refinement_swaps
    report["duplication_refinement_swaps"] = duplication_refinement_swaps
    report["refinement_attempts"] = refinement_attempts
    report["refinement_stop_reason"] = refinement_stop_reason
    report["refinement_seconds"] = max(0.0, time.perf_counter() - refinement_started)
    for warning in warnings:
        if warning not in report["warnings"]:
            report["warnings"].append(warning)
    report["text"] = _report_text(report)
    return {
        "lineups": selected,
        "report": report,
        "candidate_count": len(pool),
        "retained_count": len(retained),
    }


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
        contest_rows = [row for row in sim_rows if row.get("sim_expected_roi_pct") is not None]
        if contest_rows:
            sim_summary.update({
                "contest_aware": True,
                "contest_name": str(contest_rows[0].get("sim_contest_name") or "Attached contest"),
                "average_expected_roi_pct": sum(
                    float(row.get("sim_expected_roi_pct", 0.0) or 0.0) for row in contest_rows
                ) / len(contest_rows),
                "average_expected_payout": sum(
                    float(row.get("sim_expected_payout", 0.0) or 0.0) for row in contest_rows
                ) / len(contest_rows),
                "average_expected_profit": sum(
                    float(row.get("sim_expected_profit", 0.0) or 0.0) for row in contest_rows
                ) / len(contest_rows),
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
        contest_line = []
        if sim.get("contest_aware"):
            contest_line = [
                (
                    f"Contest ROI: {float(sim.get('average_expected_roi_pct', 0.0)):+.1f}% | "
                    f"expected payout ${float(sim.get('average_expected_payout', 0.0)):,.2f} | "
                    f"expected profit ${float(sim.get('average_expected_profit', 0.0)):+,.2f}"
                )
            ]
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
        ] + contest_line
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
