"""Full available season evidence, separate from the four-week form window."""
import math
from collections import defaultdict


def season_index(rows, season):
    from nfl_auto_data import normalize_nfl_name, normalize_nfl_team
    groups = defaultdict(lambda: defaultdict(set))
    for row in rows or []:
        try:
            year, week = int(row.get('season', season)), int(row.get('week', 0))
        except (TypeError, ValueError):
            continue
        if year != season or not 1 <= week <= 18 or str(row.get('season_type', 'REG')).upper() not in ('REG', 'REGULAR'):
            continue
        name = normalize_nfl_name(row.get('player_display_name') or row.get('player_name'))
        team = normalize_nfl_team(row.get('recent_team') or row.get('team'))
        if not name:
            continue
        # Stable IDs combine traded players; absent IDs do not merge teams.
        identity = str(row.get('player_id') or '').strip() or ('team:' + team)
        groups[name][identity].add((team, week))
    return dict(groups)


def match_season(index, player, season):
    from nfl_auto_data import normalize_nfl_name, normalize_nfl_team
    groups = index.get(normalize_nfl_name(player.get('Name')), {})
    team = normalize_nfl_team(player.get('Team'))
    matches = [games for games in groups.values() if any(t == team for t, _ in games)]
    if not matches and len(groups) == 1:
        matches = list(groups.values())
    state = 'matched' if len(matches) == 1 else 'ambiguous' if groups else 'no_player_match' if index else 'source_unavailable'
    return dict(season=season, state=state, games=len({w for _, w in matches[0]}) if state == 'matched' else None)


def attach_history(player, current, prior, season, checked_at):
    from nfl_auto_data import NFLVERSE_PLAYER_STATS_URL
    player['NFLUsageHistory'] = dict(version=1, target_season=season, checked_at=checked_at,
        current=match_season(current, player, season), prior=match_season(prior, player, season-1))
    for period in ('current', 'prior'):
        record = player['NFLUsageHistory'][period]
        record['source_url'] = NFLVERSE_PLAYER_STATS_URL.format(season=record['season'])


def history_evidence(player):
    history = player.get('NFLUsageHistory') or {}
    counts = {}
    for period in ('current', 'prior'):
        record = history.get(period) or {}
        value = record.get('games')
        counts[period] = value if record.get('state') == 'matched' and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 < value <= 18 else None
    current, prior = counts['current'], counts['prior']
    total = (current or 0) + (prior or 0)
    if total >= 4:
        label = 'Short current-season sample; prior-season evidence' if (current or 0) < 4 and prior else 'At least four observed games across available seasons'
        stress = False
    elif history.get('version') == 1:
        label = 'Limited matched season evidence' if total else 'No matched full-season history'
        if any((history.get(p) or {}).get('state') in ('source_unavailable', 'ambiguous') for p in ('current', 'prior')):
            label += '; coverage incomplete'
        stress = True
    else:
        # A legacy recent-window count can establish a lower bound, never total history.
        try:
            recent = float(player.get('NFLUsageGames'))
        except (TypeError, ValueError):
            recent = 0
        stress = not (math.isfinite(recent) and recent >= 4)
        label = 'Full-season history missing from saved input; recent-window count only' if stress else 'At least four recent observed games; full-season history not recorded'
    if player.get('NFLRookie') is True or player.get('Rookie') is True:
        label, stress = 'Explicit rookie flag', True
    return dict(reason=label, stress=stress, current_games=current, prior_games=prior)
