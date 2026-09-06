"""NFL Showdown Deep exploration and shared-outcome contest simulation."""
import bisect
import math
import random
import time
from array import array
from collections import Counter

from compute_settings import deep_candidate_budget, deep_search_seeds
from game_day_safety import UNAVAILABLE_STATUSES, _status
from nfl_simulation import _scenario_outcomes, _quantile, player_key
from optimizers import (ShowdownOptimizer, ShowdownLineup, attach_showdown_metrics,
                        _salary, _cpt_salary, _showdown_cpt_own, _showdown_flex_own)
from portfolio_rules import select_portfolio


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
    unique = {}
    for raw in players:
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
    games = {str(p.get("GameInfo") or "").split()[0] for p in pool if p.get("GameInfo")}
    if len(games) > 1:
        raise ValueError("NFL Showdown Deep requires one game.")
    for p in pool:
        if not p.get("GameInfo"):
            p["GameInfo"] = "@".join(teams)
    return pool


def generate_showdown_field(players, count, *, salary_cap=50000, seed=0, cancel_callback=None):
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
                      seed=90210, cancel_callback=None, progress_callback=None):
    if not candidates:
        return {"lineups": [], "report": {"scenarios": 0, "field_lineups": 0}}
    pool = active_showdown_players(players)
    for lineup in candidates:
        validate_showdown_lineup(lineup, pool, salary_cap)
    field = generate_showdown_field(pool, field_lineup_count, salary_cap=salary_cap, seed=seed + 1, cancel_callback=cancel_callback)
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
    cashes, busts = [0] * len(candidates), [0] * len(candidates)
    values = [{} for _ in candidates]
    scripts = Counter()
    completed = 0
    for scenario in range(max(1, int(scenarios))):
        if cancel_callback and cancel_callback():
            break
        outcomes = _scenario_outcomes(rng, pool, script_counter=scripts)
        ranked = sorted(showdown_score(lu, outcomes) for lu in banks[scenario % 3])
        top1, top5, cash, bust = [ranked[int(q * (len(ranked) - 1))] for q in (0.99, 0.95, 0.8, 0.4)]
        for i, lineup in enumerate(candidates):
            score = showdown_score(lineup, outcomes)
            scores[i].append(score)
            pct = bisect.bisect_right(ranked, score) / len(ranked)
            sums[i] += pct
            for group, threshold in zip(hits, (top1, top5, ranked[-1])):
                if score >= threshold:
                    group[i].add(scenario)
            cashes[i] += score >= cash
            busts[i] += score < bust
            value = 16.0 if score >= ranked[-1] else 6.0 + (pct - .99) * 200 if score >= top1 else 1.5 + (pct - .95) * 75 if score >= top5 else .2 if score >= cash else -1.0
            returns[i] += value
            if value >= 1.5:
                values[i][scenario] = value
        completed += 1
        if progress_callback and (completed % 50 == 0 or completed == scenarios):
            progress_callback(completed, scenarios, "Scoring Captain and FLEX against Showdown opponents")
    rows = []
    for i, lineup in enumerate(candidates):
        base = dict(getattr(lineup, "sim_metrics", {}) or {})
        base.update(sim_scenarios=completed, sim_field_lineups=len(field),
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
    return {"lineups": result, "report": {
        "scenarios": completed, "field_lineups": len(field), "opponent_field_samples": 3,
        "model": "showdown-shared-outcomes-v1", "field_preset": "Showdown ownership sample",
        "payout_model": "payout-shape-proxy-v1", "contest_aware": False,
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
    retained_keys = {showdown_signature(lu) for lu in retained}
    requested = max(0, worker.num_lineups - len(retained))
    budget = deep_candidate_budget(max(1, requested), options, False) if requested else 0
    bank = {}
    seeds = deep_search_seeds(options["seeds"])
    for index, seed in enumerate(seeds):
        if not requested or stop(start + limit * .38):
            break
        target = math.ceil((budget - len(bank)) / (len(seeds) - index))
        optimizer = ShowdownOptimizer(players, salary_cap=worker.salary_cap, seed=seed,
            own_mode=worker.own_mode, own_weight=worker.own_weight, build_style=worker.build_style)
        rows = optimizer.build_lineups(num_lineups=target,
            cancel_callback=lambda: stop(start + limit * .38),
            progress_callback=lambda done, total, text: worker.progress.emit(len(bank) + done, budget, f"Phase 1 of 4 - Showdown explore seed {index + 1}/{len(seeds)}"))
        for lu in attach_showdown_metrics(rows, worker.salary_cap):
            key = showdown_signature(lu)
            if key not in retained_keys:
                bank.setdefault(key, lu)
    generated = len(bank)
    generation_seconds = time.perf_counter() - start
    sim_start = time.perf_counter()
    lineups = list(bank.values())
    sim_report = {}
    deep = {"enabled": True, "time_limit_seconds": limit, "screening_scenarios": 0,
            "validation_scenarios": 0, "shortlist_count": 0, "validation_top_overlap_pct": None,
            "candidate_bank_count": generated, "validation_time_limit_reached": False}
    if lineups and not stop(start + limit * .58):
        coarse = simulate_showdown(retained + lineups, players,
            scenarios=min(options["screening"], max(250, worker.sim_scenarios)),
            field_lineup_count=min(1600, options["field"] or 1200), salary_cap=worker.salary_cap, seed=73129,
            cancel_callback=lambda: stop(start + limit * .58),
            progress_callback=lambda a,b,c: worker.progress.emit(a,b,"Phase 2 of 4 - " + c))
        deep["screening_scenarios"] = coarse["report"]["scenarios"]
        if deep["screening_scenarios"]:
            short = shortlist_fn(coarse["lineups"], max(worker.num_lineups, options["shortlist"] or 900), reserved_signatures=retained_keys)
            sim_report = coarse["report"]
            rank = lambda lu: (lu.sim_metrics.get("sim_edge", 0), lu.sim_metrics.get("sim_top_one_pct", 0), showdown_signature(lu))
            top = {showdown_signature(lu) for lu in sorted(short, key=rank, reverse=True)[:worker.num_lineups]}
            validation_end = deadline - min(60, limit * .20)
            if not stop(validation_end):
                validated = simulate_showdown(short, players, scenarios=max(2500, worker.sim_scenarios),
                    field_lineup_count=options["field"] or 2700, salary_cap=worker.salary_cap, seed=90210,
                    cancel_callback=lambda: stop(validation_end),
                    progress_callback=lambda a,b,c: worker.progress.emit(a,b,"Phase 3 of 4 - " + c))
                if validated["report"]["scenarios"]:
                    short = validated["lineups"]
                    sim_report = validated["report"]
                    deep["validation_scenarios"] = sim_report["scenarios"]
                    other = {showdown_signature(lu) for lu in sorted(short, key=rank, reverse=True)[:worker.num_lineups]}
                    deep["validation_top_overlap_pct"] = len(top & other) / max(1, len(top)) * 100
                deep["validation_time_limit_reached"] = time.perf_counter() >= validation_end
            deep["shortlist_count"] = len(short)
            retained = [lu for lu in short if showdown_signature(lu) in retained_keys]
            lineups = [lu for lu in short if showdown_signature(lu) not in retained_keys]
    simulation_seconds = time.perf_counter() - sim_start
    selection_start = time.perf_counter()
    worker.progress.emit(0, worker.num_lineups, "Phase 4 of 4 - selecting and refining Showdown portfolio")
    selected = select_portfolio(lineups, worker.num_lineups, kind="showdown", rules=worker.portfolio_rules,
        retained_lineups=retained, refinement_passes=256,
        refinement_stop_callback=lambda: stop(deadline), refinement_polish_duplication=True)
    for key in ("refinement_swaps", "duplication_refinement_swaps", "refinement_attempts", "refinement_seconds", "refinement_stop_reason"):
        deep[key] = selected["report"].get(key, "completed" if key.endswith("reason") else 0)
    deep["time_remaining_seconds"] = max(0, deadline - time.perf_counter())
    deep["time_limit_reached"] = time.perf_counter() >= deadline
    if not deep["validation_scenarios"]:
        selected["report"].setdefault("warnings", []).append("Deep Showdown did not complete independent validation; returning the best available stage.")
    sim_report["deep_build"] = dict(deep)
    timing = dict(deep, deep_options=dict(options), deep_time_limit_seconds=limit,
        compute_mode="Deep", generation_seconds=generation_seconds, simulation_seconds=simulation_seconds,
        selection_seconds=time.perf_counter() - selection_start, total_seconds=time.perf_counter() - start,
        candidate_target=budget, optimizer_candidate_target=budget, candidate_count=generated,
        selected_count=len(selected["lineups"]), requested_count=worker.num_lineups,
        sim_scenarios=worker.sim_scenarios, build_pool_size=len(players), role_pool_applied=False)
    return {"kind": "showdown", "sport": "NFL", "lineups": selected["lineups"],
        "requested": worker.num_lineups, "cancelled": worker._cancel_event.is_set(),
        "portfolio_report": selected["report"], "candidate_count": generated,
        "sim_report": sim_report, "timing_report": timing, "repair_source": worker.repair_source}
