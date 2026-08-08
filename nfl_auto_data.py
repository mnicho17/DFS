from __future__ import annotations

"""Automatic, keyless NFL context enrichment.

Sleeper supplies role/depth/practice context, nflverse supplies recent usage and
opponent production allowed, and Open-Meteo supplies game-time outdoor weather.
Every source is optional: unavailable or malformed data leaves its component at
neutral, and the combined projection change is capped at +/- 3.5 DK points.
"""

import csv
import datetime as dt
import gzip
import io
import logging
import math
import re
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

try:
    import requests  # type: ignore
    HAS_REQUESTS = True
except Exception:
    HAS_REQUESTS = False

logger = logging.getLogger("dfs.nfl_auto")

SLEEPER_PLAYERS_URL = "https://api.sleeper.app/v1/players/nfl"
NFLVERSE_PLAYER_STATS_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "player_stats/player_stats_{season}.csv.gz"
)
OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

MAX_NFL_ADJUSTMENT = 3.5
RECENT_WEEKS = 4

_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}

# Canonical values follow current NFL/nflverse/Sleeper abbreviations. Historical
# and common DK aliases are normalized before any name/team match.
_TEAM_ALIASES = {
    "ARZ": "ARI", "AZ": "ARI", "ARI": "ARI",
    "BLT": "BAL", "BAL": "BAL",
    "CLV": "CLE", "CLE": "CLE",
    "GB": "GB", "GNB": "GB",
    "HST": "HOU", "HOU": "HOU",
    "JAC": "JAX", "JAX": "JAX",
    "KC": "KC", "KAN": "KC", "KCR": "KC",
    "LA": "LAR", "LAR": "LAR", "STL": "LAR",
    "LAC": "LAC", "SD": "LAC", "SDG": "LAC",
    "LV": "LV", "LVR": "LV", "OAK": "LV",
    "NE": "NE", "NWE": "NE",
    "NO": "NO", "NOR": "NO",
    "SF": "SF", "SFO": "SF",
    "TB": "TB", "TAM": "TB",
    "TEN": "TEN", "OTI": "TEN",
    "WAS": "WAS", "WSH": "WAS", "WSN": "WAS",
}

# Approximate stadium coordinates and roof classification. Covered/retractable
# venues remain weather-neutral because a reliable roof-open feed is not used.
_STADIUMS: Dict[str, Tuple[float, float, str]] = {
    "ARI": (33.5276, -112.2626, "covered"),
    "ATL": (33.7554, -84.4008, "covered"),
    "BAL": (39.2780, -76.6227, "outdoor"),
    "BUF": (42.7738, -78.7870, "outdoor"),
    "CAR": (35.2258, -80.8528, "outdoor"),
    "CHI": (41.8623, -87.6167, "outdoor"),
    "CIN": (39.0955, -84.5161, "outdoor"),
    "CLE": (41.5061, -81.6995, "outdoor"),
    "DAL": (32.7473, -97.0945, "covered"),
    "DEN": (39.7439, -105.0201, "outdoor"),
    "DET": (42.3400, -83.0456, "covered"),
    "GB": (44.5013, -88.0622, "outdoor"),
    "HOU": (29.6847, -95.4107, "covered"),
    "IND": (39.7601, -86.1639, "covered"),
    "JAX": (30.3239, -81.6373, "outdoor"),
    "KC": (39.0489, -94.4839, "outdoor"),
    "LAC": (33.9535, -118.3392, "covered"),
    "LAR": (33.9535, -118.3392, "covered"),
    "LV": (36.0908, -115.1830, "covered"),
    "MIA": (25.9580, -80.2389, "outdoor"),
    "MIN": (44.9738, -93.2577, "covered"),
    "NE": (42.0909, -71.2643, "outdoor"),
    "NO": (29.9511, -90.0812, "covered"),
    "NYG": (40.8135, -74.0745, "outdoor"),
    "NYJ": (40.8135, -74.0745, "outdoor"),
    "PHI": (39.9008, -75.1675, "outdoor"),
    "PIT": (40.4468, -80.0158, "outdoor"),
    "SEA": (47.5952, -122.3316, "outdoor"),
    "SF": (37.4030, -121.9700, "outdoor"),
    "TB": (27.9759, -82.5033, "outdoor"),
    "TEN": (36.1665, -86.7713, "outdoor"),
    "WAS": (38.9078, -76.8645, "outdoor"),
}


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        result = float(str(value).replace("%", "").replace(",", "").strip())
        return result if math.isfinite(result) else default
    except Exception:
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def normalize_nfl_team(team: Any) -> str:
    raw = re.sub(r"[^A-Z0-9]", "", str(team or "").strip().upper())
    return _TEAM_ALIASES.get(raw, raw)


def normalize_nfl_name(name: Any) -> str:
    text = str(name or "").strip().lower()
    text = re.sub(r"[^a-z0-9\s'-]", "", text)
    parts = re.sub(r"\s+", " ", text).strip().split()
    if parts and parts[-1] in _SUFFIXES:
        parts = parts[:-1]
    return " ".join(parts)


def _position_group(value: Any) -> str:
    raw = str(value or "").strip().upper().split("/")[0]
    aliases = {
        "HB": "RB", "FB": "RB", "TB": "RB",
        "SE": "WR", "FL": "WR",
        "DEF": "DST", "D/ST": "DST", "D": "DST",
        "PK": "K",
    }
    return aliases.get(raw, raw)


def _fetch_json(url: str, *, params: Optional[Dict[str, Any]] = None, timeout_sec: int = 10) -> Any:
    if not HAS_REQUESTS:
        return None
    try:
        response = requests.get(url, params=params or {}, timeout=timeout_sec)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        logger.info("NFL context fetch failed for %s: %s", url, exc)
        return None


def fetch_sleeper_players(timeout_sec: int = 10) -> Optional[Dict[str, Any]]:
    data = _fetch_json(SLEEPER_PLAYERS_URL, timeout_sec=timeout_sec)
    return data if isinstance(data, dict) else None


def _fetch_nflverse_season_rows(season: int, timeout_sec: int = 15) -> List[Dict[str, Any]]:
    if not HAS_REQUESTS:
        return []
    url = NFLVERSE_PLAYER_STATS_URL.format(season=int(season))
    try:
        response = requests.get(url, timeout=timeout_sec)
        response.raise_for_status()
        payload = response.content
        if payload[:2] == b"\x1f\x8b":
            payload = gzip.decompress(payload)
        reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig", errors="replace")))
        rows = []
        for row in reader:
            season_type = str(row.get("season_type") or "REG").strip().upper()
            if season_type not in ("REG", "REGULAR"):
                continue
            if int(_to_float(row.get("season"), season)) != int(season):
                continue
            rows.append(dict(row))
        return rows
    except Exception as exc:
        logger.info("nflverse season %s unavailable: %s", season, exc)
        return []


def fetch_recent_usage_with_fallback(
    season: int,
    *,
    timeout_sec: int = 15,
) -> Tuple[List[Dict[str, Any]], Optional[int]]:
    """Load the requested nflverse season, then exactly one prior season."""
    for candidate in (int(season), int(season) - 1):
        rows = _fetch_nflverse_season_rows(candidate, timeout_sec=timeout_sec)
        if rows:
            return rows, candidate
    return [], None


def _recent_rows(rows: Sequence[Mapping[str, Any]], recent_weeks: int = RECENT_WEEKS) -> List[Dict[str, Any]]:
    usable = [dict(row) for row in rows if int(_to_float(row.get("week"), 0)) > 0]
    weeks = sorted({int(_to_float(row.get("week"), 0)) for row in usable})
    keep = set(weeks[-max(1, int(recent_weeks)):])
    return [row for row in usable if int(_to_float(row.get("week"), 0)) in keep]


def score_recent_usage(position: Any, *, attempts: float = 0.0, carries: float = 0.0, targets: float = 0.0) -> float:
    """Convert per-game opportunities into a conservative DK-point adjustment."""
    pos = _position_group(position)
    if pos == "QB":
        score = (attempts - 30.0) / 12.0 + (carries - 3.5) / 10.0
    elif pos == "RB":
        score = (carries + 1.35 * targets - 15.0) / 9.0
    elif pos == "WR":
        score = (targets - 6.0) / 4.5
    elif pos == "TE":
        score = (targets - 4.5) / 3.5
    else:
        score = 0.0
    return _clamp(score, -1.25, 1.25)


def _build_usage_index(rows: Sequence[Mapping[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    groups: Dict[Tuple[str, str], List[Mapping[str, Any]]] = defaultdict(list)
    for row in _recent_rows(rows):
        name = normalize_nfl_name(row.get("player_display_name") or row.get("player_name"))
        team = normalize_nfl_team(row.get("recent_team") or row.get("team"))
        if name:
            groups[(name, team)].append(row)

    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    name_counts: Dict[str, int] = defaultdict(int)
    for name, _team in groups:
        name_counts[name] += 1

    for (name, team), player_rows in groups.items():
        games = max(1, len({int(_to_float(row.get("week"), 0)) for row in player_rows}))
        position = _position_group(next((row.get("position") for row in reversed(player_rows) if row.get("position")), ""))
        attempts = sum(_to_float(row.get("attempts") or row.get("passing_attempts")) for row in player_rows) / games
        carries = sum(_to_float(row.get("carries")) for row in player_rows) / games
        targets = sum(_to_float(row.get("targets")) for row in player_rows) / games
        record = {
            "position": position,
            "attempts": attempts,
            "carries": carries,
            "targets": targets,
            "games": games,
            "score": score_recent_usage(position, attempts=attempts, carries=carries, targets=targets),
        }
        out[(name, team)] = record
        if name_counts[name] == 1:
            out[(name, "")] = record
    return out


def score_matchup(allowed_average: float, league_average: float) -> float:
    """Score opponent production allowed; positive means an easier matchup."""
    league = float(league_average or 0.0)
    if league <= 0:
        return 0.0
    relative = (float(allowed_average or 0.0) - league) / league
    return _clamp(relative * 3.0, -1.0, 1.0)


def _build_matchup_index(rows: Sequence[Mapping[str, Any]]) -> Dict[Tuple[str, str], float]:
    # First total fantasy points allowed by opponent/week/position, then compare
    # each opponent's recent average to the league average for that position.
    by_game: Dict[Tuple[str, int, str], float] = defaultdict(float)
    for row in _recent_rows(rows):
        opponent = normalize_nfl_team(row.get("opponent_team"))
        position = _position_group(row.get("position") or row.get("position_group"))
        week = int(_to_float(row.get("week"), 0))
        if not opponent or position not in {"QB", "RB", "WR", "TE"} or week <= 0:
            continue
        points = _to_float(row.get("fantasy_points_ppr"), _to_float(row.get("fantasy_points"), 0.0))
        by_game[(opponent, week, position)] += points

    opponent_values: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    league_values: Dict[str, List[float]] = defaultdict(list)
    for (opponent, _week, position), points in by_game.items():
        opponent_values[(opponent, position)].append(points)
        league_values[position].append(points)

    league_avg = {
        position: (sum(values) / len(values) if values else 0.0)
        for position, values in league_values.items()
    }
    return {
        key: score_matchup(sum(values) / len(values), league_avg.get(key[1], 0.0))
        for key, values in opponent_values.items()
        if values
    }


def _sleeper_index(data: Mapping[str, Any]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    name_counts: Dict[str, int] = defaultdict(int)
    for raw in data.values():
        if not isinstance(raw, dict):
            continue
        name = normalize_nfl_name(raw.get("full_name") or " ".join(filter(None, [raw.get("first_name"), raw.get("last_name")])))
        team = normalize_nfl_team(raw.get("team"))
        if not name:
            continue
        grouped[(name, team)] = raw
        name_counts[name] += 1
    for (name, _team), raw in list(grouped.items()):
        if name_counts[name] == 1:
            grouped[(name, "")] = raw
    return grouped


def _role_context(player: Mapping[str, Any], sleeper: Optional[Mapping[str, Any]]) -> Tuple[str, float]:
    if not sleeper:
        return "", 0.0
    position = _position_group(sleeper.get("depth_chart_position") or sleeper.get("position") or player.get("Position"))
    depth = int(_to_float(sleeper.get("depth_chart_order"), 0))
    practice = str(sleeper.get("practice_participation") or sleeper.get("practice_description") or "").strip()
    injury = str(sleeper.get("injury_status") or "").strip()

    score = 0.0
    if depth == 1:
        score += 0.35
    elif depth == 2:
        score += 0.05
    elif depth == 3:
        score -= 0.25
    elif depth > 3:
        score -= 0.45

    practice_u = practice.upper()
    if "DID NOT" in practice_u or practice_u in {"DNP", "NONE"}:
        score -= 0.45
        practice_short = "DNP"
    elif "LIMIT" in practice_u or practice_u == "LP":
        score -= 0.15
        practice_short = "LP"
    elif "FULL" in practice_u or practice_u == "FP":
        practice_short = "FP"
    else:
        practice_short = ""

    injury_u = injury.upper()
    if injury_u in {"OUT", "IR", "PUP", "NFI", "SUSP", "INACTIVE"}:
        score -= 1.0
    elif injury_u == "DOUBTFUL":
        score -= 0.55
    elif injury_u == "QUESTIONABLE":
        score -= 0.10

    label = f"{position}{depth}" if position and depth else position
    if practice_short:
        label = f"{label} {practice_short}".strip()
    return label, _clamp(score, -0.9, 0.9)


def _slate_season(players: Iterable[Mapping[str, Any]]) -> int:
    today = dt.date.today()
    years: List[int] = []
    months: List[int] = []
    for player in players or []:
        text = str(player.get("GameInfo") or "")
        match = re.search(r"(\d{1,2})/(\d{1,2})/(20\d{2})", text)
        if match:
            months.append(int(match.group(1)))
            years.append(int(match.group(3)))
    year = max(years) if years else today.year
    month = max(months) if months else today.month
    return year - 1 if month <= 3 else year


def _game_datetime(player: Mapping[str, Any]) -> Optional[dt.datetime]:
    text = str(player.get("GameInfo") or "").strip().upper()
    match = re.search(r"(\d{1,2})/(\d{1,2})/(20\d{2})\s+(\d{1,2}):(\d{2})(AM|PM)", text)
    if not match:
        return None
    month, day, year, hour, minute = map(int, match.groups()[:5])
    if match.group(6) == "PM" and hour != 12:
        hour += 12
    if match.group(6) == "AM" and hour == 12:
        hour = 0
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("America/New_York"))
    except Exception:
        try:
            return dt.datetime(year, month, day, hour, minute, tzinfo=dt.timezone(dt.timedelta(hours=-5)))
        except Exception:
            return None


def score_weather(
    *,
    temperature_f: float = 65.0,
    wind_mph: float = 0.0,
    precipitation_probability: float = 0.0,
    weather_code: int = 0,
    position: Any = "WR",
    indoor: bool = False,
) -> float:
    """Return a modest, position-aware weather adjustment in DK points."""
    if indoor:
        return 0.0
    temp = float(temperature_f)
    wind = max(0.0, float(wind_mph))
    precip = _clamp(float(precipitation_probability), 0.0, 100.0)
    penalty = 0.0
    if wind >= 25:
        penalty -= 0.90
    elif wind >= 20:
        penalty -= 0.65
    elif wind >= 15:
        penalty -= 0.35
    if precip >= 75:
        penalty -= 0.55
    elif precip >= 50:
        penalty -= 0.30
    if temp <= 20:
        penalty -= 0.50
    elif temp <= 32:
        penalty -= 0.25
    elif temp >= 95:
        penalty -= 0.20
    if int(weather_code or 0) in set(range(71, 78)) | set(range(95, 100)):
        penalty -= 0.20

    position_u = _position_group(position)
    if position_u == "DST":
        return _clamp(-0.45 * penalty, -0.9, 0.9)
    if position_u == "RB":
        penalty *= 0.35
    elif position_u == "QB":
        penalty *= 0.90
    elif position_u == "K":
        penalty *= 0.80
    return _clamp(penalty, -0.9, 0.9)


def _hourly_weather_at(data: Mapping[str, Any], when: dt.datetime) -> Optional[Dict[str, Any]]:
    hourly = data.get("hourly") if isinstance(data, dict) else None
    if not isinstance(hourly, dict):
        return None
    times = hourly.get("time") or []
    if not isinstance(times, list) or not times:
        return None
    when_utc = when.astimezone(dt.timezone.utc).replace(tzinfo=None)
    candidates: List[Tuple[float, int]] = []
    for index, raw in enumerate(times):
        try:
            parsed = dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(dt.timezone.utc).replace(tzinfo=None)
            candidates.append((abs((parsed - when_utc).total_seconds()), index))
        except Exception:
            continue
    if not candidates:
        return None
    distance, index = min(candidates)
    if distance > 3 * 3600:
        return None

    def value(key: str, default: float = 0.0) -> float:
        values = hourly.get(key) or []
        return _to_float(values[index] if index < len(values) else None, default)

    return {
        "temperature_f": value("temperature_2m", 65.0),
        "wind_mph": value("wind_speed_10m", 0.0),
        "precipitation_probability": value("precipitation_probability", 0.0),
        "weather_code": int(value("weather_code", 0.0)),
        "indoor": False,
    }


def _fetch_weather_for_games(players: Sequence[Mapping[str, Any]], timeout_sec: int = 8) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    games: Dict[str, Mapping[str, Any]] = {}
    for player in players or []:
        game_key = str(player.get("GameKey") or "").strip().upper()
        if game_key:
            games.setdefault(game_key, player)

    for game_key, player in games.items():
        home = normalize_nfl_team(player.get("HomeTeam") or game_key.split("@")[-1])
        stadium = _STADIUMS.get(home)
        when = _game_datetime(player)
        if not stadium:
            continue
        latitude, longitude, roof = stadium
        if roof != "outdoor":
            result[game_key] = {"indoor": True}
            continue
        if when is None:
            continue
        data = _fetch_json(
            OPEN_METEO_FORECAST_URL,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "hourly": "temperature_2m,precipitation_probability,wind_speed_10m,weather_code",
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
                "timezone": "UTC",
                "forecast_days": 16,
            },
            timeout_sec=timeout_sec,
        )
        condition = _hourly_weather_at(data, when) if isinstance(data, dict) else None
        if condition:
            result[game_key] = condition
    return result


def apply_context_adjustment(
    player: Dict[str, Any],
    *,
    usage: float = 0.0,
    matchup: float = 0.0,
    role: float = 0.0,
    weather: float = 0.0,
    vegas: float = 0.0,
) -> float:
    """Apply component scores to a player and return the capped total."""
    raw = float(usage) + float(matchup) + float(role) + float(weather) + float(vegas)
    adjustment = _clamp(raw, -MAX_NFL_ADJUSTMENT, MAX_NFL_ADJUSTMENT)
    base = _to_float(player.get("BaseProjection"), _to_float(player.get("FlexProjection"), 0.0))
    player["BaseProjection"] = base
    player["NFLAdjRaw"] = raw
    player["NFLAdjScore"] = adjustment
    player["FlexProjection"] = max(0.0, base + adjustment)
    player["CptProjection"] = 1.5 * player["FlexProjection"]
    return adjustment


def _usage_display(record: Mapping[str, Any]) -> float:
    position = _position_group(record.get("position"))
    if position == "QB":
        return _to_float(record.get("attempts")) + _to_float(record.get("carries"))
    if position in {"RB", "WR", "TE"}:
        return _to_float(record.get("carries")) + _to_float(record.get("targets"))
    return 0.0


def apply_auto_nfl_context(
    players: List[Dict[str, Any]],
    *,
    sleeper_data: Optional[Mapping[str, Any]] = None,
    usage_rows: Optional[Sequence[Mapping[str, Any]]] = None,
    usage_season: Optional[int] = None,
    weather_by_game: Optional[Mapping[str, Mapping[str, Any]]] = None,
    fetch_external: bool = True,
    season: Optional[int] = None,
) -> Dict[str, Any]:
    """Enrich NFL players in-place and conservatively adjust projections.

    Tests and offline callers can inject source payloads and set fetch_external=False.
    Missing payloads are treated as neutral rather than as errors.
    """
    if not players:
        return {"total": 0, "sleeper": 0, "usage": 0, "weather_games": 0, "usage_season": None}

    target_season = int(season or _slate_season(players))
    if fetch_external and sleeper_data is None:
        sleeper_data = fetch_sleeper_players()
    if fetch_external and usage_rows is None:
        usage_rows, usage_season = fetch_recent_usage_with_fallback(target_season)
    if fetch_external and weather_by_game is None:
        weather_by_game = _fetch_weather_for_games(players)

    sleeper_index = _sleeper_index(sleeper_data or {})
    usage_index = _build_usage_index(usage_rows or [])
    matchup_index = _build_matchup_index(usage_rows or [])
    weather_index = {str(key).strip().upper(): dict(value) for key, value in (weather_by_game or {}).items()}

    sleeper_matches = 0
    usage_matches = 0
    for player in players:
        name = normalize_nfl_name(player.get("Name"))
        team = normalize_nfl_team(player.get("Team"))
        opponent = normalize_nfl_team(player.get("Opponent"))
        position = _position_group(player.get("Position"))
        game_key = str(player.get("GameKey") or "").strip().upper()

        sleeper = sleeper_index.get((name, team)) or sleeper_index.get((name, ""))
        if sleeper:
            sleeper_matches += 1
            player["NFLDepthPosition"] = str(sleeper.get("depth_chart_position") or sleeper.get("position") or "")
            player["NFLDepthOrder"] = int(_to_float(sleeper.get("depth_chart_order"), 0))
            player["NFLPractice"] = str(sleeper.get("practice_participation") or sleeper.get("practice_description") or "")
            player["InjuryStatus"] = str(sleeper.get("injury_status") or "")
            player["InjuryBodyPart"] = str(sleeper.get("injury_body_part") or "")
            player["InjuryStartDate"] = str(sleeper.get("injury_start_date") or "")
            player["InjurySource"] = "Sleeper"
            status = str(player["InjuryStatus"]).strip().upper()
            is_out = status in {"OUT", "IR", "PUP", "NFI", "SUSP", "SUSPENDED", "INACTIVE"}
            if is_out:
                player["LockFlex"] = False
                player["LockCpt"] = False
                player["FadeFlex"] = True
                player["FadeCpt"] = True
                player["AutoFadeInjury"] = True
            elif player.get("AutoFadeInjury") is True:
                player["FadeFlex"] = False
                player["FadeCpt"] = False
                player["AutoFadeInjury"] = False
        else:
            player["NFLDepthPosition"] = ""
            player["NFLDepthOrder"] = 0
            player["NFLPractice"] = ""

        role_label, role_score = _role_context(player, sleeper)
        usage = usage_index.get((name, team)) or usage_index.get((name, "")) or {}
        if usage:
            usage_matches += 1
        usage_score = _to_float(usage.get("score"), 0.0)
        matchup_score = matchup_index.get((opponent, position), 0.0)

        condition = weather_index.get(game_key, {})
        indoor = bool(condition.get("indoor"))
        weather_score = score_weather(
            temperature_f=_to_float(condition.get("temperature_f"), 65.0),
            wind_mph=_to_float(condition.get("wind_mph"), 0.0),
            precipitation_probability=_to_float(condition.get("precipitation_probability"), 0.0),
            weather_code=int(_to_float(condition.get("weather_code"), 0.0)),
            position=position,
            indoor=indoor,
        ) if condition else 0.0

        # Vegas intentionally stays neutral until a reliable keyless source exists.
        vegas_score = 0.0
        player["NFLUsage"] = _usage_display(usage)
        player["NFLUsageGames"] = int(_to_float(usage.get("games"), 0))
        player["NFLUsageSeason"] = usage_season
        player["NFLUsageScore"] = usage_score
        player["NFLMatchupScore"] = matchup_score
        player["NFLRole"] = role_label
        player["NFLRoleScore"] = role_score
        player["NFLWeatherScore"] = weather_score
        player["NFLWeatherIndoor"] = indoor
        player["NFLWeatherTempF"] = _to_float(condition.get("temperature_f"), 0.0) if condition else 0.0
        player["NFLWeatherWindMph"] = _to_float(condition.get("wind_mph"), 0.0) if condition else 0.0
        player["NFLWeatherPrecipPct"] = _to_float(condition.get("precipitation_probability"), 0.0) if condition else 0.0
        player["NFLVegas"] = vegas_score
        apply_context_adjustment(
            player,
            usage=usage_score,
            matchup=matchup_score,
            role=role_score,
            weather=weather_score,
            vegas=vegas_score,
        )

    summary = {
        "total": len(players),
        "sleeper": sleeper_matches,
        "usage": usage_matches,
        "weather_games": len(weather_index),
        "usage_season": usage_season,
        "max_adjustment": MAX_NFL_ADJUSTMENT,
    }
    logger.info("NFL auto context applied: %s", summary)
    return summary


def clear_nfl_context(players: List[Dict[str, Any]]) -> None:
    for player in players or []:
        base = _to_float(player.get("BaseProjection"), _to_float(player.get("FlexProjection"), 0.0))
        player["FlexProjection"] = base
        player["CptProjection"] = 1.5 * base
        for key in (
            "NFLAdjRaw", "NFLAdjScore", "NFLUsage", "NFLUsageGames", "NFLUsageSeason",
            "NFLUsageScore", "NFLMatchupScore", "NFLRole", "NFLRoleScore",
            "NFLDepthPosition", "NFLDepthOrder", "NFLPractice", "NFLWeatherScore",
            "NFLWeatherIndoor", "NFLWeatherTempF", "NFLWeatherWindMph",
            "NFLWeatherPrecipPct", "NFLVegas",
        ):
            player.pop(key, None)
