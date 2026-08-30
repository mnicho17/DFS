# optimizers.py
from __future__ import annotations

import logging
import random
import math
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from collections import Counter

logger = logging.getLogger("dfs.opt")

try:
    import pulp  # type: ignore

    HAS_PULP = True
except Exception:
    HAS_PULP = False


# ---------------- Shared helpers ----------------

def _pkey(p: Dict[str, Any]) -> str:
    return (
        str(p.get("FlexNamePlusID") or "").strip()
        or str(p.get("FlexID") or "").strip()
        or str(p.get("Name") or "").strip()
    )


def _max_count_from_pct(pct: Optional[float], total_lineups: int) -> Optional[int]:
    """Convert a percent cap into a max appearance count for a multi-lineup build.

    Important: a positive cap should allow at least one lineup. The previous
    floor-only behavior turned low simulated ownership values, like 0.4%, into
    a hard zero-appearance cap when copied into Max%. That could make MLB pools
    artificially infeasible or leave assignment/display holes after tight caps.
    Use explicit 0 only when the user truly wants to block a player.
    """
    if pct is None:
        return None
    try:
        f = float(pct)
    except Exception:
        return None
    f = max(0.0, min(100.0, f))
    if f <= 0.0:
        return 0
    raw_count = int(math.floor((f / 100.0) * float(max(1, total_lineups))))
    return max(1, raw_count)


def _build_showdown_cap_maps(players: List[Dict[str, Any]], total_lineups: int) -> Tuple[Dict[str, int], Dict[str, int]]:
    """Return (captain_cap_map, flex_cap_map) keyed by _pkey."""
    cap_cpt: Dict[str, int] = {}
    cap_flex: Dict[str, int] = {}
    for p in players:
        pk = _pkey(p)
        mc = _max_count_from_pct(p.get("MaxCptPct", None), total_lineups)
        # Showdown FLEX cap uses unified MaxPct (legacy MaxFlexPct supported)
        legacy_flex = p.get("MaxFlexPct", None)
        mf = _max_count_from_pct(
            legacy_flex if legacy_flex not in (None, "") else p.get("MaxPct", None),
            total_lineups,
        )
        if mc is not None:
            cap_cpt[pk] = mc
        if mf is not None:
            cap_flex[pk] = mf
    return cap_cpt, cap_flex


def _salary(p: Dict[str, Any]) -> float:
    return float(p.get("FlexSalary", 0.0) or 0.0)


def _team_adj_multiplier(p: Dict[str, Any]) -> float:
    try:
        pct = float(p.get("TeamAdjPct", 0.0) or 0.0)
    except Exception:
        pct = 0.0
    return max(0.05, 1.0 + pct / 100.0)


def _proj(p: Dict[str, Any]) -> float:
    base = float(p.get("FlexProjection", 0.0) or 0.0) * _team_adj_multiplier(p)
    return base + float(p.get("_PortfolioCandidateBoost", 0.0) or 0.0)


def _cpt_salary(p: Dict[str, Any]) -> float:
    return float(p.get("CptSalary", 1.5 * _salary(p)) or 0.0)


def _cpt_proj(p: Dict[str, Any]) -> float:
    raw = float(p.get("CptProjection", 1.5 * float(p.get("FlexProjection", 0.0) or 0.0)) or 0.0)
    return raw * _team_adj_multiplier(p) + float(p.get("_PortfolioCptCandidateBoost", 0.0) or 0.0)


def _own(p: Dict[str, Any]) -> float:
    """Simulated ownership percent (0..100)."""
    try:
        return float(p.get("ProjOwnPct", 0.0) or 0.0)
    except Exception:
        return 0.0


def _showdown_cpt_own(p: Dict[str, Any]) -> float:
    """Captain ownership when available, with total ownership as a safe fallback."""
    try:
        value = p.get("ProjCptOwnPct", None)
        if value not in (None, ""):
            return float(value)
    except Exception:
        pass
    return _own(p)


def _showdown_flex_own(p: Dict[str, Any]) -> float:
    """FLEX-specific ownership when available, with total ownership as fallback."""
    try:
        value = p.get("ProjFlexOwnPct", None)
        if value not in (None, ""):
            return float(value)
    except Exception:
        pass
    return _own(p)


def _own_sign(mode: str) -> float:
    """Return sign for ownership term: + for chalk, - for leverage."""
    m = (mode or "").strip().lower()
    if m in ("chalk", "high", "field", "popular"):
        return +1.0
    if m in ("leverage", "contrarian", "low"):
        return -1.0
    # "balanced" defaults to a mild leverage tilt
    return -0.5


def _style_level(build_style: str) -> float:
    """Strategic-template strength.

    0.0 = mostly projection/randomized optimizer
    1.0 = standard strategic
    >1.0 = stronger template bias
    """
    s = (build_style or "Strategic").strip().lower()
    if s in ("randomized", "random", "semi-random", "semi random", "projection"):
        return 0.0
    if s in ("balanced", "standard"):
        return 0.65
    if s in ("contrarian", "leverage"):
        return 0.85
    if s in ("chalk", "optimal"):
        return 0.75
    return 1.0


def _team(p: Dict[str, Any]) -> str:
    return str(p.get("Team", "") or "").strip().upper()


def _game_key(p: Dict[str, Any]) -> str:
    return str(p.get("GameKey") or p.get("GameInfo", "") or "").strip().upper()


def _nfl_opponent(p: Dict[str, Any]) -> str:
    """Return opponent from parsed DK context, with a GameKey fallback."""
    opp = str(p.get("Opponent") or "").strip().upper()
    if opp:
        return opp
    team = _team(p)
    game = _game_key(p)
    if "@" in game:
        away, home = [x.strip().upper() for x in game.split("@", 1)]
        # GameInfo fallback may contain date/time after the home team.
        home = home.split()[0] if home else home
        if team == away:
            return home
        if team == home:
            return away
    return ""


def _nfl_style_profile(build_style: str) -> Dict[str, Any]:
    """Internal NFL construction defaults mapped onto the existing Build Style UI."""
    s = (build_style or "Strategic").strip().lower()
    if s in ("randomized", "random", "semi-random", "semi random", "projection"):
        return {"required_qb_stack": 0, "max_team": 5, "max_game": 6, "min_unique": 2}
    if s in ("chalk", "optimal"):
        return {"required_qb_stack": 1, "max_team": 5, "max_game": 6, "min_unique": 1}
    if s in ("contrarian", "leverage"):
        return {"required_qb_stack": 1, "max_team": 4, "max_game": 5, "min_unique": 3}
    if s in ("balanced", "standard"):
        return {"required_qb_stack": 1, "max_team": 4, "max_game": 5, "min_unique": 2}
    return {"required_qb_stack": 1, "max_team": 4, "max_game": 5, "min_unique": 2}


def _nfl_lineup_features(lineup: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarize the NFL correlations that matter for Classic tournament builds."""
    players = list(lineup or [])
    qbs = [p for p in players if "QB" in _position_tokens(p)]
    dsts = [p for p in players if "DST" in _position_tokens(p)]
    qb = qbs[0] if qbs else None

    team_counts = Counter(_team(p) for p in players if _team(p))
    game_counts = Counter(_game_key(p) for p in players if _game_key(p))
    max_team = max(team_counts.values()) if team_counts else 0
    max_game = max(game_counts.values()) if game_counts else 0

    stack_count = 0
    bringback_count = 0
    qb_vs_opp_dst = False
    qb_game_count = 0
    qb_team = ""
    qb_opp = ""
    qb_game = ""
    if qb is not None:
        qb_team = _team(qb)
        qb_opp = _nfl_opponent(qb)
        qb_game = _game_key(qb)
        stack_count = sum(
            1 for p in players
            if p is not qb
            and _team(p) == qb_team
            and bool(_position_tokens(p) & {"WR", "TE"})
        )
        bringback_count = sum(
            1 for p in players
            if qb_opp
            and _team(p) == qb_opp
            and bool(_position_tokens(p) & {"RB", "WR", "TE"})
        )
        qb_vs_opp_dst = any(qb_opp and _team(d) == qb_opp for d in dsts)
        qb_game_count = sum(1 for p in players if qb_game and _game_key(p) == qb_game)

    rb_dst_pairs = 0
    for d in dsts:
        dt = _team(d)
        rb_dst_pairs += sum(1 for p in players if _team(p) == dt and "RB" in _position_tokens(p))

    same_team_rb_pairs = 0
    for t in set(_team(p) for p in players if _team(p)):
        rbs = sum(1 for p in players if _team(p) == t and "RB" in _position_tokens(p))
        if rbs >= 2:
            same_team_rb_pairs += (rbs * (rbs - 1)) // 2

    stack_score = 30.0
    if stack_count >= 1:
        stack_score += 24.0
    if stack_count >= 2:
        stack_score += 12.0
    if bringback_count >= 1:
        stack_score += 18.0
    if stack_count >= 2 and bringback_count >= 1:
        stack_score += 8.0
    if rb_dst_pairs:
        stack_score += min(10.0, 7.0 * rb_dst_pairs)
    if 4 <= qb_game_count <= 5:
        stack_score += 5.0
    elif qb_game_count >= 6:
        stack_score -= 7.0
    if same_team_rb_pairs:
        stack_score -= 8.0 * same_team_rb_pairs
    if qb_vs_opp_dst:
        stack_score -= 45.0
    if max_team >= 5:
        stack_score -= 10.0 * (max_team - 4)
    stack_score = max(0.0, min(100.0, stack_score))

    return {
        "qb_team": qb_team,
        "qb_opponent": qb_opp,
        "qb_game": qb_game,
        "qb_stack": stack_count,
        "bringback": bringback_count,
        "qb_vs_opp_dst": qb_vs_opp_dst,
        "rb_dst_pairs": rb_dst_pairs,
        "same_team_rb_pairs": same_team_rb_pairs,
        "qb_game_count": qb_game_count,
        "max_team": max_team,
        "max_game": max_game,
        "stack_score": stack_score,
        "stack_shape": f"QB+{stack_count} / BB{bringback_count}" if qb is not None else "no QB",
    }


def _nfl_lineup_is_acceptable(lineup: List[Dict[str, Any]], build_style: str) -> bool:
    """Hard safety rails; strategic preferences beyond these remain soft scores."""
    f = _nfl_lineup_features(lineup)
    profile = _nfl_style_profile(build_style)
    if f.get("qb_team", "") == "":
        return False
    # QB against the opposing DST is sufficiently anti-correlated to reject in all presets.
    if bool(f.get("qb_vs_opp_dst")):
        return False
    if int(f.get("qb_stack", 0) or 0) < int(profile.get("required_qb_stack", 0) or 0):
        return False
    if int(f.get("max_team", 0) or 0) > int(profile.get("max_team", 99) or 99):
        return False
    if int(f.get("max_game", 0) or 0) > int(profile.get("max_game", 99) or 99):
        return False
    return True


def _is_mlb_pitcher(p: Dict[str, Any]) -> bool:
    return bool(_position_tokens(p) & {"P", "SP", "RP"})


def _is_mlb_hitter(p: Dict[str, Any]) -> bool:
    return not _is_mlb_pitcher(p)


def _batting_order_bonus(p: Dict[str, Any]) -> float:
    """Small DFS strategy boost/penalty for MLB confirmed batting order.

    This is intentionally modest: it helps 1-5 hitters and confirmed starters
    rise in otherwise-close decisions without turning lineup order into a hard rule.
    """
    try:
        order = int(p.get("BattingOrder", 0) or 0)
    except Exception:
        order = 0
    bonus = 0.0
    if order in (1, 2, 3):
        bonus += 1.15
    elif order in (4, 5):
        bonus += 0.75
    elif order == 6:
        bonus += 0.15
    elif order in (7, 8, 9):
        bonus -= 0.45
    if p.get("ConfirmedLineup"):
        bonus += 0.25
    return bonus


def _is_nba_ball_handler_or_wing(p: Dict[str, Any]) -> bool:
    return bool(_position_tokens(p) & {"PG", "SG", "SF", "PF", "C", "G", "F"})


# ---------------- Showdown ----------------


def _showdown_number(player: Dict[str, Any], key: str) -> float:
    try:
        return float(player.get(key, 0.0) or 0.0)
    except Exception:
        return 0.0


def _showdown_player_script_bonus(player: Dict[str, Any], *, captain: bool = False) -> float:
    """Small Vegas-aware role preference for a single-game lineup slot."""
    total = _showdown_number(player, "NFLVegasGameTotal")
    spread = _showdown_number(player, "NFLVegasSpread")
    position = _position_tokens(player)
    bonus = 0.0

    if total >= 48.0:
        scale = min(1.0, (total - 46.0) / 8.0)
        if "QB" in position:
            bonus += 0.18 * scale
        elif position & {"WR", "TE"}:
            bonus += 0.24 * scale
        elif position & {"K", "DST"}:
            bonus -= 0.12 * scale
    elif 0.0 < total <= 43.0:
        scale = min(1.0, (45.0 - total) / 8.0)
        if "RB" in position:
            bonus += 0.22 * scale
        elif "K" in position:
            bonus += 0.20 * scale
        elif "DST" in position:
            bonus += 0.24 * scale
        elif position & {"QB", "WR", "TE"}:
            bonus -= 0.08 * scale

    if spread <= -3.0:
        scale = min(1.0, (abs(spread) - 2.0) / 8.0)
        if "RB" in position:
            bonus += 0.20 * scale
        elif position & {"K", "DST"}:
            bonus += 0.16 * scale
    elif spread >= 3.0:
        scale = min(1.0, (spread - 2.0) / 8.0)
        if position & {"QB", "WR", "TE"}:
            bonus += 0.20 * scale
        elif "RB" in position:
            bonus -= 0.08 * scale
        elif "DST" in position:
            bonus -= 0.18 * scale

    return bonus * (1.20 if captain else 1.0)


def _showdown_pair_script_bonus(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    """Reward pairs that tell the same likely game story."""
    pa = _position_tokens(a)
    pb = _position_tokens(b)
    ta, tb = _team(a), _team(b)
    if not ta or not tb:
        return 0.0
    total_values = [
        value for value in (
            _showdown_number(a, "NFLVegasGameTotal"),
            _showdown_number(b, "NFLVegasGameTotal"),
        ) if value > 0
    ]
    total = sum(total_values) / len(total_values) if total_values else 0.0
    bonus = 0.0

    a_qb, b_qb = "QB" in pa, "QB" in pb
    a_receiver, b_receiver = bool(pa & {"WR", "TE"}), bool(pb & {"WR", "TE"})
    # Key-free correlation baseline. Live totals can refine these preferences,
    # but ordinary salary files should still create coherent Showdown stories.
    if ta == tb and ((a_qb and b_receiver) or (b_qb and a_receiver)):
        bonus += 0.22
    elif ta != tb and ((a_qb and b_receiver) or (b_qb and a_receiver)):
        bonus += 0.07
    if ta == tb and (("RB" in pa and "DST" in pb) or ("RB" in pb and "DST" in pa)):
        bonus += 0.12
    if ta != tb and (("RB" in pa and "DST" in pb) or ("RB" in pb and "DST" in pa)):
        bonus -= 0.14
    if total >= 47.0:
        scale = min(1.0, (total - 45.0) / 9.0)
        if ta == tb and ((a_qb and b_receiver) or (b_qb and a_receiver)):
            bonus += 0.34 * scale
        elif ta != tb and ((a_qb and b_receiver) or (b_qb and a_receiver)):
            bonus += 0.12 * scale

    if 0.0 < total <= 44.0 and ta == tb:
        scale = min(1.0, (46.0 - total) / 9.0)
        if ("RB" in pa and bool(pb & {"K", "DST"})) or ("RB" in pb and bool(pa & {"K", "DST"})):
            bonus += 0.30 * scale
        elif ("K" in pa and "DST" in pb) or ("K" in pb and "DST" in pa):
            bonus += 0.12 * scale

    return bonus


def _showdown_split_bonus_for_count(
    focus_team_count: int,
    focus_team: str,
    reference_players: List[Dict[str, Any]],
) -> float:
    """Score a two-team Showdown split, leaning toward the Vegas favorite."""
    count = max(0, min(6, int(focus_team_count)))
    split = tuple(sorted((count, 6 - count), reverse=True))
    bonus = {
        (4, 2): 1.55,
        (3, 3): 1.35,
        (5, 1): 0.30,
        (6, 0): -2.0,
    }.get(split, 0.0)

    team_spreads: Dict[str, List[float]] = {}
    totals: List[float] = []
    for player in reference_players or []:
        team = _team(player)
        spread = _showdown_number(player, "NFLVegasSpread")
        total = _showdown_number(player, "NFLVegasGameTotal")
        if team and spread != 0.0:
            team_spreads.setdefault(team, []).append(spread)
        if total > 0.0:
            totals.append(total)

    favorite_count: Optional[int] = None
    margin_scale = 0.0
    if team_spreads:
        average_spread = {team: sum(values) / len(values) for team, values in team_spreads.items()}
        favorite = min(average_spread, key=average_spread.get)
        favorite_spread = average_spread[favorite]
        margin_scale = min(1.0, max(0.0, (abs(favorite_spread) - 2.0) / 8.0))
        favorite_count = count if focus_team == favorite else 6 - count
        if favorite_count == 4:
            bonus += 0.35 + 0.35 * margin_scale
        elif favorite_count == 5:
            bonus += -0.05 + 0.65 * margin_scale
        elif favorite_count <= 2:
            bonus -= 0.45 * margin_scale

    game_total = sum(totals) / len(totals) if totals else 0.0
    if game_total >= 49.0 and split == (3, 3):
        bonus += 0.20
    elif game_total >= 49.0 and split == (4, 2):
        bonus += 0.10
    elif 0.0 < game_total <= 42.0 and favorite_count == 5:
        bonus += 0.20 * margin_scale
    return bonus


def _showdown_lineup_script_bonus(captain: Dict[str, Any], flex: List[Dict[str, Any]]) -> float:
    players = [captain] + list(flex or [])
    teams = sorted({_team(player) for player in players if _team(player)})
    if len(teams) == 2:
        focus = teams[0]
        split_bonus = _showdown_split_bonus_for_count(
            sum(1 for player in players if _team(player) == focus),
            focus,
            players,
        )
    else:
        counts = sorted(Counter(_team(player) for player in players if _team(player)).values(), reverse=True)
        split_bonus = {
            (4, 2): 1.55,
            (3, 3): 1.35,
            (5, 1): 0.30,
        }.get(tuple(counts), -2.0 if tuple(counts) == (6,) else 0.0)

    pair_bonus = 0.0
    for index, player in enumerate(players):
        for other in players[index + 1:]:
            pair_bonus += _showdown_pair_script_bonus(player, other)
    return split_bonus + pair_bonus


class ShowdownLineup(dict):
    """Dict-compatible lineup carrying tournament-selection metadata."""

    def __init__(self, captain: Dict[str, Any], flex: List[Dict[str, Any]]):
        super().__init__(Captain=captain, Flex=list(flex))
        self.sim_metrics: Dict[str, Any] = {}
        self.candidate_source = "showdown_optimizer"
        self.candidate_archetype = ""
        self.sim_top_hits: set[int] = set()


def showdown_lineup_archetype(captain: Dict[str, Any], flex: List[Dict[str, Any]]) -> str:
    """Describe the lineup's primary construction without requiring betting data."""
    players = [captain] + list(flex or [])
    positions = Counter(
        next(iter(_position_tokens(player)), "") for player in players
    )
    team_counts = Counter(_team(player) for player in players if _team(player))
    split = tuple(sorted(team_counts.values(), reverse=True))
    captain_pos = next(iter(_position_tokens(captain)), "")
    captain_team = _team(captain)
    same_team_receivers = sum(
        1 for player in flex
        if _team(player) == captain_team and _position_tokens(player) & {"WR", "TE"}
    )
    same_team_qb = any(
        _team(player) == captain_team and "QB" in _position_tokens(player)
        for player in flex
    )
    if captain_pos in {"DST", "K"} or positions["DST"] + positions["K"] >= 3:
        return "Defensive"
    if split == (5, 1):
        return "Onslaught"
    if captain_pos == "QB" and same_team_receivers >= 1:
        return "Passing Stack"
    if captain_pos in {"WR", "TE"} and same_team_qb:
        return "Receiver Captain"
    if captain_pos == "RB":
        return "Rushing Control"
    return "Balanced"


def attach_showdown_metrics(lineups: List[Dict[str, Any]], salary_cap: float = 50000.0) -> List[Dict[str, Any]]:
    """Attach relative quality, leverage, construction, and duplication estimates."""
    prepared: List[ShowdownLineup] = []
    raw_quality: List[float] = []
    for raw in lineups or []:
        captain = raw.get("Captain") or {}
        flex = list(raw.get("Flex") or [])
        lineup = raw if isinstance(raw, ShowdownLineup) else ShowdownLineup(captain, flex)
        archetype = showdown_lineup_archetype(captain, flex)
        salary = _cpt_salary(captain) + sum(_salary(player) for player in flex)
        salary_left = max(0.0, float(salary_cap) - salary)
        cpt_own = max(0.0, _showdown_cpt_own(captain))
        flex_owns = [max(0.0, _showdown_flex_own(player)) for player in flex]
        total_own = cpt_own + sum(flex_owns)
        projection = _cpt_proj(captain) + sum(_proj(player) for player in flex)
        correlation = _showdown_lineup_script_bonus(captain, flex)
        # Chalk Captain + full salary + high cumulative ownership are the main
        # practical duplication signals available without a historical field.
        duplication = (
            0.75 * cpt_own
            + 0.16 * total_own
            + max(0.0, 18.0 - salary_left / 125.0)
            + (7.0 if archetype in {"Balanced", "Passing Stack"} else 2.0)
        )
        duplication = max(0.0, min(100.0, duplication))
        leverage = max(0.0, min(100.0, 70.0 - 0.65 * cpt_own - 0.10 * total_own + salary_left / 180.0))
        quality = projection + 0.9 * correlation + 0.05 * leverage - 0.045 * duplication
        lineup.candidate_archetype = archetype
        lineup.sim_metrics = {
            "candidate_source": "showdown_optimizer",
            "candidate_archetype": archetype,
            "showdown_projection": projection,
            "showdown_cpt_ownership": cpt_own,
            "showdown_total_ownership": total_own,
            "showdown_salary_left": salary_left,
            "showdown_correlation": correlation,
            "sim_leverage": leverage,
            "duplicate_risk": duplication,
        }
        prepared.append(lineup)
        raw_quality.append(quality)

    ordered = sorted(raw_quality)
    for lineup, quality in zip(prepared, raw_quality):
        rank = sum(value <= quality for value in ordered) / max(1, len(ordered)) * 100.0
        lineup.sim_metrics["sim_edge"] = round(rank, 3)
        lineup.sim_metrics["sim_return_index"] = round(
            max(0.0, min(100.0, 0.70 * rank + 0.30 * lineup.sim_metrics["sim_leverage"])), 3
        )
    return list(prepared)


class ShowdownOptimizer:
    """DK Showdown

    - 1 CPT + 5 FLEX
    - salary cap
    - maximize (projection + optional ownership term)

    Tags:
      - LockFlex/FadeFlex apply to FLEX slot eligibility
      - LockCpt/FadeCpt apply to CPT slot eligibility

    Ownership controls:
      - own_mode: 'Balanced' | 'Leverage' | 'Chalk'
      - own_weight: strength of ownership term in the objective
    """

    def __init__(
        self,
        players: List[Dict[str, Any]],
        salary_cap: float = 50000.0,
        seed: int = 1337,
        *,
        own_mode: str = "Balanced",
        own_weight: float = 0.0,
        build_style: str = "Strategic",
    ):
        self.players = [p for p in players if _salary(p) > 0 or _cpt_salary(p) > 0]
        self.salary_cap = float(salary_cap)
        self.rng = random.Random(seed)
        self.own_mode = own_mode
        self.own_weight = float(own_weight or 0.0)
        self.build_style = build_style or "Strategic"

    def build_lineups(
        self,
        num_lineups: int = 10,
        *,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        cancel_callback: Optional[Callable[[], bool]] = None,
    ) -> List[Dict[str, Any]]:
        if not self.players:
            return []

        num_lineups = max(1, int(num_lineups or 1))

        showdown_teams = sorted({_team(player) for player in self.players if _team(player)})
        showdown_games = sorted({_game_key(player) for player in self.players if _game_key(player)})
        if len(showdown_teams) != 2:
            logger.info(
                "Showdown build requires exactly two teams; the loaded pool has %d.",
                len(showdown_teams),
            )
            return []
        if len(showdown_games) > 1:
            logger.info(
                "Showdown build requires one game; the loaded pool has %d game keys.",
                len(showdown_games),
            )
            return []

        locked = [p for p in self.players if p.get("LockCpt")]
        if len(locked) > 1:
            names = ", ".join(str(p.get("Name")) for p in locked[:10])
            raise ValueError(f"Multiple CPT locks set (only 1 CPT allowed). Locked: {names}")

        if locked:
            logger.info("CPT LOCK ACTIVE: %s | key=%s", locked[0].get("Name", ""), _pkey(locked[0]))

        # Rebuilding a CBC model once per lineup becomes progressively slower as
        # no-good constraints accumulate. Use a randomized candidate portfolio
        # for larger requests; small requests retain the exact solver.
        if num_lineups > 20:
            logger.info("Using fast showdown portfolio builder for %d lineups.", num_lineups)
            return self._build_lineups_fast(
                num_lineups=num_lineups,
                progress_callback=progress_callback,
                cancel_callback=cancel_callback,
            )

        if HAS_PULP:
            return self._build_lineups_pulp(
                num_lineups=num_lineups,
                progress_callback=progress_callback,
                cancel_callback=cancel_callback,
            )
        logger.warning("PuLP not installed; using fast fallback for showdown (tags honored).")
        return self._build_lineups_fast(
            num_lineups=num_lineups,
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
        )

    @staticmethod
    def _cancelled(cancel_callback: Optional[Callable[[], bool]]) -> bool:
        if cancel_callback is None:
            return False
        try:
            return bool(cancel_callback())
        except Exception:
            logger.debug("Showdown cancellation callback failed.", exc_info=True)
            return False

    @staticmethod
    def _report_progress(
        progress_callback: Optional[Callable[[int, int, str], None]],
        done: int,
        total: int,
        text: str,
    ) -> None:
        if progress_callback is None:
            return
        try:
            progress_callback(done, total, text)
        except Exception:
            logger.debug("Showdown progress callback failed.", exc_info=True)

    def _build_lineups_pulp(
        self,
        num_lineups: int,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        cancel_callback: Optional[Callable[[], bool]] = None,
    ) -> List[Dict[str, Any]]:
        prev: List[Tuple[str, Tuple[str, ...]]] = []
        out: List[Dict[str, Any]] = []

        keys = [_pkey(p) for p in self.players]
        key_to_player = {_pkey(p): p for p in self.players}

        cap_cpt, cap_flex = _build_showdown_cap_maps(self.players, num_lineups)
        used_cpt: Dict[str, int] = {}
        used_flex: Dict[str, int] = {}

        # Ownership settings
        own_s = _own_sign(self.own_mode)
        # Slightly stronger ownership shaping at CPT than FLEX (industry-ish)
        w_cpt = self.own_weight * 1.35 * own_s
        w_flex = self.own_weight * 1.00 * own_s
        style = _style_level(self.build_style)

        self._report_progress(progress_callback, 0, num_lineups, "Optimizing showdown portfolio")
        for k in range(num_lineups):
            if self._cancelled(cancel_callback):
                logger.info("Showdown build cancelled at %d/%d.", len(out), num_lineups)
                break
            prob = pulp.LpProblem(f"showdown_{k}", pulp.LpMaximize)

            cpt = pulp.LpVariable.dicts("cpt", keys, lowBound=0, upBound=1, cat="Binary")
            flx = pulp.LpVariable.dicts("flx", keys, lowBound=0, upBound=1, cat="Binary")

            # Base objective
            obj = pulp.lpSum(
                [
                    cpt[pk] * (
                        _cpt_proj(key_to_player[pk])
                        + style * _showdown_player_script_bonus(key_to_player[pk], captain=True)
                    )
                    + flx[pk] * (
                        _proj(key_to_player[pk])
                        + style * _showdown_player_script_bonus(key_to_player[pk])
                    )
                    for pk in keys
                ]
            )

            # Ownership term (soft)
            if self.own_weight and abs(self.own_weight) > 1e-9:
                obj += pulp.lpSum(
                    [
                        cpt[pk] * (w_cpt * _showdown_cpt_own(key_to_player[pk]))
                        + flx[pk] * (w_flex * _showdown_flex_own(key_to_player[pk]))
                        for pk in keys
                    ]
                )

            # Strategic Showdown bias uses the actual spread/total when present.
            # It remains soft so projections, locks, fades, and salary still lead.
            if style > 0:
                teams = sorted({_team(player) for player in self.players if _team(player)})
                if len(teams) == 2:
                    focus_team = teams[0]
                    team_count = pulp.lpSum([
                        cpt[pk] + flx[pk]
                        for pk in keys if _team(key_to_player[pk]) == focus_team
                    ])
                    split_choice = pulp.LpVariable.dicts(f"sd_split_{k}", list(range(7)), 0, 1, cat="Binary")
                    prob += pulp.lpSum([split_choice[count] for count in range(7)]) == 1
                    prob += team_count == pulp.lpSum([count * split_choice[count] for count in range(7)])
                    obj += pulp.lpSum([
                        split_choice[count]
                        * style
                        * _showdown_split_bonus_for_count(count, focus_team, self.players)
                        for count in range(7)
                    ])

                scripted_pairs = []
                for a_i, a in enumerate(keys):
                    for b in keys[a_i + 1:]:
                        pair_bonus = _showdown_pair_script_bonus(key_to_player[a], key_to_player[b])
                        if abs(pair_bonus) > 1e-9:
                            scripted_pairs.append((a, b, pair_bonus))
                if scripted_pairs:
                    ypair = pulp.LpVariable.dicts(f"sd_pair_{k}", list(range(len(scripted_pairs))), 0, 1, cat="Binary")
                    for idx, (a, b, _) in enumerate(scripted_pairs):
                        a_used = cpt[a] + flx[a]
                        b_used = cpt[b] + flx[b]
                        prob += ypair[idx] <= a_used
                        prob += ypair[idx] <= b_used
                        prob += ypair[idx] >= a_used + b_used - 1
                    obj += pulp.lpSum([
                        ypair[index] * (pair_bonus * style)
                        for index, (_, _, pair_bonus) in enumerate(scripted_pairs)
                    ])

                # Tiny jitter so repeated lineups are not just strictly deterministic after no-good cuts.
                obj += pulp.lpSum([
                    (cpt[pk] + flx[pk]) * self.rng.uniform(-0.015, 0.015)
                    for pk in keys
                ])

            prob += obj

            prob += pulp.lpSum([cpt[pk] for pk in keys]) == 1
            prob += pulp.lpSum([flx[pk] for pk in keys]) == 5

            for pk in keys:
                prob += cpt[pk] + flx[pk] <= 1

            # DraftKings Showdown lineups must include both teams. Keep this a
            # hard platform-validity rule even when the build style is Randomized.
            for team_name in sorted({_team(player) for player in self.players if _team(player)}):
                team_keys = [pk for pk in keys if _team(key_to_player[pk]) == team_name]
                prob += pulp.lpSum([cpt[pk] + flx[pk] for pk in team_keys]) >= 1

            prob += (
                pulp.lpSum(
                    [
                        cpt[pk] * _cpt_salary(key_to_player[pk])
                        + flx[pk] * _salary(key_to_player[pk])
                        for pk in keys
                    ]
                )
                <= self.salary_cap
            )

            # Tags
            for pk in keys:
                p = key_to_player[pk]
                if p.get("FadeFlex"):
                    prob += flx[pk] == 0
                if p.get("FadeCpt"):
                    prob += cpt[pk] == 0
                if p.get("LockFlex"):
                    prob += flx[pk] == 1
                if p.get("LockCpt"):
                    prob += cpt[pk] == 1
                    prob += flx[pk] == 0

            # Ownership caps (Showdown only)
            for pk in keys:
                mx_cpt = cap_cpt.get(pk, None)
                if mx_cpt is not None and used_cpt.get(pk, 0) >= mx_cpt:
                    prob += cpt[pk] == 0

                mx_flex = cap_flex.get(pk, None)
                if mx_flex is not None and used_flex.get(pk, 0) >= mx_flex:
                    prob += flx[pk] == 0

            # Uniqueness (avoid exact same 6-man set)
            for prev_cpt, prev_flex in prev:
                prob += (cpt[prev_cpt] + pulp.lpSum([flx[pk] for pk in prev_flex])) <= 5

            status = prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=4))
            if pulp.LpStatus[status] != "Optimal":
                logger.info(
                    "Showdown stopped early at %d/%d (status=%s)",
                    k,
                    num_lineups,
                    pulp.LpStatus[status],
                )
                break

            cpt_key = next(pk for pk in keys if pulp.value(cpt[pk]) > 0.5)
            flex_keys = tuple(sorted([pk for pk in keys if pulp.value(flx[pk]) > 0.5]))

            used_cpt[cpt_key] = used_cpt.get(cpt_key, 0) + 1
            for pk in flex_keys:
                used_flex[pk] = used_flex.get(pk, 0) + 1

            prev.append((cpt_key, flex_keys))
            out.append(ShowdownLineup(key_to_player[cpt_key], [key_to_player[pk] for pk in flex_keys]))
            self._report_progress(progress_callback, len(out), num_lineups, "Optimizing showdown portfolio")

        return out

    def _build_lineups_fast(
        self,
        num_lineups: int,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        cancel_callback: Optional[Callable[[], bool]] = None,
    ) -> List[Dict[str, Any]]:
        """Build a large unique showdown portfolio without one CBC solve per lineup.

        Each portfolio slot samples a batch of salary-feasible candidates, scores
        them with projection/ownership inputs plus a conservative team-split
        preference, then accepts the best candidate that still fits all portfolio
        exposure caps. Locks, fades, max percentages, and salary remain hard rules.
        """
        key_to_player: Dict[str, Dict[str, Any]] = {}
        for player in self.players:
            key = _pkey(player)
            if key and key not in key_to_player:
                key_to_player[key] = player
        players = list(key_to_player.values())
        player_key = {id(player): _pkey(player) for player in players}
        flex_salary = {id(player): _salary(player) for player in players}
        captain_salary = {id(player): _cpt_salary(player) for player in players}

        cap_cpt, cap_flex = _build_showdown_cap_maps(players, num_lineups)
        used_cpt: Dict[str, int] = {}
        used_flex: Dict[str, int] = {}
        used_signatures: set[Tuple[str, Tuple[str, ...]]] = set()
        out: List[Dict[str, Any]] = []

        own_s = _own_sign(self.own_mode)
        w_cpt = self.own_weight * 1.35 * own_s
        w_flex = self.own_weight * own_s
        style = _style_level(self.build_style)
        captain_scores = {
            id(player): (
                _cpt_proj(player)
                + w_cpt * _showdown_cpt_own(player)
                + style * _showdown_player_script_bonus(player, captain=True)
            )
            for player in players
        }
        flex_scores = {
            id(player): (
                _proj(player)
                + w_flex * _showdown_flex_own(player)
                + style * _showdown_player_script_bonus(player)
            )
            for player in players
        }
        pair_scores = {
            tuple(sorted((id(first), id(second)))): _showdown_pair_script_bonus(first, second)
            for index, first in enumerate(players)
            for second in players[index + 1:]
        }

        locked_cpt = next((p for p in players if p.get("LockCpt")), None)
        conflicting_locks = [
            p for p in players
            if (p.get("LockFlex") and p.get("FadeFlex"))
            or (p.get("LockCpt") and (p.get("FadeCpt") or p.get("LockFlex")))
        ]
        if conflicting_locks:
            logger.info(
                "Showdown fast build is infeasible because lock/fade tags conflict for: %s",
                ", ".join(str(p.get("Name", "")) for p in conflicting_locks[:10]),
            )
            return []
        locked_flex = [p for p in players if p.get("LockFlex")]
        locked_flex_keys = {_pkey(p) for p in locked_flex}
        if len(locked_flex_keys) > 5:
            logger.info("Showdown fast build is infeasible: %d FLEX locks.", len(locked_flex_keys))
            return []

        def cpt_score(player: Dict[str, Any]) -> float:
            return captain_scores[id(player)]

        def flex_score(player: Dict[str, Any]) -> float:
            return flex_scores[id(player)]

        def pair_score(first: Dict[str, Any], second: Dict[str, Any]) -> float:
            return pair_scores.get(tuple(sorted((id(first), id(second)))), 0.0)

        def under_cap(cap_map: Dict[str, int], used: Dict[str, int], key: str) -> bool:
            maximum = cap_map.get(key)
            return maximum is None or used.get(key, 0) < maximum

        def weighted_pick(pool: List[Dict[str, Any]], raw_scores: List[float]) -> Dict[str, Any]:
            floor = min(raw_scores) if raw_scores else 0.0
            weights = [max(0.05, score - floor + 0.75) ** 1.7 for score in raw_scores]
            return self.rng.choices(pool, weights=weights, k=1)[0]

        def lineup_score(captain: Dict[str, Any], flex: List[Dict[str, Any]]) -> float:
            score = cpt_score(captain) + sum(flex_score(p) for p in flex)
            if style <= 0:
                return score + self.rng.uniform(-0.08, 0.08)

            return score + style * _showdown_lineup_script_bonus(captain, flex) + self.rng.uniform(-0.08, 0.08)

        def sample_candidate() -> Optional[Tuple[float, Dict[str, Any], List[Dict[str, Any]], Tuple[str, Tuple[str, ...]]]]:
            cpt_pool = [
                p for p in players
                if not p.get("FadeCpt")
                and not p.get("LockFlex")
                and under_cap(cap_cpt, used_cpt, player_key[id(p)])
            ]
            if locked_cpt is not None:
                cpt_pool = [locked_cpt] if locked_cpt in cpt_pool else []
            if not cpt_pool:
                return None

            cpt_raw = [
                cpt_score(p) + 0.10 * (cpt_score(p) / max(_cpt_salary(p), 1.0)) * 1000.0
                for p in cpt_pool
            ]
            captain = weighted_pick(cpt_pool, cpt_raw)
            captain_key = player_key[id(captain)]
            cap_left = self.salary_cap - captain_salary[id(captain)]
            if cap_left < 0:
                return None

            flex: List[Dict[str, Any]] = []
            for player in locked_flex:
                key = player_key[id(player)]
                if key == captain_key or not under_cap(cap_flex, used_flex, key):
                    return None
                flex.append(player)
                cap_left -= flex_salary[id(player)]
            if cap_left < 0:
                return None

            available = [
                p for p in players
                if player_key[id(p)] != captain_key
                and player_key[id(p)] not in locked_flex_keys
                and not p.get("FadeFlex")
                and under_cap(cap_flex, used_flex, player_key[id(p)])
            ]

            while len(flex) < 5:
                slots_after_pick = 4 - len(flex)
                feasible: List[Dict[str, Any]] = []
                raw_scores: List[float] = []
                cheapest_options = sorted((flex_salary[id(p)], player_key[id(p)]) for p in available)
                for player in available:
                    salary = flex_salary[id(player)]
                    if salary > cap_left:
                        continue
                    candidate_key = player_key[id(player)]
                    cheapest_others = [
                        other_salary for other_salary, other_key in cheapest_options
                        if other_key != candidate_key
                    ][:slots_after_pick]
                    if slots_after_pick and (
                        len(cheapest_others) < slots_after_pick
                        or sum(cheapest_others) > cap_left - salary
                    ):
                        continue
                    raw = flex_score(player)
                    raw += 0.10 * (flex_score(player) / max(salary, 1.0)) * 1000.0
                    raw += style * sum(
                        pair_score(player, chosen)
                        for chosen in [captain] + flex
                    )
                    feasible.append(player)
                    raw_scores.append(raw)
                if not feasible:
                    return None

                chosen = weighted_pick(feasible, raw_scores)
                flex.append(chosen)
                cap_left -= flex_salary[id(chosen)]
                chosen_key = player_key[id(chosen)]
                available = [p for p in available if player_key[id(p)] != chosen_key]

            flex_keys = tuple(sorted(player_key[id(p)] for p in flex))
            signature = (captain_key, flex_keys)
            if signature in used_signatures:
                return None
            if len({_team(player) for player in [captain] + flex if _team(player)}) != 2:
                return None
            games = {_game_key(player) for player in [captain] + flex if _game_key(player)}
            if len(games) > 1:
                return None
            return lineup_score(captain, flex), captain, flex, signature

        self._report_progress(progress_callback, 0, num_lineups, "Generating fast showdown portfolio")
        # A few dozen alternatives per slot are enough to preserve quality while
        # keeping 150-lineup builds comfortably interactive on ordinary laptops.
        candidates_per_lineup = 28 if num_lineups >= 100 else 48
        failures = 0
        max_failures = 16

        while len(out) < num_lineups and failures < max_failures:
            if self._cancelled(cancel_callback):
                logger.info("Showdown fast build cancelled at %d/%d.", len(out), num_lineups)
                break

            candidates = []
            attempts = max(candidates_per_lineup * 3, 180)
            for _ in range(attempts):
                if self._cancelled(cancel_callback):
                    break
                candidate = sample_candidate()
                if candidate is not None:
                    candidates.append(candidate)
                    if len(candidates) >= candidates_per_lineup:
                        break

            if self._cancelled(cancel_callback):
                break
            if not candidates:
                failures += 1
                continue

            candidates.sort(key=lambda item: item[0], reverse=True)
            _, captain, flex, signature = candidates[0]
            used_signatures.add(signature)
            captain_key = player_key[id(captain)]
            used_cpt[captain_key] = used_cpt.get(captain_key, 0) + 1
            for player in flex:
                key = player_key[id(player)]
                used_flex[key] = used_flex.get(key, 0) + 1
            out.append(ShowdownLineup(captain, flex))
            failures = 0
            self._report_progress(progress_callback, len(out), num_lineups, "Generating fast showdown portfolio")

        if len(out) < num_lineups and not self._cancelled(cancel_callback):
            logger.info(
                "Showdown fast build stopped early at %d/%d after exhausting feasible candidates.",
                len(out),
                num_lineups,
            )
        return out

    def _build_lineups_greedy(self, num_lineups: int) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        used_sets: set[Tuple[str, Tuple[str, ...]]] = set()

        cap_cpt, cap_flex = _build_showdown_cap_maps(self.players, num_lineups)
        used_cpt: Dict[str, int] = {}
        used_flex: Dict[str, int] = {}

        own_s = _own_sign(self.own_mode)
        w_cpt = self.own_weight * 1.35 * own_s
        w_flex = self.own_weight * 1.00 * own_s

        def score_cpt(p: Dict[str, Any]) -> float:
            return _cpt_proj(p) + w_cpt * _showdown_cpt_own(p)

        def score_flex(p: Dict[str, Any]) -> float:
            return _proj(p) + w_flex * _showdown_flex_own(p)

        locked_cpt = next((p for p in self.players if p.get("LockCpt")), None)

        # CPT pool
        if locked_cpt:
            cpt_choices = [locked_cpt]
        else:
            cpt_pool = [p for p in self.players if not p.get("FadeCpt")]
            cpt_pool = sorted(cpt_pool, key=lambda p: (score_cpt(p) / max(_cpt_salary(p), 1.0)), reverse=True)
            cpt_choices = cpt_pool[: max(10, len(cpt_pool) // 10)]

        # FLEX pool
        base_flex_pool = [p for p in self.players if not p.get("FadeFlex")]
        base_flex_pool = sorted(base_flex_pool, key=lambda p: (score_flex(p) / max(_salary(p), 1.0)), reverse=True)

        for _ in range(num_lineups * 50):
            cpt = self.rng.choice(cpt_choices)
            cpt_key = _pkey(cpt)
            mx_cpt = cap_cpt.get(cpt_key, None)
            if mx_cpt is not None and used_cpt.get(cpt_key, 0) >= mx_cpt:
                continue

            cap_left = self.salary_cap - _cpt_salary(cpt)
            if cap_left < 0:
                continue

            flex: List[Dict[str, Any]] = []

            # forced flex locks first
            locked_flex = [p for p in self.players if p.get("LockFlex") and not p.get("FadeFlex")]
            locked_flex = [p for p in locked_flex if _pkey(p) != _pkey(cpt)]

            ok = True
            for p in locked_flex:
                if _salary(p) > cap_left:
                    ok = False
                    break
                pk = _pkey(p)
                mx_f = cap_flex.get(pk, None)
                if mx_f is not None and used_flex.get(pk, 0) >= mx_f:
                    ok = False
                    break
                flex.append(p)
                cap_left -= _salary(p)
            if not ok or len(flex) > 5:
                continue

            for p in base_flex_pool:
                if _pkey(p) == _pkey(cpt):
                    continue
                if any(_pkey(p) == _pkey(x) for x in flex):
                    continue
                if _salary(p) <= cap_left:
                    pk = _pkey(p)
                    mx_f = cap_flex.get(pk, None)
                    if mx_f is not None and used_flex.get(pk, 0) >= mx_f:
                        continue
                    flex.append(p)
                    cap_left -= _salary(p)
                if len(flex) == 5:
                    break

            if len(flex) < 5:
                continue

            if len({_team(player) for player in [cpt] + flex if _team(player)}) != 2:
                continue
            games = {_game_key(player) for player in [cpt] + flex if _game_key(player)}
            if len(games) > 1:
                continue

            sig = (_pkey(cpt), tuple(sorted(_pkey(p) for p in flex)))
            if sig in used_sets:
                continue

            used_sets.add(sig)
            out.append(ShowdownLineup(cpt, flex))

            used_cpt[_pkey(cpt)] = used_cpt.get(_pkey(cpt), 0) + 1
            for pp in flex:
                pk = _pkey(pp)
                used_flex[pk] = used_flex.get(pk, 0) + 1

            if len(out) >= num_lineups:
                break

        return out


# ---------------- Classic ----------------


class ClassicOptimizer:
    """DK Classic

    QB, RB, RB, WR, WR, WR, TE, FLEX(RB/WR/TE), DST

    Tags:
      - LockFlex = must include player
      - FadeFlex = exclude player
      - CPT tags ignored

    Builder biases (soft): stacking + bringbacks + correlation.

    Ownership controls:
      - own_mode: 'Balanced' | 'Leverage' | 'Chalk'
      - own_weight: strength of ownership term in the objective
    """

    def __init__(
        self,
        players: List[Dict[str, Any]],
        salary_cap: float = 50000.0,
        seed: int = 1337,
        *,
        own_mode: str = "Balanced",
        own_weight: float = 0.0,
    ):
        self.players = [p for p in players if _salary(p) > 0 and str(p.get("Position", "")).strip()]
        self.salary_cap = float(salary_cap)
        self.rng = random.Random(seed)
        self.own_mode = own_mode
        self.own_weight = float(own_weight or 0.0)

    def build_lineups(self, num_lineups: int = 10) -> List[List[Dict[str, Any]]]:
        if not self.players:
            return []

        if HAS_PULP:
            return self._build_lineups_pulp(num_lineups=num_lineups)
        logger.warning("PuLP not installed; using greedy fallback for classic (tags honored).")
        return self._build_lineups_greedy(num_lineups=num_lineups)

    def _mlb_stack_weights(self) -> Tuple[float, float, float]:
        pref = (getattr(self, "mlb_stack_pref", "Strategic") or "Strategic").strip().lower()
        if "no" in pref or "off" in pref:
            return (0.0, 0.0, 0.0)
        if "5-3" in pref:
            return (0.25, 0.90, 3.00)
        if "5-2-1" in pref:
            return (0.15, 0.60, 2.70)
        if "4-4" in pref:
            return (0.25, 2.40, 1.20)
        if "4-3-1" in pref:
            return (0.90, 2.00, 0.75)
        # Any Strategic: prefer useful stacks without forcing a specific construction.
        return (0.70, 1.25, 2.10)

    def _build_lineups_pulp(self, num_lineups: int) -> List[List[Dict[str, Any]]]:
        prev_sets: List[Tuple[str, ...]] = []
        out: List[List[Dict[str, Any]]] = []

        keys = [_pkey(p) for p in self.players]
        key_to_player = {_pkey(p): p for p in self.players}

        def pos(pk: str) -> str:
            return str(key_to_player[pk].get("Position", "")).strip().upper()

        def team(pk: str) -> str:
            return str(key_to_player[pk].get("Team", "")).strip().upper()

        def opp(pk: str) -> str:
            return str(key_to_player[pk].get("Opponent", "")).strip().upper()

        # Exposure caps (Classic): MaxPct (fallback to legacy MaxFlexPct)
        cap_total: Dict[str, int] = {}
        used_total: Dict[str, int] = {}
        for p in self.players:
            pk = _pkey(p)
            pct_val = p.get("MaxPct", None)
            if pct_val in (None, ""):
                pct_val = p.get("MaxFlexPct", None)
            mc = _max_count_from_pct(pct_val, num_lineups)
            if mc is not None:
                cap_total[pk] = mc
                used_total[pk] = 0

        # ---- Classic builder biases (soft) ----
        DEFAULT_BIAS = {
            "stack_pair": 2.0,  # QB with each same-team WR/TE
            "bringback_pair": 0.8,  # QB with each opposing RB/WR/TE
            "qb_vs_dst": -2.5,  # QB against opposing DST
            "rb_dst": 0.6,  # RB + same-team DST
            "rb_rb_same": -1.0,  # 2 RB same team
        }
        bias_weights = [1.0, 0.6, 0.3, 0.0]  # progressive relaxation

        # Ownership term
        own_s = _own_sign(self.own_mode)
        w_own = self.own_weight * own_s

        # Precompute pair terms once: (a_key, b_key, coef)
        def build_pair_terms() -> List[Tuple[str, str, float]]:
            pair_terms: List[Tuple[str, str, float]] = []
            by_team_pos: Dict[Tuple[str, str], List[str]] = {}
            for pk in keys:
                by_team_pos.setdefault((team(pk), pos(pk)), []).append(pk)

            # QB stacking with WR/TE
            for qbpk in [pk for pk in keys if pos(pk) == "QB"]:
                t = team(qbpk)
                if not t:
                    continue
                passcatch = (by_team_pos.get((t, "WR"), []) or []) + (by_team_pos.get((t, "TE"), []) or [])
                for ppk in passcatch:
                    pair_terms.append((qbpk, ppk, DEFAULT_BIAS["stack_pair"]))

            # Bring-backs: QB with opposing RB/WR/TE
            for qbpk in [pk for pk in keys if pos(pk) == "QB"]:
                o = opp(qbpk)
                if not o:
                    continue
                opp_skills = (
                    (by_team_pos.get((o, "RB"), []) or [])
                    + (by_team_pos.get((o, "WR"), []) or [])
                    + (by_team_pos.get((o, "TE"), []) or [])
                )
                for sk in opp_skills:
                    pair_terms.append((qbpk, sk, DEFAULT_BIAS["bringback_pair"]))

            # QB vs opposing DST penalty
            for qbpk in [pk for pk in keys if pos(pk) == "QB"]:
                o = opp(qbpk)
                if not o:
                    continue
                for dstpk in (by_team_pos.get((o, "DST"), []) or []):
                    pair_terms.append((qbpk, dstpk, DEFAULT_BIAS["qb_vs_dst"]))

            # RB + DST same team bonus
            for t in {team(pk) for pk in keys}:
                if not t:
                    continue
                rbs = by_team_pos.get((t, "RB"), []) or []
                dsts = by_team_pos.get((t, "DST"), []) or []
                for rbpk in rbs:
                    for dstpk in dsts:
                        pair_terms.append((rbpk, dstpk, DEFAULT_BIAS["rb_dst"]))

            # RB + RB same team penalty
            for t in {team(pk) for pk in keys}:
                if not t:
                    continue
                rbs = sorted(by_team_pos.get((t, "RB"), []) or [])
                for i in range(len(rbs)):
                    for j in range(i + 1, len(rbs)):
                        pair_terms.append((rbs[i], rbs[j], DEFAULT_BIAS["rb_rb_same"]))

            return pair_terms

        pair_terms = build_pair_terms()

        for k in range(num_lineups):
            solved = False
            last_status = "Unknown"

            for bw in bias_weights:
                prob = pulp.LpProblem(f"classic_{k}", pulp.LpMaximize)
                x = pulp.LpVariable.dicts("pick", keys, lowBound=0, upBound=1, cat="Binary")

                # Base objective
                obj = pulp.lpSum([x[pk] * _proj(key_to_player[pk]) for pk in keys])

                # Ownership term (soft)
                if self.own_weight and abs(self.own_weight) > 1e-9:
                    obj += pulp.lpSum([x[pk] * (w_own * _own(key_to_player[pk])) for pk in keys])

                # Pair bias terms (soft) with relaxation
                if pair_terms and bw > 0:
                    y = pulp.LpVariable.dicts(
                        f"pair_{k}", list(range(len(pair_terms))), lowBound=0, upBound=1, cat="Binary"
                    )
                    for idx, (a, b, coef) in enumerate(pair_terms):
                        prob += y[idx] <= x[a]
                        prob += y[idx] <= x[b]
                        prob += y[idx] >= x[a] + x[b] - 1
                        obj += y[idx] * (bw * coef)

                prob += obj

                # Roster constraints
                prob += pulp.lpSum([x[pk] for pk in keys]) == 9
                prob += pulp.lpSum([x[pk] * _salary(key_to_player[pk]) for pk in keys]) <= self.salary_cap

                prob += pulp.lpSum([x[pk] for pk in keys if pos(pk) == "QB"]) == 1
                prob += pulp.lpSum([x[pk] for pk in keys if pos(pk) == "DST"]) == 1
                prob += pulp.lpSum([x[pk] for pk in keys if pos(pk) == "RB"]) >= 2
                prob += pulp.lpSum([x[pk] for pk in keys if pos(pk) == "WR"]) >= 3
                prob += pulp.lpSum([x[pk] for pk in keys if pos(pk) == "TE"]) >= 1
                prob += pulp.lpSum([x[pk] for pk in keys if pos(pk) in ("RB", "WR", "TE")]) == 7

                # Tags
                for pk in keys:
                    p = key_to_player[pk]
                    if p.get("FadeFlex"):
                        prob += x[pk] == 0
                    if p.get("LockFlex"):
                        prob += x[pk] == 1

                # Exposure caps
                for pk, cap in cap_total.items():
                    used = used_total.get(pk, 0)
                    if used >= cap:
                        prob += x[pk] == 0
                    else:
                        prob += x[pk] <= (cap - used)

                # Uniqueness (avoid exact same 9-man set)
                for prev in prev_sets:
                    prob += pulp.lpSum([x[pk] for pk in prev]) <= 8

                status = prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=4))
                last_status = pulp.LpStatus.get(status, str(status))
                if pulp.LpStatus[status] != "Optimal":
                    continue

                picked = tuple(sorted([pk for pk in keys if pulp.value(x[pk]) > 0.5]))
                if len(picked) != 9:
                    continue

                for pk in picked:
                    if pk in used_total:
                        used_total[pk] = used_total.get(pk, 0) + 1

                prev_sets.append(picked)
                out.append([key_to_player[pk] for pk in picked])
                solved = True
                break

            if not solved:
                logger.info("Classic stopped early at %d/%d (status=%s)", k, num_lineups, last_status)
                break

        return out

    def _build_lineups_greedy(self, num_lineups: int) -> List[List[Dict[str, Any]]]:
        out: List[List[Dict[str, Any]]] = []
        used: set[Tuple[str, ...]] = set()

        own_s = _own_sign(self.own_mode)
        w_own = self.own_weight * own_s

        def score(p: Dict[str, Any]) -> float:
            return _proj(p) + w_own * _own(p)

        pool = [p for p in self.players if not p.get("FadeFlex")]
        pool = sorted(pool, key=lambda p: (score(p) / max(_salary(p), 1.0)), reverse=True)

        locked = [p for p in self.players if p.get("LockFlex") and not p.get("FadeFlex")]
        locked_keys = {_pkey(p) for p in locked}

        # Exposure caps
        cap_total: Dict[str, int] = {}
        used_total: Dict[str, int] = {}
        for p in self.players:
            pk = _pkey(p)
            pct_val = p.get("MaxPct", None)
            if pct_val in (None, ""):
                pct_val = p.get("MaxFlexPct", None)
            mc = _max_count_from_pct(pct_val, num_lineups)
            if mc is not None:
                cap_total[pk] = mc
                used_total[pk] = 0

        def pick_best(pos_name: str, exclude: set[str], cap_left: float) -> Optional[Dict[str, Any]]:
            for p in pool:
                pk = _pkey(p)
                if pk in exclude:
                    continue
                if pk in cap_total and pk not in locked_keys and used_total.get(pk, 0) >= cap_total[pk]:
                    continue
                if str(p.get("Position", "")).strip().upper() != pos_name:
                    continue
                if _salary(p) <= cap_left:
                    return p
            return None

        for _ in range(num_lineups * 60):
            cap_left = self.salary_cap
            chosen: List[Dict[str, Any]] = []
            excl: set[str] = set()

            ok = True
            for p in locked:
                if _salary(p) > cap_left:
                    ok = False
                    break
                chosen.append(p)
                excl.add(_pkey(p))
                cap_left -= _salary(p)
            if not ok or len(chosen) > 9:
                continue

            def count(posn: str) -> int:
                return sum(1 for p in chosen if str(p.get("Position", "")).upper() == posn)

            if count("QB") == 0:
                qb = pick_best("QB", excl, cap_left)
                if not qb:
                    continue
                chosen.append(qb)
                excl.add(_pkey(qb))
                cap_left -= _salary(qb)

            if count("DST") == 0:
                dst = pick_best("DST", excl, cap_left)
                if not dst:
                    continue
                chosen.append(dst)
                excl.add(_pkey(dst))
                cap_left -= _salary(dst)

            while count("RB") < 2:
                p = pick_best("RB", excl, cap_left)
                if not p:
                    ok = False
                    break
                chosen.append(p)
                excl.add(_pkey(p))
                cap_left -= _salary(p)
            if not ok:
                continue

            while count("WR") < 3:
                p = pick_best("WR", excl, cap_left)
                if not p:
                    ok = False
                    break
                chosen.append(p)
                excl.add(_pkey(p))
                cap_left -= _salary(p)
            if not ok:
                continue

            while count("TE") < 1:
                p = pick_best("TE", excl, cap_left)
                if not p:
                    ok = False
                    break
                chosen.append(p)
                excl.add(_pkey(p))
                cap_left -= _salary(p)
            if not ok:
                continue

            while len(chosen) < 9:
                flex = None
                for p in pool:
                    if _pkey(p) in excl:
                        continue
                    if str(p.get("Position", "")).upper() not in ("RB", "WR", "TE"):
                        continue
                    if _salary(p) <= cap_left:
                        flex = p
                        break
                if not flex:
                    ok = False
                    break
                chosen.append(flex)
                excl.add(_pkey(flex))
                cap_left -= _salary(flex)

            if not ok:
                continue

            if not locked_keys.issubset({_pkey(p) for p in chosen}):
                continue

            sig = tuple(sorted(_pkey(p) for p in chosen))
            if sig in used:
                continue
            used.add(sig)

            # Update exposure usage counts
            for p in chosen:
                pk = _pkey(p)
                if pk in used_total and pk not in locked_keys:
                    used_total[pk] = used_total.get(pk, 0) + 1

            out.append(chosen)
            if len(out) >= num_lineups:
                break

        return out


# ---------------- Multi-sport Classic (MLB / NBA / WNBA) ----------------

SPORT_ROSTER_SLOTS = {
    "NFL": ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST"],
    "MLB": ["P", "P", "C", "1B", "2B", "3B", "SS", "OF", "OF", "OF"],
    "NBA": ["PG", "SG", "SF", "PF", "C", "G", "F", "UTIL"],
    "WNBA": ["PG", "SG", "SF", "PF", "C", "G", "F", "UTIL"],
}


def get_roster_slots_for_sport(sport: str) -> List[str]:
    s = (sport or "NFL").strip().upper()
    return list(SPORT_ROSTER_SLOTS.get(s, SPORT_ROSTER_SLOTS["NFL"]))


def _position_tokens(p: Dict[str, Any]) -> set[str]:
    raw = str(p.get("Position", "") or "").upper().replace("/", ",").replace(";", ",")
    parts = [x.strip() for x in raw.split(",") if x.strip()]
    if not parts and raw.strip():
        parts = [raw.strip()]
    return set(parts)


def _eligible_for_slot(p: Dict[str, Any], slot: str, sport: str) -> bool:
    slot = (slot or "").upper()
    sport = (sport or "NFL").upper()
    pos = _position_tokens(p)

    # Direct match first. Handles normal cases like QB, 1B, SS, PG, etc.
    if slot in pos:
        return True

    # MLB DraftKings files often label pitchers as SP/RP instead of generic P,
    # and outfielders may occasionally appear as LF/CF/RF rather than OF.
    if sport == "MLB":
        if slot == "P":
            return bool(pos & {"P", "SP", "RP"})
        if slot == "OF":
            return bool(pos & {"OF", "LF", "CF", "RF"})

    # NBA/WNBA utility slots.
    if slot == "UTIL":
        return bool(pos & {"PG", "SG", "SF", "PF", "C", "G", "F"})
    if slot == "G":
        return bool(pos & {"PG", "SG", "G"})
    if slot == "F":
        return bool(pos & {"SF", "PF", "F"})

    # NFL flex.
    if slot == "FLEX":
        return bool(pos & {"RB", "WR", "TE"})

    return False



def _lineup_salary(lineup: List[Dict[str, Any]]) -> float:
    return sum(_salary(p) for p in (lineup or []))


def _salary_floor_for_strategy(salary_cap: float, strategy: str, sport: str) -> float:
    """Preferred minimum salary. This is enforced progressively, not blindly forever."""
    strategy_l = (strategy or "Near Cap").strip().lower()
    sport_u = (sport or "").strip().upper()
    cap = float(salary_cap or 50000.0)
    if "leverage" in strategy_l or "loose" in strategy_l:
        return max(0.0, cap - 4500.0)   # e.g. 45.5k on 50k cap
    if "balanced" in strategy_l:
        return max(0.0, cap - 3000.0)   # e.g. 47k
    if "max" in strategy_l or "cash" in strategy_l:
        return max(0.0, cap - (500.0 if sport_u == "NFL" else 1000.0))
    # Recommended tournament default: use almost all salary without requiring perfect 50k.
    return max(0.0, cap - (1000.0 if sport_u == "NFL" else 1500.0))


def _salary_bonus(lineup: List[Dict[str, Any]], salary_cap: float, strategy: str = "Near Cap") -> float:
    used = _lineup_salary(lineup)
    cap = float(salary_cap or 50000.0)
    if cap <= 0:
        return 0.0
    # Smoothly rewards spending from ~94% of cap upward. Caps the effect so bad
    # high-salary players do not overwhelm projections and stack logic.
    pct = max(0.0, min(1.0, used / cap))
    if "leverage" in (strategy or "").strip().lower():
        # Salary leverage still dislikes extreme punts, but doesn't force near-cap.
        return max(0.0, min(3.0, (pct - 0.88) * 25.0))
    return max(0.0, min(8.0, (pct - 0.94) * 100.0))


def lineup_grade_for_sport(lineup: List[Dict[str, Any]], sport: str, salary_cap: float = 50000.0) -> Dict[str, Any]:
    """UI-only lineup grade. Do not export this to DK upload files."""
    sport_u = (sport or "NFL").upper()
    used = _lineup_salary(lineup)
    cap = float(salary_cap or 50000.0)
    sal_pct = (used / cap) if cap > 0 else 0.0
    salary_score = max(0.0, min(100.0, (sal_pct - 0.88) / 0.12 * 100.0))

    proj = sum(_proj(p) for p in (lineup or []))
    # Sport-specific loose normalization. NFL previously used proj*2, which made
    # virtually every normal Classic lineup hit a meaningless 100 projection score.
    if sport_u == "NFL":
        proj_score = max(0.0, min(100.0, ((proj - 90.0) / 80.0) * 100.0))
    elif sport_u == "MLB":
        proj_score = max(0.0, min(100.0, proj * 7.5))
    else:
        proj_score = max(0.0, min(100.0, proj * 2.0))

    stack_score = 50.0
    stack_shape = "n/a"
    warnings: List[str] = []
    if sport_u == "NFL":
        nf = _nfl_lineup_features(lineup)
        stack_score = float(nf.get("stack_score", 50.0) or 50.0)
        stack_shape = str(nf.get("stack_shape", "n/a") or "n/a")
        if int(nf.get("qb_stack", 0) or 0) == 0:
            warnings.append("QB unstacked")
        if int(nf.get("bringback", 0) or 0) == 0:
            warnings.append("no bring-back")
        if bool(nf.get("qb_vs_opp_dst")):
            warnings.append("QB vs opposing DST")
        if int(nf.get("max_team", 0) or 0) >= 5:
            warnings.append("heavy team concentration")
        if used < cap - 3000:
            warnings.append("low salary")
    elif sport_u == "MLB":
        hitters = [p for p in lineup if _is_mlb_hitter(p)]
        counts = sorted([c for _, c in Counter(_team(p) for p in hitters if _team(p)).items()], reverse=True)
        stack_shape = "-".join(map(str, counts[:3])) if counts else "none"
        if counts[:2] in ([5,3], [4,4]):
            stack_score = 100.0
        elif counts and counts[0] == 5:
            stack_score = 88.0
        elif counts and counts[0] == 4:
            stack_score = 78.0
        elif counts and counts[0] == 3:
            stack_score = 62.0
        else:
            stack_score = 35.0
        top_order = sum(1 for p in hitters if 1 <= int(p.get("BattingOrder", 0) or 0) <= 5)
        confirmed = sum(1 for p in hitters if p.get("ConfirmedLineup"))
        order_score = min(100.0, 35.0 + top_order * 8.0 + confirmed * 2.0)
        stack_score = min(100.0, 0.82 * stack_score + 0.18 * order_score)
        if used < cap - 3000:
            warnings.append("low salary")
    elif sport_u in ("NBA", "WNBA"):
        teams = Counter(_team(p) for p in lineup if _team(p))
        max_team = max(teams.values()) if teams else 0
        stack_shape = f"max team {max_team}"
        stack_score = min(100.0, 45.0 + max_team * 12.0)

    sim_metrics = getattr(lineup, "sim_metrics", None)
    if sport_u == "NFL" and isinstance(sim_metrics, dict) and sim_metrics:
        sim_edge = max(0.0, min(100.0, float(sim_metrics.get("sim_edge", 0.0) or 0.0)))
        duplicate_risk = max(0.0, min(100.0, float(sim_metrics.get("duplicate_risk", 0.0) or 0.0)))
        if duplicate_risk >= 80.0:
            warnings.append("high duplication risk")
        letter = "A" if sim_edge >= 80 else "B" if sim_edge >= 60 else "C" if sim_edge >= 40 else "D"
        return {
            "grade": letter,
            "score": sim_edge,
            "salary_used": used,
            "salary_left": max(0.0, cap - used),
            "stack_shape": stack_shape,
            "warnings": ", ".join(warnings),
            "sim_edge": sim_edge,
            "sim_top_one_pct": float(sim_metrics.get("sim_top_one_pct", 0.0) or 0.0),
            "sim_top_five_pct": float(sim_metrics.get("sim_top_five_pct", 0.0) or 0.0),
            "sim_win_rate": float(sim_metrics.get("sim_win_rate", 0.0) or 0.0),
            "sim_cash_rate": float(sim_metrics.get("sim_cash_rate", 0.0) or 0.0),
            "sim_bust_rate": float(sim_metrics.get("sim_bust_rate", 0.0) or 0.0),
            "sim_average_percentile": float(sim_metrics.get("sim_average_percentile", 0.0) or 0.0),
            "sim_ceiling": float(sim_metrics.get("sim_ceiling", 0.0) or 0.0),
            "sim_return_index": float(sim_metrics.get("sim_return_index", 0.0) or 0.0),
            "sim_expected_payout": sim_metrics.get("sim_expected_payout"),
            "sim_expected_profit": sim_metrics.get("sim_expected_profit"),
            "sim_expected_roi_pct": sim_metrics.get("sim_expected_roi_pct"),
            "sim_joint_portfolio": bool(sim_metrics.get("sim_joint_portfolio")),
            "sim_portfolio_cash_rate": sim_metrics.get("sim_portfolio_cash_rate"),
            "sim_portfolio_scenarios": int(sim_metrics.get("sim_portfolio_scenarios", 0) or 0),
            "sim_portfolio_entry_count": int(sim_metrics.get("sim_portfolio_entry_count", 0) or 0),
            "sim_portfolio_expected_total_payout": sim_metrics.get("sim_portfolio_expected_total_payout"),
            "sim_portfolio_expected_total_profit": sim_metrics.get("sim_portfolio_expected_total_profit"),
            "sim_portfolio_expected_roi_pct": sim_metrics.get("sim_portfolio_expected_roi_pct"),
            "sim_portfolio_profit_probability_pct": sim_metrics.get("sim_portfolio_profit_probability_pct"),
            "sim_portfolio_roi_ci_low": sim_metrics.get("sim_portfolio_roi_ci_low"),
            "sim_portfolio_roi_ci_high": sim_metrics.get("sim_portfolio_roi_ci_high"),
            "sim_standalone_expected_roi_pct": sim_metrics.get("sim_standalone_expected_roi_pct"),
            "sim_contest_name": str(sim_metrics.get("sim_contest_name", "") or ""),
            "sim_entry_fee": sim_metrics.get("sim_entry_fee"),
            "sim_contest_field_size": sim_metrics.get("sim_contest_field_size"),
            "sim_leverage": float(sim_metrics.get("sim_leverage", 0.0) or 0.0),
            "duplicate_risk": duplicate_risk,
            "field_duplicate_estimate": float(sim_metrics.get("field_duplicate_estimate", 0.0) or 0.0),
            "sim_edge_components": dict(sim_metrics.get("sim_edge_components") or {}),
            "sim_edge_drivers": list(sim_metrics.get("sim_edge_drivers") or []),
            "learned_profile_fit": sim_metrics.get("learned_profile_fit"),
            "sim_scenarios": int(sim_metrics.get("sim_scenarios", 0) or 0),
            "sim_field_lineups": int(sim_metrics.get("sim_field_lineups", 0) or 0),
        }

    overall = 0.42 * proj_score + 0.35 * salary_score + 0.23 * stack_score
    if used < cap - 5000:
        overall -= 18.0
    elif used < cap - 3000:
        overall -= 8.0
    overall = max(0.0, min(100.0, overall))
    letter = "A" if overall >= 85 else "B" if overall >= 72 else "C" if overall >= 58 else "D"
    return {
        "grade": letter,
        "score": overall,
        "salary_used": used,
        "salary_left": max(0.0, cap - used),
        "stack_shape": stack_shape,
        "warnings": ", ".join(warnings),
    }


class MultiSportClassicOptimizer:
    """Generic DK-style classic optimizer for NFL / MLB / NBA / WNBA.

    Uses sport-specific slot templates and supports LockFlex/FadeFlex, salary cap,
    max exposure via MaxPct, and ownership objective shaping.
    """

    def __init__(
        self,
        players: List[Dict[str, Any]],
        *,
        sport: str = "NFL",
        salary_cap: float = 50000.0,
        seed: int = 1337,
        own_mode: str = "Balanced",
        own_weight: float = 0.0,
        build_style: str = "Strategic",
        mlb_stack_pref: str = "Strategic",
        salary_strategy: str = "Near Cap",
    ):
        self.sport = (sport or "NFL").strip().upper()
        self.slots = get_roster_slots_for_sport(self.sport)
        self.players = [p for p in players if _salary(p) > 0 and str(p.get("Position", "")).strip()]
        self.salary_cap = float(salary_cap)
        self.rng = random.Random(seed)
        self.own_mode = own_mode
        self.own_weight = float(own_weight or 0.0)
        self.build_style = build_style or "Strategic"
        self.mlb_stack_pref = mlb_stack_pref or "Strategic"
        self.salary_strategy = salary_strategy or "Near Cap"

    def build_lineups(
        self,
        num_lineups: int = 10,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        cancel_callback: Optional[Callable[[], bool]] = None,
        excluded_signatures: Optional[Iterable[Tuple[str, ...]]] = None,
        minimum_unique: Optional[int] = None,
    ) -> List[List[Dict[str, Any]]]:
        # Use the same strategy-aware builder for every Classic sport. NFL used to
        # delegate to the legacy ClassicOptimizer here, silently dropping the UI's
        # Build Style and Salary Strategy settings. Keeping NFL in this path makes
        # those controls real and lets the portfolio scorer enforce NFL-specific
        # correlation/uniqueness rules.

        # IMPORTANT: MLB/NBA/WNBA slates can contain hundreds of players.
        # The exact PuLP slot-assignment model becomes very large because it
        # creates player-by-slot variables and, for MLB, additional stack and
        # pitcher-vs-hitter pair variables. On large DK MLB files this can make
        # the UI appear stuck at "Optimizing...".
        #
        # Use the fast strategic greedy builder first for non-NFL classic slates.
        # It still respects sport roster slots, salary cap, fades, locks, max%,
        # ownership influence, and complete-slot validation. If it ever returns
        # no lineups on a smaller slate, then try PuLP as a backup.
        lineups = self._build_lineups_greedy(
            num_lineups=num_lineups,
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
            excluded_signatures=excluded_signatures,
            minimum_unique=minimum_unique,
        )
        if lineups:
            logger.info("%s fast strategic build returned %d/%d lineups.", self.sport, len(lineups), num_lineups)
            return lineups

        if HAS_PULP and len(self.players) <= 120 and num_lineups <= 20:
            logger.info("%s fast build returned no lineups; trying small-slate PuLP fallback.", self.sport)
            return self._build_lineups_pulp(num_lineups=num_lineups)

        logger.info("%s fast build returned no lineups; PuLP skipped for large slate safety.", self.sport)
        return []

    @staticmethod
    def _report_progress(
        progress_callback: Optional[Callable[[int, int, str], None]],
        done: int,
        total: int,
        text: str,
    ) -> None:
        if progress_callback is None:
            return
        try:
            progress_callback(done, total, text)
        except Exception:
            logger.debug("Classic progress callback failed.", exc_info=True)

    def _score(self, p: Dict[str, Any]) -> float:
        score = _proj(p) + self.own_weight * _own_sign(self.own_mode) * _own(p)
        if self.sport == "MLB" and _is_mlb_hitter(p):
            score += _batting_order_bonus(p)
        return score

    def _caps(self, total_lineups: int) -> Tuple[Dict[str, int], Dict[str, int]]:
        cap: Dict[str, int] = {}
        used: Dict[str, int] = {}
        for p in self.players:
            pk = _pkey(p)
            pct_val = p.get("MaxPct", None)
            if pct_val in (None, ""):
                pct_val = p.get("MaxFlexPct", None)
            mc = _max_count_from_pct(pct_val, total_lineups)
            if mc is not None:
                cap[pk] = mc
                used[pk] = 0
        return cap, used

    def _mlb_stack_weights(self) -> Tuple[float, float, float]:
        pref = (getattr(self, "mlb_stack_pref", "Strategic") or "Strategic").strip().lower()
        if "no" in pref or "off" in pref:
            return (0.0, 0.0, 0.0)
        if "5-3" in pref:
            return (0.25, 0.90, 3.00)
        if "5-2-1" in pref:
            return (0.15, 0.60, 2.70)
        if "4-4" in pref:
            return (0.25, 2.40, 1.20)
        if "4-3-1" in pref:
            return (0.90, 2.00, 0.75)
        # Any Strategic: prefer useful stacks without forcing a specific construction.
        return (0.70, 1.25, 2.10)

    def _build_lineups_pulp(self, num_lineups: int) -> List[List[Dict[str, Any]]]:
        prev_sets: List[Tuple[str, ...]] = []
        out: List[List[Dict[str, Any]]] = []
        keys = [_pkey(p) for p in self.players]
        key_to_player = {_pkey(p): p for p in self.players}
        cap_total, used_total = self._caps(num_lineups)
        locked_keys = {_pkey(p) for p in self.players if p.get("LockFlex") and not p.get("FadeFlex")}

        # Helpful feasibility logging: if a slot has zero candidates, the solver
        # would be infeasible before lineup 1.
        try:
            slot_counts = {slot: sum(1 for pk in keys if _eligible_for_slot(key_to_player[pk], slot, self.sport) and not key_to_player[pk].get("FadeFlex")) for slot in sorted(set(self.slots))}
            logger.info("%s candidate counts by slot: %s", self.sport, slot_counts)
        except Exception:
            pass

        for k in range(num_lineups):
            prob = pulp.LpProblem(f"{self.sport.lower()}_{k}", pulp.LpMaximize)
            x = pulp.LpVariable.dicts("pick", keys, lowBound=0, upBound=1, cat="Binary")
            y = pulp.LpVariable.dicts("slot", [(i, pk) for i in range(len(self.slots)) for pk in keys], lowBound=0, upBound=1, cat="Binary")

            obj = pulp.lpSum([x[pk] * self._score(key_to_player[pk]) for pk in keys])

            # Strategic template bias. These are soft nudges layered on top of
            # projections and MLB factor-adjusted projections. If Build Style is
            # Randomized, this block is disabled and the optimizer falls back to
            # projection/value plus the tiny diversity jitter.
            style = _style_level(self.build_style)
            if style > 0:
                teams = sorted({_team(key_to_player[pk]) for pk in keys if _team(key_to_player[pk])})

                if self.sport == "NFL":
                    profile = _nfl_style_profile(self.build_style)
                    # Baseline roster concentration rails. These are intentionally
                    # generous enough to allow shootout stacks while preventing
                    # accidental five/six-man team over-concentration.
                    for t in teams:
                        tkeys = [pk for pk in keys if _team(key_to_player[pk]) == t]
                        if tkeys:
                            prob += pulp.lpSum([x[pk] for pk in tkeys]) <= int(profile.get("max_team", 5))

                    games = sorted({_game_key(key_to_player[pk]) for pk in keys if _game_key(key_to_player[pk])})
                    for g in games:
                        gkeys = [pk for pk in keys if _game_key(key_to_player[pk]) == g]
                        if gkeys:
                            prob += pulp.lpSum([x[pk] for pk in gkeys]) <= int(profile.get("max_game", 6))

                    # Every selected QB in strategic modes carries at least one of
                    # his WR/TE pass catchers. Opposing DST is a hard anti-correlation.
                    required_stack = int(profile.get("required_qb_stack", 0) or 0)
                    for qbpk in [pk for pk in keys if "QB" in _position_tokens(key_to_player[pk])]:
                        qt = _team(key_to_player[qbpk])
                        qo = _nfl_opponent(key_to_player[qbpk])
                        passcatch = [
                            pk for pk in keys
                            if _team(key_to_player[pk]) == qt
                            and bool(_position_tokens(key_to_player[pk]) & {"WR", "TE"})
                        ]
                        if required_stack > 0:
                            if passcatch:
                                prob += pulp.lpSum([x[pk] for pk in passcatch]) >= required_stack * x[qbpk]
                            else:
                                prob += x[qbpk] == 0
                        for dstpk in [pk for pk in keys if "DST" in _position_tokens(key_to_player[pk]) and qo and _team(key_to_player[pk]) == qo]:
                            prob += x[qbpk] + x[dstpk] <= 1

                    # Soft NFL pair scoring: QB-pass catcher, bring-back and RB-DST
                    # bonuses; same-team RB and DST-vs-opponent offense penalties.
                    nfl_pairs: List[Tuple[str, str, float]] = []
                    for qbpk in [pk for pk in keys if "QB" in _position_tokens(key_to_player[pk])]:
                        qt = _team(key_to_player[qbpk])
                        qo = _nfl_opponent(key_to_player[qbpk])
                        for pk in keys:
                            if pk == qbpk:
                                continue
                            pp = key_to_player[pk]
                            toks = _position_tokens(pp)
                            if _team(pp) == qt and bool(toks & {"WR", "TE"}):
                                nfl_pairs.append((qbpk, pk, 1.8))
                            elif qo and _team(pp) == qo and bool(toks & {"RB", "WR", "TE"}):
                                nfl_pairs.append((qbpk, pk, 0.75))
                    for t in teams:
                        rbs = [pk for pk in keys if _team(key_to_player[pk]) == t and "RB" in _position_tokens(key_to_player[pk])]
                        dsts = [pk for pk in keys if _team(key_to_player[pk]) == t and "DST" in _position_tokens(key_to_player[pk])]
                        for rbpk in rbs:
                            for dstpk in dsts:
                                nfl_pairs.append((rbpk, dstpk, 0.55))
                        for i in range(len(rbs)):
                            for j in range(i + 1, len(rbs)):
                                nfl_pairs.append((rbs[i], rbs[j], -0.85))
                    if nfl_pairs:
                        ynfl = pulp.LpVariable.dicts(f"nfl_pair_{k}", list(range(len(nfl_pairs))), 0, 1, cat="Binary")
                        for idx, (a, b, coef) in enumerate(nfl_pairs):
                            prob += ynfl[idx] <= x[a]
                            prob += ynfl[idx] <= x[b]
                            prob += ynfl[idx] >= x[a] + x[b] - 1
                            obj += ynfl[idx] * (style * coef)

                elif self.sport == "MLB":
                    # DK MLB usually wants stacks. Encourage 3/4/5 hitter stacks
                    # and cap hitters at 5 from one team. Pitchers do not count
                    # toward hitter stacks.
                    for t in teams:
                        hitter_keys = [pk for pk in keys if _team(key_to_player[pk]) == t and _is_mlb_hitter(key_to_player[pk])]
                        if not hitter_keys:
                            continue
                        team_count = pulp.lpSum([x[pk] for pk in hitter_keys])
                        prob += team_count <= 5
                        z3 = pulp.LpVariable(f"mlb_{k}_{t}_stack3", 0, 1, cat="Binary")
                        z4 = pulp.LpVariable(f"mlb_{k}_{t}_stack4", 0, 1, cat="Binary")
                        z5 = pulp.LpVariable(f"mlb_{k}_{t}_stack5", 0, 1, cat="Binary")
                        # Indicator constraints for stack sizes.
                        # z3/z4/z5 should turn ON when team_count reaches 3/4/5.
                        # Important: these must not make low team_count values infeasible.
                        # Prior version used team_count - 2 >= z3, which is impossible
                        # when team_count is 0/1 and caused MLB builds to return Infeasible.
                        m = max(5, len(hitter_keys))
                        prob += team_count >= 3 * z3
                        prob += team_count <= 2 + m * z3
                        prob += team_count >= 4 * z4
                        prob += team_count <= 3 + m * z4
                        prob += team_count >= 5 * z5
                        prob += team_count <= 4 + m * z5
                        w3, w4, w5 = self._mlb_stack_weights()
                        obj += style * (w3 * z3 + w4 * z4 + w5 * z5)

                    # Avoid pitcher vs opposing hitters in the same GameInfo.
                    # This is a penalty rather than a hard ban so users can still
                    # force unusual builds with locks if desired.
                    opp_pairs = []
                    for ppk in keys:
                        pp = key_to_player[ppk]
                        if not _is_mlb_pitcher(pp):
                            continue
                        for hpk in keys:
                            hp = key_to_player[hpk]
                            if hpk == ppk or not _is_mlb_hitter(hp):
                                continue
                            if _game_key(pp) and _game_key(pp) == _game_key(hp) and _team(pp) != _team(hp):
                                opp_pairs.append((ppk, hpk))
                    if opp_pairs:
                        ybad = pulp.LpVariable.dicts(f"mlb_bad_{k}", list(range(len(opp_pairs))), 0, 1, cat="Binary")
                        for idx, (a, b) in enumerate(opp_pairs):
                            prob += ybad[idx] <= x[a]
                            prob += ybad[idx] <= x[b]
                            prob += ybad[idx] >= x[a] + x[b] - 1
                        obj -= pulp.lpSum([ybad[i] * (1.25 * style) for i in range(len(opp_pairs))])

                elif self.sport in ("NBA", "WNBA"):
                    # Gentle same-game/team correlation. Useful for overtime/close-game
                    # environments without forcing rigid stacks.
                    game_pairs = []
                    for a_i, a in enumerate(keys):
                        ga = _game_key(key_to_player[a])
                        if not ga:
                            continue
                        for b in keys[a_i + 1:]:
                            if ga == _game_key(key_to_player[b]):
                                game_pairs.append((a, b))
                    if game_pairs:
                        ygame = pulp.LpVariable.dicts(f"nba_game_{k}", list(range(len(game_pairs))), 0, 1, cat="Binary")
                        for idx, (a, b) in enumerate(game_pairs):
                            prob += ygame[idx] <= x[a]
                            prob += ygame[idx] <= x[b]
                            prob += ygame[idx] >= x[a] + x[b] - 1
                        obj += pulp.lpSum([ygame[i] * (0.08 * style) for i in range(len(game_pairs))])

            # Small jitter creates semi-strategic randomness behind the template bias.
            obj += pulp.lpSum([x[pk] * self.rng.uniform(-0.02, 0.02) for pk in keys])
            prob += obj

            prob += pulp.lpSum([x[pk] for pk in keys]) == len(self.slots)
            prob += pulp.lpSum([x[pk] * _salary(key_to_player[pk]) for pk in keys]) <= self.salary_cap

            # DraftKings Classic requires athletes from at least two teams.
            # Capping every individual team below the roster size expresses that
            # platform rule without imposing a strategy-specific concentration cap.
            platform_teams = sorted({_team(key_to_player[pk]) for pk in keys if _team(key_to_player[pk])})
            if len(platform_teams) < 2:
                return out
            for team_name in platform_teams:
                team_keys = [pk for pk in keys if _team(key_to_player[pk]) == team_name]
                prob += pulp.lpSum([x[pk] for pk in team_keys]) <= len(self.slots) - 1

            for i, slot in enumerate(self.slots):
                elig = [pk for pk in keys if _eligible_for_slot(key_to_player[pk], slot, self.sport)]
                prob += pulp.lpSum([y[(i, pk)] for pk in elig]) == 1
                for pk in keys:
                    if pk not in elig:
                        prob += y[(i, pk)] == 0
                    else:
                        prob += y[(i, pk)] <= x[pk]

            for pk in keys:
                prob += pulp.lpSum([y[(i, pk)] for i in range(len(self.slots))]) == x[pk]

            for pk in keys:
                p = key_to_player[pk]
                if p.get("FadeFlex"):
                    prob += x[pk] == 0
                if p.get("LockFlex"):
                    prob += x[pk] == 1

            for pk, cap in cap_total.items():
                if pk not in locked_keys and used_total.get(pk, 0) >= cap:
                    prob += x[pk] == 0

            min_unique = int(_nfl_style_profile(self.build_style).get("min_unique", 1) or 1) if self.sport == "NFL" else 1
            for prev in prev_sets:
                prob += pulp.lpSum([x[pk] for pk in prev]) <= len(self.slots) - min_unique

            status = prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=4))
            if pulp.LpStatus[status] != "Optimal":
                logger.info("%s stopped early at %d/%d (status=%s)", self.sport, k, num_lineups, pulp.LpStatus[status])
                break
            picked = tuple(sorted([pk for pk in keys if pulp.value(x[pk]) > 0.5]))
            if len(picked) != len(self.slots):
                break
            lineup = [key_to_player[pk] for pk in picked]
            if not lineup_is_complete_for_sport(lineup, self.sport):
                logger.warning("%s solver produced an incomplete slot assignment; skipping lineup %d", self.sport, k + 1)
                prev_sets.append(picked)
                continue
            if self.sport == "NFL" and not _nfl_lineup_is_acceptable(lineup, self.build_style):
                logger.warning("NFL solver produced a lineup outside correlation safety rails; skipping lineup %d", k + 1)
                prev_sets.append(picked)
                continue
            prev_sets.append(picked)
            for pk in picked:
                if pk in used_total and pk not in locked_keys:
                    used_total[pk] = used_total.get(pk, 0) + 1
            out.append(lineup)
        return out

    def _lineup_strategy_score(self, lineup: List[Dict[str, Any]]) -> float:
        """Score a complete lineup for tournament usefulness."""
        base = sum(self._score(p) for p in lineup)
        sal_bonus = _salary_bonus(lineup, self.salary_cap, self.salary_strategy)
        stack_bonus = 0.0
        style = _style_level(self.build_style)
        if self.sport == "NFL":
            nf = _nfl_lineup_features(lineup)
            # Keep the baseline safety rails meaningful even in Randomized mode,
            # then scale the tournament-construction preference by Build Style.
            stack_bonus += (float(nf.get("stack_score", 50.0) or 50.0) - 50.0) * 0.16 * max(0.35, style)
            # Double stacks with a bring-back get a small extra ceiling nudge.
            if int(nf.get("qb_stack", 0) or 0) >= 2 and int(nf.get("bringback", 0) or 0) >= 1:
                stack_bonus += 2.5 * max(0.35, style)
            elif int(nf.get("qb_stack", 0) or 0) >= 1 and int(nf.get("bringback", 0) or 0) >= 1:
                stack_bonus += 1.2 * max(0.35, style)
        elif self.sport == "MLB" and style > 0:
            hitters = [p for p in lineup if _is_mlb_hitter(p)]
            counts = sorted([c for _, c in Counter(_team(p) for p in hitters if _team(p)).items()], reverse=True)
            top_order = sum(1 for p in hitters if 1 <= int(p.get("BattingOrder", 0) or 0) <= 5)
            confirmed = sum(1 for p in hitters if p.get("ConfirmedLineup"))
            stack_bonus += min(3.0, 0.45 * top_order + 0.12 * confirmed) * style
            if counts[:2] == [5, 3]:
                stack_bonus += 8.0 * style
            elif counts[:3] == [5, 2, 1]:
                stack_bonus += 7.0 * style
            elif counts[:2] == [4, 4]:
                stack_bonus += 7.5 * style
            elif counts[:3] == [4, 3, 1]:
                stack_bonus += 6.5 * style
            elif counts and counts[0] >= 4:
                stack_bonus += 4.0 * style
            # Soft pitcher-vs-opposing-hitters penalty.
            pitchers = [p for p in lineup if _is_mlb_pitcher(p)]
            bad = 0
            for pp in pitchers:
                for hp in hitters:
                    if _game_key(pp) and _game_key(pp) == _game_key(hp) and _team(pp) != _team(hp):
                        bad += 1
            stack_bonus -= bad * 1.2 * style
        return base + sal_bonus + stack_bonus + self.rng.uniform(-0.05, 0.05)

    def _build_lineups_greedy(
        self,
        num_lineups: int,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        cancel_callback: Optional[Callable[[], bool]] = None,
        excluded_signatures: Optional[Iterable[Tuple[str, ...]]] = None,
        minimum_unique: Optional[int] = None,
    ) -> List[List[Dict[str, Any]]]:
        """Fast strategic builder with salary-floor priority.

        It first tries to return near-cap tournament-style lineups, then relaxes
        salary only if roster rules, locks/fades, or max exposure caps make that
        impossible. This avoids the previous behavior where valid but very cheap
        $30k MLB lineups could be accepted too early.
        """
        accepted: List[List[Dict[str, Any]]] = []
        used_sigs: set[Tuple[str, ...]] = {
            tuple(sorted(str(key) for key in signature if str(key)))
            for signature in (excluded_signatures or [])
        }
        base_pool = [p for p in self.players if not p.get("FadeFlex")]
        key_by_id = {id(p): _pkey(p) for p in base_pool}
        salary_by_id = {id(p): _salary(p) for p in base_pool}
        score_by_id = {id(p): self._score(p) for p in base_pool}
        pool = sorted(
            base_pool,
            key=lambda p: (
                score_by_id[id(p)] + 0.20 * (salary_by_id[id(p)] / 1000.0)
            ) / max(salary_by_id[id(p)], 1.0),
            reverse=True,
        )
        eligible_by_slot = {
            slot: [p for p in pool if _eligible_for_slot(p, slot, self.sport)]
            for slot in set(self.slots)
        }
        eligible_ids_by_slot = {
            slot: {id(p) for p in players}
            for slot, players in eligible_by_slot.items()
        }
        locked = [p for p in pool if p.get("LockFlex")]
        locked_keys = {key_by_id[id(p)] for p in locked}
        cap_total, used_total = self._caps(num_lineups)
        nfl_profile = _nfl_style_profile(self.build_style) if self.sport == "NFL" else {}
        preferred_min_unique = int(nfl_profile.get("min_unique", 1) or 1) if self.sport == "NFL" else 1
        if minimum_unique is not None:
            preferred_min_unique = max(preferred_min_unique, min(len(self.slots), int(minimum_unique or 1)))

        preferred_floor = _salary_floor_for_strategy(self.salary_cap, self.salary_strategy, self.sport)
        floor_stages = [
            preferred_floor,
            max(0.0, preferred_floor - 1500.0),
            max(0.0, preferred_floor - 3000.0),
            0.0,
        ]

        def can_use(p: Dict[str, Any]) -> bool:
            pk = key_by_id[id(p)]
            return pk not in cap_total or pk in locked_keys or used_total.get(pk, 0) < cap_total[pk]

        def lineup_respects_caps_now(lineup: List[Dict[str, Any]]) -> bool:
            # Candidate lineups are accumulated in a pool before acceptance. A cap
            # can become exhausted after another candidate is accepted, so recheck
            # against the live usage counts immediately before committing a lineup.
            for p in lineup:
                pk = key_by_id[id(p)]
                if pk in locked_keys or pk not in cap_total:
                    continue
                if used_total.get(pk, 0) >= cap_total[pk]:
                    return False
            return True

        def candidate_rank(p: Dict[str, Any], cap_left: float, slots_left: int, floor: float, salary_used_so_far: float) -> float:
            # Prefer good players, but when we are behind salary pace, give salary
            # real weight so the builder does not keep settling for cheap value.
            base = score_by_id[id(p)]
            sal = salary_by_id[id(p)]
            target_remaining = max(0.0, floor - salary_used_so_far)
            avg_needed = target_remaining / max(1, slots_left)
            salary_pressure = 0.0
            if floor > 0 and salary_used_so_far < floor:
                salary_pressure = min(4.0, max(0.0, (sal - avg_needed) / 1000.0) * 0.75)
            return base + salary_pressure + 0.12 * (sal / 1000.0) + self.rng.uniform(-0.20, 0.20)

        def try_build_one(floor: float) -> Optional[List[Dict[str, Any]]]:
            chosen = list(locked)
            chosen_keys = {key_by_id[id(p)] for p in chosen}
            cap_left = self.salary_cap - sum(salary_by_id[id(p)] for p in chosen)
            if cap_left < 0 or len(chosen) > len(self.slots):
                return None

            assigned_locked: set[str] = set()
            slots_to_fill: List[str] = []
            for slot in self.slots:
                found = None
                for p in chosen:
                    pk = key_by_id[id(p)]
                    if pk not in assigned_locked and id(p) in eligible_ids_by_slot[slot]:
                        found = pk
                        break
                if found:
                    assigned_locked.add(found)
                else:
                    slots_to_fill.append(slot)

            for idx, slot in enumerate(slots_to_fill):
                slots_left = len(slots_to_fill) - idx
                salary_used_so_far = self.salary_cap - cap_left
                candidates = [
                    p for p in eligible_by_slot[slot]
                    if key_by_id[id(p)] not in chosen_keys
                    and can_use(p)
                    and salary_by_id[id(p)] <= cap_left
                ]
                if not candidates:
                    return None

                # Choose from a strong but not deterministic top slice.
                ranked = sorted(
                    candidates,
                    key=lambda p: candidate_rank(p, cap_left, slots_left, floor, salary_used_so_far),
                    reverse=True,
                )
                top_n = min(len(ranked), max(10, len(ranked) // 4))
                p = self.rng.choice(ranked[:top_n])
                chosen.append(p)
                chosen_keys.add(key_by_id[id(p)])
                cap_left -= salary_by_id[id(p)]

            if len(chosen) != len(self.slots):
                return None
            if sum(salary_by_id[id(p)] for p in chosen) < floor:
                return None
            if not lineup_is_complete_for_sport(chosen, self.sport):
                return None
            if len({_team(player) for player in chosen if _team(player)}) < 2:
                return None
            if self.sport == "NFL" and not _nfl_lineup_is_acceptable(chosen, self.build_style):
                return None
            return chosen

        def too_similar(sig: Tuple[str, ...], min_unique: int) -> bool:
            if min_unique <= 1:
                return sig in used_sigs
            sset = set(sig)
            max_overlap = max(0, len(self.slots) - int(min_unique))
            return any(len(sset.intersection(prev)) > max_overlap for prev in used_sigs)

        self._report_progress(progress_callback, 0, num_lineups, f"Generating {self.sport} Classic candidates")

        def accept_candidate_pool(min_unique: int) -> None:
            """Commit every currently viable candidate in score order.

            The previous high-volume path accepted only one lineup whenever its
            candidate buffer filled. A 225-candidate build therefore required
            roughly 25,000 otherwise-useful attempts. Draining the viable batch
            preserves the same score, uniqueness, and live exposure checks while
            making work scale with requested lineups instead of requested squared.
            """
            if not candidates_by_sig:
                return
            ranked_pool = sorted(
                candidates_by_sig.items(),
                key=lambda item: self._lineup_strategy_score(item[1]),
                reverse=True,
            )
            candidates_by_sig.clear()
            for sig, lineup in ranked_pool:
                if len(accepted) >= num_lineups:
                    break
                if cancel_callback and cancel_callback():
                    break
                if too_similar(sig, min_unique) or not lineup_respects_caps_now(lineup):
                    continue
                used_sigs.add(sig)
                for player in lineup:
                    pk = key_by_id[id(player)]
                    if pk in used_total and pk not in locked_keys:
                        used_total[pk] = used_total.get(pk, 0) + 1
                accepted.append(lineup)
            self._report_progress(
                progress_callback,
                len(accepted),
                num_lineups,
                f"Generating {self.sport} Classic candidates",
            )

        # Build progressively: near-cap first, then relax only if needed.
        attempts_per_stage = max(500, num_lineups * 20)
        candidate_batch_size = min(60, max(20, num_lineups // 4))
        candidates_by_sig: Dict[Tuple[str, ...], List[Dict[str, Any]]] = {}

        for stage_idx, floor in enumerate(floor_stages):
            # Preserve portfolio diversity early; only relax if constraints prevent
            # filling the requested lineup count. Strategic/Balanced start at two
            # uniques, Contrarian at three, Chalk at one.
            stage_min_unique = max(1, preferred_min_unique - max(0, stage_idx - 1))
            for attempt in range(attempts_per_stage):
                if cancel_callback and cancel_callback():
                    accept_candidate_pool(stage_min_unique)
                    return accepted[:num_lineups]
                lu = try_build_one(floor)
                if not lu:
                    continue
                sig = tuple(sorted(key_by_id[id(p)] for p in lu))
                if sig in candidates_by_sig or too_similar(sig, stage_min_unique):
                    continue
                candidates_by_sig[sig] = lu

                # Periodically accept the best viable batch so exposure caps
                # advance without forcing another full buffer per lineup.
                if len(candidates_by_sig) >= candidate_batch_size:
                    accept_candidate_pool(stage_min_unique)
                if len(accepted) >= num_lineups:
                    return accepted[:num_lineups]

                if attempt and attempt % 100 == 0:
                    self._report_progress(
                        progress_callback,
                        len(accepted),
                        num_lineups,
                        f"Evaluating {self.sport} Classic candidates",
                    )

            # After each floor stage, accept the best remaining candidates at that floor.
            accept_candidate_pool(stage_min_unique)
            if len(accepted) >= num_lineups:
                break

        self._report_progress(
            progress_callback,
            len(accepted),
            num_lineups,
            f"Generated {len(accepted)} {self.sport} Classic candidates",
        )
        return accepted[:num_lineups]


def lineup_slots_for_sport(lineup: List[Dict[str, Any]], sport: str) -> List[Tuple[str, Optional[Dict[str, Any]]]]:
    """Assign lineup players to display/export slots for the selected sport.

    This uses a small bipartite matching/backtracking step instead of a greedy
    first-fit assignment. Greedy assignment can put a multi-position MLB player
    into an early slot and then strand a later slot, causing blank cells even
    when the optimizer actually selected a valid 10-man lineup.
    """
    sport = (sport or "NFL").upper()
    slots = get_roster_slots_for_sport(sport)
    players = list(lineup or [])

    if len(players) < len(slots):
        return [(slot, players[i] if i < len(players) and _eligible_for_slot(players[i], slot, sport) else None) for i, slot in enumerate(slots)]

    slot_candidates: List[List[int]] = []
    for slot in slots:
        cand = [idx for idx, player in enumerate(players) if _eligible_for_slot(player, slot, sport)]
        slot_candidates.append(cand)

    # If any slot has no eligible player, there is no complete assignment.
    if any(len(c) == 0 for c in slot_candidates):
        return [(slot, None) for slot in slots]

    # Fill most-constrained slots first, but return in original roster order.
    order = sorted(range(len(slots)), key=lambda i: len(slot_candidates[i]))
    assigned_idx: Dict[int, int] = {}
    used_players: set[int] = set()

    def backtrack(pos: int) -> bool:
        if pos >= len(order):
            return True
        slot_i = order[pos]
        candidates = sorted(
            slot_candidates[slot_i],
            key=lambda idx: (len([j for j in range(len(slots)) if idx in slot_candidates[j]]), -_proj(players[idx]), _salary(players[idx]))
        )
        for player_i in candidates:
            if player_i in used_players:
                continue
            used_players.add(player_i)
            assigned_idx[slot_i] = player_i
            if backtrack(pos + 1):
                return True
            assigned_idx.pop(slot_i, None)
            used_players.remove(player_i)
        return False

    if not backtrack(0):
        return [(slot, None) for slot in slots]

    return [(slot, players[assigned_idx[i]]) for i, slot in enumerate(slots)]


def lineup_is_complete_for_sport(lineup: List[Dict[str, Any]], sport: str) -> bool:
    assigned = lineup_slots_for_sport(lineup, sport)
    return bool(assigned) and all(player is not None for _, player in assigned)

