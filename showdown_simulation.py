"""NFL Showdown Deep exploration and shared-outcome contest simulation."""
import bisect
import math
import random
import time
from array import array
from collections import Counter

from lineup_ranking import search_jobs, ranked_lineups, finish_rank
from compute_settings import deep_candidate_budget, deep_search_seeds, deep_phase_fractions
from game_day_safety import UNAVAILABLE_STATUSES, _status
from nfl_simulation import _scenario_outcomes, _quantile, player_key
from optimizers import (ShowdownOptimizer, ShowdownLineup, attach_showdown_metrics,
                        _salary, _cpt_salary, _showdown_cpt_own, _showdown_flex_own)
from portfolio_rules import select_portfolio
from selection_shortage import PortfolioSelectionShortage, TITLE as LIMITS_TITLE


def salary_floor(salary_cap, strategy):
    text = str(strategy or '').casefold()
    if 'max' in text:
        return max(0, salary_cap - 500)
    if 'near cap' in text:
        return max(0, salary_cap - 2500)
    return 0


def filter_salary_candidates(rows, salary_cap, strategy):
    floor = salary_floor(salary_cap, strategy)
    return [lu for lu in rows if floor <= _cpt_salary(lu['Captain']) + sum(_salary(p) for p in lu['Flex']) <= salary_cap]


def showdown_signature(lineup):
    return ("CPT:" + player_key(lineup["Captain"]),) + tuple(sorted(player_key(p) for p in lineup["Flex"]))


def showdown_score(lineup, outcomes):
    return 1.5 * outcomes[player_key(lineup["Captain"])] + sum(outcomes[player_key(p)] for p in lineup["Flex"])


def validate_showdown_lineup(lineup, players, salary_cap):
    roster = [lineup.get("Captain") or {}] + list(lineup.get("Flex") or [])
    keys = {player_key(p) for p in roster}
    if (len(roster) != 6 or len(keys) != 6 or "" in keys
            or not keys.issubset({player_key(p) for p in players})
            or len({p.get("Team") for p in roster}) != 2
            or _cpt_salary(roster[0]) + sum(_salary(p) for p in roster[1:]) > salary_cap):
        raise ValueError("Showdown SIM requires a current-slate Captain, five distinct FLEX athletes, both teams, and a legal salary total.")


def active_showdown_players(players):
    from nfl_eligibility import eligible_players
    players = eligible_players(players)
    unique = {}
    for raw in players:
        if raw.get("NFLQBEligible") is False:
            continue
        p = dict(raw)
        if _status(p) in UNAVAILABLE_STATUSES:
            if p.get("LockCpt") or p.get("LockFlex"):
                raise ValueError("A locked Showdown player is unavailable: " + str(p.get("Name", "Unknown")))
            continue
        if _salary(p) > 0 and player_key(p):
            unique[player_key(p)] = p
    pool = list(unique.values())
    teams = sorted({str(p.get("Team") or "").strip().upper() for p in pool})
    if len(teams) != 2 or not all(teams):
        raise ValueError("NFL Showdown Deep requires exactly two teams from one game.")
    games = {str(p.get(key) or "").strip().upper().split()[0]
             for p in pool for key in ("GameKey", "GameInfo")
             if str(p.get(key) or "").strip()}
    if len(games) > 1:
        raise ValueError("NFL Showdown Deep requires one game.")
    for p in pool:
        p["Team"] = str(p["Team"]).strip().upper()
        expected_opponent = next(team for team in teams if team != p["Team"])
        opponent = str(p.get("Opponent") or "").strip().upper()
        if opponent and opponent != expected_opponent:
            raise ValueError("Showdown opponent information conflicts with the two slate teams.")
        p["Opponent"] = expected_opponent
        p["GameKey"] = next(iter(games)) if games else "@".join(teams)
        if not p.get("GameInfo"):
            p["GameInfo"] = p["GameKey"]
    return pool


def generate_showdown_field(players, count, *, salary_cap=50000, seed=0, cancel_callback=None, model='salary-bands-v1'):
    if model == 'legacy':
        return _generate_showdown_field_legacy(players, count, salary_cap=salary_cap, seed=seed, cancel_callback=cancel_callback)
    if model != 'salary-bands-v1':
        raise ValueError('Unknown Showdown field model')
    from showdown_field import sample_field
    pool = active_showdown_players([dict(p, LockCpt=False, LockFlex=False) for p in players])
    return sample_field(pool, count, salary_cap=salary_cap, seed=seed, cancel_callback=cancel_callback)


def _generate_showdown_field_legacy(players, count, *, salary_cap=50000, seed=0, cancel_callback=None):
    """Sample legal opponent entries independently of the user's locks and fades.

    Slot-specific ownership drives weighted draws; projections are the fallback.
    Repeated field entries are intentional so Captain-aware duplicates are retained.
    """
    rng = random.Random(seed)
    pool = active_showdown_players([dict(p, LockCpt=False, LockFlex=False) for p in players])
    captains = [p for p in pool if _cpt_salary(p) > 0]
    def weights(items, own):
        supplied = any(own(p) > 0 for p in items)
        return [max(0.05, own(p)) if supplied else max(0.1, float(p.get("FlexProjection", 0) or 0)) ** 1.3 for p in items]
    captain_weights = weights(captains, _showdown_cpt_own)
    field = []
    if len(pool) < 6 or not captains:
        return field
    for attempt in range(max(200, int(count) * 80)):
        if len(field) >= count or (cancel_callback and cancel_callback()):
            break
        captain = rng.choices(captains, weights=captain_weights, k=1)[0]
        available = [p for p in pool if player_key(p) != player_key(captain)]
        flex = []
        for _ in range(5):
            p = rng.choices(available, weights=weights(available, _showdown_flex_own), k=1)[0]
            flex.append(p)
            available = [other for other in available if player_key(other) != player_key(p)]
        salary = _cpt_salary(captain) + sum(_salary(p) for p in flex)
        if salary_cap * 0.85 <= salary <= salary_cap and len({p.get("Team") for p in [captain] + flex}) == 2:
            field.append(ShowdownLineup(captain, flex))
    return field


def simulate_showdown(candidates, players, *, scenarios, field_lineup_count, salary_cap=50000,
                      seed=90210, cancel_callback=None, progress_callback=None, field_model='salary-bands-v1', opponent_players=None, outcome_transform=None, capture_distributions=False, scenario_cache=False):
    if not candidates:
        return {"lineups": [], "report": {"scenarios": 0, "field_lineups": 0}}
    pool = active_showdown_players(players)
    for lineup in candidates:
        validate_showdown_lineup(lineup, pool, salary_cap)
    field_pool = active_showdown_players(opponent_players) if opponent_players is not None else pool
    field = generate_showdown_field(field_pool, field_lineup_count, salary_cap=salary_cap, seed=seed + 1, cancel_callback=cancel_callback, model=field_model)
    if not field:
        return {"lineups": list(candidates), "report": {"scenarios": 0, "field_lineups": 0}}
    # Three bootstrap fields share outcomes within each scenario, as in Classic.
    rng = random.Random(seed)
    bank_rng = random.Random(seed + 37)
    banks = [field] + [[bank_rng.choice(field) for _ in field] for _ in range(2)]
    field_counts = Counter(showdown_signature(lu) for lu in field)
    scores = [array("f") for _ in candidates]
    hits = [[set() for _ in candidates] for _ in range(3)]
    sums = [0.0] * len(candidates)
    returns = [0.0] * len(candidates)
    top_twos = [0] * len(candidates)
    cashes, busts = [0] * len(candidates), [0] * len(candidates)
    values = [{} for _ in candidates]
    scripts = Counter()
    from scoring_distributions import DistributionCapture
    distribution = DistributionCapture(pool, "showdown", max(1, int(scenarios)), seed) if capture_distributions and outcome_transform is None else None
    from scenario_cache import ScenarioReplay
    replay = ScenarioReplay(pool, [[showdown_signature(lu) for lu in bank] for bank in banks],
        kind='showdown', seed=seed, count=max(1,int(scenarios)),
        enabled=scenario_cache and outcome_transform is None, cancelled=cancel_callback,
        context=dict(players=list(players),opponent_players=opponent_players,field_model=field_model,salary_cap=salary_cap))
    completed = 0
    for scenario in range(max(1, int(scenarios))):
        if cancel_callback and cancel_callback():
            break
        def compute_frame():
            outcomes = _scenario_outcomes(rng, pool, script_counter=scripts)
            if outcome_transform is not None:
                outcomes = outcome_transform(outcomes)
            return outcomes, sorted(showdown_score(lu, outcomes) for lu in banks[scenario % 3])
        outcomes, ranked = replay.frame(scenario, compute_frame, scripts)
        top1, top5, cash, bust = [ranked[int(q * (len(ranked) - 1))] for q in (0.99, 0.95, 0.8, 0.4)]
        for i, lineup in enumerate(candidates):
            score = showdown_score(lineup, outcomes)
            scores[i].append(score)
            pct = bisect.bisect_right(ranked, score) / len(ranked)
            sums[i] += pct
            for group, threshold in zip(hits, (top1, top5, ranked[-1])):
                if score >= threshold:
                    group[i].add(scenario)
            top_twos[i] += score >= ranked[int(.98 * (len(ranked) - 1))]
            cashes[i] += score >= cash
            busts[i] += score < bust
            value = 16.0 if score >= ranked[-1] else 6.0 + (pct - .99) * 200 if score >= top1 else 1.5 + (pct - .95) * 75 if score >= top5 else .2 if score >= cash else -1.0
            returns[i] += value
            if value >= 1.5:
                values[i][scenario] = value
        if distribution is not None: distribution.record(outcomes)
        completed += 1
        if progress_callback and (completed % 50 == 0 or completed == scenarios):
            progress_callback(completed, scenarios, "Scoring Captain and FLEX against Showdown opponents")
    cache_report = replay.finish(completed)
    rows = []
    for i, lineup in enumerate(candidates):
        base = dict(getattr(lineup, "sim_metrics", {}) or {})
        base.update(sim_scenarios=completed, sim_field_lineups=len(field),
                    sim_top_two_pct=top_twos[i] / max(1, completed) * 100,
                    sim_top_one_pct=len(hits[0][i]) / max(1, completed) * 100,
                    sim_top_five_pct=len(hits[1][i]) / max(1, completed) * 100,
                    sim_win_rate=len(hits[2][i]) / max(1, completed) * 100,
                    sim_cash_rate=cashes[i] / max(1, completed) * 100,
                    sim_bust_rate=busts[i] / max(1, completed) * 100,
                    sim_average_percentile=sums[i] / max(1, completed) * 100,
                    sim_mean=sum(scores[i]) / max(1, completed), sim_ceiling=_quantile(scores[i], .9),
                    sim_return_score=returns[i] / max(1, completed),
                    field_exact_matches=field_counts[showdown_signature(lineup)])
        rows.append(base)
    ordered = {key: sorted(row[key] for row in rows) for key in ("sim_top_one_pct", "sim_top_five_pct", "sim_ceiling", "sim_return_score", "field_exact_matches")}
    for i, row in enumerate(rows):
        rank = lambda key: .5 if len(rows) <= 1 else (bisect.bisect_left(ordered[key], row[key]) + bisect.bisect_right(ordered[key], row[key]) - 1) / (2 * (len(rows) - 1))
        row["sim_return_index"] = 100 * rank("sim_return_score")
        row["sim_edge"] = 100 * (.40 * rank("sim_top_one_pct") + .20 * rank("sim_top_five_pct") + .15 * rank("sim_ceiling") + .15 * rank("sim_return_score") + .10 * (1 - rank("field_exact_matches")))
    result = []
    for i, raw in enumerate(candidates):
        lu = ShowdownLineup(raw["Captain"], raw["Flex"])
        lu.sim_metrics = rows[i]
        lu.candidate_source = "showdown_optimizer"
        lu.candidate_archetype = getattr(raw, "candidate_archetype", "")
        lu.sim_top_hits, lu.sim_top_five_hits, lu.sim_win_hits = (group[i] for group in hits)
        lu.sim_scenario_values = values[i]
        result.append(lu)
    from field_diagnostics import summarize_field
    from build_snapshots import fingerprint
    return {"lineups": result, "report": {
        "scenario_cache": cache_report,
        "field_diagnostic": summarize_field(field, field_pool, showdown=True, salary_cap=salary_cap),
        "sensitivity_field_id": fingerprint([showdown_signature(lu) for lu in field]) if outcome_transform is not None else None,
        "player_distributions": distribution.finish(completed) if distribution is not None else None,
        "scenarios": completed, "field_lineups": len(field), "opponent_field_samples": 3,
        "model": "showdown-shared-outcomes-v1", "field_preset": "Showdown ownership sample",
        "payout_model": "payout-shape-proxy-v1", "contest_aware": False,
        "kicker_opportunity_count": sum(p.get("ProjectionSource")=="Automatic kicker opportunities" for p in pool),
        "specialist_model": "shared-specialist-events-v1",
            "volatility_model": "role-aware-player-volatility-v1", "rare_event_model": "guardrailed-breakout-tails-v1",
        "game_script_mix": {k: v / max(1, sum(scripts.values())) * 100 for k, v in scripts.items()},
    }}


def run_deep_showdown(worker, shortlist_fn):
    start = time.perf_counter()
    limit = worker.deep_time_limit_seconds
    deadline = start + limit
    stop = lambda when: worker._cancel_event.is_set() or time.perf_counter() >= when
    options = worker.deep_options
    players = active_showdown_players(worker.players)
    retained = list(worker.retained_lineups)
    for lineup in retained:
        validate_showdown_lineup(lineup, players, worker.salary_cap)
        if not worker._cancel_event.is_set() and not filter_salary_candidates([lineup], worker.salary_cap, worker.salary_strategy):
            raise PortfolioSelectionShortage(LIMITS_TITLE + "\n\nA retained lineup is outside the selected salary strategy. Change the salary strategy or remove that retained lineup before rebuilding.")
    retained_keys = {showdown_signature(lu) for lu in retained}
    requested = max(0, worker.num_lineups - len(retained))
    budget = deep_candidate_budget(max(1, requested), options, False) if requested else 0
    bank = {showdown_signature(lu): lu for lu in getattr(worker, "library_candidates", [])}
    style_counts = {}
    seeds = deep_search_seeds(options["seeds"])
    jobs = [] if bank else search_jobs(seeds, worker.build_style, options["all_styles"])
    if bank:
        budget = len(bank)
    generation_fraction, screening_fraction = deep_phase_fractions(options)
    generation_end = start + limit * generation_fraction
    from captain_coverage import captain_targets, seed_captains, shortlist_reservations, coverage_report
    targets = captain_targets(players)
    library_build = bool(bank)
    def expand_coverage():
        if not requested or library_build or getattr(worker, 'candidate_library', '') or stop(generation_end):
            return
        from showdown_coverage import expand_capped_candidates
        coverage = expand_capped_candidates(list(bank.values()), players, worker.num_lineups,
            worker.portfolio_rules, salary_cap=worker.salary_cap, own_mode=worker.own_mode,
            own_weight=worker.own_weight, build_style=worker.build_style,
            seconds=max(0, min(20, generation_end-time.perf_counter())),
            max_additions=max(0, budget-len(bank)), retained=retained,
            salary_strategy=worker.salary_strategy,
            automatic=options['selection_mode'] != 'Individual ranking', cancelled=worker._cancel_event.is_set)
        for lu in attach_showdown_metrics(coverage, worker.salary_cap):
            key = showdown_signature(lu)
            bank.setdefault(key, lu)
            exclusions.add((key[0][4:], tuple(key[1:])))
        style_counts['Portfolio coverage'] = style_counts.get('Portfolio coverage', 0) + len(coverage)

    seeded = 0
    if requested and not library_build:
        seeded = seed_captains(players, targets, bank, retained_keys, budget,
            salary_cap=worker.salary_cap, own_mode=worker.own_mode, own_weight=worker.own_weight,
            deadline=start + min(30.0, limit * generation_fraction * .10),
            cancelled=worker._cancel_event.is_set,
            progress=lambda text: worker.progress.emit(len(bank), budget, 'Phase 1 of 4 - ' + text))
        style_counts['Captain coverage'] = seeded
    exclusions = {(sig[0][4:], tuple(sig[1:])) for sig in retained_keys}
    exclusions.update((sig[0][4:], tuple(sig[1:])) for sig in bank)
    for index, (style, seed) in enumerate(jobs):
        if not requested or len(bank) >= budget or stop(generation_end):
            break
        target = math.ceil((budget - len(bank)) / (len(jobs) - index))
        job_end = time.perf_counter() + max(0, generation_end - time.perf_counter()) / (len(jobs) - index)
        optimizer = ShowdownOptimizer(players, salary_cap=worker.salary_cap, seed=seed,
            own_mode=worker.own_mode, own_weight=worker.own_weight, build_style=style)
        rows = optimizer.build_lineups(num_lineups=target,
            excluded_signatures=exclusions,
            cancel_callback=lambda: stop(job_end),
            progress_callback=lambda done, total, text: worker.progress.emit(len(bank) + done, budget, f"Phase 1 of 4 - Showdown {style} search {index + 1}/{len(jobs)}"))
        style_counts[style] = style_counts.get(style, 0) + len(rows)
        for lu in attach_showdown_metrics(rows, worker.salary_cap):
            key = showdown_signature(lu)
            if key not in retained_keys:
                bank.setdefault(key, lu)
                exclusions.add((key[0][4:], tuple(key[1:])))
        if index == 0:
            expand_coverage()
    expand_coverage()
    from pipeline_audit import quarterback_mix, defense_mix
    qb_stages = {"generated": quarterback_mix(list(bank.values()) + retained)}
    dst_stages = {"generated": defense_mix(list(bank.values()) + retained)}
    generated = len(bank)
    eligible_salary = filter_salary_candidates(list(bank.values()), worker.salary_cap, worker.salary_strategy)
    salary_excluded = generated - len(eligible_salary)
    bank = {showdown_signature(lu): lu for lu in eligible_salary}
    dst_stages['salary_eligible'] = defense_mix(list(bank.values()) + retained)
    if generated and not bank and not retained:
        raise PortfolioSelectionShortage(LIMITS_TITLE + "\n\nNo generated Showdown candidates meet the selected salary strategy. Broaden the search or deliberately change the salary strategy.")
    generation_seconds = time.perf_counter() - start
    sim_start = time.perf_counter()
    lineups = list(bank.values())
    sim_report = {}
    feasible_fallback = []
    coverage_short = []
    coverage_validated = []
    coverage_reserved = 0
    coverage_complete = False
    deep = {"enabled": True, "time_limit_seconds": limit, "screening_scenarios": 0,
            "validation_scenarios": 0, "shortlist_count": 0, "validation_top_overlap_pct": None,
            "candidate_bank_count": generated, "validation_time_limit_reached": False}
    if lineups and not stop(start + limit * screening_fraction):
        coarse = simulate_showdown(retained + lineups, players, scenario_cache=getattr(worker,"scenario_cache",False),
            scenarios=min(options["screening"], max(250, worker.sim_scenarios)),
            field_lineup_count=min(1600, options["field"] or 1200), salary_cap=worker.salary_cap, seed=73129,
            cancel_callback=lambda: stop(start + limit * screening_fraction),
            progress_callback=lambda a,b,c: worker.progress.emit(a,b,"Phase 2 of 4 - " + c))
        deep["screening_scenarios"] = coarse["report"]["scenarios"]
        if deep["screening_scenarios"]:
            shortlist_limit = max(worker.num_lineups, options["shortlist"] or 900)
            reservations, coverage_reserved = shortlist_reservations(coarse['lineups'], targets, shortlist_limit, retained_keys)
            from feasible_shortlist import preserve
            worker.progress.emit(0, worker.num_lineups, "Phase 2 of 4 - checking portfolio feasibility (up to 20 seconds; Cancel available)")
            short, feasible_fallback, deep['portfolio_feasibility'] = preserve(
                coarse['lineups'], shortlist_fn, shortlist_limit, worker.num_lineups,
                kind='showdown', rules=worker.portfolio_rules, retained=retained,
                reserved=reservations, signature=showdown_signature,
                individual_ranking=options['selection_mode'] == 'Individual ranking',
                deadline=deadline - min(60, limit * .20), cancelled=worker._cancel_event.is_set)
            coverage_short = list(short)
            qb_stages["shortlisted"] = quarterback_mix(short, scored=True)
            dst_stages['shortlisted'] = defense_mix(short, scored=True)
            sim_report = coarse["report"]
            rank = finish_rank
            top = {showdown_signature(lu) for lu in sorted(short, key=rank, reverse=True)[:worker.num_lineups]}
            validation_end = deadline - min(60, limit * .20)
            if not stop(validation_end):
                validated = simulate_showdown(short, players, scenario_cache=getattr(worker,"scenario_cache",False), scenarios=max(2500, worker.sim_scenarios),
                    field_lineup_count=options["field"] or 2700, salary_cap=worker.salary_cap, seed=90210, capture_distributions=True,
                    cancel_callback=lambda: stop(validation_end),
                    progress_callback=lambda a,b,c: worker.progress.emit(a,b,"Phase 3 of 4 - " + c))
                if validated["report"]["scenarios"]:
                    short = validated["lineups"]
                    coverage_validated = list(short)
                    coverage_complete = validated['report']['scenarios'] == max(2500, worker.sim_scenarios)
                    qb_stages["validated"] = quarterback_mix(short, scored=True)
                    dst_stages['validated'] = defense_mix(short, scored=True)
                    sim_report = validated["report"]
                    deep["validation_scenarios"] = sim_report["scenarios"]
                    other = {showdown_signature(lu) for lu in sorted(short, key=rank, reverse=True)[:worker.num_lineups]}
                    deep["validation_top_overlap_pct"] = len(top & other) / max(1, len(top)) * 100
                deep["validation_time_limit_reached"] = time.perf_counter() >= validation_end
            deep["shortlist_count"] = len(short)
            from ranking_stability import audit_ranking
            if deep.get('validation_scenarios', 0) >= 1000:
                from repeatability import capture_bank
                deep['ranking_bank'] = capture_bank(short, players, kind='showdown',
                    salary_cap=worker.salary_cap, field_count=options['field'] or 2700,
                    input_id=getattr(worker, 'build_input_id', ''))
                deep['ranking_audit'] = audit_ranking(short,
                    lambda audit_stop: simulate_showdown(short, players, scenarios=2000,
                        field_lineup_count=options['field'] or 2700, salary_cap=worker.salary_cap,
                        seed=481516, cancel_callback=audit_stop,
                        progress_callback=lambda a,b,c: worker.progress.emit(a,b,'Phase 3 of 4 - Ranking audit: ' + c)),
                    showdown_signature, deadline=validation_end, cancelled=worker._cancel_event.is_set)
            else:
                deep['ranking_audit'] = {'status': 'skipped', 'reason': 'independent validation unavailable', 'scenarios': 0}
            retained = [lu for lu in short if showdown_signature(lu) in retained_keys]
            lineups = [lu for lu in short if showdown_signature(lu) not in retained_keys]
    from scoring_distributions import save_distribution
    save_distribution(sim_report, getattr(worker, "build_input_id", ""))
    simulation_seconds = time.perf_counter() - sim_start
    selection_start = time.perf_counter()
    worker.progress.emit(0, worker.num_lineups, "Phase 4 of 4 - selecting and refining Showdown portfolio")
    selected = select_portfolio(lineups, worker.num_lineups, kind="showdown", rules=worker.portfolio_rules,
        allow_relaxation=worker._cancel_event.is_set(),
        # Keep the existing cancelled Deep Showdown receipt path verbatim.
        automatic_recovery=not worker._cancel_event.is_set(), recovery_deadline=deadline,
        fallback_lineups=feasible_fallback,
        selection_cancel_callback=worker._cancel_event.is_set,
        repair_time_limit=max(0,min(15,deadline-time.perf_counter())),
        retained_lineups=retained, refinement_passes=256,
        refinement_stop_callback=lambda: stop(deadline), refinement_polish_duplication=True,
        individual_ranking=options["selection_mode"] == "Individual ranking")
    selected["lineups"] = ranked_lineups(selected["lineups"])
    for key in ("refinement_swaps", "duplication_refinement_swaps", "refinement_attempts", "refinement_seconds", "refinement_stop_reason"):
        deep[key] = selected["report"].get(key, "completed" if key.endswith("reason") else 0)
    deep["time_remaining_seconds"] = max(0, deadline - time.perf_counter())
    deep["time_limit_reached"] = time.perf_counter() >= deadline
    if not deep["validation_scenarios"]:
        selected["report"].setdefault("warnings", []).append("Deep Showdown did not complete independent validation; returning the best available stage.")
    qb_stages["selected"] = quarterback_mix(selected["lineups"], scored=True)
    sim_report["quarterback_pipeline"] = qb_stages
    if coverage_complete:
        dst_stages['ranked'] = defense_mix(ranked_lineups(coverage_validated)[:len(selected['lineups'])], scored=True)
    dst_stages['selected'] = defense_mix(selected['lineups'], scored=True)
    sim_report['defense_pipeline'] = dst_stages
    from ownership_strategy import leverage_report
    sim_report['ownership_leverage'] = leverage_report(retained+lineups,selected['lineups'],players,
        sim_report.get('field_diagnostic') or {},showdown=True)
    sim_report['salary_filter'] = dict(excluded=salary_excluded, minimum=salary_floor(worker.salary_cap, worker.salary_strategy))
    sim_report["candidate_library"] = getattr(worker, "library_report", {})
    from projection_coverage import summarize_projection_coverage
    sim_report["projection_coverage"] = summarize_projection_coverage(players)
    sim_report["deep_build"] = dict(deep)
    sim_report['captain_coverage'] = coverage_report(targets, list(bank.values()) + list(worker.retained_lineups),
        coverage_short, coverage_validated, selected['lineups'], validation_complete=coverage_complete,
        seeded=seeded, reserved=coverage_reserved, library=library_build,
        players=worker.players, selection_mode=options['selection_mode'], retained_count=len(worker.retained_lineups))
    timing = dict(deep, generation_allocation_seconds=limit * generation_fraction,
        style_candidate_counts=style_counts, deep_options=dict(options), deep_time_limit_seconds=limit,
        compute_mode="Deep", generation_seconds=generation_seconds, simulation_seconds=simulation_seconds,
        selection_seconds=time.perf_counter() - selection_start, total_seconds=time.perf_counter() - start,
        candidate_target=budget, optimizer_candidate_target=budget, candidate_count=generated,
        selected_count=len(selected["lineups"]), requested_count=worker.num_lineups,
        sim_scenarios=worker.sim_scenarios, build_pool_size=len(players), role_pool_applied=False)
    return {"kind": "showdown", "sport": "NFL", "lineups": selected["lineups"],
        "requested": worker.num_lineups, "cancelled": worker._cancel_event.is_set(),
        "portfolio_report": selected["report"], "candidate_count": generated,
        "sim_report": sim_report, "timing_report": timing, "repair_source": worker.repair_source}

