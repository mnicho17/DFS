"""Describe specialist selection without modifying scores, ranks or limits."""
from collections import Counter
from optimizers import _position_tokens


def usage(rows):
    counts = Counter()
    rows = list(rows)
    for row in rows:
        captain, flex = row['Captain'], list(row['Flex'])
        counts['specialist_captains'] += bool(_position_tokens(captain) & {'K','DST'})
        counts['kicker_captains'] += 'K' in _position_tokens(captain)
        counts['defense_captains'] += 'DST' in _position_tokens(captain)
        n = sum(bool(_position_tokens(p) & {'K','DST'}) for p in [captain]+flex)
        counts['slots_'+str(n)] += 1
        counts['specialist_flex_slots'] += sum(bool(_position_tokens(p) & {'K','DST'}) for p in flex)
    return dict(lineups=len(rows), counts=dict(sorted(counts.items())))


def compare(candidates, selected):
    return dict(candidates=usage(candidates), selected=usage(selected),
                interpretation='Candidate-bank and selected usage are descriptive. A concentration difference alone does not identify a projection or SIM defect.')


def text(data):
    lines = ['Kicker/defense selection diagnostics:']
    for stage in ('candidates','selected'):
        row=data[stage]; n=row['lineups']; counts=row['counts']
        lines.append(f"- {stage}: {n} lineups; K Captains {counts.get('kicker_captains',0)}; DST Captains {counts.get('defense_captains',0)}; K/DST FLEX slots {counts.get('specialist_flex_slots',0)}.")
        lines.append('  Total K/DST slots per lineup: '+', '.join(f"{key[6:]}: {value}/{n}" for key,value in counts.items() if key.startswith('slots_')))
    lines.append(data['interpretation'])
    return lines
