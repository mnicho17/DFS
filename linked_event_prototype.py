"""Diagnostic event scoring; normal builds never instantiate this model.

Raw receiving yards/TDs are mirrored on the verified QB ledger. Forecast-normalized
points preserve each modeled forecast mean, but are NOT exact DK event totals.
No bonuses, fumbles, reserve-player production or opponent defensive linkage is
modeled. Independent per-event yardage assumptions are predeclared, not fitted.
"""
import copy,hashlib,math,random
from collections import Counter
from joint_opportunity_prototype import allocate
from rotation_zero_prototype import _number
from nfl_eligibility import depth,unavailable

VERSION='linked-event-prototype-v1'
YARDAGE_SHAPE=4.


def probability(value):
    value=_number(value)
    if value>1:raise ValueError('Invalid event probability')
    return value


class LinkedEventPrototype:
    def __init__(self,players,*,completion_environment=0.):
        from nfl_simulation import player_key,_projection
        self.completion_environment=_number(completion_environment)
        if self.completion_environment>.2:raise ValueError('Completion environment exceeds diagnostic allowance')
        self.players=copy.deepcopy(sorted(players,key=player_key))
        self.keys=[player_key(p) for p in self.players]
        if len(set(self.keys))!=len(self.keys):raise ValueError('Ambiguous prototype player identities')
        self.work={};self.forecasts={};self.teams={};self.qbs={};self.expected={};self.counts=Counter()
        self.last_ledger=None
        for p in self.players:
            w=p.get('NFLWorkload');pos=p.get('Position');key=player_key(p)
            if not w or w.get('version')!='workload-v2' or unavailable(p) or depth(p)<1:continue
            if pos=='QB' and p.get('NFLQBEligible') is not True:continue
            if pos not in ('QB','RB','WR','TE'):continue
            self.work[key]=w;self.teams[key]=str(p.get('Team') or '').strip().upper()
            if p.get('ProjectionSource')=='Automatic workload estimate':self.forecasts[key]=_number(_projection(p))
            catch=probability(w['efficiency'][1]);ypc=_number(w['efficiency'][0]);ypr=_number(w['efficiency'][2])
            rush=probability(w['rates']['rushing_td_per_carry']);rec=probability(w['rates']['receiving_td_per_catch'])
            self.expected[key]=_number(w['carries'])*(ypc*.1+6*rush)+_number(w['targets'])*catch*(1+ypr*.1+6*rec)
        for team in sorted(set(self.teams.values())):
            qbs=[player_key(p) for p in self.players if self.teams.get(player_key(p))==team and p.get('Position')=='QB' and player_key(p) in self.work]
            if len(qbs)!=1:raise ValueError('Exactly one frozen eligible quarterback required per modeled team')
            qb=qbs[0];self.qbs[team]=qb
            receivers=[k for k in self.work if self.teams[k]==team and k!=qb]
            targets=sum(_number(self.work[k]['targets']) for k in receivers)
            catches=sum(_number(self.work[k]['targets'])*probability(self.work[k]['efficiency'][1]) for k in receivers)
            incomplete=targets-catches
            expected_int=targets*probability(self.work[qb]['rates']['interceptions_per_attempt'])
            if expected_int>incomplete:raise ValueError('Interception prior incompatible with completion prior')
            rate=expected_int/incomplete if incomplete else 0.
            yards=sum(_number(self.work[k]['targets'])*probability(self.work[k]['efficiency'][1])*_number(self.work[k]['efficiency'][2]) for k in receivers)
            touchdowns=sum(_number(self.work[k]['targets'])*probability(self.work[k]['efficiency'][1])*probability(self.work[k]['rates']['receiving_td_per_catch']) for k in receivers)
            self.qbs[team]=dict(key=qb,interception_probability=rate)
            self.expected[qb]+=.04*yards+4*touchdowns-expected_int
        for key,forecast in self.forecasts.items():
            if self.expected[key]<=0 and forecast:raise ValueError('Forecast has no positive expected event score')
        self.completion_amplitudes={}
        for team in self.qbs:
            rates=[probability(w['efficiency'][1]) for k,w in self.work.items() if self.teams[k]==team and _number(w['targets'])>0 and probability(w['efficiency'][1])>0]
            self.completion_amplitudes[team]=min([self.completion_environment]+[max(0.,1/c-1) for c in rates])
        self.affected_keys=frozenset(self.forecasts)
        self.normalizers={k:self.forecasts[k]/self.expected[k] if self.expected[k]>0 else 0. for k in self.forecasts}
        # Validate frozen identities and budgets before any diagnostic scenarios.
        allocate(self.players,{id(p):self.work[player_key(p)] for p in self.players if player_key(p) in self.work},random.Random(0))

    def yardage(self,stream,mean):
        return stream.gammavariate(YARDAGE_SHAPE,mean/YARDAGE_SHAPE) if mean else 0.

    def apply(self,players,sampled_workloads,outcomes,rng):
        from nfl_simulation import player_key
        if sorted(player_key(p) for p in players)!=self.keys:raise ValueError('Frozen event player identities changed')
        stream=random.Random(hashlib.sha256((VERSION+repr(rng.getstate())).encode()).digest())
        contexts={team:1+(amplitude if stream.random()<.5 else -amplitude) if amplitude else 1. for team,amplitude in sorted(self.completion_amplitudes.items())}
        work_by_id={}
        for p in self.players:
            key=player_key(p)
            if key not in self.work:continue
            w=dict(self.work[key]);w['efficiency']=list(w['efficiency'])
            w['efficiency'][1]*=contexts[self.teams[key]]
            work_by_id[id(p)]=w
        allocated,_,budgets=allocate(self.players,work_by_id,stream)
        ledger={k:dict(**v,rushing_yards=0.,rushing_td=0,receiving_yards=0.,receiving_td=0,
            passing_yards=0.,passing_td=0,interceptions=0,passing_attempts=0,passing_completions=0,unresolved_targets=0) for k,v in allocated.items()}
        for key,row in ledger.items():
            w=self.work[key]
            ypc=_number(w['efficiency'][0]);ypr=_number(w['efficiency'][2])
            for _ in range(row['carries']):
                row['rushing_yards']+=self.yardage(stream,ypc)
                row['rushing_td']+=int(stream.random()<probability(w['rates']['rushing_td_per_carry']))
            for _ in range(row['catches']):
                row['receiving_yards']+=self.yardage(stream,ypr)
                row['receiving_td']+=int(stream.random()<probability(w['rates']['receiving_td_per_catch']))
        for team,qb in self.qbs.items():
            row=ledger[qb['key']];receivers=[k for k in ledger if self.teams[k]==team and k!=qb['key']]
            row['passing_yards']=sum(ledger[k]['receiving_yards'] for k in receivers)
            row['passing_td']=sum(ledger[k]['receiving_td'] for k in receivers)
            row['passing_attempts']=sum(ledger[k]['targets'] for k in receivers)
            row['passing_completions']=sum(ledger[k]['catches'] for k in receivers)
            row['unresolved_targets']=sum(b['reserve'] for b in budgets if b['team']==team and b['metric']=='targets')
            incomplete=row['passing_attempts']-row['passing_completions']
            row['interceptions']=sum(stream.random()<qb['interception_probability'] for _ in range(incomplete))
        result=dict(outcomes)
        for key,row in ledger.items():
            row['raw_points']=.1*(row['rushing_yards']+row['receiving_yards'])+6*(row['rushing_td']+row['receiving_td'])+row['catches']+.04*row['passing_yards']+4*row['passing_td']-row['interceptions']
            if key not in self.affected_keys:continue
            result[key]=row['raw_points']*self.normalizers[key]
            if not math.isfinite(result[key]):raise ValueError('Nonfinite event score')
            self.counts['eligible_draws']+=1;self.counts['zero_gates']+=int(result[key]==0)
        self.last_ledger=dict(players=ledger,budgets=budgets,completion_contexts=contexts)
        return result
