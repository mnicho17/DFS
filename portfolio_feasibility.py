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


def valid_portfolio(rows, retained, meta, conflicts, limits, group_ok, conflict_groups=None):
    from collections import Counter
    ids = {id(lu) for lu in rows}
    if len(ids) != limits['requested'] or len(rows) != len(ids):
        return False
    if not {id(lu) for lu in retained} <= ids:
        return False
    if any(not meta[key].get('eligible', True) or not group_ok(meta[key]['keys']) for key in ids):
        return False
    if conflict_groups is not None:
        if any(len(set(group) & ids) > 1 for group in conflict_groups):
            return False
    elif any(conflicts.get(key, set()) & ids for key in ids):
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
    if cap is not None and sum(meta[key]['specialist_captain'] for key in ids) > cap:
        return False
    kicker_cap = limits.get('kicker_captain')
    return kicker_cap is None or sum(meta[key].get('kicker_captain', False) for key in ids) <= kicker_cap


def diversity_first_witness(pool, retained, meta, conflicts, limits, group_ok, score,
                            conflict_groups=None, seconds=2, cancelled=lambda: False):
    """Find a complete rule-compliant set while preserving scarce diversity.

    Greedy ranking by quality can pick a high-scoring lineup that blocks several
    alternatives. Search low-conflict candidates first for a bounded witness;
    keep the stronger exact CBC search as the fallback when this pass cannot fill.
    This is a coverage pass only. The final portfolio is still selected/ranked
    with the caller's normal scores.
    """
    from collections import Counter, defaultdict

    start = time.perf_counter()
    deadline = start + max(0, float(seconds))
    retained_ids = {id(lu) for lu in retained}
    rows = list(retained) + [lu for lu in pool if id(lu) not in retained_ids]
    if not rows or len(rows) < limits['requested'] or deadline <= start:
        return None
    row_ids = {id(lu) for lu in rows}
    group_list = [set(group) & row_ids for group in (conflict_groups or ())]
    group_list = [group for group in group_list if len(group) > 1]
    groups_by_id = defaultdict(list)
    degree = Counter()
    if group_list:
        for group_index, group in enumerate(group_list):
            group_cost = len(group) - 1
            for row_id in group:
                groups_by_id[row_id].append(group_index)
                degree[row_id] += group_cost
    else:
        for row_id in row_ids:
            degree[row_id] = len(conflicts.get(row_id, set()) & row_ids)

    def checkpoint():
        if cancelled():
            raise TimeoutError('Portfolio diversity search was cancelled')
        return time.perf_counter() < deadline

    def verified(selected):
        if len(selected) != limits['requested']:
            return None
        if valid_portfolio(selected, retained, meta, conflicts, limits, group_ok,
                           conflict_groups=conflict_groups):
            return selected
        return None

    ranked = sorted(pool, key=score)
    rank = {id(lu): position for position, lu in enumerate(ranked)}
    if not checkpoint():
        return None

    # Keep all existing hard limits in the coverage pass. The two stable orders
    # trade rank within the same conflict-degree band to escape greedy traps.
    signatures = {id(lu): tuple(meta[id(lu)]['signature']) for lu in rows}
    orders = (
        sorted(pool, key=lambda lu: (degree[id(lu)], -rank[id(lu)], signatures[id(lu)])),
        sorted(pool, key=lambda lu: (degree[id(lu)], rank[id(lu)], signatures[id(lu)])),
    )

    for ordered in orders:
        if not checkpoint():
            break
        selected = list(retained)
        selected_ids = {id(lu) for lu in retained}
        blocked = set(selected_ids)
        counts = {name: Counter() for name in ('total', 'captain', 'team', 'game')}
        specialist = 0
        kicker_captains = 0

        # Retained lineups are fixed and must themselves satisfy every hard rule.
        valid_seed = True
        for lineup in retained:
            if not checkpoint():
                valid_seed = False
                break
            row_id = id(lineup)
            data = meta[row_id]
            keys = data['keys']
            if (not keys or not group_ok(keys)
                    or (group_list and any(
                        (group_list[group_index] & selected_ids) - {row_id}
                        for group_index in groups_by_id[row_id]))
                    or (not group_list and conflicts.get(row_id, set()) & (selected_ids - {row_id}))):
                valid_seed = False
                break
            counts['total'].update(keys)
            counts['captain'].update([data['captain_key']] if data['captain_key'] else [])
            counts['team'].update(data['teams'])
            counts['game'].update(data['games'])
            specialist += int(data['specialist_captain'])
            kicker_captains += int(data.get('kicker_captain', False))
            if any(value is not None and counts['total'][key] > value
                   for key, value in limits['total'].items()):
                valid_seed = False
                break
            if any(value is not None and counts['captain'][key] > value
                   for key, value in limits['captain'].items()):
                valid_seed = False
                break
            if limits['team'] is not None and any(value > limits['team'] for value in counts['team'].values()):
                valid_seed = False
                break
            if limits['game'] is not None and any(value > limits['game'] for value in counts['game'].values()):
                valid_seed = False
                break
            if limits['specialist'] is not None and specialist > limits['specialist']:
                valid_seed = False
                break
            if limits.get('kicker_captain') is not None and kicker_captains > limits['kicker_captain']:
                valid_seed = False
                break
            if group_list:
                for group_index in groups_by_id[row_id]:
                    blocked.update(group_list[group_index])
            else:
                blocked.update(conflicts.get(row_id, set()) & row_ids)
        if not valid_seed:
            return None

        for lineup in ordered:
            if len(selected) >= limits['requested']:
                return verified(selected)
            if not checkpoint():
                break
            row_id = id(lineup)
            if row_id in blocked:
                continue
            data = meta[row_id]
            keys = data['keys']
            captain_key = data['captain_key']
            if not keys or not group_ok(keys):
                continue
            if any(limits['total'].get(key) is not None and counts['total'][key] >= limits['total'][key]
                   for key in keys):
                continue
            if captain_key and limits['captain'].get(captain_key) is not None and counts['captain'][captain_key] >= limits['captain'][captain_key]:
                continue
            if limits['team'] is not None and any(counts['team'][team] >= limits['team'] for team in data['teams']):
                continue
            if limits['game'] is not None and any(counts['game'][game] >= limits['game'] for game in data['games']):
                continue
            if data['specialist_captain'] and limits['specialist'] is not None and specialist >= limits['specialist']:
                continue
            if data.get('kicker_captain') and limits.get('kicker_captain') is not None and kicker_captains >= limits['kicker_captain']:
                continue

            selected.append(lineup)
            selected_ids.add(row_id)
            counts['total'].update(keys)
            if captain_key:
                counts['captain'][captain_key] += 1
            counts['team'].update(data['teams'])
            counts['game'].update(data['games'])
            specialist += int(data['specialist_captain'])
            kicker_captains += int(data.get('kicker_captain', False))
            blocked.add(row_id)
            for group_index in groups_by_id[row_id]:
                blocked.update(group_list[group_index])
            if not group_list:
                blocked.update(conflicts.get(row_id, set()) & row_ids)
        if len(selected) >= limits['requested']:
            return verified(selected)
    return None


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
        if limits.get('kicker_captain') is not None:
            problem += pulp.lpSum(variables[id(lu)] for lu in all_rows if meta[id(lu)].get('kicker_captain', False)) <= limits['kicker_captain']
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
