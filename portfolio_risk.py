"""RL-06: detached, deterministic roster counts and point arithmetic only.

No optimizer, simulation, live enrichment, outcome scores or storage writers.
The evidence adapter supplies only the scoped roster/forecast/role contract below.
"""
from collections import Counter
from dataclasses import dataclass
from itertools import product
import json
import math

from review_build_evidence import timestamp
from review_report import Cancelled

VERSION = 1
RETENTIONS = (1.0, .75, .5, .25, 0.0)
ROLE_AGE_HOURS = 24
EXPORT_UNAVAILABLE = 'Unavailable: no exact qualified pregame input association'


@dataclass(frozen=True)
class RiskCapture:
    evidence: str

    @classmethod
    def freeze(cls, value):
        return cls(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False))

    @property
    def data(self):
        return json.loads(self.evidence)


@dataclass(frozen=True)
class RiskReport(RiskCapture):
    pass


def check(cancelled):
    if cancelled():
        raise Cancelled()


def finite(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def total(values):
    if any(v is None for v in values):
        return None
    try:
        return finite(math.fsum(values))
    except (ValueError, OverflowError):
        return None


def role_evidence(raw, captured_at, kickoff, qualified=True):
    """Report-only policy v1. Age is relative to historical kickoff, never now."""
    from nfl_eligibility import depth, unavailable
    from results_snapshot_learning import forecast_group
    keys = ('Position','NFLDepthOrder','NFLDepthPosition','NFLAvailability','NFLActive',
            'NFLRosterStatus','InjuryStatus','Status','NFLQBEligible','LiveStatusConflict',
            'LiveStatusUpdatedAt','InjurySource','FadeFlex','FadeCpt','LockFlex','LockCpt')
    values = {k:raw.get(k) for k in keys}
    for k,v in values.items():
        if isinstance(v,float) and not math.isfinite(v):
            values[k] = None
    source = str(values.get('InjurySource') or '')
    recorded, capture, start = map(timestamp, (values.get('LiveStatusUpdatedAt'), captured_at, kickoff))
    order = depth(values)
    availability = str(values.get('NFLAvailability') or '').upper()
    conflicts = bool(values.get('LiveStatusConflict')) or (
        unavailable(values) and availability in ('STARTER','ACTIVE','BACKUP 2','BACKUP 3','BACKUP 4'))
    conflicts |= (availability=='STARTER' and order>1) or (availability.startswith('BACKUP') and order==1)
    age = (start-recorded).total_seconds()/3600 if start and recorded else None
    state, reason = 'unknown', 'Missing applicable role source, depth, availability or aware role timestamp'
    if not qualified:
        reason = EXPORT_UNAVAILABLE
    elif conflicts:
        state, reason = 'conflict', 'Recorded role/availability fields conflict'
    elif source not in ('Sleeper','DraftKings file + Sleeper role') or not order or not availability:
        pass
    elif not all((recorded, capture, start)):
        pass
    elif not recorded <= capture < start:
        state, reason = 'unknown', 'Role timestamp must be at/before capture, before earliest kickoff'
    elif age > ROLE_AGE_HOURS:
        state, reason = 'stale', 'Role check was more than 24 hours before earliest kickoff'
    else:
        state, reason = 'timely', 'Applicable archived role check within 24 hours of historical kickoff'
    return dict(raw=values, group=forecast_group(values), source=source or 'source not recorded',
                state=state, reason=reason, age_hours=age, threshold_hours=ROLE_AGE_HOURS,
                policy_version=1, depth=order or None, unavailable=unavailable(values))


def recorded_player(key, raw, *, qualified, captured_at=None, kickoff=None, game=None):
    """Explicit allowlist: actuals, ranks, payouts and ownership cannot enter math."""
    base = finite(raw.get('FlexProjection')) if qualified else None
    source = str(raw.get('ProjectionSource') or 'source not recorded')
    if source.strip().casefold() == 'missing forecast':
        base = None
    captain = finite(raw.get('CptProjection'))
    has_captain = raw.get('CptProjection') not in (None, '')
    captain_ok = base is not None and (not has_captain or
        captain is not None and abs(captain-1.5*base)<=1e-6)
    return dict(key=str(key), name=str(raw.get('Name') or 'Name not recorded'),
        position=str(raw.get('Position') or '').upper().replace('D/ST','DST'),
        team=str(raw.get('Team') or '').upper(), game=game,
        base_projection=base, captain_ok=captain_ok,
        captain_basis=('recorded 1.5x base' if has_captain and captain_ok else
                       'derived 1.5x base; stored Captain field absent' if captain_ok else
                       'unavailable: missing base or conflicting Captain projection'),
        forecast_source=source, forecast_recorded_at=captured_at,
        role=role_evidence(raw, captured_at, kickoff, qualified),
        legacy_metadata=None if qualified else {k:raw.get(k) for k in
            ('RecordedProjection','RecordedStatus')})


def metric(count, denominator):
    return dict(count=count, denominator=denominator,
                pct=100*count/denominator if denominator and count is not None else None)


def exposure(rows, pool, key, kind):
    if key not in pool:
        return dict(key=key, applicable=False, any=metric(None,0), captain=metric(None,0),
                    noncaptain=metric(None,0), reason='Player outside the captured identity pool')
    captain = sum(any(s['key']==key and s['role']=='CPT' for s in row) for row in rows)
    regular = sum(any(s['key']==key and s['role']!='CPT' for s in row) for row in rows)
    return dict(key=key, applicable=True, any=metric(captain+regular,len(rows)),
                captain=metric(captain,len(rows)) if kind=='showdown' else metric(None,0),
                noncaptain=metric(regular,len(rows)))


def pair_coverage(rows, pool, a, b):
    if a not in pool or b not in pool or a==b:
        return dict(applicable=False, denominator=0, buckets=None, either=None, exactly_one=None)
    counts = dict(both=0, a_only=0, b_only=0, neither=0)
    for row in rows:
        keys = {s['key'] for s in row}
        name = 'both' if a in keys and b in keys else 'a_only' if a in keys else 'b_only' if b in keys else 'neither'
        counts[name] += 1
    return dict(applicable=True, denominator=len(rows), buckets=counts,
                either=counts['both']+counts['a_only']+counts['b_only'],
                exactly_one=counts['a_only']+counts['b_only'])


def distribution(values):
    """Empirical quantiles: sorted index floor((n-1)*q), not interpolated."""
    values = sorted(values)
    n = len(values)
    # Divide first: a finite entry mean need not have a representable sum.
    return dict(denominator=n, mean=total([v/n for v in values]) if n else None,
                min=values[0] if n else None, median=values[(n-1)//2] if n else None,
                p90=values[math.floor((n-1)*.9)] if n else None, max=values[-1] if n else None)


def relation(pool, risk, alternative):
    a,b = pool.get(risk),pool.get(alternative)
    supported = bool(a and b and a['team'] and a['team']==b['team'] and a['position'] and
        a['position']==b['position'] and a['role']['state']==b['role']['state']=='timely' and
        a['role']['depth']==1 and (b['role']['depth'] or 0)>1 and not b['role']['unavailable'])
    return ('Recorded same-team/position depth alternative; not exclusive replacement or transferred production'
            if supported else 'User-selected alternative; timely recorded depth relationship not established')


def calculate(capture, targets=(), alternatives=(), *, cancelled=lambda:False):
    """Accept only the detached adapter contract, retaining occurrence weights."""
    check(cancelled)
    data = capture.data
    pool, rows, kind = data['pool'], data['rosters'], data['format']
    targets, alternatives = tuple(targets), tuple(dict.fromkeys(alternatives))
    if len(targets)>2 or len(set(targets))!=len(targets):
        raise ValueError('Choose one or two distinct risk athletes')
    n = len(rows)
    from entry_review import roster_signature
    signatures = [roster_signature([s['key'] for s in row],kind) for row in rows]
    exposures = []
    for key in pool:
        check(cancelled)
        exposures.append(exposure(rows,pool,key,kind))
    target_exposures = [exposure(rows,pool,key,kind) for key in targets]
    pair = pair_coverage(rows,pool,*targets) if len(targets)==2 else None
    team_rows = [row for row in rows if all(pool[s['key']]['team'] for s in row)]
    context_rows = [row for row in team_rows if all(pool[s['key']]['position'] for s in row)]
    teams, qb_pairs, games = {}, Counter(), {}
    for row in team_rows:
        check(cancelled)
        counts = Counter(pool[s['key']]['team'] for s in row)
        for team,count in counts.items():
            teams.setdefault(team,Counter())[count] += 1
    for row in context_rows:
        ps = [pool[s['key']] for s in row]
        for qb in (p for p in ps if p['position']=='QB'):
            for receiver in (p for p in ps if p['position'] in ('WR','TE') and p['team']==qb['team']):
                key = (qb['key'],receiver['key'])
                qb_pairs[key] += 1
                games[key] = qb['game'] if qb['game'] and qb['game']==receiver['game'] else None
    stress_reason = (EXPORT_UNAVAILABLE if not data['forecast_qualified'] else
                     'Choose one or two risk athletes' if not targets else
                     'Unavailable: selected identity outside captured pool' if any(k not in pool for k in targets) else '')
    factors = list(product(RETENTIONS, repeat=len(targets))) if not stress_reason else []
    baselines, contributions, delta_rows, full_rows = [], [], [], []
    for i,row in enumerate(rows):
        check(cancelled)
        values = {}
        for slot in row:
            player = pool[slot['key']]
            base = player['base_projection'] if data['forecast_qualified'] else None
            values[slot['key']] = (1.5 if slot['role']=='CPT' else 1)*base if base is not None and (
                slot['role']!='CPT' or player['captain_ok']) else None
        contributions.append(values)
        baselines.append(total(list(values.values())))
        if baselines[-1] is not None:
            full_rows.append(i)
        if factors and all(values.get(k,0) is not None for k in targets):
            delta_rows.append(i)
    # Cohorts are fixed for the entire grid, including its unchanged baseline.
    deltas = [[total([contributions[i].get(k,0)*(1-r) if contributions[i].get(k,0) is not None else None
                     for k,r in zip(targets,case)]) for i in range(n)] for case in factors]
    delta_rows = [i for i in delta_rows if all(ds[i] is not None for ds in deltas)]
    full_rows = [i for i in full_rows if all(ds[i] is not None and finite(baselines[i]-ds[i]) is not None for ds in deltas)]
    baseline = distribution([baselines[i] for i in full_rows])
    scenarios = []
    for case,changes in zip(factors,deltas):
        check(cancelled)
        reduced = {k for k,r in zip(targets,case) if r<1}
        affected = sum(bool(reduced & {s['key'] for s in row}) for row in rows)
        scenarios.append(dict(retention=list(case), affected=metric(affected,n),
            M=len(full_rows), M_delta=len(delta_rows), baseline_mean=baseline['mean'],
            stressed_mean=distribution([baselines[i]-changes[i] for i in full_rows])['mean'],
            row_change=distribution([changes[i] for i in full_rows]),
            direct_change=distribution([changes[i] for i in delta_rows])))
    denominator = total([baselines[i] for i in full_rows])
    numerator = total([contributions[i].get(k,0) for i in full_rows for k in targets]) if not stress_reason else None
    alt_rows = []
    for risk in targets:
        for alternative in alternatives:
            coverage = pair_coverage(rows,pool,risk,alternative)
            counts = coverage['buckets'] or {}
            alt_rows.append(dict(risk=risk, alternative=alternative, coverage=coverage,
                exposure=exposure(rows,pool,alternative,kind), relationship=relation(pool,risk,alternative),
                all_coexist=bool(counts.get('both') and counts.get('b_only')==0)))
    union = (metric(sum(bool(set(alternatives)&{s['key'] for s in row}) for row in rows),n)
             if all(k in pool for k in alternatives) else metric(None,0))
    check(cancelled)
    return RiskReport.freeze(dict(version=VERSION, capture=data, targets=list(targets), alternatives=list(alternatives),
        R=data['source_count'], N=n, U=len(set(signatures)), M=len(full_rows), M_delta=len(delta_rows),
        exposures=exposures, target_exposures=target_exposures, pair=pair,
        teams=[dict(team=team, appearances=metric(sum(counts.values()),len(team_rows)),
                    players_per_entry=dict(counts)) for team,counts in sorted(teams.items())],
        qb_receivers=[dict(qb=a,receiver=b,appearances=metric(count,len(context_rows)),game=games[(a,b)])
                      for (a,b),count in sorted(qb_pairs.items())],
        context=dict(team=len(team_rows),position_team=len(context_rows),
                     game=sum(all(pool[s['key']]['game'] for s in row) for row in rows)),
        selected_projection_share=numerator/denominator if numerator is not None and denominator and denominator>0 else None,
        scenarios=scenarios, stress_reason=stress_reason, alternatives_coverage=alt_rows, alternative_union=union,
        quantiles='Sorted empirical index floor((n-1)*q); distribution across portfolio entries under this assumption'))


def display_number(value):
    return 'Unavailable' if value is None else f'{value:.3f}'


def summary(report, include_details=False):
    """Aggregate copy excludes all source IDs, paths, private labels and rosters."""
    d = report.data
    lines = ['Portfolio Risk - read-only', d['capture']['label'],
        f"{d['capture']['format']} | source occurrences R={d['R']}; valid N={d['N']}; unique U={d['U']}; complete forecasts M={d['M']}; direct-change M_delta={d['M_delta']}",
        f"Rejected/omitted occurrences: {d['capture']['source_count']-d['N']}. Limits: {d['capture']['limits']}",
        'Shared roster dependencies, not measured outcome correlations. Concentration is not an error; neither selected player does not mean safe.',
        'Retention is a point-production assumption, not minutes, injury probability, simulated frequency or transferred production. Signed changes may increase totals.']
    for i,e in enumerate(d['target_exposures'],1):
        lines.append(f"Target {i}: {e['any']['count']} / {e['any']['denominator']} appearances; {display_number(e['any']['pct'])}%")
    if d['pair']:
        lines.append(f"Selected pair: {d['pair']['buckets']}; either (at least one)={d['pair']['either']}; exactly one={d['pair']['exactly_one']}; denominator={d['pair']['denominator']}")
    lines += [f"Alternative union: {d['alternative_union']['count']} / {d['alternative_union']['denominator']}; exposure does not prove injury compensation.",
              f"Selected projected contribution share on M: {display_number(d['selected_projection_share'])}"]
    if d['stress_reason']:
        lines.append(d['stress_reason'])
    for s in d['scenarios']:
        lines.append(f"Retained {s['retention']}: affected {s['affected']['count']}/{s['affected']['denominator']}; mean points {display_number(s['baseline_mean'])} -> {display_number(s['stressed_mean'])} on M={s['M']}; direct mean change {display_number(s['direct_change']['mean'])} on M_delta={s['M_delta']}; entry change min/median/p90/max: " +
                     '/'.join(display_number(s['row_change'][k]) for k in ('min','median','p90','max')))
    lines += [d['quantiles'], 'No probability average, cash/Top-1%/ROI or injury prediction. Submission and original build are not established.']
    if include_details:
        lines += ['', 'PRIVATE: player names and lineup details included.']
        for e in d['exposures']:
            p = d['capture']['pool'][e['key']]
            lines.append(f"{p['name']} [{p['team']} {p['position']}]: {e['any']['count']}/{d['N']} appearances; {p['role']['group']}; role evidence {p['role']['state']}")
        for i,row in enumerate(d['capture']['rosters'],1):
            lines.append(f"Entry {i}: " + ', '.join(s['role']+' '+d['capture']['pool'][s['key']]['name'] for s in row))
    return '\n'.join(lines)
