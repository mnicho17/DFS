"""Opt-in starting-QB fade coverage and explicitly conditional point review.

No injury likelihood, replacement promotion, payout estimate or forecast write.
"""
import math
from collections import Counter
from portfolio_rules import player_key, lineup_players, lineup_captain


def normalize_coverage(raw):
    if not isinstance(raw, dict) or not raw.get('enabled'):
        return None
    a, b = str(raw.get('qb_a') or ''), str(raw.get('qb_b') or '')
    if not a or not b or a == b:
        raise ValueError('QB coverage requires two distinct starting quarterbacks.')
    result = dict(enabled=True, qb_a=a, qb_b=b)
    if raw.get('kind'):
        if raw['kind'] not in ('classic','showdown'):
            raise ValueError('Unsupported QB coverage format.')
        result['kind'] = raw['kind']
    if raw.get('slate_id'):
        result['slate_id'] = str(raw['slate_id'])
    for key in ('fade_a', 'fade_b', 'neither'):
        value = raw.get(key, 0)
        try:
            count = int(value)
        except (TypeError, ValueError, OverflowError):
            raise ValueError('QB fade targets must be nonnegative whole entry counts.') from None
        if isinstance(value, bool) or count != value or count < 0:
            raise ValueError('QB fade targets must be nonnegative whole entry counts.')
        result[key] = count
    return result


def coverage_limits(config, requested):
    config = normalize_coverage(config)
    if not config:
        return []
    if any(config[key] > requested for key in ('fade_a', 'fade_b', 'neither')):
        raise ValueError('QB fade targets cannot exceed the requested lineup count.')
    return [dict(label=label, excluded=keys, minimum=config[label]) for label, keys in (
        ('fade_a', [config['qb_a']]), ('fade_b', [config['qb_b']]),
        ('neither', [config['qb_a'], config['qb_b']])) if config[label]]


def slate_identity(players):
    from build_snapshots import fingerprint
    return fingerprint(sorted((player_key(p),str(p.get('Team') or ''),str(p.get('GameKey') or p.get('GameInfo') or '')) for p in players))


def coverage_ok(rows, meta, limits):
    return all(sum(not set(item['excluded']).intersection(meta[id(lu)]['keys']) for lu in rows)
               >= item['minimum'] for item in limits)


def coverage_report(rows, players, config, kind='showdown'):
    config = normalize_coverage(config)
    if not config:
        return {}
    lookup = {player_key(p): p for p in players}
    qbs = [lookup.get(config[key]) or dict(FlexNamePlusID=config[key], Name=config[key], Position='QB', Team=None)
           for key in ('qb_a', 'qb_b')]
    if any(str(p.get('Position') or '').upper() != 'QB' or p.get('NFLQBEligible') is False for p in qbs):
        raise ValueError('QB coverage selections must be eligible starting quarterbacks from this slate. Reopen QB Coverage after refreshing players.')
    n = len(rows)
    counts = Counter()
    roles = [Counter(), Counter()]
    dependencies = [0, 0]
    for lu in rows:
        roster = lineup_players(lu, kind)
        keys = {player_key(p) for p in roster}
        present = [player_key(p) in keys for p in qbs]
        counts['both' if all(present) else 'a_only' if present[0] else 'b_only' if present[1] else 'neither'] += 1
        cpt = lineup_captain(lu, kind)
        for i, qb in enumerate(qbs):
            if present[i]:
                roles[i]['captain' if cpt and player_key(cpt) == player_key(qb) else 'non_captain'] += 1
            dependencies[i] += any(qb.get('Team') and p.get('Team') == qb.get('Team') and str(p.get('Position') or '').upper() in ('WR', 'TE') for p in roster)
    achieved = dict(fade_a=n-counts['both']-counts['a_only'], fade_b=n-counts['both']-counts['b_only'], neither=counts['neither'])
    return dict(entries=n, counts={key: counts[key] for key in ('both', 'a_only', 'b_only', 'neither')},
                percentages={key: 100*counts[key]/max(1,n) for key in ('both','a_only','b_only','neither')},
                qbs=[dict(key=player_key(p), name=p.get('Name'), team=p.get('Team'),
                          captain=roles[i]['captain'], non_captain=roles[i]['non_captain'],
                          receiver_dependent_entries=dependencies[i] if p.get('Team') else None) for i,p in enumerate(qbs)],
                targets={key: dict(requested=config[key], achieved=achieved[key], shortage=max(0,config[key]-achieved[key]))
                         for key in ('fade_a', 'fade_b', 'neither')})


def paired_point_review(rows, players, config, *, elapsed=.25, receiver_efficiency=.75,
                        scenarios=250, seed=481516, kind='showdown', cancelled=lambda: False):
    """Same frozen draws for normal/A-exit/B-exit; uniform production assumption.

    WR/TE receive the explicit remaining-efficiency factor. RB/K/DST effects
    and replacement opportunities are unmodeled; never call this cash coverage.
    """
    config = normalize_coverage(config)
    if not config:
        raise ValueError('Select starting QBs for the conditional comparison.')
    if not all(math.isfinite(float(v)) and 0 <= float(v) <= 1 for v in (elapsed, receiver_efficiency)):
        raise ValueError('Elapsed fraction and remaining receiver efficiency must be between 0 and 1.')
    from nfl_simulation import _scenario_outcomes, _quantile
    import random
    import copy
    pool = copy.deepcopy(players)
    if not {config['qb_a'],config['qb_b']} <= {player_key(p) for p in pool}:
        raise ValueError('Selected quarterbacks are missing from the current slate.')
    coverage = coverage_report(rows, pool, config, kind)
    rng = random.Random(seed)
    scores = [[], [], []]
    threshold_hits = [0, 0, 0]
    completed = 0
    def score(lu, outcomes):
        cpt = lineup_captain(lu, kind)
        return sum(outcomes[player_key(p)] * (1.5 if cpt is p else 1) for p in lineup_players(lu,kind))
    for _ in range(max(1,int(scenarios))):
        if cancelled():
            break
        outcomes = _scenario_outcomes(rng,pool)
        baseline = [score(lu,outcomes) for lu in rows]
        threshold = _quantile(baseline,.75) if baseline else 0
        for case in range(3):
            changed = dict(outcomes)
            if case:
                qb = coverage['qbs'][case-1]
                changed[qb['key']] *= elapsed
                for p in pool:
                    if p.get('Team') == qb['team'] and str(p.get('Position') or '').upper() in ('WR','TE'):
                        changed[player_key(p)] *= elapsed + (1-elapsed)*receiver_efficiency
            values = baseline if case == 0 else [score(lu,changed) for lu in rows]
            scores[case].extend(values)
            threshold_hits[case] += sum(value >= threshold for value in values)
        completed += 1
    return dict(coverage=coverage, scenarios=completed, cancelled=bool(cancelled()),
                elapsed_fraction=elapsed, remaining_wr_te_efficiency=receiver_efficiency,
                competitive_threshold='Normal portfolio 75th-percentile points in each paired draw; includes ties. Not a paid-finish threshold.',
                cases=[dict(label=label, mean_entry_points=sum(values)/max(1,len(values)),
                            mean_change=(sum(values)-sum(scores[0]))/max(1,len(values)),
                            competitive_entry_pct=100*threshold_hits[i]/max(1,len(values)))
                       for i,(label,values) in enumerate(zip(('Normal','QB A exits','QB B exits'),scores))],
                limitations='Conditional point sensitivity, not injury probabilities or cash estimates. Uniform production before exit; explicit WR/TE efficiency afterwards. RB receiving, K/DST effects and replacement playing time are not modeled. Backup allocation is unavailable.')


def format_coverage(report):
    lines = [f"QB coverage: {report['entries']} entries (repeated entries counted)."]
    labels = dict(both='Both QBs',a_only='QB A only',b_only='QB B only',neither='Neither QB',fade_a='Without QB A',fade_b='Without QB B')
    lines.extend(f"{labels[key]}: {value} ({report['percentages'][key]:.1f}%)" for key,value in report['counts'].items())
    lines.extend(f"{p['name']}: Captain {p['captain']}, non-Captain {p['non_captain']}; WR/TE dependence in {p['receiver_dependent_entries'] if p['receiver_dependent_entries'] is not None else 'unknown'} entries."
                 for p in report['qbs'])
    lines.extend(f"{labels[key]}: requested {value['requested']}, achieved {value['achieved']}, shortage {value['shortage']}"
                 for key,value in report['targets'].items())
    return '\n'.join(lines)


def seed_showdown_fades(worker, players, bank, retained_keys, budget, deadline):
    """Bounded generation on copied inputs; every ordinary rule remains applied.

    Caller filters salary and scores all additions before selecting. No library
    expansion and no temporary fade metadata in returned lineups.
    """
    import time
    import copy
    from optimizers import ShowdownOptimizer, ShowdownLineup, attach_showdown_metrics
    from showdown_simulation import showdown_signature
    config = normalize_coverage(worker.portfolio_rules.get('qb_coverage'))
    if not config or getattr(worker,'candidate_library','') or worker.library_candidates:
        return 0
    original = {player_key(p):p for p in players}
    added = 0
    for index,item in enumerate(coverage_limits(config,worker.num_lineups)):
        if worker._cancel_event.is_set() or time.perf_counter() >= deadline or len(bank) >= budget:
            break
        if any(original[key].get('LockCpt') or original[key].get('LockFlex') for key in item['excluded']):
            continue
        copied = copy.deepcopy(players)
        for p in copied:
            if player_key(p) in item['excluded']:
                p['FadeCpt'] = p['FadeFlex'] = True
        opt = ShowdownOptimizer(copied,salary_cap=worker.salary_cap,seed=73129+index,
                               own_mode=worker.own_mode,own_weight=worker.own_weight,build_style=worker.build_style)
        excluded = {(sig[0][4:],tuple(sig[1:])) for sig in set(bank)|retained_keys}
        target = min(budget-len(bank), max(12,min(300,item['minimum']*2)))
        rows = opt._build_lineups_fast(num_lineups=target,excluded_signatures=excluded,
                                     cancel_callback=lambda: worker._cancel_event.is_set() or time.perf_counter() >= deadline)
        for row in rows:
            lu = ShowdownLineup(original[player_key(row['Captain'])],[original[player_key(p)] for p in row['Flex']])
            sig = showdown_signature(lu)
            if sig not in bank and sig not in retained_keys and len(bank)<budget:
                bank[sig] = attach_showdown_metrics([lu],worker.salary_cap)[0]
                added += 1
    return added
