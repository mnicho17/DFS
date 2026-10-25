"""Bounded, aggregate-only observations; never search or feasibility authority."""
import itertools
import math
import time

MAX_PLAYERS=128
MAX_OBSERVED=100000
MAX_COMBINATIONS=1000000


def observe(players,signatures,*,salary_cap,minimum_salary,target,cancelled=lambda:False,
            seconds=1.0,clock=time.perf_counter,max_combinations=MAX_COMBINATIONS):
    start=clock();examined=0
    def result(status,**values):
        return dict(status=status,examined=examined,seconds=max(0,clock()-start),**values)
    def stopped():
        return 'cancelled' if cancelled() else 'time bound' if clock()-start>=seconds else ''
    try:
        stop=stopped()
        if stop:return result(stop)
        pool=[p for p in players if p.get('NFLQBEligible') is not False]
        if len(pool)>MAX_PLAYERS:return result('size bound')
        cap=float(salary_cap);floor=float(minimum_salary)
        if not all(math.isfinite(x) for x in (cap,floor)) or not 0<=floor<=cap<=1e9:
            return result('unavailable')
        from nfl_simulation import player_key
        keys=[];explicit=[];cpt_ids=[];salaries=[];captain_salaries=[];teams=[]
        for p in pool:
            values=[str(p.get(k) or '').strip() for k in ('FlexID','CptID')]
            key=player_key(p)
            if not all(values) or not key or len(key)>256:return result('unavailable')
            prices=[]
            for field in ('FlexSalary','CptSalary'):
                raw=p.get(field)
                if isinstance(raw,bool):return result('unavailable')
                price=float(raw)
                # Whole-unit prices avoid FLEX-order floating-point ambiguity.
                if not math.isfinite(price) or not 0<price<=1e9 or not price.is_integer():
                    return result('unavailable')
                prices.append(price)
            keys.append(key);explicit.append(values[0]);cpt_ids.append(values[1])
            salaries.append(prices[0]);captain_salaries.append(prices[1])
            teams.append(str(p.get('Team') or '').strip().upper())
        if (len(set(keys))!=len(pool) or len(set(explicit))!=len(pool)
                or len(set(cpt_ids))!=len(pool) or '' in teams or len(set(teams))!=2):
            return result('unavailable')
        if any((p.get('LockFlex') and p.get('FadeFlex')) or
               (p.get('LockCpt') and (p.get('FadeCpt') or p.get('LockFlex'))) for p in pool):
            return result('unavailable')
        locks=[i for i,p in enumerate(pool) if p.get('LockFlex')]
        if len(locks)>5:return result('unavailable')
        captains=[i for i,p in enumerate(pool) if not p.get('FadeCpt') and not p.get('LockFlex')]
        if any(p.get('LockCpt') for p in pool):captains=[i for i in captains if pool[i].get('LockCpt')]
        choices=[];structural=0
        for captain in captains:
            available=[i for i,p in enumerate(pool) if i!=captain and i not in locks and not p.get('FadeFlex')]
            slots=5-len(locks)
            structural+=math.comb(len(available),slots) if len(available)>=slots else 0
            choices.append((captain,available,slots))
        if structural>max_combinations:return result('size bound')
        known=set()
        for index,signature in enumerate(signatures):
            if index>=MAX_OBSERVED:return result('size bound')
            signature=tuple(signature)
            if (len(signature)!=6 or any(not isinstance(k,str) or len(k)>260 for k in signature)
                    or not signature[0].startswith('CPT:') or len(set(signature[1:]))!=5
                    or signature[0][4:] in signature[1:] or tuple(sorted(signature[1:]))!=signature[1:]):
                return result('unavailable')
            known.add(signature)
            if index%1024==0:
                stop=stopped()
                if stop:return result(stop)
        upper=matched=0
        for captain,available,slots in choices:
            for extra in itertools.combinations(available,slots):
                if examined%1024==0:
                    stop=stopped()
                    if stop:return result(stop)
                examined+=1
                flex=tuple(locks)+extra
                salary=captain_salaries[captain]+sum(salaries[i] for i in flex)
                if not floor<=salary<=cap:continue
                if len({teams[i] for i in (captain,)+flex})!=2:continue
                upper+=1
                signature=('CPT:'+keys[captain],)+tuple(sorted(keys[i] for i in flex))
                matched+=signature in known
        # Observed rosters outside this captured scope invalidate coverage claims.
        if matched!=len(known):return result('unavailable')
        return result('complete',upper_bound=upper,observed=len(known),remaining_upper_bound=upper-len(known),target=target)
    except (KeyError,TypeError,ValueError,OverflowError):
        return result('unavailable')


def clean(value):
    if not isinstance(value,dict):return {}
    status=value.get('status')
    if status not in ('complete','size bound','time bound','cancelled','unavailable'):return {}
    output=dict(status=status)
    for key in ('examined','upper_bound','observed','remaining_upper_bound','target'):
        number=value.get(key)
        if type(number) is int and 0<=number<=10**12:output[key]=number
    elapsed=value.get('seconds')
    if type(elapsed) in (int,float) and math.isfinite(elapsed) and elapsed>=0:output['seconds']=elapsed
    if status=='complete':
        if (any(key not in output for key in ('upper_bound','observed','remaining_upper_bound','target'))
                or output['observed']+output['remaining_upper_bound']!=output['upper_bound']):
            return dict(status='unavailable')
    else:
        for key in ('upper_bound','observed','remaining_upper_bound','target'):output.pop(key,None)
    return output


def lines(value):
    data=clean(value)
    if not data:return []
    if data['status']!='complete':
        return ['- Candidate-space diagnostic: '+data['status']+'; no numeric salary bound recorded.']
    text=(f"- Candidate-space diagnostic: {data['observed']:,} observed salary-eligible candidates out of at most "
          f"{data['upper_bound']:,} combinations under the captured pool, Captain/FLEX locks, fades, salary range and two-team rule; "
          f"at most {data['remaining_upper_bound']:,} additional combinations before other rules.")
    if data['target']>data['upper_bound']:text+=f" Candidate target {data['target']:,} exceeds this bound."
    return [text,'- This outer bound ignores exposure, groups, style correlations and other rules; it is not proof of portfolio feasibility. Search and limits were unchanged.']
