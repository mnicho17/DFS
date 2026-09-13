"""Explainable salary/role screens; no new forecasts or simulation work."""
import copy
from collections import defaultdict
from datetime import datetime, timezone
from forecast_checks import check_forecasts
from nfl_eligibility import apply_qb_eligibility, unavailable, depth
from portfolio_insights import _position, _team
from projection_sources import number
from usage_history import history_evidence

CATEGORIES = ('Underpriced role candidate', 'High-projection anchor', 'Popular play to assess',
              'Lower-owned alternative', 'Role-change opportunity', 'Needs review')
POSITIVE = set(CATEGORIES[:-1])


def build_core_plays(players, kind='classic', now=None):
    if kind not in ('classic', 'showdown'):
        raise ValueError('Core Plays supports NFL Classic and Showdown.')
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    pool = apply_qb_eligibility(copy.deepcopy(players))
    groups = defaultdict(list)
    for p in pool:
        groups[(_team(p), _position(p))].append(p)
    findings = defaultdict(list)
    for f in check_forecasts(pool)['findings']:
        findings[f['player']].append(f['message'])
    rows = []
    seen = set()
    for p in pool:
        name = str(p.get('Name') or '').strip()
        if not name:
            continue
        pos, team = _position(p), _team(p)
        key = str(p.get('FlexID') or p.get('FlexNamePlusID') or f'{name}|{team}|{pos}')
        if key in seen:
            continue
        seen.add(key)
        label = f'{name} [{team} {pos}]'
        d = depth(p)
        earlier = groups[(team, pos)]
        promoted = (d > 1 and bool(team) and all(
            any(depth(q) == i and unavailable(q) for q in earlier) for i in range(1, d)))
        effective = 1 if promoted else d
        role = p.get('NFLQBReason') if pos == 'QB' else (
            f'Recorded {pos}{d}' if d else 'Depth role unknown')
        if pos in ('K', 'DST'):
            role = 'Specialist; separate value comparison'
        if promoted and not unavailable(p):
            role += '; earlier depth slots unavailable'
        supported = bool(team) and (p.get('NFLQBEligible') is True if pos == 'QB' else
                    effective in range(1, {'RB':2, 'WR':3, 'TE':2}.get(pos, 0)+1) or pos in ('K', 'DST'))
        source = p.get('ProjectionSource') or 'Legacy / unknown source'
        checked = str(p.get('LiveStatusUpdatedAt') or '')
        concerns = []
        try:
            stamp = datetime.fromisoformat(checked.replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                raise ValueError('Unknown timezone')
            age = (now-stamp).total_seconds()/3600
            if age > 24:
                concerns.append('Role/status check is over 24 hours old; refresh before relying on it.')
            elif age < -1:
                concerns.append('Role/status timestamp is in the future; freshness is uncertain.')
        except (ValueError, TypeError):
            concerns.append('Role/status check time is unknown.')
        if not supported:
            concerns.append('Starting/rotation role is unverified or outside the supported rotation.')
        injury = str(p.get('InjuryStatus') or p.get('NFLAvailability') or '').strip()
        if injury.upper() in ('Q', 'QUESTIONABLE', 'D', 'DOUBTFUL', 'GTD'):
            concerns.append(f'Recorded availability: {injury}; this is not a confirmed absence.')
        if source in ('Legacy / unknown source', 'Historical average estimate'):
            concerns.append('Forecast uses historical/unknown provenance, not a verified current projection.')
        evidence = history_evidence(p)
        if pos in ('QB','RB','WR','TE') and evidence['stress']:
            concerns.append(evidence['reason'])
        concerns.extend(findings[label])
        history = f"{p.get('NFLUsageSeason') or 'Unknown season'}; {p.get('NFLUsageGames', 'unknown')} recent-window games; {evidence['reason']}"
        w = p.get('NFLWorkload') or {}
        workload = '; '.join(f'{number(w.get(k)):.1f} {k}' for k in ('attempts','carries','targets') if number(w.get(k)) is not None) or 'No recorded workload breakdown'
        slots = ('Captain','FLEX') if kind == 'showdown' else ('Regular',)
        for slot in slots:
            captain = slot == 'Captain'
            salary = number(p.get('CptSalary' if captain else 'FlexSalary'))
            projection = number(p.get('CptProjection' if captain else 'FlexProjection'))
            if source == 'Missing forecast':
                projection = None
            ownkey = 'ProjCptOwnPct' if captain else 'ProjFlexOwnPct' if kind == 'showdown' else 'ProjOwnPct'
            ownership = number(p.get(ownkey)) if p.get('OwnershipUnits') == 'percent_of_entries' else None
            if ownership is not None and ownership > 100:
                ownership = None
            excluded = []
            if unavailable(p):
                excluded.append('Recorded unavailable')
            if p.get('NFLQBEligible') is False:
                excluded.append(p.get('NFLQBReason', 'QB excluded'))
            if p.get('FadeCpt' if captain else 'FadeFlex'):
                excluded.append('Manually faded for this slot')
            if salary is None or salary <= 0:
                excluded.append('Missing/invalid salary')
            if projection is None:
                excluded.append('Missing forecast')
            notes = list(concerns)
            if ownership is None:
                notes.append('Ownership percentage or its units are unavailable.')
            if projection == 0:
                notes.append('Explicit zero forecast; not a positive core candidate.')
            value = projection*1000/salary if projection is not None and salary and salary > 0 else None
            rows.append(dict(key=key, player=label, name=name, team=team, position=pos, slot=slot,
                salary=salary, projection=projection, value=value, ownership=ownership,
                ownership_source=p.get('OwnershipSource') or 'Not recorded', source=source,
                role=role, workload=workload, history=history, checked=checked or 'Unknown',
                eligible=not excluded, supported=supported, promoted=promoted,
                categories=[], reasons=[], concerns=list(dict.fromkeys(notes)), excluded=excluded,
                peers=0, value_percentile=None, projection_percentile=None, partners=[]))
    for r in rows:
        peers = [q for q in rows if q['slot']==r['slot'] and q['position']==r['position']
                 and q['eligible'] and q['supported'] and q['projection'] is not None and q['projection']>0]
        r['peers'] = len(peers)
        active = r['eligible'] and r['supported'] and r['projection'] is not None and r['projection']>0
        def add(category, reason):
            if category not in r['categories']:
                r['categories'].append(category)
            r['reasons'].append(reason)
        if active and len(peers)>=4:
            # Strictly-lower percentiles avoid calling an all-tied position elite.
            for field in ('value','projection'):
                r[field+'_percentile'] = 100*sum(q[field]<r[field] for q in peers)/(len(peers)-1)
            if r['value_percentile']>=75 and r['projection_percentile']>=50:
                add(CATEGORIES[0], f"Value exceeds {r['value_percentile']:.0f}% of supported {r['position']} peers, with projection at or above the middle of that group.")
            if r['projection_percentile']>=80:
                add(CATEGORIES[1], f"Projection exceeds {r['projection_percentile']:.0f}% of supported {r['position']} peers. This is a positional anchor, not a floor or ceiling estimate.")
        threshold = 8 if r['slot']=='Captain' else 25 if kind=='showdown' else 10
        if active and r['ownership'] is not None and r['ownership']>=threshold:
            add(CATEGORIES[2], f"Estimated ownership {r['ownership']:.1f}% exceeds the {threshold}% review threshold; popularity alone does not imply value.")
        if active and r['ownership'] is not None:
            alternatives = [q for q in peers if q['key']!=r['key'] and q['ownership'] is not None
                and q['ownership']>=threshold and q['ownership']-r['ownership']>=5
                and r['ownership']<=.7*q['ownership'] and r['projection']>=.9*q['projection']
                and r['salary']<=1.1*q['salary']]
            for q in sorted(alternatives,key=lambda q:-q['ownership'])[:2]:
                add(CATEGORIES[3], f"Compare with {q['name']}: {q['projection']:.1f} projected points, ${q['salary']:,.0f}, {q['ownership']:.1f}% estimated ownership. Similar mean projection does not establish similar ceiling.")
        if active and r['promoted']:
            add(CATEGORIES[4], 'Earlier recorded depth slots are explicitly unavailable. Increased opportunity is a candidate signal, not confirmation of starting snaps.')
        if r['concerns'] or r['excluded']:
            r['categories'].append(CATEGORIES[5])
        if active and kind=='classic' and r['position']=='QB':
            partners = [q for q in rows if q['team']==r['team'] and q['position'] in ('WR','TE')
                        and q['eligible'] and q['supported'] and q['projection'] is not None and q['projection']>0]
            r['partners'] = [f"{q['name']} ({q['projection']:.1f} pts; ${q['salary']:,.0f})" for q in sorted(partners,key=lambda q:-q['projection'])[:3]]
    rows.sort(key=lambda r:(not bool(POSITIVE.intersection(r['categories'])),not r['eligible'], -(r['value'] or 0),r['player'],r['slot']))
    return dict(version=1,kind=kind,rows=rows,generated_at=now.isoformat(),players=len(seen),
                note='Loaded data only; no refresh, forecasts, exposure changes or SIMs. Labels are heuristic review candidates, not must-haves or calibrated predictions. Showdown Captain/FLEX use separate salary, projection and ownership. Value per $1,000 alone cannot identify the best Captain. Data warnings remain visible even for tagged candidates.')


def format_core_report(report, rows=None):
    rows = report['rows'] if rows is None else rows
    lines = ['DFS Core Plays',f"{report['kind'].title()} | {report['players']} loaded players | {len(rows)} displayed player/slot rows",report['note'],
        'Value/anchor thresholds require at least four eligible, supported, positive-forecast peers at the same position and slot. Underpriced: value percentile >=75 and projection >=50; anchor: projection >=80. Strictly lower peers determine percentiles. Popular: Regular >=10%, FLEX >=25%, Captain >=8%. Alternatives: same position/slot, >=90% projection, <=110% salary, >=5pp and >=30% less ownership than a popular peer. All ownership remains estimated; unknown units are not guessed.',
        'Recorded rotation: eligible QB; RB1-2, WR1-3, TE1-2; K/DST separate. Confirmed unavailable earlier slots may promote a role. Status older than 24h is flagged; no live verification occurs here. Filters affect this report, not player selections.']
    for r in rows:
        def n(key,fmt):return format(r[key],fmt) if r[key] is not None else 'Unknown'
        lines += ['',f"{r['player']} — {r['slot']} | {', '.join(r['categories']) or 'No threshold flags'}",
                  f"Salary: {n('salary',',.0f')}; projection: {n('projection','.2f')}; pts/$1,000: {n('value','.2f')}; estimated ownership: {n('ownership','.1f')}%.",
                  f"Role: {r['role']}; workload: {r['workload']} (unmultiplied player opportunities).",
                  f"Forecast: {r['source']}; ownership source: {r['ownership_source']}; status checked: {r['checked']}; history: {r['history']}."]
        lines += ['Reason: '+s for s in r['reasons']]
        lines += ['Review: '+s for s in r['concerns']]
        lines += ['Excluded from candidates: '+s for s in r['excluded']]
        if r['partners']:lines.append('Same-team receiving partners: '+'; '.join(r['partners']))
    lines.append('Privacy: includes player names, forecasts and recorded data timestamps; excludes account settings and API keys.')
    return '\n'.join(lines)
