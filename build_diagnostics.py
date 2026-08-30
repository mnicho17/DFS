from __future__ import annotations

"""Local, shareable diagnostics for completed lineup builds.

Reports may include compact lineup details to make strategy problems
reproducible. They never include salary-file paths or API keys.
"""

import datetime as _dt
import json
import math
import os
import sys
import tempfile
import uuid
from collections import Counter
from typing import Any, Dict, List, Mapping, Optional


HISTORY_LIMIT = 25


def _now_iso() -> str:
    return _dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _aggregate_warning(value: Any) -> str:
    """Keep warning meaning while stripping player, team, and game identifiers."""
    text = str(value or "").strip()
    lower = text.casefold()
    if not text:
        return ""
    if "captain exposure" in lower:
        return "A player Captain exposure constraint was not met."
    if "total exposure" in lower or "player exposure" in lower:
        return "A player exposure constraint was not met."
    if "team exposure" in lower:
        return "A team exposure constraint was not met."
    if "game exposure" in lower:
        return "A game exposure constraint was not met."
    if "group" in lower:
        return "A player-group rule was not satisfied."
    if "minimum unique" in lower:
        return "Minimum uniqueness was relaxed to finish the portfolio."
    return "A portfolio or simulation rule could not be fully satisfied."


def _base_dir() -> str:
    override = str(os.environ.get("DFS_OPTIMIZER_DATA_DIR") or "").strip()
    if override:
        os.makedirs(override, exist_ok=True)
        return override
    if getattr(sys, "frozen", False):
        local = str(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")).strip()
        path = os.path.join(local, "DFS Optimizer")
        os.makedirs(path, exist_ok=True)
        return path
    return os.path.dirname(os.path.abspath(__file__))


def build_history_path() -> str:
    folder = os.path.join(_base_dir(), "history")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "build-diagnostics.json")


def load_build_history(*, limit: int = HISTORY_LIMIT, path: Optional[str] = None) -> List[Dict[str, Any]]:
    target = path or build_history_path()
    try:
        with open(target, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError, ValueError):
        return []
    records = payload.get("records", []) if isinstance(payload, dict) else []
    cleaned = [dict(record) for record in records if isinstance(record, dict)]
    return cleaned[: max(0, int(limit))]


def _write_history(records: List[Dict[str, Any]], *, path: Optional[str] = None) -> None:
    target = path or build_history_path()
    folder = os.path.dirname(os.path.abspath(target))
    os.makedirs(folder, exist_ok=True)
    temporary = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=folder,
            prefix="build-diagnostics-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = handle.name
            json.dump({"schema_version": 1, "records": records}, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, target)
    finally:
        if temporary and os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass


def save_build_diagnostic(
    record: Mapping[str, Any],
    *,
    limit: int = HISTORY_LIMIT,
    path: Optional[str] = None,
) -> Dict[str, Any]:
    saved = dict(record)
    saved.setdefault("diagnostic_id", uuid.uuid4().hex)
    saved.setdefault("created_at", _now_iso())
    history = load_build_history(limit=max(HISTORY_LIMIT, int(limit)), path=path)
    history.insert(0, saved)
    _write_history(history[: max(1, int(limit))], path=path)
    return saved


def clear_build_history(*, path: Optional[str] = None) -> None:
    _write_history([], path=path)


def create_build_diagnostic(
    *,
    context: Mapping[str, Any],
    timing_report: Mapping[str, Any],
    portfolio_report: Optional[Mapping[str, Any]] = None,
    sim_report: Optional[Mapping[str, Any]] = None,
    displayed_count: int = 0,
    cancelled: bool = False,
    lineups: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """Build a JSON-safe diagnostic record with bounded lineup detail."""
    settings = dict(context.get("settings") or {})
    rules = dict(context.get("portfolio_rules") or {})
    space = dict(context.get("lineup_space") or {})
    timing = dict(timing_report or {})
    portfolio = dict(portfolio_report or {})
    sim = dict(sim_report or {})
    sim_summary = dict(portfolio.get("sim_summary") or sim.get("portfolio") or {})
    candidate_sources = dict(sim.get("candidate_sources") or {})
    joint = dict(sim.get("joint_portfolio") or portfolio.get("joint_contest") or {})
    contest_type = str(context.get("kind") or "classic").strip().lower()

    def player_snapshot(player: Mapping[str, Any], *, captain: bool = False) -> Dict[str, Any]:
        salary_key = "CptSalary" if captain else "FlexSalary"
        projection_key = "CptProjection" if captain else "FlexProjection"
        ownership_key = "ProjCptOwnPct" if captain else "ProjFlexOwnPct"
        ownership = player.get(ownership_key)
        if ownership in (None, ""):
            ownership = player.get("ProjOwnPct")
        return {
            "name": str(player.get("Name") or "Unknown"),
            "team": str(player.get("Team") or "").upper(),
            "position": str(player.get("Position") or "").upper(),
            "salary": _number(player.get(salary_key)),
            "projection": _number(player.get(projection_key)),
            "ownership": _number(ownership),
        }

    lineup_details: List[Dict[str, Any]] = []
    correlation_counts: Dict[str, int] = {}
    correlation_exception_lineups = 0
    exposure_counts: Counter[str] = Counter()
    captain_exposure_counts: Counter[str] = Counter()
    exposure_labels: Dict[str, str] = {}
    salary_exception_count = 0
    salary_strategy = str(settings.get("salary_strategy") or "").strip().casefold()
    salary_cap = _number(context.get("salary_cap"), 50000.0)
    if contest_type == "showdown":
        for index, lineup in enumerate(list(lineups or [])):
            captain = dict(lineup.get("Captain") or {})
            flex = [dict(player) for player in lineup.get("Flex") or []]
            metrics = dict(getattr(lineup, "sim_metrics", {}) or {})
            flags = [str(flag) for flag in metrics.get("showdown_correlation_flags") or []]
            if flags:
                correlation_exception_lineups += 1
            for flag in flags:
                correlation_counts[flag] = correlation_counts.get(flag, 0) + 1
            all_players = [captain] + flex
            for player in all_players:
                key = str(
                    player.get("FlexNamePlusID")
                    or player.get("FlexID")
                    or f"{player.get('Name')}|{player.get('Team')}|{player.get('Position')}"
                )
                exposure_counts[key] += 1
                exposure_labels[key] = (
                    f"{player.get('Name') or 'Unknown'} "
                    f"[{str(player.get('Team') or '?').upper()} {str(player.get('Position') or '?').upper()}]"
                )
            captain_key = str(
                captain.get("FlexNamePlusID")
                or captain.get("FlexID")
                or f"{captain.get('Name')}|{captain.get('Team')}|{captain.get('Position')}"
            )
            captain_exposure_counts[captain_key] += 1
            captain_salary = _number(captain.get("CptSalary"))
            flex_salary = sum(_number(player.get("FlexSalary")) for player in flex)
            salary_left = salary_cap - captain_salary - flex_salary
            salary_threshold = 500.0 if "max" in salary_strategy else 2500.0
            if ("near cap" in salary_strategy or "max" in salary_strategy) and salary_left > salary_threshold:
                salary_exception_count += 1
            if index < 50:
                captain_row = player_snapshot(captain, captain=True)
                flex_rows = [player_snapshot(player) for player in flex]
                lineup_details.append({
                    "number": index + 1,
                    "captain": captain_row,
                    "flex": flex_rows,
                    "salary": captain_row["salary"] + sum(row["salary"] for row in flex_rows),
                    "projection": captain_row["projection"] + sum(row["projection"] for row in flex_rows),
                    "team_split": dict(Counter(
                        row["team"] for row in [captain_row] + flex_rows if row["team"]
                    )),
                    "archetype": str(
                        getattr(lineup, "candidate_archetype", "")
                        or metrics.get("candidate_archetype")
                        or ""
                    ),
                    "duplicate_risk": _number(metrics.get("duplicate_risk")),
                    "flags": flags,
                })

    def aggregate_counts(value: Any) -> Dict[str, int]:
        allowed = {"optimizer", "field_shaped", "scenario_built"}
        return {
            str(key): max(0, _integer(count))
            for key, count in dict(value or {}).items()
            if str(key) in allowed
        }

    warnings: List[str] = []
    for source in (portfolio.get("warnings") or [], sim.get("warnings") or []):
        items = [source] if isinstance(source, str) else source
        for item in items:
            warning = _aggregate_warning(item)
            if warning and warning not in warnings:
                warnings.append(warning)
    if correlation_exception_lineups:
        warnings.append(
            f"{correlation_exception_lineups} selected Showdown lineups contain "
            f"{sum(correlation_counts.values())} correlation exception flags."
        )
    if salary_exception_count:
        warnings.append(
            f"{salary_exception_count} selected Showdown lineups fall outside the "
            f"{settings.get('salary_strategy') or 'selected'} salary strategy tolerance."
        )

    selected_total = max(1, len(list(lineups or [])))
    exposure_summary = {
        "total": [
            {
                "label": exposure_labels.get(key, key),
                "count": count,
                "pct": 100.0 * count / selected_total,
            }
            for key, count in exposure_counts.most_common()
        ],
        "captain": [
            {
                "label": exposure_labels.get(key, key),
                "count": count,
                "pct": 100.0 * count / selected_total,
            }
            for key, count in captain_exposure_counts.most_common()
        ],
    }

    preset = dict(sim.get("preset_comparison") or {})
    contest_profile = dict(settings.get("contest_profile") or sim.get("contest_profile") or {})
    diagnostic = {
        "schema_version": 4,
        "created_at": _now_iso(),
        "status": "cancelled" if cancelled else "completed",
        "sport": str(context.get("sport") or "NFL").strip().upper(),
        "contest_type": contest_type,
        "salary_cap": _number(context.get("salary_cap"), 50000.0),
        "requested_count": _integer(timing.get("requested_count"), _integer(context.get("requested_count"))),
        "displayed_count": max(0, _integer(displayed_count)),
        "pool": {
            "label": str(space.get("pool_label") or "active player pool"),
            "loaded": max(0, _integer(space.get("loaded"))),
            "eligible": max(0, _integer(space.get("eligible"))),
            "omitted": max(0, _integer(space.get("omitted"))),
            "locked": max(0, _integer(space.get("locked"))),
            "structural_combinations": max(0, _integer(space.get("structural_combinations"))),
            "exact": bool(space.get("exact")),
            "explanation": str(space.get("explanation") or ""),
        },
        "timing": {
            "generation_seconds": max(0.0, _number(timing.get("generation_seconds"))),
            "simulation_seconds": max(0.0, _number(timing.get("simulation_seconds"))),
            "selection_seconds": max(0.0, _number(timing.get("selection_seconds"))),
            "total_seconds": max(0.0, _number(timing.get("total_seconds"))),
        },
        "candidates": {
            "target": max(0, _integer(timing.get("candidate_target"))),
            "optimizer_target": max(0, _integer(timing.get("optimizer_candidate_target"))),
            "field_shaped_target": max(0, _integer(timing.get("ownership_candidate_target"))),
            "scenario_built_target": max(0, _integer(timing.get("scenario_candidate_target"))),
            "generated": max(0, _integer(timing.get("candidate_count"))),
            "selected": max(0, _integer(timing.get("selected_count"), displayed_count)),
            "shortlisted": max(0, _integer(timing.get("shortlist_count"))),
        },
        "settings": {
            "build_style": str(settings.get("build_style") or ""),
            "salary_strategy": str(settings.get("salary_strategy") or ""),
            "ownership_mode": str(settings.get("ownership_mode") or ""),
            "ownership_weight": _number(settings.get("ownership_weight")),
            "sim_enabled": bool(settings.get("sim_enabled")),
            "sim_scenarios": max(0, _integer(settings.get("sim_scenarios"))),
            "field_preset": str(settings.get("field_preset") or ""),
            "contest_profile_name": str(contest_profile.get("name") or ""),
            "contest_field_size": max(0, _integer(contest_profile.get("field_size"))),
            "contest_entry_fee": max(0.0, _number(contest_profile.get("entry_fee"))),
            "contest_user_entries": max(0, _integer(contest_profile.get("user_entries"))),
            "compute_mode": str(
                timing.get("compute_mode") or settings.get("compute_mode") or "Fast"
            ),
        },
        "portfolio_rules": {
            "minimum_unique": max(0, _integer(rules.get("min_unique"))),
            "team_max_pct": _number(rules.get("max_team_pct"), 100.0),
            "game_max_pct": _number(rules.get("max_game_pct"), 100.0),
            "balance_ownership": bool(rules.get("balance_ownership")),
            "group_count": len(rules.get("groups") or []),
            "constrained_player_count": len(rules.get("player_constraints") or {}),
        },
        "portfolio": {
            "compliant": bool(portfolio.get("compliant", not warnings)),
            "warning_count": len(warnings),
            "warnings": warnings,
            "correlation_exception_count": sum(correlation_counts.values()),
            "correlation_exception_lineups": correlation_exception_lineups,
            "correlation_exceptions": correlation_counts,
            "salary_exception_count": salary_exception_count,
            "automatic_showdown_guardrails": dict(
                portfolio.get("automatic_showdown_guardrails") or {}
            ),
        },
        "exposures": exposure_summary,
        "lineup_details": lineup_details,
        "lineup_details_total": len(list(lineups or [])),
        "sim": {
            "preset_fit": _number(preset.get("fit_score")) if preset.get("available") else None,
            "field_lineups": max(0, _integer(sim.get("field_lineups"), _integer(sim.get("field_lineup_count")))),
            "opponent_field_samples": max(
                0, _integer(joint.get("opponent_field_samples"), _integer(sim.get("opponent_field_samples")))
            ),
            "game_script_mix": dict(joint.get("game_script_mix") or sim.get("game_script_mix") or {}),
            "volatility_model": str(joint.get("volatility_model") or sim.get("volatility_model") or ""),
            "rare_event_model": str(joint.get("rare_event_model") or sim.get("rare_event_model") or ""),
            "average_edge": _number(sim_summary.get("average_edge")) if sim_summary else None,
            "average_return_index": _number(sim_summary.get("average_return_index")) if sim_summary else None,
            "average_expected_roi_pct": (
                _number(sim_summary.get("average_expected_roi_pct"))
                if sim_summary.get("contest_aware") else None
            ),
            "average_expected_payout": (
                _number(sim_summary.get("average_expected_payout"))
                if sim_summary.get("contest_aware") else None
            ),
            "average_expected_profit": (
                _number(sim_summary.get("average_expected_profit"))
                if sim_summary.get("contest_aware") else None
            ),
            "average_duplicate_risk": _number(sim_summary.get("average_duplicate_risk")) if sim_summary else None,
            "scenario_count": max(0, _integer(sim_summary.get("scenario_count"))),
            "top_one_scenarios_covered": max(0, _integer(sim_summary.get("top_one_scenarios_covered"))),
            "generated_sources": aggregate_counts(candidate_sources.get("generated")),
            "selected_sources": aggregate_counts(candidate_sources.get("selected")),
            "screening_scenarios": max(0, _integer(timing.get("screening_scenarios"))),
            "validation_scenarios": max(0, _integer(timing.get("validation_scenarios"))),
            "portfolio_simulation_scenarios": max(
                0, _integer(joint.get("scenarios"), _integer(timing.get("portfolio_simulation_scenarios")))
            ),
            "joint_portfolio": bool(joint.get("joint_portfolio")),
            "joint_entries": max(0, _integer(joint.get("entries_simulated"))),
            "joint_planned_entries": max(0, _integer(joint.get("planned_entries"))),
            "joint_entry_count_match": bool(joint.get("entry_count_match", True)),
            "joint_total_entry_cost": max(0.0, _number(joint.get("total_entry_cost"))),
            "joint_expected_total_payout": _number(joint.get("expected_total_payout")),
            "joint_expected_total_profit": _number(joint.get("expected_total_profit")),
            "joint_expected_roi_pct": _number(joint.get("expected_roi_pct")),
            "joint_roi_ci_low": _number(joint.get("roi_ci_low")),
            "joint_roi_ci_high": _number(joint.get("roi_ci_high")),
            "joint_profit_probability_pct": _number(joint.get("profit_probability_pct")),
            "joint_double_probability_pct": _number(joint.get("double_probability_pct")),
            "joint_any_top_ten_probability_pct": _number(joint.get("any_top_ten_probability_pct")),
            "joint_payout_p10": _number(joint.get("payout_p10")),
            "joint_payout_p50": _number(joint.get("payout_p50")),
            "joint_payout_p90": _number(joint.get("payout_p90")),
            "joint_stability": str(joint.get("stability") or ""),
            "joint_adaptive_stopped": bool(joint.get("adaptive_stopped")),
            "refinement_swaps": max(0, _integer(timing.get("refinement_swaps"))),
            "duplication_refinement_swaps": max(
                0, _integer(timing.get("duplication_refinement_swaps"))
            ),
            "refinement_attempts": max(0, _integer(timing.get("refinement_attempts"))),
            "refinement_seconds": max(0.0, _number(timing.get("refinement_seconds"))),
            "refinement_stop_reason": str(timing.get("refinement_stop_reason") or ""),
            "time_remaining_seconds": max(0.0, _number(timing.get("time_remaining_seconds"))),
            "deep_time_limit_seconds": max(0.0, _number(timing.get("deep_time_limit_seconds"))),
            "deep_time_limit_reached": bool(timing.get("time_limit_reached")),
            "validation_top_overlap_pct": (
                _number(timing.get("validation_top_overlap_pct"))
                if timing.get("validation_top_overlap_pct") is not None
                else None
            ),
        },
    }
    return diagnostic


def _created_label(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "Unknown time"
    try:
        created = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if created.tzinfo is not None:
            created = created.astimezone()
        return created.strftime("%b %d, %Y %I:%M:%S %p")
    except ValueError:
        return raw


def build_history_label(record: Mapping[str, Any]) -> str:
    sport = str(record.get("sport") or "NFL").upper()
    contest = str(record.get("contest_type") or "classic").title()
    selected = _integer((record.get("candidates") or {}).get("selected"))
    seconds = _number((record.get("timing") or {}).get("total_seconds"))
    return f"{_created_label(record.get('created_at'))}  |  {sport} {contest}  |  {selected} lineups  |  {seconds:.1f}s"


def format_build_report(record: Mapping[str, Any]) -> str:
    pool = dict(record.get("pool") or {})
    timing = dict(record.get("timing") or {})
    candidates = dict(record.get("candidates") or {})
    settings = dict(record.get("settings") or {})
    rules = dict(record.get("portfolio_rules") or {})
    portfolio = dict(record.get("portfolio") or {})
    sim = dict(record.get("sim") or {})
    lineup_details = [dict(row) for row in record.get("lineup_details") or []]
    exposures = dict(record.get("exposures") or {})
    is_showdown = str(record.get("contest_type") or "").casefold() == "showdown"
    sim_ran = bool(settings.get("sim_enabled") and _integer(sim.get("scenario_count")) > 0)

    phase_times = {
        "Generate": _number(timing.get("generation_seconds")),
        "SIM": _number(timing.get("simulation_seconds")),
        "Select": _number(timing.get("selection_seconds")),
    }
    slowest_name, slowest_seconds = max(phase_times.items(), key=lambda item: item[1])
    total_seconds = _number(timing.get("total_seconds"))
    slowest_share = (slowest_seconds / total_seconds * 100.0) if total_seconds > 0 else 0.0

    space_count = max(0, _integer(pool.get("structural_combinations")))
    space_kind = "exact roster-shape count" if pool.get("exact") else "upper bound"
    sim_text = "Off"
    if settings.get("sim_enabled"):
        sim_text = f"On ({_integer(settings.get('sim_scenarios')):,} scenarios, {settings.get('field_preset') or 'default preset'})"

    candidate_detail = ""
    optimizer_target = _integer(candidates.get("optimizer_target"))
    field_target = _integer(candidates.get("field_shaped_target"))
    scenario_target = _integer(candidates.get("scenario_built_target"))
    if optimizer_target or field_target or scenario_target:
        sources = [f"{optimizer_target:,} optimizer"]
        if field_target:
            sources.append(f"{field_target:,} field-shaped")
        if scenario_target:
            sources.append(f"{scenario_target:,} scenario-built")
        candidate_detail = f" ({' + '.join(sources)})"

    compute_mode = str(settings.get("compute_mode") or "Fast")
    deep_mode = compute_mode.casefold().startswith("deep")

    lines = [
        "DFS Optimizer Build Report",
        f"Run: {_created_label(record.get('created_at'))}",
        f"Status: {str(record.get('status') or 'completed').title()}",
        "",
        "Build",
        f"- Contest: {str(record.get('sport') or 'NFL').upper()} {str(record.get('contest_type') or 'classic').title()}",
        f"- Salary cap: ${_number(record.get('salary_cap'), 50000.0):,.0f}",
        f"- Requested: {_integer(record.get('requested_count')):,}",
        f"- Candidates: {_integer(candidates.get('generated')):,} generated / {_integer(candidates.get('target')):,} budget{candidate_detail}",
        f"- Selected: {_integer(candidates.get('selected')):,} ({_integer(record.get('displayed_count')):,} displayed)",
        "",
        "Build space",
        f"- Pool: {_integer(pool.get('eligible')):,} of {_integer(pool.get('loaded')):,} ({pool.get('label') or 'active player pool'})",
        f"- Omitted: {_integer(pool.get('omitted')):,} | Locked: {_integer(pool.get('locked')):,}",
        f"- Structural combinations: {space_count:,} ({space_kind})",
    ]
    if str(pool.get("explanation") or "").strip():
        lines.append(f"- Note: {str(pool.get('explanation')).strip()}")
    if deep_mode:
        lines.append(
            f"- Deep shortlist: {_integer(candidates.get('shortlisted')):,} candidates after "
            f"{_integer(sim.get('screening_scenarios')):,} screening scenarios"
        )
    lines.extend([
        "",
        "Timing",
        f"- Generate: {phase_times['Generate']:.2f}s",
        f"- SIM: {phase_times['SIM']:.2f}s",
        f"- Select: {phase_times['Select']:.2f}s",
        f"- Total: {total_seconds:.2f}s",
        f"- Slowest phase: {slowest_name} ({slowest_seconds:.2f}s, {slowest_share:.0f}% of total)",
        "",
        "Settings",
        f"- Build style: {settings.get('build_style') or 'n/a'}",
        f"- Salary strategy: {settings.get('salary_strategy') or 'n/a'}",
        f"- Ownership: {settings.get('ownership_mode') or 'n/a'} (weight {_number(settings.get('ownership_weight')):.2f})",
        f"- NFL SIM Edge: {sim_text}",
        f"- Compute: {compute_mode}",
        (
            f"- Portfolio: minimum unique {_integer(rules.get('minimum_unique'))}; "
            f"team exposure cap {_number(rules.get('team_max_pct'), 100.0):.0f}%"
            if is_showdown else
            f"- Portfolio: minimum unique {_integer(rules.get('minimum_unique'))}; "
            f"team max {_number(rules.get('team_max_pct'), 100.0):.0f}%; "
            f"game max {_number(rules.get('game_max_pct'), 100.0):.0f}%"
        ),
        f"- Balance ownership/dup risk: {'On' if rules.get('balance_ownership') else 'Off'}",
        f"- Player groups: {_integer(rules.get('group_count'))} | Player limits: {_integer(rules.get('constrained_player_count'))}",
    ])
    if settings.get("contest_profile_name"):
        lines.insert(
            lines.index(f"- Compute: {compute_mode}"),
            (
                f"- Contest-Aware SIM: {settings.get('contest_profile_name')} • "
                f"{_integer(settings.get('contest_field_size')):,} entries • "
                f"${_number(settings.get('contest_entry_fee')):,.2f} entry • "
                f"{_integer(settings.get('contest_user_entries')):,} user entries"
            ),
        )
    if sim.get("preset_fit") is not None:
        lines.append(f"- Preset fit: {_number(sim.get('preset_fit')):.0f}/100")
    if sim.get("joint_portfolio"):
        lines.append(
            f"- Joint contest: {_integer(sim.get('joint_entries')):,} entries cost "
            f"${_number(sim.get('joint_total_entry_cost')):,.2f}; expected payout "
            f"${_number(sim.get('joint_expected_total_payout')):,.2f}; profit "
            f"${_number(sim.get('joint_expected_total_profit')):+,.2f}; ROI "
            f"{_number(sim.get('joint_expected_roi_pct')):+.1f}%"
        )
        lines.append(
            f"- Joint range: profit chance {_number(sim.get('joint_profit_probability_pct')):.1f}%; "
            f"95% ROI {_number(sim.get('joint_roi_ci_low')):+.1f}% to "
            f"{_number(sim.get('joint_roi_ci_high')):+.1f}%; total payout P10/P50/P90 "
            f"${_number(sim.get('joint_payout_p10')):,.0f}/${_number(sim.get('joint_payout_p50')):,.0f}/"
            f"${_number(sim.get('joint_payout_p90')):,.0f}"
        )
        lines.append(
            f"- Joint validation: {_integer(sim.get('portfolio_simulation_scenarios')):,} scenarios; "
            f"{_integer(sim.get('opponent_field_samples'))} opponent-field samples; "
            f"{sim.get('joint_stability') or 'n/a'} stability"
        )
    elif sim.get("average_expected_roi_pct") is not None:
        lines.append(
            f"- Contest portfolio: ROI {_number(sim.get('average_expected_roi_pct')):+.1f}% • "
            f"expected payout ${_number(sim.get('average_expected_payout')):,.2f} • "
            f"expected profit ${_number(sim.get('average_expected_profit')):+,.2f} per entry"
        )
    if sim.get("volatility_model"):
        lines.append("- Scenario model: game scripts, role-aware player ranges, and guarded rare ceiling outcomes")
    if deep_mode:
        stop_reason = str(sim.get("refinement_stop_reason") or "").strip()
        time_remaining = max(0.0, _number(sim.get("time_remaining_seconds")))
        if sim.get("deep_time_limit_reached"):
            deep_status = "time budget used"
        elif "local optimum" in stop_reason:
            deep_status = f"{stop_reason} with {time_remaining:.0f}s remaining"
        elif stop_reason:
            deep_status = stop_reason
        else:
            deep_status = "completed before time budget"
        lines.append(
            f"- Deep validation: {_integer(sim.get('validation_scenarios')):,} independent scenarios; "
            f"{_integer(sim.get('refinement_swaps')):,} portfolio swaps "
            f"({_integer(sim.get('duplication_refinement_swaps')):,} duplication polish); {deep_status}"
        )
        lines.append(
            f"- Deep polish: {_number(sim.get('refinement_seconds')):.2f}s across "
            f"{_integer(sim.get('refinement_attempts')):,} search passes"
        )
        if sim.get("validation_top_overlap_pct") is not None:
            lines.append(
                f"- Independent top-candidate agreement: "
                f"{_number(sim.get('validation_top_overlap_pct')):.1f}%"
            )
    if sim.get("average_edge") is not None and sim_ran:
        lines.append(
            f"- SIM portfolio: edge {_number(sim.get('average_edge')):.0f}/100; "
            f"return {_number(sim.get('average_return_index')):.0f}/100; "
            f"dup risk {_number(sim.get('average_duplicate_risk')):.0f}/100; "
            f"top-1% paths {_integer(sim.get('top_one_scenarios_covered')):,}/"
            f"{_integer(sim.get('scenario_count')):,}"
        )
    elif sim.get("average_edge") is not None:
        lines.append(
            f"- Portfolio estimates (no SIM): quality {_number(sim.get('average_edge')):.0f}/100; "
            f"leverage-adjusted return {_number(sim.get('average_return_index')):.0f}/100; "
            f"dup risk {_number(sim.get('average_duplicate_risk')):.0f}/100"
        )
    if is_showdown:
        exception_count = _integer(portfolio.get("correlation_exception_count"))
        exception_lineups = _integer(portfolio.get("correlation_exception_lineups"))
        lines.append(
            f"- Correlation exceptions: {exception_count:,} flags across "
            f"{exception_lineups:,} of {_integer(candidates.get('selected')):,} lineups"
        )
        exception_types = dict(portfolio.get("correlation_exceptions") or {})
        if exception_types:
            lines.append(
                "- Exception types: "
                + "; ".join(
                    f"{name} ({_integer(count)})"
                    for name, count in sorted(exception_types.items())
                )
            )
        guardrails = dict(portfolio.get("automatic_showdown_guardrails") or {})
        if guardrails:
            lines.append(
                f"- Automatic guardrails: player { _number(guardrails.get('total_player_pct')):.0f}% max; "
                f"Captain {_number(guardrails.get('captain_pct')):.0f}% max; "
                f"combined K/DST Captain {_number(guardrails.get('specialist_captain_pct')):.0f}% max"
            )
    selected_sources = dict(sim.get("selected_sources") or {})
    if selected_sources:
        source_labels = {
            "optimizer": "optimizer",
            "field_shaped": "field-shaped",
            "scenario_built": "scenario-built",
        }
        lines.append(
            "- Selected sources: "
            + " + ".join(
                f"{_integer(selected_sources.get(key)):,} {source_labels[key]}"
                for key in ("optimizer", "field_shaped", "scenario_built")
                if _integer(selected_sources.get(key)) > 0
            )
        )

    warnings = [str(warning) for warning in portfolio.get("warnings") or [] if str(warning).strip()]
    lines.extend(["", "Warnings"])
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings)
    else:
        lines.append("- None")
    if is_showdown and exposures.get("total"):
        selected_count = max(1, _integer(candidates.get("selected")))
        lines.extend(["", "Full portfolio exposures"])
        lines.append("- Overall (all selected lineups):")
        for row in list(exposures.get("total") or [])[:15]:
            lines.append(
                f"  - {row.get('label')}: {_integer(row.get('count'))}/{selected_count} "
                f"({_number(row.get('pct')):.1f}%)"
            )
        lines.append("- Captain (all selected lineups):")
        for row in list(exposures.get("captain") or [])[:12]:
            lines.append(
                f"  - {row.get('label')}: {_integer(row.get('count'))}/{selected_count} "
                f"({_number(row.get('pct')):.1f}%)"
            )
    if is_showdown and lineup_details:
        lines.extend(["", "Showdown lineup diagnostics"])
        for row in lineup_details:
            captain = dict(row.get("captain") or {})
            flex = [dict(player) for player in row.get("flex") or []]

            def player_text(player: Mapping[str, Any], slot: str) -> str:
                own = _number(player.get("ownership"))
                return (
                    f"{slot} {player.get('name') or 'Unknown'} "
                    f"[{player.get('team') or '?'} {player.get('position') or '?'}] "
                    f"${_number(player.get('salary')):,.0f} / "
                    f"proj {_number(player.get('projection')):.2f} / own {own:.1f}%"
                )

            split = "-".join(
                str(count) for count in sorted(
                    (_integer(value) for value in dict(row.get("team_split") or {}).values()),
                    reverse=True,
                )
            ) or "n/a"
            lines.append(
                f"- #{_integer(row.get('number'))}: ${_number(row.get('salary')):,.0f} salary | "
                f"proj {_number(row.get('projection')):.2f} | split {split} | "
                f"{row.get('archetype') or 'Unclassified'} | dup {_number(row.get('duplicate_risk')):.0f}/100"
            )
            lines.append(f"  - {player_text(captain, 'CPT')}")
            for player in flex:
                lines.append(f"  - {player_text(player, 'FLEX')}")
            flags = [str(flag) for flag in row.get("flags") or []]
            lines.append(f"  - Flags: {', '.join(flags) if flags else 'None'}")
        total_detail = _integer(record.get("lineup_details_total"), len(lineup_details))
        if total_detail > len(lineup_details):
            lines.append(
                f"- Detail limited to the first {len(lineup_details):,} of {total_detail:,} selected lineups."
            )
    lines.extend(["", (
        "Privacy: This report includes lineup names and strategy inputs for troubleshooting; "
        "it excludes file paths and API keys."
        if lineup_details else
        "Privacy: This report contains aggregate settings and counts only; no players, "
        "lineups, file paths, or API keys."
    )])
    return "\n".join(lines)


def format_build_comparison(first: Mapping[str, Any], second: Mapping[str, Any]) -> str:
    """Compare two privacy-safe build-history records."""
    records = sorted(
        [dict(first or {}), dict(second or {})],
        key=lambda record: str(record.get("created_at") or ""),
    )
    earlier, later = records

    def value(record: Mapping[str, Any], section: str, key: str) -> Any:
        return dict(record.get(section) or {}).get(key)

    def delta(newer: Any, older: Any, *, digits: int = 1, suffix: str = "") -> str:
        change = _number(newer) - _number(older)
        return f"{change:+.{digits}f}{suffix}"

    def source_text(record: Mapping[str, Any]) -> str:
        sources = dict(value(record, "sim", "selected_sources") or {})
        labels = {
            "optimizer": "optimizer",
            "field_shaped": "field-shaped",
            "scenario_built": "scenario-built",
        }
        parts = [
            f"{_integer(sources.get(key))} {labels[key]}"
            for key in labels
            if _integer(sources.get(key)) > 0
        ]
        return " + ".join(parts) if parts else "n/a"

    lines = [
        "DFS Optimizer Build Comparison",
        f"A: {_created_label(earlier.get('created_at'))}",
        f"B: {_created_label(later.get('created_at'))}",
        "",
        "Build",
        f"- Contest: {str(earlier.get('sport') or 'NFL').upper()} {str(earlier.get('contest_type') or 'classic').title()} -> "
        f"{str(later.get('sport') or 'NFL').upper()} {str(later.get('contest_type') or 'classic').title()}",
        f"- Preset: {value(earlier, 'settings', 'field_preset') or 'n/a'} -> {value(later, 'settings', 'field_preset') or 'n/a'}",
        f"- Selected: {_integer(value(earlier, 'candidates', 'selected')):,} -> {_integer(value(later, 'candidates', 'selected')):,} "
        f"({delta(value(later, 'candidates', 'selected'), value(earlier, 'candidates', 'selected'), digits=0)})",
        f"- Candidates: {_integer(value(earlier, 'candidates', 'generated')):,} -> {_integer(value(later, 'candidates', 'generated')):,} "
        f"({delta(value(later, 'candidates', 'generated'), value(earlier, 'candidates', 'generated'), digits=0)})",
        f"- Pool: {_integer(value(earlier, 'pool', 'eligible')):,} -> {_integer(value(later, 'pool', 'eligible')):,} "
        f"({delta(value(later, 'pool', 'eligible'), value(earlier, 'pool', 'eligible'), digits=0)})",
        "",
        "Performance",
        f"- Generate: {_number(value(earlier, 'timing', 'generation_seconds')):.2f}s -> {_number(value(later, 'timing', 'generation_seconds')):.2f}s "
        f"({delta(value(later, 'timing', 'generation_seconds'), value(earlier, 'timing', 'generation_seconds'), digits=2, suffix='s')})",
        f"- SIM: {_number(value(earlier, 'timing', 'simulation_seconds')):.2f}s -> {_number(value(later, 'timing', 'simulation_seconds')):.2f}s "
        f"({delta(value(later, 'timing', 'simulation_seconds'), value(earlier, 'timing', 'simulation_seconds'), digits=2, suffix='s')})",
        f"- Select: {_number(value(earlier, 'timing', 'selection_seconds')):.2f}s -> {_number(value(later, 'timing', 'selection_seconds')):.2f}s "
        f"({delta(value(later, 'timing', 'selection_seconds'), value(earlier, 'timing', 'selection_seconds'), digits=2, suffix='s')})",
        f"- Total: {_number(value(earlier, 'timing', 'total_seconds')):.2f}s -> {_number(value(later, 'timing', 'total_seconds')):.2f}s "
        f"({delta(value(later, 'timing', 'total_seconds'), value(earlier, 'timing', 'total_seconds'), digits=2, suffix='s')}; negative is faster)",
        "",
        "SIM portfolio",
    ]
    for label, key in (
        ("Preset fit", "preset_fit"),
        ("Average Edge", "average_edge"),
        ("Return index", "average_return_index"),
        ("Duplication risk", "average_duplicate_risk"),
    ):
        old = value(earlier, "sim", key)
        new = value(later, "sim", key)
        if old is not None or new is not None:
            lines.append(
                f"- {label}: {_number(old):.1f} -> {_number(new):.1f} ({delta(new, old, digits=1)})"
            )
    old_covered = _integer(value(earlier, "sim", "top_one_scenarios_covered"))
    new_covered = _integer(value(later, "sim", "top_one_scenarios_covered"))
    old_scenarios = _integer(value(earlier, "sim", "scenario_count"))
    new_scenarios = _integer(value(later, "sim", "scenario_count"))
    if old_scenarios or new_scenarios:
        lines.append(f"- Top-1% paths: {old_covered}/{old_scenarios} -> {new_covered}/{new_scenarios}")
    lines.extend([
        f"- Selected sources A: {source_text(earlier)}",
        f"- Selected sources B: {source_text(later)}",
        "",
        "Interpretation: Compare builds only when the slate and inputs are meaningfully similar. "
        "A higher score or broader scenario coverage is not automatically better if exposure, news, or contest assumptions changed.",
        "",
        "Privacy: This comparison contains aggregate settings and counts only; no players, lineups, file paths, or API keys.",
    ])
    return "\n".join(lines)

