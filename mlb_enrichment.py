# mlb_enrichment.py
from __future__ import annotations

import csv
import re
from typing import Any, Dict, List, Optional, Tuple


def _norm_name(name: Any) -> str:
    s = str(name or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    for suf in (" jr.", " jr", " sr.", " sr", " ii", " iii", " iv", " v"):
        if s.endswith(suf):
            s = s[: -len(suf)].strip()
            break
    return s


def _norm_team(team: Any) -> str:
    return str(team or "").strip().upper()


def _canon_col(col: Any) -> str:
    raw = str(col or "").strip().replace("\ufeff", "")
    c = raw.lower().replace("_", " ").replace("-", " ")
    c = re.sub(r"\s+", " ", c).strip()
    aliases = {
        "player": "Name", "player name": "Name", "name": "Name", "full name": "Name",
        "team": "Team", "teamabbrev": "Team", "team abbrev": "Team",
        "recent": "RecentForm", "recent form": "RecentForm", "form": "RecentForm", "last 5": "RecentForm", "last 10": "RecentForm", "l10": "RecentForm",
        "matchup": "Matchup", "pitcher matchup": "Matchup", "opp pitcher": "Matchup", "opposing pitcher": "Matchup",
        "ballpark": "Ballpark", "park": "Ballpark", "park factor": "Ballpark",
        "weather": "Weather", "wind": "Weather",
        "vegas": "Vegas", "team total": "Vegas", "implied total": "Vegas", "implied runs": "Vegas",
        "stack": "Stack", "stack score": "Stack",
        "hr": "HR", "hr score": "HR", "hr upside": "HR",
        "boost": "Boost", "projection boost": "Boost", "proj boost": "Boost", "adjustment": "Boost", "adj": "Boost",
        "own": "OwnPct", "ownership": "OwnPct", "own pct": "OwnPct", "projected ownership": "OwnPct",
        "notes": "Notes", "note": "Notes",
    }
    return aliases.get(c, raw)


def _to_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        s = str(x).replace("%", "").replace("+", "").replace(",", "").strip()
        if s == "":
            return default
        return float(s)
    except Exception:
        return default


def _score_from_value(x: Any) -> float:
    """Accept numeric scores or Favorable/Neutral/Bad style labels."""
    if x is None:
        return 0.0
    s = str(x).strip().lower()
    if s == "":
        return 0.0
    label_map = {
        "elite": 2.0,
        "great": 1.5,
        "good": 1.0,
        "favorable": 1.0,
        "plus": 0.75,
        "neutral": 0.0,
        "average": 0.0,
        "bad": -1.0,
        "poor": -1.0,
        "negative": -1.0,
        "unfavorable": -1.0,
        "avoid": -1.5,
    }
    if s in label_map:
        return label_map[s]
    return _to_float(s, 0.0)


def read_mlb_factors_csv(path: str) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Read an optional MLB factors CSV keyed by normalized (name, team).

    Supported columns include:
      Name, Team, RecentForm, Matchup, Ballpark, Weather, Vegas, Stack, HR, Boost, OwnPct, Notes

    Scores may be numeric or labels like Favorable / Neutral / Bad.
    """
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except Exception:
            dialect = csv.excel
        reader = csv.DictReader(f, dialect=dialect)
        if not reader.fieldnames:
            return out
        for raw in reader:
            row: Dict[str, Any] = {}
            for k, v in (raw or {}).items():
                row[_canon_col(k)] = v
            name = row.get("Name") or row.get("Player")
            if not name:
                continue
            team = row.get("Team", "")
            clean = {
                "RecentForm": _score_from_value(row.get("RecentForm")),
                "Matchup": _score_from_value(row.get("Matchup")),
                "Ballpark": _score_from_value(row.get("Ballpark")),
                "Weather": _score_from_value(row.get("Weather")),
                "Vegas": _score_from_value(row.get("Vegas")),
                "Stack": _score_from_value(row.get("Stack")),
                "HR": _score_from_value(row.get("HR")),
                "Boost": _score_from_value(row.get("Boost")),
                "OwnPct": _to_float(row.get("OwnPct"), 0.0),
                "Notes": str(row.get("Notes") or "").strip(),
            }
            out[(_norm_name(name), _norm_team(team))] = clean
            # also allow name-only fallback
            out.setdefault((_norm_name(name), ""), clean)
    return out


def apply_mlb_factors(players: List[Dict[str, Any]], factors_path: Optional[str] = None) -> Dict[str, Any]:
    """Apply MLB factor adjustments in-place.

    The optimizer already uses FlexProjection, so this function preserves BaseProjection
    then writes adjusted FlexProjection/CptProjection back to the player dict.
    """
    factors = read_mlb_factors_csv(factors_path) if factors_path else {}
    matched = 0
    hitter_positions = {"C", "1B", "2B", "3B", "SS", "OF", "LF", "CF", "RF"}

    for p in players or []:
        pos_raw = str(p.get("Position", "")).upper().replace("/", ",")
        pos = {x.strip() for x in pos_raw.split(",") if x.strip()}
        is_hitter = bool(pos & hitter_positions)
        is_pitcher = bool(pos & {"P", "SP", "RP"})

        base = float(p.get("BaseProjection", p.get("FlexProjection", 0.0)) or 0.0)
        p.setdefault("BaseProjection", base)
        # reset to base before applying new factors, so repeated loads don't compound
        p["FlexProjection"] = base
        p["CptProjection"] = 1.5 * base

        key = (_norm_name(p.get("Name")), _norm_team(p.get("Team")))
        row = factors.get(key) or factors.get((key[0], "")) or {}
        if row:
            matched += 1

        recent = float(row.get("RecentForm", 0.0) or 0.0)
        matchup = float(row.get("Matchup", 0.0) or 0.0)
        park = float(row.get("Ballpark", 0.0) or 0.0)
        weather = float(row.get("Weather", 0.0) or 0.0)
        vegas = float(row.get("Vegas", 0.0) or 0.0)
        stack = float(row.get("Stack", 0.0) or 0.0)
        hr = float(row.get("HR", 0.0) or 0.0)
        manual = float(row.get("Boost", 0.0) or 0.0)

        # Conservative first-pass weights. Scores are "fantasy point" adjustments.
        if is_hitter:
            adj = (
                0.55 * recent +
                0.75 * matchup +
                0.35 * park +
                0.25 * weather +
                0.45 * vegas +
                0.35 * stack +
                0.35 * hr +
                manual
            )
        elif is_pitcher:
            # Pitchers care less about park/weather/stack, more about matchup/recent.
            adj = 0.65 * recent + 0.85 * matchup + 0.20 * park + 0.30 * vegas + manual
        else:
            adj = manual

        # Guardrails so a bad/huge CSV doesn't blow up projections.
        adj = max(-8.0, min(8.0, adj))
        new_proj = max(0.0, base + adj)

        p["MLBRecentForm"] = recent
        p["MLBMatchup"] = matchup
        p["MLBBallpark"] = park
        p["MLBWeather"] = weather
        p["MLBVegas"] = vegas
        p["MLBStack"] = stack
        p["MLBHR"] = hr
        p["MLBManualBoost"] = manual
        p["MLBAdjScore"] = adj
        p["MLBNotes"] = str(row.get("Notes") or "")
        p["FlexProjection"] = new_proj
        p["CptProjection"] = 1.5 * new_proj

        own = float(row.get("OwnPct", 0.0) or 0.0)
        if own > 0:
            p["ProjOwnPct"] = own
            p["ProjFlexOwnPct"] = own

    return {"matched": matched, "total": len(players or []), "path": factors_path or ""}


def clear_mlb_factors(players: List[Dict[str, Any]]) -> None:
    for p in players or []:
        base = float(p.get("BaseProjection", p.get("FlexProjection", 0.0)) or 0.0)
        p["FlexProjection"] = base
        p["CptProjection"] = 1.5 * base
        for k in [
            "MLBRecentForm", "MLBMatchup", "MLBBallpark", "MLBWeather", "MLBVegas",
            "MLBStack", "MLBHR", "MLBManualBoost", "MLBAdjScore", "MLBNotes"
        ]:
            p.pop(k, None)
