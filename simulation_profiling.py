"""Observational wall-time components; never change RNG, inputs or stop checks."""
from contextvars import ContextVar
from functools import wraps
import math
import time

FIELDS=('setup_seconds','cache_lookup_seconds','outcomes_seconds','opponents_seconds',
        'scenario_io_seconds','candidates_seconds','cache_finish_seconds','result_assembly_seconds','other_seconds','total_seconds')
PHASES=('screening','validation','ranking_audit','other')
_active=ContextVar('simulation_profile',default=None)
_session=ContextVar('simulation_profile_session',default=None)


class Clock:
    def __init__(self):
        self.started=time.perf_counter()
        self.setup_seconds=self.cache_lookup_seconds=self.outcomes_seconds=self.opponents_seconds=0.0
        self.scenario_io_seconds=self.candidates_seconds=self.cache_finish_seconds=0.0
        self.assembly_started=None

    def report(self, result, requested, candidates):
        ended=time.perf_counter();total=max(0,ended-self.started)
        timings={key:max(0,getattr(self,key)) for key in FIELDS[:7]}
        timings['result_assembly_seconds']=max(0,ended-self.assembly_started) if self.assembly_started else 0.0
        timings['other_seconds']=max(0,total-sum(timings.values()))
        timings['total_seconds']=total
        report=result.get('report') or {};cache=report.get('scenario_cache') or {}
        return dict(timings=timings,requested_scenarios=max(0,int(requested)),
            completed_scenarios=max(0,int(report.get('scenarios',0))),candidate_count=candidates,
            field_count=max(0,int(report.get('field_lineups',0))),
            reused_scenarios=max(0,int(cache.get('reused_scenarios',0))),cache_status=str(cache.get('status','disabled')))


def current():return _active.get()
def begin_session():return _session.set({})
def end_session(token):_session.reset(token)


def snapshot():
    import copy
    return copy.deepcopy(_session.get() or {})


def observe_simulation(fn):
    @wraps(fn)
    def wrapped(*args,**kwargs):
        clock=Clock();token=_active.set(clock)
        try:result=fn(*args,**kwargs)
        finally:_active.reset(token)
        try:
            profile=clock.report(result,kwargs.get('scenarios',750),len(result.get('lineups') or []))
            result.setdefault('report',{})['simulation_profile']=profile
            session=_session.get()
            if session is not None:
                phase={73129:'screening',90210:'validation',481516:'ranking_audit'}.get(kwargs.get('seed',90210),'other')
                row=session.setdefault(phase,dict(calls=0,completed_scenarios=0,reused_scenarios=0,candidate_evaluations=0,
                    timings={key:0.0 for key in FIELDS}))
                row['calls']+=1;row['completed_scenarios']+=profile['completed_scenarios']
                row['reused_scenarios']+=profile['reused_scenarios']
                row['candidate_evaluations']+=profile['candidate_count']*profile['completed_scenarios']
                for key in FIELDS:row['timings'][key]+=profile['timings'][key]
        except Exception:
            pass  # Profiling delivery cannot reject a completed simulation.
        return result
    return wrapped


def clean(value):
    if not isinstance(value,dict):return {}
    result={}
    for phase in PHASES:
        row=value.get(phase)
        if not isinstance(row,dict):continue
        timings=row.get('timings') if isinstance(row.get('timings'),dict) else {};item={}
        for key in ('calls','completed_scenarios','reused_scenarios','candidate_evaluations'):
            number=row.get(key)
            if isinstance(number,int) and not isinstance(number,bool) and number>=0:item[key]=number
        item['timings']={key:float(timings[key]) for key in FIELDS if isinstance(timings.get(key),(int,float)) and not isinstance(timings[key],bool) and math.isfinite(timings[key]) and timings[key]>=0}
        result[phase]=item
    return result
