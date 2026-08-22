from __future__ import annotations

"""Passive local learning database for DFS exports.

This module intentionally has no UI. It records exported saved lineups and the
features the app already knows at export time. Later result-import/backtesting can
attach actual fantasy points/ROI to these same records.
"""

import datetime as _dt
import csv
import hashlib
import json
import math
import os
import re
import sqlite3
import statistics
import sys
import uuid
from collections import Counter
from typing import Any, Dict, List, Optional


class _ImportCancelled(Exception):
    pass


def _now_iso() -> str:
    return _dt.datetime.now().replace(microsecond=0).isoformat()


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def _safe_int(x: Any, default: int = 0) -> int:
    try:
        if x is None:
            return default
        return int(float(x))
    except Exception:
        return default


def _pkey(p: Dict[str, Any]) -> str:
    return (
        str(p.get("FlexNamePlusID") or "").strip()
        or str(p.get("FlexID") or "").strip()
        or str(p.get("Name") or "").strip()
    )


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


def history_db_path() -> str:
    hist_dir = os.path.join(_base_dir(), "history")
    os.makedirs(hist_dir, exist_ok=True)
    return os.path.join(hist_dir, "exports.sqlite")


def _connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or history_db_path())
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS exports (
            export_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            app_version TEXT,
            sport TEXT,
            contest_type TEXT,
            salary_cap REAL,
            lineup_count INTEGER,
            export_path TEXT,
            build_style TEXT,
            own_mode TEXT,
            own_weight REAL,
            field_preset TEXT,
            mlb_stack_pref TEXT,
            salary_strategy TEXT,
            validation_json TEXT
        );

        CREATE TABLE IF NOT EXISTS lineups (
            lineup_id TEXT PRIMARY KEY,
            export_id TEXT NOT NULL,
            lineup_index INTEGER NOT NULL,
            roster_ids_json TEXT,
            salary REAL,
            projection REAL,
            base_projection REAL,
            context_adjustment REAL,
            grade TEXT,
            grade_score REAL,
            dup_risk REAL,
            dup_risk_label TEXT,
            uniqueness REAL,
            stack_shape TEXT,
            primary_team TEXT,
            secondary_team TEXT,
            top_order_hitters INTEGER,
            confirmed_hitters INTEGER,
            avg_ownership REAL,
            max_ownership REAL,
            warnings TEXT,
            explanation TEXT,
            sim_edge REAL,
            sim_win_rate REAL,
            sim_top_one_pct REAL,
            sim_top_five_pct REAL,
            sim_cash_rate REAL,
            sim_bust_rate REAL,
            sim_average_percentile REAL,
            sim_ceiling REAL,
            sim_return_index REAL,
            sim_leverage REAL,
            sim_duplicate_risk REAL,
            sim_scenarios INTEGER,
            sim_field_lineups INTEGER,
            sim_expected_payout REAL,
            sim_expected_profit REAL,
            sim_expected_roi_pct REAL,
            sim_contest_name TEXT,
            sim_entry_fee REAL,
            sim_contest_field_size INTEGER,
            actual_points REAL,
            roi REAL,
            cashed INTEGER,
            top_one_pct INTEGER,
            FOREIGN KEY(export_id) REFERENCES exports(export_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS lineup_players (
            lineup_player_id TEXT PRIMARY KEY,
            lineup_id TEXT NOT NULL,
            slot TEXT,
            player_key TEXT,
            player_id TEXT,
            name TEXT,
            team TEXT,
            opponent TEXT,
            position TEXT,
            salary REAL,
            projection REAL,
            base_projection REAL,
            context_adjustment REAL,
            context_json TEXT,
            ownership REAL,
            batting_order INTEGER,
            confirmed_lineup INTEGER,
            injury_status TEXT,
            actual_points REAL,
            FOREIGN KEY(lineup_id) REFERENCES lineups(lineup_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_exports_created_at ON exports(created_at);
        CREATE INDEX IF NOT EXISTS idx_lineups_export ON lineups(export_id);
        CREATE INDEX IF NOT EXISTS idx_lineup_players_lineup ON lineup_players(lineup_id);
        CREATE INDEX IF NOT EXISTS idx_lineup_players_player_id ON lineup_players(player_id);
        """
    )
    _ensure_column(conn, "lineups", "base_projection", "REAL")
    _ensure_column(conn, "lineups", "context_adjustment", "REAL")
    for column, definition in (
        ("sim_edge", "REAL"),
        ("sim_win_rate", "REAL"),
        ("sim_top_one_pct", "REAL"),
        ("sim_top_five_pct", "REAL"),
        ("sim_cash_rate", "REAL"),
        ("sim_bust_rate", "REAL"),
        ("sim_average_percentile", "REAL"),
        ("sim_ceiling", "REAL"),
        ("sim_return_index", "REAL"),
        ("sim_leverage", "REAL"),
        ("sim_duplicate_risk", "REAL"),
        ("sim_scenarios", "INTEGER"),
        ("sim_field_lineups", "INTEGER"),
        ("sim_expected_payout", "REAL"),
        ("sim_expected_profit", "REAL"),
        ("sim_expected_roi_pct", "REAL"),
        ("sim_contest_name", "TEXT"),
        ("sim_entry_fee", "REAL"),
        ("sim_contest_field_size", "INTEGER"),
    ):
        _ensure_column(conn, "lineups", column, definition)
    _ensure_column(conn, "lineup_players", "base_projection", "REAL")
    _ensure_column(conn, "lineup_players", "context_adjustment", "REAL")
    _ensure_column(conn, "lineup_players", "context_json", "TEXT")
    conn.commit()


def _lineup_players(kind: str, lineup: Any, sport: str) -> List[tuple[str, Dict[str, Any]]]:
    if kind == "showdown" and isinstance(lineup, dict):
        out: List[tuple[str, Dict[str, Any]]] = []
        cpt = lineup.get("Captain") or {}
        if isinstance(cpt, dict) and cpt:
            out.append(("CPT", cpt))
        flex = lineup.get("Flex", []) or []
        for i, p in enumerate(flex, start=1):
            if isinstance(p, dict):
                out.append((f"FLEX{i}", p))
        return out

    if isinstance(lineup, list):
        # Generic stored slot labels. The DK export row remains the source of exact
        # upload slot ordering; these labels are for analysis/reporting only.
        return [(f"SLOT{i}", p) for i, p in enumerate(lineup, start=1) if isinstance(p, dict)]
    return []


def _is_pitcher(p: Dict[str, Any], sport: str) -> bool:
    pos = str(p.get("Position") or "").upper().replace("/", ",").replace(";", ",")
    toks = {x.strip() for x in pos.split(",") if x.strip()}
    return sport.upper() == "MLB" and bool(toks & {"P", "SP", "RP"})


def _team_counts(players: List[Dict[str, Any]], sport: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for p in players:
        if _is_pitcher(p, sport):
            continue
        team = str(p.get("Team") or "").strip().upper()
        if team:
            counts[team] = counts.get(team, 0) + 1
    return counts


def _player_projection_context(p: Dict[str, Any], slot: str) -> tuple[float, float, float, Dict[str, Any]]:
    is_cpt = str(slot or "").upper() == "CPT"
    adjusted = _safe_float(p.get("CptProjection") if is_cpt else p.get("FlexProjection"))
    raw_base = p.get("BaseProjection")
    if raw_base in (None, ""):
        base = adjusted
    else:
        base = _safe_float(raw_base) * (1.5 if is_cpt else 1.0)
    context = {
        key: p.get(key)
        for key in (
            "NFLAdjScore", "NFLUsageScore", "NFLMatchupScore", "NFLRoleScore",
            "NFLWeatherScore", "NFLVegas", "NFLNotes", "MLBAdjScore", "MLBRecentForm",
            "MLBMatchup", "MLBBallpark", "MLBWeather", "MLBVegas", "MLBNotes",
            "TeamAdjPct",
        )
        if p.get(key) not in (None, "")
    }
    return adjusted, base, adjusted - base, context


def _lineup_feature_fallback(kind: str, sport: str, lineup: Any, salary_cap: float) -> Dict[str, Any]:
    pairs = _lineup_players(kind, lineup, sport)
    players = [p for _, p in pairs]
    if kind == "showdown" and isinstance(lineup, dict):
        cpt = lineup.get("Captain") or {}
        flex = lineup.get("Flex", []) or []
        salary = _safe_float(cpt.get("CptSalary")) + sum(_safe_float(p.get("FlexSalary")) for p in flex if isinstance(p, dict))
        projection = _safe_float(cpt.get("CptProjection")) + sum(_safe_float(p.get("FlexProjection")) for p in flex if isinstance(p, dict))
    else:
        salary = sum(_safe_float(p.get("FlexSalary")) for p in players)
        projection = sum(_safe_float(p.get("FlexProjection")) for p in players)
    base_projection = sum(_player_projection_context(p, slot)[1] for slot, p in pairs)

    owns = [_safe_float(p.get("ProjOwnPct")) for p in players]
    avg_own = sum(owns) / max(1, len(owns))
    max_own = max(owns) if owns else 0.0
    counts = sorted(_team_counts(players, sport).items(), key=lambda kv: kv[1], reverse=True)
    stack_shape = "-".join(str(c) for _, c in counts[:3]) if counts else "n/a"
    top_order = 0
    confirmed = 0
    for p in players:
        if _is_pitcher(p, sport):
            continue
        bo = _safe_int(p.get("BattingOrder"), 0)
        if 1 <= bo <= 5:
            top_order += 1
        if bool(p.get("ConfirmedLineup")):
            confirmed += 1
    unused = max(0.0, float(salary_cap or 50000.0) - salary)
    dup_risk = 25.0 + max(0.0, max_own - 20.0) * 0.9
    if unused <= 200:
        dup_risk += 15.0
    elif 400 <= unused <= 1200:
        dup_risk -= 8.0
    if sport.upper() == "MLB" and stack_shape in {"5-3", "5-2-1", "4-4"}:
        dup_risk += 8.0
    dup_risk = max(0.0, min(100.0, dup_risk))
    risk_label = "High" if dup_risk >= 70 else "Medium" if dup_risk >= 40 else "Low"
    return {
        "salary": salary,
        "projection": projection,
        "base_projection": base_projection,
        "context_adjustment": projection - base_projection,
        "grade": "",
        "score": 0.0,
        "dup_risk": dup_risk,
        "dup_risk_label": risk_label,
        "uniqueness": 100.0 - dup_risk,
        "stack_shape": stack_shape,
        "primary_team": counts[0][0] if len(counts) >= 1 else "",
        "secondary_team": counts[1][0] if len(counts) >= 2 else "",
        "top_order_hitters": top_order,
        "confirmed_hitters": confirmed,
        "avg_ownership": avg_own,
        "max_ownership": max_own,
        "warnings": "",
        "explanation": "",
    }


def record_export(
    *,
    kind: str,
    sport: str,
    lineups: List[Any],
    rows: List[List[str]],
    salary_cap: float,
    export_path: str,
    validation: Dict[str, Any],
    settings: Optional[Dict[str, Any]] = None,
    grade_func: Any = None,
    app_version: str = "",
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Persist one completed export and its lineup/player features.

    Returns a small summary safe to show in the export report.
    """
    settings = settings or {}
    export_id = str(uuid.uuid4())
    now = _now_iso()
    conn = _connect(db_path)
    try:
        init_db(conn)
        with conn:
            conn.execute(
                """
                INSERT INTO exports (
                    export_id, created_at, app_version, sport, contest_type, salary_cap,
                    lineup_count, export_path, build_style, own_mode, own_weight,
                    field_preset, mlb_stack_pref, salary_strategy, validation_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    export_id,
                    now,
                    app_version,
                    sport.upper(),
                    kind,
                    float(salary_cap or 50000.0),
                    len(lineups or []),
                    export_path,
                    str(settings.get("build_style", "") or ""),
                    str(settings.get("own_mode", "") or ""),
                    _safe_float(settings.get("own_weight"), 0.0),
                    str(settings.get("field_preset", "") or ""),
                    str(settings.get("mlb_stack_pref", "") or ""),
                    str(settings.get("salary_strategy", "") or ""),
                    json.dumps(validation or {}, default=str),
                ),
            )

            for idx, lineup in enumerate(lineups or []):
                fallback = _lineup_feature_fallback(kind, sport, lineup, salary_cap)
                grade = {}
                if callable(grade_func) and kind != "showdown":
                    try:
                        grade = grade_func(lineup, sport, salary_cap) or {}
                    except Exception:
                        grade = {}
                feature = dict(fallback)
                feature.update({k: v for k, v in grade.items() if v not in (None, "")})
                lineup_id = str(uuid.uuid4())
                roster_ids = rows[idx] if idx < len(rows or []) else []
                conn.execute(
                    """
                    INSERT INTO lineups (
                        lineup_id, export_id, lineup_index, roster_ids_json, salary, projection,
                        base_projection, context_adjustment,
                        grade, grade_score, dup_risk, dup_risk_label, uniqueness, stack_shape,
                        primary_team, secondary_team, top_order_hitters, confirmed_hitters,
                        avg_ownership, max_ownership, warnings, explanation
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        lineup_id,
                        export_id,
                        idx + 1,
                        json.dumps(roster_ids),
                        _safe_float(feature.get("salary", fallback.get("salary"))),
                        _safe_float(feature.get("projection", fallback.get("projection"))),
                        _safe_float(feature.get("base_projection", fallback.get("base_projection"))),
                        _safe_float(feature.get("context_adjustment", fallback.get("context_adjustment"))),
                        str(feature.get("grade", "") or ""),
                        _safe_float(feature.get("score", feature.get("grade_score", 0.0))),
                        _safe_float(feature.get("dup_risk", fallback.get("dup_risk"))),
                        str(feature.get("dup_risk_label", fallback.get("dup_risk_label", "")) or ""),
                        _safe_float(feature.get("uniqueness", fallback.get("uniqueness"))),
                        str(feature.get("stack_shape", fallback.get("stack_shape", "")) or ""),
                        str(feature.get("primary_team", fallback.get("primary_team", "")) or ""),
                        str(feature.get("secondary_team", fallback.get("secondary_team", "")) or ""),
                        _safe_int(feature.get("top_order_hitters", fallback.get("top_order_hitters"))),
                        _safe_int(feature.get("confirmed_hitters", fallback.get("confirmed_hitters"))),
                        _safe_float(feature.get("avg_ownership", fallback.get("avg_ownership"))),
                        _safe_float(feature.get("max_ownership", fallback.get("max_ownership"))),
                        str(feature.get("warnings", "") or ""),
                        str(feature.get("explanation", "") or ""),
                    ),
                )
                if feature.get("sim_edge") is not None:
                    conn.execute(
                        """
                        UPDATE lineups SET
                            sim_edge=?, sim_win_rate=?, sim_top_one_pct=?, sim_top_five_pct=?,
                            sim_cash_rate=?, sim_bust_rate=?, sim_average_percentile=?, sim_ceiling=?,
                            sim_return_index=?, sim_leverage=?, sim_duplicate_risk=?, sim_scenarios=?,
                            sim_field_lineups=?, sim_expected_payout=?, sim_expected_profit=?,
                            sim_expected_roi_pct=?, sim_contest_name=?, sim_entry_fee=?,
                            sim_contest_field_size=?
                        WHERE lineup_id=?
                        """,
                        (
                            _safe_float(feature.get("sim_edge")),
                            _safe_float(feature.get("sim_win_rate")),
                            _safe_float(feature.get("sim_top_one_pct")),
                            _safe_float(feature.get("sim_top_five_pct")),
                            _safe_float(feature.get("sim_cash_rate")),
                            _safe_float(feature.get("sim_bust_rate")),
                            _safe_float(feature.get("sim_average_percentile")),
                            _safe_float(feature.get("sim_ceiling")),
                            _safe_float(feature.get("sim_return_index")),
                            _safe_float(feature.get("sim_leverage")),
                            _safe_float(feature.get("duplicate_risk")),
                            _safe_int(feature.get("sim_scenarios")),
                            _safe_int(feature.get("sim_field_lineups")),
                            None if feature.get("sim_expected_payout") is None else _safe_float(feature.get("sim_expected_payout")),
                            None if feature.get("sim_expected_profit") is None else _safe_float(feature.get("sim_expected_profit")),
                            None if feature.get("sim_expected_roi_pct") is None else _safe_float(feature.get("sim_expected_roi_pct")),
                            str(feature.get("sim_contest_name", "") or ""),
                            None if feature.get("sim_entry_fee") is None else _safe_float(feature.get("sim_entry_fee")),
                            None if feature.get("sim_contest_field_size") is None else _safe_int(feature.get("sim_contest_field_size")),
                            lineup_id,
                        ),
                    )
                for slot, p in _lineup_players(kind, lineup, sport):
                    player_id = str(p.get("CptID") if slot == "CPT" else p.get("FlexID") or "").strip()
                    adjusted_projection, base_projection, context_adjustment, context = _player_projection_context(p, slot)
                    conn.execute(
                        """
                        INSERT INTO lineup_players (
                            lineup_player_id, lineup_id, slot, player_key, player_id, name,
                            team, opponent, position, salary, projection, base_projection,
                            context_adjustment, context_json, ownership,
                            batting_order, confirmed_lineup, injury_status
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuid.uuid4()),
                            lineup_id,
                            slot,
                            _pkey(p),
                            player_id,
                            str(p.get("Name", "") or ""),
                            str(p.get("Team", "") or ""),
                            str(p.get("Opponent", "") or ""),
                            str(p.get("Position", "") or ""),
                            _safe_float(p.get("CptSalary") if slot == "CPT" else p.get("FlexSalary")),
                            adjusted_projection,
                            base_projection,
                            context_adjustment,
                            json.dumps(context, default=str),
                            _safe_float(p.get("ProjCptOwnPct") if slot == "CPT" else p.get("ProjOwnPct")),
                            _safe_int(p.get("BattingOrder"), 0),
                            1 if bool(p.get("ConfirmedLineup")) else 0,
                            str(p.get("InjuryStatus", "") or ""),
                        ),
                    )
        matching = match_historical_results(conn)
        return {
            "export_id": export_id,
            "db_path": db_path or history_db_path(),
            "lineups_recorded": len(lineups or []),
            "results_matched": matching.get("matched", 0),
        }
    finally:
        conn.close()


def _bucket_ownership(value: float) -> str:
    if value < 8:
        return "Under 8% avg own"
    if value < 12:
        return "8-12% avg own"
    if value < 16:
        return "12-16% avg own"
    if value < 20:
        return "16-20% avg own"
    return "20%+ avg own"


def _context_bucket(value: float) -> str:
    if value > 0.25:
        return "Positive context"
    if value < -0.25:
        return "Negative context"
    return "Neutral context"


def _bucket_sim_edge(value: float) -> str:
    if value < 60:
        return "Under 60 Edge"
    if value < 70:
        return "60-69 Edge"
    if value < 80:
        return "70-79 Edge"
    return "80+ Edge"


def _bucket_sim_return(value: float) -> str:
    if value < 40:
        return "Under 40 return index"
    if value < 60:
        return "40-59 return index"
    if value < 80:
        return "60-79 return index"
    return "80+ return index"


def _bucket_sim_leverage(value: float) -> str:
    if value < 40:
        return "Under 40 leverage"
    if value < 60:
        return "40-59 leverage"
    if value < 80:
        return "60-79 leverage"
    return "80+ leverage"


def _bucket_sim_duplication(value: float) -> str:
    if value < 25:
        return "Under 25 duplication risk"
    if value < 50:
        return "25-49 duplication risk"
    if value < 75:
        return "50-74 duplication risk"
    return "75+ duplication risk"


def _correlation(xs: List[float], ys: List[float]) -> Optional[float]:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    x_mean = statistics.mean(xs)
    y_mean = statistics.mean(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    denominator = math.sqrt(
        sum((x - x_mean) ** 2 for x in xs) * sum((y - y_mean) ** 2 for y in ys)
    )
    return numerator / denominator if denominator > 0 else None


def _render_breakdown(
    title: str, groups: Dict[str, List[Dict[str, Any]]], *, minimum: int = 5
) -> List[str]:
    lines = [title]
    eligible = []
    for label, rows in groups.items():
        if len(rows) < minimum:
            continue
        roi_values = [float(row["roi"]) for row in rows if row.get("roi") is not None]
        scores = [float(row["actual_points"]) for row in rows if row.get("actual_points") is not None]
        eligible.append((label, rows, roi_values, scores))
    if not eligible:
        lines.append(f"- Hidden until a bucket has at least {minimum} matched entries.")
        return lines
    for label, rows, roi_values, scores in sorted(
        eligible, key=lambda item: len(item[1]), reverse=True
    )[:8]:
        details = [f"{len(rows)} entries"]
        if roi_values:
            details.append(f"avg net {_fmt_money(statistics.mean(roi_values))}")
        if scores:
            details.append(f"avg score {statistics.mean(scores):.2f}")
        lines.append(f"- {label or 'Unknown'}: " + " | ".join(details))
    return lines


def generate_learning_report(*, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Build conservative, local-only outcome and projection calibration reporting."""
    path = db_path or history_db_path()
    conn = _connect(path)
    try:
        init_historical_import_tables(conn)
        match_historical_results(conn)
        cur = conn.cursor()
        export_count = int(cur.execute("SELECT COUNT(*) FROM exports").fetchone()[0] or 0)
        exported_lineups = int(cur.execute("SELECT COUNT(*) FROM lineups").fetchone()[0] or 0)
        imported_files = int(cur.execute("SELECT COUNT(*) FROM historical_imports WHERE notes IN ('ok', 'field_only')").fetchone()[0] or 0)
        imported_rows = int(cur.execute("SELECT COUNT(*) FROM historical_results").fetchone()[0] or 0)
        matched_rows = int(cur.execute("SELECT COUNT(*) FROM historical_results WHERE matched_lineup_id IS NOT NULL").fetchone()[0] or 0)
        matched_lineups = int(cur.execute("SELECT COUNT(DISTINCT matched_lineup_id) FROM historical_results WHERE matched_lineup_id IS NOT NULL").fetchone()[0] or 0)
        field_rows = cur.execute(
            """
            SELECT sport, field_preset, entry_count, field_size, metadata_coverage_pct,
                   duplicate_entry_pct, top_one_entries, top_one_duplicate_pct,
                   avg_salary, salary_p10, ownership_mae, stack_rates_json,
                   bringback_rates_json, flex_rates_json, ownership_json,
                   ownership_profile_json, roster_size
            FROM contest_field_summaries
            ORDER BY created_at DESC
            """
        ).fetchall()
        field_contests = len(field_rows)
        field_entries = sum(_safe_int(row[2], 0) for row in field_rows)
        field_presets = Counter(str(row[1] or "Unclassified") for row in field_rows)

        def _weighted_field_average(index: int, weight_index: int = 2) -> Optional[float]:
            pairs = [
                (_safe_float(row[index]), _safe_int(row[weight_index], 0))
                for row in field_rows if row[index] is not None and _safe_int(row[weight_index], 0) > 0
            ]
            if not pairs:
                return None
            return sum(value * weight for value, weight in pairs) / sum(weight for _, weight in pairs)

        field_dup_pct = _weighted_field_average(5)
        field_metadata_coverage = _weighted_field_average(4)
        field_avg_salary = _weighted_field_average(8)
        field_ownership_mae = _weighted_field_average(10)
        top_dup_pairs = [
            (_safe_float(row[7]), _safe_int(row[6], 0))
            for row in field_rows if row[7] is not None and _safe_int(row[6], 0) > 0
        ]
        field_top_dup_pct = (
            sum(value * weight for value, weight in top_dup_pairs) / sum(weight for _, weight in top_dup_pairs)
            if top_dup_pairs else None
        )
        latest_sim_comparison: Dict[str, Any] = {}
        for (validation_json,) in cur.execute(
            """
            SELECT validation_json FROM exports
            WHERE sport='NFL' AND contest_type='classic'
            ORDER BY created_at DESC
            """
        ).fetchall():
            try:
                validation_payload = json.loads(validation_json or "{}")
                candidate = dict((validation_payload.get("sim_report") or {}).get("field_comparison") or {})
            except Exception:
                candidate = {}
            if candidate.get("available"):
                latest_sim_comparison = candidate
                break

        outcome_rows = []
        raw_outcomes = cur.execute(
            """
            SELECT hr.roi, hr.entry_fee, hr.winnings, hr.percentile, hr.cashed,
                   hr.top_one_pct, hr.actual_points, l.salary, l.avg_ownership,
                   l.stack_shape, l.context_adjustment, e.salary_cap, e.sport,
                   l.sim_edge, l.sim_win_rate, l.sim_top_one_pct, l.sim_top_five_pct,
                   l.sim_cash_rate, l.sim_bust_rate, l.sim_average_percentile,
                   l.sim_ceiling, l.sim_return_index, l.sim_leverage,
                   l.sim_duplicate_risk, l.sim_scenarios, l.sim_field_lineups,
                   l.sim_expected_payout, l.sim_expected_profit, l.sim_expected_roi_pct,
                   l.sim_contest_name, l.sim_entry_fee, l.sim_contest_field_size
            FROM historical_results hr
            JOIN lineups l ON l.lineup_id=hr.matched_lineup_id
            JOIN exports e ON e.export_id=l.export_id
            """
        ).fetchall()
        for row in raw_outcomes:
            outcome_rows.append({
                "roi": row[0], "entry_fee": row[1], "winnings": row[2],
                "percentile": row[3], "cashed": row[4], "top_one_pct": row[5],
                "actual_points": row[6], "salary": row[7], "avg_ownership": row[8],
                "stack_shape": row[9], "context_adjustment": row[10],
                "salary_cap": row[11], "sport": row[12],
                "sim_edge": row[13], "sim_win_rate": row[14],
                "sim_top_one_pct": row[15], "sim_top_five_pct": row[16],
                "sim_cash_rate": row[17], "sim_bust_rate": row[18],
                "sim_average_percentile": row[19], "sim_ceiling": row[20],
                "sim_return_index": row[21], "sim_leverage": row[22],
                "sim_duplicate_risk": row[23], "sim_scenarios": row[24],
                "sim_field_lineups": row[25],
                "sim_expected_payout": row[26], "sim_expected_profit": row[27],
                "sim_expected_roi_pct": row[28], "sim_contest_name": row[29],
                "sim_entry_fee": row[30], "sim_contest_field_size": row[31],
            })

        calibration_rows = cur.execute(
            """
            SELECT projection, base_projection, actual_points
            FROM lineups
            WHERE actual_points IS NOT NULL AND projection IS NOT NULL AND projection > 0
            """
        ).fetchall()
        adjusted = [float(row[0]) for row in calibration_rows]
        actual = [float(row[2]) for row in calibration_rows]
        adjusted_mae = statistics.mean(abs(a - p) for p, a in zip(adjusted, actual)) if actual else None
        bias = statistics.mean(a - p for p, a in zip(adjusted, actual)) if actual else None
        corr = _correlation(adjusted, actual)
        base_pairs = [
            (float(row[1]), float(row[2]))
            for row in calibration_rows
            if row[1] is not None and float(row[1]) > 0
        ]
        base_mae = statistics.mean(abs(a - p) for p, a in base_pairs) if base_pairs else None
        context_edge = (
            base_mae - adjusted_mae
            if base_mae is not None and adjusted_mae is not None
            else None
        )

        roi_values = [float(row["roi"]) for row in outcome_rows if row.get("roi") is not None]
        stake = sum(float(row.get("entry_fee") or 0.0) for row in outcome_rows if row.get("roi") is not None)
        winnings = sum(float(row.get("winnings") or 0.0) for row in outcome_rows if row.get("roi") is not None)
        net = sum(roi_values)
        roi_pct = (net / stake * 100.0) if stake > 0 else None
        cash_values = [int(row["cashed"]) for row in outcome_rows if row.get("cashed") is not None]
        percentile_values = [float(row["percentile"]) for row in outcome_rows if row.get("percentile") is not None]
        top_values = [int(row["top_one_pct"]) for row in outcome_rows if row.get("top_one_pct") is not None]

        salary_groups: Dict[str, List[Dict[str, Any]]] = {}
        ownership_groups: Dict[str, List[Dict[str, Any]]] = {}
        stack_groups: Dict[str, List[Dict[str, Any]]] = {}
        context_groups: Dict[str, List[Dict[str, Any]]] = {}
        sport_groups: Dict[str, List[Dict[str, Any]]] = {}
        sim_edge_groups: Dict[str, List[Dict[str, Any]]] = {}
        sim_return_groups: Dict[str, List[Dict[str, Any]]] = {}
        sim_leverage_groups: Dict[str, List[Dict[str, Any]]] = {}
        sim_duplication_groups: Dict[str, List[Dict[str, Any]]] = {}
        for row in outcome_rows:
            salary_groups.setdefault(
                _bucket_salary_unused(_safe_float(row["salary"]), _safe_float(row["salary_cap"], 50000.0)), []
            ).append(row)
            ownership_groups.setdefault(_bucket_ownership(_safe_float(row["avg_ownership"])), []).append(row)
            stack_groups.setdefault(str(row.get("stack_shape") or "Unknown"), []).append(row)
            context_groups.setdefault(_context_bucket(_safe_float(row["context_adjustment"])), []).append(row)
            sport_groups.setdefault(str(row.get("sport") or "Unknown"), []).append(row)
            if row.get("sim_edge") is not None:
                sim_edge_groups.setdefault(_bucket_sim_edge(float(row["sim_edge"])), []).append(row)
                sim_return_groups.setdefault(_bucket_sim_return(_safe_float(row.get("sim_return_index"))), []).append(row)
                sim_leverage_groups.setdefault(_bucket_sim_leverage(_safe_float(row.get("sim_leverage"))), []).append(row)
                sim_duplication_groups.setdefault(_bucket_sim_duplication(_safe_float(row.get("sim_duplicate_risk"))), []).append(row)

        sim_rows = [row for row in outcome_rows if row.get("sim_edge") is not None]
        sim_top_one_pred = [float(row["sim_top_one_pct"]) for row in sim_rows if row.get("sim_top_one_pct") is not None]
        sim_top_one_actual = [int(row["top_one_pct"]) * 100.0 for row in sim_rows if row.get("top_one_pct") is not None]
        sim_top_five_pred = [float(row["sim_top_five_pct"]) for row in sim_rows if row.get("sim_top_five_pct") is not None]
        sim_top_five_actual = [100.0 if float(row["percentile"]) >= 95.0 else 0.0 for row in sim_rows if row.get("percentile") is not None]
        sim_cash_pred = [float(row["sim_cash_rate"]) for row in sim_rows if row.get("sim_cash_rate") is not None]
        sim_cash_actual = [int(row["cashed"]) * 100.0 for row in sim_rows if row.get("cashed") is not None]
        edge_finish_pairs = [
            (float(row["sim_edge"]), float(row["percentile"]))
            for row in sim_rows if row.get("percentile") is not None
        ]
        return_roi_pairs = [
            (float(row["sim_return_index"]), float(row["roi"]))
            for row in sim_rows
            if row.get("sim_return_index") is not None and row.get("roi") is not None
        ]
        edge_finish_corr = _correlation(
            [pair[0] for pair in edge_finish_pairs], [pair[1] for pair in edge_finish_pairs]
        )
        return_roi_corr = _correlation(
            [pair[0] for pair in return_roi_pairs], [pair[1] for pair in return_roi_pairs]
        )
        contest_roi_rows = [
            row for row in sim_rows
            if row.get("sim_expected_roi_pct") is not None
            and row.get("roi") is not None
            and float(row.get("entry_fee") or 0.0) > 0
        ]
        predicted_contest_roi = (
            statistics.mean(float(row["sim_expected_roi_pct"]) for row in contest_roi_rows)
            if contest_roi_rows else None
        )
        actual_contest_roi = (
            statistics.mean(
                float(row["roi"]) / float(row["entry_fee"]) * 100.0
                for row in contest_roi_rows
            )
            if contest_roi_rows else None
        )

        match_rate = (matched_rows / imported_rows * 100.0) if imported_rows else 0.0
        confidence = _confidence_label(exported_lineups + imported_rows)
        outcome_confidence = _confidence_label(matched_rows)
        lines = [
            "DFS Results & Learning", "", "Local data status",
            f"- Exports recorded: {export_count}",
            f"- Exported lineups: {exported_lineups}",
            f"- Result files imported: {imported_files}",
            f"- Result entries imported: {imported_rows}",
            f"- Exact lineup matches: {matched_rows} entries / {matched_lineups} unique lineups ({match_rate:.1f}%)",
            f"- Outcome confidence: {outcome_confidence}",
            "- All history stays on this computer.", "", "Contest performance",
        ]
        if outcome_rows:
            if roi_values:
                lines.append(f"- Entry fees: {_fmt_money(stake)} | winnings: {_fmt_money(winnings)} | net: {_fmt_money(net)}")
                if roi_pct is not None:
                    lines.append(f"- ROI: {roi_pct:+.1f}% across {len(roi_values)} matched entries")
            else:
                lines.append("- Monetary ROI is unavailable in the imported file.")
            lines.append(
                f"- Cash rate: {statistics.mean(cash_values) * 100.0:.1f}% ({len(cash_values)} entries)"
                if cash_values else "- Cash rate is unavailable in the imported file."
            )
            lines.append(
                f"- Average finish percentile: {statistics.mean(percentile_values):.1f}% ({len(percentile_values)} entries)"
                if percentile_values else "- Percentile needs contest field size or complete standings."
            )
            lines.append(
                f"- Top 1% rate: {statistics.mean(top_values) * 100.0:.1f}% ({len(top_values)} entries)"
                if top_values else "- Top 1% rate needs contest field size or complete standings."
            )
        else:
            lines.append("- No imported result has matched an exported lineup yet.")

        lines.extend(["", "Projection calibration"])
        if actual:
            lines.append(f"- Matched lineups with actual scores: {len(actual)}")
            lines.append(f"- Adjusted projection MAE: {adjusted_mae:.2f} DK points")
            lines.append(f"- Mean actual minus projection: {bias:+.2f} DK points")
            lines.append(f"- Projection/actual correlation: {corr:.3f}" if corr is not None else "- Correlation needs score variation.")
            if base_mae is not None:
                lines.append(f"- Base projection MAE: {base_mae:.2f} DK points")
                if len(base_pairs) >= 25:
                    verdict = "helped" if context_edge and context_edge > 0 else "hurt" if context_edge and context_edge < 0 else "was neutral"
                    lines.append(f"- Context adjustment {verdict} MAE by {abs(context_edge or 0.0):.2f} points ({len(base_pairs)} lineups).")
                else:
                    lines.append(f"- Base vs context comparison is directional only until 25 matched lineups ({len(base_pairs)}/25).")
        else:
            lines.append("- Export lineups, then import DraftKings results to measure projection accuracy.")

        lines.extend(["", "NFL SIM validation"])
        if sim_rows:
            lines.append(f"- Matched NFL SIM entries: {len(sim_rows)}")
            if sim_top_one_pred and sim_top_one_actual:
                lines.append(
                    f"- Predicted top 1%: {statistics.mean(sim_top_one_pred):.2f}% | "
                    f"actual: {statistics.mean(sim_top_one_actual):.2f}%"
                )
            if sim_top_five_pred and sim_top_five_actual:
                lines.append(
                    f"- Predicted top 5%: {statistics.mean(sim_top_five_pred):.2f}% | "
                    f"actual: {statistics.mean(sim_top_five_actual):.2f}%"
                )
            if sim_cash_pred and sim_cash_actual:
                lines.append(
                    f"- Predicted cash rate: {statistics.mean(sim_cash_pred):.1f}% | "
                    f"actual: {statistics.mean(sim_cash_actual):.1f}%"
                )
            if predicted_contest_roi is not None and actual_contest_roi is not None:
                lines.append(
                    f"- Contest-Aware ROI: predicted {predicted_contest_roi:+.1f}% | "
                    f"actual {actual_contest_roi:+.1f}% ({len(contest_roi_rows)} entries)"
                )
            lines.append(
                f"- SIM Edge / finish correlation: {edge_finish_corr:.3f}"
                if edge_finish_corr is not None else "- SIM Edge correlation needs more result variation."
            )
            lines.append(
                f"- Return index / net correlation: {return_roi_corr:.3f}"
                if return_roi_corr is not None else "- Return index correlation needs more result variation."
            )
            if len(sim_rows) < 50:
                lines.append(
                    f"- SIM validation is directional until 50 matched entries ({len(sim_rows)}/50); no automatic tuning is applied."
                )
        else:
            lines.append("- Generate and export NFL Classic SIM lineups, then import DraftKings results to validate the model.")

        lines.extend(["", "Contest field learning"])
        if field_rows:
            preset_text = ", ".join(
                f"{name}: {count}" for name, count in field_presets.most_common()
            )
            lines.append(
                f"- Complete fields analyzed: {field_contests} | field entries: {field_entries:,} | {preset_text}"
            )
            if field_dup_pct is not None:
                lines.append(f"- Entries sharing a duplicated roster: {field_dup_pct:.1f}%")
            if field_top_dup_pct is not None:
                lines.append(f"- Top 1% entries sharing a duplicated roster: {field_top_dup_pct:.1f}%")
            if field_metadata_coverage is not None:
                lines.append(f"- Player metadata coverage: {field_metadata_coverage:.1f}%")
            if field_avg_salary is not None:
                lines.append(f"- Average field salary used: ${field_avg_salary:,.0f}")
            if field_ownership_mae is not None:
                lines.append(
                    f"- Projected-vs-actual player ownership MAE: {field_ownership_mae:.2f} percentage points"
                )
            latest_nfl = next(
                (row for row in field_rows if str(row[0] or "").upper() == "NFL" and _safe_int(row[16], 0) == 9),
                None,
            )
            if latest_nfl:
                try:
                    stack_rates = json.loads(latest_nfl[11] or "{}")
                    flex_rates = json.loads(latest_nfl[13] or "{}")
                    actual_ownership = json.loads(latest_nfl[14] or "{}")
                    ownership_profile = json.loads(latest_nfl[15] or "{}")
                except Exception:
                    stack_rates, flex_rates, actual_ownership, ownership_profile = {}, {}, {}, {}
                if actual_ownership:
                    top_owned = sorted(
                        actual_ownership.items(), key=lambda item: _safe_float(item[1]), reverse=True
                    )[:5]
                    lines.append(
                        "- Latest NFL field highest ownership: "
                        + " | ".join(
                            f"{str(name).title()}: {_safe_float(value):.1f}%"
                            for name, value in top_owned
                        )
                    )
                profile_field = dict(ownership_profile.get("field") or {})
                profile_top = dict(ownership_profile.get("top_one") or {})
                if profile_field.get("lineups") and profile_top.get("lineups"):
                    lines.append(
                        "- Lineup ownership, field vs top 1%: "
                        f"total {profile_field.get('avg_total_ownership', 0.0):.1f}% vs "
                        f"{profile_top.get('avg_total_ownership', 0.0):.1f}% | "
                        f"sub-5% players {profile_field.get('avg_sub_five_players', 0.0):.2f} vs "
                        f"{profile_top.get('avg_sub_five_players', 0.0):.2f} | "
                        f"20%+ players {profile_field.get('avg_twenty_plus_players', 0.0):.2f} vs "
                        f"{profile_top.get('avg_twenty_plus_players', 0.0):.2f}"
                    )
                    coverage = _safe_float(ownership_profile.get("ownership_coverage_pct"), 0.0)
                    source_mae = ownership_profile.get("source_vs_computed_mae")
                    validation = f"{coverage:.1f}% lineup ownership coverage"
                    if source_mae is not None:
                        validation += f", DK listed/computed MAE {_safe_float(source_mae):.3f} points"
                    lines.append(f"- Ownership-profile validation: {validation}")
                    bucket_rows = dict(ownership_profile.get("buckets") or {})
                    useful_buckets = [
                        (label, values) for label, values in bucket_rows.items()
                        if _safe_int((values or {}).get("entries"), 0) > 0
                    ]
                    if useful_buckets:
                        lines.append(
                            "- Top-1% rate / duplication by total-ownership band: "
                            + " | ".join(
                                f"{label}: {_safe_float(values.get('top_one_rate')):.2f}% / "
                                f"{_safe_float(values.get('duplicate_pct')):.1f}%"
                                for label, values in useful_buckets
                            )
                        )
                if stack_rates and sum(_safe_float(value) for value in stack_rates.values()) > 0:
                    lines.append(
                        "- Latest NFL field QB stacks: "
                        + " | ".join(
                            f"{key if key != '3' else '3+'}: {_safe_float(stack_rates.get(key)) * 100.0:.1f}%"
                            for key in ("0", "1", "2", "3")
                        )
                    )
                if flex_rates and sum(_safe_float(value) for value in flex_rates.values()) > 0:
                    lines.append(
                        "- Latest NFL FLEX mix: "
                        + " | ".join(
                            f"{key}: {_safe_float(flex_rates.get(key)) * 100.0:.1f}%"
                            for key in ("RB", "WR", "TE")
                        )
                    )
            for preset_name in ("Single Entry", "3-Max", "20-Max", "150-Max"):
                if field_presets.get(preset_name, 0) <= 0:
                    continue
                calibration = load_nfl_field_calibration(preset_name, db_path=path)
                lines.append(f"- {calibration['message']}")
            if field_presets.get("Unclassified", 0):
                lines.append(
                    "- Unclassified fields remain report-only; include Single Entry, 3-Max, 20-Max, or 150-Max in the contest/file name to enable preset learning."
                )
        else:
            lines.append(
                "- Import a complete DraftKings standings CSV (at least 25 entries and about 95% of the advertised field) to measure real ownership, construction, and duplication."
            )
            lines.append("- Personal entry-history files remain useful for results, but are not treated as opponent fields.")

        lines.extend(["", "Real Field vs latest NFL SIM"])
        if latest_sim_comparison:
            real = dict(latest_sim_comparison.get("real") or {})
            simulated = dict(latest_sim_comparison.get("simulated") or {})
            real_profile = dict((real.get("ownership_profile") or {}).get("field") or {})
            sim_profile = dict(simulated.get("ownership_profile") or {})
            lines.append(
                f"- Preset: {latest_sim_comparison.get('preset', 'Unknown')} | "
                f"real fields {int(real.get('contests', 0) or 0)} / {int(real.get('entries', 0) or 0):,} entries | "
                + ("report-only comparison" if latest_sim_comparison.get("report_only") else "learned blend active")
            )
            if real.get("duplicate_entry_pct") is not None:
                lines.append(
                    f"- Duplicated entries: SIM {_safe_float(simulated.get('duplicate_entry_pct')):.1f}% | "
                    f"real {_safe_float(real.get('duplicate_entry_pct')):.1f}% | "
                    f"difference {_safe_float(simulated.get('duplicate_entry_pct')) - _safe_float(real.get('duplicate_entry_pct')):+.1f} points"
                )
            if real_profile.get("avg_total_ownership") is not None and sim_profile.get("avg_total_ownership") is not None:
                lines.append(
                    f"- Average lineup total ownership: SIM {_safe_float(sim_profile.get('avg_total_ownership')):.1f}% | "
                    f"real {_safe_float(real_profile.get('avg_total_ownership')):.1f}%"
                )
                lines.append(
                    f"- Average sub-5% players: SIM {_safe_float(sim_profile.get('avg_sub_five_players')):.2f} | "
                    f"real {_safe_float(real_profile.get('avg_sub_five_players')):.2f}; "
                    f"20%+ players: SIM {_safe_float(sim_profile.get('avg_twenty_plus_players')):.2f} | "
                    f"real {_safe_float(real_profile.get('avg_twenty_plus_players')):.2f}"
                )
            if real.get("avg_salary") is not None and simulated.get("avg_salary") is not None:
                lines.append(
                    f"- Average salary: SIM ${_safe_float(simulated.get('avg_salary')):,.0f} | "
                    f"real ${_safe_float(real.get('avg_salary')):,.0f}"
                )
        else:
            lines.append("- Generate and export an NFL Classic SIM portfolio after importing a matching preset field.")

        lines.extend([""] + _render_breakdown("Performance by sport", sport_groups))
        lines.extend([""] + _render_breakdown("Performance by salary used", salary_groups))
        lines.extend([""] + _render_breakdown("Performance by ownership", ownership_groups))
        lines.extend([""] + _render_breakdown("Performance by stack / construction", stack_groups))
        lines.extend([""] + _render_breakdown("Performance by context adjustment", context_groups))
        if sim_rows:
            lines.extend([""] + _render_breakdown("Performance by SIM Edge", sim_edge_groups))
            lines.extend([""] + _render_breakdown("Performance by tournament return index", sim_return_groups))
            lines.extend([""] + _render_breakdown("Performance by SIM leverage", sim_leverage_groups))
            lines.extend([""] + _render_breakdown("Performance by SIM duplication risk", sim_duplication_groups))
        lines.extend(["", "Guardrails"])
        if matched_rows < 25:
            lines.append(f"- No strategy tuning is recommended yet; collect at least 25 matched entries ({matched_rows}/25).")
        elif matched_rows < 100:
            lines.append("- Treat these as early signals. Avoid changing strategy from a single bucket or tournament.")
        else:
            lines.append("- Samples are useful for auditing, but contest selection and slate strength still affect ROI.")
        if imported_rows and matched_rows < imported_rows:
            lines.append("- Unmatched rows usually mean the final submitted lineup differed from the app export or the file lacks a parseable lineup.")

        return {
            "text": "\n".join(lines), "db_path": path, "export_count": export_count,
            "exported_lineups": exported_lineups, "historical_rows": imported_rows,
            "matched_rows": matched_rows, "matched_lineups": matched_lineups,
            "match_rate": match_rate, "net": net, "roi_pct": roi_pct,
            "cash_rate": statistics.mean(cash_values) if cash_values else None,
            "avg_percentile": statistics.mean(percentile_values) if percentile_values else None,
            "adjusted_mae": adjusted_mae, "base_mae": base_mae,
            "sim_matched_rows": len(sim_rows),
            "sim_edge_finish_correlation": edge_finish_corr,
            "sim_return_roi_correlation": return_roi_corr,
            "contest_roi_matched_rows": len(contest_roi_rows),
            "predicted_contest_roi_pct": predicted_contest_roi,
            "actual_contest_roi_pct": actual_contest_roi,
            "field_contests": field_contests,
            "field_entries": field_entries,
            "field_duplicate_pct": field_dup_pct,
            "field_top_one_duplicate_pct": field_top_dup_pct,
            "field_metadata_coverage_pct": field_metadata_coverage,
            "field_ownership_mae": field_ownership_mae,
            "latest_sim_field_comparison": latest_sim_comparison,
            "confidence": confidence, "outcome_confidence": outcome_confidence,
        }
    finally:
        conn.close()

# ---------------- Historical result import / folder helpers ----------------


def history_folder_structure() -> Dict[str, str]:
    """Create and return the app's local learning folders.

    - exports_auto: automatic copy of every DK export the app writes.
    - results_to_import: user drop-zone for old DK contest/result CSVs.
    - imported_results: optional archival copy of files successfully imported.
    - snapshots: full slate-state snapshots saved automatically with exports.
    """
    base = os.path.join(_base_dir(), "history")
    paths = {
        "history": base,
        "exports_auto": os.path.join(base, "exports_auto"),
        "results_to_import": os.path.join(base, "results_to_import"),
        "imported_results": os.path.join(base, "imported_results"),
        "snapshots": os.path.join(base, "snapshots"),
    }
    for p in paths.values():
        os.makedirs(p, exist_ok=True)
    return paths


def archive_export_file(export_path: str, *, sport: str = "", kind: str = "") -> Optional[str]:
    """Copy a completed export into history/exports_auto without changing workflow."""
    if not export_path or not os.path.exists(export_path):
        return None
    import shutil

    paths = history_folder_structure()
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.basename(export_path)
    prefix = "_".join(x.lower() for x in (sport, kind) if str(x or "").strip())
    filename = f"{stamp}_{prefix + '_' if prefix else ''}{base}"
    dest = os.path.join(paths["exports_auto"], filename)
    shutil.copy2(export_path, dest)
    return dest


def create_slate_snapshot(
    *,
    sport: str,
    kind: str,
    players: List[Dict[str, Any]],
    lineups: List[Any],
    rows: List[List[str]],
    headers: List[str],
    salary_cap: float,
    export_path: str,
    archive_path: str = "",
    source_csv_path: str = "",
    validation: Optional[Dict[str, Any]] = None,
    settings: Optional[Dict[str, Any]] = None,
    learning_payload: Optional[Dict[str, Any]] = None,
    app_version: str = "",
) -> Dict[str, Any]:
    """Write a full slate-state snapshot beside the learning database.

    This intentionally does not affect export success. It captures what the app
    knew at export time so future result imports/backtests can be interpreted in
    the exact slate context: salaries, projections, tags, ownership simulation,
    batting order/status, saved lineups, export rows, validation, settings, and
    version metadata.
    """
    import shutil
    import re

    folders = history_folder_structure()
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    sport_u = (sport or "UNKNOWN").strip().upper()
    kind_l = (kind or "unknown").strip().lower()
    export_id = str((learning_payload or {}).get("export_id") or "")[:8]
    safe_base = re.sub(r"[^A-Za-z0-9_.-]+", "_", os.path.splitext(os.path.basename(export_path or "export"))[0]).strip("_") or "export"
    folder_name = "_".join(x for x in [stamp, sport_u, kind_l, export_id, safe_base] if x)
    snap_dir = os.path.join(folders["snapshots"], folder_name)
    os.makedirs(snap_dir, exist_ok=True)

    def _json_dump(path: str, obj: Any) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, default=str)

    metadata = {
        "created_at": _now_iso(),
        "app_version": app_version,
        "sport": sport_u,
        "contest_type": kind_l,
        "salary_cap": float(salary_cap or 50000.0),
        "player_count": len(players or []),
        "lineup_count": len(lineups or []),
        "export_path": export_path or "",
        "archive_path": archive_path or "",
        "source_csv_path": source_csv_path or "",
        "learning_db": history_db_path(),
        "learning_export_id": (learning_payload or {}).get("export_id", ""),
        "settings": settings or {},
    }
    _json_dump(os.path.join(snap_dir, "metadata.json"), metadata)
    _json_dump(os.path.join(snap_dir, "validation.json"), validation or {})

    # Exact DK export rows as the app wrote them.
    with open(os.path.join(snap_dir, "exported_lineups.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers or [])
        writer.writerows(rows or [])

    # Human/analyzer-friendly lineup details.
    lineup_details: List[Dict[str, Any]] = []
    for idx, lineup in enumerate(lineups or [], start=1):
        pairs = _lineup_players(kind_l, lineup, sport_u)
        feature = _lineup_feature_fallback(kind_l, sport_u, lineup, salary_cap)
        lineup_details.append({
            "lineup_index": idx,
            "roster_ids": rows[idx - 1] if idx - 1 < len(rows or []) else [],
            "features": feature,
            "players": [
                {
                    "slot": slot,
                    "player_key": _pkey(p),
                    "player_id": str(p.get("CptID") if slot == "CPT" else p.get("FlexID") or ""),
                    "name": p.get("Name", ""),
                    "team": p.get("Team", ""),
                    "opponent": p.get("Opponent", ""),
                    "position": p.get("Position", ""),
                    "salary": _safe_float(p.get("CptSalary") if slot == "CPT" else p.get("FlexSalary")),
                    "projection": _safe_float(p.get("CptProjection") if slot == "CPT" else p.get("FlexProjection")),
                    "ownership_total": _safe_float(p.get("ProjOwnPct")),
                    "ownership_cpt": _safe_float(p.get("ProjCptOwnPct")),
                    "ownership_flex": _safe_float(p.get("ProjFlexOwnPct")),
                    "batting_order": _safe_int(p.get("BattingOrder"), 0),
                    "bats": p.get("Bats", ""),
                    "confirmed_lineup": bool(p.get("ConfirmedLineup")),
                    "lineup_status": p.get("LineupStatus", ""),
                    "injury_status": p.get("InjuryStatus", ""),
                    "tags": {
                        "lock_flex": bool(p.get("LockFlex")),
                        "fade_flex": bool(p.get("FadeFlex")),
                        "lock_cpt": bool(p.get("LockCpt")),
                        "fade_cpt": bool(p.get("FadeCpt")),
                    },
                }
                for slot, p in pairs
            ],
        })
    _json_dump(os.path.join(snap_dir, "lineups_detail.json"), lineup_details)

    # Player/slate table: wide enough for future learning, compact enough to inspect.
    player_fields = [
        "Name", "Team", "Position", "GameInfo", "FlexID", "CptID",
        "FlexSalary", "CptSalary", "BaseProjection", "FlexProjection", "CptProjection",
        "ProjOwnPct", "ProjCptOwnPct", "ProjFlexOwnPct", "MaxPct", "MaxCptPct",
        "LockFlex", "FadeFlex", "LockCpt", "FadeCpt", "InjuryStatus", "InjurySource",
        "BattingOrder", "Bats", "ConfirmedLineup", "LineupStatus",
        "NFLUsageScore", "NFLMatchupScore", "NFLRoleScore", "NFLWeatherScore", "NFLVegas", "NFLAdjScore", "NFLNotes",
        "MLBRecentForm", "MLBMatchup", "MLBBallpark", "MLBWeather", "MLBVegas", "MLBStack", "MLBHR", "MLBAdjScore", "MLBNotes", "TeamAdjPct",
    ]
    with open(os.path.join(snap_dir, "slate_players.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=player_fields, extrasaction="ignore")
        writer.writeheader()
        for p in players or []:
            writer.writerow({k: p.get(k, "") for k in player_fields})

    # Lossless-ish JSON version of player context for future migrations.
    _json_dump(os.path.join(snap_dir, "slate_players.json"), players or [])

    # Copy original salary input and final export/archive if available.
    copies: Dict[str, str] = {}
    for label, src, dest_name in [
        ("source_csv", source_csv_path, "source_salaries.csv"),
        ("export", export_path, "export_saved_original.csv"),
        ("archive", archive_path, "export_auto_archive.csv"),
    ]:
        try:
            if src and os.path.exists(src):
                dest = os.path.join(snap_dir, dest_name)
                shutil.copy2(src, dest)
                copies[label] = dest
        except Exception:
            pass
    if copies:
        _json_dump(os.path.join(snap_dir, "copied_files.json"), copies)

    return {"snapshot_dir": snap_dir, "files": len(os.listdir(snap_dir))}


def _money_to_float(x: Any) -> float:
    s = str(x or "").strip().replace("$", "").replace(",", "")
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    return _safe_float(s, 0.0)


def _canon_result_col(name: Any) -> str:
    s = str(name or "").replace("\ufeff", "").strip().lower()
    s = s.replace("_", " ").replace("-", " ")
    while "  " in s:
        s = s.replace("  ", " ")
    aliases = {
        "contest": "contest_name", "contest name": "contest_name", "contestname": "contest_name", "name": "contest_name",
        "entry": "entry_name", "entry name": "entry_name", "entryname": "entry_name", "lineup name": "entry_name",
        "entryid": "entry_id", "entry id": "entry_id",
        "entry fee": "entry_fee", "entryfee": "entry_fee", "fee": "entry_fee", "buy in": "entry_fee",
        "winnings": "winnings", "winning": "winnings", "prize": "winnings", "prizes": "winnings", "payout": "winnings",
        "fpts": "actual_points", "fantasy points": "actual_points", "fantasypoints": "actual_points", "points": "actual_points", "score": "actual_points",
        "rank": "rank", "place": "rank", "finish": "rank", "position": "rank",
        "lineup": "lineup", "roster": "lineup", "players": "lineup", "draft group": "draft_group", "draftgroup": "draft_group",
        "sport": "sport", "date": "slate_date", "contest date": "slate_date", "start date": "slate_date", "startdate": "slate_date",
        "entries": "field_size", "contest entries": "field_size", "field size": "field_size", "fieldsize": "field_size",
        "places paid": "places_paid", "placespaid": "places_paid", "paid places": "places_paid",
    }
    return aliases.get(s, s)


def _first_present(row: Dict[str, Any], keys: List[str], default: Any = "") -> Any:
    for k in keys:
        if k in row and str(row.get(k, "")).strip() != "":
            return row.get(k)
    return default


_ROSTER_POSITIONS = (
    "CAPTAIN", "CPT", "FLEX", "UTIL", "D/ST", "DST", "QB", "RB", "WR", "TE", "K",
    "SP", "RP", "P", "1B", "2B", "3B", "SS", "OF", "C", "PG", "SG", "SF", "PF", "G", "F",
)
_ROSTER_MARKER = re.compile(
    r"(?:^|\s)(" + "|".join(re.escape(x) for x in _ROSTER_POSITIONS) + r")(?=\s)",
    flags=re.I,
)


def _normalize_roster_token(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    id_match = re.fullmatch(r"\s*(\d{4,12})\s*", text)
    if id_match:
        return id_match.group(1)
    text = re.sub(r"^\s*(?:captain|cpt|flex|util|d/st|dst|qb|rb|wr|te|k|sp|rp|p|1b|2b|3b|ss|of|c|pg|sg|sf|pf|g|f)\s+", "", text, flags=re.I)
    text = re.sub(r"\s*\(\s*\d{4,12}\s*\)\s*$", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    parts = text.split()
    if parts and parts[-1] in {"jr", "sr", "ii", "iii", "iv", "v"}:
        parts.pop()
    return " ".join(parts)


def _roster_signature(tokens: List[Any]) -> tuple[str, ...]:
    normalized = [_normalize_roster_token(token) for token in tokens]
    return tuple(sorted(token for token in normalized if token))


def _extract_lineup_tokens(row: Dict[str, Any]) -> List[str]:
    """Extract likely DK player IDs from a result/entry row.

    DK exports vary. We accept a Lineup/Roster field or position columns. Numeric
    IDs are preferred, but name-like tokens are stored when IDs are absent.
    """
    lineup_text = str(_first_present(row, ["lineup", "roster", "players"], "") or "")
    pos_cols = ["cpt", "captain", "flex", "qb", "rb", "rb1", "rb2", "wr", "wr1", "wr2", "wr3", "te", "dst", "p", "p1", "p2", "c", "1b", "2b", "3b", "ss", "of", "of1", "of2", "of3", "pg", "sg", "sf", "pf", "g", "f", "util"]
    parts: List[str] = []
    if lineup_text:
        nums = re.findall(r"\b\d{4,12}\b", lineup_text)
        if nums:
            parts.extend(nums)
        else:
            matches = list(_ROSTER_MARKER.finditer(lineup_text))
            if matches:
                for i, match in enumerate(matches):
                    start = match.end()
                    end = matches[i + 1].start() if i + 1 < len(matches) else len(lineup_text)
                    name = lineup_text[start:end].strip(" ,;|/")
                    if name:
                        parts.append(name)
            else:
                parts.extend(x.strip() for x in re.split(r"[;,|]", lineup_text) if x.strip())
    for k, v in row.items():
        lk = str(k or "").strip().lower()
        if lk in pos_cols or any(lk.startswith(pc) for pc in ("flex", "of", "wr", "rb")):
            val = str(v or "").strip()
            if not val:
                continue
            nums = re.findall(r"\b\d{4,12}\b", val)
            parts.extend(nums or [val])
    out: List[str] = []
    for p in parts:
        pp = str(p or "").strip()
        if pp:
            out.append(pp)
    return out


def _infer_sport_from_file_or_row(path: str, row: Dict[str, Any]) -> str:
    val = str(_first_present(row, ["sport"], "") or "").upper()
    text = (val + " " + os.path.basename(path).upper())
    for s in ("NFL", "MLB", "NBA", "WNBA"):
        if s in text:
            return s
    lineup = str(_first_present(row, ["lineup", "roster", "players"], "") or "").upper()
    if re.search(r"\bQB\b", lineup) and re.search(r"\b(?:RB|WR|TE|DST|D/ST)\b", lineup):
        return "NFL"
    if re.search(r"\b(?:SP|RP|1B|2B|3B|SS|OF)\b", lineup):
        return "MLB"
    if re.search(r"\b(?:PG|SG|SF|PF)\b", lineup):
        return "NBA"
    return "UNKNOWN"


def init_historical_import_tables(conn: sqlite3.Connection) -> None:
    init_db(conn)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS historical_imports (
            import_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            source_path TEXT,
            file_name TEXT,
            sport TEXT,
            rows_imported INTEGER,
            file_sha256 TEXT,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS historical_results (
            result_id TEXT PRIMARY KEY,
            import_id TEXT NOT NULL,
            source_file TEXT,
            row_index INTEGER,
            sport TEXT,
            slate_date TEXT,
            contest_name TEXT,
            entry_name TEXT,
            entry_fee REAL,
            winnings REAL,
            roi REAL,
            actual_points REAL,
            rank_text TEXT,
            lineup_tokens_json TEXT,
            raw_json TEXT,
            field_size INTEGER,
            places_paid INTEGER,
            percentile REAL,
            cashed INTEGER,
            top_one_pct INTEGER,
            matched_lineup_id TEXT,
            matched_export_id TEXT,
            match_method TEXT,
            FOREIGN KEY(import_id) REFERENCES historical_imports(import_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS historical_result_players (
            hist_player_id TEXT PRIMARY KEY,
            result_id TEXT NOT NULL,
            token TEXT,
            slot_index INTEGER,
            FOREIGN KEY(result_id) REFERENCES historical_results(result_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS contest_field_summaries (
            field_id TEXT PRIMARY KEY,
            import_id TEXT NOT NULL,
            contest_key TEXT NOT NULL,
            sport TEXT,
            contest_name TEXT,
            field_preset TEXT,
            entry_count INTEGER,
            field_size INTEGER,
            roster_size INTEGER,
            metadata_coverage_pct REAL,
            unique_lineups INTEGER,
            duplicated_entries INTEGER,
            duplicate_entry_pct REAL,
            max_duplicate_count INTEGER,
            top_one_entries INTEGER,
            top_one_duplicate_pct REAL,
            avg_salary REAL,
            salary_p10 REAL,
            avg_unused_salary REAL,
            stack_rates_json TEXT,
            bringback_rates_json TEXT,
            flex_rates_json TEXT,
            ownership_json TEXT,
            ownership_profile_json TEXT,
            ownership_mae REAL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(import_id) REFERENCES historical_imports(import_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_hist_results_import ON historical_results(import_id);
        CREATE INDEX IF NOT EXISTS idx_hist_results_sport ON historical_results(sport);
        CREATE INDEX IF NOT EXISTS idx_hist_players_token ON historical_result_players(token);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_field_import_contest ON contest_field_summaries(import_id, contest_key);
        CREATE INDEX IF NOT EXISTS idx_field_sport_preset ON contest_field_summaries(sport, field_preset);
        """
    )
    _ensure_column(conn, "historical_imports", "file_sha256", "TEXT")
    for column, definition in (
        ("field_size", "INTEGER"), ("places_paid", "INTEGER"), ("percentile", "REAL"),
        ("cashed", "INTEGER"), ("top_one_pct", "INTEGER"),
        ("matched_lineup_id", "TEXT"), ("matched_export_id", "TEXT"), ("match_method", "TEXT"),
    ):
        _ensure_column(conn, "historical_results", column, definition)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hist_results_lineup ON historical_results(matched_lineup_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hist_import_hash ON historical_imports(file_sha256)")
    _ensure_column(conn, "contest_field_summaries", "ownership_profile_json", "TEXT")
    conn.commit()


def _parse_rank(value: Any) -> int:
    match = re.search(r"\d+", str(value or "").replace(",", ""))
    return int(match.group(0)) if match else 0


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finalize_import_outcomes(conn: sqlite3.Connection, import_id: str) -> None:
    rows = conn.execute(
        """
        SELECT result_id, contest_name, source_file, rank_text, winnings, entry_fee,
               field_size, places_paid, raw_json
        FROM historical_results WHERE import_id=?
        """,
        (import_id,),
    ).fetchall()
    grouped: Dict[str, List[tuple]] = {}
    for row in rows:
        key = str(row[1] or row[2] or import_id)
        grouped.setdefault(key, []).append(row)
    for contest_rows in grouped.values():
        ranks = [_parse_rank(row[3]) for row in contest_rows]
        explicit_size = max((_safe_int(row[6], 0) for row in contest_rows), default=0)
        if not explicit_size:
            for row in contest_rows:
                rank_size = re.search(r"(?:\bof\b|/)\s*([\d,]+)", str(row[3] or ""), flags=re.I)
                if rank_size:
                    explicit_size = max(explicit_size, _safe_int(rank_size.group(1).replace(",", ""), 0))
        max_rank = max(ranks or [0])
        inferred_size = len(contest_rows) if len(contest_rows) >= 5 and max_rank <= len(contest_rows) else 0
        field_size = explicit_size or inferred_size
        places_paid = max((_safe_int(row[7], 0) for row in contest_rows), default=0)
        for row, rank in zip(contest_rows, ranks):
            raw = {}
            try:
                raw = json.loads(row[8] or "{}")
            except Exception:
                pass
            winnings_present = "winnings" in raw and str(raw.get("winnings", "")).strip() != ""
            cashed: Optional[int]
            if _safe_float(row[4], 0.0) > 0:
                cashed = 1
            elif places_paid > 0 and rank > 0:
                cashed = 1 if rank <= places_paid else 0
            elif winnings_present:
                cashed = 0
            else:
                cashed = None
            percentile = None
            top_one = None
            if field_size > 0 and rank > 0:
                percentile = 100.0 * (1.0 - (rank - 1) / max(1, field_size))
                percentile = max(0.0, min(100.0, percentile))
                top_one = 1 if rank <= max(1, math.ceil(field_size * 0.01)) else 0
            conn.execute(
                """
                UPDATE historical_results
                SET field_size=?, places_paid=?, percentile=?, cashed=?, top_one_pct=?
                WHERE result_id=?
                """,
                (field_size or None, places_paid or None, percentile, cashed, top_one, row[0]),
            )


def _infer_field_preset(value: Any) -> str:
    """Infer a contest entry-limit preset only when the label is explicit."""
    raw = str(value or "").lower()
    entry_limits = [
        _safe_int(match, 0)
        for match in re.findall(r"\(\s*\d+\s*/\s*(\d+)\s*\)", raw)
    ]
    max_limit = max(entry_limits or [0])
    if max_limit >= 150:
        return "150-Max"
    if max_limit == 20:
        return "20-Max"
    if max_limit == 3:
        return "3-Max"
    if max_limit == 1:
        return "Single Entry"
    text = re.sub(r"[^a-z0-9]+", " ", raw).strip()
    if re.search(r"\b(single entry|single|se)\b", text):
        return "Single Entry"
    if re.search(r"\b(3 max|three max|3 entry|three entry)\b", text):
        return "3-Max"
    if re.search(r"\b(20 max|twenty max|20 entry|twenty entry)\b", text):
        return "20-Max"
    if re.search(r"\b(150 max|one hundred fifty max|150 entry)\b", text):
        return "150-Max"
    return "Unclassified"


def _percentile_value(values: List[float], fraction: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * fraction))))
    return ordered[index]


_OWNERSHIP_BUCKETS = (
    (180.0, "Under 180"),
    (220.0, "180-219"),
    (250.0, "220-249"),
    (280.0, "250-279"),
    (float("inf"), "280+"),
)


def _ownership_bucket(total_ownership: float) -> str:
    for ceiling, label in _OWNERSHIP_BUCKETS:
        if total_ownership < ceiling:
            return label
    return "280+"


def _new_ownership_profile_accumulator() -> Dict[str, Any]:
    return {
        "field": Counter(),
        "top_one": Counter(),
        "bucket_entries": Counter(),
        "bucket_top_one": Counter(),
        "signature_buckets": {},
        "mapped_slots": 0,
        "total_slots": 0,
    }


def _add_ownership_profile(
    accumulator: Dict[str, Any],
    signature: tuple[str, ...],
    signature_key: Any,
    ownership: Dict[str, float],
    *,
    top_one: bool,
) -> None:
    accumulator["total_slots"] += len(signature)
    values = [ownership.get(token) for token in signature]
    accumulator["mapped_slots"] += sum(value is not None for value in values)
    if not values or not all(value is not None for value in values):
        return
    typed = [float(value) for value in values if value is not None]
    total = sum(typed)
    metrics = {
        "lineups": 1.0,
        "total_ownership": total,
        "player_ownership": total / max(1, len(typed)),
        "sub_five_players": float(sum(value < 5.0 for value in typed)),
        "sub_ten_players": float(sum(value < 10.0 for value in typed)),
        "twenty_plus_players": float(sum(value >= 20.0 for value in typed)),
        "thirty_plus_players": float(sum(value >= 30.0 for value in typed)),
        "log_ownership_product": sum(math.log(max(0.05, value) / 100.0) for value in typed),
    }
    accumulator["field"].update(metrics)
    bucket = _ownership_bucket(total)
    accumulator["bucket_entries"][bucket] += 1
    accumulator["signature_buckets"].setdefault(signature_key, bucket)
    if top_one:
        accumulator["top_one"].update(metrics)
        accumulator["bucket_top_one"][bucket] += 1


def _finalize_ownership_profile(
    accumulator: Dict[str, Any],
    signature_counts: Counter,
    *,
    source_vs_computed_mae: Optional[float] = None,
) -> Dict[str, Any]:
    def averages(counter: Counter) -> Dict[str, Any]:
        count = int(counter.get("lineups", 0) or 0)
        result: Dict[str, Any] = {"lineups": count}
        for key in (
            "total_ownership", "player_ownership", "sub_five_players",
            "sub_ten_players", "twenty_plus_players", "thirty_plus_players",
            "log_ownership_product",
        ):
            result[f"avg_{key}"] = float(counter.get(key, 0.0)) / max(1, count)
        return result

    bucket_duplicates: Counter[str] = Counter()
    for signature_key, bucket in accumulator["signature_buckets"].items():
        count = int(signature_counts.get(signature_key, 0) or 0)
        if count > 1:
            bucket_duplicates[bucket] += count
    bucket_rows: Dict[str, Dict[str, Any]] = {}
    for _, label in _OWNERSHIP_BUCKETS:
        entries = int(accumulator["bucket_entries"].get(label, 0) or 0)
        top_one = int(accumulator["bucket_top_one"].get(label, 0) or 0)
        duplicated = int(bucket_duplicates.get(label, 0) or 0)
        bucket_rows[label] = {
            "entries": entries,
            "field_pct": entries / max(1, int(accumulator["field"].get("lineups", 0))) * 100.0,
            "top_one_entries": top_one,
            "top_one_rate": top_one / max(1, entries) * 100.0,
            "duplicated_entries": duplicated,
            "duplicate_pct": duplicated / max(1, entries) * 100.0,
        }
    return {
        "ownership_coverage_pct": accumulator["mapped_slots"] / max(1, accumulator["total_slots"]) * 100.0,
        "source_vs_computed_mae": source_vs_computed_mae,
        "field": averages(accumulator["field"]),
        "top_one": averages(accumulator["top_one"]),
        "buckets": bucket_rows,
    }


def _player_metadata_lookup(conn: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
    """Index the newest player metadata captured with saved lineup exports."""
    lookup: Dict[str, Dict[str, Any]] = {}
    rows = conn.execute(
        """
        SELECT lp.player_id, lp.player_key, lp.name, lp.team, lp.opponent,
               lp.position, lp.salary, lp.ownership
        FROM lineup_players lp
        JOIN lineups l ON l.lineup_id=lp.lineup_id
        JOIN exports e ON e.export_id=l.export_id
        ORDER BY e.created_at DESC, l.lineup_index ASC
        """
    ).fetchall()
    for player_id, player_key_value, name, team, opponent, position, salary, ownership in rows:
        meta = {
            "name": str(name or ""),
            "team": str(team or "").strip().upper(),
            "opponent": str(opponent or "").strip().upper(),
            "position": str(position or "").strip().upper().replace("D/ST", "DST").split("/")[0],
            "salary": _safe_float(salary, 0.0),
            "ownership": _safe_float(ownership, 0.0),
        }
        keys = {
            _normalize_roster_token(player_id),
            _normalize_roster_token(player_key_value),
            _normalize_roster_token(name),
        }
        for key in keys:
            if key:
                lookup.setdefault(key, meta)
    return lookup


def _analyze_imported_fields(conn: sqlite3.Connection, import_id: str) -> Dict[str, Any]:
    """Summarize complete contest standings without mistaking entry history for a field."""
    rows = conn.execute(
        """
        SELECT result_id, contest_name, entry_name, source_file, sport,
               lineup_tokens_json, field_size, top_one_pct
        FROM historical_results
        WHERE import_id=?
        ORDER BY row_index
        """,
        (import_id,),
    ).fetchall()
    grouped: Dict[str, List[tuple]] = {}
    for row in rows:
        contest_key = str(row[1] or row[3] or import_id).strip()
        grouped.setdefault(contest_key, []).append(row)

    metadata = _player_metadata_lookup(conn)
    analyzed_contests = 0
    analyzed_entries = 0
    presets: Counter[str] = Counter()
    for contest_key, contest_rows in grouped.items():
        parsed: List[Dict[str, Any]] = []
        for row in contest_rows:
            try:
                tokens = json.loads(row[5] or "[]")
            except Exception:
                tokens = []
            signature = _roster_signature(tokens)
            if signature:
                parsed.append({
                    "row": row,
                    "tokens": list(tokens),
                    "signature": signature,
                })
        if len(parsed) < 25:
            continue
        roster_sizes = Counter(len(item["signature"]) for item in parsed)
        roster_size = int(roster_sizes.most_common(1)[0][0])
        parsed = [item for item in parsed if len(item["signature"]) == roster_size]
        entry_count = len(parsed)
        field_size = max((_safe_int(item["row"][6], 0) for item in parsed), default=0)
        # A complete standings file should contain essentially the advertised
        # field. This protects calibration from ordinary "my entries" exports.
        if field_size <= 0 or entry_count < max(25, int(math.ceil(field_size * 0.95))):
            continue

        signature_counts = Counter(item["signature"] for item in parsed)
        duplicated_entries = sum(count for count in signature_counts.values() if count > 1)
        top_items = [item for item in parsed if _safe_int(item["row"][7], 0) == 1]
        top_duplicated = sum(
            1 for item in top_items if signature_counts[item["signature"]] > 1
        )
        player_counts: Counter[str] = Counter(
            token for item in parsed for token in item["signature"]
        )
        actual_ownership = {
            token: count / max(1, entry_count) * 100.0
            for token, count in player_counts.items()
        }
        ownership_accumulator = _new_ownership_profile_accumulator()
        for item in parsed:
            _add_ownership_profile(
                ownership_accumulator,
                item["signature"],
                item["signature"],
                actual_ownership,
                top_one=_safe_int(item["row"][7], 0) == 1,
            )
        ownership_profile = _finalize_ownership_profile(
            ownership_accumulator,
            signature_counts,
        )

        mapped_slots = 0
        total_slots = entry_count * roster_size
        salaries: List[float] = []
        stack_counts: Counter[str] = Counter()
        bringback_trials: Counter[str] = Counter()
        bringback_hits: Counter[str] = Counter()
        flex_counts: Counter[str] = Counter()
        for item in parsed:
            lineup_meta = [metadata.get(token) for token in item["signature"]]
            mapped_slots += sum(1 for meta in lineup_meta if meta)
            if all(lineup_meta):
                salary = sum(_safe_float(meta.get("salary"), 0.0) for meta in lineup_meta if meta)
                if salary > 0:
                    salaries.append(salary)
            if roster_size != 9 or not all(lineup_meta):
                continue
            typed_meta = [meta for meta in lineup_meta if meta]
            quarterbacks = [meta for meta in typed_meta if meta.get("position") == "QB"]
            if len(quarterbacks) != 1:
                continue
            qb = quarterbacks[0]
            stack_count = sum(
                1 for meta in typed_meta
                if meta.get("team") == qb.get("team") and meta.get("position") in {"WR", "TE"}
            )
            stack_key = str(min(3, stack_count))
            stack_counts[stack_key] += 1
            bring_key = "2_plus" if stack_count >= 2 else str(stack_count)
            bringback_trials[bring_key] += 1
            if qb.get("opponent") and any(
                meta.get("team") == qb.get("opponent")
                and meta.get("position") in {"RB", "WR", "TE"}
                for meta in typed_meta
            ):
                bringback_hits[bring_key] += 1
            position_counts = Counter(str(meta.get("position") or "") for meta in typed_meta)
            excess = {
                "RB": position_counts["RB"] - 2,
                "WR": position_counts["WR"] - 3,
                "TE": position_counts["TE"] - 1,
            }
            flex_pos = max(excess, key=lambda pos: excess[pos])
            if excess[flex_pos] > 0:
                flex_counts[flex_pos] += 1

        construction_count = sum(stack_counts.values())
        stack_rates = {
            str(value): stack_counts[str(value)] / max(1, construction_count)
            for value in range(4)
        }
        bringback_rates = {
            key: bringback_hits[key] / max(1, bringback_trials[key])
            for key in ("0", "1", "2_plus")
        }
        flex_total = sum(flex_counts.values())
        flex_rates = {
            pos: flex_counts[pos] / max(1, flex_total)
            for pos in ("RB", "WR", "TE")
        }
        ownership_errors = [
            abs(actual - _safe_float(metadata[token].get("ownership"), 0.0))
            for token, actual in actual_ownership.items()
            if token in metadata and _safe_float(metadata[token].get("ownership"), 0.0) > 0
        ]
        contest_name = str(parsed[0]["row"][1] or contest_key)
        source_file = str(parsed[0]["row"][3] or "")
        sport = str(parsed[0]["row"][4] or "UNKNOWN").upper()
        entry_limit_hint = max(
            (
                _safe_int(match, 0)
                for item in parsed
                for match in re.findall(r"\(\s*\d+\s*/\s*(\d+)\s*\)", str(item["row"][2] or ""))
            ),
            default=0,
        )
        preset = _infer_field_preset(
            f"{contest_name} {source_file}"
            + (f" (1/{entry_limit_hint})" if entry_limit_hint else "")
        )
        salary_p10 = _percentile_value(salaries, 0.10)
        conn.execute(
            """
            INSERT OR REPLACE INTO contest_field_summaries (
                field_id, import_id, contest_key, sport, contest_name, field_preset,
                entry_count, field_size, roster_size, metadata_coverage_pct,
                unique_lineups, duplicated_entries, duplicate_entry_pct,
                max_duplicate_count, top_one_entries, top_one_duplicate_pct,
                avg_salary, salary_p10, avg_unused_salary, stack_rates_json,
                bringback_rates_json, flex_rates_json, ownership_json,
                ownership_profile_json, ownership_mae, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()), import_id, contest_key, sport, contest_name, preset,
                entry_count, field_size, roster_size,
                mapped_slots / max(1, total_slots) * 100.0,
                len(signature_counts), duplicated_entries,
                duplicated_entries / max(1, entry_count) * 100.0,
                max(signature_counts.values() or [0]), len(top_items),
                top_duplicated / max(1, len(top_items)) * 100.0 if top_items else None,
                statistics.mean(salaries) if salaries else None, salary_p10,
                50000.0 - statistics.mean(salaries) if salaries and sport == "NFL" else None,
                json.dumps(stack_rates), json.dumps(bringback_rates), json.dumps(flex_rates),
                json.dumps(actual_ownership), json.dumps(ownership_profile),
                statistics.mean(ownership_errors) if ownership_errors else None,
                _now_iso(),
            ),
        )
        analyzed_contests += 1
        analyzed_entries += entry_count
        presets[preset] += 1
    return {
        "contests": analyzed_contests,
        "entries": analyzed_entries,
        "presets": dict(presets),
    }


def match_historical_results(
    conn: sqlite3.Connection,
    *,
    result_ids: Optional[List[str]] = None,
    import_ids: Optional[List[str]] = None,
) -> Dict[str, int]:
    """Link imported result rows to the newest exact exported roster."""
    init_historical_import_tables(conn)
    lineup_rows = conn.execute(
        """
        SELECT l.lineup_id, l.export_id, l.roster_ids_json
        FROM lineups l JOIN exports e ON e.export_id=l.export_id
        ORDER BY e.created_at DESC, l.lineup_index ASC
        """
    ).fetchall()
    id_lookup: Dict[tuple[str, ...], tuple[str, str]] = {}
    name_lookup: Dict[tuple[str, ...], tuple[str, str]] = {}
    player_names_by_lineup: Dict[str, List[str]] = {}
    for lineup_id, name in conn.execute(
        "SELECT lineup_id, name FROM lineup_players ORDER BY lineup_id, slot"
    ).fetchall():
        player_names_by_lineup.setdefault(str(lineup_id), []).append(str(name or ""))
    for lineup_id, export_id, roster_json in lineup_rows:
        try:
            roster_ids = json.loads(roster_json or "[]")
        except Exception:
            roster_ids = []
        id_sig = _roster_signature(roster_ids)
        if id_sig:
            id_lookup.setdefault(id_sig, (lineup_id, export_id))
        player_names = player_names_by_lineup.get(str(lineup_id), [])
        name_sig = _roster_signature(player_names)
        if name_sig:
            name_lookup.setdefault(name_sig, (lineup_id, export_id))

    sql = "SELECT result_id, lineup_tokens_json FROM historical_results WHERE matched_lineup_id IS NULL"
    params: List[Any] = []
    if import_ids:
        placeholders = ",".join("?" for _ in import_ids)
        sql += f" AND import_id IN ({placeholders})"
        params.extend(import_ids)
    elif result_ids:
        placeholders = ",".join("?" for _ in result_ids)
        sql += f" AND result_id IN ({placeholders})"
        params.extend(result_ids)
    result_rows = conn.execute(sql, params).fetchall()
    matched = 0
    for result_id, lineup_json in result_rows:
        try:
            tokens = json.loads(lineup_json or "[]")
        except Exception:
            tokens = []
        signature = _roster_signature(tokens)
        if not signature:
            continue
        target = None
        method = ""
        if all(token.isdigit() for token in signature):
            target = id_lookup.get(signature)
            method = "player_ids"
        if target is None:
            target = name_lookup.get(signature)
            method = "player_names"
        if target is None:
            continue
        conn.execute(
            """
            UPDATE historical_results
            SET matched_lineup_id=?, matched_export_id=?, match_method=?
            WHERE result_id=?
            """,
            (target[0], target[1], method, result_id),
        )
        matched += 1

    linked_lineups = [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT matched_lineup_id FROM historical_results WHERE matched_lineup_id IS NOT NULL"
        ).fetchall()
    ]
    for lineup_id in linked_lineups:
        aggregate = conn.execute(
            """
            SELECT AVG(actual_points), AVG(roi), MAX(cashed), MAX(top_one_pct)
            FROM historical_results WHERE matched_lineup_id=?
            """,
            (lineup_id,),
        ).fetchone()
        conn.execute(
            "UPDATE lineups SET actual_points=?, roi=?, cashed=?, top_one_pct=? WHERE lineup_id=?",
            (aggregate[0], aggregate[1], aggregate[2], aggregate[3], lineup_id),
        )
    conn.commit()
    return {"matched": matched, "unmatched": max(0, len(result_rows) - matched)}


def _preflight_complete_field_csv(
    path: str,
    *,
    progress_callback: Optional[Any] = None,
    cancel_callback: Optional[Any] = None,
) -> Dict[str, Any]:
    """Identify a complete standings export before creating row-level records."""
    result: Dict[str, Any] = {"complete": False}
    with open(path, "r", newline="", encoding="utf-8-sig") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except Exception:
            dialect = csv.excel
        reader = csv.DictReader(handle, dialect=dialect)
        headers = list(reader.fieldnames or [])
        source_by_canon: Dict[str, str] = {}
        for header in headers:
            source_by_canon.setdefault(_canon_result_col(header), header)
        rank_col = source_by_canon.get("rank")
        lineup_col = source_by_canon.get("lineup")
        entry_col = source_by_canon.get("entry_name")
        entry_id_col = source_by_canon.get("entry_id")
        contest_col = source_by_canon.get("contest_name")
        field_size_col = source_by_canon.get("field_size")
        player_col = source_by_canon.get("player")
        drafted_col = source_by_canon.get("%drafted") or source_by_canon.get("drafted")
        if not rank_col or not lineup_col or not (entry_col or entry_id_col):
            return result

        entry_rows = 0
        lineup_rows = 0
        max_rank = 0
        explicit_size = 0
        contest_names: set[str] = set()
        max_entry_limit = 0
        sport = "UNKNOWN"
        source_ownership: Dict[str, float] = {}
        for raw in reader:
            if entry_rows % 5000 == 0:
                if cancel_callback and cancel_callback():
                    raise _ImportCancelled()
                if progress_callback:
                    progress_callback(entry_rows, 0, f"Inspecting {os.path.basename(path)}")
            rank = _parse_rank(raw.get(rank_col))
            entry_id = str(raw.get(entry_id_col, "") or "").strip() if entry_id_col else ""
            if rank <= 0 and not entry_id:
                continue
            entry_rows += 1
            max_rank = max(max_rank, rank)
            lineup_text = str(raw.get(lineup_col, "") or "").strip()
            if lineup_text:
                lineup_rows += 1
                if sport == "UNKNOWN":
                    sport = _infer_sport_from_file_or_row(path, {"lineup": lineup_text})
            if field_size_col:
                explicit_size = max(explicit_size, _safe_int(raw.get(field_size_col), 0))
            if contest_col:
                contest_name = str(raw.get(contest_col, "") or "").strip()
                if contest_name:
                    contest_names.add(contest_name)
                    if len(contest_names) > 1:
                        return result
            if entry_col:
                for match in re.findall(
                    r"\(\s*\d+\s*/\s*(\d+)\s*\)",
                    str(raw.get(entry_col, "") or ""),
                ):
                    max_entry_limit = max(max_entry_limit, _safe_int(match, 0))
            if player_col and drafted_col:
                player_name = _normalize_roster_token(raw.get(player_col))
                drafted = str(raw.get(drafted_col, "") or "").strip().rstrip("%")
                if player_name and drafted:
                    value = _safe_float(drafted, -1.0)
                    if value >= 0:
                        source_ownership[player_name] = value
        if progress_callback:
            progress_callback(0, entry_rows, f"Standings format recognized: {entry_rows:,} rows")

    inferred_size = (
        entry_rows
        if entry_rows >= 25 and entry_rows * 0.90 <= max_rank <= entry_rows
        else 0
    )
    field_size = explicit_size or inferred_size
    complete = bool(
        field_size >= 25
        and entry_rows >= math.ceil(field_size * 0.95)
        and lineup_rows >= math.ceil(entry_rows * 0.95)
    )
    contest_name = next(iter(contest_names), "")
    preset_hint = f"{contest_name} {os.path.basename(path)}"
    if max_entry_limit:
        preset_hint += f" (1/{max_entry_limit})"
    return {
        "complete": complete,
        "headers": headers,
        "entry_rows": entry_rows,
        "lineup_rows": lineup_rows,
        "field_size": field_size,
        "max_rank": max_rank,
        "sport": sport,
        "contest_name": contest_name or os.path.basename(path),
        "contest_key": contest_name or os.path.basename(path),
        "field_preset": _infer_field_preset(preset_hint),
        "max_entry_limit": max_entry_limit,
        "source_ownership": source_ownership,
    }


def _stream_complete_field_summary(
    conn: sqlite3.Connection,
    path: str,
    import_id: str,
    preflight: Dict[str, Any],
    *,
    progress_callback: Optional[Any] = None,
    cancel_callback: Optional[Any] = None,
    metadata_override: Optional[Dict[str, Dict[str, Any]]] = None,
    replace_field_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Summarize a large complete field without a multi-million-row database expansion."""
    metadata = _player_metadata_lookup(conn)
    metadata.update(metadata_override or {})
    signature_counts: Counter[bytes] = Counter()
    player_counts: Counter[str] = Counter()
    roster_sizes: Counter[int] = Counter()
    top_signatures: List[bytes] = []
    mapped_slots = 0
    total_slots = 0
    salaries: List[float] = []
    stack_counts: Counter[str] = Counter()
    bringback_trials: Counter[str] = Counter()
    bringback_hits: Counter[str] = Counter()
    flex_counts: Counter[str] = Counter()
    field_size = _safe_int(preflight.get("field_size"), 0)
    top_cutoff = max(1, math.ceil(field_size * 0.01))
    source_ownership = {
        str(key): _safe_float(value)
        for key, value in dict(preflight.get("source_ownership") or {}).items()
    }
    ownership_accumulator = _new_ownership_profile_accumulator()

    with open(path, "r", newline="", encoding="utf-8-sig") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except Exception:
            dialect = csv.excel
        reader = csv.DictReader(handle, dialect=dialect)
        source_by_canon: Dict[str, str] = {}
        for header in reader.fieldnames or []:
            source_by_canon.setdefault(_canon_result_col(header), header)
        lineup_col = source_by_canon["lineup"]
        rank_col = source_by_canon["rank"]
        processed_rows = 0
        total_rows = _safe_int(preflight.get("entry_rows"), 0)
        for raw in reader:
            processed_rows += 1
            if processed_rows % 2500 == 0:
                if cancel_callback and cancel_callback():
                    raise _ImportCancelled()
                if progress_callback:
                    progress_callback(
                        min(processed_rows, total_rows), total_rows,
                        f"Analyzing opponent field: {processed_rows:,}/{total_rows:,}",
                    )
            lineup_text = str(raw.get(lineup_col, "") or "").strip()
            if not lineup_text:
                continue
            tokens = _extract_lineup_tokens({"lineup": lineup_text})
            signature = _roster_signature(tokens)
            if not signature:
                continue
            roster_sizes[len(signature)] += 1
            digest = hashlib.blake2b(
                "\x1f".join(signature).encode("utf-8"),
                digest_size=12,
            ).digest()
            signature_counts[digest] += 1
            player_counts.update(signature)
            rank = _parse_rank(raw.get(rank_col))
            is_top_one = rank <= top_cutoff
            if is_top_one:
                top_signatures.append(digest)
            if source_ownership:
                _add_ownership_profile(
                    ownership_accumulator,
                    signature,
                    digest,
                    source_ownership,
                    top_one=is_top_one,
                )

            lineup_meta = [metadata.get(token) for token in signature]
            mapped_slots += sum(1 for meta in lineup_meta if meta)
            total_slots += len(signature)
            if all(lineup_meta):
                salary = sum(_safe_float(meta.get("salary"), 0.0) for meta in lineup_meta if meta)
                if salary > 0:
                    salaries.append(salary)
            if len(signature) != 9 or not all(lineup_meta):
                continue
            typed_meta = [meta for meta in lineup_meta if meta]
            quarterbacks = [meta for meta in typed_meta if meta.get("position") == "QB"]
            if len(quarterbacks) != 1:
                continue
            qb = quarterbacks[0]
            stack_count = sum(
                1 for meta in typed_meta
                if meta.get("team") == qb.get("team") and meta.get("position") in {"WR", "TE"}
            )
            stack_key = str(min(3, stack_count))
            stack_counts[stack_key] += 1
            bring_key = "2_plus" if stack_count >= 2 else str(stack_count)
            bringback_trials[bring_key] += 1
            if qb.get("opponent") and any(
                meta.get("team") == qb.get("opponent")
                and meta.get("position") in {"RB", "WR", "TE"}
                for meta in typed_meta
            ):
                bringback_hits[bring_key] += 1
            position_counts = Counter(str(meta.get("position") or "") for meta in typed_meta)
            excess = {
                "RB": position_counts["RB"] - 2,
                "WR": position_counts["WR"] - 3,
                "TE": position_counts["TE"] - 1,
            }
            flex_pos = max(excess, key=lambda pos: excess[pos])
            if excess[flex_pos] > 0:
                flex_counts[flex_pos] += 1
        if cancel_callback and cancel_callback():
            raise _ImportCancelled()
        if progress_callback:
            progress_callback(total_rows, total_rows, "Finalizing field patterns")

    entry_count = sum(roster_sizes.values())
    roster_size = int(roster_sizes.most_common(1)[0][0]) if roster_sizes else 0
    duplicated_entries = sum(count for count in signature_counts.values() if count > 1)
    top_duplicated = sum(1 for digest in top_signatures if signature_counts[digest] > 1)
    actual_ownership = {
        token: count / max(1, entry_count) * 100.0
        for token, count in player_counts.items()
    }
    ownership_errors = [
        abs(actual - _safe_float(metadata[token].get("ownership"), 0.0))
        for token, actual in actual_ownership.items()
        if token in metadata and _safe_float(metadata[token].get("ownership"), 0.0) > 0
    ]
    source_ownership_errors = [
        abs(actual_ownership[token] - source_ownership[token])
        for token in actual_ownership
        if token in source_ownership
    ]
    ownership_profile = _finalize_ownership_profile(
        ownership_accumulator,
        signature_counts,
        source_vs_computed_mae=(
            statistics.mean(source_ownership_errors) if source_ownership_errors else None
        ),
    )
    construction_count = sum(stack_counts.values())
    stack_rates = {
        str(value): stack_counts[str(value)] / max(1, construction_count)
        for value in range(4)
    }
    bringback_rates = {
        key: bringback_hits[key] / max(1, bringback_trials[key])
        for key in ("0", "1", "2_plus")
    }
    flex_total = sum(flex_counts.values())
    flex_rates = {
        pos: flex_counts[pos] / max(1, flex_total)
        for pos in ("RB", "WR", "TE")
    }
    salary_p10 = _percentile_value(salaries, 0.10)
    sport = str(preflight.get("sport") or "UNKNOWN").upper()
    if replace_field_id:
        conn.execute("DELETE FROM contest_field_summaries WHERE field_id=?", (replace_field_id,))
    conn.execute(
        """
        INSERT INTO contest_field_summaries (
            field_id, import_id, contest_key, sport, contest_name, field_preset,
            entry_count, field_size, roster_size, metadata_coverage_pct,
            unique_lineups, duplicated_entries, duplicate_entry_pct,
            max_duplicate_count, top_one_entries, top_one_duplicate_pct,
            avg_salary, salary_p10, avg_unused_salary, stack_rates_json,
            bringback_rates_json, flex_rates_json, ownership_json,
            ownership_profile_json, ownership_mae, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            replace_field_id or str(uuid.uuid4()), import_id, str(preflight.get("contest_key") or os.path.basename(path)),
            sport, str(preflight.get("contest_name") or os.path.basename(path)),
            str(preflight.get("field_preset") or "Unclassified"), entry_count, field_size,
            roster_size, mapped_slots / max(1, total_slots) * 100.0,
            len(signature_counts), duplicated_entries,
            duplicated_entries / max(1, entry_count) * 100.0,
            max(signature_counts.values() or [0]), len(top_signatures),
            top_duplicated / max(1, len(top_signatures)) * 100.0 if top_signatures else None,
            statistics.mean(salaries) if salaries else None, salary_p10,
            50000.0 - statistics.mean(salaries) if salaries and sport == "NFL" else None,
            json.dumps(stack_rates), json.dumps(bringback_rates), json.dumps(flex_rates),
            json.dumps(actual_ownership), json.dumps(ownership_profile),
            statistics.mean(ownership_errors) if ownership_errors else None,
            _now_iso(),
        ),
    )
    return {
        "contests": 1,
        "entries": entry_count,
        "presets": {str(preflight.get("field_preset") or "Unclassified"): 1},
        "sport": sport,
        "source_rows": _safe_int(preflight.get("entry_rows"), entry_count),
    }


def import_historical_result_csvs(
    paths: List[str],
    *,
    db_path: Optional[str] = None,
    archive_files: bool = True,
    progress_callback: Optional[Any] = None,
    cancel_callback: Optional[Any] = None,
) -> Dict[str, Any]:
    """Import old DK-ish result/entry CSV files into the learning DB.

    The parser is intentionally forgiving. It stores raw rows plus normalized fields
    so better sport-specific learning can be layered on later without re-importing.
    """
    import shutil

    files = [p for p in (paths or []) if p and os.path.isfile(p) and p.lower().endswith(".csv")]
    folders = history_folder_structure() if archive_files else {}
    conn = _connect(db_path)
    total_rows = 0
    total_files = 0
    sports: Dict[str, int] = {}
    errors: List[str] = []
    duplicates_skipped = 0
    imported_import_ids: List[str] = []
    field_contests_analyzed = 0
    field_entries_analyzed = 0
    field_presets: Counter[str] = Counter()
    field_only_files = 0
    cancelled = False
    try:
        init_historical_import_tables(conn)
        for path in files:
            if cancel_callback and cancel_callback():
                cancelled = True
                break
            import_id = str(uuid.uuid4())
            rows_for_file = 0
            sport_for_file = "UNKNOWN"
            try:
                file_hash = _file_sha256(path)
                if conn.execute(
                    "SELECT 1 FROM historical_imports WHERE file_sha256=? AND notes IN ('ok', 'field_only') LIMIT 1",
                    (file_hash,),
                ).fetchone():
                    duplicates_skipped += 1
                    continue
                preflight = _preflight_complete_field_csv(
                    path,
                    progress_callback=progress_callback,
                    cancel_callback=cancel_callback,
                )
                if preflight.get("complete"):
                    with conn:
                        conn.execute(
                            "INSERT INTO historical_imports (import_id, created_at, source_path, file_name, sport, rows_imported, file_sha256, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                import_id, _now_iso(), path, os.path.basename(path),
                                str(preflight.get("sport") or "UNKNOWN"),
                                _safe_int(preflight.get("entry_rows"), 0), file_hash, "started",
                            ),
                        )
                        field_analysis = _stream_complete_field_summary(
                            conn, path, import_id, preflight,
                            progress_callback=progress_callback,
                            cancel_callback=cancel_callback,
                        )
                        conn.execute(
                            "UPDATE historical_imports SET sport=?, rows_imported=?, notes='field_only' WHERE import_id=?",
                            (
                                str(field_analysis.get("sport") or "UNKNOWN"),
                                _safe_int(field_analysis.get("source_rows"), 0),
                                import_id,
                            ),
                        )
                    total_files += 1
                    field_only_files += 1
                    source_rows = _safe_int(field_analysis.get("source_rows"), 0)
                    total_rows += source_rows
                    sport_name = str(field_analysis.get("sport") or "UNKNOWN")
                    sports[sport_name] = sports.get(sport_name, 0) + source_rows
                    field_contests_analyzed += int(field_analysis.get("contests", 0) or 0)
                    field_entries_analyzed += int(field_analysis.get("entries", 0) or 0)
                    field_presets.update(field_analysis.get("presets") or {})
                    if archive_files:
                        dest = os.path.join(
                            folders["imported_results"],
                            f"{_dt.datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.path.basename(path)}",
                        )
                        try:
                            shutil.copy2(path, dest)
                        except Exception:
                            pass
                    continue
                with open(path, "r", newline="", encoding="utf-8-sig") as f:
                    sample = f.read(4096)
                    f.seek(0)
                    try:
                        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
                    except Exception:
                        dialect = csv.excel
                    reader = csv.DictReader(f, dialect=dialect)
                    if not reader.fieldnames:
                        raise ValueError("CSV has no header row")
                    with conn:
                        conn.execute(
                            "INSERT INTO historical_imports (import_id, created_at, source_path, file_name, sport, rows_imported, file_sha256, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (import_id, _now_iso(), path, os.path.basename(path), "UNKNOWN", 0, file_hash, "started"),
                        )
                        for idx, raw in enumerate(reader, start=1):
                            if idx % 1000 == 0:
                                if cancel_callback and cancel_callback():
                                    raise _ImportCancelled()
                                if progress_callback:
                                    progress_callback(
                                        idx,
                                        _safe_int(preflight.get("entry_rows"), 0),
                                        f"Importing result rows: {idx:,}",
                                    )
                            row = {_canon_result_col(k): v for k, v in (raw or {}).items()}
                            tokens = _extract_lineup_tokens(row)
                            # DK standings files also contain player-result rows. Keep entry/result
                            # records and ignore those separate player rows here.
                            player_only = bool(str(row.get("player", "") or "").strip()) and not tokens and not str(row.get("rank", "") or "").strip()
                            useful = bool(tokens) or any(
                                str(_first_present(row, [k], "")).strip()
                                for k in ["winnings", "rank", "entry_name"]
                            )
                            if player_only:
                                useful = False
                            if not useful:
                                continue
                            sport = _infer_sport_from_file_or_row(path, row)
                            if sport_for_file == "UNKNOWN" and sport != "UNKNOWN":
                                sport_for_file = sport
                            entry_fee = _money_to_float(row.get("entry_fee")) if "entry_fee" in row and str(row.get("entry_fee", "")).strip() else None
                            winnings = _money_to_float(row.get("winnings")) if "winnings" in row and str(row.get("winnings", "")).strip() else None
                            roi = ((winnings or 0.0) - (entry_fee or 0.0)) if entry_fee is not None or winnings is not None else None
                            actual_points = _safe_float(row.get("actual_points")) if "actual_points" in row and str(row.get("actual_points", "")).strip() else None
                            result_id = str(uuid.uuid4())
                            conn.execute(
                                """
                                INSERT INTO historical_results (
                                    result_id, import_id, source_file, row_index, sport, slate_date,
                                    contest_name, entry_name, entry_fee, winnings, roi,
                                    actual_points, rank_text, lineup_tokens_json, raw_json,
                                    field_size, places_paid
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    result_id,
                                    import_id,
                                    os.path.basename(path),
                                    idx,
                                    sport,
                                    str(_first_present(row, ["slate_date"], "") or ""),
                                    str(_first_present(row, ["contest_name"], "") or ""),
                                    str(_first_present(row, ["entry_name"], "") or ""),
                                    entry_fee,
                                    winnings,
                                    roi,
                                    actual_points,
                                    str(_first_present(row, ["rank"], "") or ""),
                                    json.dumps(tokens),
                                    json.dumps(row, default=str),
                                    _safe_int(row.get("field_size"), 0) or None,
                                    _safe_int(row.get("places_paid"), 0) or None,
                                ),
                            )
                            for slot_idx, tok in enumerate(tokens, start=1):
                                conn.execute(
                                    "INSERT INTO historical_result_players (hist_player_id, result_id, token, slot_index) VALUES (?, ?, ?, ?)",
                                    (str(uuid.uuid4()), result_id, tok, slot_idx),
                                )
                            rows_for_file += 1
                            sports[sport] = sports.get(sport, 0) + 1
                        conn.execute(
                            "UPDATE historical_imports SET sport=?, rows_imported=?, notes=? WHERE import_id=?",
                            (sport_for_file, rows_for_file, "ok", import_id),
                        )
                        _finalize_import_outcomes(conn, import_id)
                        field_analysis = _analyze_imported_fields(conn, import_id)
                if rows_for_file > 0:
                    imported_import_ids.append(import_id)
                    total_files += 1
                    total_rows += rows_for_file
                    field_contests_analyzed += int(field_analysis.get("contests", 0) or 0)
                    field_entries_analyzed += int(field_analysis.get("entries", 0) or 0)
                    field_presets.update(field_analysis.get("presets") or {})
                    if archive_files:
                        dest = os.path.join(folders["imported_results"], f"{_dt.datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.path.basename(path)}")
                        try:
                            shutil.copy2(path, dest)
                        except Exception:
                            pass
            except _ImportCancelled:
                cancelled = True
                break
            except Exception as e:
                errors.append(f"{os.path.basename(path)}: {e}")
        matching = (
            match_historical_results(conn, import_ids=imported_import_ids)
            if imported_import_ids
            else {"matched": 0, "unmatched": 0}
        )
        return {
            "files_imported": total_files,
            "rows_imported": total_rows,
            "sports": sports,
            "errors": errors,
            "duplicates_skipped": duplicates_skipped,
            "matched_rows": matching["matched"],
            "unmatched_rows": matching["unmatched"],
            "field_contests_analyzed": field_contests_analyzed,
            "field_entries_analyzed": field_entries_analyzed,
            "field_presets": dict(field_presets),
            "field_only_files": field_only_files,
            "cancelled": cancelled,
            "db_path": db_path or history_db_path(),
            "folders": folders,
        }
    finally:
        conn.close()


def attach_salary_csv_to_latest_field(
    salary_path: str,
    *,
    db_path: Optional[str] = None,
    progress_callback: Optional[Any] = None,
    cancel_callback: Optional[Any] = None,
    minimum_match_pct: float = 70.0,
) -> Dict[str, Any]:
    """Rebuild the latest field summary with a matching DraftKings salary file."""
    from data_io import read_players_csv

    if not salary_path or not os.path.isfile(salary_path):
        raise ValueError("Select an existing DraftKings salary CSV.")
    conn = _connect(db_path)
    try:
        init_historical_import_tables(conn)
        target = conn.execute(
            """
            SELECT cfs.field_id, cfs.import_id, hi.source_path, cfs.contest_name,
                   cfs.field_preset
            FROM contest_field_summaries cfs
            JOIN historical_imports hi ON hi.import_id=cfs.import_id
            WHERE cfs.sport='NFL' AND cfs.roster_size=9
            ORDER BY cfs.created_at DESC
            LIMIT 1
            """
        ).fetchone()
        if not target:
            return {
                "attached": False,
                "cancelled": False,
                "message": "Import a complete NFL Classic standings file before attaching salaries.",
            }
        field_id, import_id, standings_path, contest_name, preset = target
        if not standings_path or not os.path.isfile(standings_path):
            return {
                "attached": False,
                "cancelled": False,
                "message": "The original standings CSV is no longer available at its imported location.",
            }
        if progress_callback:
            progress_callback(0, 0, "Reading matching DraftKings salaries")
        players = read_players_csv(salary_path)
        metadata: Dict[str, Dict[str, Any]] = {}
        for player in players:
            meta = {
                "name": str(player.get("Name") or ""),
                "team": str(player.get("Team") or "").strip().upper(),
                "opponent": str(player.get("Opponent") or "").strip().upper(),
                "position": str(player.get("Position") or "").strip().upper().replace("D/ST", "DST").split("/")[0],
                "salary": _safe_float(player.get("FlexSalary"), 0.0),
                "ownership": _safe_float(player.get("ProjOwnPct"), 0.0),
            }
            for value in (
                player.get("FlexID"), player.get("FlexNamePlusID"), player.get("Name"),
            ):
                key = _normalize_roster_token(value)
                if key:
                    metadata[key] = meta
        preflight = _preflight_complete_field_csv(
            standings_path,
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
        )
        source_players = set((preflight.get("source_ownership") or {}).keys())
        matched_players = sum(1 for key in source_players if key in metadata)
        match_pct = matched_players / max(1, len(source_players)) * 100.0
        if source_players and match_pct < minimum_match_pct:
            return {
                "attached": False,
                "cancelled": False,
                "contest_name": contest_name,
                "field_preset": preset,
                "matched_players": matched_players,
                "field_players": len(source_players),
                "match_pct": match_pct,
                "message": (
                    f"Salary file match was only {match_pct:.1f}% ({matched_players}/{len(source_players)} players). "
                    "The field was left unchanged because this appears to be a different slate."
                ),
            }
        if cancel_callback and cancel_callback():
            raise _ImportCancelled()
        with conn:
            summary = _stream_complete_field_summary(
                conn,
                standings_path,
                str(import_id),
                preflight,
                progress_callback=progress_callback,
                cancel_callback=cancel_callback,
                metadata_override=metadata,
                replace_field_id=str(field_id),
            )
        refreshed = conn.execute(
            """
            SELECT metadata_coverage_pct, avg_salary, salary_p10,
                   stack_rates_json, bringback_rates_json, flex_rates_json
            FROM contest_field_summaries WHERE field_id=?
            """,
            (field_id,),
        ).fetchone()
        return {
            "attached": True,
            "cancelled": False,
            "contest_name": contest_name,
            "field_preset": preset,
            "matched_players": matched_players,
            "field_players": len(source_players),
            "match_pct": match_pct,
            "metadata_coverage_pct": _safe_float(refreshed[0]),
            "avg_salary": refreshed[1],
            "salary_p10": refreshed[2],
            "stack_rates": json.loads(refreshed[3] or "{}"),
            "bringback_rates": json.loads(refreshed[4] or "{}"),
            "flex_rates": json.loads(refreshed[5] or "{}"),
            "entries": int(summary.get("entries", 0) or 0),
            "message": (
                f"Attached matching salaries to {contest_name}: {match_pct:.1f}% player match and "
                f"{_safe_float(refreshed[0]):.1f}% lineup-slot coverage."
            ),
        }
    except _ImportCancelled:
        return {"attached": False, "cancelled": True, "message": "Salary attachment cancelled."}
    finally:
        conn.close()


def load_nfl_field_calibration(
    preset: str,
    *,
    db_path: Optional[str] = None,
    min_entries: int = 1000,
    min_contests: int = 3,
) -> Dict[str, Any]:
    """Return a guarded blend of local complete-field patterns and a baseline preset."""
    from nfl_simulation import NFL_FIELD_PRESETS

    selected = preset if preset in NFL_FIELD_PRESETS else "150-Max"
    baseline = NFL_FIELD_PRESETS[selected]
    conn = _connect(db_path)
    try:
        init_historical_import_tables(conn)
        rows = conn.execute(
            """
            SELECT entry_count, field_size, metadata_coverage_pct, salary_p10,
                   stack_rates_json, bringback_rates_json, flex_rates_json,
                   duplicate_entry_pct, top_one_entries, top_one_duplicate_pct,
                   avg_salary, ownership_profile_json
            FROM contest_field_summaries
            WHERE sport='NFL' AND field_preset=? AND roster_size=9
            """,
            (selected,),
        ).fetchall()
    finally:
        conn.close()

    entries = sum(_safe_int(row[0], 0) for row in rows)
    contests = len(rows)
    coverage = (
        sum(_safe_float(row[2], 0.0) * _safe_int(row[0], 0) for row in rows) / max(1, entries)
        if rows else 0.0
    )
    enabled = contests >= min_contests and entries >= min_entries and coverage >= 70.0
    result: Dict[str, Any] = {
        "enabled": enabled,
        "preset": selected,
        "contests": contests,
        "entries": entries,
        "metadata_coverage_pct": coverage,
        "required_contests": min_contests,
        "required_entries": min_entries,
        "field_config": {},
    }
    def weighted_rates(index: int, keys: List[str]) -> Dict[str, float]:
        totals = {key: 0.0 for key in keys}
        denominator = 0
        for row in rows:
            try:
                values = json.loads(row[index] or "{}")
            except Exception:
                values = {}
            weight = _safe_int(row[0], 0)
            if not values or weight <= 0:
                continue
            denominator += weight
            for key in keys:
                totals[key] += _safe_float(values.get(key), 0.0) * weight
        return {key: totals[key] / max(1, denominator) for key in keys}

    def blended_distribution(key: str, learned_rates: Dict[str, float]) -> Dict[str, float]:
        baseline_rates = dict(baseline[key])
        blended = {
            rate_key: 0.65 * _safe_float(baseline_rates.get(rate_key), 0.0)
            + 0.35 * _safe_float(learned_rates.get(rate_key), 0.0)
            for rate_key in baseline_rates
        }
        total = sum(blended.values())
        return {rate_key: value / max(0.0001, total) for rate_key, value in blended.items()}

    learned_stack = weighted_rates(4, ["0", "1", "2", "3"])
    learned_bringback = weighted_rates(5, ["0", "1", "2_plus"])
    learned_flex = weighted_rates(6, ["RB", "WR", "TE"])
    bringback_rates = {
        key: max(
            0.02,
            min(
                0.98,
                0.65 * _safe_float(baseline["bringback_rates"].get(key), 0.0)
                + 0.35 * _safe_float(learned_bringback.get(key), 0.0),
            ),
        )
        for key in ("0", "1", "2_plus")
    }
    salary_p10_values = [
        (_safe_float(row[3]), _safe_int(row[0], 0))
        for row in rows if row[3] is not None and _safe_float(row[3]) > 0
    ]
    learned_salary_pct = (
        sum(value * weight for value, weight in salary_p10_values)
        / max(1, sum(weight for _, weight in salary_p10_values))
        / 50000.0
        if salary_p10_values else _safe_float(baseline["min_salary_pct"], 0.94)
    )
    baseline_salary_pct = _safe_float(baseline["min_salary_pct"], 0.94)
    salary_pct = 0.65 * baseline_salary_pct + 0.35 * learned_salary_pct
    salary_pct = max(baseline_salary_pct - 0.025, min(baseline_salary_pct + 0.025, salary_pct))
    field_sizes = [
        (_safe_int(row[1], 0), _safe_int(row[0], 0))
        for row in rows if _safe_int(row[1], 0) > 0
    ]
    field_size = int(round(
        sum(value * weight for value, weight in field_sizes)
        / max(1, sum(weight for _, weight in field_sizes))
    )) if field_sizes else int(baseline["field_size"])

    def weighted_scalar(value_index: int, weight_index: int = 0) -> Optional[float]:
        pairs = [
            (_safe_float(row[value_index]), _safe_int(row[weight_index], 0))
            for row in rows if row[value_index] is not None and _safe_int(row[weight_index], 0) > 0
        ]
        return (
            sum(value * weight for value, weight in pairs) / sum(weight for _, weight in pairs)
            if pairs else None
        )

    profile_sections: Dict[str, Counter] = {"field": Counter(), "top_one": Counter()}
    profile_buckets: Dict[str, Counter] = {
        label: Counter() for _, label in _OWNERSHIP_BUCKETS
    }
    profile_coverage_weighted = 0.0
    profile_coverage_entries = 0
    for row in rows:
        try:
            profile = json.loads(row[11] or "{}")
        except Exception:
            profile = {}
        row_entries = _safe_int(row[0], 0)
        if profile and row_entries > 0:
            profile_coverage_weighted += _safe_float(profile.get("ownership_coverage_pct"), 0.0) * row_entries
            profile_coverage_entries += row_entries
        for section in ("field", "top_one"):
            values = dict(profile.get(section) or {})
            count = _safe_int(values.get("lineups"), 0)
            if count <= 0:
                continue
            profile_sections[section]["lineups"] += count
            for key, value in values.items():
                if key == "lineups" or not key.startswith("avg_"):
                    continue
                profile_sections[section][key] += _safe_float(value) * count
        for label, values in dict(profile.get("buckets") or {}).items():
            if label not in profile_buckets:
                continue
            values = dict(values or {})
            profile_buckets[label]["entries"] += _safe_int(values.get("entries"), 0)
            profile_buckets[label]["top_one_entries"] += _safe_int(values.get("top_one_entries"), 0)
            profile_buckets[label]["duplicated_entries"] += _safe_int(values.get("duplicated_entries"), 0)

    aggregate_profile: Dict[str, Any] = {
        "ownership_coverage_pct": profile_coverage_weighted / max(1, profile_coverage_entries),
        "field": {}, "top_one": {}, "buckets": {},
    }
    for section, totals in profile_sections.items():
        count = int(totals.get("lineups", 0) or 0)
        aggregate_profile[section] = {"lineups": count}
        for key, value in totals.items():
            if key != "lineups":
                aggregate_profile[section][key] = float(value) / max(1, count)
    for label, totals in profile_buckets.items():
        bucket_entries = int(totals.get("entries", 0) or 0)
        bucket_top = int(totals.get("top_one_entries", 0) or 0)
        bucket_dup = int(totals.get("duplicated_entries", 0) or 0)
        aggregate_profile["buckets"][label] = {
            "entries": bucket_entries,
            "field_pct": bucket_entries / max(1, entries) * 100.0,
            "top_one_entries": bucket_top,
            "top_one_rate": bucket_top / max(1, bucket_entries) * 100.0,
            "duplicated_entries": bucket_dup,
            "duplicate_pct": bucket_dup / max(1, bucket_entries) * 100.0,
        }

    result["reference"] = {
        "contests": contests,
        "entries": entries,
        "field_size": field_size,
        "duplicate_entry_pct": weighted_scalar(7),
        "top_one_duplicate_pct": weighted_scalar(9, 8),
        "avg_salary": weighted_scalar(10),
        "salary_p10": (
            sum(value * weight for value, weight in salary_p10_values)
            / max(1, sum(weight for _, weight in salary_p10_values))
            if salary_p10_values else None
        ),
        "stack_rates": learned_stack,
        "bringback_rates": learned_bringback,
        "flex_rates": learned_flex,
        "ownership_profile": aggregate_profile,
        "report_only": not enabled,
    }

    if not enabled:
        result["message"] = (
            f"Baseline {selected} model active; learned tuning needs {min_contests} complete fields, "
            f"{min_entries:,} entries, and 70% player metadata coverage "
            f"({contests}/{min_contests} fields, {entries:,}/{min_entries:,} entries, {coverage:.1f}% coverage)."
        )
        return result

    result["field_config"] = {
        "field_size": field_size,
        "min_salary_pct": salary_pct,
        "ownership_exponent": _safe_float(baseline["ownership_exponent"], 0.55),
        "stack_rates": blended_distribution("stack_rates", learned_stack),
        "bringback_rates": bringback_rates,
        "flex_rates": blended_distribution("flex_rates", learned_flex),
        "field_ownership_profile": dict(aggregate_profile.get("field") or {}),
        "winning_ownership_profile": dict(aggregate_profile.get("top_one") or {}),
    }
    result["message"] = (
        f"Learned {selected} field blend active from {contests} complete contests and "
        f"{entries:,} entries ({coverage:.1f}% metadata coverage)."
    )
    return result

# ---------------- Learning report ----------------


def _bucket_salary_unused(salary: float, cap: float = 50000.0) -> str:
    unused = max(0.0, float(cap or 50000.0) - float(salary or 0.0))
    if unused <= 200:
        return "$0-$200 unused"
    if unused <= 700:
        return "$201-$700 unused"
    if unused <= 1200:
        return "$701-$1,200 unused"
    if unused <= 2500:
        return "$1,201-$2,500 unused"
    return "$2,500+ unused"


def _confidence_label(n: int) -> str:
    if n >= 1000:
        return "High"
    if n >= 250:
        return "Medium"
    if n >= 50:
        return "Early"
    if n > 0:
        return "Very early"
    return "No data"


def _fmt_money(v: float) -> str:
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.2f}"


def _top_rows(rows: List[tuple], *, value_kind: str = "count", limit: int = 8) -> List[str]:
    out: List[str] = []
    for row in rows[:limit]:
        if value_kind == "count":
            label, count = row[0], int(row[1] or 0)
            out.append(f"• {label or 'Unknown'}: {count}")
        elif value_kind == "roi":
            label, count, avg_roi, total_roi = row[0], int(row[1] or 0), float(row[2] or 0.0), float(row[3] or 0.0)
            out.append(f"• {label or 'Unknown'}: {count} lineups | avg ROI {_fmt_money(avg_roi)} | total {_fmt_money(total_roi)}")
    return out


def _generate_legacy_learning_report(*, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Build a human-readable learning report from the local SQLite history.

    The report is intentionally conservative: when results/ROI are not imported yet,
    it reports what has been collected and labels strategy notes as low-confidence.
    """
    path = db_path or history_db_path()
    conn = _connect(path)
    try:
        init_historical_import_tables(conn)
        cur = conn.cursor()

        export_count = int(cur.execute("SELECT COUNT(*) FROM exports").fetchone()[0] or 0)
        exported_lineups = int(cur.execute("SELECT COUNT(*) FROM lineups").fetchone()[0] or 0)
        imported_files = int(cur.execute("SELECT COUNT(*) FROM historical_imports").fetchone()[0] or 0)
        imported_rows = int(cur.execute("SELECT COUNT(*) FROM historical_results").fetchone()[0] or 0)
        result_lineups = int(cur.execute("SELECT COUNT(*) FROM lineups WHERE roi IS NOT NULL OR actual_points IS NOT NULL").fetchone()[0] or 0)

        sport_rows = cur.execute(
            """
            SELECT sport, COUNT(*)
            FROM (
                SELECT sport FROM exports
                UNION ALL
                SELECT sport FROM historical_results
            )
            GROUP BY sport
            ORDER BY COUNT(*) DESC
            """
        ).fetchall()

        stack_rows = cur.execute(
            """
            SELECT stack_shape, COUNT(*)
            FROM lineups
            GROUP BY stack_shape
            ORDER BY COUNT(*) DESC
            LIMIT 8
            """
        ).fetchall()

        risk_rows = cur.execute(
            """
            SELECT COALESCE(dup_risk_label, 'Unknown'), COUNT(*)
            FROM lineups
            GROUP BY COALESCE(dup_risk_label, 'Unknown')
            ORDER BY COUNT(*) DESC
            """
        ).fetchall()

        salary_rows_raw = cur.execute("SELECT salary, e.salary_cap FROM lineups l JOIN exports e ON e.export_id=l.export_id").fetchall()
        salary_buckets: Dict[str, int] = {}
        for sal, cap in salary_rows_raw:
            b = _bucket_salary_unused(_safe_float(sal), _safe_float(cap, 50000.0))
            salary_buckets[b] = salary_buckets.get(b, 0) + 1
        salary_rows = sorted(salary_buckets.items(), key=lambda kv: kv[1], reverse=True)

        # Performance summaries only become meaningful once actual/ROI exists.
        stack_roi_rows = cur.execute(
            """
            SELECT stack_shape, COUNT(*), AVG(roi), SUM(roi)
            FROM lineups
            WHERE roi IS NOT NULL
            GROUP BY stack_shape
            HAVING COUNT(*) >= 3
            ORDER BY AVG(roi) DESC
            LIMIT 8
            """
        ).fetchall()

        hist_roi_rows = cur.execute(
            """
            SELECT sport, COUNT(*), AVG(roi), SUM(roi)
            FROM historical_results
            WHERE roi IS NOT NULL
            GROUP BY sport
            ORDER BY SUM(roi) DESC
            """
        ).fetchall()

        avg_projection = cur.execute("SELECT AVG(projection) FROM lineups WHERE projection > 0").fetchone()[0]
        avg_own = cur.execute("SELECT AVG(avg_ownership) FROM lineups WHERE avg_ownership > 0").fetchone()[0]
        avg_dup = cur.execute("SELECT AVG(dup_risk) FROM lineups WHERE dup_risk > 0").fetchone()[0]

        total_learning_rows = exported_lineups + imported_rows
        confidence = _confidence_label(total_learning_rows)
        outcome_confidence = _confidence_label(result_lineups + imported_rows)

        lines: List[str] = []
        lines.append("DFS Learning Report")
        lines.append("")
        lines.append("Database")
        lines.append(f"• Path: {path}")
        lines.append(f"• Exports recorded: {export_count}")
        lines.append(f"• Exported lineups recorded: {exported_lineups}")
        lines.append(f"• Historical files imported: {imported_files}")
        lines.append(f"• Historical result rows imported: {imported_rows}")
        lines.append(f"• Learning confidence: {confidence}")
        lines.append(f"• Outcome confidence: {outcome_confidence}")
        lines.append("")

        lines.append("Sports in database")
        if sport_rows:
            lines.extend(_top_rows(sport_rows, value_kind="count", limit=10))
        else:
            lines.append("• No sport data yet.")
        lines.append("")

        lines.append("Lineup construction currently being collected")
        if stack_rows:
            lines.append("Stack shapes:")
            lines.extend(_top_rows(stack_rows, value_kind="count", limit=8))
        else:
            lines.append("• No exported lineup construction data yet.")
        if salary_rows:
            lines.append("Salary usage:")
            lines.extend([f"• {label}: {count}" for label, count in salary_rows[:8]])
        if risk_rows:
            lines.append("Duplication risk buckets:")
            lines.extend(_top_rows(risk_rows, value_kind="count", limit=8))
        lines.append("")

        lines.append("Averages from exported lineups")
        lines.append(f"• Avg projected score: {float(avg_projection or 0.0):.2f}")
        lines.append(f"• Avg lineup ownership: {float(avg_own or 0.0):.1f}%")
        lines.append(f"• Avg duplication risk: {float(avg_dup or 0.0):.1f}/100")
        lines.append("")

        lines.append("Performance once results are available")
        if stack_roi_rows:
            lines.append("Best stack shapes by recorded ROI:")
            lines.extend(_top_rows(stack_roi_rows, value_kind="roi", limit=8))
        else:
            lines.append("• No exported lineups have matched actual results/ROI yet.")
        if hist_roi_rows:
            lines.append("Historical imported ROI by sport:")
            lines.extend(_top_rows(hist_roi_rows, value_kind="roi", limit=8))
        else:
            lines.append("• No historical result ROI rows imported yet.")
        lines.append("")

        lines.append("Strategic notes")
        if total_learning_rows < 50:
            lines.append("• The app is collecting data, but it is too early to tune strategy from it.")
            lines.append("• Keep exporting saved lineups; import NFL history if available to jump-start confidence.")
        elif result_lineups + imported_rows < 50:
            lines.append("• Construction data is building, but outcome data is thin.")
            lines.append("• Strategy can be audited now, but should not self-tune until more results are imported.")
        else:
            if stack_roi_rows:
                best_stack = stack_roi_rows[0][0] or "Unknown"
                lines.append(f"• Early results favor {best_stack} constructions among recorded outcomes.")
            if hist_roi_rows:
                best_sport = hist_roi_rows[0][0] or "Unknown"
                lines.append(f"• Imported historical results are strongest so far for {best_sport}.")
            lines.append("• Next step is connecting result rows back to exact exported lineups/player IDs for sharper self-tuning.")

        return {
            "text": "\n".join(lines),
            "db_path": path,
            "export_count": export_count,
            "exported_lineups": exported_lineups,
            "historical_rows": imported_rows,
            "confidence": confidence,
            "outcome_confidence": outcome_confidence,
        }
    finally:
        conn.close()
