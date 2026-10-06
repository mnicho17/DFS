"""Frozen diagnostic candidate bank; current SIM code on recorded inputs."""
from copy import deepcopy
from statistics import mean
import analysis_imports as ai
from review_build_evidence import digest
from showdown_field import sample_field
from showdown_simulation import showdown_signature, simulate_showdown
from ownership_strategy import exposures
from optimizers import _pkey


def ownership_drift(field,pool):
    actual=exposures(field,True)
    return mean(abs(actual.get((slot,_pkey(p)),0)-p[key]) for p in pool
                for slot,key in (('Captain','ProjCptOwnPct'),('FLEX','ProjFlexOwnPct'))) if field else None


def freeze_bank(pool,count,cancelled):
    proposals=sample_field(pool,count*4,seed=7109,cancel_callback=cancelled)
    ai._check(cancelled)
    unique={}
    for row in proposals:unique.setdefault(showdown_signature(row),row)
    return list(unique.values())[:count]


def compare_sim(bank,pool,current,historical,seed,scenarios,cancelled,calibrated=None):
    ai._check(cancelled)
    if len(bank)<20:return dict(status='unavailable',reason='Fewer than 20 distinct diagnostic candidates; no ranking comparison.')
    models={}
    fields=dict(current=current,historical=historical)
    if calibrated is not None:fields['calibrated']=calibrated
    for name,field in fields.items():
        run=simulate_showdown(deepcopy(bank),deepcopy(pool),scenarios=scenarios,field_lineup_count=len(field),
            seed=seed,diagnostic_field=field,cancel_callback=cancelled,scenario_cache=False)
        ai._check(cancelled)
        if run['report']['scenarios']!=scenarios:raise ValueError('Incomplete SIM comparison was withheld.')
        models[name]=run['lineups']
    key=showdown_signature
    moments={name:{key(lu):(lu.sim_metrics['sim_mean'],lu.sim_metrics['sim_ceiling']) for lu in rows}
             for name,rows in models.items()}
    if any(value!=moments['current'] for value in moments.values()):
        raise ValueError('Shared-outcome comparison changed candidate scoring distributions; report withheld.')
    top=min(20,len(bank));leaders={};result={}
    for name,rows in models.items():
        ordered=sorted(rows,key=lambda lu:(-lu.sim_metrics['sim_top_one_pct'],key(lu)))
        leaders[name]={key(lu) for lu in ordered[:top]}
        result[name]=dict(top_n=top,top_n_mean_sim_top_one_pct=mean(lu.sim_metrics['sim_top_one_pct'] for lu in ordered[:top]),
            field_ownership_drift_pp=ownership_drift(fields[name],pool))
    return dict(status='complete',candidate_count=len(bank),candidate_digest=digest(sorted(key(lu) for lu in bank)),
        candidate_generation_seed=7109,scenario_seed=seed,scenarios=scenarios,outcome_moments_identical=True,
        top_n_overlap_pct=100*len(leaders['current']&leaders['historical'])/top,models=result,
        calibrated_overlap_pct=100*len(leaders['current']&leaders['calibrated'])/top if calibrated is not None else None,
        note='Same independently frozen diagnostic bank and scenario seed, ranked by simulated top-1% rate with stable roster tie-breaking. Not the original build shortlist or a realized-return evaluation.')
