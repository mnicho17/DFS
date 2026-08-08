# injury_api.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import logging
from datetime import datetime

try:
    import requests  # type: ignore
    HAS_REQUESTS = True
except Exception:
    HAS_REQUESTS = False

logger = logging.getLogger("dfs.injury")

SLEEPER_PLAYERS_URL = "https://api.sleeper.app/v1/players/nfl"
MLB_API_BASE = "https://statsapi.mlb.com/api/v1"

# MLB Stats API team IDs. We fetch active teams dynamically first, then fall back
# to these if the teams endpoint is unavailable.
MLB_TEAM_ID_FALLBACK: Dict[str, int] = {
    "ARI": 109, "ATL": 144, "BAL": 110, "BOS": 111, "CHC": 112,
    "CWS": 145, "CIN": 113, "CLE": 114, "COL": 115, "DET": 116,
    "HOU": 117, "KC": 118, "LAA": 108, "LAD": 119, "MIA": 146,
    "MIL": 158, "MIN": 142, "NYM": 121, "NYY": 147, "OAK": 133,
    "PHI": 143, "PIT": 134, "SD": 135, "SEA": 136, "SF": 137,
    "STL": 138, "TB": 139, "TEX": 140, "TOR": 141, "WSH": 120,
}

_TEAM_ALIAS = {
    "AZ": "ARI", "CHW": "CWS", "CHA": "CWS", "KC": "KC", "KCR": "KC",
    "LA": "LAD", "LAN": "LAD", "LAD": "LAD", "LAA": "LAA",
    "NY": "NYY", "WSN": "WSH", "WAS": "WSH", "SDP": "SD", "SFG": "SF",
    "TBR": "TB", "TAM": "TB", "OAK": "OAK", "ATH": "OAK",
}
_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}


def _normalize_name(name: str) -> str:
    if not name:
        return ""
    parts = str(name).strip().lower().split()
    if not parts:
        return ""
    if parts[-1] in _SUFFIXES and len(parts) >= 2:
        parts = parts[:-1]
    return " ".join(parts)


def _fetch_sleeper_players(timeout_sec: int = 10) -> Optional[Dict[str, Any]]:
    if not HAS_REQUESTS:
        logger.warning("requests not available; skipping injury enrichment.")
        return None
    try:
        resp = requests.get(SLEEPER_PLAYERS_URL, timeout=timeout_sec)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            logger.warning("Unexpected Sleeper response (not a dict).")
            return None
        return data
    except Exception as e:
        logger.warning("Failed to fetch Sleeper players: %s", e)
        return None


def _build_name_index(sleeper_data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    for _, pdata in sleeper_data.items():
        full_name = pdata.get("full_name") or pdata.get("first_name")
        if not full_name:
            continue
        index[_normalize_name(full_name)] = pdata
    return index



def _normalize_team(team: Any) -> str:
    raw = str(team or "").strip().upper()
    raw = raw.replace(".", "").replace(" ", "")
    return _TEAM_ALIAS.get(raw, raw)


def _fetch_json(url: str, timeout_sec: int = 10) -> Optional[Dict[str, Any]]:
    if not HAS_REQUESTS:
        logger.warning("requests not available; skipping injury enrichment.")
        return None
    try:
        resp = requests.get(url, timeout=timeout_sec)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else None
    except Exception as e:
        logger.warning("Failed to fetch %s: %s", url, e)
        return None


def _fetch_mlb_team_ids(timeout_sec: int = 10) -> Dict[str, int]:
    url = f"{MLB_API_BASE}/teams?sportId=1&activeStatus=Y"
    data = _fetch_json(url, timeout_sec=timeout_sec)
    out: Dict[str, int] = {}
    for t in (data or {}).get("teams", []) or []:
        try:
            abbr = _normalize_team(t.get("abbreviation") or t.get("teamCode") or t.get("fileCode"))
            tid = int(t.get("id"))
            if abbr and tid:
                out[abbr] = tid
        except Exception:
            continue
    return out or dict(MLB_TEAM_ID_FALLBACK)


def _mlb_status_is_injured(status_desc: str, status_code: str = "") -> bool:
    text = f"{status_code} {status_desc}".strip().lower()
    if not text:
        return False
    injured_markers = (
        "injured", "injury", "il", "10-day", "15-day", "60-day",
        "concussion", "bereavement", "paternity", "restricted", "suspended",
    )
    # Avoid false-positive on Active.
    if text in {"a active", "active"}:
        return False
    return any(m in text for m in injured_markers)


def _fetch_mlb_injury_index(timeout_sec: int = 10) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Build an MLB injury/status index keyed by (normalized name, team abbr).

    Source: MLB Stats API team fullRoster endpoint. This captures players whose
    current roster status is IL/injured/restricted/etc. without requiring an API key.
    """
    teams = _fetch_mlb_team_ids(timeout_sec=timeout_sec)
    season = datetime.now().year
    index: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for abbr, team_id in teams.items():
        url = f"{MLB_API_BASE}/teams/{team_id}/roster?rosterType=fullRoster&season={season}&hydrate=person"
        data = _fetch_json(url, timeout_sec=timeout_sec)
        roster = (data or {}).get("roster", []) or []
        for row in roster:
            person = row.get("person") or {}
            full_name = person.get("fullName") or person.get("full_name") or row.get("person", {}).get("name")
            if not full_name:
                continue
            status = row.get("status") or person.get("status") or {}
            status_code = str(status.get("code") or "").strip()
            status_desc = str(status.get("description") or row.get("statusDescription") or "").strip()
            if not _mlb_status_is_injured(status_desc, status_code):
                continue
            rec = {
                "InjuryStatus": status_desc or status_code or "Injured",
                "InjuryBodyPart": "",
                "InjuryStartDate": "",
                "InjurySource": "MLB Stats API",
            }
            n = _normalize_name(full_name)
            team_norm = _normalize_team(abbr)
            index[(n, team_norm)] = rec
            index.setdefault((n, ""), rec)
    return index


def enrich_players_with_mlb_injuries(players: List[Dict[str, Any]]) -> None:
    """Add MLB current roster injury/status data in-place.

    Uses MLB Stats API fullRoster status data. Players on IL / injured / suspended /
    restricted-style statuses are tagged; active players are left blank.
    """
    if not players:
        return
    index = _fetch_mlb_injury_index()
    if not index:
        logger.info("No MLB injury/status data found; leaving InjuryStatus blank.")
        return

    hit = 0
    for p in players:
        name = _normalize_name(str(p.get("Name", "")))
        team = _normalize_team(p.get("Team"))
        rec = index.get((name, team)) or index.get((name, ""))
        if not rec:
            # Clear stale values from a previous sport/load, but do not touch user fades.
            p["InjuryStatus"] = ""
            p["InjuryBodyPart"] = ""
            p["InjuryStartDate"] = ""
            p["InjurySource"] = ""
            continue
        p.update(rec)
        status = str(p.get("InjuryStatus") or "").strip().lower()
        if _mlb_status_is_injured(status):
            p["LockFlex"] = False
            p["LockCpt"] = False
            p["FadeFlex"] = True
            p["FadeCpt"] = True
            p["AutoFadeInjury"] = True
        hit += 1

    logger.info("MLB injury enrichment complete: %d/%d matched by name/team", hit, len(players))


def clear_injury_fields(players: List[Dict[str, Any]]) -> None:
    for p in players or []:
        p["InjuryStatus"] = ""
        p["InjuryBodyPart"] = ""
        p["InjuryStartDate"] = ""
        p["InjurySource"] = ""
        if p.get("AutoFadeInjury") is True:
            p["FadeFlex"] = False
            p["FadeCpt"] = False
            p["AutoFadeInjury"] = False

def enrich_players_with_injuries(players: List[Dict[str, Any]], sport: str = "NFL") -> None:
    """
    Adds/updates:
      - InjuryStatus
      - InjuryBodyPart
      - InjuryStartDate

    sport="NFL" uses Sleeper. sport="MLB" uses MLB Stats API. Other sports
    currently clear/skip injury fields until a sport-specific source is added.
    """
    if not players:
        return

    sport_u = (sport or "NFL").strip().upper()
    if sport_u == "MLB":
        enrich_players_with_mlb_injuries(players)
        return
    if sport_u != "NFL":
        clear_injury_fields(players)
        logger.info("Skipping injury enrichment for unsupported sport: %s", sport_u)
        return

    sleeper = _fetch_sleeper_players()
    if not sleeper:
        logger.info("No Sleeper data; leaving InjuryStatus blank.")
        return

    index = _build_name_index(sleeper)

    hit = 0
    for p in players:
        name = str(p.get("Name", "")).strip()
        sp = index.get(_normalize_name(name))
        if not sp:
            continue

        p["InjuryStatus"] = sp.get("injury_status") or ""
        p["InjuryBodyPart"] = sp.get("injury_body_part") or ""
        p["InjuryStartDate"] = sp.get("injury_start_date") or ""

        # --- Auto-fade players who are effectively OUT ---
        # We keep this reversible by tracking a flag. If the player later becomes active,
        # we remove the auto-fade (but we don't touch user-set locks).
        status = str(p.get("InjuryStatus") or "").strip().lower()
        out_like = {
            "out", "ir", "pup", "susp", "suspended", "covid", "nfi",
            "inactive", "dnp"
        }
        is_out = status in out_like

        # Initialize marker if missing
        if "AutoFadeInjury" not in p:
            p["AutoFadeInjury"] = False

        if is_out:
            # Clear locks; then fade both CPT + FLEX eligibility.
            p["LockFlex"] = False
            p["LockCpt"] = False
            p["FadeFlex"] = True
            p["FadeCpt"] = True
            p["AutoFadeInjury"] = True
        else:
            # If we previously auto-faded and the player is no longer OUT-like, un-fade.
            if p.get("AutoFadeInjury") is True:
                p["FadeFlex"] = False
                p["FadeCpt"] = False
                p["AutoFadeInjury"] = False
        hit += 1

    logger.info("Injury enrichment complete: %d/%d matched by name", hit, len(players))
