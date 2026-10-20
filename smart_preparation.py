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
ATTEMPT_SECONDS = 1800


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


def summary(state, digest, path):
    """Small persistent UI record; never expose a snapshot/ownership bank to widgets."""
    keys=('state','count','target','seconds','total_seconds','stagnant','attempt',
          'message','pause_reason','pause_code','updated_at','source_path','captured_at')
    result={key:state[key] for key in keys if key in state}
    result.update(digest=digest,library=str(path),library_saved=path.is_file(),preparation=state.get('message',''),
                  ownership_complete=bool(state.get('snapshot') and state.get('ownership')))
    if state.get('pause_code')=='error':result['error']=state.get('pause_reason',state.get('message',''))
    return result


def prepare_one(catalog, folder, output, cancelled, progress, *, now=None, seconds=CHUNK_SECONDS,
                target_digest=None, retry=False, pause_reason=lambda:'Waiting for idle time or higher-priority work.'):
    from slate_catalog import rows, record_preparation
    from candidate_library import code_id, run_search, metadata
    from csv_loading import CsvLoadWorker
    from main_window import OwnershipSimWorker
    from nfl_pipeline import apply_ownership
    from nfl_workload import available
    today=now or dt.date.today()
    output=Path(output)
    deadline=today+dt.timedelta(days=7)
    candidates=rows(catalog,folder,today=today.isoformat(),all_dates=bool(target_digest))
    candidates=[row for row in candidates if row['sport']=='NFL' and
                (row['digest']==target_digest if target_digest else row['date']<=deadline.isoformat())]
    if target_digest and not candidates:
        result=dict(digest=target_digest,state='Needs attention',pause_code='source_changed',
            pause_reason='Selected salary file changed or is no longer in the configured folder. Refresh the list and select its current record.',
            preparation='Selected salary file is no longer available under that identity.')
        record_preparation(catalog,result)
        return result
    model=code_id()
    output.mkdir(parents=True,exist_ok=True)
    receipts=output/'discovered'
    for row in candidates:
        if cancelled():return dict(cancelled=True)
        identity=fingerprint(dict(source=row['digest'],code=model,policy=1))
        receipt=receipts/(identity+'.json')
        path=output/('auto-discovered-'+identity+'.dfslib')
        try:
            state=load(receipt)
            if not isinstance(state,dict):raise ValueError('Preparation receipt is invalid.')
        except (OSError,ValueError) as exc:
            result=dict(digest=row['digest'],state='Needs attention',pause_code='error',error=str(exc),
                pause_reason='Preparation receipt could not be read; saved files were retained. '+str(exc))
            record_preparation(catalog,result)
            return result
        if state.get('state') in ('Needs attention','Storage limit') and not retry:
            continue
        state.setdefault('target',TARGET)
        state['source_path']=row['path']
        state.setdefault('total_seconds',state.get('seconds',0))
        state.setdefault('attempt',1)
        started=None
        retry_consumed=False
        def publish(status, code, reason, *, message=None):
            state.update(state=status,pause_code=code,pause_reason=reason if code!='none' else '',
                message=message or reason,updated_at=dt.datetime.now().astimezone().isoformat())
            save(receipt,state)
            result=summary(state,row['digest'],path)
            result['retry_consumed']=retry_consumed
            record_preparation(catalog,result)
            return result
        def used():
            return sum(p.stat().st_size for p in output.rglob('*') if p.is_file())
        try:
            if source_digest(row['path'],cancelled)!=row['digest']:
                return publish('Needs attention','source_changed','Salary file changed; refresh the list and select its current record. Saved work was retained.')
            if state.get('state')=='Prepared candidates' and path.exists():
                info=metadata(path)
                if (info['count']<TARGET or info.get('code_id')!=model or
                        info.get('input_id')!=(state.get('snapshot') or {}).get('input_id')):
                    raise ValueError('Prepared library no longer matches its captured inputs, target, or app version.')
                if target_digest:return publish('Prepared candidates','none','Candidate target already reached; current-input validation still runs when building.')
                continue
            if used()>=BUDGET:
                return publish('Storage limit','storage','Shared 8 GiB candidate-folder budget reached. Free space in that folder, then Retry/Resume. No saved files were deleted.')
            if retry:
                state.update(seconds=0,stagnant=0,attempt=state['attempt']+1)
                retry_consumed=True
            if state.get('seconds',0)>=ATTEMPT_SECONDS:
                return publish('Needs attention','time_budget','Preparation paused: 30-minute attempt allowance reached. Retry/Resume grants another bounded attempt, retaining compatible saved work.')
            started=time.monotonic()
            progress('Smart preparation: '+row['label'])
            if not state.get('snapshot'):
                publish('Preparing inputs','none','Refreshing detached slate inputs; current loaded slate is unchanged.')
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
                publish('Preparing ownership','none','Running the existing 500-entry ownership estimator.')
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
                if source_digest(row['path'],cancelled)!=row['digest']:
                    return publish('Needs attention','source_changed','Salary file changed during preparation; refresh the list. Incomplete captured work was not saved.')
                state.update(snapshot=snapshot,ownership=result,source=row['digest'],captured_at=snapshot['created_at'])
            publish('Preparing candidates','none','Ownership captured; preparing a bounded candidate library.')
            last_check=[0.0];over=[False]
            remaining=max(0,ATTEMPT_SECONDS-state.get('seconds',0)-(time.monotonic()-started))
            if remaining<=0:
                raise TimeoutError('30-minute attempt allowance reached while preparing inputs.')
            def stop():
                if cancelled():return True
                if time.monotonic()-last_check[0]>=5:
                    last_check[0]=time.monotonic();over[0]=used()>=BUDGET
                return over[0]
            count=run_search(path,state['snapshot'],seconds=min(seconds,remaining),candidate_limit=TARGET,
                             cancelled=stop,progress=progress,batch_size=100)
            stagnant=(state.get('stagnant',0)+(0 if cancelled() or over[0] else 1)
                      if count==state.get('count') else 0)
            elapsed=time.monotonic()-started
            state.update(count=count,stagnant=stagnant,seconds=state.get('seconds',0)+elapsed,
                         total_seconds=state['total_seconds']+elapsed)
            started=None
            message=f'{count:,}/{TARGET:,} candidates; final SIM and current-input validation run when building.'
            if cancelled():return dict(publish('Paused','yield',pause_reason(),message=message),cancelled=True)
            if over[0]:return publish('Storage limit','storage','Shared storage budget reached. Free space, then Retry/Resume.',message=message)
            if count>=TARGET:return publish('Prepared candidates','none','Candidate target reached; current-input validation still runs when building.',message=message)
            if state['seconds']>=ATTEMPT_SECONDS:return publish('Needs attention','time_budget','30-minute attempt allowance reached. Retry/Resume grants another bounded attempt.',message=message)
            if stagnant>=3:return publish('Needs attention','no_progress','Three search batches added no candidates. Retry/Resume retries the bounded search; this is not proof the slate is exhausted.',message=message)
            return publish('Queued','batch','Checkpoint saved; waiting for the next idle search batch.',message=message)
        except InterruptedError:
            if started is not None:
                elapsed=time.monotonic()-started
                state.update(seconds=state.get('seconds',0)+elapsed,total_seconds=state['total_seconds']+elapsed)
            return dict(publish('Paused','yield',pause_reason()),cancelled=True)
        except Exception as exc:
            if started is not None:
                elapsed=time.monotonic()-started
                state.update(seconds=state.get('seconds',0)+elapsed,total_seconds=state['total_seconds']+elapsed)
            return publish('Needs attention','time_budget' if isinstance(exc,TimeoutError) else 'error',str(exc))
    return dict(preparation='Upcoming discovered slates have no pending automatic work.')


class _EventProxy:
    def __init__(self,callback):self.callback=callback
    def is_set(self):return self.callback()
