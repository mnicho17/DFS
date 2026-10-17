"""Opt-in moment reconciliation and bounded opposing-event DST coupling.

No forecasts are edited. Raw offensive point means match them within solver
precision, without per-player point multipliers. DST remains an explicitly
fractional, forecast-centered research residual, not a complete DK event model.
"""
import copy,hashlib,math,random
from pathlib import Path
from collections import defaultdict
import pulp
from linked_event_prototype import LinkedEventPrototype
from rotation_zero_prototype import _number
from nfl_eligibility import unavailable

VERSION='reconciled-event-prototype-v1'
TOLERANCE=2e-5
BOUNDS=dict(rush_yards=15.,receive_yards=30.,rush_td=.15,receive_td=.35,interception=.25)
SCALES=dict(carries=1.,targets=1.,catches=1.,rush_yards=10.,receive_yards=10.,rush_td=.1,receive_td=.1)


def reconcile(players):
    from nfl_simulation import player_key
    original=LinkedEventPrototype(players)
    # A conservative passing-point lower bound can explain conflicts before solving.
    # WR/TE receiving points beyond receptions imply at least .4 QB points per
    # point, while interceptions cost at most .25 per incomplete target.
    for team,qb in original.qbs.items():
        if original.work[qb['key']]['budgets']['targets']:raise ValueError('QB target prior unsupported')
        if qb['key'] not in original.forecasts:continue
        roster=[k for k in original.work if original.teams[k]==team]
        wrte=[k for k in roster if original.work[k]['position'] in ('WR','TE')]
        receiving=sum(original.forecasts.get(k,original.expected[k]) for k in wrte)
        position_budgets={}
        for k in roster:position_budgets.setdefault(original.work[k]['position'],original.work[k]['budgets'])
        targets=sum(position_budgets.get(pos,{}).get('targets',0.) for pos in ('WR','TE'))
        rush=sum(position_budgets.get(pos,{}).get('carries',0.) for pos in ('WR','TE'))*(.1*BOUNDS['rush_yards']+6*BOUNDS['rush_td'])
        rb_targets=position_budgets.get('RB',{}).get('targets',0.)
        lower=.4*(receiving-rush)-.4*targets-BOUNDS['interception']*rb_targets
        forecast=original.forecasts[qb['key']]
        if forecast+TOLERANCE<lower:raise ValueError(f'Event-prior reconciliation Infeasible: {team} QB mean {forecast:.4f} below conservative lower bound {lower:.4f} under declared priors; no forecasts or bounds changed')
    problem=pulp.LpProblem('diagnostic_event_moments',pulp.LpMinimize)
    variables={};priors={};objective=[]
    for index,(key,w) in enumerate(sorted(original.work.items())):
        c=_number(w['carries']);t=_number(w['targets']);r=t*w['efficiency'][1]
        prior=dict(carries=c,targets=t,catches=r,rush_yards=c*w['efficiency'][0],receive_yards=r*w['efficiency'][2],
            rush_td=c*w['rates']['rushing_td_per_carry'],receive_td=r*w['rates']['receiving_td_per_catch'])
        priors[key]=prior;v={}
        for metric,base in prior.items():
            v[metric]=pulp.LpVariable(f'p{index}_{metric}',lowBound=0)
            plus=pulp.LpVariable(f'p{index}_{metric}_plus',lowBound=0);minus=pulp.LpVariable(f'p{index}_{metric}_minus',lowBound=0)
            problem+=v[metric]-base==plus-minus
            objective.append((plus+minus)/max(SCALES[metric],base))
            if key not in original.forecasts:problem+=v[metric]==base
            elif original.forecasts[key]==0 and w['position']!='QB':problem+=v[metric]==0
        variables[key]=v
        problem+=v['catches']<=v['targets']
        problem+=v['rush_yards']<=BOUNDS['rush_yards']*v['carries']
        problem+=v['receive_yards']<=BOUNDS['receive_yards']*v['catches']
        problem+=v['rush_td']<=BOUNDS['rush_td']*v['carries']
        problem+=v['receive_td']<=BOUNDS['receive_td']*v['catches']
        if not w['budgets']['carries']:problem+=v['carries']==0
        if not w['budgets']['targets']:problem+=v['targets']==0
    groups=defaultdict(list)
    for key,w in original.work.items():groups[(w['team'],w['position'])].append(key)
    for roster in groups.values():
        for metric in ('carries','targets'):
            budgets={original.work[k]['budgets'][metric] for k in roster}
            if len(budgets)!=1:raise ValueError('Conflicting shared budgets')
            budget=_number(next(iter(budgets)))
            problem+=pulp.lpSum(variables[k][metric] for k in roster)<=budget*(1-1e-6)
    interceptions={}
    for team,qb in sorted(original.qbs.items()):
        key=qb['key'];roster=[k for k in variables if original.teams[k]==team and k!=key]
        value=pulp.LpVariable(f'interceptions_{team}',lowBound=0,upBound=3.)
        interceptions[team]=value
        incomplete=pulp.lpSum(variables[k]['targets']-variables[k]['catches'] for k in roster)
        problem+=value<=BOUNDS['interception']*incomplete
        prior=sum(priors[k]['targets']-priors[k]['catches'] for k in roster)*qb['interception_probability']
        plus=pulp.LpVariable(f'int_plus_{team}',lowBound=0);minus=pulp.LpVariable(f'int_minus_{team}',lowBound=0)
        problem+=value-prior==plus-minus;objective.append((plus+minus)/max(.1,prior))
        if key not in original.forecasts:problem+=value==prior
    for key,forecast in original.forecasts.items():
        v=variables[key]
        points=.1*(v['rush_yards']+v['receive_yards'])+6*(v['rush_td']+v['receive_td'])+v['catches']
        if original.work[key]['position']=='QB':
            team=original.teams[key];roster=[k for k in variables if original.teams[k]==team and k!=key]
            points+=.04*pulp.lpSum(variables[k]['receive_yards'] for k in roster)+4*pulp.lpSum(variables[k]['receive_td'] for k in roster)-interceptions[team]
        problem+=points==forecast
    problem+=pulp.lpSum(objective)
    solver=pulp.PULP_CBC_CMD(msg=False,threads=1,timeLimit=10)
    status=problem.solve(solver)
    if status!=pulp.LpStatusOptimal or problem.sol_status!=pulp.LpSolutionOptimal:raise ValueError('Event-prior reconciliation '+pulp.LpStatus.get(status,str(status))+' under declared bounds; no forecasts or bounds changed')
    for constraint in problem.constraints.values():
        residual=constraint.value()
        violation=abs(residual) if constraint.sense==0 else max(0.,residual) if constraint.sense<0 else max(0.,-residual)
        if not math.isfinite(violation) or violation>TOLERANCE:raise ValueError('Solver constraint residual exceeds tolerance')
    solved={key:{name:max(0.,float(value.value())) for name,value in v.items()} for key,v in variables.items()}
    calibrated=copy.deepcopy(players);changes=[]
    for p in calibrated:
        key=player_key(p)
        if key not in solved:continue
        v=solved[key];w=p['NFLWorkload']
        v['catches']=min(v['catches'],v['targets'])
        v['rush_yards']=min(v['rush_yards'],BOUNDS['rush_yards']*v['carries'])
        v['receive_yards']=min(v['receive_yards'],BOUNDS['receive_yards']*v['catches'])
        v['rush_td']=min(v['rush_td'],BOUNDS['rush_td']*v['carries'])
        v['receive_td']=min(v['receive_td'],BOUNDS['receive_td']*v['catches'])
        c=v['carries'];t=v['targets'];r=v['catches']
        w['diagnostic_reconciliation']=VERSION
        w['carries']=c;w['targets']=t;w['efficiency']=[v['rush_yards']/c if c else 0.,r/t if t else 0.,v['receive_yards']/r if r else 0.]
        w['rates']['rushing_td_per_carry']=v['rush_td']/c if c else 0.
        w['rates']['receiving_td_per_catch']=v['receive_td']/r if r else 0.
        if w['position']=='QB':
            team=original.teams[key];targets=sum(solved[k]['targets'] for k in solved if original.teams[k]==team and k!=key)
            w['attempts']=targets
            w['rates']['passing_yards_per_attempt']=sum(solved[k]['receive_yards'] for k in solved if original.teams[k]==team and k!=key)/targets if targets else 0.
            w['rates']['passing_td_per_attempt']=sum(solved[k]['receive_td'] for k in solved if original.teams[k]==team and k!=key)/targets if targets else 0.
            w['rates']['interceptions_per_attempt']=float(interceptions[team].value())/targets if targets else 0.
        changes.append(dict(key=key,before=priors[key],after=v))
    return calibrated,dict(status='Optimal',pulp_version=pulp.__version__,solver_sha256=hashlib.sha256(Path(solver.path).read_bytes()).hexdigest(),objective=pulp.value(problem.objective),bounds=dict(BOUNDS),changes=changes,
        numerical_tolerance=TOLERANCE,budget_margin=1e-6,limitation='Heuristic relative-L1 prior distance, not fitted to outcomes; nonautomatic individual carry/target/receiving moments fixed; QB passing inherits the team.')


class ReconciledEventPrototype(LinkedEventPrototype):
    def __init__(self,players,*,link_dst=False):
        from nfl_simulation import player_key,_projection
        calibrated,self.reconciliation=reconcile(players)
        super().__init__(calibrated,completion_environment=.2)
        for key,forecast in self.forecasts.items():
            if abs(self.expected[key]-forecast)>TOLERANCE:raise ValueError('Reconciled raw mean residual exceeds tolerance')
        self.normalizers={key:1. for key in self.forecasts}
        self.dst_links={}
        if link_dst:
            if len(self.qbs)!=2:raise ValueError('DST linkage requires exactly two verified teams')
            for p in self.players:
                if p.get('Position')!='DST':continue
                if (p.get('ProjectionSource')!='Historical average estimate' or unavailable(p)
                    or p.get('ManualProjection') is not None or p.get('ImportedProjection') is not None):continue
                team=str(p.get('Team') or '').strip().upper();opponents=[t for t in self.qbs if t!=team]
                if len(self.qbs)!=2 or team not in self.qbs or len(opponents)!=1:raise ValueError('DST linkage requires exactly two verified teams')
                opponent=opponents[0];qb=self.qbs[opponent]['key'];maximum=self.qb_upper_bound(opponent)
                if qb not in self.forecasts:raise ValueError('DST linkage requires modeled opposing quarterback')
                forecast=_number(_projection(p));noise=min(3.,(forecast+4)/2)
                expected=self.expected[qb];coefficient=min(.35,(forecast+4-noise)/(maximum-expected)) if maximum>expected else .35
                key=player_key(p)
                if any(v['team']==team for v in self.dst_links.values()):raise ValueError('Ambiguous defensive identity')
                self.dst_links[key]=dict(team=team,qb=qb,forecast=forecast,noise=noise,coefficient=coefficient,maximum=maximum,opponent_mean=expected)
                self.forecasts[key]=forecast;self.expected[key]=forecast;self.normalizers[key]=1.
            if len(self.dst_links)!=2:raise ValueError('DST linkage requires two unambiguous supported-source defenses')
        self.affected_keys=frozenset(self.forecasts)

    def yardage(self,stream,mean):
        # Bounded, symmetric beta keeps the mean and permits an analytic DST floor.
        return 2*mean*stream.betavariate(1.5,1.5) if mean else 0.

    def qb_upper_bound(self,team):
        qb=self.qbs[team]['key'];w=self.work[qb]
        maximum=math.ceil(w['budgets']['carries'])*(.2*w['efficiency'][0]+(6 if w['rates']['rushing_td_per_carry'] else 0)) if w['carries'] else 0.
        groups=defaultdict(list)
        for key,record in self.work.items():
            if self.teams[key]==team and key!=qb and record['targets']>0 and record['efficiency'][1]>0:groups[record['position']].append(record)
        for roster in groups.values():
            per_target=max(.08*w['efficiency'][2]+(4 if w['rates']['receiving_td_per_catch'] else 0) for w in roster)
            maximum+=math.ceil(roster[0]['budgets']['targets'])*per_target
        return maximum

    def apply(self,players,sampled_workloads,outcomes,rng):
        result=super().apply(players,sampled_workloads,outcomes,rng)
        stream=random.Random(hashlib.sha256((VERSION+repr(rng.getstate())).encode()).digest())
        defenses={}
        for key,link in sorted(self.dst_links.items()):
            opponent=self.last_ledger['players'][link['qb']]['raw_points']
            if opponent>link['maximum']+TOLERANCE:raise ValueError('Opponent event bound exceeded')
            signal=link['coefficient']*(link['opponent_mean']-opponent)
            noise=stream.uniform(-link['noise'],link['noise'])
            result[key]=link['forecast']+signal+noise
            if result[key]<-4-TOLERANCE:raise ValueError('DST floor bound violated')
            defenses[key]=dict(opponent_raw_points=opponent,centered_signal=signal,noise=noise,score=result[key])
        self.last_ledger['defenses']=defenses
        return result
