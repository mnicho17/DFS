"""Controlled synthetic Deep builds using one frozen candidate bank per format."""
import argparse
import copy
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def run(kind, root, candidates=80, field=200, scenarios=2500):
    from build_snapshots import create_snapshot,fingerprint
    from candidate_library import run_search,roster_keys,code_id
    from test_nfl_logic import _fixture_players
    from test_showdown_performance import _showdown_players
    from main_window import LineupBuildWorker
    import main_window,showdown_simulation
    players=(_fixture_players if kind=='classic' else _showdown_players)()
    frozen=fingerprint(players)
    snapshot=create_snapshot(players,dict(sport='NFL',contest_kind=kind,salary_cap=50000,
        salary_strategy='Balanced Spend',requested_lineups=4),{})
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
    for mode,cache in (('fresh',False),('cold_record',True),('warm_replay',True)):
        captures=[];results=[];errors=[]
        def simulate(*args,**kwargs):
            result=real(*args,**kwargs)
            captures.append(dict(lineups=result['lineups'],report=copy.deepcopy(result['report'])))
            return result
        worker=LineupBuildWorker(copy.deepcopy(players),kind=kind,num_lineups=4,salary_cap=50000,
            salary_strategy='Balanced Spend',sim_enabled=True,sim_scenarios=scenarios,compute_mode='Deep',
            deep_time_limit_seconds=1200,deep_options=dict(candidates=candidates,shortlist=20,field=field,screening=250),
            candidate_library=str(library),scenario_cache=cache,compute_telemetry=False)
        worker.finished.connect(results.append);worker.error.connect(errors.append)
        started=time.perf_counter()
        with patch.object(owner,name,side_effect=simulate):worker.run()
        elapsed=time.perf_counter()-started
        if errors or not results:raise RuntimeError(str(errors or 'No completed build'))
        result=results[0]
        if result.get('cancelled') or len(result.get('lineups',[]))!=4:raise RuntimeError('Incomplete benchmark portfolio')
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
        runs.append(dict(mode=mode,wall_seconds=elapsed,result_digest=digest,
            stages={k:timing.get(k) for k in ('generation_seconds','simulation_seconds','selection_seconds','total_seconds')},
            preparation=[dict(stage=r['stage'],seconds=r.get('seconds')) for r in timing.get('build_preparation',[])],
            simulation_profile=timing.get('simulation_profile',{})))
    return dict(kind=kind,requested_candidates=candidates,requested_field=field,requested_scenarios=scenarios,
        build=code_id(),identical_results=True,runs=runs)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind',choices=('classic','showdown','both'),default='both')
    parser.add_argument('--candidates',type=int,default=80)
    parser.add_argument('--field',type=int,default=200)
    parser.add_argument('--scenarios',type=int,default=2500)
    parser.add_argument('--output',required=True)
    args=parser.parse_args();output=Path(args.output).resolve()
    from test_environment import install
    install()
    from PyQt5 import QtCore
    app=QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
    with tempfile.TemporaryDirectory() as raw:
        root=Path(raw)
        import os
        os.environ['DFS_OPTIMIZER_DATA_DIR']=str(root/'data')
        result=[run(kind,root,args.candidates,args.field,args.scenarios) for kind in
                (('classic','showdown') if args.kind=='both' else (args.kind,))]
    output.write_text(json.dumps(dict(note='Synthetic fixed-candidate Deep comparison, not production slate latency. Independent audits remain fresh.',results=result),indent=2),encoding='utf-8')
    print(str(output))


if __name__=='__main__':main()
