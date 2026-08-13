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
            "generated": max(0, _integer(timing.get("candidate_count"))),
            "selected": max(0, _integer(timing.get("selected_count"), displayed_count)),
        },
        "settings": {
            "build_style": str(settings.get("build_style") or ""),
            "salary_strategy": str(settings.get("salary_strategy") or ""),
            "ownership_mode": str(settings.get("ownership_mode") or ""),
            "ownership_weight": _number(settings.get("ownership_weight")),
            "sim_enabled": bool(settings.get("sim_enabled")),
            "sim_scenarios": max(0, _integer(settings.get("sim_scenarios"))),
            "field_preset": str(settings.get("field_preset") or ""),
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

    lines = [
        "DFS Optimizer Build Report",
        f"Run: {_created_label(record.get('created_at'))}",
        f"Status: {str(record.get('status') or 'completed').title()}",
        "",
        "Build",
        f"- Contest: {str(record.get('sport') or 'NFL').upper()} {str(record.get('contest_type') or 'classic').title()}",
        f"- Salary cap: ${_number(record.get('salary_cap'), 50000.0):,.0f}",
        f"- Requested: {_integer(record.get('requested_count')):,}",
        f"- Candidates: {_integer(candidates.get('generated')):,} generated / {_integer(candidates.get('target')):,} target",
        f"- Selected: {_integer(candidates.get('selected')):,} ({_integer(record.get('displayed_count')):,} displayed)",
        "",
        "Build space",
        f"- Pool: {_integer(pool.get('eligible')):,} of {_integer(pool.get('loaded')):,} ({pool.get('label') or 'active player pool'})",
        f"- Omitted: {_integer(pool.get('omitted')):,} | Locked: {_integer(pool.get('locked')):,}",
        f"- Structural combinations: {space_count:,} ({space_kind})",
    ]
    if str(pool.get("explanation") or "").strip():
        lines.append(f"- Note: {str(pool.get('explanation')).strip()}")
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
        f"- Portfolio: minimum unique {_integer(rules.get('minimum_unique'))}; team max {_number(rules.get('team_max_pct'), 100.0):.0f}%; game max {_number(rules.get('game_max_pct'), 100.0):.0f}%",
        f"- Balance ownership/dup risk: {'On' if rules.get('balance_ownership') else 'Off'}",
        f"- Player groups: {_integer(rules.get('group_count'))} | Player limits: {_integer(rules.get('constrained_player_count'))}",
    ])
    if sim.get("preset_fit") is not None:
        lines.append(f"- Preset fit: {_number(sim.get('preset_fit')):.0f}/100")

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
