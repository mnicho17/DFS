"""Bounded candidate retries and explicit-rule-preserving portfolio fallback."""
import copy
import math
import time
from collections import Counter

from portfolio_rules import (
    _candidate_signature, _pct, _report_text, normalize_rules, player_key,
    select_portfolio,
)
from selection_shortage import PortfolioSelectionShortage


def select_with_fallback(candidates, requested, *, deadline=None,
                         progress_callback=lambda text: None, **options):
    """Try strict selection first, then ease only automatic Showdown caps.

    All attempts use the same scored candidates. New candidates must enter the
    normal simulation stages before this function; cancellation is never retryable.
    Configured uniqueness, explicit maxima, groups and retained rows stay fixed.
    """
    candidates = list(candidates)
    options = dict(options, allow_relaxation=False)
    cancelled = options.get('selection_cancel_callback') or (lambda: False)

    def checkpoint():
        if cancelled():
            raise ValueError('Selection cancelled')

    checkpoint()
    try:
        result = select_portfolio(candidates, requested, **options)
        checkpoint()
        return result
    except PortfolioSelectionShortage as original:
        checkpoint()
        automatic = (str(options.get('kind', 'classic')).lower() == 'showdown'
                     and normalize_rules(options.get('rules'))['balance_ownership']
                     and not options.get('individual_ranking'))
        if not automatic:
            raise
        end = min(deadline if deadline is not None else float('inf'),
                  time.perf_counter() + 15)
        steps = sorted({max(1, math.ceil(requested * .10)),
                        max(1, math.ceil(requested * .25)), requested})
        attempts = []
        for index, increase in enumerate(steps):
            checkpoint()
            remaining = end - time.perf_counter()
            if remaining <= 0:
                break
            progress_callback('Fallback: easing automatic exposure targets; explicit rules stay fixed')
            checkpoint()
            attempts.append(increase)
            trial = dict(options, automatic_cap_increase=increase,
                         repair_time_limit=min(5, remaining / (len(steps) - index)),
                         selection_deadline=end)
            previous_stop = options.get('refinement_stop_callback')
            trial['refinement_stop_callback'] = lambda: (
                time.perf_counter() >= end or bool(previous_stop and previous_stop()))
            try:
                result = select_portfolio(candidates, requested, **trial)
            except PortfolioSelectionShortage:
                continue
            except TimeoutError:
                break
            checkpoint()
            report = result['report']
            caps = report['automatic_showdown_guardrails']
            report['automatic_fallback'] = dict(status='completed', attempts=attempts,
                requested=requested, cap_increase=increase)
            report['warnings'].append(
                f"Automatic fallback completed {len(result['lineups'])}/{requested} lineups. "
                f"Automatic total/Captain/K-DST Captain limits are now "
                f"{caps['effective_total_max_count']}/{requested}, "
                f"{caps['effective_captain_max_count']}/{requested}, "
                f"{caps['effective_specialist_captain_max_count']}/{requested}. "
                "Explicit limits, configured uniqueness, groups and retained entries were unchanged. "
                "Review the increased concentration before using this portfolio.")
            report['text'] = _report_text(report)
            return result
        checkpoint()
        raise PortfolioSelectionShortage(str(original) + '\n\n'
            + f'Automatic fallback tried {len(attempts)} bounded recovery stages without finding a complete portfolio. '
            + 'No trial portfolio was applied; your explicit rules remain unchanged.') from original


def expand_candidates(worker, players, rows, *, deadline=None, max_extra=600):
    """Explore alternatives before simulation; never change the input player tags."""
    cancelled = worker._cancel_event.is_set
    end = min(deadline if deadline is not None else float('inf'), time.perf_counter() + 20)
    if cancelled() or end <= time.perf_counter() or max_extra <= 0:
        return []
    worker.progress.emit(len(rows), max(len(rows), worker.num_lineups),
        'Exploring additional candidates before simulation (up to 20 seconds; Cancel available)')
    if worker.kind == 'showdown':
        from showdown_coverage import expand_capped_candidates
        rules = dict(worker.portfolio_rules)
        if (worker.compute_mode.casefold().startswith('deep')
                and worker.deep_options['selection_mode'] == 'Individual ranking'):
            rules['balance_ownership'] = False
        return expand_capped_candidates(rows, players, worker.num_lineups, rules,
            salary_cap=worker.salary_cap, own_mode=worker.own_mode,
            own_weight=worker.own_weight, build_style=worker.build_style,
            cancelled=cancelled, seconds=max(0, end-time.perf_counter()), max_extra=max_extra)

    from optimizers import MultiSportClassicOptimizer
    rules = normalize_rules(worker.portfolio_rules)
    counts = Counter(key for row in rows for key in {player_key(p) for p in row})
    targets = []
    for player in players:
        key = player_key(player)
        constraint = dict(player)
        constraint.update({k:v for k,v in rules['player_constraints'].get(key, {}).items()
                           if v not in (None, '')})
        cap = _pct(constraint.get('MaxPct'), 100)
        if (not player.get('LockFlex') and not constraint.get('LockFlex')
                and not player.get('FadeFlex') and not constraint.get('FadeFlex')
                and 0 < cap < 100 and counts[key] / max(1, len(rows)) * 100 > cap):
            targets.append(key)
    if len(rows) + len(worker.retained_lineups) < worker.num_lineups:
        targets.insert(0, None)
    seen = {_candidate_signature(row, 'classic') for row in rows + list(worker.retained_lineups)}
    retained = [_candidate_signature(row, 'classic') for row in worker.retained_lineups]
    originals = {player_key(p): p for p in players}
    extra = []
    for index, key in enumerate(targets[:4]):
        if cancelled() or time.perf_counter() >= end or len(extra) >= max_extra:
            break
        probe = copy.deepcopy(players)
        for player in probe:
            if player_key(player) == key:
                player['FadeFlex'] = True
        optimizer = MultiSportClassicOptimizer(probe, sport=worker.sport,
            salary_cap=worker.salary_cap, seed=91337+index, own_mode=worker.own_mode,
            own_weight=worker.own_weight, build_style=worker.build_style,
            mlb_stack_pref=worker.mlb_stack_pref, salary_strategy=worker.salary_strategy)
        found = optimizer.build_lineups(num_lineups=min(max_extra-len(extra), max(21, min(150, worker.num_lineups))),
            cancel_callback=lambda: cancelled() or time.perf_counter() >= end,
            excluded_signatures=retained, exact_excluded_signatures=seen,
            minimum_unique=rules['min_unique'])
        for row in found:
            signature = _candidate_signature(row, 'classic')
            if signature not in seen and len(extra) < max_extra:
                seen.add(signature)
                extra.append([originals[player_key(p)] for p in row])
    return extra
