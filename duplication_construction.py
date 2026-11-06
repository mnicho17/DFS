"""Descriptive observed copies by construction; never optimizer inputs."""
from collections import defaultdict
import statistics


def build_rows(rosters,keys):
    rows=list(rosters)
    if any(pattern is None for _,_,pattern in rows):return None
    result={key:[dict(rosters=0,entries=0,shared_entries=0,sum_squared_copies=0,maximum_copies=0) for _ in (0,1)] for key in keys}
    for n,_,pattern in rows:
        for key,present in zip(keys,pattern):
            if present is None:continue
            side=result[key][present];side['rosters']+=1;side['entries']+=n
            side['shared_entries']+=n if n>1 else 0;side['sum_squared_copies']+=n*n
            side['maximum_copies']=max(side['maximum_copies'],n)
    for sides in result.values():
        for side in sides:
            n=side['entries'];r=side['rosters']
            side['shared_entry_pct']=100*side['shared_entries']/n if n else None
            side['mean_copies_per_roster']=n/r if r else None
            side['mean_copies_per_entry']=side['sum_squared_copies']/n if n else None
    return result


def summarize(report,minimum=10):
    from single_entry_review import field_scope,MIN_GAMES
    if type(minimum) is not int or minimum not in (5,10):raise ValueError('Use five or ten distinct rosters per side.')
    groups=defaultdict(lambda:defaultdict(list))
    endpoints=('shared_entry_pct','mean_copies_per_roster','mean_copies_per_entry')
    for c in report['contests']:
        if c['format']!='showdown' or c['preset']!='Single Entry':continue
        detail=(c.get('single_entry') or {}).get('duplication') or {}
        for metric,sides in (detail.get('by_construction') or {}).items():
            if min(side['rosters'] for side in sides)<minimum:continue
            gap={k:sides[1][k]-sides[0][k] for k in endpoints}
            groups[field_scope(c),metric][(c['date'],tuple(c['slate'][2]))].append(gap)
    result=[]
    for (scope,metric),games in sorted(groups.items()):
        values=[{k:statistics.mean(v[k] for v in rows) for k in endpoints} for _,rows in sorted(games.items())]
        summary={}
        for key in endpoints:
            numbers=[v[key] for v in values]
            omit=[statistics.mean(numbers[:i]+numbers[i+1:]) for i in range(len(numbers))] if len(numbers)>=3 else []
            summary[key]=dict(difference=statistics.mean(numbers),higher=sum(v>0 for v in numbers),lower=sum(v<0 for v in numbers),equal=sum(v==0 for v in numbers),omit_one_range=[min(omit),max(omit)] if omit else None)
        result.append(dict(scope=scope,metric=metric,games=len(values),status='supported descriptive sample' if len(values)>=MIN_GAMES else 'insufficient independent games',endpoints=summary))
    return result


def render(report):
    from single_entry_review import LABELS,MIN_GAMES,field_scope
    lines=['OBSERVED DUPLICATION BY CONSTRUCTION',
        'Feature-present versus feature-absent, within each contest. WR/TE Captain with own QB uses only WR/TE Captain rosters. Other rows use all resolved rosters, including missing-rank entries.',
        'Shared entry percentage is the fraction of observed entries on rosters with multiple copies. Mean copies per distinct roster weights each roster once; mean copies per entry weights popular rosters by their entry counts. These are different denominators.',
        'Counts are observed lower bounds. Unknown/unseen copies are not inferred. Comparisons are not matched by Captain, salary, player quality or entry fee; correlated construction rows are not independent effects. No payout, prediction, profitability or rule recommendation.',
        'Support requires five or ten DISTINCT observed rosters on EACH side; repeated copies cannot supply distinct-roster support. Contests average within salary game/date, then games equally. Five games is only a descriptive minimum.']
    captured=False
    for c in report['contests']:
        if c['format']!='showdown' or c['preset']!='Single Entry':continue
        rows=((c.get('single_entry') or {}).get('duplication') or {}).get('by_construction')
        if rows is None:continue
        captured=True;lines.append(c['name']+' | '+field_scope(c))
        for metric,sides in rows.items():
            for label,v in zip(('absent','present'),sides):
                mean='unavailable' if v['mean_copies_per_entry'] is None else f"{v['mean_copies_per_entry']:.3f}"
                per_roster='unavailable' if v['mean_copies_per_roster'] is None else f"{v['mean_copies_per_roster']:.3f}"
                lines.append(f"  {LABELS[metric]} / {label}: {v['entries']} entries / {v['rosters']} distinct rosters; {v['shared_entries']} entries on shared rosters; mean copies per roster {per_roster}, per entry {mean}; maximum {v['maximum_copies']}.")
    if not captured:lines.append('Unavailable: no captured per-roster construction evidence; rerun the verified review. Aggregate copy counts cannot reconstruct this breakdown.')
    for minimum in (5,10):
        lines.append(f'At least {minimum} distinct rosters per side:')
        rows=summarize(report,minimum)
        if not rows:lines.append('Unavailable: no comparisons with this support.')
        for row in rows:
            text=f"{row['scope']} / {LABELS[row['metric']]}: {row['games']} games; {row['status']}"
            if row['games']>=MIN_GAMES:
                for key,v in row['endpoints'].items():
                    unit=' pp' if key=='shared_entry_pct' else ' copies'
                    label={'shared_entry_pct':'Shared-entry rate','mean_copies_per_roster':'Mean copies per roster','mean_copies_per_entry':'Mean copies per entry'}[key]
                    text+=f"; {label} present-minus-absent {v['difference']:+.3f}{unit}; higher/lower/equal {v['higher']}/{v['lower']}/{v['equal']}; omit-one {v['omit_one_range']}"
            lines.append(text+'.')
    return '\n'.join(lines)
