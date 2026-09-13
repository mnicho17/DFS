"""Paired diagnostic selection, evaluated on a separate scenario stream."""
import copy
import math
from statistics import mean
from ownership_sensitivity import load_sensitivity_bank
from repeatability import candidates, identity, model_version
from portfolio_rules import select_portfolio
from entry_review import review_entries


def compare_portfolios(path, requested=150, scenarios=2000, cancelled=lambda:False,
                       progress=lambda text:None, simulate=None):
    if not 1 <= requested <= 150 or scenarios not in (2000,5000,10000):
        raise ValueError('Choose 1–150 entries and 2,000, 5,000 or 10,000 scenarios.')
    bank=load_sensitivity_bank(path);p=bank['payload'];kind=p['kind']
    original=candidates(p);keys=[identity(lu,kind) for lu in original]
    if len(set(keys))!=len(keys) or len(keys)<=requested:
        raise ValueError('Choose a bank containing more distinct candidates than requested entries.')
    def score(seed,phase):
        if cancelled():raise ValueError('Cancelled; no comparison published.')
        kwargs=dict(scenarios=scenarios,field_lineup_count=p['field_count'],salary_cap=p['salary_cap'],
                    seed=seed,cancel_callback=cancelled,
                    progress_callback=lambda a,b,c:progress(f'{phase}: {a:,}/{b:,} — {c}'))
        if simulate:result=simulate(candidates(p),copy.deepcopy(p['players']),**kwargs)
        elif kind=='classic':
            from nfl_simulation import simulate_nfl_contest
            result=simulate_nfl_contest(candidates(p),copy.deepcopy(p['players']),field_config=copy.deepcopy(p['field_config']),**kwargs)
        else:
            from showdown_simulation import simulate_showdown
            result=simulate_showdown(candidates(p),copy.deepcopy(p['players']),**kwargs)
        scored=result.get('lineups') or []
        actual=[identity(lu,kind) for lu in scored]
        if cancelled() or result.get('report',{}).get('scenarios')!=scenarios:
            raise ValueError('Incomplete simulation; no comparison published.')
        if len(actual)!=len(keys) or set(actual)!=set(keys):raise ValueError('Candidate identities changed.')
        for lu in scored:
            m=getattr(lu,'sim_metrics',{})
            if m.get('sim_scenarios')!=scenarios:raise ValueError('Incomplete candidate scores.')
            if not hasattr(lu,'sim_top_hits'):raise ValueError('Scenario coverage is unavailable.')
            for name in ('sim_top_one_pct','sim_mean'):
                if name=='sim_mean' and name not in m:continue
                if not math.isfinite(float(m.get(name,float('nan')))):raise ValueError('Invalid candidate metrics.')
        return scored
    training=score(910021,'Selection scenarios')
    selected={};warnings={}
    # Banks lack original group/team/game settings. Use disclosed identical rules;
    # embedded player maximums/minimums remain supported by the shared selector.
    rules={'min_unique':2,'balance_ownership':True}
    for name,penalty in (('Current selector',0.0),('Core-aware trial',6.0)):
        if cancelled():raise ValueError('Cancelled; no comparison published.')
        progress('Selecting: '+name)
        result=select_portfolio(training,requested,kind=kind,rules=rules,core_penalty=penalty,selection_cancel_callback=cancelled)
        selected[name]=result['lineups'];warnings[name]=result['report'].get('warnings',[])
    heldout=score(1910021,'Independent evaluation')
    lookup={identity(lu,kind):lu for lu in heldout};rows=[]
    for name,entries in selected.items():
        tested=[lookup[identity(lu,kind)] for lu in entries]
        review=review_entries(entries,kind,p['salary_cap'],name)
        hits=set().union(*(set(lu.sim_top_hits) for lu in tested))
        pairs=review['tables']['Pairs'];trios=review['tables']['Trios']
        rows.append(dict(name=name,selected=len(entries),
            selection_mean_top1=mean(lu.sim_metrics['sim_top_one_pct'] for lu in entries) if entries else None,
            evaluation_mean_top1=mean(lu.sim_metrics['sim_top_one_pct'] for lu in tested) if tested else None,
            evaluation_coverage=100*len(hits)/scenarios,
            highest_pair_pct=max((r['pct'] for r in pairs),default=0),
            highest_trio_pct=max((r['pct'] for r in trios),default=0),
            identities=[identity(lu,kind) for lu in entries],warnings=warnings[name],review=review))
    overlap=len(set(map(tuple,rows[0]['identities'])) & set(map(tuple,rows[1]['identities'])))
    return dict(status='completed',bank_id=bank['bank_id'],input_id=p['input_id'],kind=kind,
        model_version=model_version(),version='core-comparison-v1',requested=requested,scenarios=scenarios,
        field_count=p['field_count'],seeds=[910021,1910021],rules=rules,core_penalty=6.0,
        overlap=overlap,rows=rows)


def format_report(r):
    lines=['DFS Portfolio Comparison',f"{r['kind'].title()} | requested {r['requested']} | bank {r['bank_id']}",
        f"Two passes of {r['scenarios']:,} scenarios; requested opponents {r['field_count']:,} per pass. Shared candidate bank, selection stream and independent evaluation stream.",
        'Diagnostic only: original outputs, forecasts and rules are unchanged. This does not reproduce a prior build: original group/team/game rules are not stored in banks. Both trials use minimum unique 2, ownership balance on, embedded player limits, and no refinement; standard Showdown guardrails apply.',
        'Core-aware trial adds a bounded 6-point repeated-pair/trio penalty to the existing selection score. Athlete cores ignore Captain assignment; existing Captain safeguards remain. This experimental weight is not calibrated.',
        'Evaluation scenarios do not choose lineups. Results measure this model and one held-out stream, not historical accuracy, profit or guaranteed improvement. Repeating uses the same seeds.',
        f"Shared selected rosters: {r['overlap']}."]
    for row in r['rows']:
        lines+=['',row['name'],f"Selected: {row['selected']}/{r['requested']}"]
        if row['selected']:
            lines += [f"Average individual top-1%: selection {row['selection_mean_top1']:.3f}%; independent evaluation {row['evaluation_mean_top1']:.3f}%.",
                      f"Independent scenarios with at least one top-1% entry: {row['evaluation_coverage']:.2f}%.",
                      f"Most repeated pair: {row['highest_pair_pct']:.1f}%; trio: {row['highest_trio_pct']:.1f}%."]
        lines += ['Warning: '+str(w) for w in row['warnings']]
        lines += [row['review']['text']]
    return '\n'.join(lines)
