"""Read-only zero-score prototype calibration and team-interaction diagnostics; no production defaults changed."""
import sys,json,random,statistics,datetime as dt,math,argparse,hashlib
from pathlib import Path
from collections import defaultdict
repository=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(repository))
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--db',required=True)
parser.add_argument('--output-prefix',required=True)
parser.add_argument('--completed-before',required=True)
parser.add_argument('--interaction-study',action='store_true',help='Predeclared shared-gate and weaker-hurdle comparisons; no fitting')
parser.add_argument('--joint-study',action='store_true',help='Predeclared finite-count and occupancy comparisons')
parser.add_argument('--evaluation-after',help='Exclusive game-date boundary fixed before reading holdout scores')
args=parser.parse_args()
if args.evaluation_after:
 dt.date.fromisoformat(args.evaluation_after)
 if args.evaluation_after>=args.completed_before:parser.error('Holdout boundary must precede completion cutoff')
dt.date.fromisoformat(args.completed_before)
root=Path(args.output_prefix).resolve().parent
root.mkdir(parents=True,exist_ok=True)
import analysis_imports as ai
from opponent_history import _connect,VERSION
from pregame_field_evidence import PregameEvidence
from field_outcome_validation import _scores
from nfl_simulation import _scenario_outcomes,player_key,_projection
from nfl_specialists import team_kicker
from nfl_eligibility import depth
from repeatability import model_version
from distribution_validation import validation_report

db=args.db
version=model_version();audit=PregameEvidence(db)
report=dict(status='running',model_version=version,completed_before=args.completed_before,seeds=[90210,481516,730001],scenarios_per_seed=2000,
 games=[],excluded=[],source_exclusions=[],limitations=[
 'Current-model retrospective reruns on strictly qualified frozen pregame inputs, separate from distributions recorded by the old executable.',
 'Exact selected-source base-player scores only; other exports detect conflicts and never fill missing outcomes. Captain is not a second observation.',
 'One representative contest per game, selected by known construction coverage before reading outcomes. Games, not players or contests, are evaluation units.',
 'No weighting fit, future scores, missing-score zeros, current-depth substitutions or production changes. Small game samples remain exploratory.',
 'Kicker primary summaries use the team kicker selected from frozen role evidence by the current model, with fallback status disclosed. Other listed kickers remain in raw rows.'])
destination=Path(args.output_prefix).with_suffix('.json')
def save():destination.write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
score_cache={};verified={}
def verify(source):
 if source['hash'] not in verified:ai._verify(source,lambda:False);verified[source['hash']]=source
def scores(source,manifest,salary_hash):
 key=(source['hash'],salary_hash)
 if key not in score_cache:score_cache[key]=_scores(source,manifest,lambda:False)
 return score_cache[key]
report['prototype']=dict(version='rotation-zero-prototype-v1',diagnostic_only=True,defaults_changed=False,
 modes=['baseline','frozen_hurdle','sampled_hurdle'],
 limitations='Conditional means of transformed players preserved in expectation; kicker means may drift through nonlinear offense feedback. Shared continuous workload budgets retained, not a discrete opportunity redistribution or full play-by-play model.')
profile_options={'baseline':None,'frozen_hurdle':dict(opportunity_basis='frozen'),
 'sampled_hurdle':dict(opportunity_basis='sampled')}
if args.interaction_study:
 profile_options.update(shared_quarter=dict(team_gate_share=.25),
  weaker_hurdle=dict(zero_strength=.5),weaker_shared=dict(zero_strength=.5,team_gate_share=.25))
if args.joint_study:
 profile_options={'baseline':None,'sampled_hurdle':dict(opportunity_basis='sampled'),
  'joint_counts':dict(joint_scoring='counts'),'joint_occupancy':dict(joint_scoring='occupancy')}
report['evaluation']=dict(kind='temporal_holdout' if args.evaluation_after else 'development_retrospective',after=args.evaluation_after)
report['prototype']['profiles']=profile_options
report['prototype']['modes']=list(profile_options)
report['limitations']=[v for v in report['limitations'] if not v.startswith('Kicker primary summaries')]
def corr(a,b):
 return statistics.correlation(a,b) if statistics.pstdev(a)>0 and statistics.pstdev(b)>0 else 0.
def metrics(values,actual):
 s=sorted(values);n=len(s);lo=s[int((n-1)*.1)];hi=s[int((n-1)*.9)];zero=sum(x==0 for x in s)/n
 return dict(mean=statistics.mean(s),bias=statistics.mean(s)-actual,
  crps=sum(abs(v-actual) for v in s)/n-sum((2*i-n-1)*v for i,v in enumerate(s,1))/(n*n),
  interval_score=hi-lo+10*max(lo-actual,0)+10*max(actual-hi,0),inside=float(lo<=actual<=hi),predicted_zero=zero,
  zero_brier=(zero-float(actual==0))**2,p95=s[int((n-1)*.95)])
save()
with _connect(db,True) as conn:
 conn.execute('BEGIN');sources={s['hash']:s for s in ai._sources(conn)}
 pairs=dict(conn.execute('SELECT result_hash,salary_hash FROM analysis_salary_pairs'))
 catalog=[];manifests={}
 raw=conn.execute('SELECT contest_key,result_hash,salary_hash,start_date,end_date,name,audit_json FROM opponent_contests WHERE format=? AND version=? AND end_date<? ORDER BY end_date,contest_key',('showdown',VERSION,report['completed_before'])).fetchall()
 for key,r,s,start,end,name,encoded in raw:
  if args.evaluation_after and end<=args.evaluation_after:continue
  try:
   if r not in sources or s not in sources or pairs.get(r)!=s:raise ValueError('Unresolved original source/salary association')
   verify(sources[r]);verify(sources[s])
   if not ai.qualify_pair(sources[r],sources[s])['compatible']:raise ValueError('Original salary association incompatible')
   if s not in manifests:manifests[s]=ai._salary_manifest(sources[s]['snapshot'],lambda:False)
   m=manifests[s]
   if len(m['dates'])!=1 or len(m['games'])!=1 or start!=end or end!=m['dates'][0]:raise ValueError('Single dated game required')
   if json.loads(encoded).get('conflicting_entry_ids_excluded'):raise ValueError('Conflicting entry identities require review')
   known=conn.execute("SELECT SUM(json_extract(stats_json,'$.constructions.known_entries')) FROM opponent_user_contest_stats WHERE contest_key=?",(key,)).fetchone()[0] or 0
   if not known:raise ValueError('No known constructions for representative-source selection')
   catalog.append(dict(key=key,result_hash=r,salary_hash=s,date=end,game=m['games'][0],name=name,known=known))
  except Exception as exc:report['source_exclusions'].append(dict(name=name,reason=str(exc)))
 representatives={}
 for row in sorted(catalog,key=lambda x:(-x['known'],x['key'])):representatives.setdefault((row['date'],row['game']),row)
 report['indexed_contests']=len(raw);report['candidate_games']=len(representatives)
 report['recorded_distribution_validation']=validation_report(conn)
 for row in sorted(representatives.values(),key=lambda x:(x['date'],x['game'])):
  print('CHECK',row['date'],row['game'],flush=True)
  try:
   source=sources[row['result_hash']];manifest=manifests[row['salary_hash']]
   pool,receipt=audit.qualify(source,manifest,row['salary_hash'],conn)
   _,base,observations=scores(source,manifest,row['salary_hash']);observations=dict(observations)
   for other in catalog:
    if (other['date'],other['game'])!=(row['date'],row['game']) or other['result_hash']==row['result_hash']:continue
    _,_,comparison=scores(sources[other['result_hash']],manifests[other['salary_hash']],other['salary_hash'])
    if any(observations[k]!=v for k,v in comparison.items() if k in observations):raise ValueError('Cross-export exact player/role score conflict')
    observations.update(comparison)
   from rotation_zero_prototype import RotationZeroPrototype,eligible
   keys=[player_key(p) for p in pool]
   from joint_opportunity_prototype import JointOpportunityPrototype
   models={name:None if options is None else JointOpportunityPrototype(scoring=options['joint_scoring']) if 'joint_scoring' in options else RotationZeroPrototype(**options) for name,options in profile_options.items()}
   values={name:{key:[] for key in keys} for name in models}
   target=[p for p in pool if eligible(p)]
   original=json.dumps(pool,sort_keys=True)
   for seed in report['seeds']:
    rngs={name:random.Random(seed) for name in models}
    for _ in range(report['scenarios_per_seed']):
     outputs={name:_scenario_outcomes(rngs[name],pool,rotation_zero_prototype=model) for name,model in models.items()}
     assert all(rng.getstate()==rngs['baseline'].getstate() for rng in rngs.values()),'Parent RNG drift'
     for p in pool:
      key=player_key(p)
      if not eligible(p) and p['Position']!='K':
       assert all(v[key]==outputs['baseline'][key] for v in outputs.values()),'Protected player draw changed'
     for name,v in outputs.items():
      for key in keys:values[name][key].append(v[key])
   assert json.dumps(pool,sort_keys=True)==original,'Frozen player inputs changed'
   rows=[]
   for p in target:
    actual=base.get((ai._name(p['Name']),p['Team'],p['Position'],manifest['games'][0]))
    if actual is not None:rows.append(dict(name=p['Name'],position=p['Position'],actual=float(actual),
     profiles={name:metrics(v[player_key(p)],float(actual)) for name,v in values.items()}))
   teams=[]
   for team in sorted({p['Team'] for p in pool}):
    offense=[p for p in pool if p['Team']==team and p['Position'] in ('QB','RB','WR','TE')]
    receivers=[p for p in offense if p['Position'] in ('WR','TE')]
    qb=[p for p in offense if p['Position']=='QB']
    rotations=[p for p in target if p['Team']==team]
    kickers=[p for p in pool if p['Team']==team and p['Position']=='K']
    from nfl_specialists import team_kicker
    kicker=team_kicker(kickers)
    profiles={}
    for name,v in values.items():
     total=[sum(v[player_key(p)][i] for p in offense) for i in range(6000)]
     receiving=[sum(v[player_key(p)][i] for p in receivers) for i in range(6000)]
     pairs=[corr(v[player_key(a)],v[player_key(b)]) for i,a in enumerate(rotations) for b in rotations[i+1:]]
     profiles[name]=dict(offense_mean=statistics.mean(total),offense_sd=statistics.pstdev(total),offense_p90=sorted(total)[int(5999*.9)],
      qb_receiver_correlation=corr(v[player_key(qb[0])],receiving) if len(qb)==1 and receivers else None,
      rotation_pair_correlation=statistics.mean(pairs) if pairs else None,
      kicker_mean=statistics.mean(v[player_key(kicker)]) if kicker else None,
      kicker_zero=sum(x==0 for x in v[player_key(kicker)])/6000 if kicker else None,
      rotations_all_zero=sum(all(v[player_key(p)][i]==0 for p in rotations) for i in range(6000))/6000 if rotations else None)
    teams.append(dict(team=team,profiles=profiles))
   report['games'].append(dict(date=row['date'],game=row['game'],source_hash=row['result_hash'],salary_hash=row['salary_hash'],
    snapshot=receipt,rows=rows,teams=teams,eligible=len(target),observed=len(rows),scenarios=6000,
    model_counts={name:dict(model.counts) for name,model in models.items() if model}))
   print('MATCHED',len(rows),'rotation outcomes',flush=True)
  except Exception as exc:
   report['excluded'].append(dict(date=row['date'],game=row['game'],reason=str(exc)));print('EXCLUDED',str(exc),flush=True)
  save()
for source in verified.values():ai._verify(source,lambda:False)
audit.revalidate();assert version==model_version(),'Scoring source changed'
report['summary']={}
for profile in profile_options if report['games'] else ():
 game_metrics=[{k:statistics.mean(r['profiles'][profile][k] for r in g['rows']) for k in ('bias','crps','interval_score','inside','predicted_zero','zero_brier')} for g in report['games'] if g['rows']]
 team_metrics=[{k:statistics.mean(t['profiles'][profile][k] for t in g['teams'] if t['profiles'][profile][k] is not None) for k in ('offense_mean','offense_sd','offense_p90','qb_receiver_correlation','rotation_pair_correlation','kicker_mean','kicker_zero','rotations_all_zero')} for g in report['games']]
 report['summary'][profile]=dict(games=len(game_metrics),observations=sum(len(g['rows']) for g in report['games']),
  **{k:statistics.mean(g[k] for g in game_metrics) for k in game_metrics[0]},
  interactions={k:statistics.mean(g[k] for g in team_metrics) for k in team_metrics[0]})
 if args.interaction_study or args.joint_study:
  report['summary'][profile]['subgroups']={}
  for label,is_zero in [('actual_zero',True),('actual_positive',False)]:
   groups=[[r['profiles'][profile]['crps'] for r in g['rows'] if (r['actual']==0)==is_zero] for g in report['games']]
   present=[statistics.mean(v) for v in groups if v]
   report['summary'][profile]['subgroups'][label]=dict(games=len(present),observations=sum(map(len,groups)),
    crps=statistics.mean(present) if present else None)
report['status']='complete' if report['games'] else 'no_qualified_games';save()
print(json.dumps(report['summary'],indent=2),flush=True)
