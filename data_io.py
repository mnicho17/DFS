# data_io.py
import re
import csv
from typing import List, Dict, Any


def _canon_field(name: str) -> str:
    if name is None:
        return ""
    s = str(name).replace("\ufeff", "").strip()
    low = s.lower()
    base = re.sub(r"\s*\(.*?\)\s*", "", low)
    base = base.replace("_", " ").strip()
    base = re.sub(r"\s+", " ", base)
    if base in ("name",):
        return "Name"
    if "name + id" in base or base in ("name id", "name+id"):
        return "Name + ID"
    if base in ("id",):
        return "ID"
    if base in ("team", "teamabbrev", "team abbr", "team abbrev"):
        return "Team"
    if base in ("position", "pos"):
        return "Position"
    if base in ("roster position", "roster pos"):
        return "Roster Position"
    if base in ("game info", "gameinfo", "game", "matchup"):
        return "Game Info"
    if "salary" in base:
        return "Salary"
    if base in ("projection", "proj", "avgpointspergame", "fppg", "fantasy points", "fpts"):
        return "Projection"
    return s.strip()


def _to_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        s = str(x).replace(",", "").strip()
        if s == "":
            return default
        return float(s)
    except Exception:
        return default


def _extract_name(row: Dict[str, Any]) -> str:
    name = (row.get("Name") or "").strip()
    if not name:
        name_id = (row.get("Name + ID") or "").strip()
        if name_id:
            if "(" in name_id and name_id.endswith(")"):
                name = name_id[: name_id.rfind("(")].strip()
            else:
                name = name_id
    return name


def _parse_game_context(game_info: Any, team: Any) -> Dict[str, str]:
    """Parse DK Game Info into normalized game/opponent fields.

    DraftKings commonly uses values like ``BUF@MIA 09/13/2026 01:00PM ET``.
    Older app versions kept only the raw GameInfo string, which meant NFL
    bring-back and QB-vs-DST logic could not reliably identify opponents.

    The parser is intentionally sport-agnostic: it only trusts the two team
    abbreviations present in the DK value and does not apply league-specific
    aliases that could collide across NFL/MLB/NBA.
    """
    text = str(game_info or "").strip().upper()
    team_u = str(team or "").strip().upper()
    m = re.search(r"\b([A-Z0-9]{2,4})\s*(?:@|\bAT\b)\s*([A-Z0-9]{2,4})\b", text)
    if not m:
        return {
            "AwayTeam": "",
            "HomeTeam": "",
            "Opponent": "",
            "HomeAway": "",
            "GameKey": "",
        }

    away, home = m.group(1), m.group(2)
    opponent = ""
    home_away = ""
    if team_u == away:
        opponent = home
        home_away = "A"
    elif team_u == home:
        opponent = away
        home_away = "H"

    return {
        "AwayTeam": away,
        "HomeTeam": home,
        "Opponent": opponent,
        "HomeAway": home_away,
        "GameKey": f"{away}@{home}",
    }


def read_players_csv(path: str) -> List[Dict[str, Any]]:
    """
    Read a DK Showdown/Classic-style CSV and return a list of unified player
    dicts with Flex/CPT pricing & projections.
    """
    flex_rows: Dict[str, Dict[str, Any]] = {}
    cpt_rows: Dict[str, Dict[str, Any]] = {}

    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("CSV appears to be empty.")
        for raw in reader:
            if not raw:
                continue
            row: Dict[str, Any] = {}
            for k, v in raw.items():
                canon = _canon_field(k)
                if not canon:
                    continue
                if canon not in row or str(row[canon]).strip() == "":
                    row[canon] = v

            name = _extract_name(row)
            if not name:
                continue

            team = (row.get("Team") or "").strip()
            position = (row.get("Position") or "").strip()
            roster_pos = (row.get("Roster Position") or "").strip().upper()
            salary = _to_float(row.get("Salary"), 0.0)
            projection = _to_float(row.get("Projection"), 0.0)
            game_info = (row.get("Game Info") or "").strip()
            pid = (row.get("ID") or "").strip()
            name_plus_id = (row.get("Name + ID") or "").strip()

            game_ctx = _parse_game_context(game_info, team)
            rec = {
                "Name": name,
                "Team": team,
                "Position": position,
                "GameInfo": game_info,
                **game_ctx,
                "Salary": salary,
                "Projection": projection,
                "ID": pid,
                "NamePlusID": name_plus_id,
            }

            if roster_pos == "CPT":
                cpt_rows[name] = rec
            else:
                prior = flex_rows.get(name)
                if prior is None or salary < _to_float(prior.get("Salary"), 1e9):
                    flex_rows[name] = rec

    players: List[Dict[str, Any]] = []
    for name, flex in flex_rows.items():
        cpt = cpt_rows.get(name, {})
        flex_salary = float(flex.get("Salary", 0.0))
        flex_proj = float(flex.get("Projection", 0.0))
        cpt_salary = float(cpt.get("Salary", 1.5 * flex_salary)) if cpt else 1.5 * flex_salary
        cpt_proj = 1.5 * flex_proj  # DK scoring rule

        players.append({
            "Name": flex.get("Name", name),
            "Team": flex.get("Team", ""),
            "Position": flex.get("Position", ""),
            "GameInfo": flex.get("GameInfo", ""),
            "AwayTeam": flex.get("AwayTeam", ""),
            "HomeTeam": flex.get("HomeTeam", ""),
            "Opponent": flex.get("Opponent", ""),
            "HomeAway": flex.get("HomeAway", ""),
            "GameKey": flex.get("GameKey", ""),
            "FlexSalary": flex_salary,
            "FlexProjection": flex_proj,
            "CptSalary": cpt_salary,
            "CptProjection": cpt_proj,
            "FlexID": (flex.get("ID") or ""),
            "FlexNamePlusID": (flex.get("NamePlusID") or ""),
            "CptID": (cpt.get("ID") or ""),
            "CptNamePlusID": (cpt.get("NamePlusID") or ""),
        })

    if not players:
        raise ValueError("No players found. Ensure the CSV includes FLEX rows (and CPT rows for proper CPT pricing).")
    return players


def extract_teams(players: List[Dict[str, Any]]) -> List[str]:
    teams: List[str] = []
    seen = set()
    for p in players:
        t = p["Team"]
        if t and t not in seen:
            seen.add(t)
            teams.append(t)
    return teams
