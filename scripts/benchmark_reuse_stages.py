"""Controlled synthetic Deep builds using one frozen candidate bank per format."""
import argparse
import copy
import json
import os
import statistics
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def verify_profiles(profile, mode):
    if mode not in ('fresh','cold_record','warm_replay'):
        raise ValueError('Unsupported benchmark mode')
    for phase in ('screening','validation','ranking_audit'):
        row=profile.get(phase) or {}
        completed=row.get('completed_scenarios');reused=row.get('reused_scenarios')
        if type(completed) is not int or completed<=0 or type(reused) is not int or not 0<=reused<=completed:
            raise ValueError('Incomplete scenario-reuse evidence: '+phase)
        expected=completed if mode=='warm_replay' and phase!='ranking_audit' else 0
        if reused!=expected:raise ValueError('Scenario reuse disagrees with benchmark mode: '+phase)


def summarize(runs):
    modes=('fresh','cold_record','warm_replay')
    trials={}
    for row in runs:
        trial=trials.setdefault(row['repeat'],{})
        if row['mode'] in trial:raise ValueError('Duplicate benchmark mode in a trial')
        trial[row['mode']]=row
    if not trials or any(set(trial)!=set(modes) for trial in trials.values()):
        raise ValueError('Each trial must contain fresh, cold and warm measurements')
    if len({row['result_digest'] for row in runs})!=1:
        raise ValueError('Exact result parity is required before summarizing timings')
    result={}
    for mode in modes:
        rows=[r for r in runs if r['mode']==mode]
        result[mode]=dict(wall_seconds=statistics.median(r['wall_seconds'] for r in rows),
            stage_seconds={key:statistics.median(r['stages'][key] for r in rows)
                for key in rows[0]['stages']},
            simulation_phase_seconds={key:statistics.median(r['simulation_profile'][key]['timings']['total_seconds'] for r in rows)
                for key in rows[0]['simulation_profile']})
    return result


def run(kind, root, candidates=80, field=200, scenarios=2500, repeats=1, lineups=4, shortlist=20):
    if kind not in ('classic','showdown'):raise ValueError('Unsupported format')
    if any(type(value) is not int or not 1<=value<=bound for value,bound in
           ((candidates,2000),(field,10000),(scenarios,10000),(repeats,5),(lineups,150),(shortlist,2000))):
        raise ValueError('Benchmark bounds exceeded')
    if lineups>min(candidates,shortlist):raise ValueError('Candidate and shortlist limits must cover the request')
    if min(candidates,shortlist)<20:raise ValueError('At least 20 candidates and shortlist rows are needed for the fresh ranking audit')
    from build_snapshots import create_snapshot,fingerprint
    from candidate_library import run_search,roster_keys,code_id
    from test_nfl_logic import _fixture_players
    from test_showdown_performance import _showdown_players
    from main_window import LineupBuildWorker
    import main_window,showdown_simulation
    players=(_fixture_players if kind=='classic' else _showdown_players)()
    frozen=fingerprint(players)
    snapshot=create_snapshot(players,dict(sport='NFL',contest_kind=kind,salary_cap=50000,
        salary_strategy='Balanced Spend',requested_lineups=lineups),{})
    library=root/(kind+'.dfslib')
    run_search(library,snapshot,seconds=3,candidate_limit=candidates,batch_size=candidates)
    owner=main_window if kind=='classic' else showdown_simulation
    name='simulate_nfl_contest' if kind=='classic' else 'simulate_showdown'
    real=getattr(owner,name);runs=[];expected=None
    def without_timestamps(value):
        # Distribution-capture timestamps are observational; preserve all numerical results.
        if isinstance(value,dict):return {k:without_timestamps(v) for k,v in value.items() if k not in ('started_at','finished_at')}
        if isinstance(value,(list,tuple)):return [without_timestamps(v) for v in value]
        return value
    for repeat in range(repeats):
        # One frozen bank across trials; separate disposable caches per trial.
        with patch.dict(os.environ,{'DFS_OPTIMIZER_DATA_DIR':str(root/(kind+'-cache-'+str(repeat)))}):
            for mode,cache in (('fresh',False),('cold_record',True),('warm_replay',True)):
                print(f'{kind} trial {repeat+1}/{repeats}: {mode}',flush=True)
                captures=[];results=[];errors=[]
                def simulate(*args,**kwargs):
                    result=real(*args,**kwargs)
                    captures.append(dict(lineups=result['lineups'],report=copy.deepcopy(result['report'])))
                    return result
                worker=LineupBuildWorker(copy.deepcopy(players),kind=kind,num_lineups=lineups,salary_cap=50000,
                    salary_strategy='Balanced Spend',sim_enabled=True,sim_scenarios=scenarios,compute_mode='Deep',
                    deep_time_limit_seconds=1200,deep_options=dict(candidates=candidates,shortlist=shortlist,field=field,screening=250),
                    candidate_library=str(library),scenario_cache=cache,compute_telemetry=False)
                worker.finished.connect(results.append);worker.error.connect(errors.append)
                started=time.perf_counter()
                with patch.object(owner,name,side_effect=simulate):worker.run()
                elapsed=time.perf_counter()-started
                if errors or not results:raise RuntimeError(str(errors or 'No completed build'))
                result=results[0]
                if result.get('cancelled') or len(result.get('lineups',[]))!=lineups:raise RuntimeError('Incomplete benchmark portfolio')
                def row(lineup):
                    return dict(signature=roster_keys(lineup,kind),metrics=lineup.sim_metrics,
                        top=sorted(lineup.sim_top_hits),top_five=sorted(lineup.sim_top_five_hits),wins=sorted(lineup.sim_win_hits),
                        values=lineup.sim_scenario_values)
                evidence=[]
                for captured in captures:
                    report={k:v for k,v in captured['report'].items() if k not in ('simulation_profile','scenario_cache')}
                    evidence.append(dict(rows=[row(lu) for lu in captured['lineups']],report=report))
                payload=without_timestamps(dict(stages=evidence,selected=[row(lu) for lu in result['lineups']]))
                digest=fingerprint(payload)
                if expected is None:expected=digest;baseline=copy.deepcopy(payload)
                if digest!=expected:
                    def first_difference(a,b,path='root'):
                        if type(a)!=type(b):return path+' type differs'
                        if isinstance(a,dict):
                            if set(a)!=set(b):return path+' keys differ: '+str(set(a)^set(b))
                            for key in a:
                                difference=first_difference(a[key],b[key],path+'.'+str(key))
                                if difference:return difference
                        elif isinstance(a,list):
                            if len(a)!=len(b):return path+' length differs'
                            for index,(left,right) in enumerate(zip(a,b)):
                                difference=first_difference(left,right,path+'['+str(index)+']')
                                if difference:return difference
                        elif a!=b:return path+' differs: '+repr(a)+' versus '+repr(b)
                        return None
                    raise RuntimeError('Scoring/selection parity failed for '+kind+' '+mode+': '+str(first_difference(baseline,payload)))
                if fingerprint(players)!=frozen:raise RuntimeError('Frozen inputs changed')
                timing=result['timing_report']
                verify_profiles(timing.get('simulation_profile',{}),mode)
                runs.append(dict(repeat=repeat,mode=mode,wall_seconds=elapsed,result_digest=digest,
                    stages={k:timing.get(k) for k in ('generation_seconds','simulation_seconds','selection_seconds','total_seconds')},
                    preparation=[dict(stage=r['stage'],seconds=r.get('seconds')) for r in timing.get('build_preparation',[])],
                    simulation_profile=timing.get('simulation_profile',{})))
    return dict(kind=kind,requested_lineups=lineups,requested_shortlist=shortlist,requested_candidates=candidates,requested_field=field,requested_scenarios=scenarios,
        build=code_id(),identical_results=True,scenario_reuse_verified=True,repeats=repeats,summary=summarize(runs),runs=runs)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind',choices=('classic','showdown','both'),default='both')
    parser.add_argument('--candidates',type=int,default=80)
    parser.add_argument('--field',type=int,default=200)
    parser.add_argument('--scenarios',type=int,default=2500)
    parser.add_argument('--repeats',type=int,default=1)
    parser.add_argument('--lineups',type=int,default=4)
    parser.add_argument('--shortlist',type=int,default=20)
    parser.add_argument('--output',required=True)
    args=parser.parse_args();output=Path(args.output).resolve()
    from test_environment import install,network_attempts
    install()
    from PyQt5 import QtCore
    app=QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
    with tempfile.TemporaryDirectory() as raw:
        root=Path(raw)
        os.environ['DFS_OPTIMIZER_DATA_DIR']=str(root/'data')
        result=[run(kind,root,args.candidates,args.field,args.scenarios,args.repeats,args.lineups,args.shortlist) for kind in
                (('classic','showdown') if args.kind=='both' else (args.kind,))]
    if network_attempts:raise RuntimeError('Unexpected network attempts during benchmark')
    output.write_text(json.dumps(dict(limitations='Fixed fresh/cold/warm ordering; OS cache, hardware and load affect timings. Small synthetic banks are not production-slate latency. Profiles are nested inside simulation time, not extra elapsed time.',note='Synthetic fixed-candidate Deep comparison, not production slate latency. Independent audits remain fresh.',results=result),indent=2,allow_nan=False),encoding='utf-8')
    print(str(output))


if __name__=='__main__':main()
