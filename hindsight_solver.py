"""Pure actual-point MILPs over detached, qualified supplied salary universes."""
import math
import time

import bounded_solver
from hindsight_contract import (Frozen, Cancelled, check, decimal, points, signature,
    validate_roster, restrictions, RULES, SCALE, DEFAULT_SECONDS, DEFAULT_TIES, SCOPE)


def checkpoint(deadline,cancelled):
    check(cancelled)
    if time.perf_counter()>=deadline:raise TimeoutError('Shared hindsight solver budget ended')


def model(pool,kind,rules,deadline,cancelled):
    import pulp
    checkpoint(deadline,cancelled)
    problem=pulp.LpProblem('hindsight',pulp.LpMaximize)
    variables={}; by_key={p['key']:p for p in pool}
    for n,p in enumerate(pool):
        checkpoint(deadline,cancelled)
        for j,role in enumerate(sorted(p['roles'])):
            variables[p['key'],role]=pulp.LpVariable(f'x{n}_{j}',cat='Binary')
    def appearance(key,role='ANY'):
        return pulp.lpSum(v for (k,r),v in variables.items() if k==key and (role=='ANY' or role==r))
    # A role-count model is equivalent to repeated-slot assignment, without permutations.
    from collections import Counter
    for role,count in Counter(RULES[kind+'_slots']).items():
        problem+=pulp.lpSum(v for (key,r),v in variables.items() if role==r)==count
    for p in pool:
        checkpoint(deadline,cancelled);problem+=appearance(p['key'])<=1
    objective=pulp.lpSum(by_key[k]['roles'][r]['score_units']*v for (k,r),v in variables.items())
    problem+=objective
    problem+=pulp.lpSum(by_key[k]['roles'][r]['salary']*v for (k,r),v in variables.items())<=rules['salary_cap']
    # At least two teams: no team may occupy every slot. Showdown evidence has one game.
    roster_size=len(RULES[kind+'_slots'])
    for team in sorted({p['team'] for p in pool}):
        problem+=pulp.lpSum(v for (k,r),v in variables.items() if by_key[k]['team']==team)<=roster_size-1
    for key,role in rules['require']:problem+=appearance(key,role)==1
    for key,role in rules['exclude']:problem+=appearance(key,role)==0
    for group in rules['groups']:
        total=pulp.lpSum(appearance(key) for key in group['keys'])
        problem+=(total>=1 if group['type']=='at_least_one' else total<=1)
    checkpoint(deadline,cancelled)
    return problem,variables,objective


def proof(metadata,problem):
    import pulp
    if not metadata.get('processed') or not metadata.get('zero_gap_options') or metadata.get('exit_code')!=0:
        return 'solver_error'
    status,solution=problem.status,problem.sol_status
    if metadata.get('model_status')!=status or metadata.get('solution_status')!=solution:
        return 'validation_failed'
    termination=metadata.get('termination')
    if termination=='optimal' and status==pulp.LpStatusOptimal and solution==pulp.LpSolutionOptimal and metadata.get('complete_variables'):
        return 'optimal'
    # PuLP 3.x omits "Integer" from cbcSolStatus. Its explicit Integer
    # infeasible header plus completed infeasibility log is still a proof.
    infeasible_solution = solution==pulp.LpSolutionInfeasible or (
        solution==pulp.LpSolutionNoSolutionFound and metadata.get('header','').startswith('Integer infeasible - '))
    if termination=='infeasible' and status==pulp.LpStatusInfeasible and infeasible_solution:
        return 'infeasible'
    if termination=='time_limit' or solution==pulp.LpSolutionIntegerFeasible:
        return 'time_limit'
    return 'solver_error'


def witness(variables,metadata,pool,kind,rules):
    if any(v.value() not in (0,1) or isinstance(v.value(),bool) for v in variables.values()):
        raise ValueError('nonbinary_assignment')
    row=[dict(key=k,role=r) for (k,r),v in variables.items() if v.value()==1]
    checked=validate_roster(row,pool,kind,rules)
    if decimal(metadata['objective'])!=checked['score_units']:
        raise ValueError('solver_objective_receipt_conflict')
    return checked


def empty(status,blockers=()):
    return dict(status=status,blockers=list(blockers),score_units=None,points=None,lineups=[],
                ties=dict(status='not_checked',returned=0,exact_total=None,lower_bound=0),
                bound=None,gap=None,solver=None)


def _primary(pool,kind,rules,deadline,cancelled):
    report=empty('solver_error'); prepared=None
    try:
        prepared=model(pool,kind,rules,deadline,cancelled)
        problem,variables,objective=prepared
        metadata=bounded_solver.solve(problem,deadline,cancelled,strict=True)
        checkpoint(deadline,cancelled)
        report['solver']=metadata;report['status']=proof(metadata,problem)
        if report['status']=='optimal':
            checked=witness(variables,metadata,pool,kind,rules)
            checkpoint(deadline,cancelled)
            report.update(score_units=checked['score_units'],points=checked['points'],lineups=[checked])
            report['ties'].update(returned=1,lower_bound=1)
    except Cancelled:report['status']='cancelled'
    except TimeoutError:report['status']='cancelled' if cancelled() else 'time_limit'
    except (ValueError,KeyError,TypeError) as exc:
        report.update(status='validation_failed',blockers=[str(exc)])
    except Exception:
        report.update(status='solver_error',blockers=['bounded_solver_failed'])
    return report,prepared


def _ties(report,prepared,pool,kind,rules,limit,deadline,cancelled):
    import pulp
    if report['status']!='optimal' or not limit:return
    problem,variables,objective=prepared
    problem+=objective==report['score_units']
    seen={tuple(report['lineups'][0]['signature'])}; current=report['lineups'][0]
    report['ties']['status']='partial'
    while True:
        try:
            checkpoint(deadline,cancelled)
            chosen={(s['key'],s['role']) for s in current['roster']}
            if kind=='classic':
                keys={k for k,r in chosen}
                problem+=pulp.lpSum(v for (k,r),v in variables.items() if k in keys)<=8
            else:
                problem+=pulp.lpSum(variables[k,r] for k,r in sorted(chosen))<=5
            metadata=bounded_solver.solve(problem,deadline,cancelled,strict=True)
            checkpoint(deadline,cancelled)
            status=proof(metadata,problem)
            if status=='infeasible':
                report['ties'].update(status='complete',exact_total=len(seen));break
            if status!='optimal':
                report['ties']['stop_reason']=status;break
            current=witness(variables,metadata,pool,kind,rules)
            key=tuple(current['signature'])
            if key in seen or current['score_units']!=report['score_units']:
                raise ValueError('invalid_tied_roster')
            seen.add(key);report['ties']['lower_bound']=len(seen)
            if len(report['lineups'])>=limit:
                report['ties']['stop_reason']='display_limit';break
            report['lineups'].append(current)
        except (Cancelled,TimeoutError):
            report['ties']['stop_reason']='cancelled' if cancelled() else 'time_limit';break
        except Exception:
            report['ties']['stop_reason']='validation_or_solver_error';break
    report['lineups'].sort(key=lambda r:r['signature'])
    report['ties']['returned']=len(report['lineups'])


def calculate(capture, *, seconds=DEFAULT_SECONDS,tie_limit=DEFAULT_TIES,cancelled=lambda:False,progress=lambda text:None):
    if isinstance(seconds,bool) or not isinstance(seconds,(int,float)) or not math.isfinite(seconds) or not 0<=seconds<=DEFAULT_SECONDS:
        raise ValueError('Solver budget must be between 0 and 30 seconds')
    if isinstance(tie_limit,bool) or not isinstance(tie_limit,int) or not 0<=tie_limit<=DEFAULT_TIES:
        raise ValueError('Tie display limit must be between 0 and 20')
    d=capture.data; started=time.perf_counter();deadline=started+seconds
    scopes={};models={};pool=d['pool'];kind=d['format']
    rules={'supplied':restrictions(),'snapshot':d['restricted']['rules']}
    for name in ('supplied','snapshot'):
        gate=d['gates'][name]
        if gate['status']!='ready':scopes[name]=empty(gate['status'],gate['blockers']);continue
        progress('Solving '+('supplied salary universe' if name=='supplied' else 'snapshot-local restrictions'))
        scopes[name],models[name]=_primary(pool,kind,rules[name],deadline,cancelled)
    # Complete both requested primaries before optional tie work spends the remainder.
    for name in ('supplied','snapshot'):
        if name in models and scopes[name]['status']=='optimal':
            progress('Checking distinct optimal roster ties: '+name)
            _ties(scopes[name],models[name],pool,kind,rules[name],tie_limit,deadline,cancelled)
    gaps=dict(snapshot_restriction_units=None,observed_units=None,issues=[])
    a,b=scopes['supplied'],scopes['snapshot']
    if a['status']==b['status']=='optimal':
        delta=a['score_units']-b['score_units']
        if delta<0:gaps['issues'].append('restricted_optimum_exceeds_supplied_optimum')
        else:gaps['snapshot_restriction_units']=delta
    best=d['observed'].get('highest')
    if a['status']=='optimal' and best:
        if decimal(best)>decimal(a['points']):gaps['issues'].append('reported_entry_exceeds_supplied_optimum')
        if d['observed'].get('highest_exact_validated'):
            delta=a['score_units']-d['observed']['highest_units']
            if delta>=0:gaps['observed_units']=delta
    return Frozen.freeze(dict(version=1,capture=d,scopes=scopes,gaps=gaps,cancelled=bool(cancelled()),
        timing=dict(seconds=time.perf_counter()-started,budget_seconds=seconds,tie_limit=tie_limit),
        numerical_policy=dict(scale=SCALE,base_decimal_places=4,max_absolute_base=10000,zero_gaps=True),scope=SCOPE))


def summary(report,private=False):
    d=report.data; c=d['capture'];lines=['Hindsight Solver — retrospective actual points only',SCOPE,
        'Original contest pool completeness: unverified. Original/submitted build: not established.',
        f"Actual coverage: {c['actual_coverage'].get('known',0)}/{c['actual_coverage'].get('athletes',0)} athletes.",
        f"Highest reported score in supplied entries: {c['observed'].get('highest') or 'Unavailable'}."]
    for name,label in (('supplied','Supplied pool'),('snapshot','Snapshot-local restrictions')):
        r=d['scopes'][name]; t=r['ties']
        lines.append(f"{label}: {r['status']}; points {r['points'] if r['points'] is not None else 'Unavailable'}; "
                     f"ties {t['status']}, {t['returned']} returned, total {t['exact_total'] if t['exact_total'] is not None else 'unknown'}, at least {t['lower_bound']}.")
        # Blockers are controlled codes, never paths, names or raw source values.
        if r['blockers']:lines.append('Evidence/rules blockers: '+', '.join(r['blockers']))
        if private:
            names={p['key']:p['name'] for p in c['pool']}
            for row in r['lineups']:
                lines.append(' | '.join(s['role']+' '+names[s['key']] for s in row['roster']))
    lines+=['No pipeline-stage explanation, prediction, payout, submission or live-strategy inference.',
            'Portfolio percentages, uniqueness, retained rows and effective recovery policies are not evaluated here.']
    return '\n'.join(lines)
