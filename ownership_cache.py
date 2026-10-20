"""Data-only complete ownership estimates; never historical/SIM evidence."""
import copy
import json
import math
import os
from pathlib import Path
import re
import tempfile
from build_snapshots import fingerprint
from model_compatibility import model_id

MAX_BYTES=16*1024**2
MAX_ENTRIES=128
OBSERVATIONS=frozenset(('NFLUsageCheckedAt','NFLUsageFetchedAt','LiveStatusUpdatedAt',
    'NFLVegasUpdatedAt','NFLNewsUpdatedAt','LiveStatusChanged'))
SHOWDOWN_OUTPUTS=frozenset(('ProjOwnPct','ProjCptOwnPct','ProjFlexOwnPct','OwnershipSource',
    'OwnershipUnits','OwnershipModel','OwnershipSampleSize','NFLFieldEligible',
    '_FieldCptWeight','_FieldFlexWeight'))


def context(worker):
    rows=copy.deepcopy(worker.players)
    for player in rows:
        for key in OBSERVATIONS:player.pop(key,None)
        history=player.get('NFLUsageHistory')
        if isinstance(history,dict):history.pop('checked_at',None)
        if worker.mode=='showdown':
            for key in SHOWDOWN_OUTPUTS:player.pop(key,None)
    known={'players','mode','sport','num_sims','salary_cap','template_sim',
           '_cancel','_stopped','cache_report','reuse_prepared'}
    extra={k:v for k,v in vars(worker).items() if k not in known}
    return dict(schema=1,model=model_id('ownership'),players=rows,extra=extra,mode=worker.mode,
        sport=worker.sport,count=worker.num_sims,cap=worker.salary_cap,
        # Both legacy NFL Showdown template switches use the same shared estimator.
        template=False if worker.mode=='showdown' else worker.template_sim)


def pack(value):
    if isinstance(value,dict):return {'map':[[pack(k),pack(v)] for k,v in value.items()]}
    if isinstance(value,tuple):return {'tuple':[pack(v) for v in value]}
    if isinstance(value,list):return [pack(v) for v in value]
    if value is None or isinstance(value,(str,bool,int)):return value
    if isinstance(value,float) and math.isfinite(value):return value
    raise ValueError('Unsupported or non-finite ownership payload.')


def unpack(value):
    if isinstance(value,list):return [unpack(v) for v in value]
    if isinstance(value,dict):
        if set(value)=={'tuple'}:return tuple(unpack(v) for v in value['tuple'])
        if set(value)=={'map'}:
            pairs=[(unpack(k),unpack(v)) for k,v in value['map']]
            result=dict(pairs)
            if len(result)!=len(pairs):raise ValueError('Duplicate ownership payload keys.')
            return result
        raise ValueError('Unsupported ownership encoding.')
    return value


def validate(result,ctx):
    meta=result.get('meta') or {}
    if any(type(meta.get(k)) is not int or meta[k]!=ctx['count'] for k in ('valid_lineups','requested_lineups')):
        raise ValueError('Only a complete requested ownership sample is reusable.')
    keys=meta.get('eligible_keys')
    if not isinstance(keys,list) or len(set(keys))!=len(keys) or not keys:
        raise ValueError('Ownership role-pool identity is incomplete.')
    from optimizers import _pkey
    allowed={_pkey(p) for p in ctx['players']}
    if not set(keys).issubset(allowed):raise ValueError('Ownership contains unknown players.')
    for column in ('total','cpt','flex'):
        values=result.get(column)
        if not isinstance(values,dict) or not set(values).issubset(set(keys)):
            raise ValueError('Invalid ownership player mapping.')
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=100 for v in values.values()):
            raise ValueError('Invalid ownership percentages.')
    if ctx['mode']=='showdown':
        if any(abs(sum(result[k].values())-target)>1e-5 for k,target in (('cpt',100),('flex',500),('total',600))):
            raise ValueError('Incomplete Showdown slot totals.')
        if any(abs(result['total'].get(k,0)-result['cpt'].get(k,0)-result['flex'].get(k,0))>1e-6 for k in keys):
            raise ValueError('Inconsistent Showdown ownership totals.')
    elif abs(sum(result['total'].values())-900)>1e-5 or result['total']!=result['flex'] or result['cpt']:
        raise ValueError('Incomplete Classic slot totals.')
    return result


def folder():
    from learning_db import history_db_path
    return Path(history_db_path()).parent/'ownership-cache'


def estimate(worker, simulate, *, reuse=True, root=None):
    """Keep fresh estimator behavior unchanged if reuse/storage cannot qualify."""
    report=dict(status='refreshed',reason='No matching completed estimate.')
    if worker.sport!='NFL' or worker.mode not in ('classic','showdown'):
        return simulate(),dict(status='refreshed',reason='Reuse supports NFL estimates.')
    try:
        ctx=context(worker);encoded_context=pack(ctx);identity=fingerprint(encoded_context);directory=Path(root) if root else folder()
        path=directory/(identity+'.json')
        if reuse and path.is_file() and not path.is_symlink():
            if path.stat().st_size>MAX_BYTES:raise ValueError('Ownership cache exceeds size limit.')
            with path.open('r',encoding='utf-8') as handle:raw=handle.read(MAX_BYTES+1)
            if len(raw)>MAX_BYTES:raise ValueError('Ownership cache exceeds size limit.')
            saved=json.loads(raw)
            if saved['context']!=encoded_context or saved['identity']!=identity or fingerprint(saved['context'])!=identity:
                raise ValueError('Ownership input identity mismatch.')
            if fingerprint(saved['result'])!=saved['result_id']:raise ValueError('Ownership payload digest mismatch.')
            result=validate(unpack(saved['result']),ctx)
            if not worker._cancel:return result,dict(status='reused',reason='Completed estimate matches refreshed inputs and settings.')
    except Exception:
        report['reason']='Saved ownership unavailable or invalid; recalculated.'
    if worker._cancel:return {},dict(status='cancelled',reason='Ownership estimation cancelled.')
    result=simulate()
    if worker._cancel:return result,dict(status='cancelled',reason='Ownership estimation cancelled.')
    try:
        # Recompute context only if no qualification context could be built.
        if 'ctx' not in locals():ctx=context(worker);encoded_context=pack(ctx);identity=fingerprint(encoded_context);directory=Path(root) if root else folder();path=directory/(identity+'.json')
        validate(result,ctx);payload=pack(result)
        value=dict(context=encoded_context,identity=identity,result=payload,result_id=fingerprint(payload))
        raw=json.dumps(value,allow_nan=False)
        if len(raw.encode('utf-8'))>MAX_BYTES:raise ValueError('Ownership cache exceeds size limit.')
        directory.mkdir(parents=True,exist_ok=True)
        fd,temp=tempfile.mkstemp(dir=directory,suffix='.tmp')
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as handle:handle.write(raw)
            if not worker._cancel:os.replace(temp,path)
        finally:
            if os.path.exists(temp):os.unlink(temp)
        candidates=sorted((p for p in directory.glob('*.json') if re.fullmatch(r'[0-9a-f]{64}\.json',p.name) and not p.is_symlink()),key=lambda p:p.stat().st_mtime_ns)
        for old in candidates[:-MAX_ENTRIES]:
            if old.resolve().parent==directory.resolve():old.unlink()
    except Exception:
        report['stored']=False
    else:report['stored']=True
    return result,report
