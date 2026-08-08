from __future__ import annotations

import csv
import os
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

APP_ANALYZER_VERSION = "1.0"

_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}


def _norm_name(x: Any) -> str:
    s = str(x or "").strip().lower()
    s = re.sub(r"[^a-z0-9\s.'-]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    parts = s.split()
    if parts and parts[-1] in _SUFFIXES:
        parts = parts[:-1]
    return " ".join(parts)


def _to_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        s = str(x).replace("$", "").replace(",", "").replace("%", "").strip()
        if not s:
            return default
        return float(s)
    except Exception:
        return default


def _to_int(x: Any, default: int = 0) -> int:
    try:
        return int(float(str(x or "").replace(",", "").strip()))
    except Exception:
        return default


def _canon_key(k: Any) -> str:
    s = str(k or "").replace("\ufeff", "").strip().lower()
    s = re.sub(r"\s+", " ", s.replace("_", " "))
    return s


def _parse_game_info(game_info: Any) -> Tuple[str, str]:
    s = str(game_info or "").strip().upper()
    m = re.search(r"\b([A-Z]{2,3})\s*@\s*([A-Z]{2,3})\b", s)
    if m:
        return m.group(1), m.group(2)
    return "", ""


def load_salary_file(path: str) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    by_id: Dict[str, Dict[str, Any]] = {}
    by_name: Dict[str, Dict[str, Any]] = {}
    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            row = {_canon_key(k): v for k, v in (raw or {}).items()}
            pid = str(row.get("id") or "").strip()
            name = str(row.get("name") or "").strip()
            if not name:
                nid = str(row.get("name + id") or "").strip()
                name = re.sub(r"\s*\([^)]*\)\s*$", "", nid).strip()
            if not pid or not name:
                continue
            team = str(row.get("teamabbrev") or row.get("team") or "").strip().upper()
            pos = str(row.get("position") or "").strip().upper()
            roster_pos = str(row.get("roster position") or pos).strip().upper()
            game_info = str(row.get("game info") or "").strip()
            away, home = _parse_game_info(game_info)
            rec = {
                "id": pid,
                "name": name,
                "norm_name": _norm_name(name),
                "team": team,
                "position": pos,
                "roster_position": roster_pos,
                "salary": _to_float(row.get("salary"), 0.0),
                "projection": _to_float(row.get("avgpointspergame"), 0.0),
                "game_info": game_info,
                "away": away,
                "home": home,
            }
            by_id[pid] = rec
            by_name.setdefault(rec["norm_name"], rec)
    return by_id, by_name


def load_exported_lineups(path: str, salary_by_id: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        rows = list(reader)
    if not rows:
        return out
    headers = [str(h or "").strip() for h in rows[0]]
    for idx, row in enumerate(rows[1:], start=1):
        ids = [str(x or "").strip() for x in row if str(x or "").strip()]
        players = [salary_by_id.get(pid, {"id": pid, "name": f"Unknown {pid}", "team": "", "position": "", "salary": 0.0, "projection": 0.0}) for pid in ids]
        out.append({"index": idx, "headers": headers, "ids": ids, "players": players})
    return out


def load_contest_standings(path: str) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    entries: List[Dict[str, Any]] = []
    player_results: Dict[str, Dict[str, Any]] = {}
    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            row = dict(raw or {})
            rank = row.get("Rank")
            pts = row.get("Points")
            lineup = row.get("Lineup") or ""
            if str(rank or "").strip() or str(pts or "").strip() or lineup.strip():
                entries.append({
                    "rank": _to_int(rank, 0),
                    "entry_id": str(row.get("EntryId") or "").strip(),
                    "entry_name": str(row.get("EntryName") or "").strip(),
                    "points": _to_float(pts, 0.0),
                    "lineup": lineup,
                })
            pname = str(row.get("Player") or "").strip()
            if pname:
                key = _norm_name(pname)
                player_results[key] = {
                    "name": pname,
                    "position": str(row.get("Roster Position") or "").strip(),
                    "drafted_pct": _to_float(row.get("%Drafted"), 0.0),
                    "fpts": _to_float(row.get("FPTS"), 0.0),
                }
    return entries, player_results


def _augment_lineup(lu: Dict[str, Any], player_results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    players = list(lu.get("players") or [])
    score = 0.0
    drafted = []
    missing = []
    teams = Counter()
    hitters_by_team = Counter()
    top_names = []
    salary = 0.0
    projection = 0.0
    for p in players:
        key = _norm_name(p.get("name"))
        res = player_results.get(key)
        if res:
            score += float(res.get("fpts", 0.0) or 0.0)
            drafted.append(float(res.get("drafted_pct", 0.0) or 0.0))
        else:
            missing.append(str(p.get("name") or p.get("id") or "Unknown"))
        team = str(p.get("team") or "").upper()
        if team:
            teams[team] += 1
        pos = str(p.get("roster_position") or p.get("position") or "").upper()
        if team and pos not in {"P", "SP", "RP"}:
            hitters_by_team[team] += 1
        salary += float(p.get("salary", 0.0) or 0.0)
        projection += float(p.get("projection", 0.0) or 0.0)
        top_names.append(str(p.get("name") or ""))
    hitter_counts = sorted(hitters_by_team.values(), reverse=True)
    shape = "-".join(map(str, hitter_counts[:3])) if hitter_counts else "n/a"
    primary = hitters_by_team.most_common(1)[0][0] if hitters_by_team else ""
    secondary = hitters_by_team.most_common(2)[1][0] if len(hitters_by_team) > 1 else ""
    avg_own = statistics.mean(drafted) if drafted else 0.0
    max_own = max(drafted) if drafted else 0.0
    return {
        **lu,
        "actual_score": score,
        "salary": salary,
        "salary_left": max(0.0, 50000.0 - salary),
        "projection": projection,
        "avg_ownership": avg_own,
        "max_ownership": max_own,
        "stack_shape": shape,
        "primary_stack": primary,
        "secondary_stack": secondary,
        "missing_actuals": missing,
        "names": top_names,
    }


def _lineup_players_from_standing(lineup: str, salary_by_name: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    # DK lineup text format: "1B Christian Walker 2B Matt McLain ... P Joe Ryan ..."
    positions = {"P", "C", "1B", "2B", "3B", "SS", "OF", "UTIL", "FLEX", "DST", "QB", "RB", "WR", "TE", "K", "CPT"}
    tokens = str(lineup or "").split()
    chunks: List[Tuple[str, str]] = []
    cur_pos = ""
    cur_name: List[str] = []
    for tok in tokens:
        if tok.upper() in positions:
            if cur_pos and cur_name:
                chunks.append((cur_pos, " ".join(cur_name)))
            cur_pos = tok.upper()
            cur_name = []
        else:
            cur_name.append(tok)
    if cur_pos and cur_name:
        chunks.append((cur_pos, " ".join(cur_name)))
    players = []
    for pos, name in chunks:
        rec = dict(salary_by_name.get(_norm_name(name), {}))
        if not rec:
            rec = {"id": "", "name": name, "team": "", "position": pos, "roster_position": pos, "salary": 0.0, "projection": 0.0}
        rec.setdefault("roster_position", pos)
        players.append(rec)
    return players


def _pct_rank(score: float, entries: List[Dict[str, Any]]) -> Tuple[int, float]:
    if not entries:
        return 0, 0.0
    better = sum(1 for e in entries if float(e.get("points", 0.0) or 0.0) > score)
    rank = better + 1
    percentile = 100.0 * (1.0 - (rank - 1) / max(1, len(entries)))
    return rank, percentile


def _bucket_salary_left(v: float) -> str:
    if v <= 100:
        return "$0-$100 left"
    if v <= 500:
        return "$101-$500 left"
    if v <= 1000:
        return "$501-$1k left"
    if v <= 2000:
        return "$1k-$2k left"
    return ">$2k left"


def _bucket_own(v: float) -> str:
    if v < 8:
        return "<8% avg own"
    if v < 12:
        return "8-12% avg own"
    if v < 16:
        return "12-16% avg own"
    if v < 20:
        return "16-20% avg own"
    return "20%+ avg own"


def _avg(vals: List[float]) -> float:
    return statistics.mean(vals) if vals else 0.0


def analyze_slate(salary_csv: str, exported_lineups_csv: str, standings_csv: str, *, final_lineups_csv: Optional[str] = None) -> Dict[str, Any]:
    salary_by_id, salary_by_name = load_salary_file(salary_csv)
    entries, player_results = load_contest_standings(standings_csv)
    exported = [_augment_lineup(lu, player_results) for lu in load_exported_lineups(exported_lineups_csv, salary_by_id)]
    exported.sort(key=lambda x: float(x.get("actual_score", 0.0) or 0.0), reverse=True)

    # Field top slices reconstructed from standings text.
    entries_sorted = sorted(entries, key=lambda x: float(x.get("points", 0.0) or 0.0), reverse=True)
    field_top_n = max(1, int(len(entries_sorted) * 0.01)) if entries_sorted else 0
    field_top = []
    for i, e in enumerate(entries_sorted[: max(25, field_top_n)]):
        lu = {"index": i + 1, "ids": [], "players": _lineup_players_from_standing(e.get("lineup", ""), salary_by_name)}
        aug = _augment_lineup(lu, player_results)
        aug["field_rank"] = e.get("rank", i + 1)
        aug["field_points"] = e.get("points", 0.0)
        aug["entry_name"] = e.get("entry_name", "")
        field_top.append(aug)

    winner = entries_sorted[0] if entries_sorted else {}
    best_export = exported[0] if exported else {}
    rank, percentile = _pct_rank(float(best_export.get("actual_score", 0.0) or 0.0), entries_sorted)

    def summarize_group(lineups: List[Dict[str, Any]]) -> Dict[str, Any]:
        stack = Counter(str(x.get("stack_shape") or "n/a") for x in lineups)
        salary = defaultdict(list)
        own = defaultdict(list)
        team = Counter(str(x.get("primary_stack") or "") for x in lineups if x.get("primary_stack"))
        for x in lineups:
            salary[_bucket_salary_left(float(x.get("salary_left", 0.0) or 0.0))].append(float(x.get("actual_score", x.get("field_points", 0.0)) or 0.0))
            own[_bucket_own(float(x.get("avg_ownership", 0.0) or 0.0))].append(float(x.get("actual_score", x.get("field_points", 0.0)) or 0.0))
        return {
            "count": len(lineups),
            "avg_score": _avg([float(x.get("actual_score", x.get("field_points", 0.0)) or 0.0) for x in lineups]),
            "stacks": stack.most_common(10),
            "primary_teams": team.most_common(10),
            "salary_buckets": [(k, len(v), _avg(v)) for k, v in sorted(salary.items())],
            "ownership_buckets": [(k, len(v), _avg(v)) for k, v in sorted(own.items())],
        }

    report = {
        "version": APP_ANALYZER_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "salary_csv": salary_csv,
        "exported_lineups_csv": exported_lineups_csv,
        "standings_csv": standings_csv,
        "final_lineups_csv": final_lineups_csv or "",
        "salary_players": len(salary_by_id),
        "contest_entries": len(entries_sorted),
        "actual_players": len(player_results),
        "winner_points": float(winner.get("points", 0.0) or 0.0),
        "winner_entry": winner.get("entry_name", ""),
        "exported_count": len(exported),
        "best_export": best_export,
        "best_export_field_rank_est": rank,
        "best_export_percentile": percentile,
        "export_summary": summarize_group(exported),
        "field_top_summary": summarize_group(field_top),
        "top_exported": exported[:10],
        "field_top": field_top[:10],
    }
    return report


def _fmt_money(v: Any) -> str:
    return f"${float(v or 0):,.0f}"


def render_report(r: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("DFS Slate Post-Mortem")
    lines.append("=" * 52)
    lines.append(f"Generated: {r.get('created_at','')}")
    lines.append(f"Analyzer version: {r.get('version','')}")
    lines.append("")
    lines.append("Files")
    lines.append(f"- Salary file: {os.path.basename(str(r.get('salary_csv','')))}")
    lines.append(f"- Export file: {os.path.basename(str(r.get('exported_lineups_csv','')))}")
    lines.append(f"- Standings file: {os.path.basename(str(r.get('standings_csv','')))}")
    lines.append("")
    lines.append("Important late-swap note")
    lines.append("- This analyzes the exported lineup file supplied to the analyzer. If you changed lineups manually before lock, treat this as an app-export audit, not a final-submission audit.")
    lines.append("- For exact post-lock results, run the analyzer with the actual final DK entry export when available.")
    lines.append("")
    lines.append("Contest / Data")
    lines.append(f"- Contest entries parsed: {int(r.get('contest_entries',0)):,}")
    lines.append(f"- Player result rows parsed: {int(r.get('actual_players',0)):,}")
    lines.append(f"- Salary players parsed: {int(r.get('salary_players',0)):,}")
    lines.append(f"- Exported lineups analyzed: {int(r.get('exported_count',0)):,}")
    lines.append(f"- Winning score: {float(r.get('winner_points',0.0)):.2f} ({r.get('winner_entry','')})")
    lines.append("")
    be = r.get("best_export") or {}
    if be:
        lines.append("Best Exported Lineup")
        lines.append(f"- Actual score: {float(be.get('actual_score',0.0)):.2f}")
        lines.append(f"- Estimated field rank by score: {int(r.get('best_export_field_rank_est',0)):,} / {int(r.get('contest_entries',0)):,}")
        lines.append(f"- Estimated percentile: {float(r.get('best_export_percentile',0.0)):.1f}")
        lines.append(f"- Salary used / left: {_fmt_money(be.get('salary'))} / {_fmt_money(be.get('salary_left'))}")
        lines.append(f"- Stack shape: {be.get('stack_shape','')} ({be.get('primary_stack','')}/{be.get('secondary_stack','')})")
        lines.append(f"- Avg / max ownership: {float(be.get('avg_ownership',0.0)):.1f}% / {float(be.get('max_ownership',0.0)):.1f}%")
        lines.append(f"- Players: {', '.join(be.get('names', [])[:12])}")
        if be.get("missing_actuals"):
            lines.append(f"- Missing actuals: {', '.join(be.get('missing_actuals', [])[:8])}")
        lines.append("")
    ex = r.get("export_summary") or {}
    ft = r.get("field_top_summary") or {}
    lines.append("Exported Lineup Summary")
    lines.append(f"- Average exported score: {float(ex.get('avg_score',0.0)):.2f}")
    lines.append("- Exported stack shapes: " + "; ".join([f"{k}: {v}" for k, v in (ex.get('stacks') or [])[:6]]))
    lines.append("- Exported primary stacks: " + "; ".join([f"{k}: {v}" for k, v in (ex.get('primary_teams') or [])[:6]]))
    lines.append("")
    lines.append("Field Top-Lineup Summary")
    lines.append(f"- Top field sample analyzed: {int(ft.get('count',0)):,}")
    lines.append(f"- Average top-field score: {float(ft.get('avg_score',0.0)):.2f}")
    lines.append("- Top-field stack shapes: " + "; ".join([f"{k}: {v}" for k, v in (ft.get('stacks') or [])[:6]]))
    lines.append("- Top-field primary stacks: " + "; ".join([f"{k}: {v}" for k, v in (ft.get('primary_teams') or [])[:6]]))
    lines.append("")
    lines.append("Salary Buckets — Exported")
    for k, n, avg in ex.get("salary_buckets") or []:
        lines.append(f"- {k}: {n} lineups, avg score {avg:.2f}")
    lines.append("")
    lines.append("Ownership Buckets — Exported")
    for k, n, avg in ex.get("ownership_buckets") or []:
        lines.append(f"- {k}: {n} lineups, avg score {avg:.2f}")
    lines.append("")
    lines.append("Top 10 Exported Lineups")
    for i, lu in enumerate(r.get("top_exported") or [], start=1):
        lines.append(f"{i}. Score {float(lu.get('actual_score',0.0)):.2f} | {lu.get('stack_shape','')} | {lu.get('primary_stack','')}/{lu.get('secondary_stack','')} | left {_fmt_money(lu.get('salary_left'))} | avg own {float(lu.get('avg_ownership',0.0)):.1f}%")
    lines.append("")
    lines.append("Initial Teaching Notes")
    lines.append("- Use this report to compare what your exported builds emphasized versus what the top field lineups emphasized.")
    lines.append("- Because you manually adjusted lineups near lock, the next improvement is storing/importing a final submitted entries CSV so the analyzer can separate optimizer intent from late-swap corrections.")
    lines.append("- If many missing actuals appear, it usually means DK name formatting differed; salary IDs still remain the cleaner matching key for your exported files.")
    return "\n".join(lines)


def analyze_and_render(salary_csv: str, exported_lineups_csv: str, standings_csv: str, *, final_lineups_csv: Optional[str] = None) -> str:
    return render_report(analyze_slate(salary_csv, exported_lineups_csv, standings_csv, final_lineups_csv=final_lineups_csv))
