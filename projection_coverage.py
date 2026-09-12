"""Describe the actual build pool without treating estimates as missing forecasts."""
from collections import Counter
from projection_sources import number
from usage_history import history_evidence
from nfl_auto_data import _slate_season


def summarize_projection_coverage(players):
    season=_slate_season(players);sources=Counter();usage=Counter();positions=Counter();missing=[];history=Counter()
    for p in players:
        source=str(p.get('ProjectionSource') or 'Legacy / unknown source')
        sources[source]+=1;positions[str(p.get('Position') or 'Unknown')]+=1
        pos=str(p.get('Position') or '').upper()
        if pos in {'QB','RB','WR','TE'}: history[history_evidence(p)['reason']]+=1
        if pos not in {'QB','RB','WR','TE'}:
            usage['Specialist / usage not applicable']+=1
        elif (number(p.get('NFLUsageGames')) or 0)>0:
            year=p.get('NFLUsageSeason')
            usage['Current-season usage' if year==season else 'Prior-season usage' if year==season-1 else 'Other / unknown usage season']+=1
        else:
            usage['No matched usage']+=1
        if source=='Missing forecast' or number(p.get('BaseProjection',p.get('FlexProjection'))) is None:
            missing.append(f"{p.get('Name','Unknown')} [{p.get('Team','')} {pos}]")
    return dict(eligible=len(players),season=season,sources=dict(sources),usage=dict(usage),
                history=dict(history),positions=dict(positions),missing_count=len(missing),missing_players=missing[:12])


def format_projection_coverage(report):
    if not report:return []
    lines=['','Projection and usage coverage — actual build pool',
           f"- Eligible players: {report['eligible']}; reference NFL season: {report['season']}."]
    lines.append('- Projection sources: '+'; '.join(f'{key}: {value}' for key,value in sorted(report['sources'].items()))+'.')
    lines.append('- Recent-window usage evidence: '+'; '.join(f'{key}: {value}' for key,value in sorted(report['usage'].items()))+'.')
    if report.get('history'): lines.append('- Full-season history: '+'; '.join(f'{k}: {v}' for k,v in sorted(report['history'].items()))+'.')
    lines.append(f"- Missing / invalid forecasts: {report['missing_count']}.")
    if report.get('missing_players'):lines.append('  Review: '+'; '.join(report['missing_players']))
    lines.append('- Automatic workload and historical-average estimates are forecasts with limitations, not missing values. Usage counts describe available evidence; supplied/manual forecasts take precedence. Specialists use separate history. Prior-season usage is not current-season form; missing usage is not an observed zero.')
    return lines
