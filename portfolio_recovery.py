"""AR-02: bounded recovery of automatic caps on an already scored bank."""
from collections import Counter
from copy import deepcopy
import math
import time

CONCENTRATION_WARNING = ('Portfolio concentration increased to complete the request by increasing automatic guardrails. '
                         'All explicit user rules were preserved.')


def explicit_lineup_ok(keys, captain_key, players):
    for key, player in players.items():
        if player.get('LockFlex') and (key not in keys or key == captain_key):
            return False
        if player.get('LockCpt') and key != captain_key:
            return False
        if key in keys and player.get('FadeCpt' if key == captain_key else 'FadeFlex'):
            return False
    return True


def automatic_caps(limits, total_keys, captain_keys):
    return {
        'total': {key: limits['total'][key] for key in sorted(total_keys)},
        'captain': {key: limits['captain'][key] for key in sorted(captain_keys)},
        'specialist': limits['specialist'],
    }


def recovery_diagnostics(requested, strict_count, starting):
    return dict(requested_count=requested, strict_selected_count=strict_count,
                automatic_recovery_ran=False, starting_automatic_caps=deepcopy(starting),
                effective_automatic_caps=deepcopy(starting), recovery_stage='none',
                attempted_stages=[], final_selected_count=strict_count)


def recover(pool, retained, selected, meta, conflicts, limits, group_ok, score, *,
            total_keys, captain_keys, diagnostics, deadline, cancelled, conflict_groups=None):
    """Try three cap ceilings; explicit limits and candidates never change.

    The final stage permits up to the request, but commits only the increases
    actually needed by the first complete valid portfolio. All stages share
    one deadline, and cancellation is checked before and after every solve.
    """
    from portfolio_feasibility import repair, valid_portfolio
    starting = diagnostics['starting_automatic_caps']
    requested = limits['requested']
    if not total_keys and not captain_keys and starting['specialist'] is None:
        return None, limits
    for stage, factor in [('ceil+10%', 1.10), ('ceil+25%', 1.25), ('required', None)]:
        if cancelled():
            raise ValueError('Selection cancelled')
        if time.perf_counter() >= deadline:
            break
        trial = deepcopy(limits)
        def increased(cap):
            return requested if factor is None else min(requested, math.ceil(cap * factor - 1e-9))
        for label, keys in [('total', total_keys), ('captain', captain_keys)]:
            for key in keys:
                trial[label][key] = increased(starting[label][key])
        if starting['specialist'] is not None:
            trial['specialist'] = increased(starting['specialist'])
        diagnostics['automatic_recovery_ran'] = True
        diagnostics['attempted_stages'].append(stage)
        result = repair(pool, retained, selected, meta, conflicts, trial, group_ok, score,
                        seconds=max(0, deadline - time.perf_counter()),
                        cancelled=cancelled, conflict_groups=conflict_groups)
        if cancelled():
            raise ValueError('Selection cancelled')
        if result is None or not valid_portfolio(result, retained, meta, conflicts, trial, group_ok):
            continue
        total = Counter(key for lu in result for key in meta[id(lu)]['keys'])
        captain = Counter(meta[id(lu)]['captain_key'] for lu in result)
        for label, counts, keys in [('total', total, total_keys), ('captain', captain, captain_keys)]:
            for key in keys:
                trial[label][key] = max(starting[label][key], counts[key])
        if starting['specialist'] is not None:
            trial['specialist'] = max(starting['specialist'], sum(meta[id(lu)]['specialist_captain'] for lu in result))
        diagnostics.update(recovery_stage=stage, final_selected_count=len(result),
                           effective_automatic_caps=automatic_caps(trial, total_keys, captain_keys))
        return result, trial
    return None, limits


def format_recovery(report):
    if not report:
        return []
    lines = [f"Portfolio recovery: requested {report['requested_count']}; strict selected "
             f"{report['strict_selected_count']}; automatic recovery "
             f"{'ran' if report['automatic_recovery_ran'] else 'not needed / not run'}; "
             f"stage {report['recovery_stage']}; final selected {report['final_selected_count']}."]
    starting = report['starting_automatic_caps']
    lines.append('Starting automatic caps (lineups): total ' + ', '.join(map(str, sorted(set(starting['total'].values()))))
                 + '; Captain ' + ', '.join(map(str, sorted(set(starting['captain'].values()))))
                 + f"; combined K/DST Captain {starting['specialist']}.")
    for label, title in [('total', 'Player total'), ('captain', 'Captain')]:
        start = report['starting_automatic_caps'][label]
        end = report['effective_automatic_caps'][label]
        changes = [f'{key}: {start[key]} -> {end[key]}' for key in start if start[key] != end[key]]
        if changes:
            lines.append(f"Automatic {title} caps (lineups): " + '; '.join(changes))
    start = report['starting_automatic_caps']['specialist']
    end = report['effective_automatic_caps']['specialist']
    if start != end:
        lines.append(f'Automatic combined K/DST Captain cap: {start} -> {end} lineups.')
    return lines


def aggregate_recovery(report):
    """Keep build-history diagnostics aggregate-only, as its export promises."""
    if not report:
        return {}
    result = deepcopy(report)
    keys = sorted(set(report['starting_automatic_caps']['total']) | set(report['starting_automatic_caps']['captain']))
    labels = {key: f'player {index + 1}' for index, key in enumerate(keys)}
    for field in ('starting_automatic_caps', 'effective_automatic_caps'):
        for role in ('total', 'captain'):
            result[field][role] = {labels[key]: value for key, value in report[field][role].items()}
    return result
