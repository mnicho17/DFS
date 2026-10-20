"""Bounded idle warming receipts; never qualify a production scenario cache."""
import copy
import datetime as dt
import re
import time
from build_snapshots import fingerprint,validate_snapshot

ATTEMPT_SECONDS=600
TOTAL_SECONDS=1800
MAX_ATTEMPTS=3


def request_snapshot(prepared,current=None,previous=None):
    prepared=validate_snapshot(prepared)
    if current:
        from candidate_library import slate_id
        current=validate_snapshot(current)
        a,b=prepared['inputs'],current['inputs']
        if slate_id(a['players'],a['recipe']['contest_kind'])==slate_id(b['players'],b['recipe']['contest_kind']):
            return current,'Current captured setup'
    if previous:
        captured,scope=request_snapshot(prepared,previous)
        if scope=='Current captured setup':return captured,'Last captured setup'
    return prepared,'Saved Smart setup'


def request_id(snapshot):
    from candidate_library import code_id
    return fingerprint(dict(policy=1,input_id=snapshot['input_id'],build=code_id()))


def artifact_present(record):
    from scenario_cache import cache_folder
    key=record.get('cache_id') or ''
    try:return bool(re.fullmatch(r'[0-9a-f]{64}',key) and (cache_folder()/(key+'.sqlite')).is_file())
    except OSError:return False


def settle_interrupted(record):
    if record.get('state')=='Running':
        record['seconds']=record.get('seconds',0)+record.pop('reserved_seconds',0)
        record.update(state='Paused',message='Previous attempt did not retire; its reserved allowance is counted conservatively.')


def pending(state,snapshot,*,retry=False):
    record=state.get('scenario_preparation') or {}
    if record.get('request_id')!=request_id(snapshot):return True
    if retry:return True
    settle_interrupted(record)
    if record.get('state')=='Completed':return not artifact_present(record)
    if record.get('state')=='Needs attention':return False
    if record.get('attempts',0)>=MAX_ATTEMPTS or TOTAL_SECONDS-record.get('seconds',0)<60:
        record.update(state='Needs attention',message='Scenario attempt allowance reached. Use Retry/Resume for another bounded attempt.')
        return False
    return True


def warm(catalog,receipt,state,row,path,snapshot,scope,cancelled,progress,*,retry=False,pause_reason=lambda:'Waiting for idle time.'):
    from smart_preparation import save,summary,source_digest
    from slate_catalog import record_preparation
    from scenario_preparation import scenario_preparation_result
    identity=request_id(snapshot)
    record=copy.deepcopy(state.get('scenario_preparation') or {})
    if record.get('request_id')!=identity or retry:
        record=dict(request_id=identity,input_id=snapshot['input_id'],scope=scope,attempts=0,seconds=0)
    if scope in ('Current captured setup','Last captured setup'):
        state['scenario_snapshot']=copy.deepcopy(snapshot)
    settle_interrupted(record)
    record['scope']=scope
    if record.get('state')=='Completed':
        # A missing completed artifact needs a new bounded attempt, not a claim of readiness.
        record.update(attempts=0,seconds=0)
    def publish(status,message):
        record.update(state=status,message=message,updated_at=dt.datetime.now().astimezone().isoformat())
        state['scenario_preparation']=record
        state.update(message=message,updated_at=record['updated_at'])
        save(receipt,state)
        result=summary(state,row['digest'],path)
        record_preparation(catalog,result)
        result['retry_consumed']=bool(retry)
        if status in ('Queued','Paused'):result['pause_reason']=message
        elif status=='Needs attention':result['errors']=1
        return result
    if cancelled():return dict(publish('Paused',pause_reason()),cancelled=True)
    if record.get('attempts',0)>=MAX_ATTEMPTS or TOTAL_SECONDS-record.get('seconds',0)<60:
        return publish('Needs attention','Scenario attempt allowance reached. Use Retry/Resume for another bounded attempt.')
    try:
        matches=source_digest(row['path'],cancelled)==row['digest']
    except InterruptedError:
        return dict(publish('Paused',pause_reason()),cancelled=True)
    except OSError:
        return publish('Needs attention','Salary source unavailable; refresh discovery before warming scenarios.')
    if not matches:return publish('Needs attention','Salary source changed; refresh discovery before warming scenarios.')
    record['attempts']+=1
    allowance=min(ATTEMPT_SECONDS,TOTAL_SECONDS-record['seconds'])
    record['reserved_seconds']=allowance
    publish('Running','Preparing reusable scenarios from '+scope.lower()+'. Current loaded slate is unchanged.')
    started=time.monotonic()
    prior_seconds=record['seconds']
    try:
        outcome=scenario_preparation_result(path,snapshot,seconds=allowance,
            cancelled=cancelled,progress=progress)
        record.pop('reserved_seconds',None)
        record['seconds']=prior_seconds+max(0,time.monotonic()-started)
        cache=outcome.get('cache') or {}
        record.update(cache_status=cache.get('status','not recorded'),cache_id=cache.get('cache_id'),
                      reused_scenarios=cache.get('reused_scenarios',0),requested_scenarios=cache.get('requested_scenarios',0))
        if cancelled():
            record['attempts']-=1
            return dict(publish('Paused',pause_reason()),cancelled=True)
        if source_digest(row['path'],cancelled)!=row['digest']:
            return publish('Needs attention','Salary source changed during warming. Captured caches do not establish current readiness.')
        complete=outcome.get('state')=='Completed' and cache.get('status') in ('saved','reused') and artifact_present(record)
        if complete:return publish('Completed',outcome['message'])
        status='Needs attention' if outcome.get('state')=='Needs attention' or record['attempts']>=MAX_ATTEMPTS or record['seconds']>=TOTAL_SECONDS else 'Queued'
        return publish(status,outcome.get('message','No completed scenario cache was recorded.')+
                       (' Use Retry/Resume to grant another bounded attempt.' if status=='Needs attention' else ' Another bounded attempt waits for idle time.'))
    except Exception as exc:
        record.pop('reserved_seconds',None)
        record['seconds']=prior_seconds+max(0,time.monotonic()-started)
        if cancelled():
            record['attempts']-=1
            return dict(publish('Paused',pause_reason()),cancelled=True)
        return publish('Needs attention','Scenario warming stopped; candidate library retained. '+str(exc))
