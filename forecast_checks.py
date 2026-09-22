"""Cheap, read-only review signals from recorded NFL forecast inputs."""
from collections import Counter
from projection_sources import number
from usage_history import history_evidence


def check_forecasts(players):
    findings=[]; checked=0; seen=set()
    for p in players:
        if p.get('Position') not in ('QB','RB','WR','TE'): continue
        key=(str(p.get('FlexID') or p.get('Name')),p.get('Team'),p.get('Position'))
        if key in seen: continue
        seen.add(key);checked+=1
        name=f"{p.get('Name','Unknown')} [{p.get('Team','')} {p.get('Position','')}]"
        source=p.get('ProjectionSource')
        if source not in ('Automatic workload estimate','Historical average estimate'): continue
        base=number(p.get('BaseProjection')); history=number(p.get('HistoricalPPG'))
        def add(code, message, level='review'):
            findings.append(dict(player=name,code=code,level=level,message=message,source=source))
        if base is None: continue  # Existing coverage check reports missing forecasts.
        if base>=6 and history_evidence(p)['stress']:
            add('limited_evidence',f'{base:.1f} base points; {history_evidence(p)["reason"]}. Check the recorded role if this player is important to your entries.','information')
        if source!='Automatic workload estimate': continue
        w=p.get('NFLWorkload') or {}
        if not w:
            add('missing_workload','Automatic workload forecast has no recorded opportunity breakdown. Refresh player data to inspect its basis.')
            continue
        if history is not None and history>0 and abs(base-history)>=5 and abs(base-history)/history>=.5:
            add('history_gap',f'Base {base:.1f} vs historical average {history:.1f} points. Role changes may explain this difference; review the opportunity estimate.')
        recent_games=number(p.get('NFLUsageGames')) or 0
        for metric,minimum in (('attempts',8),('carries',4),('targets',3)):
            projected=number(w.get(metric)); recent=number(p.get('NFLRecent'+metric.title()))
            if projected is None:
                add('invalid_opportunity',f'Missing or invalid projected {metric} in the recorded workload.')
                continue
            budget=number((w.get('budgets') or {}).get(metric))
            if budget is not None and projected>budget+.01:
                add('budget_exceeded',f'Projected {metric} {projected:.1f} exceeds its recorded position budget {budget:.1f}.')
            if recent_games>=4 and recent is not None and abs(projected-recent)>=minimum and (recent==0 or abs(projected-recent)/recent>=.5):
                season=p.get('NFLUsageSeason')
                add('opportunity_gap',f'Projected {metric} {projected:.1f} vs recent observed {recent:.1f}/game ({recent_games:g} games, season {season or "unknown"}). Confirm any role change; prior-season form may not represent today’s role.')
    priority={'missing_workload':0,'invalid_opportunity':0,'budget_exceeded':0,'history_gap':1,'opportunity_gap':1}
    findings.sort(key=lambda f:(f['level']!='review',priority.get(f['code'],2),f['player'],f['code']))
    return dict(version=1,checked=checked,review_players=len({f['player'] for f in findings if f['level']=='review'}),
                information_players=len({f['player'] for f in findings if f['level']=='information'}),
                counts=dict(Counter(f['code'] for f in findings)),findings=findings)


def format_checks(report):
    if not report:return []
    lines=['','Automatic forecast checks',
        f"- {report['checked']} skill players checked; {report['review_players']} with forecast review signals; {report['information_players']} with limited-evidence notices."]
    rows=report['findings']; reviews=[r for r in rows if r['level']=='review']; info=[r for r in rows if r['level']=='information']
    shown=reviews[:6]+info[:2]
    for r in shown:lines.append(f"- {r['player']}: {r['message']}")
    if len(rows)>len(shown):lines.append(f'- {len(rows)-len(shown)} additional findings retained in the saved build diagnostics.')
    if not rows:lines.append('- No threshold-based review signals found in the recorded inputs. This does not validate forecast accuracy.')
    lines.append('- Automatic checks use saved inputs only; no extra SIMs or downloads. Review thresholds are heuristics, not calibrated error probabilities. Supplied/manual forecasts are not judged against these automatic-workload rules; K/DST are outside this check. No forecasts or exposure limits were changed.')
    return lines
