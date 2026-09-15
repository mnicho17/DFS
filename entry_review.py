"""Descriptive construction counts, independent of scoring and selection."""
from collections import Counter
from itertools import combinations
from statistics import mean
import math
from portfolio_insights import _position, _team, _opponent


def review_entries(lineups, kind='classic', salary_cap=50000, source='Generated outputs'):
    counts={k:Counter() for k in ('Players','Pairs','Trios','Captain + FLEX','Constructions','Salary unused','Repeated rosters')}
    labels={}; salaries=[]; unknown_salary=0; excluded=0; valid=0; unknown_context=0
    def identity(p):
        return str(p.get('FlexID') or p.get('FlexNamePlusID') or (str(p.get('Name',''))+'|'+_team(p)+'|'+_position(p)))
    def money(value):
        try:v=float(value)
        except (TypeError,ValueError):return None
        return v if math.isfinite(v) and v>=0 else None
    for lu in lineups:
        try:players=([lu['Captain']]+list(lu['Flex'])) if kind=='showdown' else list(lu)
        except (TypeError,KeyError):excluded+=1;continue
        if len(players)!=(6 if kind=='showdown' else 9) or any(not isinstance(p,dict) or not p.get('Name') for p in players):
            excluded+=1;continue
        keys=[identity(p) for p in players]
        if len(set(keys))!=len(keys):excluded+=1;continue
        valid+=1
        for key,p in zip(keys,players):labels[key]=f"{p['Name']} [{_team(p)} {_position(p)}]"
        for key in keys:counts['Players'][key]+=1
        for size,title in ((2,'Pairs'),(3,'Trios')):
            for group in combinations(sorted(keys),size):counts[title][group]+=1
        signature=(keys[0],tuple(sorted(keys[1:]))) if kind=='showdown' else tuple(sorted(keys))
        counts['Repeated rosters'][signature]+=1
        values=[money(p.get('CptSalary') if kind=='showdown' and i==0 else p.get('FlexSalary')) for i,p in enumerate(players)]
        if any(v is None for v in values):unknown_salary+=1
        else:
            salary=sum(values);salaries.append(salary);unused=salary_cap-salary
            band='Over cap' if unused<0 else '$0–200' if unused<=200 else '$201–700' if unused<=700 else '$701–1,200' if unused<=1200 else 'Over $1,200'
            counts['Salary unused'][band]+=1
        if kind=='showdown':
            for key in keys[1:]:counts['Captain + FLEX'][(keys[0],key)]+=1
        if any(not _team(p) or not _position(p) for p in players):unknown_context+=1;continue
        teams=Counter(_team(p) for p in players)
        if kind=='showdown':
            counts['Constructions']['Team split: '+'–'.join(map(str,sorted(teams.values(),reverse=True)))]+=1
            counts['Constructions']['Captain position: '+_position(players[0])]+=1
            counts['Constructions']['QBs: '+str(sum(_position(p)=='QB' for p in players))]+=1
            counts['Constructions']['K/DST slots: '+str(sum(_position(p) in ('K','DST') for p in players))]+=1
        else:
            qbs=[p for p in players if _position(p)=='QB']
            if len(qbs)!=1:unknown_context+=1;continue
            qb=qbs[0];team=_team(qb);opp=_opponent(qb)
            stack=sum(_team(p)==team and _position(p) in ('WR','TE') for p in players)
            counts['Constructions'][f'QB + {stack} same-team WR/TE']+=1
            counts['Constructions']['QB team: '+team]+=1
            if opp:
                bring=sum(_team(p)==opp and _position(p) in ('RB','WR','TE') for p in players)
                counts['Constructions'][f'Opposing RB/WR/TE: {bring}']+=1
            else:counts['Constructions']['Opposing players: unknown game metadata']+=1
            game_counts=Counter()
            for p in players:
                opponent=_opponent(p)
                if opponent:game_counts[tuple(sorted((_team(p),opponent)))]+=1
            for game,n in game_counts.items():
                if n>=3:counts['Constructions']['3+ players from '+' vs '.join(game)]+=1
    tables={}
    for title,counter in counts.items():
        rows=[]
        for key,n in counter.most_common():
            if title in ('Pairs','Trios','Captain + FLEX','Repeated rosters') and n<2:continue
            if title=='Players':label=labels[key]
            elif title in ('Pairs','Trios'):label=' + '.join(labels[k] for k in key)
            elif title=='Captain + FLEX':label='CPT '+labels[key[0]]+' + FLEX '+labels[key[1]]
            elif title=='Repeated rosters':
                label=('CPT '+labels[key[0]]+' | '+' + '.join(labels[k] for k in key[1])) if kind=='showdown' else ' + '.join(labels[k] for k in key)
            else:label=key
            rows.append(dict(label=label,count=n,pct=100*n/valid if valid else 0))
        tables[title]=rows
    report=dict(kind=kind,source=source,total=len(lineups),valid=valid,excluded=excluded,unknown_salary=unknown_salary,
        unknown_context=unknown_context,salary_mean=mean(salaries) if salaries else None,salary_count=len(salaries),tables=tables)
    report['text']=format_review(report)
    return report


def format_review(r):
    lines=['Review my entries',f"{r['kind'].title()} — {r['source']}: {r['valid']}/{r['total']} rosters analyzed.",
        f"Excluded malformed/duplicate-player rosters: {r['excluded']}; unknown salary: {r['unknown_salary']}; incomplete construction metadata: {r['unknown_context']}.",
        'Every percentage uses all analyzed rosters, including repeated entries. Pairs/trios count shared athletes regardless of slot; Captain + FLEX and repeated Showdown rosters preserve Captain identity.',
        'Construction rows describe overlapping categories and do not sum to 100%. Missing metadata is not an observed absence. Repetition describes shared dependencies, not measured outcome correlation, opponent duplication or a strategy error. No lineups or settings are changed. Saved entries may span builds; choose a single slate for meaningful comparisons.']
    if r['salary_mean'] is not None:lines.append(f"Average salary: ${r['salary_mean']:,.0f} across {r['salary_count']} rosters with complete salary data.")
    for title,rows in r['tables'].items():
        if not rows:continue
        lines+=['',title]
        for row in rows[:20]:lines.append(f"- {row['label']}: {row['count']}/{r['valid']} ({row['pct']:.1f}%)")
        if len(rows)>20:lines.append(f'- {len(rows)-20} more rows available in the dashboard.')
    lines.append('Privacy: includes player names and roster construction; no account settings or API keys.')
    return '\n'.join(lines)
