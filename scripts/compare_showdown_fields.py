"""Freeze a diagnostic candidate bank and compare opponent models on shared outcomes."""
import argparse
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_snapshots import load_snapshot
from optimizers import ShowdownOptimizer, ShowdownLineup
from showdown_simulation import active_showdown_players, showdown_signature, simulate_showdown, validate_showdown_lineup
from lineup_ranking import ranked_lineups
from pipeline_audit import quarterback_mix


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot');parser.add_argument('--bank',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--candidates',type=int,default=1200)
    parser.add_argument('--scenarios',type=int,default=2000)
    parser.add_argument('--field',type=int,default=4000)
    args=parser.parse_args()
    if not 1<=args.candidates<=5000 or not 1<=args.scenarios<=10000 or not 1<=args.field<=10000:
        parser.error('Use 1–5000 candidates and 1–10000 scenarios/opponents.')
    snap=load_snapshot(args.snapshot);recipe=snap['inputs']['recipe'];cap=recipe.get('salary_cap',50000)
    if recipe.get('sport')!='NFL' or recipe.get('contest_kind')!='showdown':
        parser.error('An NFL Showdown snapshot is required.')
    players=active_showdown_players(snap['inputs']['players']);path=Path(args.bank)
    if path.exists():
        data=json.loads(path.read_text(encoding='utf-8'))
        if data['input_id']!=snap['input_id']:
            parser.error('Candidate bank belongs to different snapshot inputs.')
    else:
        bank={};styles=['Strategic','Balanced','Contrarian','Chalk','Randomized']
        for i,style in enumerate(styles):
            deadline=time.monotonic()+30
            rows=ShowdownOptimizer(players,salary_cap=cap,seed=5100+i,build_style=style,
                own_mode=recipe.get('ownership_mode','Balanced'),own_weight=recipe.get('ownership_weight',.15)).build_lineups(
                    num_lineups=max(1,(args.candidates+4)//5),cancel_callback=lambda:time.monotonic()>=deadline)
            for lu in rows:bank.setdefault(showdown_signature(lu),lu)
        data={'input_id':snap['input_id'],'purpose':'Frozen diagnostic candidates, not the original build shortlist',
              'lineups':[dict(lu) for lu in list(bank.values())[:args.candidates]]}
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data),encoding='utf-8')
    candidates=[ShowdownLineup(lu['Captain'],lu['Flex']) for lu in data['lineups']]
    if not candidates:parser.error('No diagnostic candidates generated.')
    for lu in candidates:validate_showdown_lineup(lu,players,cap)
    result={'input_id':snap['input_id'],'candidate_count':len(candidates),'purpose':data['purpose'],
            'scenarios':args.scenarios,'opponents':args.field,'models':{}}
    leaders={};means={}
    for model in ['legacy','salary-bands-v1']:
        start=time.monotonic()
        sim=simulate_showdown(candidates,players,scenarios=args.scenarios,field_lineup_count=args.field,
                              salary_cap=cap,seed=90210,field_model=model)
        ordered=ranked_lineups(sim['lineups']);top=ordered[:min(150,len(ordered))]
        result['models'][model]={'field':sim['report']['field_diagnostic'],'top150':quarterback_mix(top,scored=True),
              'top150_mean_top1':sum(lu.sim_metrics['sim_top_one_pct'] for lu in top)/max(1,len(top)),
              'seconds':round(time.monotonic()-start,2)}
        leaders[model]={showdown_signature(lu) for lu in ordered[:min(50,len(ordered))]}
        means[model]={showdown_signature(lu):lu.sim_metrics['sim_mean'] for lu in ordered}
    result['top50_overlap_pct']=100*len(leaders['legacy']&leaders['salary-bands-v1'])/max(1,len(leaders['legacy']))
    result['candidate_mean_points_unchanged']=means['legacy']==means['salary-bands-v1']
    Path(args.output).parent.mkdir(parents=True,exist_ok=True)
    Path(args.output).write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='models'},indent=2))
    for model,info in result['models'].items():
        print(model, 'salary',info['field']['salary_mean'],'top150 top1',round(info['top150_mean_top1'],3),
              'QB mix',info['top150'],'sampling',info['field'].get('sampling'))


if __name__=='__main__':main()
