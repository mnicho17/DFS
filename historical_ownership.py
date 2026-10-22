"""Bounded, opt-in ownership calibration with a construction-fit guard."""
import math
from copy import deepcopy
from historical_field import sample_history_field, distributions, CATEGORIES
from ownership_strategy import exposures
from optimizers import _pkey

MODEL='historical-construction-ownership-experiment-v1'
CONSTRUCTION_TOLERANCE=.02
MAX_PASSES=2


def _targets(pool):
    targets={}
    for slot,key,total in (('Captain','ProjCptOwnPct',100),('FLEX','ProjFlexOwnPct',500)):
        values=[]
        for p in pool:
            value=p.get(key)
            if p.get('OwnershipUnits')!='percent_of_entries' or isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=100:
                raise ValueError('Calibration requires verified finite percentage slot ownership.')
            targets[(slot,_pkey(p))]=value;values.append(value)
        if abs(sum(values)-total)>max(2,len(pool)*.051):
            raise ValueError('Calibration ownership totals do not match roster slots.')
    if not targets:raise ValueError('Calibration requires a nonempty frozen pool.')
    return targets


def _error(field,targets):
    actual=exposures(field,True)
    return sum(abs(actual.get(k,0)-v) for k,v in targets.items())/len(targets)


def _construction_error(field,priors,salary_cap):
    actual=distributions(field,salary_cap)
    return sum(.5*sum(abs(actual[c].get(k,0)-priors[c].get(k,0)/sum(priors[c].values()))
                      for k in actual[c].keys()|priors[c].keys()) for c in CATEGORIES)/len(CATEGORIES)


def sample_calibrated_history_field(pool,count,priors,*,salary_cap=50000,seed=0,cancelled=lambda:False):
    targets=_targets(pool)
    initial=sample_history_field(pool,count,priors,salary_cap=salary_cap,seed=seed,cancelled=cancelled)
    best=deepcopy(initial)
    info=dict(model=MODEL,status='incomplete',passes=0,construction_tolerance=CONSTRUCTION_TOLERANCE,
              targets=[dict(slot=k[0],player_key=k[1],pct=v) for k,v in sorted(targets.items())],trials=[])
    best.ownership_fit=info
    if cancelled() or len(initial)!=count or not initial:return best
    start=_error(initial,targets);score=start
    baseline=_construction_error(initial,priors,salary_cap)
    current=initial;weights={}
    for attempt in range(MAX_PASSES):
        if cancelled():break
        actual=exposures(current,True)
        for key,value in targets.items():
            ratio=(value+.2)/(actual.get(key,0)+.2)
            weights[key]=max(.1,min(10,weights.get(key,1)*max(.5,min(2,ratio**.6))))
        trial=sample_history_field(pool,count,priors,salary_cap=salary_cap,
            seed=seed+(attempt+1)*104729,cancelled=cancelled,draw_weights=weights)
        info['passes']+=1
        if cancelled() or len(trial)!=count:
            info['trials'].append(dict(pass_number=attempt+1,returned=len(trial),accepted=False,
                status='cancelled' if cancelled() else 'incomplete'))
            break
        current=trial;error=_error(trial,targets);construction=_construction_error(trial,priors,salary_cap)
        accepted=error<score and construction<=baseline+CONSTRUCTION_TOLERANCE
        info['trials'].append(dict(pass_number=attempt+1,returned=len(trial),mae_pp=error,construction_distance=construction,accepted=accepted,status='complete'))
        if accepted:best=deepcopy(trial);score=error
    info.update(status='cancelled' if cancelled() else 'completed',before_mae_pp=start,after_mae_pp=score,
                before_construction_distance=baseline,after_construction_distance=_construction_error(best,priors,salary_cap),
                note='At most two fresh legal fields; accept lower ownership MAE only within 0.02 mean construction distance of the original historical field. Targets may be infeasible; no ownership or lineup constraints are rewritten.')
    best.ownership_fit=info
    best.diagnostic['calibration_model']=MODEL
    return best
