from __future__ import annotations

"""Fast, dependency-free NFL field and contest simulation.

The ownership model builds complete, field-like DraftKings Classic rosters and
counts appearances.  The contest model then gives every lineup the same player
outcomes within a scenario, ranks candidate lineups against a representative
field, and attaches slate-relative SIM Edge metrics to each candidate.
"""

import bisect
import math
import random
from collections import Counter, defaultdict
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple


INACTIVE_STATUSES = {"OUT", "IR", "PUP", "NFI", "SUSP", "SUSPENDED"}
ROLE_LIMITS = {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "DST": 1}
ROLE_POOL_BUILD_STYLES = {"strategic", "balanced", "contrarian", "chalk"}
POSITION_CV = {"QB": 0.32, "RB": 0.55, "WR": 0.65, "TE": 0.70, "DST": 0.80}


# These are conservative starting assumptions, not claims about every contest.
# Results & Learning can blend sufficiently large local contest samples into a
# selected preset without replacing these guardrailed baselines.
NFL_FIELD_PRESETS: Dict[str, Dict[str, Any]] = {
    "Single Entry": {
        "field_size": 5000,
        "min_salary_pct": 0.980,
        "ownership_exponent": 0.70,
        "flex_rates": {"RB": 0.49, "WR": 0.40, "TE": 0.11},
        "stack_rates": {"0": 0.06, "1": 0.78, "2": 0.13, "3": 0.03},
        "bringback_rates": {"0": 0.10, "1": 0.38, "2_plus": 0.52},
    },
    "3-Max": {
        "field_size": 15000,
        "min_salary_pct": 0.975,
        "ownership_exponent": 0.64,
        "flex_rates": {"RB": 0.48, "WR": 0.41, "TE": 0.11},
        "stack_rates": {"0": 0.08, "1": 0.76, "2": 0.13, "3": 0.03},
        "bringback_rates": {"0": 0.10, "1": 0.40, "2_plus": 0.54},
    },
    "20-Max": {
        "field_size": 50000,
        "min_salary_pct": 0.960,
        "ownership_exponent": 0.56,
        "flex_rates": {"RB": 0.47, "WR": 0.42, "TE": 0.11},
        "stack_rates": {"0": 0.09, "1": 0.74, "2": 0.13, "3": 0.04},
        "bringback_rates": {"0": 0.11, "1": 0.42, "2_plus": 0.57},
    },
    "150-Max": {
        "field_size": 100000,
        "min_salary_pct": 0.940,
        "ownership_exponent": 0.48,
        "flex_rates": {"RB": 0.46, "WR": 0.42, "TE": 0.12},
        "stack_rates": {"0": 0.10, "1": 0.727, "2": 0.13, "3": 0.043},
        "bringback_rates": {"0": 0.12, "1": 0.42, "2_plus": 0.58},
    },
}


def nfl_field_preset(
    name: str = "150-Max",
    learned: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return a detached, normalized contest-field configuration."""
    selected = name if name in NFL_FIELD_PRESETS else "150-Max"
    config = {
        key: (dict(value) if isinstance(value, dict) else value)
        for key, value in NFL_FIELD_PRESETS[selected].items()
    }
    config["name"] = selected
    reference = dict((learned or {}).get("reference") or {})
    if reference:
        config["real_field_reference"] = reference
    learned_config = dict((learned or {}).get("field_config") or {})
    if (learned or {}).get("enabled") and learned_config:
        for key in ("min_salary_pct", "ownership_exponent", "field_size"):
            if key in learned_config:
                config[key] = learned_config[key]
        for key in (
            "flex_rates", "stack_rates", "bringback_rates",
            "field_ownership_profile", "winning_ownership_profile",
        ):
            if isinstance(learned_config.get(key), dict):
                config[key] = dict(learned_config[key])
        config["learned"] = True
        config["learned_entries"] = int((learned or {}).get("entries", 0) or 0)
        config["learned_contests"] = int((learned or {}).get("contests", 0) or 0)
    else:
        config["learned"] = False
    return config


class SimLineup(list):
    """Normal lineup list carrying simulation metadata for UI/portfolio scoring."""

    def __init__(
        self,
        players: Iterable[Dict[str, Any]],
        *,
        metrics: Optional[Dict[str, Any]] = None,
        top_hits: Optional[Iterable[int]] = None,
        top_five_hits: Optional[Iterable[int]] = None,
        win_hits: Optional[Iterable[int]] = None,
        scenario_values: Optional[Dict[int, float]] = None,
    ) -> None:
        super().__init__(players)
        self.sim_metrics = dict(metrics or {})
        self.sim_top_hits = set(top_hits or [])
        self.sim_top_five_hits = set(top_five_hits or [])
        self.sim_win_hits = set(win_hits or [])
        # Only positive, tournament-relevant scenarios are retained.  This
        # keeps portfolio scoring sparse while allowing diminishing returns
        # when several lineups succeed in the same game script.
        self.sim_scenario_values = dict(scenario_values or {})


def player_key(player: Dict[str, Any]) -> str:
    return (
        str(player.get("FlexNamePlusID") or "").strip()
        or str(player.get("FlexID") or "").strip()
        or str(player.get("Name") or "").strip()
    )


def lineup_signature(lineup: Sequence[Dict[str, Any]]) -> Tuple[str, ...]:
    return tuple(sorted(player_key(player) for player in lineup if player_key(player)))


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _position(player: Dict[str, Any]) -> str:
    raw = str(player.get("Position") or "").strip().upper().replace("D/ST", "DST")
    return raw.split("/")[0].split(",")[0]


def _team(player: Dict[str, Any]) -> str:
    return str(player.get("Team") or "").strip().upper()


def _opponent(player: Dict[str, Any]) -> str:
    return str(player.get("Opponent") or "").strip().upper()


def _game(player: Dict[str, Any]) -> str:
    game = str(player.get("GameKey") or player.get("GameInfo") or "").strip().upper()
    if " " in game:
        game = game.split()[0]
    return game


def _salary(player: Dict[str, Any]) -> float:
    return _number(player.get("FlexSalary"), 0.0)


def _projection(player: Dict[str, Any]) -> float:
    base = _number(player.get("FlexProjection"), 0.0)
    boost = _number(player.get("_PortfolioCandidateBoost"), 0.0)
    team_pct = _number(player.get("TeamAdjPct"), 0.0)
    return max(0.0, base * max(0.05, 1.0 + team_pct / 100.0) + boost)


def _status(player: Dict[str, Any]) -> str:
    return str(player.get("InjuryStatus") or player.get("Status") or "").strip().upper()


def _is_active(player: Dict[str, Any]) -> bool:
    return (
        _salary(player) > 0
        and bool(_position(player))
        and not bool(player.get("FadeFlex"))
        and _status(player) not in INACTIVE_STATUSES
    )


def _role_rank(player: Dict[str, Any]) -> Tuple[float, ...]:
    depth = int(_number(player.get("NFLDepthOrder"), 0.0))
    # Depth 1 is authoritative. Unknown depth sits between 1 and 2 so a partial
    # data match cannot incorrectly promote a known backup over an obvious DK starter.
    primary_depth = 1.0 if depth == 1 else 0.0
    depth_rank = -float(depth) if depth > 0 else -1.5
    return (
        1.0 if player.get("LockFlex") else 0.0,
        primary_depth,
        depth_rank,
        _number(player.get("NFLRoleScore"), 0.0),
        _projection(player),
        _salary(player),
    )


def build_nfl_role_pool(
    players: Sequence[Dict[str, Any]],
    *,
    preserve_locks: bool = True,
    preserve_player_keys: Iterable[str] = (),
) -> List[Dict[str, Any]]:
    """Return a compact active starter/rotation pool.

    When refreshed NFL depth data exists it leads the ranking.  Salary and
    projection provide a deterministic fallback for a raw DraftKings file.
    Manual locks and players explicitly required by portfolio rules are always
    preserved for lineup generation.
    """

    grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    preserve_keys = {str(key) for key in preserve_player_keys if str(key)}
    preserved: List[Dict[str, Any]] = []
    for player in players:
        if not _is_active(player):
            continue
        pos = _position(player)
        if pos not in ROLE_LIMITS:
            continue
        grouped[(_team(player), pos)].append(player)
        if (preserve_locks and player.get("LockFlex")) or player_key(player) in preserve_keys:
            preserved.append(player)

    selected: Dict[str, Dict[str, Any]] = {}
    for (_, pos), group in grouped.items():
        ranked = sorted(group, key=_role_rank, reverse=True)
        for player in ranked[: ROLE_LIMITS[pos]]:
            selected[player_key(player)] = player
    for player in preserved:
        selected[player_key(player)] = player
    return list(selected.values())


def should_use_nfl_role_pool(
    *,
    sport: str,
    kind: str,
    build_style: str,
    sim_enabled: bool,
) -> bool:
    """Return whether Classic generation should use the compact NFL role pool.

    Normal NFL build styles exclude deep backups even when contest simulation is
    disabled. Randomized remains the deliberate broad-pool option. SIM always
    uses the role pool, and Showdown keeps its full single-game player pool.
    """
    if str(sport or "").strip().upper() != "NFL":
        return False
    if str(kind or "classic").strip().lower() == "showdown":
        return False
    style = str(build_style or "Strategic").strip().lower()
    return bool(sim_enabled) or style in ROLE_POOL_BUILD_STYLES


def _weighted_pick(
    rng: random.Random,
    candidates: Sequence[Dict[str, Any]],
    *,
    use_ownership: bool,
    precomputed_weights: Optional[Dict[int, float]] = None,
) -> Optional[Dict[str, Any]]:
    if not candidates:
        return None
    weights: List[float] = []
    for player in candidates:
        if precomputed_weights is not None and id(player) in precomputed_weights:
            weight = precomputed_weights[id(player)]
        else:
            projection = _projection(player)
            salary = _salary(player)
            value = projection / max(1.0, salary / 1000.0)
            quality = projection + 0.35 * value + 0.00045 * salary
            weight = math.exp(max(-8.0, min(8.0, quality / 8.0)))
            if use_ownership:
                ownership = max(0.05, _number(player.get("ProjOwnPct"), 0.0))
                weight *= ownership ** 0.55
        weights.append(max(1e-9, weight))
    return rng.choices(list(candidates), weights=weights, k=1)[0]


def _lineup_counts(lineup: Sequence[Dict[str, Any]]) -> Tuple[Counter[str], Counter[str]]:
    return (
        Counter(_team(player) for player in lineup if _team(player)),
        Counter(_game(player) for player in lineup if _game(player)),
    )


def _can_add(
    player: Dict[str, Any],
    lineup: Sequence[Dict[str, Any]],
    used: set[str],
    salary_cap: float,
) -> bool:
    if player_key(player) in used:
        return False
    if sum(_salary(item) for item in lineup) + _salary(player) > salary_cap:
        return False
    team_counts, game_counts = _lineup_counts(lineup)
    if _team(player) and team_counts[_team(player)] >= 4:
        return False
    if _game(player) and game_counts[_game(player)] >= 5:
        return False
    return True


def _build_field_lineup(
    rng: random.Random,
    by_pos: Dict[str, List[Dict[str, Any]]],
    *,
    salary_cap: float,
    min_salary: float,
    use_ownership: bool,
    precomputed_weights: Dict[int, float],
    candidate_mode: bool = False,
    field_config: Optional[Dict[str, Any]] = None,
) -> Optional[List[Dict[str, Any]]]:
    config = dict(field_config or {})
    # Field-like FLEX mix: RB/WR dominate; TE remains uncommon. A selected
    # contest preset (and, eventually, guarded local learning) can refine it.
    flex_rates = dict(config.get("flex_rates") or {"RB": 0.46, "WR": 0.42, "TE": 0.12})
    flex_pos = rng.choices(
        ["RB", "WR", "TE"],
        weights=[max(0.001, _number(flex_rates.get(pos), 0.0)) for pos in ("RB", "WR", "TE")],
        k=1,
    )[0]
    needs = {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "DST": 1}
    needs[flex_pos] += 1
    lineup: List[Dict[str, Any]] = []
    used: set[str] = set()
    salary_used = 0.0
    team_counts: Counter[str] = Counter()
    game_counts: Counter[str] = Counter()

    def can_add(player: Dict[str, Any]) -> bool:
        key = player_key(player)
        team = _team(player)
        game = _game(player)
        return (
            key not in used
            and salary_used + _salary(player) <= salary_cap
            and (not team or team_counts[team] < 4)
            and (not game or game_counts[game] < 5)
        )

    def add(player: Dict[str, Any]) -> None:
        nonlocal salary_used
        lineup.append(player)
        used.add(player_key(player))
        salary_used += _salary(player)
        if _team(player):
            team_counts[_team(player)] += 1
        if _game(player):
            game_counts[_game(player)] += 1

    def pick(candidates: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        return _weighted_pick(
            rng,
            candidates,
            use_ownership=use_ownership,
            precomputed_weights=precomputed_weights,
        )

    qb = pick(by_pos.get("QB", []))
    if qb is None:
        return None
    add(qb)
    needs["QB"] = 0

    # Approximate field construction priors: 13% double stack, 4.3% triple,
    # most remaining lineups use a single pass catcher.
    roll = rng.random()
    if candidate_mode:
        # Give the candidate bank deliberate access to the constructions that
        # historically appear more often near the top than in the field.
        stack_target = 3 if roll < 0.077 else 2 if roll < 0.267 else 1
    else:
        stack_rates = dict(
            config.get("stack_rates")
            or {"0": 0.10, "1": 0.727, "2": 0.13, "3": 0.043}
        )
        stack_values = [0, 1, 2, 3]
        stack_target = rng.choices(
            stack_values,
            weights=[max(0.001, _number(stack_rates.get(str(value)), 0.0)) for value in stack_values],
            k=1,
        )[0]
    for _ in range(stack_target):
        stack_pool = [
            player
            for pos in ("WR", "TE")
            for player in by_pos.get(pos, [])
            if _team(player) == _team(qb)
            and needs.get(pos, 0) > 0
            and can_add(player)
        ]
        chosen = pick(stack_pool)
        if chosen is None:
            break
        pos = _position(chosen)
        add(chosen)
        needs[pos] -= 1

    actual_stack = sum(
        1 for player in lineup
        if _team(player) == _team(qb) and _position(player) in {"WR", "TE"}
    )
    bringback_chance = (
        (0.68 if actual_stack >= 2 else 0.52 if actual_stack == 1 else 0.12)
        if candidate_mode
        else _number(
            (config.get("bringback_rates") or {}).get(
                "2_plus" if actual_stack >= 2 else str(actual_stack),
                0.58 if actual_stack >= 2 else 0.42 if actual_stack == 1 else 0.12,
            ),
            0.0,
        )
    )
    if _opponent(qb) and rng.random() < bringback_chance:
        bring_pool = [
            player
            for pos in ("RB", "WR", "TE")
            for player in by_pos.get(pos, [])
            if _team(player) == _opponent(qb)
            and needs.get(pos, 0) > 0
            and can_add(player)
        ]
        bringback = pick(bring_pool)
        if bringback is not None:
            pos = _position(bringback)
            add(bringback)
            needs[pos] -= 1

    for pos in ("TE", "WR", "RB"):
        while needs[pos] > 0:
            candidates = [
                player for player in by_pos.get(pos, [])
                if can_add(player)
            ]
            chosen = pick(candidates)
            if chosen is None:
                return None
            add(chosen)
            needs[pos] -= 1

    dst_candidates = []
    for dst in by_pos.get("DST", []):
        if _team(dst) == _opponent(qb) or not can_add(dst):
            continue
        opposing_offense = sum(1 for player in lineup if _team(player) == _opponent(dst))
        if opposing_offense > 1:
            continue
        dst_candidates.append(dst)
    dst = pick(dst_candidates)
    if dst is None:
        return None
    add(dst)

    if len(lineup) != 9 or salary_used < min_salary or salary_used > salary_cap:
        return None
    if len({player_key(player) for player in lineup}) != 9:
        return None
    return lineup


def generate_nfl_field_lineups(
    players: Sequence[Dict[str, Any]],
    count: int,
    *,
    salary_cap: float = 50000.0,
    min_salary: Optional[float] = None,
    seed: int = 20260809,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    cancel_callback: Optional[Callable[[], bool]] = None,
    candidate_mode: bool = False,
    unique: bool = False,
    field_config: Optional[Dict[str, Any]] = None,
) -> Tuple[List[List[Dict[str, Any]]], List[Dict[str, Any]]]:
    role_pool = build_nfl_role_pool(players, preserve_locks=False)
    by_pos: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for player in role_pool:
        by_pos[_position(player)].append(player)
    if any(not by_pos.get(pos) for pos in ("QB", "RB", "WR", "TE", "DST")):
        return [], role_pool

    requested = max(1, int(count or 1))
    config = dict(field_config or {})
    default_floor = salary_cap * _number(config.get("min_salary_pct"), 0.98)
    floor = float(min_salary if min_salary is not None else max(0.0, default_floor))
    ownership_sum = sum(max(0.0, _number(player.get("ProjOwnPct"), 0.0)) for player in role_pool)
    use_ownership = ownership_sum >= 300.0
    precomputed_weights: Dict[int, float] = {}
    for player in role_pool:
        projection = _projection(player)
        salary = _salary(player)
        value = projection / max(1.0, salary / 1000.0)
        quality = projection + 0.35 * value + 0.00045 * salary
        weight = math.exp(max(-8.0, min(8.0, quality / 8.0)))
        if use_ownership:
            ownership = max(0.05, _number(player.get("ProjOwnPct"), 0.0))
            ownership_exponent = _number(config.get("ownership_exponent"), 0.55)
            weight *= ownership ** max(0.05, min(1.25, ownership_exponent))
        precomputed_weights[id(player)] = max(1e-9, weight)
    rng = random.Random(seed)
    lineups: List[List[Dict[str, Any]]] = []
    signatures: set[Tuple[str, ...]] = set()
    attempts = 0
    max_attempts = max(2500, requested * 100)
    report_every = max(25, requested // 25)
    while len(lineups) < requested and attempts < max_attempts:
        if cancel_callback and cancel_callback():
            break
        attempts += 1
        lineup = _build_field_lineup(
            rng,
            by_pos,
            salary_cap=salary_cap,
            min_salary=floor,
            use_ownership=use_ownership,
            precomputed_weights=precomputed_weights,
            candidate_mode=candidate_mode,
            field_config=config,
        )
        if lineup is None:
            continue
        signature = lineup_signature(lineup)
        if unique and signature in signatures:
            continue
        signatures.add(signature)
        lineups.append(lineup)
        if progress_callback and (len(lineups) % report_every == 0 or len(lineups) == requested):
            progress_callback(len(lineups), requested, "Building valid NFL field lineups")
    return lineups, role_pool


def simulate_nfl_field_ownership(
    players: Sequence[Dict[str, Any]],
    num_lineups: int,
    *,
    salary_cap: float = 50000.0,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    cancel_callback: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    lineups, role_pool = generate_nfl_field_lineups(
        players,
        num_lineups,
        salary_cap=salary_cap,
        min_salary=max(0.0, salary_cap - 1000.0),
        progress_callback=progress_callback,
        cancel_callback=cancel_callback,
    )
    counts: Counter[str] = Counter()
    pair_counts: Counter[Tuple[str, str]] = Counter()
    for lineup in lineups:
        keys = sorted(player_key(player) for player in lineup)
        counts.update(keys)
        for index, left in enumerate(keys):
            for right in keys[index + 1 :]:
                pair_counts[(left, right)] += 1
    denominator = float(max(1, len(lineups)))
    total_pct = {key: count / denominator * 100.0 for key, count in counts.items()}
    return {
        "total": total_pct,
        "cpt": {},
        "flex": total_pct,
        "meta": {
            "valid_lineups": len(lineups),
            "requested_lineups": int(num_lineups),
            "role_pool_size": len(role_pool),
            "eligible_keys": [player_key(player) for player in role_pool],
            "pair_counts": dict(pair_counts.most_common(250)),
        },
    }


def _scenario_outcomes(
    rng: random.Random,
    players: Sequence[Dict[str, Any]],
) -> Dict[str, float]:
    games = {_game(player) for player in players if _game(player)}
    teams = {_team(player) for player in players if _team(player)}
    game_factor = {game: rng.gauss(0.0, 1.0) for game in games}
    team_environment = {team: rng.gauss(0.0, 1.0) for team in teams}
    team_style = {team: rng.gauss(0.0, 1.0) for team in teams}
    team_pass = {
        team: (team_environment[team] + team_style[team]) / math.sqrt(2.0)
        for team in teams
    }
    team_rush = {
        team: (team_environment[team] - team_style[team]) / math.sqrt(2.0)
        for team in teams
    }

    outcomes: Dict[str, float] = {}
    for player in players:
        pos = _position(player)
        team = _team(player)
        game = _game(player)
        opponent = _opponent(player)
        game_z = game_factor.get(game, 0.0)
        env_z = team_environment.get(team, 0.0)
        pass_z = team_pass.get(team, 0.0)
        rush_z = team_rush.get(team, 0.0)
        idio = rng.gauss(0.0, 1.0)
        if pos == "QB":
            z = 0.30 * game_z + 0.50 * pass_z + 0.15 * env_z + math.sqrt(0.638) * idio
        elif pos in {"WR", "TE"}:
            z = 0.32 * game_z + 0.43 * pass_z + 0.12 * env_z + math.sqrt(0.699) * idio
        elif pos == "RB":
            z = 0.22 * game_z + 0.38 * rush_z + 0.15 * env_z + math.sqrt(0.785) * idio
        else:  # DST: own rushing success helps; opposing offense and game scoring hurt.
            opp_env = team_environment.get(opponent, 0.0)
            z = -0.20 * game_z + 0.18 * rush_z - 0.38 * opp_env + math.sqrt(0.783) * idio

        mean = max(0.10, _projection(player))
        cv = POSITION_CV.get(pos, 0.60)
        if pos == "DST":
            score = max(-4.0, mean + max(3.0, mean * cv) * z)
        else:
            sigma_log = math.sqrt(math.log1p(cv * cv))
            score = mean * math.exp(sigma_log * z - 0.5 * sigma_log * sigma_log)
            score = min(score, mean * 5.0 + 15.0)
        outcomes[player_key(player)] = score
    return outcomes


def _percentile_rank(values: Sequence[float], value: float) -> float:
    if len(values) <= 1:
        return 0.5
    ordered = sorted(values)
    left = bisect.bisect_left(ordered, value)
    right = bisect.bisect_right(ordered, value)
    return ((left + right - 1) / 2.0) / float(len(ordered) - 1)


def _quantile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * percentile))))
    return float(ordered[index])


def _summarize_generated_field(
    lineups: Sequence[Sequence[Dict[str, Any]]],
) -> Dict[str, Any]:
    signatures = Counter(lineup_signature(lineup) for lineup in lineups)
    duplicated_entries = sum(count for count in signatures.values() if count > 1)
    salaries: List[float] = []
    stack_counts: Counter[str] = Counter()
    bringback_trials: Counter[str] = Counter()
    bringback_hits: Counter[str] = Counter()
    flex_counts: Counter[str] = Counter()
    profile = Counter()
    ownership_slots = 0
    total_slots = 0
    for lineup in lineups:
        if not lineup:
            continue
        salaries.append(sum(_salary(player) for player in lineup))
        ownership = [_number(player.get("ProjOwnPct"), 0.0) for player in lineup]
        total_slots += len(ownership)
        ownership_slots += sum(value > 0 for value in ownership)
        if ownership and all(value > 0 for value in ownership):
            total = sum(ownership)
            profile.update({
                "lineups": 1.0,
                "total_ownership": total,
                "player_ownership": total / len(ownership),
                "sub_five_players": float(sum(value < 5.0 for value in ownership)),
                "sub_ten_players": float(sum(value < 10.0 for value in ownership)),
                "twenty_plus_players": float(sum(value >= 20.0 for value in ownership)),
                "thirty_plus_players": float(sum(value >= 30.0 for value in ownership)),
            })
        quarterbacks = [player for player in lineup if _position(player) == "QB"]
        if len(quarterbacks) == 1:
            qb = quarterbacks[0]
            stack_count = sum(
                1 for player in lineup
                if _team(player) == _team(qb) and _position(player) in {"WR", "TE"}
            )
            stack_counts[str(min(3, stack_count))] += 1
            bring_key = "2_plus" if stack_count >= 2 else str(stack_count)
            bringback_trials[bring_key] += 1
            if _opponent(qb) and any(
                _team(player) == _opponent(qb) and _position(player) in {"RB", "WR", "TE"}
                for player in lineup
            ):
                bringback_hits[bring_key] += 1
        position_counts = Counter(_position(player) for player in lineup)
        excess = {
            "RB": position_counts["RB"] - 2,
            "WR": position_counts["WR"] - 3,
            "TE": position_counts["TE"] - 1,
        }
        flex_pos = max(excess, key=lambda pos: excess[pos])
        if excess[flex_pos] > 0:
            flex_counts[flex_pos] += 1
    lineup_count = len(lineups)
    construction_count = sum(stack_counts.values())
    flex_total = sum(flex_counts.values())
    profile_count = int(profile.get("lineups", 0) or 0)
    return {
        "entries": lineup_count,
        "unique_lineups": len(signatures),
        "duplicate_entry_pct": duplicated_entries / max(1, lineup_count) * 100.0,
        "avg_salary": sum(salaries) / max(1, len(salaries)) if salaries else None,
        "salary_p10": _quantile(salaries, 0.10) if salaries else None,
        "stack_rates": {
            str(value): stack_counts[str(value)] / max(1, construction_count)
            for value in range(4)
        },
        "bringback_rates": {
            key: bringback_hits[key] / max(1, bringback_trials[key])
            for key in ("0", "1", "2_plus")
        },
        "flex_rates": {
            pos: flex_counts[pos] / max(1, flex_total)
            for pos in ("RB", "WR", "TE")
        },
        "ownership_profile": {
            "lineups": profile_count,
            "ownership_coverage_pct": ownership_slots / max(1, total_slots) * 100.0,
            **{
                f"avg_{key}": float(profile.get(key, 0.0)) / max(1, profile_count)
                for key in (
                    "total_ownership", "player_ownership", "sub_five_players",
                    "sub_ten_players", "twenty_plus_players", "thirty_plus_players",
                )
            },
        },
    }


def compare_nfl_lineups_to_preset(
    lineups: Sequence[Sequence[Dict[str, Any]]],
    field_config: Optional[Dict[str, Any]] = None,
    *,
    salary_cap: float = 50000.0,
) -> Dict[str, Any]:
    """Explain how a lineup set differs from the selected contest preset.

    This is descriptive and report-only.  It does not reject a creative
    portfolio or modify the optimizer's selected lineups.
    """
    config = dict(field_config or {})
    summary = _summarize_generated_field(lineups)
    entries = int(summary.get("entries", 0) or 0)
    if not entries:
        return {
            "available": False,
            "fit_score": 0.0,
            "summary": "No generated lineups are available for preset comparison.",
            "components": {},
            "generated": summary,
            "preset": str(config.get("name") or "Custom"),
        }

    def distribution_fit(actual: Dict[str, Any], target: Dict[str, Any], keys: Sequence[str]) -> float:
        if not target:
            return 100.0
        distance = sum(abs(_number(actual.get(key), 0.0) - _number(target.get(key), 0.0)) for key in keys)
        return max(0.0, min(100.0, (1.0 - distance / 2.0) * 100.0))

    floor = float(salary_cap or 50000.0) * _number(config.get("min_salary_pct"), 0.94)
    salary_p10 = _number(summary.get("salary_p10"), 0.0)
    salary_fit = max(0.0, min(100.0, 100.0 - max(0.0, floor - salary_p10) / 20.0))
    stack_fit = distribution_fit(
        dict(summary.get("stack_rates") or {}), dict(config.get("stack_rates") or {}), ("0", "1", "2", "3")
    )
    bringback_fit = distribution_fit(
        dict(summary.get("bringback_rates") or {}), dict(config.get("bringback_rates") or {}), ("0", "1", "2_plus")
    )
    flex_fit = distribution_fit(
        dict(summary.get("flex_rates") or {}), dict(config.get("flex_rates") or {}), ("RB", "WR", "TE")
    )
    ownership_coverage = _number((summary.get("ownership_profile") or {}).get("ownership_coverage_pct"), 0.0)
    ownership_fit = max(0.0, min(100.0, ownership_coverage / 0.8))
    components = {
        "salary": round(salary_fit, 1),
        "qb_stacks": round(stack_fit, 1),
        "bring_backs": round(bringback_fit, 1),
        "flex_mix": round(flex_fit, 1),
        "ownership_coverage": round(ownership_fit, 1),
    }
    fit_score = (
        0.35 * salary_fit
        + 0.25 * stack_fit
        + 0.18 * bringback_fit
        + 0.17 * flex_fit
        + 0.05 * ownership_fit
    )
    weakest = min(components, key=components.get)
    weakest_label = {
        "salary": "salary use",
        "qb_stacks": "QB-stack mix",
        "bring_backs": "bring-back mix",
        "flex_mix": "FLEX mix",
        "ownership_coverage": "ownership coverage",
    }[weakest]
    if fit_score >= 85.0:
        fit_text = "closely matches"
    elif fit_score >= 70.0:
        fit_text = "generally matches"
    elif fit_score >= 50.0:
        fit_text = "partly matches"
    else:
        fit_text = "differs materially from"
    return {
        "available": True,
        "fit_score": round(fit_score, 1),
        "summary": (
            f"Portfolio {fit_text} the {config.get('name', 'selected')} preset "
            f"({fit_score:.0f}/100); largest gap is {weakest_label}."
        ),
        "components": components,
        "generated": summary,
        "preset": str(config.get("name") or "Custom"),
        "targets": {
            "salary_floor": floor,
            "stack_rates": dict(config.get("stack_rates") or {}),
            "bringback_rates": dict(config.get("bringback_rates") or {}),
            "flex_rates": dict(config.get("flex_rates") or {}),
        },
    }


def _learned_ownership_profile_fit(
    lineup: Sequence[Dict[str, Any]],
    target: Dict[str, Any],
) -> Optional[float]:
    ownership = [_number(player.get("ProjOwnPct"), 0.0) for player in lineup]
    if not ownership or not all(value > 0 for value in ownership) or not target.get("lineups"):
        return None
    actual = {
        "avg_total_ownership": sum(ownership),
        "avg_sub_five_players": float(sum(value < 5.0 for value in ownership)),
        "avg_sub_ten_players": float(sum(value < 10.0 for value in ownership)),
        "avg_twenty_plus_players": float(sum(value >= 20.0 for value in ownership)),
        "avg_thirty_plus_players": float(sum(value >= 30.0 for value in ownership)),
    }
    scales = {
        "avg_total_ownership": 45.0,
        "avg_sub_five_players": 1.5,
        "avg_sub_ten_players": 2.0,
        "avg_twenty_plus_players": 2.5,
        "avg_thirty_plus_players": 2.0,
    }
    weights = {
        "avg_total_ownership": 0.40,
        "avg_sub_five_players": 0.15,
        "avg_sub_ten_players": 0.15,
        "avg_twenty_plus_players": 0.20,
        "avg_thirty_plus_players": 0.10,
    }
    score = 0.0
    used_weight = 0.0
    for key, weight in weights.items():
        if target.get(key) is None:
            continue
        distance = abs(actual[key] - _number(target.get(key), actual[key]))
        score += weight * math.exp(-distance / scales[key])
        used_weight += weight
    return score / used_weight * 100.0 if used_weight > 0 else None


def simulate_nfl_contest(
    candidates: Sequence[Sequence[Dict[str, Any]]],
    players: Sequence[Dict[str, Any]],
    *,
    scenarios: int = 750,
    field_lineup_count: int = 1200,
    salary_cap: float = 50000.0,
    field_size: Optional[int] = None,
    field_config: Optional[Dict[str, Any]] = None,
    seed: int = 90210,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    cancel_callback: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    candidate_lists = [list(lineup) for lineup in candidates if lineup]
    if not candidate_lists:
        return {"lineups": [], "report": {"scenarios": 0, "field_lineups": 0}}

    config = dict(field_config or {})
    effective_field_size = int(
        field_size
        if field_size is not None
        else _number(config.get("field_size"), 100000.0)
    )
    field_floor = max(
        0.0,
        salary_cap * _number(config.get("min_salary_pct"), 0.98),
    )
    field_lineups, role_pool = generate_nfl_field_lineups(
        players,
        field_lineup_count,
        salary_cap=salary_cap,
        seed=seed + 1,
        cancel_callback=cancel_callback,
        field_config=config,
    )
    if not field_lineups:
        # A tiny or heavily locked fixture can still be graded against candidates.
        field_lineups = [list(lineup) for lineup in candidate_lists]

    player_by_key: Dict[str, Dict[str, Any]] = {}
    for player in list(role_pool) + [p for lineup in candidate_lists for p in lineup]:
        player_by_key[player_key(player)] = player
    sim_players = list(player_by_key.values())
    candidate_keys = [lineup_signature(lineup) for lineup in candidate_lists]
    field_keys = [lineup_signature(lineup) for lineup in field_lineups]

    field_signatures = Counter(field_keys)
    field_appearances: Counter[str] = Counter(key for signature in field_keys for key in signature)
    field_ownership = {
        key: count / max(1, len(field_lineups)) * 100.0
        for key, count in field_appearances.items()
    }
    generated_field_summary = _summarize_generated_field(field_lineups)

    scenario_count = max(1, int(scenarios or 1))
    rng = random.Random(seed)
    scores_by_candidate: List[List[float]] = [[] for _ in candidate_lists]
    top_hits: List[set[int]] = [set() for _ in candidate_lists]
    top_five_hits: List[set[int]] = [set() for _ in candidate_lists]
    win_hits: List[set[int]] = [set() for _ in candidate_lists]
    scenario_values: List[Dict[int, float]] = [dict() for _ in candidate_lists]
    wins = [0 for _ in candidate_lists]
    cashes = [0 for _ in candidate_lists]
    busts = [0 for _ in candidate_lists]
    return_scores = [0.0 for _ in candidate_lists]
    percentile_sums = [0.0 for _ in candidate_lists]
    completed = 0
    batch = max(10, scenario_count // 25)

    for scenario_index in range(scenario_count):
        if cancel_callback and cancel_callback():
            break
        outcomes = _scenario_outcomes(rng, sim_players)
        field_scores = sorted(
            sum(outcomes.get(key, 0.0) for key in signature)
            for signature in field_keys
        )
        if not field_scores:
            continue
        top_one_threshold = field_scores[max(0, int(math.floor(0.99 * (len(field_scores) - 1))))]
        top_five_threshold = field_scores[max(0, int(math.floor(0.95 * (len(field_scores) - 1))))]
        cash_threshold = field_scores[max(0, int(math.floor(0.80 * (len(field_scores) - 1))))]
        bust_threshold = field_scores[max(0, int(math.floor(0.40 * (len(field_scores) - 1))))]
        winning_score = field_scores[-1]
        for index, signature in enumerate(candidate_keys):
            score = sum(outcomes.get(key, 0.0) for key in signature)
            scores_by_candidate[index].append(score)
            percentile = bisect.bisect_right(field_scores, score) / float(len(field_scores))
            percentile_sums[index] += percentile
            if score >= top_one_threshold:
                top_hits[index].add(scenario_index)
            if score >= top_five_threshold:
                top_five_hits[index].add(scenario_index)
            if score >= cash_threshold:
                cashes[index] += 1
            if score < bust_threshold:
                busts[index] += 1
            if score >= winning_score:
                wins[index] += 1
                win_hits[index].add(scenario_index)

            # A compact payout-shape proxy.  The values are not dollars and
            # deliberately emphasize tournament tails over median outcomes.
            if score >= winning_score:
                scenario_value = 16.0
            elif score >= top_one_threshold:
                scenario_value = 6.0 + max(0.0, percentile - 0.99) * 200.0
            elif score >= top_five_threshold:
                scenario_value = 1.5 + max(0.0, percentile - 0.95) * 75.0
            elif score >= cash_threshold:
                scenario_value = 0.20
            else:
                scenario_value = -1.0
            return_scores[index] += scenario_value
            if scenario_value >= 1.5:
                scenario_values[index][scenario_index] = scenario_value
        completed += 1
        if progress_callback and ((scenario_index + 1) % batch == 0 or scenario_index + 1 == scenario_count):
            progress_callback(scenario_index + 1, scenario_count, "Ranking candidates against simulated NFL fields")

    denominator = float(max(1, completed))
    preliminary: List[Dict[str, Any]] = []
    winning_ownership_target = dict(config.get("winning_ownership_profile") or {})
    for index, lineup in enumerate(candidate_lists):
        signature = candidate_keys[index]
        ownership_values = [max(0.05, field_ownership.get(key, 0.05)) / 100.0 for key in signature]
        log_product = sum(math.log(value) for value in ownership_values)
        salary = sum(_salary(player) for player in lineup)
        duplication_raw = log_product + max(0.0, salary - field_floor) / 2500.0
        exact_matches = field_signatures.get(signature, 0)
        learned_profile_fit = _learned_ownership_profile_fit(lineup, winning_ownership_target)
        preliminary.append({
            "sim_win_rate": wins[index] / denominator * 100.0,
            "sim_top_one_pct": len(top_hits[index]) / denominator * 100.0,
            "sim_top_five_pct": len(top_five_hits[index]) / denominator * 100.0,
            "sim_cash_rate": cashes[index] / denominator * 100.0,
            "sim_bust_rate": busts[index] / denominator * 100.0,
            "sim_average_percentile": percentile_sums[index] / denominator * 100.0,
            "sim_mean": sum(scores_by_candidate[index]) / max(1, len(scores_by_candidate[index])),
            "sim_ceiling": _quantile(scores_by_candidate[index], 0.90),
            "sim_return_score": return_scores[index] / denominator,
            "duplication_raw": duplication_raw,
            "field_exact_matches": exact_matches,
            "field_duplicate_estimate": exact_matches * (float(effective_field_size) / max(1.0, float(len(field_lineups)))),
            "learned_profile_fit": learned_profile_fit,
        })

    top_values = [item["sim_top_one_pct"] for item in preliminary]
    top_five_values = [item["sim_top_five_pct"] for item in preliminary]
    win_values = [item["sim_win_rate"] for item in preliminary]
    ceiling_values = [item["sim_ceiling"] for item in preliminary]
    return_values = [item["sim_return_score"] for item in preliminary]
    dup_values = [item["duplication_raw"] for item in preliminary]
    wrapped: List[SimLineup] = []
    for index, lineup in enumerate(candidate_lists):
        metrics = preliminary[index]
        top_rank = _percentile_rank(top_values, metrics["sim_top_one_pct"])
        top_five_rank = _percentile_rank(top_five_values, metrics["sim_top_five_pct"])
        win_rank = _percentile_rank(win_values, metrics["sim_win_rate"])
        ceiling_rank = _percentile_rank(ceiling_values, metrics["sim_ceiling"])
        return_rank = _percentile_rank(return_values, metrics["sim_return_score"])
        duplicate_rank = _percentile_rank(dup_values, metrics["duplication_raw"])
        base_edge = (
            0.32 * top_rank
            + 0.12 * win_rank
            + 0.16 * top_five_rank
            + 0.15 * ceiling_rank
            + 0.15 * return_rank
            + 0.10 * (1.0 - duplicate_rank)
        )
        learned_fit = metrics.get("learned_profile_fit")
        edge = 100.0 * (
            0.95 * base_edge + 0.05 * (_number(learned_fit, 0.0) / 100.0)
            if config.get("learned") and learned_fit is not None
            else base_edge
        )
        metrics["duplicate_risk"] = duplicate_rank * 100.0
        metrics["sim_return_index"] = return_rank * 100.0
        metrics["sim_leverage"] = 100.0 * (0.58 * top_rank + 0.42 * (1.0 - duplicate_rank))
        metrics["sim_edge"] = max(0.0, min(100.0, edge))
        metrics["sim_edge_components"] = {
            "Top-1% outcomes": round(top_rank * 100.0, 1),
            "Representative wins": round(win_rank * 100.0, 1),
            "Top-5% outcomes": round(top_five_rank * 100.0, 1),
            "Ceiling": round(ceiling_rank * 100.0, 1),
            "Tournament return": round(return_rank * 100.0, 1),
            "Duplication safety": round((1.0 - duplicate_rank) * 100.0, 1),
        }
        component_weights = {
            "Top-1% outcomes": 0.32,
            "Representative wins": 0.12,
            "Top-5% outcomes": 0.16,
            "Ceiling": 0.15,
            "Tournament return": 0.15,
            "Duplication safety": 0.10,
        }
        ranked_drivers = sorted(
            metrics["sim_edge_components"].items(),
            key=lambda item: abs(item[1] - 50.0) * component_weights[item[0]],
            reverse=True,
        )
        metrics["sim_edge_drivers"] = [
            {
                "label": label,
                "percentile": percentile,
                "weight_pct": int(round(component_weights[label] * 100.0)),
                "direction": "helping" if percentile >= 60.0 else ("hurting" if percentile <= 40.0 else "neutral"),
            }
            for label, percentile in ranked_drivers
        ]
        metrics["sim_scenarios"] = completed
        metrics["sim_field_lineups"] = len(field_lineups)
        wrapped.append(SimLineup(
            lineup,
            metrics=metrics,
            top_hits=top_hits[index],
            top_five_hits=top_five_hits[index],
            win_hits=win_hits[index],
            scenario_values=scenario_values[index],
        ))

    wrapped.sort(
        key=lambda lineup: (
            _number(lineup.sim_metrics.get("sim_edge"), 0.0),
            _number(lineup.sim_metrics.get("sim_top_one_pct"), 0.0),
        ),
        reverse=True,
    )
    real_reference = dict(config.get("real_field_reference") or {})
    field_comparison: Dict[str, Any] = {
        "available": bool(real_reference),
        "preset": str(config.get("name") or "Custom"),
        "simulated": generated_field_summary,
        "real": real_reference,
        "report_only": not bool(config.get("learned")),
    }
    field_model_preset_comparison = compare_nfl_lineups_to_preset(
        field_lineups, config, salary_cap=salary_cap
    )
    if real_reference:
        real_profile = dict((real_reference.get("ownership_profile") or {}).get("field") or {})
        sim_profile = dict(generated_field_summary.get("ownership_profile") or {})
        differences: Dict[str, float] = {}
        if real_reference.get("duplicate_entry_pct") is not None:
            differences["duplicate_entry_pct"] = (
                _number(generated_field_summary.get("duplicate_entry_pct"), 0.0)
                - _number(real_reference.get("duplicate_entry_pct"), 0.0)
            )
        for key in (
            "avg_total_ownership", "avg_sub_five_players", "avg_sub_ten_players",
            "avg_twenty_plus_players", "avg_thirty_plus_players",
        ):
            if real_profile.get(key) is not None and sim_profile.get(key) is not None:
                differences[key] = _number(sim_profile.get(key), 0.0) - _number(real_profile.get(key), 0.0)
        field_comparison["differences"] = differences
    return {
        "lineups": wrapped,
        "report": {
            "scenarios": completed,
            "field_lineups": len(field_lineups),
            "field_size": effective_field_size,
            "role_pool_size": len(role_pool),
            "candidate_count": len(wrapped),
            "field_preset": str(config.get("name") or "Custom"),
            "learned_field_model": bool(config.get("learned")),
            "learned_entries": int(config.get("learned_entries", 0) or 0),
            "field_comparison": field_comparison,
            "field_model_preset_comparison": field_model_preset_comparison,
            "model": "scenario-portfolio-v3",
        },
    }
