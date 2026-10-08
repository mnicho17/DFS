"""Read-only automatic-cap pressure and construction comparisons."""
from collections import Counter
import math
from lineup_ranking import ranked_lineups


def _counts(rows):
    from portfolio_rules import player_key
    total, captain = Counter(), Counter()
    for row in rows:
        players = [row['Captain']] + list(row['Flex'])
        total.update({player_key(p) for p in players})
        captain[player_key(row['Captain'])] += 1
    return total, captain


def _mix(rows):
    from optimizers import _position_tokens
    return dict(lineups=len(rows),
        zero_qb=sum(not any('QB' in _position_tokens(p) for p in [r['Captain']]+list(r['Flex'])) for r in rows),
        two_plus_specialists=sum(sum(bool(_position_tokens(p) & {'K','DST'}) for p in [r['Captain']]+list(r['Flex'])) >= 2 for r in rows))


def compare(candidates, selected, requested, starting, effective, players):
    candidates, selected = list(candidates), list(selected)
    if not candidates or any(not isinstance(getattr(r,'sim_metrics',{}).get('sim_scenarios'),(int,float))
            or not math.isfinite(r.sim_metrics['sim_scenarios']) or r.sim_metrics['sim_scenarios'] <= 0 for r in candidates):
        return dict(status='unavailable', reason='A complete scored candidate bank is required.')
    from optimizers import _position_tokens
    if any(not (_position_tokens(p) & {'QB','RB','WR','TE','K','DST'})
            for row in candidates for p in [row['Captain']]+list(row['Flex'])):
        return dict(status='unavailable', reason='Complete position metadata is required.')
    ranked = ranked_lineups(candidates)[:requested]
    rank_counts, selected_counts = _counts(ranked), _counts(selected)
    rows=[]
    for index, role in enumerate(('total','captain')):
        for key, initial in (starting.get(role) or {}).items():
            current = (effective.get(role) or {}).get(key,initial)
            if initial is None or current is None: continue
            count = rank_counts[index][key]
            if count <= initial and count <= current: continue
            rows.append(dict(key=key,name=players.get(key,{}).get('Name') or key,role=role,
                ranked_count=count,selected_count=selected_counts[index][key],starting_cap=initial,
                effective_cap=current,ranked_excess=max(0,count-current)))
    rows.sort(key=lambda r:(-r['ranked_excess'],-r['ranked_count'],r['name'],r['role'],r['key']))
    return dict(status='available',requested=requested,ranked=_mix(ranked),selected=_mix(selected),players=rows,
        interpretation='Ranked leaders ignore portfolio rules and retained-entry priority. Caps, uniqueness, groups, retained entries and diversification can all change selection; this comparison does not isolate any one cause. No settings changed.')


def text(data):
    if not data or data.get('status')!='available':return []
    lines=['','Automatic exposure-cap pressure']
    for stage,label in (('ranked','Ranked leaders before portfolio rules'),('selected','Selected output')):
        row=data[stage];n=row['lineups']
        lines.append(f"- {label}: zero QB {row['zero_qb']}/{n}; two-plus K/DST {row['two_plus_specialists']}/{n}.")
    for row in data['players'][:12]:
        role='total' if row['role']=='total' else 'Captain'
        lines.append(f"- {row['name']} {role}: ranked {row['ranked_count']}/{data['ranked']['lineups']}; starting automatic cap {row['starting_cap']}/{data['requested']}; effective cap {row['effective_cap']}/{data['requested']}; selected {row['selected_count']}/{data['selected']['lineups']}; ranked {row['ranked_excess']} above effective cap.")
    if len(data['players'])>12:lines.append(f"- Plus {len(data['players'])-12} additional player/role comparisons recorded.")
    if not data['players']:lines.append('- Ranked leaders do not exceed any recorded automatic player or Captain cap.')
    lines.append('- '+data['interpretation'])
    return lines
