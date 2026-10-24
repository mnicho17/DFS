"""Warm the ordinary Deep pipeline from an overnight library's frozen inputs."""
import copy
import time
from build_snapshots import validate_snapshot, snapshot_objective
from compute_settings import normalize_deep_settings


def scenario_preparation_result(path, snapshot, *, seconds, cancelled=lambda:False, progress=lambda text:None):
    snapshot = validate_snapshot(snapshot)
    if seconds < 60 or cancelled():
        return dict(state='Paused',message='Scenario preparation skipped: less than one minute remains or Pause was requested.',cache={})
    from main_window import LineupBuildWorker
    inputs = copy.deepcopy(snapshot['inputs']); recipe = inputs['recipe']
    options = normalize_deep_settings(recipe.get('deep_compute'))
    worker = LineupBuildWorker(inputs['players'], kind=recipe['contest_kind'], sport='NFL',
        num_lineups=recipe.get('requested_lineups',150), salary_cap=recipe.get('salary_cap',50000),
        own_mode=recipe.get('ownership_mode','Balanced'), own_weight=recipe.get('ownership_weight',0),
        build_style=recipe.get('build_style','Strategic'), salary_strategy=recipe.get('salary_strategy','Near Cap'),
        portfolio_rules=inputs['rules'], sim_enabled=True, sim_scenarios=recipe.get('nfl_sim_scenarios',5000),
        field_preset=recipe.get('nfl_field_preset','150-Max'), field_calibration=inputs.get('calibration'),
        contest_profile=inputs.get('contest'), contest_objective=snapshot_objective(snapshot),
        compute_mode='Deep', deep_options=options,
        deep_time_limit_seconds=min(seconds,options['minutes']*60), candidate_library=str(path), scenario_cache=True)
    worker.build_purpose = "preparation"
    worker.build_input_id = snapshot["input_id"]
    deadline=time.monotonic()+seconds
    class Stop:
        def is_set(self):return cancelled() or time.monotonic() >= deadline
    worker._cancel_event = Stop()
    results=[];errors=[]
    worker.finished.connect(results.append);worker.error.connect(errors.append)
    worker.progress.connect(lambda done,total,text:progress('Preparing scenarios: '+text))
    worker.run()
    if errors:
        return dict(state='Needs attention',message='Scenario preparation stopped; saved combinations remain available. '+str(errors[0]),cache={})
    if cancelled():return dict(state='Paused',message='Scenario preparation paused. Only completed scenario caches are reusable.',cache={})
    cache=(results[0].get('sim_report',{}).get('scenario_cache',{}) if results else {})
    status=cache.get('status','unavailable')
    reason=str(cache.get('reason') or 'No cache outcome reason recorded.')
    message=(f'Scenario preparation finished; validation cache {status}. {reason} Matching completed scenarios can be reused by Deep; '
             'no prepared portfolio was submitted or applied to the current output table.')
    from scenario_recovery_guidance import storage_blocked,GUIDANCE
    if storage_blocked(cache):message+=' '+GUIDANCE
    complete=status in ('saved','reused') and cache.get('requested_scenarios',0)>0
    state='Completed' if complete else 'Paused' if time.monotonic()>=deadline else 'Needs attention'
    return dict(state=state,message=message,cache=dict(cache))


def prepare_scenarios(path, snapshot, *, seconds, cancelled=lambda:False, progress=lambda text:None):
    return scenario_preparation_result(path,snapshot,seconds=seconds,cancelled=cancelled,progress=progress)['message']
