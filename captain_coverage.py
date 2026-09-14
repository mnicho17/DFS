"""Bounded Showdown Captain exploration; never a final-portfolio exposure floor."""
import copy
import math
import time
from collections import Counter

from lineup_ranking import finish_rank
from nfl_simulation import player_key
from optimizers import ShowdownLineup, ShowdownOptimizer, _proj, _cpt_salary, _max_count_from_pct, attach_showdown_metrics


def captain_targets(players):
    """Use pre-game role evidence, not ownership or subsequent contest outcomes."""
    from nfl_eligibility import depth, eligible_players, unavailable
    allowed = {player_key(p) for p in eligible_players(players)}
    locks = {player_key(p) for p in players if p.get('LockCpt')}
    targets = []
    for p in players:
        key = player_key(p)
        if key not in allowed or unavailable(p) or p.get('FadeCpt') or p.get('LockFlex'):
            continue
        if locks and key not in locks:
            continue
        if any(_max_count_from_pct(p.get(k), 100) == 0 for k in ('MaxPct', 'MaxCptPct')):
            continue
        if not math.isfinite(_proj(p)) or _proj(p) <= 0 or _cpt_salary(p) <= 0:
            continue
        pos = str(p.get('Position') or '').upper()
        order = depth(p)
        supported = (pos in ('K', 'DST') or pos == 'QB' and (p.get('NFLQBEligible') is True or order == 1)
            or pos in ('RB', 'TE') and 1 <= order <= 2 or pos == 'WR' and 1 <= order <= 3)
        supplied = p.get('ProjectionSource') in ('Imported forecast', 'Manual override')
        if supported or supplied or key in locks:
            targets.append(p)
    return sorted(targets, key=lambda p: (-_proj(p), player_key(p)))


def seed_captains(players, targets, bank, retained_keys, budget, *, salary_cap, own_mode, own_weight,
                  deadline, cancelled, progress=lambda text: None):
    """Spend at most 10% of the candidate budget and the caller's short time slice."""
    from showdown_simulation import showdown_signature
    originals = {player_key(p): p for p in players}
    allowance = min(max(0, int(budget) // 10), 12 * len(targets))
    added = 0
    for index, target in enumerate(targets):
        if added >= allowance or len(bank) >= budget or cancelled() or time.perf_counter() >= deadline:
            break
        count = min(12, math.ceil((allowance - added) / (len(targets) - index)), budget - len(bank))
        end = time.perf_counter() + max(0, deadline - time.perf_counter()) / (len(targets) - index)
        copied = copy.deepcopy(players)
        for p in copied:
            p['LockCpt'] = player_key(p) == player_key(target)
        optimizer = ShowdownOptimizer(copied, salary_cap=salary_cap, seed=41000 + index,
            own_mode=own_mode, own_weight=own_weight, build_style='Strategic')
        excluded = {(sig[0][4:], tuple(sig[1:])) for sig in set(bank) | set(retained_keys)}
        progress('Captain coverage: ' + str(target.get('Name')))
        rows = optimizer._build_lineups_fast(num_lineups=count, excluded_signatures=excluded,
            cancel_callback=lambda: cancelled() or time.perf_counter() >= end)
        for row in rows:
            # Temporary search locks must not leak into simulation or portfolio constraints.
            lu = ShowdownLineup(originals[player_key(row['Captain'])], [originals[player_key(p)] for p in row['Flex']])
            sig = showdown_signature(lu)
            if sig not in bank and sig not in retained_keys and len(bank) < budget:
                bank[sig] = attach_showdown_metrics([lu], salary_cap)[0]
                added += 1
    return added


def shortlist_reservations(lineups, targets, limit, retained_keys):
    from showdown_simulation import showdown_signature
    available = {showdown_signature(lu): lu for lu in lineups}
    reserved = set(retained_keys) & set(available)
    extra = set()
    allowance = min(max(0, int(limit) // 5), max(0, int(limit) - len(reserved)))
    grouped = {player_key(p): [] for p in targets}
    for lu in sorted(lineups, key=finish_rank, reverse=True):
        key = player_key(lu['Captain'])
        if key in grouped and showdown_signature(lu) not in reserved:
            grouped[key].append(lu)
    # Three rounds distribute scarce slots before giving any Captain a second/third.
    for index in range(3):
        for p in targets:
            group = grouped[player_key(p)]
            if index < len(group) and len(extra) < allowance:
                extra.add(showdown_signature(group[index]))
    return reserved | extra, len(extra)


def coverage_report(targets, generated, shortlisted, validated, selected, *, validation_complete,
                    seeded, reserved, library):
    def counts(rows):
        return Counter(player_key(lu['Captain']) for lu in rows)
    stages = [counts(rows) for rows in (generated, shortlisted, validated if validation_complete else [], selected)]
    best = {}
    if validation_complete:
        for rank, lu in enumerate(sorted(validated, key=finish_rank, reverse=True), 1):
            best.setdefault(player_key(lu['Captain']), (rank, getattr(lu, 'sim_metrics', {})))
    rows = []
    for p in targets:
        key = player_key(p)
        gen, short, full, chosen = [stage[key] for stage in stages]
        reason = ('Selected' if chosen and full else 'Selected from incomplete validation' if chosen else
            'No generated candidate: time, budget or feasibility' if not gen else
            'Not retained in limited shortlist' if not short else
            'Independent validation incomplete or unavailable' if not full else 'Fully evaluated; not selected')
        rank, metrics = best.get(key, (None, {}))
        rows.append(dict(player=str(p.get('Name')),team=p.get('Team'),position=p.get('Position'),
            generated=gen,shortlisted=short,validated=full,selected=chosen,best_rank=rank,
            best_top1=metrics.get('sim_top_one_pct'),best_first=metrics.get('sim_win_rate'),reason=reason))
    return dict(rows=rows,seeded=seeded,reserved=reserved,validation_complete=validation_complete,
        library=library,note='Search coverage only; no final exposure minimum. Up to 12 seeded candidates per Captain within 10% of the candidate budget and a short time slice; up to three shortlist reservations per Captain within 20% of shortlist capacity. Retained entries take priority. Limited budgets may leave gaps. Saved-library generation is unchanged.')


def format_coverage(report):
    if not report:
        return []
    lines=['', 'Showdown Captain coverage', '- ' + report['note'],
        f"- Eligible review Captains: {len(report['rows'])}; coverage candidates added: {report['seeded']}; shortlist reservations: {report['reserved']}. Full independent validation: {'complete' if report['validation_complete'] else 'incomplete/unavailable'}."]
    for r in report['rows']:
        result = (f"; best tested rank #{r['best_rank']}; top-1% {r['best_top1']:.2f}%; first including ties {r['best_first']:.2f}%"
            if r['best_rank'] is not None else '')
        lines.append(f"- {r['player']} [{r['team']} {r['position']}]: generated {r['generated']}; shortlisted {r['shortlisted']}; fully evaluated {r['validated']}; selected {r['selected']}{result}. {r['reason']}.")
    lines.append('- Review eligibility: positive forecast and recorded QB eligibility, RB/TE depth 1–2, WR depth 1–3, K/DST, supplied forecast or explicit Captain lock; active-player checks and personal exclusions still apply. Rates compare tested candidates within the model, not proven winning probabilities. Reservations affect both output-selection modes; final selection rules are unchanged.')
    return lines
