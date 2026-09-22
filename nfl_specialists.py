"""Shared possession events for K/DST; heuristic rates, not fitted forecasts.

Offensive fantasy points remain a separate model, coupled through team form.
This is not a play-by-play simulation. Unmodeled events: special-teams returns,
safeties, blocked kicks and two-point tries. They are not invented independently.
"""
import random
import hashlib
import math

MODEL = 'shared-specialist-events-v1'


def points_allowed_score(points):
    for ceiling, score in ((0, 10), (6, 7), (13, 4), (20, 1), (27, 0), (34, -1)):
        if points <= ceiling:
            return score
    return -4


def kicker_score(events):
    return events['extra_points'] + sum(3 if y < 40 else 4 if y < 50 else 5 for y in events['field_goals'])


def defense_score(events, opponent):
    # A pick-six is not charged to the offense's DST; the subsequent PAT is.
    allowed = opponent['offensive_touchdowns'] * 6 + opponent['extra_points'] + 3 * len(opponent['field_goals'])
    return (events['sacks'] + 2 * events['takeaways'] + 6 * events['defensive_touchdowns']
            + points_allowed_score(allowed))


def _clip(x, low, high):
    return max(low, min(high, x))


def sample_game(rng, teams, environments, game_z, targets, defense_targets, kicking=None):
    """One shared possession budget, mutually exclusive drive endings per team.

    Targets influence event rates, not a guaranteed marginal fantasy mean.
    All constants are explicit starting assumptions awaiting outcome calibration.
    """
    kicking = kicking or {}
    drives = int(_clip(round(11 + .45 * game_z + rng.gauss(0, 1.1)), 8, 16))
    events = {t:dict(drives=drives, offensive_touchdowns=0, extra_points=0,
                     field_goals=[], field_goal_attempts=0, sacks=0, takeaways=0, defensive_touchdowns=0) for t in teams}
    for team in teams:
        other = next(t for t in teams if t != team)
        e, defense = events[team], events[other]
        form = _clip(environments.get(team, 0), -2.5, 2.5)
        strength = _clip((defense_targets.get(other, 7) - 7) / 12, -.4, .6)
        turnover_p = _clip(.12 + .06 * strength - .02 * form, .04, .23)
        td_p = _clip(.23 + .055 * form + .015 * game_z - .025 * strength, .07, .48)
        # Higher kicker targets shift empty possessions into FG opportunities.
        fg_p = _clip((targets.get(team, 8) - 2.5) / (11 * 3.65) + .012 * form, .025, .32)
        model = kicking.get(team)
        if model:
            # Attempts and accuracy are distinct. Expected PAT opportunities
            # inform team TD frequency, including a small return-TD allowance.
            td_p = _clip(model['xpa']/11 - .012 + .035*form + .01*game_z, 0, .50)
            fg_p = _clip(model['fga']/11 + .012*form, 0, 1-turnover_p-td_p)
        for _ in range(drives):
            # A sack need not end a drive. At most three recorded per possession.
            defense['sacks'] += sum(rng.random() < _clip(.075 + .04 * strength - .012 * form, .02, .16) for _ in range(3))
            roll = rng.random()
            if roll < turnover_p:
                defense['takeaways'] += 1
                if rng.random() < .10:
                    defense['defensive_touchdowns'] += 1
            elif roll < turnover_p + td_p:
                e['offensive_touchdowns'] += 1
            elif roll < turnover_p + td_p + fg_p:
                e['field_goal_attempts'] += 1
                if not model or rng.random() < model['fg_rate']:
                    mix = model['made_distance_mix'] if model else (.50,.30,.20)
                    e['field_goals'].append(rng.choices((32, 45, 53), mix)[0])
    for team in teams:
        e = events[team]
        rate = kicking.get(team,{}).get('xp_rate',.95)
        e['extra_points'] = sum(rng.random() < rate for _ in range(e['offensive_touchdowns'] + e['defensive_touchdowns']))
    return events


def specialist_outcomes(rng, players, outcomes, game_factor, team_environment):
    from nfl_simulation import _game, _team, _opponent, _position, _projection, player_key
    # Separate stream preserves every offensive draw and permits paired audits.
    seed = hashlib.sha256(repr(rng.getstate()).encode()).digest()
    event_rng = random.Random(seed)
    games = {}
    for p in players:
        if _game(p):
            games.setdefault(_game(p), []).append(p)
    result = {}
    for game, pool in sorted(games.items()):
        teams = sorted({_team(p) for p in pool} | {_opponent(p) for p in pool})
        teams = [t for t in teams if t]
        if len(teams) != 2:
            continue  # Incomplete fixture retains the legacy draw.
        form = dict(team_environment)
        targets, defenses, kicking = {}, {}, {}
        for team in teams:
            offense = [p for p in pool if _team(p)==team and _position(p) in {'QB','RB','WR','TE'} and _projection(p)>0]
            expected = sum(_projection(p) for p in offense)
            actual = sum(outcomes[player_key(p)] for p in offense)
            if expected:
                form[team] = .5 * form.get(team, 0) + .5 * _clip(2 * math.log(max(.1, actual/expected)), -2.5, 2.5)
            for pos, target in (('K', targets), ('DST', defenses)):
                values = [_projection(p) for p in pool if _team(p)==team and _position(p)==pos]
                if values:
                    target[team] = max(values)
            kickers = [p for p in pool if _team(p)==team and _position(p)=='K']
            if kickers:
                starter=max(kickers,key=lambda p:(_projection(p),player_key(p)))
                if starter.get('ProjectionSource')=='Automatic kicker opportunities' and starter.get('NFLKickerOpportunities'):
                    kicking[team]=dict(starter['NFLKickerOpportunities'])
                    baseline=float(starter.get('KickerProjection') or 0)
                    factor=_projection(starter)/baseline if baseline>0 else 1.
                    for metric in ('fga','xpa'):
                        kicking[team][metric]*=factor
        events = sample_game(event_rng, teams, form, game_factor.get(game, 0), targets, defenses, kicking)
        for p in pool:
            team = _team(p)
            if team not in events:
                continue
            opponent = next(t for t in teams if t != team)
            if _position(p)=='DST':
                result[player_key(p)] = float(defense_score(events[team], events[opponent]))
            elif _position(p)=='K':
                # Multiple listed kickers share one team scoring budget, never
                # each receive a complete game's opportunities.
                kickers = [q for q in pool if _team(q)==team and _position(q)=='K']
                starter = max(kickers, key=lambda q: (_projection(q), player_key(q)))
                result[player_key(p)] = float(kicker_score(events[team]) if player_key(p)==player_key(starter) else 0)
    return result
