# mlb_batting_order.py
from __future__ import annotations

import csv
import re
from collections import defaultdict
from typing import Any, Dict, List


_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}


def _norm_name(name: Any) -> str:
    s = str(name or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    parts = s.split()
    if parts and parts[-1] in _SUFFIXES:
        parts = parts[:-1]
    return " ".join(parts)


def _norm_team(team: Any) -> str:
    return str(team or "").strip().upper()


def _canon_col(col: Any) -> str:
    raw = str(col or "").replace("\ufeff", "").strip()
    c = raw.lower().replace("_", " ").replace("-", " ")
    c = re.sub(r"\s+", " ", c).strip()
    aliases = {
        "player": "Name", "player name": "Name", "name": "Name", "full name": "Name",
        "team": "Team", "team abbr": "Team", "teamabbrev": "Team",
        "batting order": "BattingOrder", "order": "BattingOrder", "lineup spot": "BattingOrder", "spot": "BattingOrder", "bo": "BattingOrder",
        "bats": "Bats", "bat side": "Bats", "hand": "Bats", "handedness": "Bats", "hitter hand": "Bats",
        "confirmed": "ConfirmedLineup", "confirmed lineup": "ConfirmedLineup", "is confirmed": "ConfirmedLineup", "lineup confirmed": "ConfirmedLineup",
        "status": "LineupStatus", "lineup status": "LineupStatus",
    }
    return aliases.get(c, raw)


def _to_int(x: Any, default: int = 0) -> int:
    try:
        s = str(x or "").strip()
        if not s:
            return default
        m = re.search(r"\d+", s)
        return int(m.group(0)) if m else default
    except Exception:
        return default


def _to_bool(x: Any) -> bool:
    s = str(x or "").strip().lower()
    return s in {"1", "true", "yes", "y", "confirmed", "active", "starting", "starter"}


def read_batting_order_csv(path: str) -> Dict[tuple[str, str], Dict[str, Any]]:
    out: Dict[tuple[str, str], Dict[str, Any]] = {}
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
            name = row.get("Name")
            if not name:
                continue
            team = row.get("Team", "")
            rec = {
                "BattingOrder": _to_int(row.get("BattingOrder"), 0),
                "Bats": str(row.get("Bats") or "").strip().upper(),
                "ConfirmedLineup": _to_bool(row.get("ConfirmedLineup")) or str(row.get("LineupStatus") or "").strip().lower() == "confirmed",
                "LineupStatus": str(row.get("LineupStatus") or "").strip(),
            }
            out[(_norm_name(name), _norm_team(team))] = rec
            out.setdefault((_norm_name(name), ""), rec)
    return out


def apply_batting_order(players: List[Dict[str, Any]], path: str) -> Dict[str, Any]:
    rows = read_batting_order_csv(path)
    matched = 0
    for p in players or []:
        key = (_norm_name(p.get("Name")), _norm_team(p.get("Team")))
        rec = rows.get(key) or rows.get((key[0], ""))
        if not rec:
            continue
        matched += 1
        p["BattingOrder"] = int(rec.get("BattingOrder", 0) or 0)
        p["Bats"] = str(rec.get("Bats") or "").strip().upper()
        p["ConfirmedLineup"] = bool(rec.get("ConfirmedLineup", False))
        p["LineupStatus"] = str(rec.get("LineupStatus") or "").strip()
    return {"matched": matched, "total": len(players or []), "path": path}


def clear_batting_order(players: List[Dict[str, Any]]) -> None:
    for p in players or []:
        for k in ("BattingOrder", "Bats", "ConfirmedLineup", "LineupStatus"):
            p.pop(k, None)


def _position_tokens(p: Dict[str, Any]) -> set[str]:
    raw = str(p.get("Position", "") or "").upper().replace("/", ",").replace(";", ",")
    return {x.strip() for x in raw.split(",") if x.strip()}


def _is_pitcher(p: Dict[str, Any]) -> bool:
    return bool(_position_tokens(p) & {"P", "SP", "RP"})


def _is_hitter(p: Dict[str, Any]) -> bool:
    return bool(_position_tokens(p) & {"C", "1B", "2B", "3B", "SS", "OF", "LF", "CF", "RF"}) and not _is_pitcher(p)


def build_best_stacks(players: List[Dict[str, Any]], top_n: int = 30) -> List[Dict[str, Any]]:
    teams: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for p in players or []:
        if not _is_hitter(p):
            continue
        team = str(p.get("Team") or "").strip().upper()
        if team:
            teams[team].append(p)

    rows: List[Dict[str, Any]] = []
    for team, plist in teams.items():
        hitters = sorted(plist, key=lambda p: float(p.get("FlexProjection", 0.0) or 0.0), reverse=True)
        top5 = hitters[:5]
        top8 = hitters[:8]
        if not top5:
            continue
        proj5 = sum(float(p.get("FlexProjection", 0.0) or 0.0) for p in top5)
        proj8 = sum(float(p.get("FlexProjection", 0.0) or 0.0) for p in top8)
        form = sum(float(p.get("MLBRecentForm", 0.0) or 0.0) for p in top5) / max(1, len(top5))
        matchup = sum(float(p.get("MLBMatchup", 0.0) or 0.0) for p in top5) / max(1, len(top5))
        park = sum(float(p.get("MLBBallpark", 0.0) or 0.0) for p in top5) / max(1, len(top5))
        weather = sum(float(p.get("MLBWeather", 0.0) or 0.0) for p in top5) / max(1, len(top5))
        vegas = sum(float(p.get("MLBVegas", 0.0) or 0.0) for p in top5) / max(1, len(top5))
        stack_factor = sum(float(p.get("MLBStack", 0.0) or 0.0) for p in top5) / max(1, len(top5))
        team_adj = sum(float(p.get("TeamAdjPct", 0.0) or 0.0) for p in top5) / max(1, len(top5))
        confirmed = sum(1 for p in plist if p.get("ConfirmedLineup"))
        top_order = sum(1 for p in plist if 1 <= int(p.get("BattingOrder", 0) or 0) <= 5)
        order_bonus = min(3.0, 0.55 * top_order + 0.15 * confirmed)
        score = (
            proj5
            + 1.6 * form
            + 2.0 * matchup
            + 1.1 * park
            + 0.8 * weather
            + 1.4 * vegas
            + 1.2 * stack_factor
            + 0.08 * team_adj
            + order_bonus
        )
        top_names = ", ".join(
            f"{p.get('Name','')}" + (f"({int(p.get('BattingOrder'))})" if int(p.get('BattingOrder', 0) or 0) else "")
            for p in top5
        )
        rows.append({
            "Team": team,
            "Score": score,
            "ProjTop5": proj5,
            "ProjTop8": proj8,
            "Form": form,
            "Matchup": matchup,
            "Park": park,
            "Weather": weather,
            "Vegas": vegas,
            "Stack": stack_factor,
            "TeamAdj": team_adj,
            "Confirmed": confirmed,
            "TopOrder": top_order,
            "TopHitters": top_names,
        })
    rows.sort(key=lambda r: float(r.get("Score", 0.0) or 0.0), reverse=True)
    return rows[:top_n]
