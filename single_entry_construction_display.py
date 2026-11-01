"""Display captured per-contest construction shares; never infer outcomes."""


def render(detail):
    from single_entry_review import LABELS
    lines=['CONSTRUCTION SHARES WITHIN THIS CONTEST',
        'Each cell is feature-present entries / eligible entries in that finish group (percent). This is construction prevalence within a finish group, not the chance a construction wins.',
        'Field means ranked/resolved entries only. Top groups use captured rank cutoffs; ties and partial coverage can make observed group sizes differ from the named percentage. All rank-one ties remain entries, not distinct rosters. WR/TE Captain with own QB is restricted to WR/TE Captains. Other features can overlap.',
        'Small cells are descriptive counts, not recommendations. Missing/unresolved entries and unseen field entries remain unknown; no payout or return is inferred.']
    field=(detail or {}).get('field') or {}
    for metric,label in LABELS.items():
        sides=field.get(metric)
        valid=isinstance(sides,(list,tuple)) and len(sides)==2
        valid=valid and all(isinstance(side,(list,tuple)) and len(side)==4 and all(type(n) is int and n>=0 for n in side) and side[0]>=side[1]>=side[2]>=side[3] for side in sides)
        if not valid:
            lines.append(label+': unavailable in this capture.');continue
        cells=[]
        for i,title in enumerate(('Field','Top 1%','Top 0.1%','Rank one')):
            present=sides[1][i];denominator=sides[0][i]+present
            cell=f'{present:,}/{denominator:,} ({100*present/denominator:.1f}%)' if denominator else 'unavailable (0 eligible entries)'
            cells.append(title+' '+cell)
        lines.append(label+': '+' | '.join(cells))
    return '\n'.join(lines)
