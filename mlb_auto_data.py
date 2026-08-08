from __future__ import annotations

"""Automatic MLB context enrichment.

Adds two no-upload data sources for MLB slates:
  1) Static ballpark run/HR environment scores inferred from DK GameInfo home team.
  2) Official batting order / handedness when MLB Stats API boxscore has posted lineups.

The functions are defensive. If the network is unavailable or lineups are not yet
posted, the app still loads and simply leaves those fields blank.
"""

import datetime as _dt
import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import requests  # type: ignore
    HAS_REQUESTS = True
except Exception:
    HAS_REQUESTS = False

logger = logging.getLogger("dfs.mlb_auto")

MLB_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
MLB_BOXSCORE_URL = "https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"

_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}

# DK and MLB abbreviation compatibility.
_TEAM_ALIASES = {
    "ARI": "ARI", "AZ": "ARI",
    "ATL": "ATL",
    "BAL": "BAL",
    "BOS": "BOS",
    "CHC": "CHC", "CUBS": "CHC",
    "CWS": "CWS", "CHW": "CWS", "WSH": "WSH",
    "CIN": "CIN",
    "CLE": "CLE",
    "COL": "COL",
    "DET": "DET",
    "HOU": "HOU",
    "KC": "KC", "KCR": "KC",
    "LAA": "LAA", "ANA": "LAA",
    "LAD": "LAD", "LA": "LAD",
    "MIA": "MIA", "FLA": "MIA",
    "MIL": "MIL",
    "MIN": "MIN",
    "NYM": "NYM",
    "NYY": "NYY",
    "ATH": "ATH", "OAK": "ATH",  # Current Athletics abbreviation has varied by feed/source.
    "PHI": "PHI",
    "PIT": "PIT",
    "SD": "SD", "SDP": "SD",
    "SEA": "SEA",
    "SF": "SF", "SFG": "SF",
    "STL": "STL",
    "TB": "TB", "TBR": "TB",
    "TEX": "TEX",
    "TOR": "TOR",
    "WSH": "WSH", "WAS": "WSH",
}

# Conservative DFS-friendly factor scale. Positive helps hitters in that game.
# Ballpark is broad run environment; HR is narrower power environment.
# These are intentionally modest inputs layered into mlb_enrichment-style fields.
_BALLPARK_FACTORS: Dict[str, Dict[str, float]] = {
    "ARI": {"park": 0.45, "hr": 0.10},
    "ATL": {"park": 0.35, "hr": 0.35},
    "BAL": {"park": -0.25, "hr": -0.55},
    "BOS": {"park": 0.80, "hr": -0.10},
    "CHC": {"park": 0.15, "hr": 0.15},
    "CWS": {"park": 0.25, "hr": 0.40},
    "CIN": {"park": 0.55, "hr": 0.90},
    "CLE": {"park": -0.10, "hr": -0.20},
    "COL": {"park": 2.00, "hr": 0.85},
    "DET": {"park": -0.15, "hr": -0.30},
    "HOU": {"park": 0.15, "hr": 0.35},
    "KC": {"park": 0.15, "hr": -0.45},
    "LAA": {"park": -0.05, "hr": 0.05},
    "LAD": {"park": 0.00, "hr": 0.10},
    "MIA": {"park": -0.45, "hr": -0.55},
    "MIL": {"park": 0.25, "hr": 0.45},
    "MIN": {"park": -0.05, "hr": 0.05},
    "NYM": {"park": -0.35, "hr": -0.25},
    "NYY": {"park": 0.15, "hr": 0.60},
    "ATH": {"park": -0.20, "hr": -0.10},
    "PHI": {"park": 0.35, "hr": 0.55},
    "PIT": {"park": -0.25, "hr": -0.45},
    "SD": {"park": -0.10, "hr": -0.20},
    "SEA": {"park": -0.45, "hr": -0.25},
    "SF": {"park": -0.30, "hr": -0.65},
    "STL": {"park": -0.05, "hr": -0.15},
    "TB": {"park": -0.10, "hr": 0.05},
    "TEX": {"park": 0.35, "hr": 0.30},
    "TOR": {"park": 0.30, "hr": 0.40},
    "WSH": {"park": 0.10, "hr": 0.15},
}


def _norm_name(name: Any) -> str:
    s = str(name or "").strip().lower()
    s = re.sub(r"[^a-z0-9\s.'-]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    parts = s.split()
    if parts and parts[-1] in _SUFFIXES:
        parts = parts[:-1]
    return " ".join(parts)


def _norm_team(team: Any) -> str:
    t = str(team or "").strip().upper()
    t = re.sub(r"[^A-Z0-9]", "", t)
    return _TEAM_ALIASES.get(t, t)


def _position_tokens(p: Dict[str, Any]) -> set[str]:
    raw = str(p.get("Position", "") or "").upper().replace("/", ",").replace(";", ",")
    return {x.strip() for x in raw.split(",") if x.strip()}


def _is_hitter(p: Dict[str, Any]) -> bool:
    pos = _position_tokens(p)
    return bool(pos & {"C", "1B", "2B", "3B", "SS", "OF", "LF", "CF", "RF"}) and not bool(pos & {"P", "SP", "RP"})


def _parse_game_teams(game_info: Any) -> Tuple[str, str]:
    """Return (away, home) from DK-ish GameInfo values like 'NYY@BOS 7:10PM ET'."""
    s = str(game_info or "").strip().upper()
    m = re.search(r"\b([A-Z]{2,3})\s*@\s*([A-Z]{2,3})\b", s)
    if m:
        return _norm_team(m.group(1)), _norm_team(m.group(2))
    m = re.search(r"\b([A-Z]{2,3})\s+AT\s+([A-Z]{2,3})\b", s)
    if m:
        return _norm_team(m.group(1)), _norm_team(m.group(2))
    return "", ""


def apply_auto_ballpark_factors(players: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Apply built-in park/HR context to MLB hitters based on each player's game home team."""
    matched = 0
    games_seen = set()
    for p in players or []:
        if not _is_hitter(p):
            continue
        away, home = _parse_game_teams(p.get("GameInfo"))
        if not home:
            # DK files usually have GameInfo, but fall back to neutral if absent.
            continue
        games_seen.add((away, home))
        factor = _BALLPARK_FACTORS.get(home)
        if not factor:
            continue
        park = float(factor.get("park", 0.0) or 0.0)
        hr = float(factor.get("hr", 0.0) or 0.0)
        p["MLBBallpark"] = park
        p["MLBHR"] = hr
        p["MLBAutoPark"] = True
        # Let the UI show this was generated, even if no manual factors were loaded.
        p["MLBNotes"] = (str(p.get("MLBNotes") or "") + " Auto park").strip()
        matched += 1
    return {"matched": matched, "total": len(players or []), "games": len(games_seen)}


def _fetch_json(url: str, params: Optional[Dict[str, Any]] = None, timeout_sec: int = 8) -> Optional[Dict[str, Any]]:
    if not HAS_REQUESTS:
        logger.info("requests not available; skipping MLB auto fetch.")
        return None
    try:
        resp = requests.get(url, params=params or {}, timeout=timeout_sec)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else None
    except Exception as e:
        logger.info("MLB auto fetch failed: %s", e)
        return None


def _slate_dates_from_gameinfo(players: Iterable[Dict[str, Any]]) -> List[str]:
    # DK GameInfo may not include ISO dates. Use today as default; if future date-like
    # strings are present, include them too.
    dates = {_dt.date.today().isoformat()}
    for p in players or []:
        s = str(p.get("GameInfo") or "")
        for m in re.finditer(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", s):
            y, mo, d = map(int, m.groups())
            try:
                dates.add(_dt.date(y, mo, d).isoformat())
            except Exception:
                pass
    return sorted(dates)


def _collect_slate_game_pairs(players: Iterable[Dict[str, Any]]) -> set[Tuple[str, str]]:
    pairs = set()
    for p in players or []:
        away, home = _parse_game_teams(p.get("GameInfo"))
        if away and home:
            pairs.add((away, home))
    return pairs


def _schedule_game_pks(players: List[Dict[str, Any]]) -> List[int]:
    slate_pairs = _collect_slate_game_pairs(players)
    dates = _slate_dates_from_gameinfo(players)
    out: List[int] = []
    for date_s in dates:
        data = _fetch_json(MLB_SCHEDULE_URL, {"sportId": 1, "date": date_s, "hydrate": "team"})
        if not data:
            continue
        for date_block in data.get("dates", []) or []:
            for game in date_block.get("games", []) or []:
                teams = game.get("teams", {}) or {}
                away_abbr = _norm_team(((teams.get("away") or {}).get("team") or {}).get("abbreviation"))
                home_abbr = _norm_team(((teams.get("home") or {}).get("team") or {}).get("abbreviation"))
                if slate_pairs and (away_abbr, home_abbr) not in slate_pairs:
                    continue
                game_pk = game.get("gamePk")
                if game_pk is not None:
                    try:
                        out.append(int(game_pk))
                    except Exception:
                        pass
    return sorted(set(out))


def _extract_batting_order_from_boxscore(box: Dict[str, Any]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Return map keyed by (name, team_abbrev) from an MLB Stats API boxscore."""
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    teams = box.get("teams", {}) or {}
    for side in ("away", "home"):
        team_block = teams.get(side) or {}
        team_abbr = _norm_team(((team_block.get("team") or {}).get("abbreviation")) or "")
        players = team_block.get("players", {}) or {}
        for pdata in players.values():
            person = pdata.get("person", {}) or {}
            name = person.get("fullName") or person.get("boxscoreName") or ""
            if not name:
                continue
            bo_raw = pdata.get("battingOrder")
            if not bo_raw:
                continue
            # MLB boxscore uses 100, 200, ... where /100 is lineup slot.
            try:
                order = int(str(bo_raw)[:1]) if len(str(bo_raw)) >= 3 else int(bo_raw)
                if order > 9:
                    order = int(order / 100)
            except Exception:
                order = 0
            if order <= 0:
                continue
            bat_side = ""
            try:
                bat_side = str((person.get("batSide") or {}).get("code") or "").upper()
            except Exception:
                bat_side = ""
            out[(_norm_name(name), team_abbr)] = {
                "BattingOrder": order,
                "Bats": bat_side,
                "ConfirmedLineup": True,
                "LineupStatus": "confirmed",
            }
            # name-only fallback for DK team abbreviation mismatches.
            out.setdefault((_norm_name(name), ""), out[(_norm_name(name), team_abbr)])
    return out


def fetch_official_batting_orders(players: List[Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Fetch official MLB batting orders for slate games when lineups are posted."""
    orders: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for game_pk in _schedule_game_pks(players):
        box = _fetch_json(MLB_BOXSCORE_URL.format(game_pk=game_pk))
        if not box:
            continue
        orders.update(_extract_batting_order_from_boxscore(box))
    return orders


def apply_official_batting_orders(players: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Apply official batting order data to loaded DK players if it is available."""
    orders = fetch_official_batting_orders(players)
    matched = 0
    for p in players or []:
        key = (_norm_name(p.get("Name")), _norm_team(p.get("Team")))
        rec = orders.get(key) or orders.get((key[0], ""))
        if not rec:
            continue
        p["BattingOrder"] = int(rec.get("BattingOrder", 0) or 0)
        p["Bats"] = str(rec.get("Bats") or "").strip().upper()
        p["ConfirmedLineup"] = bool(rec.get("ConfirmedLineup", False))
        p["LineupStatus"] = str(rec.get("LineupStatus") or "confirmed")
        matched += 1
    return {"matched": matched, "total": len(players or []), "games": len(_collect_slate_game_pairs(players))}



def apply_projected_batting_orders(players: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Fallback projected lineup slots when official orders are not posted.

    This guarantees MLB hitters get a visible order/status on CSV load. It ranks
    hitters within each team by current projection and assigns 1-9 repeatedly.
    Official confirmed lineups, when available, are never overwritten.
    """
    by_team: Dict[str, List[Dict[str, Any]]] = {}
    for p in players or []:
        if not _is_hitter(p):
            continue
        if int(p.get("BattingOrder", 0) or 0) > 0 and p.get("ConfirmedLineup"):
            continue
        team = _norm_team(p.get("Team"))
        if not team:
            continue
        by_team.setdefault(team, []).append(p)

    matched = 0
    for team, plist in by_team.items():
        ranked = sorted(plist, key=lambda x: float(x.get("BaseProjection", x.get("FlexProjection", 0.0)) or 0.0), reverse=True)
        for idx, player in enumerate(ranked):
            # DK slates can include more than 9 hitters/team; assign bench-depth players after 9 as blank bench.
            if idx < 9:
                player["BattingOrder"] = idx + 1
                player["LineupStatus"] = "projected"
                player["ConfirmedLineup"] = False
                matched += 1
            else:
                player.setdefault("LineupStatus", "bench/proj")
    return {"matched": matched, "teams": len(by_team), "total": len(players or [])}


def _order_projection_bonus(p: Dict[str, Any]) -> float:
    try:
        order = int(p.get("BattingOrder", 0) or 0)
    except Exception:
        order = 0
    if order in (1, 2, 3):
        return 0.65
    if order in (4, 5):
        return 0.45
    if order == 6:
        return 0.10
    if order in (7, 8, 9):
        return -0.25
    return 0.0


def apply_auto_projection_adjustments(players: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Fold auto-generated MLB context into FlexProjection/CptProjection.

    This is intentionally modest so it nudges simulations/optimizer behavior
    without overpowering the user's projection source. Manual MLB factor CSVs can
    still override this later because apply_mlb_factors resets to BaseProjection.
    """
    changed = 0
    for p in players or []:
        if not _is_hitter(p):
            continue
        base = float(p.get("BaseProjection", p.get("FlexProjection", 0.0)) or 0.0)
        p.setdefault("BaseProjection", base)
        park = float(p.get("MLBBallpark", 0.0) or 0.0)
        hr = float(p.get("MLBHR", 0.0) or 0.0)
        order_bonus = _order_projection_bonus(p)
        adj = 0.35 * park + 0.35 * hr + order_bonus
        if abs(adj) <= 1e-9:
            continue
        p["MLBAdjScore"] = float(p.get("MLBAdjScore", 0.0) or 0.0) + adj
        p["FlexProjection"] = max(0.0, base + float(p.get("MLBAdjScore", 0.0) or 0.0))
        p["CptProjection"] = 1.5 * float(p.get("FlexProjection", 0.0) or 0.0)
        changed += 1
    return {"adjusted": changed, "total": len(players or [])}

def apply_auto_mlb_context(players: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Apply built-in ballpark context and official/projected batting orders in one call."""
    park = apply_auto_ballpark_factors(players)
    official = apply_official_batting_orders(players)
    projected = apply_projected_batting_orders(players)
    proj = apply_auto_projection_adjustments(players)
    return {"ballpark": park, "official_batting_order": official, "projected_batting_order": projected, "projection": proj}
