"""Versioned opportunity priors, not fitted forecasts or a play-by-play model."""
import math
from collections import defaultdict

VERSION = 'workload-v2'
TEAM_BUDGET = {'attempts': 34.0, 'carries': 26.0, 'targets': 32.0}
POSITION_SHARE = {
    'attempts': {'QB': 1.0},
    'carries': {'QB': .12, 'RB': .85, 'WR': .03},
    'targets': {'RB': .20, 'WR': .58, 'TE': .22},
}
DEPTH_SHARE = {'QB': (1.0, .0, .0), 'RB': (.62, .28, .10),
               'WR': (.40, .30, .20, .10), 'TE': (.70, .25, .05)}
# Position efficiency assumptions; retain them with the prediction for later evaluation.
EFFICIENCY = {'QB': (4.5, .0, .0), 'RB': (4.2, .75, 7.5),
              'WR': (4.5, .63, 12.0), 'TE': (4.0, .70, 10.0)}
RATES = dict(passing_yards_per_attempt=7.0, passing_td_per_attempt=.045,
             interceptions_per_attempt=.025, rushing_td_per_carry=.028,
             receiving_td_per_catch=.055)


def n(value):
    try:
        value = float(value)
        return value if math.isfinite(value) and value >= 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def available(p):
    statuses = ' '.join(str(p.get(k) or '').upper() for k in ('NFLAvailability', 'InjuryStatus', 'Status', 'NFLRosterStatus'))
    return p.get('NFLActive') is not False and not any(
        token in statuses.split() for token in ('OUT', 'IR', 'PUP', 'NFI', 'INACTIVE', 'SUSP', 'SUSPENDED'))


def points(workload):
    a, c, t = (n(workload.get(k)) for k in ('attempts', 'carries', 'targets'))
    ypc, catch, ypr = workload['efficiency']
    r = workload.get('rates', RATES)
    # DK base scoring expectation. Yardage bonuses/fumbles are not modeled here.
    return a * (r['passing_yards_per_attempt'] * .04 + r['passing_td_per_attempt'] * 4 - r['interceptions_per_attempt']) + c * (ypc * .1 + r['rushing_td_per_carry'] * 6) + t * catch * (1 + ypr * .1 + r['receiving_td_per_catch'] * 6)


def prepare_workloads(players):
    groups = defaultdict(list)
    for p in players:
        if not p.get('ProjectionInputsVersion'):
            continue
        p.pop('NFLWorkload', None)
        p.pop('WorkloadProjection', None)
        if p.get('Team') and p.get('Position') in DEPTH_SHARE:
            groups[(str(p['Team']).upper(), p['Position'])].append(p)
    for (team, pos), roster in groups.items():
        active = [p for p in roster if available(p) and n(p.get('NFLDepthOrder')) >= 1]
        if not active:
            continue
        # Shift known backups only when the earlier slots are explicitly unavailable.
        first = min(int(n(p.get('NFLDepthOrder'))) for p in active)
        unavailable_depths = {int(n(p.get('NFLDepthOrder'))) for p in roster if not available(p)}
        promote = first - 1 if all(d in unavailable_depths for d in range(1, first)) else 0
        for p in active:
            p['NFLWorkload'] = dict(version=VERSION, team=team, position=pos,
                attempts=0.0, carries=0.0, targets=0.0, budgets={},
                efficiency=list(EFFICIENCY[pos]), team_budgets=dict(TEAM_BUDGET),
                rates=dict(RATES), uncertainty='Shared position budgets; individual opportunity weights vary 0.65–1.35 in SIMs.',
                usage_games=int(n(p.get('NFLUsageGames'))), usage_season=p.get('NFLUsageSeason'),
                depth=int(n(p.get('NFLDepthOrder'))), promoted_slots=promote,
                assumptions='Heuristic team volume, role shares and position efficiency; excludes bonuses/fumbles. Not calibrated.')
        for metric, team_total in TEAM_BUDGET.items():
            budget = team_total * POSITION_SHARE[metric].get(pos, 0)
            weights = []
            for p in active:
                depth = int(n(p.get('NFLDepthOrder'))) - promote
                shares = DEPTH_SHARE[pos]
                prior = shares[depth - 1] if 0 < depth <= len(shares) else 0.0
                recent = n(p.get('NFLRecent' + metric.title()))
                blend = min(.5, n(p.get('NFLUsageGames')) / 8)
                # Prior-season usage is less informative about today's depth role.
                season = p.get('NFLUsageSeason')
                game = str(p.get('GameInfo') or '')
                if season and str(season) not in game:
                    blend *= .5
                weight = (1 - blend) * prior + blend * min(1, recent / budget) if budget and prior else 0.0
                weights.append(weight)
            denominator = max(1.0, sum(weights))
            for p, weight in zip(active, weights):
                p['NFLWorkload'][metric] = .95 * budget * weight / denominator
                p['NFLWorkload']['budgets'][metric] = budget
        for p in active:
            if sum(p['NFLWorkload'][m] for m in TEAM_BUDGET) > 0:
                w = p['NFLWorkload']
                history = n(p.get('HistoricalPPG'))
                # History is an imperfect anchor, not a forward forecast. Without
                # player usage, generic role priors must not erase that evidence.
                weight = .25 + min(.25, n(p.get('NFLUsageGames')) / 16) if history > 0 else 1.0
                w['history_anchor'] = history
                w['workload_weight'] = weight
                w['raw_workload_points'] = points(w)
                w['assumptions'] += f' Historical PPG anchor weight {1-weight:.0%}; workload weight {weight:.0%}. Heuristic blend, not fitted.'
                p['WorkloadProjection'] = round(forecast(w), 4)


def forecast(workload):
    weight = workload.get('workload_weight', 1.0)
    return points(workload) * weight + n(workload.get('history_anchor')) * (1 - weight)


def sample_workloads(rng, players):
    """Perturb teammate shares together, retaining unused/absent-player reserves."""
    if not any(p.get('ProjectionSource') == 'Automatic workload estimate' for p in players):
        return {}
    groups = defaultdict(list)
    sampled = {}
    for p in players:
        w = p.get('NFLWorkload')
        if w and w.get('version') in {'workload-v1', VERSION}:
            groups[(w['team'], w['position'])].append(p)
            sampled[id(p)] = dict(w)
    for roster in groups.values():
        for metric in TEAM_BUDGET:
            budget = max(n(p['NFLWorkload']['budgets'].get(metric)) for p in roster)
            if not budget:
                continue
            base = [n(p['NFLWorkload'][metric]) for p in roster]
            reserve = max(0, budget - sum(base))
            draws = [v * rng.uniform(.65, 1.35) for v in base]
            total = reserve + sum(draws)
            for p, draw in zip(roster, draws):
                sampled[id(p)][metric] = budget * draw / total if total else 0
    return sampled
