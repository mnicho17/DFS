"""Bounded feasibility repair when greedy selection reaches a hard constraint dead end."""
import time
def repair(pool,retained,selected,meta,conflicts,limits,group_ok,score,seconds=15):
    try:
        import pulp
        deadline = time.perf_counter() + max(0,float(seconds))
        all_rows=list(retained)+list(pool)
        all_rows=list({id(lu):lu for lu in all_rows}.values())
        problem=pulp.LpProblem('portfolio_feasibility',pulp.LpMaximize)
        variables={id(lu):pulp.LpVariable(f'entry_{i}',cat='Binary') for i,lu in enumerate(all_rows)}
        requested=limits['requested']
        problem += pulp.lpSum(variables.values()) == requested
        chosen={id(lu) for lu in selected}
        values={id(lu):float(rank) for rank,lu in enumerate(sorted(all_rows,key=score))}
        lo=min(values.values(),default=0);span=max(1,max(values.values(),default=0)-lo)
        problem += pulp.lpSum(variables[k]*((len(all_rows)+1 if k in chosen else 0)+(values[k]-lo)/span) for k in variables)
        for lu in retained:problem += variables[id(lu)] == 1
        for lu in all_rows:
            key=id(lu)
            if not group_ok(meta[key]['keys']):problem += variables[key] == 0
            for other in conflicts.get(key,set()):
                if other in variables and other<key:problem += variables[key]+variables[other] <= 1
        def maximum(field,key,limit):
            if limit is not None:
                problem.addConstraint(pulp.lpSum(variables[id(lu)] for lu in all_rows
                    if key in meta[id(lu)][field]) <= limit)
        for key,value in limits['total'].items():maximum('keys',key,value)
        for key,value in limits['captain'].items():
            if value is not None:problem += pulp.lpSum(variables[id(lu)] for lu in all_rows if meta[id(lu)]['captain_key']==key) <= value
        for field,label in [('teams','team'),('games','game')]:
            for key in set().union(*(meta[id(lu)][field] for lu in all_rows)):maximum(field,key,limits[label])
        if limits['specialist'] is not None:
            problem += pulp.lpSum(variables[id(lu)] for lu in all_rows if meta[id(lu)]['specialist_captain']) <= limits['specialist']
        remaining = deadline-time.perf_counter()
        if remaining <= 0:return None
        problem.solve(pulp.PULP_CBC_CMD(msg=False,timeLimit=remaining,threads=1))
        # Never release fractional or constraint-violating solver incumbents on timeout.
        if any(v.value() is None or abs(v.value()-round(v.value()))>1e-6 for v in variables.values()):return None
        if any(not constraint.valid(1e-5) for constraint in problem.constraints.values()):return None
        result=[lu for lu in all_rows if variables[id(lu)].value()>.5]
        return result if len(result)==requested else None
    except (ImportError,ValueError,RuntimeError,pulp.PulpSolverError if 'pulp' in locals() else RuntimeError):
        return None
