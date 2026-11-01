"""Qualified, read-only receiver/QB and specialist-count historical comparisons."""
from construction_outcomes import contest_outcomes as collect, _game_summary, ENDPOINTS, SUPPORT_MINIMUMS
import analysis_imports as ai

METRICS=('receiver_captain_same_qb','kd_one_vs_zero','kd_two_vs_zero','kd_three_plus_vs_zero')
LABELS=dict(zip(METRICS,('WR/TE Captain with same-team QB versus without',
    'One K/DST versus zero','Two K/DST versus zero','Three or more K/DST versus zero')))


def features(slots):
    captain=next(p for _,role,p in slots if role=='CPT')
    paired=(int(any(role=='FLEX' and p['position']=='QB' and p['team']==captain['team'] for _,role,p in slots))
            if captain['position'] in ('WR','TE') else None)
    kd=sum(p['position'] in ('K','DST') for _,_,p in slots)
    return (paired,*(0 if kd==0 else 1 if (kd==n if n<3 else kd>=3) else None for n in (1,2,3)))


def contest_outcomes(conn,key,players,accepted,field_size,user,cancelled):
    from winner_size_matching import SizeMatchedObserver
    observer=SizeMatchedObserver(conn,key,cancelled)
    result=collect(conn,key,players,accepted,field_size,user,cancelled,_features=features,
                   _metrics=METRICS,_rule_metrics=(),_observed=True,_observer=observer)
    result['winner_portfolios']=observer.finish(accepted)
    return result


def summarize(contests):
    result={}
    for metric in METRICS:
        row=_game_summary(contests,metric,'matched')
        row['support_checks']={str(n):_game_summary(contests,metric,'matched',n) for n in SUPPORT_MINIMUMS}
        counts=[[sum(c['observed'][metric][side][i] for c in contests) for i in range(4)] for side in (0,1)]
        row['observed']=counts
        result[metric]=row
    return result


def review(db_path,cancelled=lambda:False,progress=lambda text:None,*,preset=''):
    from historical_series_review import _inventory
    from showdown_group_review import review_history
    before=_inventory(db_path)
    winner_entries=[0];peer_entries=[0]
    def bounded_collect(*args):
        from winner_pairing_review import MAX_ENTRIES
        from portfolio_pattern_audit import PatternAuditLimit
        value=contest_outcomes(*args)
        winner_entries[0]+=sum(row['entries'] for row in value['winner_portfolios']['users'])
        if winner_entries[0]>MAX_ENTRIES:
            raise PatternAuditLimit('Whole-review winner portfolio entry bound reached; no partial report published.')
        from winner_size_matching import MAX_REVIEW_PEER_ENTRIES
        peer_entries[0]+=value['winner_portfolios']['peer_entries']
        if peer_entries[0]>MAX_REVIEW_PEER_ENTRIES:
            raise PatternAuditLimit('Whole-review peer entry bound reached; no partial report published.')
        return value
    result=review_history(db_path,cancelled,progress,preset=preset,_outcomes=True,
                          _collector=bounded_collect,_aggregate=summarize)
    if _inventory(db_path)!=before:raise ValueError('Historical index changed during pairing review; rerun.')
    ai._check(cancelled)
    from winner_pairing_review import summarize as portfolios
    result['winner_portfolios_by_preset']={p:portfolios([c for c in result['contests'] if c['preset']==p])
                                          for p in result['by_preset']}
    from winner_size_matching import summarize as matched,SUPPORT
    result['size_matched_by_preset']={p:{str(n):matched([c for c in result['contests'] if c['preset']==p],n) for n in SUPPORT}
                                      for p in result['by_preset']}
    return result


def render(report):
    lines=['RECEIVER CAPTAIN / QB AND K/DST HISTORICAL REVIEW',
        f"Verified contests: {len(report['contests'])}; excluded: {len(report['excluded'])}.",
        'Same contest, exact Captain identity and salary band. Receiver comparisons include WR/TE Captains only.',
        'K/DST count includes Captain and FLEX. Each nonzero count is compared separately against zero; other counts are excluded from that comparison.',
        'K/DST Captains cannot have a zero-specialist baseline. Matched count contrasts therefore exclude those Captain strata; pooled usage still includes them.',
        'Other construction choices are not held fixed. Entry-weighted usage is separate from the equal-game matched comparisons.',
        'Finish rates use ranked, resolved entries only. Missing ranks and unresolved identities are not losses.',
        'Top 1%/5% use supplied field-size cutoffs when available, otherwise observed accepted entries. Complete field is unverified; rank-one ties all count.',
        'Overlap weighting n0*n1/(n0+n1), contests averaged within games, games equally weighted. Fixed support checks require 5 and 10 entries per side in each stratum.',
        'Descriptive associations, not causal effects, ROI, independence or evidence to change defaults. Winners’ full portfolios are not measured by these rank-one-entry rates.']
    for preset,summary in report['by_preset'].items():
        lines+=['',preset or 'Unknown entry limit']
        categories=[summary[METRICS[1]]['observed'][0],*(summary[m]['observed'][1] for m in METRICS[1:])]
        total=sum(v[0] for v in categories)
        lines.append('  Pooled ranked/resolved K/DST counts (all Captain positions):')
        for name,counts in zip(('Zero','One','Two','Three or more'),categories):
            share=100*counts[0]/total if total else None
            lines.append(f'    {name}: {counts[0]:,}; '+(f'{share:.2f}% of {total:,}' if share is not None else 'unavailable'))
        for metric,row in summary.items():
            lines.append(LABELS[metric])
            for side,label in ((0,'Baseline'),(1,'Comparison')):
                counts=row['observed'][side]
                rates=', '.join(f'{e}: {100*counts[i]/counts[0]:.3f}% ({counts[i]:,})' if counts[0] else e+': unavailable' for i,e in enumerate(ENDPOINTS,1))
                lines.append(f'  {label}: {counts[0]:,} ranked resolved entries; '+rates)
            for n,detail in row['support_checks'].items():
                lines.append(f"  {n} entries per side: {detail['games']} supported games")
                for e,v in detail['endpoints'].items():
                    gap=v['difference_pp'];text='unavailable (no common support)' if gap is None else f'{gap:+.3f} pp'
                    lines.append(f"    {e}: {text}; higher/lower/equal games {v['higher_games']}/{v['lower_games']}/{v['equal_games']}; omit-one {v['omit_one_range']}")
    lines+=['','Coverage:']
    for c in report['contests']:
        lines.append(f"{c['name']}: {c['accepted']:,} indexed entries; {c['unknown']:,} unresolved; {c['missing_rank']:,} missing rank; {c['rank_basis']}")
    lines+=['Excluded: '+c['name']+': '+c['reason'] for c in report['excluded']]
    from winner_pairing_review import render as render_portfolios
    from winner_size_matching import render as render_matching
    return '\n'.join(lines)+render_portfolios(report)+render_matching(report)
