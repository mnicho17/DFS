from __future__ import annotations

"""Local, shareable diagnostics for completed lineup builds.

The history intentionally contains only aggregate build settings and counts. It
does not store player names, lineup contents, salary-file paths, or API keys.
"""

import datetime as _dt
import json
import math
import os
import sys
import tempfile
import uuid
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
) -> Dict[str, Any]:
    """Build a JSON-safe aggregate diagnostic record."""
    settings = dict(context.get("settings") or {})
    rules = dict(context.get("portfolio_rules") or {})
    space = dict(context.get("lineup_space") or {})
    timing = dict(timing_report or {})
    portfolio = dict(portfolio_report or {})
    sim = dict(sim_report or {})
    sim_summary = dict(portfolio.get("sim_summary") or sim.get("portfolio") or {})
    candidate_sources = dict(sim.get("candidate_sources") or {})

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

    preset = dict(sim.get("preset_comparison") or {})
    diagnostic = {
        "schema_version": 1,
        "created_at": _now_iso(),
        "status": "cancelled" if cancelled else "completed",
        "sport": str(context.get("sport") or "NFL").strip().upper(),
        "contest_type": str(context.get("kind") or "classic").strip().lower(),
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
        },
        "sim": {
            "preset_fit": _number(preset.get("fit_score")) if preset.get("available") else None,
            "field_lineups": max(0, _integer(sim.get("field_lineup_count"))),
            "average_edge": _number(sim_summary.get("average_edge")) if sim_summary else None,
            "average_return_index": _number(sim_summary.get("average_return_index")) if sim_summary else None,
            "average_duplicate_risk": _number(sim_summary.get("average_duplicate_risk")) if sim_summary else None,
            "scenario_count": max(0, _integer(sim_summary.get("scenario_count"))),
            "top_one_scenarios_covered": max(0, _integer(sim_summary.get("top_one_scenarios_covered"))),
            "generated_sources": aggregate_counts(candidate_sources.get("generated")),
            "selected_sources": aggregate_counts(candidate_sources.get("selected")),
            "screening_scenarios": max(0, _integer(timing.get("screening_scenarios"))),
            "validation_scenarios": max(0, _integer(timing.get("validation_scenarios"))),
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
        f"- Portfolio: minimum unique {_integer(rules.get('minimum_unique'))}; team max {_number(rules.get('team_max_pct'), 100.0):.0f}%; game max {_number(rules.get('game_max_pct'), 100.0):.0f}%",
        f"- Balance ownership/dup risk: {'On' if rules.get('balance_ownership') else 'Off'}",
        f"- Player groups: {_integer(rules.get('group_count'))} | Player limits: {_integer(rules.get('constrained_player_count'))}",
    ])
    if sim.get("preset_fit") is not None:
        lines.append(f"- Preset fit: {_number(sim.get('preset_fit')):.0f}/100")
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
    if sim.get("average_edge") is not None:
        lines.append(
            f"- SIM portfolio: edge {_number(sim.get('average_edge')):.0f}/100; "
            f"return {_number(sim.get('average_return_index')):.0f}/100; "
            f"dup risk {_number(sim.get('average_duplicate_risk')):.0f}/100; "
            f"top-1% paths {_integer(sim.get('top_one_scenarios_covered')):,}/"
            f"{_integer(sim.get('scenario_count')):,}"
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
    lines.extend([
        "",
        "Privacy: This report contains aggregate settings and counts only; no players, lineups, file paths, or API keys.",
    ])
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
