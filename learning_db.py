from __future__ import annotations

"""Passive local learning database for DFS exports.

This module intentionally has no UI. It records exported saved lineups and the
features the app already knows at export time. Later result-import/backtesting can
attach actual fantasy points/ROI to these same records.
"""

import datetime as _dt
import csv
import json
import os
import sqlite3
import uuid
from typing import Any, Dict, List, Optional


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
                        grade, grade_score, dup_risk, dup_risk_label, uniqueness, stack_shape,
                        primary_team, secondary_team, top_order_hitters, confirmed_hitters,
                        avg_ownership, max_ownership, warnings, explanation
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        lineup_id,
                        export_id,
                        idx + 1,
                        json.dumps(roster_ids),
                        _safe_float(feature.get("salary", fallback.get("salary"))),
                        _safe_float(feature.get("projection", fallback.get("projection"))),
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
                for slot, p in _lineup_players(kind, lineup, sport):
                    player_id = str(p.get("CptID") if slot == "CPT" else p.get("FlexID") or "").strip()
                    conn.execute(
                        """
                        INSERT INTO lineup_players (
                            lineup_player_id, lineup_id, slot, player_key, player_id, name,
                            team, opponent, position, salary, projection, ownership,
                            batting_order, confirmed_lineup, injury_status
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                            _safe_float(p.get("CptProjection") if slot == "CPT" else p.get("FlexProjection")),
                            _safe_float(p.get("ProjCptOwnPct") if slot == "CPT" else p.get("ProjOwnPct")),
                            _safe_int(p.get("BattingOrder"), 0),
                            1 if bool(p.get("ConfirmedLineup")) else 0,
                            str(p.get("InjuryStatus", "") or ""),
                        ),
                    )
        return {"export_id": export_id, "db_path": history_db_path(), "lineups_recorded": len(lineups or [])}
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
        "contest": "contest_name", "contest name": "contest_name", "name": "contest_name",
        "entry": "entry_name", "entry name": "entry_name", "lineup name": "entry_name",
        "entry fee": "entry_fee", "fee": "entry_fee", "buy in": "entry_fee",
        "winnings": "winnings", "winning": "winnings", "prize": "winnings", "prizes": "winnings", "payout": "winnings",
        "fpts": "actual_points", "fantasy points": "actual_points", "points": "actual_points", "score": "actual_points",
        "rank": "rank", "place": "rank", "finish": "rank", "position": "rank",
        "lineup": "lineup", "roster": "lineup", "players": "lineup", "draft group": "draft_group",
        "sport": "sport", "date": "slate_date", "contest date": "slate_date", "start date": "slate_date",
    }
    return aliases.get(s, s)


def _first_present(row: Dict[str, Any], keys: List[str], default: Any = "") -> Any:
    for k in keys:
        if k in row and str(row.get(k, "")).strip() != "":
            return row.get(k)
    return default


def _extract_lineup_tokens(row: Dict[str, Any]) -> List[str]:
    """Extract likely DK player IDs from a result/entry row.

    DK exports vary. We accept a Lineup/Roster field or position columns. Numeric
    IDs are preferred, but name-like tokens are stored when IDs are absent.
    """
    import re

    lineup_text = str(_first_present(row, ["lineup", "roster", "players"], "") or "")
    pos_cols = ["cpt", "captain", "flex", "qb", "rb", "rb1", "rb2", "wr", "wr1", "wr2", "wr3", "te", "dst", "p", "p1", "p2", "c", "1b", "2b", "3b", "ss", "of", "of1", "of2", "of3", "pg", "sg", "sf", "pf", "g", "f", "util"]
    parts: List[str] = []
    if lineup_text:
        nums = re.findall(r"\b\d{4,12}\b", lineup_text)
        if nums:
            parts.extend(nums)
        else:
            split = [x.strip() for x in re.split(r"[;,|/]", lineup_text) if x.strip()]
            parts.extend(split)
    for k, v in row.items():
        lk = str(k or "").strip().lower()
        if lk in pos_cols or any(lk.startswith(pc) for pc in ("flex", "of", "wr", "rb")):
            val = str(v or "").strip()
            if not val:
                continue
            nums = re.findall(r"\b\d{4,12}\b", val)
            parts.extend(nums or [val])
    out: List[str] = []
    seen = set()
    for p in parts:
        pp = str(p or "").strip()
        if pp and pp not in seen:
            seen.add(pp)
            out.append(pp)
    return out


def _infer_sport_from_file_or_row(path: str, row: Dict[str, Any]) -> str:
    val = str(_first_present(row, ["sport"], "") or "").upper()
    text = (val + " " + os.path.basename(path).upper())
    for s in ("NFL", "MLB", "NBA", "WNBA"):
        if s in text:
            return s
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
            FOREIGN KEY(import_id) REFERENCES historical_imports(import_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS historical_result_players (
            hist_player_id TEXT PRIMARY KEY,
            result_id TEXT NOT NULL,
            token TEXT,
            slot_index INTEGER,
            FOREIGN KEY(result_id) REFERENCES historical_results(result_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_hist_results_import ON historical_results(import_id);
        CREATE INDEX IF NOT EXISTS idx_hist_results_sport ON historical_results(sport);
        CREATE INDEX IF NOT EXISTS idx_hist_players_token ON historical_result_players(token);
        """
    )
    conn.commit()


def import_historical_result_csvs(paths: List[str], *, db_path: Optional[str] = None, archive_files: bool = True) -> Dict[str, Any]:
    """Import old DK-ish result/entry CSV files into the learning DB.

    The parser is intentionally forgiving. It stores raw rows plus normalized fields
    so better sport-specific learning can be layered on later without re-importing.
    """
    import shutil

    files = [p for p in (paths or []) if p and os.path.isfile(p) and p.lower().endswith(".csv")]
    folders = history_folder_structure()
    conn = _connect(db_path)
    total_rows = 0
    total_files = 0
    sports: Dict[str, int] = {}
    errors: List[str] = []
    try:
        init_historical_import_tables(conn)
        for path in files:
            import_id = str(uuid.uuid4())
            rows_for_file = 0
            sport_for_file = "UNKNOWN"
            try:
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
                            "INSERT INTO historical_imports (import_id, created_at, source_path, file_name, sport, rows_imported, notes) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (import_id, _now_iso(), path, os.path.basename(path), "UNKNOWN", 0, "started"),
                        )
                        for idx, raw in enumerate(reader, start=1):
                            row = {_canon_result_col(k): v for k, v in (raw or {}).items()}
                            tokens = _extract_lineup_tokens(row)
                            # Store rows that have either lineup tokens or useful result values.
                            useful = bool(tokens) or any(str(_first_present(row, [k], "")).strip() for k in ["actual_points", "winnings", "rank", "contest_name"])
                            if not useful:
                                continue
                            sport = _infer_sport_from_file_or_row(path, row)
                            if sport_for_file == "UNKNOWN" and sport != "UNKNOWN":
                                sport_for_file = sport
                            entry_fee = _money_to_float(_first_present(row, ["entry_fee"], 0.0))
                            winnings = _money_to_float(_first_present(row, ["winnings"], 0.0))
                            roi = winnings - entry_fee
                            result_id = str(uuid.uuid4())
                            conn.execute(
                                """
                                INSERT INTO historical_results (
                                    result_id, import_id, source_file, row_index, sport, slate_date,
                                    contest_name, entry_name, entry_fee, winnings, roi,
                                    actual_points, rank_text, lineup_tokens_json, raw_json
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                                    _safe_float(_first_present(row, ["actual_points"], 0.0), 0.0),
                                    str(_first_present(row, ["rank"], "") or ""),
                                    json.dumps(tokens),
                                    json.dumps(row, default=str),
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
                if rows_for_file > 0:
                    total_files += 1
                    total_rows += rows_for_file
                    if archive_files:
                        dest = os.path.join(folders["imported_results"], f"{_dt.datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.path.basename(path)}")
                        try:
                            shutil.copy2(path, dest)
                        except Exception:
                            pass
            except Exception as e:
                errors.append(f"{os.path.basename(path)}: {e}")
        return {
            "files_imported": total_files,
            "rows_imported": total_rows,
            "sports": sports,
            "errors": errors,
            "db_path": history_db_path(),
            "folders": folders,
        }
    finally:
        conn.close()

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


def generate_learning_report(*, db_path: Optional[str] = None) -> Dict[str, Any]:
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
