"""Bounded feasibility repair when greedy selection reaches a hard constraint dead end."""
import time

def conflict_index(rows, keys, minimum, checkpoint=lambda: None, expand=True):
    """Index shared roster subsets; equivalent to pairwise uniqueness checks."""
    from collections import defaultdict
    from itertools import combinations
    from math import comb
    sets = {}
    for lu in rows:
        checkpoint()
        sets[id(lu)] = set(keys(lu))
    if minimum <= 1:
        return {}, []
    lengths = {len(k) for k in sets.values()}
    edges = {k: set() for k in sets}
    if len(lengths) == 1:
        size = lengths.pop()
        shared = max(0, size - minimum + 1)
        if shared <= size and comb(size, shared) * len(rows) <= 300000:
            buckets = defaultdict(set)
            for key, values in sets.items():
                checkpoint()
                for subset in combinations(sorted(values), shared):
                    buckets[subset].add(key)
            groups = [group for group in buckets.values() if len(group) > 1]
            for group in (groups if expand else []):
                checkpoint()
                for key in group:
                    checkpoint()
                    edges[key].update(group - {key})
            return edges, groups
    items = list(sets.items())
    for i, (key, values) in enumerate(items):
        checkpoint()
        for other, other_values in items[:i]:
            if len(values - other_values) < minimum:
                edges[key].add(other); edges[other].add(key)
    return edges, None


def valid_portfolio(rows, retained, meta, conflicts, limits, group_ok):
    from collections import Counter
    ids = {id(lu) for lu in rows}
    if len(ids) != limits['requested'] or len(rows) != len(ids):
        return False
    if not {id(lu) for lu in retained} <= ids:
        return False
    if any(not meta[key].get('eligible', True) or not group_ok(meta[key]['keys']) or conflicts.get(key, set()) & ids for key in ids):
        return False
    for field, label in [('keys', 'total'), ('teams', 'team'), ('games', 'game')]:
        counts = Counter(value for key in ids for value in meta[key][field])
        if label == 'total' and any(counts[key] < floor for key, floor in limits.get('min_total', {}).items()):
            return False
        for value, count in counts.items():
            cap = limits[label].get(value) if label == 'total' else limits[label]
            if cap is not None and count > cap:
                return False
    counts = Counter(meta[key]['captain_key'] for key in ids)
    flex_counts = Counter(value for key in ids for value in meta[key].get('flex_keys', ()))
    if any(cap is not None and flex_counts[key] > cap for key, cap in limits.get('flex', {}).items()):
        return False
    if any(counts[key] < floor for key, floor in limits.get('min_captain', {}).items()):
        return False
    if any(cap is not None and counts[key] > cap for key, cap in limits['captain'].items()):
        return False
    cap = limits['specialist']
    return cap is None or sum(meta[key]['specialist_captain'] for key in ids) <= cap
def repair(pool,retained,selected,meta,conflicts,limits,group_ok,score,seconds=15,conflict_groups=None,cancelled=lambda: False):
    try:
        import pulp
        deadline = time.perf_counter() + max(0,float(seconds))
        from bounded_solver import check, solve
        checkpoint = lambda: check(deadline, cancelled)
        checkpoint()
        all_rows=list(retained)+list(pool)
        all_rows=list({id(lu):lu for lu in all_rows}.values())
        problem=pulp.LpProblem('portfolio_feasibility',pulp.LpMaximize)
        variables = {}
        for i,lu in enumerate(all_rows):
            checkpoint()
            variables[id(lu)] = pulp.LpVariable(f'entry_{i}',cat='Binary')
        requested=limits['requested']
        problem += pulp.lpSum(variables.values()) == requested
        chosen={id(lu) for lu in selected}
        values={id(lu):float(rank) for rank,lu in enumerate(sorted(all_rows,key=score))}
        lo=min(values.values(),default=0);span=max(1,max(values.values(),default=0)-lo)
        problem += pulp.lpSum(variables[k]*((len(all_rows)+1 if k in chosen else 0)+(values[k]-lo)/span) for k in variables)
        for lu in retained:problem += variables[id(lu)] == 1
        for lu in all_rows:
            checkpoint()
            key=id(lu)
            if not meta[key].get('eligible', True) or not group_ok(meta[key]['keys']):problem += variables[key] == 0
            for other in (conflicts.get(key,set()) if conflict_groups is None else ()):
                if other in variables and other<key:problem += variables[key]+variables[other] <= 1
        for group in conflict_groups or []:
            checkpoint()
            problem += pulp.lpSum(variables[key] for key in group) <= 1
        def maximum(field,key,limit):
            checkpoint()
            if limit is not None:
                problem.addConstraint(pulp.lpSum(variables[id(lu)] for lu in all_rows
                    if key in meta[id(lu)][field]) <= limit)
        for key,value in limits['total'].items():maximum('keys',key,value)
        for key,value in limits.get('flex', {}).items():maximum('flex_keys',key,value)
        for key,value in limits.get('min_total', {}).items():
            checkpoint()
            if value:
                problem += pulp.lpSum(variables[id(lu)] for lu in all_rows if key in meta[id(lu)]['keys']) >= value
        for key,value in limits.get('min_captain', {}).items():
            checkpoint()
            if value:
                problem += pulp.lpSum(variables[id(lu)] for lu in all_rows if meta[id(lu)]['captain_key']==key) >= value
        for key,value in limits['captain'].items():
            checkpoint()
            if value is not None:problem += pulp.lpSum(variables[id(lu)] for lu in all_rows if meta[id(lu)]['captain_key']==key) <= value
        for field,label in [('teams','team'),('games','game')]:
            for key in set().union(*(meta[id(lu)][field] for lu in all_rows)):maximum(field,key,limits[label])
        if limits['specialist'] is not None:
            problem += pulp.lpSum(variables[id(lu)] for lu in all_rows if meta[id(lu)]['specialist_captain']) <= limits['specialist']
        remaining = deadline-time.perf_counter()
        if remaining <= 0:return None
        if not solve(problem, deadline, cancelled):return None
        # Never release fractional or constraint-violating solver incumbents on timeout.
        if any(v.value() is None or abs(v.value()-round(v.value()))>1e-6 for v in variables.values()):return None
        if any(not constraint.valid(1e-5) for constraint in problem.constraints.values()):return None
        result=[lu for lu in all_rows if variables[id(lu)].value()>.5]
        return result if len(result)==requested else None
    except (ImportError,ValueError,RuntimeError,TimeoutError,pulp.PulpSolverError if 'pulp' in locals() else RuntimeError):
        return None
