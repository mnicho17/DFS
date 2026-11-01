"""Compact reading aid over captured Single Entry evidence; no source access."""


def render(report):
    from single_entry_review import summarize,MIN_GAMES
    from duplication_construction import summarize as duplication_summary
    contests=[c for c in report['contests'] if c['format']=='showdown' and c['preset']=='Single Entry']
    captured=[c for c in contests if isinstance(c.get('single_entry'),dict)]
    games=set();valid=[]
    for c in captured:
        slate=c.get('slate')
        if isinstance(slate,(list,tuple)) and len(slate)==3 and slate[0]=='showdown' and slate[1]==c['date'] and isinstance(slate[2],(list,tuple)) and len(slate[2])==1 and isinstance(slate[2][0],str) and slate[2][0]:
            games.add((c['date'],tuple(slate[2])));valid.append(c)
    lines=['SINGLE ENTRY EVIDENCE AT A GLANCE',f'Captured field evidence: {len(captured)}/{len(contests)} contests; {len(games)} distinct salary games.']
    if not captured:return '\n'.join(lines+['Unavailable: no captured Single Entry field evidence. Run a verified review; no source files were inspected to display this summary.'])
    if len(valid)!=len(captured):lines.append('Game identity unavailable for some captured contests; they cannot establish summary support.')
    def total(key):
        values=[c['single_entry'].get(key) for c in captured]
        return sum(values) if all(type(v) is int and v>=0 for v in values) else None
    totals=[total(k) for k in ('eligible','accepted','unknown_rosters','missing_ranks')]
    if None in totals:lines.append('Entry coverage totals unavailable in this older capture.')
    else:lines.append(f'Ranked/resolved entries: {totals[0]:,}/{totals[1]:,}; unresolved rosters {totals[2]:,}; missing ranks {totals[3]:,}. Exclusion counts can overlap.')
    selected=total('selected_users');eligible=total('cohort_eligible')
    if selected is None or eligible is None:lines.append('Earlier-cohort coverage unavailable in this older capture.')
    else:
        lines.append(f'Earlier-qualified entry observations: {eligible:,} eligible / {selected:,} selected.')
        if selected==0:lines.append('No earlier-qualified entrants were selected. Field construction and duplication details follow where captured.')
        elif eligible==0:lines.append('Selected earlier-qualified entries lack ranked/resolved coverage for cohort comparisons.')
    comparable=[c for c in valid if isinstance(c['single_entry'].get('field'),dict) and isinstance(c['single_entry'].get('cohort'),dict)]
    finishes=summarize(dict(report,contests=comparable),10)
    copies=duplication_summary(dict(report,contests=valid),10)
    for label,rows in (('Finish/usage (10 entries per side)',finishes),('Duplication (10 distinct rosters per side)',copies)):
        count=sum(r['games']>=MIN_GAMES for r in rows)
        lines.append(f'{label}: {count}/{len(rows)} comparisons reach {MIN_GAMES} supported games; maximum observed support {max((r["games"] for r in rows),default=0)} games.')
    states={}
    for c in captured:
        state=(c['single_entry'].get('cash_evidence') or {}).get('state','not_captured')
        if state not in ('missing_columns','ambiguous_columns','columns_present_values_unverified','unavailable','not_captured'):state='unavailable'
        states[state]=states.get(state,0)+1
    lines.append('Payout evidence by contest: '+', '.join(f'{label} {states.get(state,0)}' for state,label in (('missing_columns','missing columns'),('ambiguous_columns','ambiguous columns'),('columns_present_values_unverified','columns present, values unverified'),('unavailable','unavailable'),('not_captured','not captured')))+'. No returns calculated.')
    lines.append('Game/support counts are descriptive, not a significance test or a rule recommendation. Field-size scopes and metric-specific support remain separate in the details. Settings are unchanged.')
    return '\n'.join(lines)
