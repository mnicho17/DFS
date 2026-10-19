"""Bounded preparation for discovered slates, detached from the current window.

Receipts are scheduling metadata. Existing library loaders still validate reuse.
"""
import datetime as dt
import hashlib
import json
from pathlib import Path
import os
import tempfile
import time
from build_snapshots import create_snapshot, fingerprint

TARGET = 12000
BUDGET = 8*1024**3
CHUNK_SECONDS = 60


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, allow_nan=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def load(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}


def source_digest(path, cancelled):
    digest=hashlib.sha256()
    with open(path,'rb') as source:
        while True:
            if cancelled(): raise InterruptedError('Preparation yielded.')
            block=source.read(1024*1024)
            if not block: return digest.hexdigest()
            digest.update(block)


def prepare_one(catalog, folder, output, cancelled, progress, *, now=None, seconds=CHUNK_SECONDS):
    from slate_catalog import rows
    from candidate_library import code_id, run_search, metadata
    from csv_loading import CsvLoadWorker
    from main_window import OwnershipSimWorker
    from nfl_pipeline import apply_ownership
    from nfl_workload import available
    today=now or dt.date.today()
    output=Path(output)
    deadline=today+dt.timedelta(days=7)
    candidates=[row for row in rows(catalog,folder,today=today.isoformat())
                if row['sport']=='NFL' and row['date']<=deadline.isoformat()]
    model=code_id()
    output.mkdir(parents=True,exist_ok=True)
    receipts=output/'discovered'
    for row in candidates:
        if cancelled():return dict(cancelled=True)
        identity=fingerprint(dict(source=row['digest'],code=model,policy=1))
        receipt=receipts/(identity+'.json')
        state=load(receipt)
        path=output/('auto-discovered-'+identity+'.dfslib')
        if state.get('state') in ('Prepared candidates','Needs attention','Storage limit'):
            if state.get('state')!='Prepared candidates':continue
            if path.exists():
                try:
                    if metadata(path)['count']>=TARGET:continue
                    raise ValueError('Prepared library no longer contains its recorded target.')
                except Exception as exc:
                    state.update(state='Needs attention',message=str(exc))
                    save(receipt,state)
                    return dict(error=str(exc),state='Needs attention',digest=row['digest'])
            state={}
        # Shared budget includes automatic and manual candidate libraries. Never
        # delete or overwrite another slate to make room for this job.
        def used():
            return sum(p.stat().st_size for p in output.rglob('*') if p.is_file())
        if used()>=BUDGET:
            state.update(state='Storage limit',message='Shared 8 GiB candidate-folder budget reached.')
            save(receipt,state)
            return dict(preparation=state['message'],state=state['state'],digest=row['digest'])
        started=time.monotonic()
        try:
            if source_digest(row['path'],cancelled)!=row['digest']:
                return dict(preparation='Salary file changed; waiting for discovery to refresh it.')
            progress('Smart preparation: '+row['label'])
            # Capture and retain detached inputs for exact resumability. Refresh
            # on future loads remains authoritative for current forecasts.
            if not state.get('snapshot'):
                loader=CsvLoadWorker(row['path']);loader.stop=_EventProxy(cancelled)
                payload=[];loader.finished.connect(payload.append);loader.run()
                if cancelled():raise InterruptedError('Preparation yielded.')
                if not payload or payload[0].get('failed'):
                    raise ValueError((payload or [{}])[0].get('error','Salary preparation failed.'))
                players=payload[0]['players']
                for player in players:
                    if not available(player):player.update(FadeFlex=True,FadeCpt=True)
                class DetachedOwnership(OwnershipSimWorker):
                    @property
                    def _cancel(self):return cancelled() or getattr(self,'_stopped',False)
                    @_cancel.setter
                    def _cancel(self,value):self._stopped=value
                progress('Smart preparation: ownership simulation (500 entries)')
                ownership=DetachedOwnership(players,mode=row['kind'],num_sims=500,salary_cap=50000,sport='NFL')
                result=ownership._simulate()
                if cancelled():raise InterruptedError('Preparation yielded.')
                if not result.get('total') or sum(result['total'].values())<=0:
                    raise ValueError('Ownership simulation produced no valid entries.')
                apply_ownership(players,result)
                snapshot=create_snapshot(players,dict(sport='NFL',contest_kind=row['kind'],salary_cap=50000,
                    salary_strategy='Near Cap',requested_lineups=150,ownership_mode='Balanced',ownership_weight=0,
                    classic_coverage_expansion=True),{},freshness=payload[0].get('live_summary'))
                state=dict(state='Preparing candidates',snapshot=snapshot,ownership=result,source=row['digest'],
                           message='Ownership captured; preparing a bounded candidate library.')
                if source_digest(row['path'],cancelled)!=row['digest']:
                    return dict(preparation='Salary file changed during preparation; captured work discarded.')
                save(receipt,state)
            last_check=[0.0];over=[False]
            def stop():
                if cancelled():return True
                if time.monotonic()-last_check[0]>=5:
                    last_check[0]=time.monotonic();over[0]=used()>=BUDGET
                return over[0]
            count=run_search(path,state['snapshot'],seconds=seconds,candidate_limit=TARGET,
                             cancelled=stop,progress=progress,batch_size=100)
            if cancelled():status='Queued'
            elif over[0]:status='Storage limit'
            elif count>=TARGET:status='Prepared candidates'
            else:status='Queued'
            stagnant=state.get('stagnant',0)+1 if count==state.get('count') else 0
            spent=state.get('seconds',0)+(time.monotonic()-started)
            if status=='Queued' and not cancelled() and (stagnant>=3 or spent>=1800):
                status='Needs attention'
            state.update(stagnant=stagnant,seconds=spent)
            state.update(state=status,count=count,path=str(path),
                message=f'{count:,}/{TARGET:,} candidates; final SIM and current-input validation run when building.')
            if status=='Needs attention':
                state['message']+=' Preparation paused: '+('30-minute allowance reached.' if spent>=1800 else 'three batches added no candidates.')
            elif status=='Storage limit':state['message']+=' Preparation paused: shared storage budget reached.'
            save(receipt,state)
            return dict(preparation=state['message'],library=str(path),state=status,digest=row['digest'],count=count)
        except InterruptedError:
            return dict(cancelled=True)
        except Exception as exc:
            state.update(state='Needs attention',message=str(exc))
            save(receipt,state)
            return dict(error='Smart preparation: '+str(exc),state='Needs attention',digest=row['digest'])
    return dict(preparation='Upcoming discovered slates have no pending automatic work.')


class _EventProxy:
    def __init__(self,callback):self.callback=callback
    def is_set(self):return self.callback()
