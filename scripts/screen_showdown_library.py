"""Explicit resumable coarse screening; never publishes lineups."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from build_snapshots import load_snapshot
from captain_pool import prepare_captain_pool
from compute_settings import deep_candidate_budget
from showdown_screening import prepare_screening,settings


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot');parser.add_argument('library')
    parser.add_argument('--hours',type=float,default=1)
    parser.add_argument('--cache-folder',type=Path)
    args=parser.parse_args()
    inputs=load_snapshot(args.snapshot)['inputs'];recipe=inputs['recipe']
    if recipe['contest_kind']!='showdown':parser.error('NFL Showdown snapshot required')
    count=recipe.get('requested_lineups',150);options=recipe.get('deep_compute',{})
    players,rules,_=prepare_captain_pool(inputs['players'],inputs['rules'],count)
    try:
        result=prepare_screening(args.library,players,limit=deep_candidate_budget(count,options,False),
            salary_cap=recipe['salary_cap'],salary_strategy=recipe.get('salary_strategy','Near Cap'),
            rules=rules,screening=settings(options,recipe.get('nfl_sim_scenarios',1000)),
            seconds=args.hours*3600,folder=args.cache_folder,progress=lambda value:print(json.dumps(value),flush=True))
        print(json.dumps(result))
    except KeyboardInterrupt:
        print('Stopped; completed batches remain available for resume.')


if __name__=='__main__':main()
