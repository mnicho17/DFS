# main_window.py
from __future__ import annotations

import csv
import json
import os
import traceback
import logging
import math
import time
import random
import threading
from typing import Any, Dict, List, Optional

from PyQt5 import QtCore, QtGui, QtWidgets


class SortKeyItem(QtWidgets.QTableWidgetItem):
    """QTableWidgetItem that sorts by a numeric key stored in Qt.UserRole when present."""

    def __lt__(self, other: "QtWidgets.QTableWidgetItem") -> bool:  # type: ignore[override]
        try:
            a = self.data(QtCore.Qt.UserRole)
            b = other.data(QtCore.Qt.UserRole)
            if a is not None and b is not None:
                return float(a) < float(b)
        except Exception:
            pass
        return super().__lt__(other)




class OwnershipSimWorker(QtCore.QObject):
    """Run a lineup-based ownership simulation in a background thread."""

    progress = QtCore.pyqtSignal(int, int, str)  # done, total, eta_str
    finished = QtCore.pyqtSignal(dict)  # {"total":{k:pct},"cpt":{k:pct},"flex":{k:pct}} (pct=0-100)
    error = QtCore.pyqtSignal(str)

    def __init__(self, players: List[Dict[str, Any]], *, mode: str, num_sims: int, salary_cap: float, template_sim: bool = False, sport: str = "NFL"):
        super().__init__()
        self.players = players
        self.mode = mode  # 'classic' or 'showdown'
        self.num_sims = max(1, int(num_sims))
        self.salary_cap = float(salary_cap or 50000.0)
        self.template_sim = bool(template_sim)
        self.sport = (sport or "NFL").strip().upper()
        self._cancel = False

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            result = self._simulate()
            if not self._cancel:
                self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))

    def cancel(self) -> None:
        self._cancel = True

    # ---- simulation helpers ----
    def _is_faded(self, p: Dict[str, Any]) -> bool:
        return bool(p.get("FadeFlex")) or bool(p.get("FadeCpt"))

    def _score(self, p: Dict[str, Any], *, use_cpt: bool = False) -> float:
        # A light 'field-like' utility score: projection + small value term.
        proj = float(p.get("CptProjection" if use_cpt else "FlexProjection", 0.0) or 0.0)
        sal = float(p.get("CptSalary" if use_cpt else "FlexSalary", 0.0) or 0.0)
        value = (proj / (sal / 1000.0)) if sal > 0 else 0.0
        # Projection dominates; value is a gentle nudge.
        return proj + 0.35 * value

    def _weighted_pick(self, pool: List[Dict[str, Any]], weights: List[float]) -> Dict[str, Any]:
        return random.choices(pool, weights=weights, k=1)[0]

    def _simulate_multisport_classic(self, eligible: List[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
        """Ownership sim for MLB/NBA/WNBA using the same sport slot eligibility as the optimizer."""
        counts_total: Dict[str, int] = {}
        slots = get_roster_slots_for_sport(self.sport)
        start = time.time()
        total = self.num_sims
        batch = max(25, min(250, total // 40 or 25))

        def bump(p: Dict[str, Any]) -> None:
            k = _pkey(p)
            counts_total[k] = counts_total.get(k, 0) + 1

        for i in range(total):
            if self._cancel:
                break
            used: set[str] = set()
            picked: List[Dict[str, Any]] = []
            cap_left = float(self.salary_cap)
            ok = True
            # Fill restrictive slots first: exact required positions before UTIL-like slots.
            fill_slots = list(slots)
            for slot in fill_slots:
                pool = [
                    p for p in eligible
                    if _pkey(p) not in used
                    and _eligible_for_slot(p, slot, self.sport)
                    and float(p.get("FlexSalary", 0) or 0) <= cap_left
                ]
                if not pool:
                    ok = False
                    break
                weights = [max(1e-9, math.exp(self._score(p, use_cpt=False) / 6.0)) for p in pool]
                pick = self._weighted_pick(pool, weights)
                used.add(_pkey(pick))
                picked.append(pick)
                cap_left -= float(pick.get("FlexSalary", 0) or 0)
            if ok and len(picked) == len(slots):
                for p in picked:
                    bump(p)

            if (i + 1) % batch == 0 or (i + 1) == total:
                elapsed = max(0.001, time.time() - start)
                done = i + 1
                rate = done / elapsed
                remaining = max(0, total - done)
                eta = remaining / rate if rate > 0 else 0.0
                self.progress.emit(done, total, f"ETA {int(eta)}s")

        denom = float(max(1, (i + 1) if 'i' in locals() else total))
        total_pct = {k: (v / denom) * 100.0 for k, v in counts_total.items()}
        return {"total": total_pct, "cpt": {}, "flex": total_pct}

    def _simulate(self) -> Dict[str, Dict[str, float]]:
        start = time.time()
        counts_total: Dict[str, int] = {}
        counts_cpt: Dict[str, int] = {}
        counts_flex: Dict[str, int] = {}

        eligible = [p for p in self.players if not self._is_faded(p)]
        if not eligible:
            return {}

        if self.mode != "showdown" and self.sport in ("MLB", "NBA", "WNBA"):
            return self._simulate_multisport_classic(eligible)

        # Precompute by position
        by_pos: Dict[str, List[Dict[str, Any]]] = {}
        for p in eligible:
            pos = (p.get("Position") or "").strip().upper()
            by_pos.setdefault(pos, []).append(p)

        def bump_total(name_key: str):
            counts_total[name_key] = counts_total.get(name_key, 0) + 1

        def bump_cpt(name_key: str):
            counts_cpt[name_key] = counts_cpt.get(name_key, 0) + 1

        def bump_flex(name_key: str):
            counts_flex[name_key] = counts_flex.get(name_key, 0) + 1

        total = self.num_sims
        batch = max(25, min(250, total // 40 or 25))

        classic_slots = [("QB", 1), ("RB", 2), ("WR", 3), ("TE", 1), ("DST", 1)]
        flex_positions = ("RB", "WR", "TE")

        for i in range(total):
            if self._cancel:
                break

            if self.mode == "showdown":
                # 1 CPT + 5 FLEX, no duplicates.
                # Option B: template-based "field construction" to better reflect real slate builds.
                # If template_sim is off, we fall back to simple weighted draws.

                def pick_cpt() -> Dict[str, Any]:
                    pool_cpt = eligible
                    w = [max(1e-9, math.exp(self._score(p, use_cpt=True) / 6.0)) for p in pool_cpt]
                    return self._weighted_pick(pool_cpt, w)

                def pick_weighted_unique(pool: List[Dict[str, Any]], used: set[str], *, use_cpt: bool = False) -> Optional[Dict[str, Any]]:
                    cand = [p for p in pool if _pkey(p) not in used]
                    if not cand:
                        return None
                    w = [max(1e-9, math.exp(self._score(p, use_cpt=use_cpt) / 6.0)) for p in cand]
                    return self._weighted_pick(cand, w)

                # Templates define the 5 FLEX slots by bucket counts.
                # Buckets:
                #   - QB
                #   - RB
                #   - WRTE (WR or TE)
                #   - DSTK (DST or K)
                templates = [
                    (0.50, {"QB": 1, "RB": 1, "WRTE": 2, "DSTK": 1}),  # "standard" 1QB build
                    (0.20, {"QB": 2, "RB": 1, "WRTE": 2, "DSTK": 0}),  # 2QB build
                    (0.15, {"QB": 1, "RB": 2, "WRTE": 1, "DSTK": 1}),  # run-heavy
                    (0.10, {"QB": 0, "RB": 1, "WRTE": 3, "DSTK": 1}),  # no-QB (rare)
                    (0.05, {"QB": 1, "RB": 0, "WRTE": 3, "DSTK": 1}),  # pass-heavy
                ]
                tmpl_weights = [t[0] for t in templates]

                pos_qb = by_pos.get("QB", []) or []
                pos_rb = by_pos.get("RB", []) or []
                pos_wrte = (by_pos.get("WR", []) or []) + (by_pos.get("TE", []) or [])
                pos_dstk = (by_pos.get("DST", []) or []) + (by_pos.get("K", []) or [])

                def build_flex_from_template(cpt: Dict[str, Any]) -> List[Dict[str, Any]]:
                    used = {_pkey(cpt)}
                    flex_picked: List[Dict[str, Any]] = []

                    if not self.template_sim:
                        # Simple (legacy) weighted draw across all eligible.
                        pool_flex = [p for p in eligible if _pkey(p) != _pkey(cpt)]
                        w_flex = [max(1e-9, math.exp(self._score(p, use_cpt=False) / 6.0)) for p in pool_flex]
                        tries = 0
                        while len(flex_picked) < 5 and tries < 150:
                            tries += 1
                            pick = self._weighted_pick(pool_flex, w_flex)
                            if _pkey(pick) in used:
                                continue
                            used.add(_pkey(pick))
                            flex_picked.append(pick)
                        return flex_picked

                    # Template-based filling
                    _, spec = random.choices(templates, weights=tmpl_weights, k=1)[0]

                    def add_from(pool: List[Dict[str, Any]], n: int) -> bool:
                        nonlocal used, flex_picked
                        for _ in range(n):
                            p = pick_weighted_unique(pool, used, use_cpt=False)
                            if p is None:
                                return False
                            used.add(_pkey(p))
                            flex_picked.append(p)
                        return True

                    ok = True
                    ok = ok and add_from(pos_qb, int(spec.get("QB", 0)))
                    ok = ok and add_from(pos_rb, int(spec.get("RB", 0)))
                    ok = ok and add_from(pos_wrte, int(spec.get("WRTE", 0)))
                    ok = ok and add_from(pos_dstk, int(spec.get("DSTK", 0)))

                    # If a bucket couldn't be filled (limited slate / heavy fades),
                    # fall back to filling remaining slots from the global pool.
                    if not ok or len(flex_picked) < 5:
                        pool_flex = [p for p in eligible if _pkey(p) not in used]
                        if pool_flex:
                            w_flex = [max(1e-9, math.exp(self._score(p, use_cpt=False) / 6.0)) for p in pool_flex]
                            tries = 0
                            while len(flex_picked) < 5 and tries < 200:
                                tries += 1
                                pick = self._weighted_pick(pool_flex, w_flex)
                                if _pkey(pick) in used:
                                    continue
                                used.add(_pkey(pick))
                                flex_picked.append(pick)

                    return flex_picked[:5]

                # Build + salary-cap validation with retries
                tries = 0
                cpt = pick_cpt()
                flex_picked = build_flex_from_template(cpt)

                while tries < 40:
                    tries += 1
                    if len(flex_picked) < 5:
                        cpt = pick_cpt()
                        flex_picked = build_flex_from_template(cpt)
                        continue

                    total_sal = float(cpt.get("CptSalary", 0) or 0) + sum(float(x.get("FlexSalary", 0) or 0) for x in flex_picked)
                    if total_sal <= self.salary_cap:
                        break

                    # resample
                    cpt = pick_cpt()
                    flex_picked = build_flex_from_template(cpt)

                bump_total(_pkey(cpt))
                bump_cpt(_pkey(cpt))
                for x in flex_picked[:5]:
                    bump_total(_pkey(x))
                    bump_flex(_pkey(x))
            else:
                chosen_keys = set()
                picked: List[Dict[str, Any]] = []

                def pick_from(pos: str) -> Optional[Dict[str, Any]]:
                    pool = [p for p in by_pos.get(pos, []) if _pkey(p) not in chosen_keys]
                    if not pool:
                        return None
                    w = [max(1e-9, math.exp(self._score(p, use_cpt=False) / 6.0)) for p in pool]
                    return self._weighted_pick(pool, w)

                # Initial fill
                for pos, n in classic_slots:
                    for _ in range(n):
                        p = pick_from(pos)
                        if p is None:
                            break
                        chosen_keys.add(_pkey(p))
                        picked.append(p)

                # FLEX
                flex_pool = [p for pp in flex_positions for p in by_pos.get(pp, [])]
                flex_pool = [p for p in flex_pool if _pkey(p) not in chosen_keys]
                if flex_pool:
                    w = [max(1e-9, math.exp(self._score(p, use_cpt=False) / 6.0)) for p in flex_pool]
                    flex_pick = self._weighted_pick(flex_pool, w)
                    chosen_keys.add(_pkey(flex_pick))
                    picked.append(flex_pick)

                # Salary cap check with retries (full resample)
                tries = 0
                while tries < 25:
                    if len(picked) == 9:
                        total_sal = sum(float(x.get("FlexSalary", 0) or 0) for x in picked)
                        if total_sal <= self.salary_cap:
                            break
                    tries += 1
                    chosen_keys = set()
                    picked = []
                    for pos, n in classic_slots:
                        for _ in range(n):
                            p = pick_from(pos)
                            if p is None:
                                break
                            chosen_keys.add(_pkey(p))
                            picked.append(p)
                    flex_pool = [p for pp in flex_positions for p in by_pos.get(pp, [])]
                    flex_pool = [p for p in flex_pool if _pkey(p) not in chosen_keys]
                    if flex_pool:
                        w = [max(1e-9, math.exp(self._score(p, use_cpt=False) / 6.0)) for p in flex_pool]
                        flex_pick = self._weighted_pick(flex_pool, w)
                        chosen_keys.add(_pkey(flex_pick))
                        picked.append(flex_pick)

                for x in picked[:9]:
                    bump_total(_pkey(x))

            # progress update
            if (i + 1) % batch == 0 or (i + 1) == total:
                elapsed = max(0.001, time.time() - start)
                done = i + 1
                rate = done / elapsed
                remaining = max(0, total - done)
                eta = remaining / rate if rate > 0 else 0.0
                eta_str = f"ETA {int(eta)}s"
                self.progress.emit(done, total, eta_str)

        denom = float(max(1, (i + 1) if not self._cancel else max(1, i)))
        total_pct = {k: (v / denom) * 100.0 for k, v in counts_total.items()}
        cpt_pct = {k: (v / denom) * 100.0 for k, v in counts_cpt.items()}
        flex_pct = {k: (v / denom) * 100.0 for k, v in counts_flex.items()}
        return {"total": total_pct, "cpt": cpt_pct, "flex": flex_pct}


class LineupBuildWorker(QtCore.QObject):
    """Build lineups in a background thread so the UI/status bar stays responsive."""

    progress = QtCore.pyqtSignal(int, int, str)  # done, total, status text
    finished = QtCore.pyqtSignal(dict)           # {kind, sport, lineups, requested}
    error = QtCore.pyqtSignal(str)

    def __init__(
        self,
        players: List[Dict[str, Any]],
        *,
        kind: str,
        num_lineups: int,
        salary_cap: float,
        sport: str = "NFL",
        own_mode: str = "Balanced",
        own_weight: float = 0.0,
        build_style: str = "Strategic",
        mlb_stack_pref: str = "Strategic",
        salary_strategy: str = "Near Cap",
    ):
        super().__init__()
        self.players = players
        self.kind = (kind or "classic").strip().lower()
        self.num_lineups = max(1, int(num_lineups or 1))
        self.salary_cap = float(salary_cap or 50000.0)
        self.sport = (sport or "NFL").strip().upper()
        self.own_mode = own_mode or "Balanced"
        self.own_weight = float(own_weight or 0.0)
        self.build_style = build_style or "Strategic"
        self.mlb_stack_pref = mlb_stack_pref or "Strategic"
        self.salary_strategy = salary_strategy or "Near Cap"
        self._cancel_event = threading.Event()

    def request_cancel(self) -> None:
        """Thread-safe cancellation request checked between lineup candidates."""
        self._cancel_event.set()

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            self.progress.emit(0, 0, "Optimizing…")
            if self.kind == "showdown":
                opt = ShowdownOptimizer(
                    self.players,
                    salary_cap=self.salary_cap,
                    own_mode=self.own_mode,
                    own_weight=self.own_weight,
                    build_style=self.build_style,
                )
                lineups = opt.build_lineups(
                    num_lineups=self.num_lineups,
                    progress_callback=lambda done, total, text: self.progress.emit(done, total, text),
                    cancel_callback=self._cancel_event.is_set,
                )
            else:
                opt = MultiSportClassicOptimizer(
                    self.players,
                    sport=self.sport,
                    salary_cap=self.salary_cap,
                    own_mode=self.own_mode,
                    own_weight=self.own_weight,
                    build_style=self.build_style,
                    mlb_stack_pref=self.mlb_stack_pref,
                    salary_strategy=self.salary_strategy,
                )
                lineups = opt.build_lineups(num_lineups=self.num_lineups)
            self.finished.emit({
                "kind": self.kind,
                "sport": self.sport,
                "lineups": lineups,
                "requested": self.num_lineups,
                "cancelled": self._cancel_event.is_set(),
            })
        except Exception:
            self.error.emit(traceback.format_exc())


from data_io import read_players_csv
from injury_api import enrich_players_with_injuries
from optimizers import ShowdownOptimizer, ClassicOptimizer, MultiSportClassicOptimizer, get_roster_slots_for_sport, lineup_slots_for_sport, _eligible_for_slot, lineup_grade_for_sport
from widgets import CopyRowTableWidget
from mlb_enrichment import apply_mlb_factors, clear_mlb_factors
from mlb_batting_order import apply_batting_order, clear_batting_order, build_best_stacks
from mlb_auto_data import apply_auto_mlb_context
from nfl_auto_data import apply_auto_nfl_context
from learning_db import (
    archive_export_file,
    generate_learning_report,
    history_folder_structure,
    import_historical_result_csvs,
    record_export,
)

logger = logging.getLogger("dfs.ui")


def _pkey(p: Dict[str, Any]) -> str:
    return (
        str(p.get("FlexNamePlusID") or "").strip()
        or str(p.get("FlexID") or "").strip()
        or str(p.get("Name") or "").strip()
    )



class ExposureDialog(QtWidgets.QDialog):
    """
    Exposure viewer for SAVED lineups.

    Sorting:
      - Percent columns sort numerically (not as strings like "7%").
    Visuals:
      - CPT % >= 40% is highlighted.
      - Warnings are shown when exposures exceed soft caps.
    """

    # --- Styling / thresholds (tweak here if you want different defaults) ---
    CPT_HIGHLIGHT_PCT = 40.0

    # Soft caps (warnings only)
    SD_TOTAL_WARN_PCT = 70.0
    SD_FLEX_WARN_PCT = 70.0
    CL_WARN_PCT = 55.0

    # Hard caps (red highlight)
    SD_TOTAL_HARD_PCT = 90.0
    SD_CPT_HARD_PCT = 60.0
    SD_FLEX_HARD_PCT = 90.0
    CL_HARD_PCT = 80.0

    def __init__(self, parent: QtWidgets.QWidget, *, showdown_rows: List[Dict[str, Any]], classic_rows: List[Dict[str, Any]]):
        super().__init__(parent)
        self.setWindowTitle("Saved Lineup Exposure")
        self.setModal(False)
        self.resize(980, 700)

        layout = QtWidgets.QVBoxLayout(self)

        tabs = QtWidgets.QTabWidget(self)
        layout.addWidget(tabs, 1)

        # --- Showdown tab ---
        tab_sd = QtWidgets.QWidget(self)
        sd_layout = QtWidgets.QVBoxLayout(tab_sd)

        self.lbl_sd_warn = QtWidgets.QLabel("")
        self.lbl_sd_warn.setWordWrap(True)
        self.lbl_sd_warn.setStyleSheet("padding: 6px;")
        sd_layout.addWidget(self.lbl_sd_warn)

        self.tbl_sd = QtWidgets.QTableWidget(self)
        self.tbl_sd.setColumnCount(7)
        self.tbl_sd.setHorizontalHeaderLabels(["Player", "FlexID", "CptID", "Count", "Total %", "CPT %", "FLEX %"])
        self.tbl_sd.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl_sd.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl_sd.setSortingEnabled(True)
        self.tbl_sd.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        sd_layout.addWidget(self.tbl_sd, 1)
        tabs.addTab(tab_sd, "Showdown")

        # --- Classic tab ---
        tab_cl = QtWidgets.QWidget(self)
        cl_layout = QtWidgets.QVBoxLayout(tab_cl)

        self.lbl_cl_warn = QtWidgets.QLabel("")
        self.lbl_cl_warn.setWordWrap(True)
        self.lbl_cl_warn.setStyleSheet("padding: 6px;")
        cl_layout.addWidget(self.lbl_cl_warn)

        self.tbl_cl = QtWidgets.QTableWidget(self)
        self.tbl_cl.setColumnCount(5)
        self.tbl_cl.setHorizontalHeaderLabels(["Player", "FlexID", "Pos", "Count", "Exposure %"])
        self.tbl_cl.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl_cl.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl_cl.setSortingEnabled(True)
        self.tbl_cl.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        cl_layout.addWidget(self.tbl_cl, 1)
        tabs.addTab(tab_cl, "Classic")

        btn_close = QtWidgets.QPushButton("Close")
        btn_close.clicked.connect(self.close)
        layout.addWidget(btn_close)

        self._load_showdown(showdown_rows)
        self._load_classic(classic_rows)

    @staticmethod
    def _fmt_pct(v: float) -> str:
        if v is None:
            return ""
        return f"{v:.1f}%"

    def _pct_item(
        self,
        pct: float,
        *,
        highlight_at: Optional[float] = None,
        warn_at: Optional[float] = None,
        hard_at: Optional[float] = None,
        bold: bool = False,
    ) -> QtWidgets.QTableWidgetItem:
        """
        Create a QTableWidgetItem that:
          - Displays 'xx.x%'
          - Sorts by numeric pct (Qt.UserRole)
          - Optionally highlights based on thresholds
        """
        try:
            pct_f = float(pct or 0.0)
        except Exception:
            pct_f = 0.0

        item = SortKeyItem(self._fmt_pct(pct_f))

        # Numeric sort key
        item.setData(QtCore.Qt.UserRole, pct_f)

        # Styling
        font = item.font()
        if bold:
            font.setBold(True)
        item.setFont(font)

        # Background colors
        # (Keep it simple: yellow for warn, red for hard, light red for highlight)
        if hard_at is not None and pct_f >= hard_at:
            item.setBackground(QtGui.QBrush(QtGui.QColor(255, 170, 170)))  # red-ish
            item.setForeground(QtGui.QBrush(QtGui.QColor(40, 0, 0)))
        elif warn_at is not None and pct_f >= warn_at:
            item.setBackground(QtGui.QBrush(QtGui.QColor(255, 245, 170)))  # yellow-ish
            item.setForeground(QtGui.QBrush(QtGui.QColor(40, 30, 0)))
        elif highlight_at is not None and pct_f >= highlight_at:
            item.setBackground(QtGui.QBrush(QtGui.QColor(255, 220, 220)))  # light red highlight

        return item

    def _load_showdown(self, rows: List[Dict[str, Any]]) -> None:
        self.tbl_sd.setRowCount(0)

        # Build warning summary
        total_over = []
        cpt_over = []
        flex_over = []

        for r in rows:
            try:
                total_pct = float(r.get("TotalPct", 0.0) or 0.0)
                cpt_pct = float(r.get("CptPct", 0.0) or 0.0)
                flex_pct = float(r.get("FlexPct", 0.0) or 0.0)
            except Exception:
                total_pct, cpt_pct, flex_pct = 0.0, 0.0, 0.0

            name = str(r.get("Player", ""))

            if total_pct >= self.SD_TOTAL_WARN_PCT:
                total_over.append((total_pct, name))
            if cpt_pct >= self.CPT_HIGHLIGHT_PCT:
                cpt_over.append((cpt_pct, name))
            if flex_pct >= self.SD_FLEX_WARN_PCT:
                flex_over.append((flex_pct, name))

            row = self.tbl_sd.rowCount()
            self.tbl_sd.insertRow(row)

            self.tbl_sd.setItem(row, 0, QtWidgets.QTableWidgetItem(name))
            self.tbl_sd.setItem(row, 1, QtWidgets.QTableWidgetItem(str(r.get("FlexID", ""))))
            self.tbl_sd.setItem(row, 2, QtWidgets.QTableWidgetItem(str(r.get("CptID", ""))))
            cnt_item = SortKeyItem(str(r.get("Count", 0)))
            cnt_item.setData(QtCore.Qt.UserRole, float(r.get("Count", 0) or 0))
            self.tbl_sd.setItem(row, 3, cnt_item)

            self.tbl_sd.setItem(
                row, 4,
                self._pct_item(
                    total_pct,
                    warn_at=self.SD_TOTAL_WARN_PCT,
                    hard_at=self.SD_TOTAL_HARD_PCT
                )
            )
            self.tbl_sd.setItem(
                row, 5,
                self._pct_item(
                    cpt_pct,
                    highlight_at=self.CPT_HIGHLIGHT_PCT,
                    warn_at=self.CPT_HIGHLIGHT_PCT,
                    hard_at=self.SD_CPT_HARD_PCT,
                    bold=(cpt_pct >= self.CPT_HIGHLIGHT_PCT),
                )
            )
            self.tbl_sd.setItem(
                row, 6,
                self._pct_item(
                    flex_pct,
                    warn_at=self.SD_FLEX_WARN_PCT,
                    hard_at=self.SD_FLEX_HARD_PCT
                )
            )

        # Sort warnings lists and show a compact summary
        total_over.sort(reverse=True)
        cpt_over.sort(reverse=True)
        flex_over.sort(reverse=True)

        def _top(items, n=6):
            return ", ".join([f"{name} ({pct:.1f}%)" for pct, name in items[:n]])

        parts = []
        if cpt_over:
            parts.append(f"CPT ≥ {self.CPT_HIGHLIGHT_PCT:.0f}%: {_top(cpt_over)}")
        if total_over:
            parts.append(f"Total ≥ {self.SD_TOTAL_WARN_PCT:.0f}%: {_top(total_over)}")
        if flex_over:
            parts.append(f"FLEX ≥ {self.SD_FLEX_WARN_PCT:.0f}%: {_top(flex_over)}")

        if parts:
            self.lbl_sd_warn.setText(" | ".join(parts))
        else:
            self.lbl_sd_warn.setText("No exposure warnings based on current thresholds.")

        # Preserve current layout while allowing manual column resizing.
        self.tbl_sd.resizeColumnsToContents()

    def _load_classic(self, rows: List[Dict[str, Any]]) -> None:
        self.tbl_cl.setRowCount(0)

        over = []
        for r in rows:
            try:
                pct = float(r.get("Pct", 0.0) or 0.0)
            except Exception:
                pct = 0.0

            name = str(r.get("Player", ""))
            if pct >= self.CL_WARN_PCT:
                over.append((pct, name))

            row = self.tbl_cl.rowCount()
            self.tbl_cl.insertRow(row)
            self.tbl_cl.setItem(row, 0, QtWidgets.QTableWidgetItem(name))
            self.tbl_cl.setItem(row, 1, QtWidgets.QTableWidgetItem(str(r.get("FlexID", ""))))
            self.tbl_cl.setItem(row, 2, QtWidgets.QTableWidgetItem(str(r.get("Pos", ""))))
            cnt_item = SortKeyItem(str(r.get("Count", 0)))
            cnt_item.setData(QtCore.Qt.UserRole, float(r.get("Count", 0) or 0))
            self.tbl_cl.setItem(row, 3, cnt_item)
            self.tbl_cl.setItem(
                row, 4,
                self._pct_item(
                    pct,
                    warn_at=self.CL_WARN_PCT,
                    hard_at=self.CL_HARD_PCT
                )
            )

        over.sort(reverse=True)
        if over:
            top = ", ".join([f"{name} ({pct:.1f}%)" for pct, name in over[:8]])
            self.lbl_cl_warn.setText(f"Exposure ≥ {self.CL_WARN_PCT:.0f}%: {top}")
        else:
            self.lbl_cl_warn.setText("No exposure warnings based on current thresholds.")

        # Preserve current layout while allowing manual column resizing.
        self.tbl_cl.resizeColumnsToContents()


class StackExposureDialog(QtWidgets.QDialog):
    """Saved lineup exposure dashboard focused on teams, stacks, salary, and pitchers."""

    def __init__(
        self,
        parent: QtWidgets.QWidget,
        *,
        sport: str,
        total_lineups: int,
        team_rows: List[Dict[str, Any]],
        stack_rows: List[Dict[str, Any]],
        salary_rows: List[Dict[str, Any]],
        pitcher_rows: List[Dict[str, Any]],
    ):
        super().__init__(parent)
        self.setWindowTitle("Stack / Team / Salary Exposure")
        self.setModal(False)
        self.resize(1050, 720)
        self.sport = (sport or "NFL").upper()
        self.total_lineups = int(total_lineups or 0)

        layout = QtWidgets.QVBoxLayout(self)
        summary = QtWidgets.QLabel(f"{self.sport} saved lineups analyzed: {self.total_lineups}")
        summary.setStyleSheet("font-weight: bold; padding: 6px;")
        layout.addWidget(summary)

        tabs = QtWidgets.QTabWidget(self)
        layout.addWidget(tabs, 1)

        self.tbl_team = self._make_table(["Team", "Lineups", "Exposure %", "Avg Players", "Max Players", "Avg Salary", "Avg Proj"])
        self._add_tab(tabs, "Team Exposure", self.tbl_team)

        self.tbl_stack = self._make_table(["Stack Shape", "Primary", "Secondary", "Count", "Exposure %", "Avg Salary", "Avg Proj", "Examples"])
        self._add_tab(tabs, "Stack Shapes", self.tbl_stack)

        self.tbl_salary = self._make_table(["Salary Band", "Count", "Exposure %", "Avg Salary", "Avg Grade"])
        self._add_tab(tabs, "Salary Bands", self.tbl_salary)

        self.tbl_pitcher = self._make_table(["Pitcher", "Team", "Count", "Exposure %", "Avg Salary", "Avg Proj"])
        self._add_tab(tabs, "Pitchers", self.tbl_pitcher)

        self._load_team(team_rows)
        self._load_stack(stack_rows)
        self._load_salary(salary_rows)
        self._load_pitcher(pitcher_rows)

        btn_close = QtWidgets.QPushButton("Close")
        btn_close.clicked.connect(self.close)
        layout.addWidget(btn_close)

    def _make_table(self, headers: List[str]) -> QtWidgets.QTableWidget:
        tbl = QtWidgets.QTableWidget(self)
        tbl.setColumnCount(len(headers))
        tbl.setHorizontalHeaderLabels(headers)
        tbl.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        tbl.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        tbl.setSortingEnabled(True)
        tbl.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        return tbl

    def _add_tab(self, tabs: QtWidgets.QTabWidget, title: str, table: QtWidgets.QTableWidget) -> None:
        tab = QtWidgets.QWidget(self)
        lay = QtWidgets.QVBoxLayout(tab)
        lay.addWidget(table, 1)
        tabs.addTab(tab, title)

    def _num_item(self, text: str, value: float) -> QtWidgets.QTableWidgetItem:
        item = SortKeyItem(text)
        item.setData(QtCore.Qt.UserRole, float(value or 0.0))
        return item

    def _load_team(self, rows: List[Dict[str, Any]]) -> None:
        self.tbl_team.setRowCount(0)
        for r in rows:
            row = self.tbl_team.rowCount()
            self.tbl_team.insertRow(row)
            self.tbl_team.setItem(row, 0, QtWidgets.QTableWidgetItem(str(r.get("Team", ""))))
            self.tbl_team.setItem(row, 1, self._num_item(str(r.get("Count", 0)), float(r.get("Count", 0) or 0)))
            pct = float(r.get("Pct", 0.0) or 0.0)
            self.tbl_team.setItem(row, 2, self._num_item(f"{pct:.1f}%", pct))
            avg = float(r.get("AvgPlayers", 0.0) or 0.0)
            self.tbl_team.setItem(row, 3, self._num_item(f"{avg:.2f}", avg))
            mx = float(r.get("MaxPlayers", 0.0) or 0.0)
            self.tbl_team.setItem(row, 4, self._num_item(f"{mx:.0f}", mx))
            sal = float(r.get("AvgSalary", 0.0) or 0.0)
            self.tbl_team.setItem(row, 5, self._num_item(f"{sal:,.0f}", sal))
            proj = float(r.get("AvgProj", 0.0) or 0.0)
            self.tbl_team.setItem(row, 6, self._num_item(f"{proj:.2f}", proj))
        self.tbl_team.resizeColumnsToContents()

    def _load_stack(self, rows: List[Dict[str, Any]]) -> None:
        self.tbl_stack.setRowCount(0)
        for r in rows:
            row = self.tbl_stack.rowCount()
            self.tbl_stack.insertRow(row)
            self.tbl_stack.setItem(row, 0, QtWidgets.QTableWidgetItem(str(r.get("Shape", ""))))
            self.tbl_stack.setItem(row, 1, QtWidgets.QTableWidgetItem(str(r.get("Primary", ""))))
            self.tbl_stack.setItem(row, 2, QtWidgets.QTableWidgetItem(str(r.get("Secondary", ""))))
            self.tbl_stack.setItem(row, 3, self._num_item(str(r.get("Count", 0)), float(r.get("Count", 0) or 0)))
            pct = float(r.get("Pct", 0.0) or 0.0)
            self.tbl_stack.setItem(row, 4, self._num_item(f"{pct:.1f}%", pct))
            sal = float(r.get("AvgSalary", 0.0) or 0.0)
            self.tbl_stack.setItem(row, 5, self._num_item(f"{sal:,.0f}", sal))
            proj = float(r.get("AvgProj", 0.0) or 0.0)
            self.tbl_stack.setItem(row, 6, self._num_item(f"{proj:.2f}", proj))
            self.tbl_stack.setItem(row, 7, QtWidgets.QTableWidgetItem(str(r.get("Examples", ""))))
        self.tbl_stack.resizeColumnsToContents()

    def _load_salary(self, rows: List[Dict[str, Any]]) -> None:
        self.tbl_salary.setRowCount(0)
        for r in rows:
            row = self.tbl_salary.rowCount()
            self.tbl_salary.insertRow(row)
            self.tbl_salary.setItem(row, 0, QtWidgets.QTableWidgetItem(str(r.get("Band", ""))))
            self.tbl_salary.setItem(row, 1, self._num_item(str(r.get("Count", 0)), float(r.get("Count", 0) or 0)))
            pct = float(r.get("Pct", 0.0) or 0.0)
            self.tbl_salary.setItem(row, 2, self._num_item(f"{pct:.1f}%", pct))
            sal = float(r.get("AvgSalary", 0.0) or 0.0)
            self.tbl_salary.setItem(row, 3, self._num_item(f"{sal:,.0f}", sal))
            grade = float(r.get("AvgGrade", 0.0) or 0.0)
            self.tbl_salary.setItem(row, 4, self._num_item(f"{grade:.1f}", grade))
        self.tbl_salary.resizeColumnsToContents()

    def _load_pitcher(self, rows: List[Dict[str, Any]]) -> None:
        self.tbl_pitcher.setRowCount(0)
        for r in rows:
            row = self.tbl_pitcher.rowCount()
            self.tbl_pitcher.insertRow(row)
            self.tbl_pitcher.setItem(row, 0, QtWidgets.QTableWidgetItem(str(r.get("Pitcher", ""))))
            self.tbl_pitcher.setItem(row, 1, QtWidgets.QTableWidgetItem(str(r.get("Team", ""))))
            self.tbl_pitcher.setItem(row, 2, self._num_item(str(r.get("Count", 0)), float(r.get("Count", 0) or 0)))
            pct = float(r.get("Pct", 0.0) or 0.0)
            self.tbl_pitcher.setItem(row, 3, self._num_item(f"{pct:.1f}%", pct))
            sal = float(r.get("AvgSalary", 0.0) or 0.0)
            self.tbl_pitcher.setItem(row, 4, self._num_item(f"{sal:,.0f}", sal))
            proj = float(r.get("AvgProj", 0.0) or 0.0)
            self.tbl_pitcher.setItem(row, 5, self._num_item(f"{proj:.2f}", proj))
        self.tbl_pitcher.resizeColumnsToContents()


class ResultsLearningDialog(QtWidgets.QDialog):
    """Local results import and learning report."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Results & Learning")
        self.resize(820, 700)
        layout = QtWidgets.QVBoxLayout(self)

        intro = QtWidgets.QLabel(
            "Import DraftKings contest standings or contest-history CSV files. "
            "The app matches exact rosters to lineups exported from this app; all data stays local."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.summary = QtWidgets.QLabel("")
        self.summary.setObjectName("learningSummary")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.report = QtWidgets.QPlainTextEdit(self)
        self.report.setObjectName("learningReport")
        self.report.setReadOnly(True)
        layout.addWidget(self.report, 1)

        buttons = QtWidgets.QHBoxLayout()
        import_button = QtWidgets.QPushButton("Import DraftKings Results")
        import_button.setObjectName("importResultsButton")
        import_button.clicked.connect(self.import_results)
        buttons.addWidget(import_button)

        refresh_button = QtWidgets.QPushButton("Refresh Report")
        refresh_button.clicked.connect(self.refresh_report)
        buttons.addWidget(refresh_button)

        folder_button = QtWidgets.QPushButton("Open Local History Folder")
        folder_button.clicked.connect(self.open_history_folder)
        buttons.addWidget(folder_button)
        buttons.addStretch(1)

        close_button = QtWidgets.QPushButton("Close")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        self.refresh_report()

    def refresh_report(self) -> None:
        try:
            payload = generate_learning_report()
            roi = payload.get("roi_pct")
            roi_text = f"{float(roi):+.1f}% ROI" if roi is not None else "ROI unavailable"
            self.summary.setText(
                f"{int(payload.get('exported_lineups', 0)):,} exported lineups  |  "
                f"{int(payload.get('matched_rows', 0)):,} matched results  |  "
                f"{float(payload.get('match_rate', 0.0)):.1f}% match rate  |  {roi_text}"
            )
            self.report.setPlainText(str(payload.get("text", "")))
        except Exception as exc:
            logger.exception("Learning report refresh failed")
            self.summary.setText("Results report is temporarily unavailable.")
            self.report.setPlainText(str(exc))

    def import_results(self) -> None:
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Select DraftKings Result CSV Files",
            "",
            "CSV Files (*.csv)",
        )
        if not paths:
            return
        try:
            result = import_historical_result_csvs(paths)
            self.refresh_report()
            message = (
                f"Imported {int(result.get('rows_imported', 0)):,} result entries from "
                f"{int(result.get('files_imported', 0)):,} file(s).\n\n"
                f"Exact lineup matches: {int(result.get('matched_rows', 0)):,}\n"
                f"Unmatched entries: {int(result.get('unmatched_rows', 0)):,}"
            )
            if int(result.get("duplicates_skipped", 0)):
                message += f"\nAlready imported files skipped: {int(result.get('duplicates_skipped', 0)):,}"
            if result.get("errors"):
                message += "\n\nSome files could not be imported:\n" + "\n".join(result["errors"])
            QtWidgets.QMessageBox.information(self, "Results Imported", message)
        except Exception as exc:
            logger.exception("Results import failed")
            QtWidgets.QMessageBox.critical(self, "Results Import Error", str(exc))

    def open_history_folder(self) -> None:
        folder = history_folder_structure()["history"]
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(folder))


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DFS Optimizer - Results & Learning Upgrade v3")
        self.resize(1300, 820)

        self.players: List[Dict[str, Any]] = []
        self.last_showdown: List[Dict[str, Any]] = []
        self.last_classic: List[List[Dict[str, Any]]] = []

        self.saved_showdown: List[Dict[str, Any]] = []
        self.saved_classic: List[List[Dict[str, Any]]] = []

        self._build_ui()

    # ---------------- UI ----------------

    def _build_ui(self) -> None:
        root = QtWidgets.QSplitter(QtCore.Qt.Horizontal, self)
        self.setCentralWidget(root)

        # Left panel
        left = QtWidgets.QWidget(self)
        left_layout = QtWidgets.QVBoxLayout(left)


        # Top controls (multi-row so buttons/toggles don't scrunch)
        top_box = QtWidgets.QVBoxLayout()

        # --- Row 1: load + injuries + ownership sim ---
        row1 = QtWidgets.QHBoxLayout()

        btn_load = QtWidgets.QPushButton("Load Player CSV")
        btn_load.clicked.connect(self.on_load_csv)
        row1.addWidget(btn_load)

        btn_refresh_inj = QtWidgets.QPushButton("Refresh Context")
        btn_refresh_inj.setToolTip("Refresh injuries plus automatic NFL role, usage, matchup, and weather context.")
        btn_refresh_inj.clicked.connect(self.on_refresh_injuries)
        row1.addWidget(btn_refresh_inj)

        btn_learning = QtWidgets.QPushButton("Results & Learning")
        btn_learning.setObjectName("resultsLearningButton")
        btn_learning.setToolTip("Import DraftKings results and review local lineup performance.")
        btn_learning.clicked.connect(self.on_results_learning)
        row1.addWidget(btn_learning)

        row1.addSpacing(18)
        row1.addWidget(QtWidgets.QLabel("Sport:"))
        self.combo_sport = QtWidgets.QComboBox()
        self.combo_sport.addItems(["NFL", "MLB", "NBA", "WNBA"])
        self.combo_sport.setCurrentText("NFL")
        self.combo_sport.setToolTip("Choose the sport for Classic roster rules. Showdown remains CPT + 5 FLEX.")
        self.combo_sport.currentTextChanged.connect(self._on_sport_changed)
        row1.addWidget(self.combo_sport)

        row1.addSpacing(18)
        row1.addWidget(QtWidgets.QLabel("Own Sims:"))
        self.spin_own_sims = QtWidgets.QSpinBox()
        self.spin_own_sims.setRange(500, 50000)
        self.spin_own_sims.setSingleStep(500)
        self.spin_own_sims.setValue(5000)
        self.spin_own_sims.setToolTip("Number of simulated lineups to estimate slate ownership.")
        row1.addWidget(self.spin_own_sims)

        # Template-based Showdown ownership sim (Option B)
        self.chk_sd_template_sim = QtWidgets.QCheckBox("Field Templates (SD)")
        self.chk_sd_template_sim.setChecked(True)
        self.chk_sd_template_sim.setToolTip(
            "Showdown only: simulate field-like lineup constructions (QB-heavy templates).\n"
            "Helps capture realities like 'only one viable QB' increasing ownership."
        )
        row1.addWidget(self.chk_sd_template_sim)

        btn_own_sim = QtWidgets.QPushButton("Recalc Own% (Sim)")
        btn_own_sim.setToolTip("Run a lineup simulation to estimate slate ownership (uses projections/value + salary cap constraints).")
        btn_own_sim.clicked.connect(self.on_recalc_ownership_sim)
        row1.addWidget(btn_own_sim)

        row1.addStretch(1)
        top_box.addLayout(row1)

        # --- Row 2: builder ownership influence ---
        row2 = QtWidgets.QHBoxLayout()
        row2.addWidget(QtWidgets.QLabel("Build Own Mode:"))

        self.combo_build_own_mode = QtWidgets.QComboBox()
        self.combo_build_own_mode.addItems(["Balanced", "Leverage", "Chalk"])
        self.combo_build_own_mode.setCurrentText("Balanced")
        self.combo_build_own_mode.setToolTip(
            "How to use simulated Own% while building lineups.\n"
            "Balanced: mild tie-break toward lower-owned combos.\n"
            "Leverage: prefer lower-owned plays/combos.\n"
            "Chalk: prefer higher-owned plays/combos (cash-like)."
        )
        row2.addWidget(self.combo_build_own_mode)

        row2.addWidget(QtWidgets.QLabel("λ:"))
        self.spin_build_own_weight = QtWidgets.QDoubleSpinBox()
        self.spin_build_own_weight.setRange(0.0, 5.0)
        self.spin_build_own_weight.setSingleStep(0.05)
        self.spin_build_own_weight.setDecimals(2)
        self.spin_build_own_weight.setValue(0.15)
        self.spin_build_own_weight.setToolTip(
            "Ownership influence weight (λ). Higher = stronger push.\n"
            "Start around 0.10–0.30. Set 0 to ignore Own% in building."
        )
        row2.addWidget(self.spin_build_own_weight)

        row2.addSpacing(18)
        row2.addWidget(QtWidgets.QLabel("Build Style:"))
        self.combo_build_style = QtWidgets.QComboBox()
        self.combo_build_style.addItems(["Strategic", "Balanced", "Contrarian", "Chalk", "Randomized"])
        self.combo_build_style.setCurrentText("Strategic")
        self.combo_build_style.setToolTip(
            "NFL Strategic: QB stack required, correlation scoring, 2+ portfolio uniques preferred.\n"
            "Balanced: lighter strategy bias with QB correlation intact.\n"
            "Contrarian: stronger portfolio diversity (3 uniques preferred) and leverage-friendly scoring.\n"
            "Chalk: projection/chalk-leaning builds with basic QB correlation.\n"
            "Randomized: mostly projection/value, while retaining anti-correlation safety rails.\n"
            "Other sports keep their existing sport-specific strategy behavior."
        )
        row2.addWidget(self.combo_build_style)

        row2.addSpacing(18)
        row2.addWidget(QtWidgets.QLabel("MLB Stack:"))
        self.combo_mlb_stack_pref = QtWidgets.QComboBox()
        self.combo_mlb_stack_pref.addItems(["Any Strategic", "Prefer 5-3", "Prefer 5-2-1", "Prefer 4-4", "Prefer 4-3-1", "No Stack Bias"])
        self.combo_mlb_stack_pref.setCurrentText("Any Strategic")
        self.combo_mlb_stack_pref.setToolTip("Soft preference for MLB stack shapes. This nudges generation; it does not hard-lock impossible builds.")
        row2.addWidget(self.combo_mlb_stack_pref)

        row2.addSpacing(18)
        row2.addWidget(QtWidgets.QLabel("Salary:"))
        self.combo_salary_strategy = QtWidgets.QComboBox()
        self.combo_salary_strategy.addItems(["Near Cap", "Maximize Salary", "Balanced Spend", "Salary Leverage"])
        self.combo_salary_strategy.setCurrentText("Near Cap")
        self.combo_salary_strategy.setToolTip(
            "Near Cap: prefer $48.5k+ on a $50k cap.\n"
            "Maximize Salary: stricter, usually $49k+.\n"
            "Balanced Spend: allows around $47k+.\n"
            "Salary Leverage: allows more unused salary for contrarian builds."
        )
        row2.addWidget(self.combo_salary_strategy)

        row2.addStretch(1)
        top_box.addLayout(row2)

        # --- Row 3: tags + max exposure controls ---
        row3 = QtWidgets.QHBoxLayout()
        row3.addWidget(QtWidgets.QLabel("Tags (selected players):"))

        btn_max_cpt = QtWidgets.QPushButton("Max CPT%")
        btn_max_cpt.setToolTip("Set a maximum Captain ownership % for selected players (Showdown only).")
        btn_max_cpt.clicked.connect(lambda: self.set_max_pct(kind="cpt"))
        row3.addWidget(btn_max_cpt)

        btn_max_exposure = QtWidgets.QPushButton("Max Exposure%")
        btn_max_exposure.setToolTip("Set a maximum exposure % for selected players. In Classic this caps total appearances; in Showdown this caps FLEX appearances.")
        btn_max_exposure.clicked.connect(lambda: self.set_max_pct(kind="exposure"))
        row3.addWidget(btn_max_exposure)

        btn_clear_max = QtWidgets.QPushButton("Clear Max%")
        btn_clear_max.setToolTip("Clear Max CPT% / Max% for selected players.")
        btn_clear_max.clicked.connect(self.clear_max_pct)
        row3.addWidget(btn_clear_max)

        btn_copy_own_to_max = QtWidgets.QPushButton("Own% → Max%")
        btn_copy_own_to_max.setToolTip(
            "Copy simulated projected ownership into Max% caps.\n"
            "Showdown: CPT Own% -> MaxCPT%, FLEX Own% -> Max Exposure%.\n"
            "Classic: Own% -> Max Exposure%.\n"
            "Applies to selected players; if none selected, applies to ALL players."
        )
        btn_copy_own_to_max.clicked.connect(self.copy_own_to_max)
        row3.addWidget(btn_copy_own_to_max)

        row3.addSpacing(12)

        btn_lock = QtWidgets.QPushButton("Lock")
        btn_lock.clicked.connect(lambda: self.apply_tags(mode="lock"))
        row3.addWidget(btn_lock)

        btn_fade = QtWidgets.QPushButton("Fade")
        btn_fade.clicked.connect(lambda: self.apply_tags(mode="fade"))
        row3.addWidget(btn_fade)

        btn_cpt_lock = QtWidgets.QPushButton("CPT Lock")
        btn_cpt_lock.clicked.connect(lambda: self.apply_tags(mode="cpt_lock"))
        row3.addWidget(btn_cpt_lock)

        btn_cpt_fade = QtWidgets.QPushButton("CPT Fade")
        btn_cpt_fade.clicked.connect(lambda: self.apply_tags(mode="cpt_fade"))
        row3.addWidget(btn_cpt_fade)

        btn_clear = QtWidgets.QPushButton("Clear Tags")
        btn_clear.setToolTip("Clears tags for selected rows; if none selected, clears ALL tags.")
        btn_clear.clicked.connect(self.clear_tags)
        row3.addWidget(btn_clear)

        row3.addSpacing(12)
        btn_team_boost = QtWidgets.QPushButton("Team Boost")
        btn_team_boost.setToolTip("Boost all players from the selected player/team by a projection percentage.")
        btn_team_boost.clicked.connect(lambda: self.set_team_adjustment(boost=True))
        row3.addWidget(btn_team_boost)

        btn_team_fade = QtWidgets.QPushButton("Team Fade")
        btn_team_fade.setToolTip("Reduce all players from the selected player/team by a projection percentage.")
        btn_team_fade.clicked.connect(lambda: self.set_team_adjustment(boost=False))
        row3.addWidget(btn_team_fade)

        btn_clear_team_adj = QtWidgets.QPushButton("Clear Team Adj")
        btn_clear_team_adj.setToolTip("Clear all team boost/fade adjustments.")
        btn_clear_team_adj.clicked.connect(self.clear_team_adjustments)
        row3.addWidget(btn_clear_team_adj)
        btn_clear_team_adj.setVisible(False)

        row3.addSpacing(12)
        btn_save_tags = QtWidgets.QPushButton("Save Tags (JSON)")
        btn_save_tags.clicked.connect(self.save_tags_json)
        row3.addWidget(btn_save_tags)
        btn_save_tags.setVisible(False)

        btn_load_tags = QtWidgets.QPushButton("Load Tags (JSON)")
        btn_load_tags.clicked.connect(self.load_tags_json)
        row3.addWidget(btn_load_tags)
        btn_load_tags.setVisible(False)

        row3.addSpacing(12)
        btn_load_mlb = QtWidgets.QPushButton("Load MLB Factors")
        btn_load_mlb.setToolTip("Load optional MLB recent form / matchup / park / weather / Vegas factor CSV and adjust projections.")
        btn_load_mlb.clicked.connect(self.on_load_mlb_factors)
        row3.addWidget(btn_load_mlb)
        btn_load_mlb.setVisible(False)

        btn_clear_mlb = QtWidgets.QPushButton("Clear MLB Factors")
        btn_clear_mlb.setToolTip("Reset MLB-adjusted projections back to base projections.")
        btn_clear_mlb.clicked.connect(self.on_clear_mlb_factors)
        row3.addWidget(btn_clear_mlb)
        btn_clear_mlb.setVisible(False)

        btn_load_order = QtWidgets.QPushButton("Load Batting Order")
        btn_load_order.setToolTip("Load optional MLB confirmed lineups / batting order / handedness CSV.")
        btn_load_order.clicked.connect(self.on_load_batting_order)
        row3.addWidget(btn_load_order)
        btn_load_order.setVisible(False)

        btn_clear_order = QtWidgets.QPushButton("Clear Batting Order")
        btn_clear_order.setToolTip("Clear MLB batting order and handedness fields.")
        btn_clear_order.clicked.connect(self.on_clear_batting_order)
        row3.addWidget(btn_clear_order)
        btn_clear_order.setVisible(False)

        row3.addStretch(1)
        top_box.addLayout(row3)

        left_layout.addLayout(top_box)


        # Player table
        self.tbl_players = QtWidgets.QTableWidget(self)
        self.tbl_players.setColumnCount(21)
        self.tbl_players.setHorizontalHeaderLabels(["Name", "Team", "Pos", "Injury", "Salary", "BaseProj", "AdjProj", "NFL±", "Usage", "Matchup", "Role", "Wx", "Vegas", "TeamAdj", "Tags", "Own% Tot", "MaxCPT%", "Max%", "Order", "Bats", "Conf"] )
        self.tbl_players.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl_players.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl_players.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        # Enable click-to-sort without changing the visual layout.
        self.tbl_players.setSortingEnabled(True)
        # Allow user to drag column widths; we still auto-size on refresh to keep the same initial look.
        self.tbl_players.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        left_layout.addWidget(self.tbl_players, 2)

        # Bottom tabs
        tabs = QtWidgets.QTabWidget(self)

        self.tabs_lineups = tabs
        # Showdown tab
        tab_sd = QtWidgets.QWidget(self)
        sd_layout = QtWidgets.QVBoxLayout(tab_sd)

        sd_controls = QtWidgets.QHBoxLayout()
        sd_controls.addWidget(QtWidgets.QLabel("Lineups:"))
        self.spin_sd = QtWidgets.QSpinBox()
        self.spin_sd.setRange(1, 150)
        self.spin_sd.setValue(5)
        sd_controls.addWidget(self.spin_sd)

        sd_controls.addWidget(QtWidgets.QLabel("Cap:"))
        self.edit_sd_cap = QtWidgets.QLineEdit("50000")
        self.edit_sd_cap.setFixedWidth(90)
        sd_controls.addWidget(self.edit_sd_cap)

        btn_build_sd = QtWidgets.QPushButton("Build Showdown")
        btn_build_sd.clicked.connect(self.on_build_showdown)
        sd_controls.addWidget(btn_build_sd)

        btn_sd_save_all = QtWidgets.QPushButton("Save All")
        btn_sd_save_all.clicked.connect(self.on_sd_save_all)
        sd_controls.addWidget(btn_sd_save_all)

        btn_sd_unsave_all = QtWidgets.QPushButton("Unsave All")
        btn_sd_unsave_all.clicked.connect(self.on_sd_unsave_all)
        sd_controls.addWidget(btn_sd_unsave_all)

        btn_export_sd = QtWidgets.QPushButton("Export Saved CSV")
        btn_export_sd.setToolTip("Save DraftKings roster IDs and record these lineups for local result matching.")
        btn_export_sd.clicked.connect(lambda: self.on_export_saved("showdown"))
        sd_controls.addWidget(btn_export_sd)

        sd_controls.addStretch(1)
        sd_layout.addLayout(sd_controls)

        self.tbl_sd = CopyRowTableWidget(self)
        self.tbl_sd.setColumnCount(7)
        self.tbl_sd.setHorizontalHeaderLabels(["Save", "CPT", "FLEX1", "FLEX2", "FLEX3", "FLEX4", "FLEX5"])
        self.tbl_sd.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl_sd.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl_sd.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        sd_layout.addWidget(self.tbl_sd, 2)

        tabs.addTab(tab_sd, "Showdown")

        # Classic tab
        tab_cl = QtWidgets.QWidget(self)
        cl_layout = QtWidgets.QVBoxLayout(tab_cl)

        cl_controls = QtWidgets.QHBoxLayout()
        cl_controls.addWidget(QtWidgets.QLabel("Lineups:"))
        self.spin_cl = QtWidgets.QSpinBox()
        self.spin_cl.setRange(1, 150)
        self.spin_cl.setValue(5)
        cl_controls.addWidget(self.spin_cl)

        cl_controls.addWidget(QtWidgets.QLabel("Cap:"))
        self.edit_cl_cap = QtWidgets.QLineEdit("50000")
        self.edit_cl_cap.setFixedWidth(90)
        cl_controls.addWidget(self.edit_cl_cap)

        btn_build_cl = QtWidgets.QPushButton("Build Classic / Sport")
        btn_build_cl.clicked.connect(self.on_build_classic)
        cl_controls.addWidget(btn_build_cl)

        btn_cl_save_all = QtWidgets.QPushButton("Save All")
        btn_cl_save_all.clicked.connect(self.on_cl_save_all)
        cl_controls.addWidget(btn_cl_save_all)

        btn_cl_unsave_all = QtWidgets.QPushButton("Unsave All")
        btn_cl_unsave_all.clicked.connect(self.on_cl_unsave_all)
        cl_controls.addWidget(btn_cl_unsave_all)

        btn_export_cl = QtWidgets.QPushButton("Export Saved CSV")
        btn_export_cl.setToolTip("Save DraftKings roster IDs and record these lineups for local result matching.")
        btn_export_cl.clicked.connect(lambda: self.on_export_saved("classic"))
        cl_controls.addWidget(btn_export_cl)

        cl_controls.addStretch(1)
        cl_layout.addLayout(cl_controls)

        self.tbl_cl = CopyRowTableWidget(self)
        self.tbl_cl.setColumnCount(11)
        self.tbl_cl.setHorizontalHeaderLabels(
            ["Save", "QB", "RB1", "RB2", "WR1", "WR2", "WR3", "TE", "FLEX", "DST", "TotalSal"]
        )
        self.tbl_cl.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl_cl.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl_cl.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        cl_layout.addWidget(self.tbl_cl, 2)

        tabs.addTab(tab_cl, "Classic")

        # Best Stacks tab (MLB-first report)
        tab_stacks = QtWidgets.QWidget(self)
        stacks_layout = QtWidgets.QVBoxLayout(tab_stacks)
        stacks_controls = QtWidgets.QHBoxLayout()
        btn_refresh_stacks = QtWidgets.QPushButton("Refresh Best Stacks")
        btn_refresh_stacks.clicked.connect(self._refresh_best_stacks_table)
        stacks_controls.addWidget(btn_refresh_stacks)
        stacks_controls.addStretch(1)
        stacks_layout.addLayout(stacks_controls)
        self.tbl_best_stacks = CopyRowTableWidget(self)
        self.tbl_best_stacks.setColumnCount(14)
        self.tbl_best_stacks.setHorizontalHeaderLabels(["Rank", "Team", "Score", "Top5Proj", "Top8Proj", "Form", "Matchup", "Park", "Wx", "Vegas", "TeamAdj", "Confirmed", "TopOrder", "Top Hitters"] )
        self.tbl_best_stacks.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl_best_stacks.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl_best_stacks.setSortingEnabled(True)
        self.tbl_best_stacks.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        stacks_layout.addWidget(self.tbl_best_stacks, 1)
        tabs.addTab(tab_stacks, "Best Stacks")

        left_layout.addWidget(self.tabs_lineups, 3)
        root.addWidget(left)

        # Right panel (Saved)
        right = QtWidgets.QWidget(self)
        right_layout = QtWidgets.QVBoxLayout(right)

        self.lbl_saved = QtWidgets.QLabel("Saved: 0 showdown | 0 classic")
        self.lbl_saved.setAlignment(QtCore.Qt.AlignCenter)
        right_layout.addWidget(self.lbl_saved)

        btn_clear_saved = QtWidgets.QPushButton("Clear All Saved")
        btn_clear_saved.clicked.connect(self.on_clear_saved)
        right_layout.addWidget(btn_clear_saved)

        btn_view_exposure = QtWidgets.QPushButton("View Exposure (Saved)")
        btn_view_exposure.setToolTip("Show player exposure based on the lineups currently saved on the right.")
        btn_view_exposure.clicked.connect(self.on_view_exposure)
        right_layout.addWidget(btn_view_exposure)

        btn_view_stack_exp = QtWidgets.QPushButton("Stack Exposure Dashboard")
        btn_view_stack_exp.setToolTip("Show saved-lineup team, stack-shape, salary-band, and pitcher exposure.")
        btn_view_stack_exp.clicked.connect(self.on_view_stack_exposure)
        right_layout.addWidget(btn_view_stack_exp)

        right_layout.addWidget(QtWidgets.QLabel("Saved Showdown (Ctrl+C copies)"))
        self.tbl_saved_sd = CopyRowTableWidget(self)
        self.tbl_saved_sd.setColumnCount(6)
        self.tbl_saved_sd.setHorizontalHeaderLabels(["CPT", "FLEX1", "FLEX2", "FLEX3", "FLEX4", "FLEX5"])
        self.tbl_saved_sd.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl_saved_sd.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl_saved_sd.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        right_layout.addWidget(self.tbl_saved_sd, 1)

        right_layout.addWidget(QtWidgets.QLabel("Saved Classic (Ctrl+C copies)"))
        self.tbl_saved_cl = CopyRowTableWidget(self)
        self.tbl_saved_cl.setColumnCount(9)
        self.tbl_saved_cl.setHorizontalHeaderLabels(["QB", "RB1", "RB2", "WR1", "WR2", "WR3", "TE", "FLEX", "DST"])
        self.tbl_saved_cl.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl_saved_cl.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl_saved_cl.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        right_layout.addWidget(self.tbl_saved_cl, 1)

        root.addWidget(right)
        root.setSizes([900, 400])

        # Status bar
        self.status = QtWidgets.QStatusBar(self)
        self.setStatusBar(self.status)

        # Ownership sim progress widgets (hidden by default)
        self._own_progress = QtWidgets.QProgressBar(self)
        self._own_progress.setVisible(False)
        self._own_progress.setMaximumWidth(260)
        self._own_progress.setTextVisible(False)
        self.status.addPermanentWidget(self._own_progress)

        self._own_eta = QtWidgets.QLabel("")
        self._own_eta.setVisible(False)
        self.status.addPermanentWidget(self._own_eta)

        self._own_thread = None
        self._own_worker = None

        # Lineup build progress widgets (hidden by default)
        self._build_progress = QtWidgets.QProgressBar(self)
        self._build_progress.setVisible(False)
        self._build_progress.setMaximumWidth(260)
        self._build_progress.setTextVisible(False)
        self.status.addPermanentWidget(self._build_progress)

        self._build_eta = QtWidgets.QLabel("")
        self._build_eta.setVisible(False)
        self.status.addPermanentWidget(self._build_eta)

        self._build_cancel = QtWidgets.QPushButton("Cancel")
        self._build_cancel.setToolTip("Stop the active Showdown lineup build and keep completed lineups.")
        self._build_cancel.setVisible(False)
        self._build_cancel.clicked.connect(self._cancel_lineup_build)
        self.status.addPermanentWidget(self._build_cancel)

        self._build_thread = None
        self._build_worker = None

        # CSV / auto-context load progress widgets (hidden by default).
        self._load_progress = QtWidgets.QProgressBar(self)
        self._load_progress.setVisible(False)
        self._load_progress.setMaximumWidth(260)
        self._load_progress.setTextVisible(True)
        self.status.addPermanentWidget(self._load_progress)

        self._load_eta = QtWidgets.QLabel("")
        self._load_eta.setVisible(False)
        self.status.addPermanentWidget(self._load_eta)

    # ---------------- Tag model ----------------

    def _get_selected_player_rows(self) -> List[int]:
        """Return indices into self.players for currently selected table rows.

        NOTE: The players table can be sorted by the user. QTableWidget's row numbers
        reflect the *view* order, not the underlying self.players order. To keep tags/fades
        consistent under sorting, we store each player's stable key (_pkey) in the Name
        cell's Qt.UserRole, then map selected keys back to self.players indices.
        """
        view_rows = sorted({idx.row() for idx in self.tbl_players.selectedIndexes()})
        if not view_rows or not self.players:
            return []

        # Pull stable keys from the Name column (col 0)
        keys: List[str] = []
        for vr in view_rows:
            item = self.tbl_players.item(vr, 0)
            if item is None:
                continue
            k = item.data(QtCore.Qt.UserRole)
            if k is None:
                continue
            ks = str(k).strip()
            if ks:
                keys.append(ks)

        if not keys:
            return []

        key_to_index = {_pkey(p): i for i, p in enumerate(self.players)}
        indices = []
        seen = set()
        for k in keys:
            idx = key_to_index.get(k)
            if idx is None or idx in seen:
                continue
            seen.add(idx)
            indices.append(idx)
        return indices

    def _tags_to_text(self, p: Dict[str, Any]) -> str:
        bits = []
        if p.get("LockFlex"):
            bits.append("L")
        if p.get("FadeFlex"):
            bits.append("F")
        if p.get("LockCpt"):
            bits.append("CL")
        if p.get("FadeCpt"):
            bits.append("CF")
        return " ".join(bits)

    def apply_tags(self, mode: str) -> None:
        rows = self._get_selected_player_rows()
        if not rows:
            self.status.showMessage("Select one or more players first.", 3000)
            return

        for r in rows:
            p = self.players[r]
            if mode == "lock":
                p["LockFlex"] = True
                p["FadeFlex"] = False
            elif mode == "fade":
                p["FadeFlex"] = True
                p["LockFlex"] = False
                # If they fade overall, also prevent CPT implicitly unless you want otherwise
                p["FadeCpt"] = True
                p["LockCpt"] = False
            elif mode == "cpt_lock":
                p["LockCpt"] = True
                p["FadeCpt"] = False
                # If CPT is locked, don't allow fading overall
                p["FadeFlex"] = False
            elif mode == "cpt_fade":
                p["FadeCpt"] = True
                p["LockCpt"] = False

        self._refresh_players_table()
        self.status.showMessage(f"Applied tags: {mode}", 2000)

    def clear_tags(self) -> None:
        rows = self._get_selected_player_rows()
        if rows:
            targets = [self.players[r] for r in rows]
            msg = "Cleared tags for selected players."
        else:
            targets = self.players
            msg = "Cleared tags for ALL players."

        for p in targets:
            p["LockFlex"] = False
            p["FadeFlex"] = False
            p["LockCpt"] = False
            p["FadeCpt"] = False

        self._refresh_players_table()
        self.status.showMessage(msg, 3000)

    def set_team_adjustment(self, *, boost: bool) -> None:
        if not self.players:
            self.status.showMessage("Load a CSV first.", 3000)
            return
        rows = self._get_selected_player_rows()
        if not rows:
            self.status.showMessage("Select a player from the team you want to adjust.", 3000)
            return
        team = str(self.players[rows[0]].get("Team", "")).strip()
        if not team:
            self.status.showMessage("Selected player has no team value.", 3000)
            return
        default = 8.0 if boost else 8.0
        label = "Boost" if boost else "Fade"
        val, ok = QtWidgets.QInputDialog.getDouble(
            self,
            f"{label} Team {team}",
            f"Projection percentage to {'boost' if boost else 'reduce'} every {team} player:",
            default,
            0.0,
            50.0,
            1,
        )
        if not ok:
            return
        signed = float(val if boost else -val)
        changed = 0
        for p in self.players:
            if str(p.get("Team", "")).strip() == team:
                p["TeamAdjPct"] = signed
                changed += 1
        self.recalc_ownership_quick()
        self._refresh_players_table()
        self._refresh_best_stacks_table()
        self.status.showMessage(f"Applied {signed:+.1f}% team adjustment to {changed} {team} players.", 5000)

    def clear_team_adjustments(self) -> None:
        if not self.players:
            return
        for p in self.players:
            p["TeamAdjPct"] = 0.0
        self.recalc_ownership_quick()
        self._refresh_players_table()
        self._refresh_best_stacks_table()
        self.status.showMessage("Cleared all team boost/fade adjustments.", 4000)


    # ---------------- Showdown Max Ownership Caps ----------------

    
    # ---------------- Max Ownership / Exposure Caps ----------------

    def set_max_pct(self, *, kind: str) -> None:
        """Set max ownership/exposure percent for selected players.

        - kind="cpt": sets MaxCptPct (Captain slot cap) — used in Showdown only.
        - kind="exposure": sets MaxPct (unified cap)
            * Classic: caps TOTAL appearances across generated Classic lineups
            * Showdown: caps FLEX appearances across generated Showdown lineups

        Notes:
        - Classic lineup generation ignores MaxCptPct entirely.
        - For backward compatibility, Showdown FLEX capping also accepts legacy MaxFlexPct where present.
        """
        rows = self._get_selected_player_rows()
        if not rows:
            self.status.showMessage("Select one or more players first.", 3000)
            return

        kind_u = (kind or "").strip().lower()
        if kind_u not in ("cpt", "exposure"):
            self.status.showMessage("Invalid max% kind.", 3000)
            return

        if kind_u == "cpt":
            label = "Max CPT%"
            key = "MaxCptPct"
            help_text = "Applies to generated Showdown lineups only. Classic ignores this."
        else:
            label = "Max Exposure%"
            key = "MaxPct"
            help_text = "Classic: total exposure cap. Showdown: FLEX exposure cap."

        # If all selected share the same value, use it as default; else 25.
        current_vals: List[float] = []
        for r in rows:
            v = self.players[r].get(key, None)
            if v is None and key == "MaxPct":
                # legacy fallback (older sessions may have MaxFlexPct only)
                v = self.players[r].get("MaxFlexPct", None)
            if v is None:
                continue
            try:
                current_vals.append(float(v))
            except Exception:
                pass

        default = 25.0
        if current_vals and all(abs(v - current_vals[0]) < 1e-9 for v in current_vals):
            default = float(current_vals[0])

        prompt = (
            f"{label} (0-100).\n"
            f"{help_text}\n"
            "Leave as 0 to effectively block a player from that slot/exposure."
        )

        val, ok = QtWidgets.QInputDialog.getDouble(
            self,
            f"Set {label}",
            prompt,
            float(default),
            0.0,
            100.0,
            1,
        )
        if not ok:
            return

        for r in rows:
            self.players[r][key] = float(val)
            # If user is setting unified exposure, keep legacy field in sync (optional but helps older code paths)
            if key == "MaxPct":
                self.players[r]["MaxFlexPct"] = None

        self._refresh_players_table()
        self.status.showMessage(f"Set {label} = {val:.1f}% for {len(rows)} players.", 5000)


    def clear_max_pct(self) -> None:
        rows = self._get_selected_player_rows()
        if not rows:
            self.status.showMessage("Select one or more players first.", 3000)
            return

        for r in rows:
            self.players[r]["MaxCptPct"] = None
            self.players[r]["MaxPct"] = None
            # Legacy compatibility:
            self.players[r]["MaxFlexPct"] = None

        self._refresh_players_table()
        self.status.showMessage(f"Cleared Max% for {len(rows)} players.", 4000)

    def copy_own_to_max(self) -> None:
        """Copy simulated ownership into max exposure cap fields.

        - Applies to selected players; if none selected, applies to all players.
        - Showdown:
            * MaxCptPct <= ProjCptOwnPct * scale
            * MaxPct    <= ProjFlexOwnPct * scale  (FLEX cap)
        - Classic:
            * MaxPct    <= ProjOwnPct * scale      (total cap)
        """
        if not self.players:
            self.status.showMessage("Load a CSV first.", 3000)
            return

        rows = self._get_selected_player_rows()
        targets = [self.players[r] for r in rows] if rows else list(self.players)

        scale, ok = QtWidgets.QInputDialog.getDouble(
            self,
            "Copy Own% → Max%",
            "Scale factor (1.0 = same, 1.15 = +15% cushion):",
            1.00,
            0.00,
            5.00,
            2,
        )
        if not ok:
            return

        mode = self._contest_mode()

        def cap_from_own(raw_own: float):
            """Convert simulated Own% into a safe Max% cap.

            Zero simulated ownership usually means the player simply did not appear
            in the sim sample, not that the optimizer should be hard-blocked. So
            zero own clears the cap instead of setting Max% to 0. Positive but tiny
            values are lifted to 1% so they allow at least one lineup when used as
            caps.
            """
            try:
                raw = float(raw_own or 0.0)
            except Exception:
                raw = 0.0
            if raw <= 0.0:
                return None
            return max(1.0, min(100.0, raw * scale))

        applied = 0
        for p in targets:
            if mode == "showdown":
                cpt_own = float(p.get("ProjCptOwnPct", 0.0) or 0.0)
                flex_own = float(p.get("ProjFlexOwnPct", 0.0) or 0.0)
                p["MaxCptPct"] = cap_from_own(cpt_own)
                p["MaxPct"] = cap_from_own(flex_own)
                p["MaxFlexPct"] = None
            else:
                tot_own = float(p.get("ProjOwnPct", 0.0) or 0.0)
                p["MaxPct"] = cap_from_own(tot_own)
                p["MaxFlexPct"] = None

            applied += 1

        self._refresh_players_table()
        scope = "selected players" if rows else "ALL players"
        self.status.showMessage(f"Copied Own% → Max% for {applied} ({scope}).", 5000)


    def save_tags_json(self) -> None:
        if not self.players:
            self.status.showMessage("Load a CSV first.", 3000)
            return

        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save Tags JSON", "", "JSON Files (*.json)")
        if not path:
            return

        payload = {
            "version": 1,
            "tags": {
                _pkey(p): {
                    "Name": p.get("Name", ""),
                    "Team": p.get("Team", ""),
                    "LockFlex": bool(p.get("LockFlex", False)),
                    "FadeFlex": bool(p.get("FadeFlex", False)),
                    "LockCpt": bool(p.get("LockCpt", False)),
                    "FadeCpt": bool(p.get("FadeCpt", False)),
                }
                for p in self.players
                if p.get("LockFlex") or p.get("FadeFlex") or p.get("LockCpt") or p.get("FadeCpt")
            },
        }

        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            self.status.showMessage(f"Saved tags: {os.path.basename(path)}", 4000)
            logger.info("Saved tags JSON: %s (%d tagged)", path, len(payload["tags"]))
        except Exception as e:
            logger.exception("Save tags failed")
            QtWidgets.QMessageBox.critical(self, "Save Tags Error", str(e))

    def load_tags_json(self) -> None:
        if not self.players:
            self.status.showMessage("Load a CSV first.", 3000)
            return

        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Load Tags JSON", "", "JSON Files (*.json)")
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)

            tags = (payload or {}).get("tags", {})
            if not isinstance(tags, dict):
                raise ValueError("Invalid tags JSON format.")

            key_to_player = {_pkey(p): p for p in self.players}
            applied = 0
            for k, tv in tags.items():
                p = key_to_player.get(k)
                if not p:
                    continue
                p["LockFlex"] = bool(tv.get("LockFlex", False))
                p["FadeFlex"] = bool(tv.get("FadeFlex", False))
                p["LockCpt"] = bool(tv.get("LockCpt", False))
                p["FadeCpt"] = bool(tv.get("FadeCpt", False))
                applied += 1

            self._refresh_players_table()
            self.status.showMessage(f"Loaded tags: {os.path.basename(path)} ({applied} applied)", 5000)
            logger.info("Loaded tags JSON: %s (%d applied)", path, applied)
        except Exception as e:
            logger.exception("Load tags failed")
            QtWidgets.QMessageBox.critical(self, "Load Tags Error", str(e))

    # ---------------- Visual highlighting ----------------

    def _set_player_row_style(self, row: int, p: Dict[str, Any]) -> None:
        """
        Visual-only highlighting. Does NOT change any logic.

        The old fade style painted the whole row very light gray, which made faded
        players hard to distinguish in the dark theme. This version keeps the row
        readable and uses the Tags/Injury cells plus muted text to show state:
          - Overall fade: muted text + red-tinted Tags/Injury cells
          - CPT fade only: red-tinted Tags cell
          - Lock: green/blue-tinted Tags cell
        """
        is_faded = bool(p.get("FadeFlex"))
        is_cpt_fade = bool(p.get("FadeCpt"))
        is_lock_cpt = bool(p.get("LockCpt"))
        is_lock_flex = bool(p.get("LockFlex"))

        # Column indices from player table headers.
        injury_col = 3
        tags_col = 14

        for c in range(self.tbl_players.columnCount()):
            item = self.tbl_players.item(row, c)
            if item is None:
                continue

            # Reset first so sorting/refreshing does not leave stale styles.
            item.setBackground(QtGui.QBrush())
            item.setForeground(QtGui.QBrush(QtGui.QColor(232, 234, 237)))
            font = item.font()
            font.setStrikeOut(False)
            font.setBold(False)
            item.setFont(font)

            if is_faded:
                # Keep the table dark/readable; do not white-out the whole row.
                item.setForeground(QtGui.QBrush(QtGui.QColor(160, 160, 160)))
                if c in (injury_col, tags_col):
                    item.setBackground(QtGui.QBrush(QtGui.QColor(85, 38, 38)))
                    item.setForeground(QtGui.QBrush(QtGui.QColor(255, 205, 205)))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
            elif is_lock_cpt and c == tags_col:
                item.setBackground(QtGui.QBrush(QtGui.QColor(38, 70, 110)))
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            elif is_lock_flex and c == tags_col:
                item.setBackground(QtGui.QBrush(QtGui.QColor(35, 85, 55)))
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            elif is_cpt_fade and c == tags_col:
                item.setBackground(QtGui.QBrush(QtGui.QColor(85, 48, 48)))
                item.setForeground(QtGui.QBrush(QtGui.QColor(255, 215, 215)))

    # ---------------- Helpers ----------------

    def _safe_float(self, text: str, default: float) -> float:
        try:
            return float((text or "").strip())
        except Exception:
            return default

    def _display_name(self, p: Optional[Dict[str, Any]]) -> str:
        if not p:
            return ""
        return f"{p.get('Name','')} ({p.get('Team','')})"

    def _display_id(self, p: Optional[Dict[str, Any]], *, slot: str = "FLEX") -> str:
            """Return DraftKings player IDs for easy CSV upload.
    
            - Showdown CPT uses CptID when available.
            - All other slots use FlexID.
            """
            if not p:
                return ""
            slot_u = (slot or "").upper()
            if slot_u in ("CPT", "CAPTAIN"):
                return str(p.get("CptID") or p.get("FlexID") or "").strip()
            return str(p.get("FlexID") or "").strip()


    def _load_step(self, progress: Optional[QtWidgets.QProgressDialog], pct: int, msg: str) -> None:
        """Update visible CSV-load progress while long auto steps run on the UI thread."""
        try:
            self.status.showMessage(msg)
            if hasattr(self, "_load_progress"):
                self._load_progress.setVisible(True)
                self._load_progress.setMaximum(100)
                self._load_progress.setValue(max(0, min(100, int(pct))))
                self._load_progress.setFormat(f"{int(pct)}%")
            if hasattr(self, "_load_eta"):
                self._load_eta.setVisible(True)
                self._load_eta.setText(msg)
            if progress is not None:
                progress.setValue(max(0, min(100, int(pct))))
                progress.setLabelText(msg)
            QtWidgets.QApplication.processEvents()
        except Exception:
            pass

    def _finish_load_progress(self) -> None:
        try:
            if hasattr(self, "_load_progress"):
                self._load_progress.setVisible(False)
            if hasattr(self, "_load_eta"):
                self._load_eta.setVisible(False)
                self._load_eta.setText("")
            QtWidgets.QApplication.processEvents()
        except Exception:
            pass


    # ---------------- Actions ----------------

    def on_load_csv(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select Player CSV", "", "CSV Files (*.csv)")
        if not path:
            return

        progress = QtWidgets.QProgressDialog("Starting CSV load…", None, 0, 100, self)
        progress.setWindowTitle("Loading Player CSV")
        progress.setWindowModality(QtCore.Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(True)
        progress.setAutoReset(True)
        progress.setValue(0)

        try:
            logger.info("Loading CSV: %s", path)
            self._load_step(progress, 5, "Reading DraftKings CSV…")
            self.players = read_players_csv(path)
            parsed_opponents = sum(1 for p in self.players if str(p.get("Opponent") or "").strip())
            parsed_games = len({str(p.get("GameKey") or "").strip() for p in self.players if str(p.get("GameKey") or "").strip()})
            logger.info(
                "Parsed DK game context: opponents=%d/%d | games=%d",
                parsed_opponents, len(self.players), parsed_games,
            )

            self._load_step(progress, 18, "Initializing player fields…")
            for p in self.players:
                p.setdefault("LockFlex", False)
                p.setdefault("FadeFlex", False)
                p.setdefault("LockCpt", False)
                p.setdefault("FadeCpt", False)
                p.setdefault("MaxCptPct", None)
                p.setdefault("MaxPct", None)
                p.setdefault("MaxFlexPct", None)
                p.setdefault("BaseProjection", float(p.get("FlexProjection", 0.0) or 0.0))
                p.setdefault("BattingOrder", 0)
                p.setdefault("Bats", "")
                p.setdefault("ConfirmedLineup", False)
                p.setdefault("LineupStatus", "")

            self._load_step(progress, 28, "Detecting sport and contest type…")
            detected_sport = self._detect_sport_from_players()
            try:
                if hasattr(self, "combo_sport") and self.combo_sport.currentText().strip().upper() != detected_sport:
                    self.combo_sport.setCurrentText(detected_sport)
                    logger.info("Auto-detected sport: %s", detected_sport)
            except Exception:
                detected_sport = self._current_sport()

            self._load_step(progress, 40, f"Refreshing {detected_sport} injury/status data…")
            if detected_sport == "NFL":
                self._load_step(progress, 46, "Refreshing NFL role, usage, matchup, and weather context...")
                try:
                    ctx = apply_auto_nfl_context(self.players)
                    logger.info("NFL auto context applied: %s", ctx)
                except Exception:
                    # Loading the CSV remains useful even if an unexpected
                    # external payload bypasses the enrichment fallbacks.
                    logger.exception("NFL auto context failed; using neutral context")
            elif self._should_run_injury_enrichment(detected_sport):
                enrich_players_with_injuries(self.players, sport=detected_sport)
            else:
                self._clear_injury_fields()
                logger.info("Skipping injury enrichment for unsupported sport: %s", detected_sport)

            if detected_sport == "MLB":
                self._load_step(progress, 58, "Applying MLB ballpark factors and batting orders…")
                try:
                    ctx = apply_auto_mlb_context(self.players)
                    logger.info("MLB auto context applied: %s", ctx)
                except Exception:
                    logger.exception("MLB auto context failed")

            self._load_step(progress, 72, "Preparing tables and ownership…")
            detected_mode = self._detect_contest_mode_from_players()
            try:
                if hasattr(self, "tabs_lineups"):
                    self.tabs_lineups.setCurrentIndex(0 if detected_mode == "showdown" else 1)
            except Exception:
                pass

            faded = self._auto_fade_out_players()
            self.recalc_ownership_quick()
            try:
                self._refresh_best_stacks_table()
            except Exception:
                pass

            self._load_step(progress, 88, "Starting ownership simulation…")
            try:
                if detected_mode == "showdown":
                    cap = self._safe_float(self.edit_sd_cap.text(), 50000.0)
                else:
                    cap = self._safe_float(self.edit_cl_cap.text(), 50000.0)
            except Exception:
                cap = 50000.0

            self._start_ownership_sim(num_sims=500, mode=detected_mode, cap=cap)

            self._load_step(progress, 100, "CSV load complete.")
            extra = f" | auto-faded {faded}" if faded else ""
            self.status.showMessage(
                f"Loaded {len(self.players)} players ({detected_mode}, {self._current_sport()}) from {os.path.basename(path)}{extra}",
                6000
            )
        except Exception as e:
            logger.exception("Failed to load CSV")
            traceback.print_exc()
            QtWidgets.QMessageBox.critical(self, "Load Error", str(e))
        finally:
            self._finish_load_progress()

    def on_refresh_injuries(self) -> None:
        if not self.players:
            self.status.showMessage("Load a CSV first.", 3000)
            return

        sport = self._current_sport()
        if not self._should_run_injury_enrichment(sport):
            self._clear_injury_fields()
            self._refresh_players_table()
            self.status.showMessage(f"Injury refresh skipped for {sport}; current injury source is NFL-only.", 5000)
            logger.info("Manual injury refresh skipped for %s slate; NFL-only injury source.", sport)
            return

        try:
            if sport == "NFL":
                ctx = apply_auto_nfl_context(self.players)
                logger.info("Manual NFL context refresh applied: %s", ctx)
            else:
                enrich_players_with_injuries(self.players, sport=sport)
            faded = self._auto_fade_out_players()
            self.recalc_ownership_quick()

            # Small sim to keep Own% aligned after injury refresh
            mode = self._contest_mode()
            try:
                cap = self._safe_float(self.edit_sd_cap.text(), 50000.0) if mode == "showdown" else self._safe_float(self.edit_cl_cap.text(), 50000.0)
            except Exception:
                cap = 50000.0
            self._start_ownership_sim(num_sims=500, mode=mode, cap=cap)

            msg = "NFL context refreshed." if sport == "NFL" else "Injuries refreshed."
            msg += f" Auto-faded {faded} OUT." if faded else ""
            self.status.showMessage(msg, 4000)
        except Exception as e:
            logger.exception("Injury refresh failed")
            QtWidgets.QMessageBox.warning(self, "Injury API Error", str(e))

    def _should_run_injury_enrichment(self, sport: Optional[str] = None) -> bool:
        """Return True only for sports supported by the current injury source."""
        return (sport or self._current_sport() or "NFL").strip().upper() in ("NFL", "MLB")

    def _clear_injury_fields(self) -> None:
        """Clear NFL-only injury metadata on non-NFL slates."""
        for p in self.players or []:
            p["InjuryStatus"] = ""
            p["InjuryBodyPart"] = ""
            p["InjuryStartDate"] = ""
            p["AutoFadeInjury"] = False


    def _detect_contest_mode_from_players(self) -> str:
        """Detect contest mode from loaded player pool.

        Heuristic:
          - If any player has a non-empty CptID/CptNamePlusID, treat as Showdown.
          - Otherwise treat as Classic.
        """
        for p in self.players or []:
            if str(p.get("CptID") or "").strip() or str(p.get("CptNamePlusID") or "").strip():
                return "showdown"
        return "classic"

    def _contest_mode(self) -> str:
        # Determine current mode from the bottom tabs (0=Showdown, 1=Classic)
        try:
            if hasattr(self, "tabs_lineups") and self.tabs_lineups.currentIndex() == 0:
                return "showdown"
        except Exception:
            pass
        return "classic"

    def _auto_fade_out_players(self) -> int:
        """Auto-fade players projected OUT after injury enrichment.

        We only *set* fades; we never remove fades the user set manually.
        If a player is explicitly locked, we do not auto-fade them.
        """
        if not self.players:
            return 0
        out_tokens = ("OUT", "IR", "INACTIVE", "PUP", "SUSP", "DOUBTFUL")
        faded = 0
        for p in self.players:
            status = str(p.get("InjuryStatus") or "").strip().upper()
            if not status:
                continue
            is_out = any(t in status for t in out_tokens) or status.startswith("O")
            if is_out:
                if not bool(p.get("LockFlex")) and not bool(p.get("LockCpt")):
                    if not bool(p.get("FadeFlex")):
                        p["FadeFlex"] = True
                        faded += 1
                    if not bool(p.get("FadeCpt")):
                        p["FadeCpt"] = True
        return faded

    def _estimate_ownership_quick(self, *, mode: str) -> Dict[str, Dict[str, float]]:
        """Fast ownership estimate used immediately after CSV load.

        This is a heuristic (not a Monte Carlo). The full sim, when run, overwrites these values.
        Returns:
            {"total": {key:pct}, "cpt": {key:pct}, "flex": {key:pct}}
        """
        players = [p for p in (self.players or []) if float(p.get("FlexSalary", 0) or 0) > 0]
        if not players:
            return {"total": {}, "cpt": {}, "flex": {}}

        # Base weights: projection + value nudge
        scores: List[float] = []
        keys: List[str] = []
        for p in players:
            sal = float(p.get("FlexSalary", 0.0) or 0.0)
            proj = float(p.get("FlexProjection", 0.0) or 0.0)
            val = (proj / (sal / 1000.0)) if sal > 0 else 0.0
            s = proj + 0.35 * val
            s = max(0.0, s)
            scores.append(s + 1e-6)  # keep >0
            keys.append(_pkey(p))

        total_sum = float(sum(scores) or 1.0)
        total_pct = {k: (w / total_sum) * 100.0 for k, w in zip(keys, scores)}

        if mode.strip().lower() != "showdown":
            # Classic doesn't use CPT/FLEX split; keep those blank.
            return {"total": total_pct, "cpt": {}, "flex": total_pct}

        # For showdown: CPT ownership is typically more concentrated than FLEX.
        # We sharpen the same base weights with a power transform for CPT.
        pow_cpt = 1.35
        cpt_raw = [w ** pow_cpt for w in scores]
        cpt_sum = float(sum(cpt_raw) or 1.0)
        cpt_pct = {k: (w / cpt_sum) * 100.0 for k, w in zip(keys, cpt_raw)}

        # FLEX ownership tends to be flatter; we slightly soften (sqrt-ish).
        pow_flex = 0.85
        flex_raw = [w ** pow_flex for w in scores]
        flex_sum = float(sum(flex_raw) or 1.0)
        flex_pct = {k: (w / flex_sum) * 100.0 for k, w in zip(keys, flex_raw)}

        return {"total": total_pct, "cpt": cpt_pct, "flex": flex_pct}

    def recalc_ownership_quick(self) -> None:
        if not self.players:
            return
        mode = self._contest_mode()
        own = self._estimate_ownership_quick(mode=mode)
        tot = (own or {}).get("total", {})
        cpt = (own or {}).get("cpt", {})
        flx = (own or {}).get("flex", {})
        for p in self.players:
            k = _pkey(p)
            p["ProjOwnPct"] = float(tot.get(k, 0.0) or 0.0)
            p["ProjCptOwnPct"] = float(cpt.get(k, 0.0) or 0.0)
            p["ProjFlexOwnPct"] = float(flx.get(k, 0.0) or 0.0)
        self._refresh_players_table()

    def _start_ownership_sim(self, *, num_sims: int, mode: str, cap: float, sport: Optional[str] = None) -> None:
        # UI: show progress bar + eta in status bar
        self._own_progress.setValue(0)
        self._own_progress.setMaximum(num_sims)
        self._own_progress.setVisible(True)
        self._own_eta.setText("Starting…")
        self._own_eta.setVisible(True)
        self.status.showMessage(f"Simulating ownership ({mode}, {num_sims:,} lineups)…")

        # Thread setup
        self._own_thread = QtCore.QThread(self)
        self._own_worker = OwnershipSimWorker(self.players, mode=mode, num_sims=num_sims, salary_cap=cap, template_sim=(mode=="showdown" and getattr(self, "chk_sd_template_sim", None) is not None and self.chk_sd_template_sim.isChecked()), sport=(sport or self._current_sport()))
        self._own_worker.moveToThread(self._own_thread)

        self._own_thread.started.connect(self._own_worker.run)
        self._own_worker.progress.connect(self._on_own_sim_progress)
        self._own_worker.finished.connect(self._on_own_sim_finished)
        self._own_worker.error.connect(self._on_own_sim_error)

        # Cleanup
        self._own_worker.finished.connect(self._own_thread.quit)
        self._own_worker.finished.connect(self._own_worker.deleteLater)
        self._own_thread.finished.connect(self._own_thread.deleteLater)

        self._own_worker.error.connect(self._own_thread.quit)
        self._own_worker.error.connect(self._own_worker.deleteLater)

        self._own_thread.start()

    def on_recalc_ownership_sim(self) -> None:
        if not self.players:
            self.status.showMessage("Load a CSV first.", 3000)
            return

        mode = self._contest_mode()
        try:
            if mode == "showdown":
                cap = self._safe_float(self.edit_sd_cap.text(), 50000.0)
            else:
                cap = self._safe_float(self.edit_cl_cap.text(), 50000.0)
        except Exception:
            cap = 50000.0

        num_sims = int(getattr(self, "spin_own_sims", None).value()) if hasattr(self, "spin_own_sims") else 5000
        self._start_ownership_sim(num_sims=num_sims, mode=mode, cap=cap, sport=self._current_sport())

    def _on_own_sim_progress(self, done: int, total: int, eta_str: str) -> None:
        self._own_progress.setMaximum(total)
        self._own_progress.setValue(done)
        self._own_eta.setText(f"{done:,}/{total:,} • {eta_str}")

    def _on_own_sim_finished(self, own_map: Dict[str, float]) -> None:
        tot = (own_map or {}).get("total", {})
        cpt = (own_map or {}).get("cpt", {})
        flx = (own_map or {}).get("flex", {})
        for p in self.players:
            k = _pkey(p)
            p["ProjOwnPct"] = float(tot.get(k, 0.0) or 0.0)
            p["ProjCptOwnPct"] = float(cpt.get(k, 0.0) or 0.0)
            p["ProjFlexOwnPct"] = float(flx.get(k, 0.0) or 0.0)
        self._own_progress.setVisible(False)
        self._own_eta.setVisible(False)
        self._refresh_players_table()
        self.status.showMessage("Ownership simulation complete.", 4000)

    def _on_own_sim_error(self, msg: str) -> None:
        self._own_progress.setVisible(False)
        self._own_eta.setVisible(False)
        self.status.showMessage("Ownership simulation failed.", 5000)
        QtWidgets.QMessageBox.warning(self, "Ownership Simulation Error", msg)

    def _refresh_players_table(self) -> None:
        # Prevent the view from re-sorting mid-refresh (keeps display stable).
        was_sorting = self.tbl_players.isSortingEnabled()
        if was_sorting:
            sort_col = self.tbl_players.horizontalHeader().sortIndicatorSection()
            sort_order = self.tbl_players.horizontalHeader().sortIndicatorOrder()
            self.tbl_players.setSortingEnabled(False)
        else:
            sort_col = -1
            sort_order = QtCore.Qt.AscendingOrder

        self.tbl_players.setRowCount(0)
        self.tbl_players.setRowCount(len(self.players))

        for r, p in enumerate(self.players):
            name = str(p.get("Name", ""))
            team = str(p.get("Team", ""))
            pos = str(p.get("Position", ""))
            inj = str(p.get("InjuryStatus", "") or "")
            sal = int(float(p.get("FlexSalary", 0.0) or 0.0))
            proj = float(p.get("FlexProjection", 0.0) or 0.0)
            tag_txt = self._tags_to_text(p)

            name_item = QtWidgets.QTableWidgetItem(name)
            name_item.setData(QtCore.Qt.UserRole, _pkey(p))
            self.tbl_players.setItem(r, 0, name_item)
            self.tbl_players.setItem(r, 1, QtWidgets.QTableWidgetItem(team))
            self.tbl_players.setItem(r, 2, QtWidgets.QTableWidgetItem(pos))
            self.tbl_players.setItem(r, 3, QtWidgets.QTableWidgetItem(inj))
            sal_item = SortKeyItem(f"{sal:,}")
            sal_item.setData(QtCore.Qt.UserRole, float(sal))
            self.tbl_players.setItem(r, 4, sal_item)

            base_proj = float(p.get("BaseProjection", proj) or 0.0)
            base_item = SortKeyItem(f"{base_proj:.2f}")
            base_item.setData(QtCore.Qt.UserRole, float(base_proj))
            self.tbl_players.setItem(r, 5, base_item)

            proj_item = SortKeyItem(f"{proj:.2f}")
            proj_item.setData(QtCore.Qt.UserRole, float(proj))
            self.tbl_players.setItem(r, 6, proj_item)

            is_nfl = self._current_sport() == "NFL"
            is_mlb = self._current_sport() == "MLB"
            if is_nfl:
                nfl_adj = float(p.get("NFLAdjScore", 0.0) or 0.0)
                adj_item = SortKeyItem(f"{nfl_adj:+.2f}")
                adj_item.setData(QtCore.Qt.UserRole, nfl_adj)
                self.tbl_players.setItem(r, 7, adj_item)

                for col, key in [(8, "NFLUsageScore"), (9, "NFLMatchupScore")]:
                    val = float(p.get(key, 0.0) or 0.0)
                    it = SortKeyItem(f"{val:+.1f}")
                    it.setData(QtCore.Qt.UserRole, val)
                    self.tbl_players.setItem(r, col, it)

                role_item = SortKeyItem(str(p.get("NFLRole", "") or ""))
                role_item.setData(QtCore.Qt.UserRole, float(p.get("NFLRoleScore", 0.0) or 0.0))
                self.tbl_players.setItem(r, 10, role_item)
                for col, key in [(11, "NFLWeatherScore"), (12, "NFLVegas")]:
                    val = float(p.get(key, 0.0) or 0.0)
                    it = SortKeyItem(f"{val:+.1f}")
                    it.setData(QtCore.Qt.UserRole, val)
                    self.tbl_players.setItem(r, col, it)
            elif is_mlb:
                mlb_adj = float(p.get("MLBAdjScore", 0.0) or 0.0)
                adj_item = SortKeyItem(f"{mlb_adj:+.2f}")
                adj_item.setData(QtCore.Qt.UserRole, float(mlb_adj))
                self.tbl_players.setItem(r, 7, adj_item)
                for col, key in [(8, "MLBRecentForm"), (9, "MLBMatchup"), (10, "MLBBallpark"), (11, "MLBWeather"), (12, "MLBVegas")]:
                    val = float(p.get(key, 0.0) or 0.0)
                    it = SortKeyItem(f"{val:+.1f}")
                    it.setData(QtCore.Qt.UserRole, val)
                    self.tbl_players.setItem(r, col, it)
            else:
                for col in range(7, 13):
                    self.tbl_players.setItem(r, col, SortKeyItem(""))

            team_adj = float(p.get("TeamAdjPct", 0.0) or 0.0)
            team_adj_item = SortKeyItem(f"{team_adj:+.0f}%" if abs(team_adj) > 1e-9 else "")
            team_adj_item.setData(QtCore.Qt.UserRole, team_adj)
            self.tbl_players.setItem(r, 13, team_adj_item)

            self.tbl_players.setItem(r, 14, QtWidgets.QTableWidgetItem(tag_txt))

            own_tot = float(p.get("ProjOwnPct", 0.0) or 0.0)
            it_tot = SortKeyItem(f"{own_tot:.1f}%")
            it_tot.setData(QtCore.Qt.UserRole, float(own_tot))
            self.tbl_players.setItem(r, 15, it_tot)

            # Ownership / exposure caps (display only)
            max_cpt = p.get("MaxCptPct", None)
            max_pct = p.get("MaxPct", None)

            mc_item = SortKeyItem("" if max_cpt in (None, "", 0) else f"{float(max_cpt):.0f}%")
            mc_item.setData(QtCore.Qt.UserRole, float(max_cpt) if max_cpt not in (None, "", 0) else -1.0)
            self.tbl_players.setItem(r, 16, mc_item)

            mp_item = SortKeyItem("" if max_pct in (None, "", 0) else f"{float(max_pct):.0f}%")
            mp_item.setData(QtCore.Qt.UserRole, float(max_pct) if max_pct not in (None, "", 0) else -1.0)
            self.tbl_players.setItem(r, 17, mp_item)

            order_val = int(p.get("BattingOrder", 0) or 0)
            is_pitcher = bool(set(str(p.get("Position", "") or "").upper().replace("/", ",").split(",")) & {"P", "SP", "RP"})
            order_text = "P" if (is_mlb and is_pitcher and order_val <= 0) else ("" if order_val <= 0 else str(order_val))
            order_item = SortKeyItem(order_text)
            order_item.setData(QtCore.Qt.UserRole, float(order_val if order_val > 0 else 99))
            self.tbl_players.setItem(r, 18, order_item)

            self.tbl_players.setItem(r, 19, QtWidgets.QTableWidgetItem(str(p.get("Bats", "") or "")))
            status = str(p.get("LineupStatus", "") or "").strip().lower()
            conf_text = "Y" if bool(p.get("ConfirmedLineup")) else ("Proj" if status == "projected" else "")
            conf_item = QtWidgets.QTableWidgetItem(conf_text)
            self.tbl_players.setItem(r, 20, conf_item)

            # Visual-only highlighting
            self._set_player_row_style(r, p)

        # Keep the same initial look (auto-fit), but allow manual resizing after.
        self.tbl_players.resizeColumnsToContents()

        # Restore sorting state.
        if was_sorting:
            self.tbl_players.setSortingEnabled(True)
            if sort_col >= 0:
                self.tbl_players.sortItems(sort_col, sort_order)

    def _start_lineup_build(self, *, kind: str, sport: str, num: int, cap: float) -> None:
        """Run a lineup build in a worker thread and show status-bar progress."""
        # A previous QThread may already have been deleted by Qt even though the
        # Python attribute still points at the wrapper. Guard against that so a
        # second build can be started safely after the first finishes.
        running = False
        try:
            thread = getattr(self, "_build_thread", None)
            running = bool(thread is not None and thread.isRunning())
        except RuntimeError:
            # Qt object was deleted; clear stale wrappers and allow new build.
            self._build_thread = None
            self._build_worker = None
            running = False

        if running:
            self.status.showMessage("A lineup build is already running.", 3000)
            return

        own_mode = getattr(self, "combo_build_own_mode", None)
        own_weight = getattr(self, "spin_build_own_weight", None)
        build_style_widget = getattr(self, "combo_build_style", None)
        mlb_stack_widget = getattr(self, "combo_mlb_stack_pref", None)
        salary_strategy_widget = getattr(self, "combo_salary_strategy", None)
        mode = own_mode.currentText() if own_mode is not None else "Balanced"
        lam = float(own_weight.value()) if own_weight is not None else 0.0
        build_style = build_style_widget.currentText() if build_style_widget is not None else "Strategic"
        mlb_stack_pref = mlb_stack_widget.currentText() if mlb_stack_widget is not None else "Any Strategic"
        salary_strategy = salary_strategy_widget.currentText() if salary_strategy_widget is not None else "Near Cap"

        label_sport = sport if kind != "showdown" else "Showdown"
        self._build_progress.setRange(0, num if kind == "showdown" else 0)
        self._build_progress.setValue(0)
        self._build_progress.setVisible(True)
        self._build_eta.setText(f"Building {label_sport} lineups…")
        self._build_eta.setVisible(True)
        self._build_cancel.setEnabled(True)
        self._build_cancel.setVisible(kind == "showdown")
        self.status.showMessage(f"Building {label_sport} lineups ({num:,}) • {build_style} • {salary_strategy}…")

        self._build_thread = QtCore.QThread(self)
        self._build_worker = LineupBuildWorker(
            list(self.players),
            kind=kind,
            sport=sport,
            num_lineups=num,
            salary_cap=cap,
            own_mode=mode,
            own_weight=lam,
            build_style=build_style,
            mlb_stack_pref=mlb_stack_pref,
            salary_strategy=salary_strategy,
        )
        self._build_worker.moveToThread(self._build_thread)

        self._build_thread.started.connect(self._build_worker.run)
        self._build_worker.progress.connect(self._on_lineup_build_progress)
        self._build_worker.finished.connect(self._on_lineup_build_finished)
        self._build_worker.error.connect(self._on_lineup_build_error)

        self._build_worker.finished.connect(self._build_thread.quit)
        self._build_worker.finished.connect(self._build_worker.deleteLater)
        self._build_thread.finished.connect(self._on_lineup_thread_finished)
        self._build_thread.finished.connect(self._build_thread.deleteLater)

        self._build_worker.error.connect(self._build_thread.quit)
        self._build_worker.error.connect(self._build_worker.deleteLater)

        self._build_thread.start()

    def _on_lineup_thread_finished(self) -> None:
        """Clear stale build-thread wrappers after Qt finishes/deletes them."""
        self._build_thread = None
        self._build_worker = None

    def _on_lineup_build_progress(self, done: int, total: int, text: str) -> None:
        if total and total > 0:
            self._build_progress.setRange(0, total)
            self._build_progress.setValue(done)
            self._build_eta.setText(f"{done:,}/{total:,} • {text}")
        else:
            self._build_progress.setRange(0, 0)
            self._build_eta.setText(text or "Building…")

    def _cancel_lineup_build(self) -> None:
        worker = getattr(self, "_build_worker", None)
        if worker is None:
            return
        worker.request_cancel()
        self._build_cancel.setEnabled(False)
        self._build_eta.setText("Cancelling after the current candidate…")
        self.status.showMessage("Cancelling Showdown lineup build…")

    def _finish_lineup_build_ui(self) -> None:
        self._build_progress.setVisible(False)
        self._build_eta.setVisible(False)
        self._build_cancel.setVisible(False)

    def _populate_showdown_lineups(self, lineups: List[Dict[str, Any]]) -> None:
        self.last_showdown = lineups or []
        self.tbl_sd.setRowCount(0)
        self.tbl_sd.setRowCount(len(self.last_showdown))

        total = max(1, len(self.last_showdown))
        self._build_progress.setRange(0, total)
        self._build_progress.setVisible(True)
        self._build_eta.setVisible(True)

        for i, lu in enumerate(self.last_showdown):
            cpt = lu.get("Captain")
            flex = sorted(lu.get("Flex", []), key=lambda x: x.get("FlexSalary", 0.0), reverse=True)

            chk = QtWidgets.QCheckBox()
            chk.stateChanged.connect(lambda state, row=i: self._sd_checkbox_changed(row, state))
            self.tbl_sd.setCellWidget(i, 0, chk)

            self.tbl_sd.setItem(i, 1, QtWidgets.QTableWidgetItem(self._display_name(cpt)))
            for j in range(5):
                self.tbl_sd.setItem(
                    i, 2 + j,
                    QtWidgets.QTableWidgetItem(self._display_name(flex[j]) if j < len(flex) else "")
                )
            self._build_progress.setValue(i + 1)
            self._build_eta.setText(f"Rendering {i + 1:,}/{total:,}")
            QtWidgets.QApplication.processEvents()

    def _populate_classic_lineups(self, lineups: List[List[Dict[str, Any]]], sport: str) -> None:
        # Defensive UI guard: never display/save lineups with unfilled slots.
        # This also protects exports if tight Max% caps produce edge cases.
        valid_lineups: List[List[Dict[str, Any]]] = []
        dropped = 0
        for lu in (lineups or []):
            assigned = lineup_slots_for_sport(lu, sport)
            if assigned and all(player is not None for _, player in assigned):
                valid_lineups.append(lu)
            else:
                dropped += 1
        if dropped:
            logger.warning("Dropped %d incomplete %s lineup(s) before display/export.", dropped, sport)
            self.status.showMessage(f"Dropped {dropped} incomplete {sport} lineup(s).", 5000)

        # Order generated lineup display by UI-only grade, descending.
        # This changes the on-screen order and Save checkbox row mapping, but saved/exported
        # DK rows remain player IDs only and do not include grade metadata.
        try:
            cap_for_grade = self._safe_float(self.edit_cl_cap.text(), 50000.0)
            valid_lineups.sort(
                key=lambda lu: float(lineup_grade_for_sport(lu, sport, cap_for_grade).get("score", 0.0) or 0.0),
                reverse=True,
            )
        except Exception:
            pass

        self.last_classic = valid_lineups
        slots = get_roster_slots_for_sport(sport)
        headers = ["Save"] + slots + ["TotalSal", "Grade"]
        self.tbl_cl.setColumnCount(len(headers))
        self.tbl_cl.setHorizontalHeaderLabels(headers)
        self.tbl_cl.setRowCount(0)
        self.tbl_cl.setRowCount(len(self.last_classic))

        total = max(1, len(self.last_classic))
        self._build_progress.setRange(0, total)
        self._build_progress.setVisible(True)
        self._build_eta.setVisible(True)

        for i, lu in enumerate(self.last_classic):
            chk = QtWidgets.QCheckBox()
            chk.stateChanged.connect(lambda state, row=i: self._cl_checkbox_changed(row, state))
            self.tbl_cl.setCellWidget(i, 0, chk)

            cells = self._classic_display_cells(lu, sport)
            for col, txt in enumerate(cells, start=1):
                self.tbl_cl.setItem(i, col, QtWidgets.QTableWidgetItem(txt))

            total_sal = int(sum(float(p.get("FlexSalary", 0.0) or 0.0) for p in lu))
            self.tbl_cl.setItem(i, len(headers)-2, QtWidgets.QTableWidgetItem(f"{total_sal:,}"))

            try:
                grade_info = lineup_grade_for_sport(lu, sport, self._safe_float(self.edit_cl_cap.text(), 50000.0))
                grade_txt = f"{grade_info.get('grade', '')} ({float(grade_info.get('score', 0.0)):.0f})"
                grade_item = QtWidgets.QTableWidgetItem(grade_txt)
                detail = (
                    f"Salary Used: ${float(grade_info.get('salary_used', 0.0)):,.0f}\n"
                    f"Salary Left: ${float(grade_info.get('salary_left', 0.0)):,.0f}\n"
                    f"Stack/Shape: {grade_info.get('stack_shape', '')}\n"
                    f"Warnings: {grade_info.get('warnings', '') or 'None'}\n"
                    "UI-only grade; not included in DK export/saved ID table."
                )
                grade_item.setToolTip(detail)
                self.tbl_cl.setItem(i, len(headers)-1, grade_item)
            except Exception:
                self.tbl_cl.setItem(i, len(headers)-1, QtWidgets.QTableWidgetItem(""))

            self._build_progress.setValue(i + 1)
            self._build_eta.setText(f"Rendering {i + 1:,}/{total:,}")
            QtWidgets.QApplication.processEvents()

    def _lineup_quality_summary(self, lineups: List[Any], sport: str, kind: str) -> str:
        try:
            from collections import Counter
            if not lineups:
                return "No lineups generated."
            if kind == "showdown":
                team_splits = Counter()
                for lu in lineups:
                    teams = [str((lu.get("Captain") or {}).get("Team", ""))] + [str(p.get("Team", "")) for p in lu.get("Flex", [])]
                    counts = sorted([c for _, c in Counter(t for t in teams if t).items()], reverse=True)
                    if counts:
                        team_splits["-".join(map(str, counts))] += 1
                common = ", ".join(f"{k}: {v}" for k, v in team_splits.most_common(3))
                return f"Quality: {len(lineups)} Showdown lineups | common splits {common or 'n/a'}."
            sport_u = (sport or "NFL").upper()
            if sport_u == "MLB":
                stacks = Counter()
                salaries = []
                for lu in lineups:
                    hitters = [p for p in lu if not (set(str(p.get("Position", "")).upper().replace('/', ',').split(',')) & {'P','SP','RP'})]
                    counts = sorted([c for _, c in Counter(str(p.get("Team", "")) for p in hitters if p.get("Team")).items()], reverse=True)
                    stacks["-".join(map(str, counts[:3])) if counts else "none"] += 1
                    salaries.append(sum(float(p.get("FlexSalary", 0) or 0) for p in lu))
                common = ", ".join(f"{k}: {v}" for k, v in stacks.most_common(4))
                avg_left = 50000 - (sum(salaries)/len(salaries)) if salaries else 0
                grades = [lineup_grade_for_sport(lu, sport_u, 50000.0).get("grade", "") for lu in lineups]
                grade_counts = Counter(g for g in grades if g)
                grade_txt = ", ".join(f"{g}:{c}" for g, c in grade_counts.most_common())
                return f"Quality: MLB stacks {common}; avg salary left ${avg_left:,.0f}; grades {grade_txt or 'n/a'}."
            salaries = [sum(float(p.get("FlexSalary", 0) or 0) for p in lu) for lu in lineups if isinstance(lu, list)]
            avg_left = 50000 - (sum(salaries)/len(salaries)) if salaries else 0
            return f"Quality: {len(lineups)} {sport_u} lineups | avg salary left ${avg_left:,.0f}."
        except Exception as e:
            return f"Quality summary unavailable: {e}"

    def _on_lineup_build_finished(self, payload: Dict[str, Any]) -> None:
        try:
            self._build_cancel.setVisible(False)
            kind = str(payload.get("kind", "classic"))
            sport = str(payload.get("sport", self._current_sport())).upper()
            requested = int(payload.get("requested", 0) or 0)
            lineups = payload.get("lineups", []) or []
            cancelled = bool(payload.get("cancelled", False))

            if kind == "showdown":
                self._populate_showdown_lineups(lineups)
                built = len(self.last_showdown)
                result = f"Cancelled after {built} of {requested}" if cancelled else f"Built {built} of {requested}"
                self.status.showMessage(f"{result} showdown lineups. {self._lineup_quality_summary(self.last_showdown, sport, kind)}", 9000)
            else:
                self._populate_classic_lineups(lineups, sport)
                built = len(self.last_classic)
                self.status.showMessage(f"Built {built} of {requested} {sport} lineups. {self._lineup_quality_summary(self.last_classic, sport, kind)}", 9000)
        finally:
            self._finish_lineup_build_ui()

    def _on_lineup_build_error(self, msg: str) -> None:
        self._finish_lineup_build_ui()
        self.status.showMessage("Lineup build failed.", 5000)
        logger.error("Lineup build failed:\n%s", msg)
        QtWidgets.QMessageBox.critical(self, "Optimization Error", msg)

    def on_build_showdown(self) -> None:
        if not self.players:
            QtWidgets.QMessageBox.warning(self, "No Data", "Load a player CSV first.")
            return

        cap = self._safe_float(self.edit_sd_cap.text(), 50000.0)
        num = int(self.spin_sd.value())
        logger.info("Building showdown lineups (n=%d cap=%.0f)", num, cap)
        self._start_lineup_build(kind="showdown", sport=self._current_sport(), num=num, cap=cap)


    def on_load_mlb_factors(self) -> None:
        if not self.players:
            self.status.showMessage("Load a DK player CSV first.", 3000)
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select MLB Factors CSV",
            "",
            "CSV Files (*.csv)",
        )
        if not path:
            return
        try:
            result = apply_mlb_factors(self.players, path)
            self.recalc_ownership_quick()
            # For MLB, rerun sport-aware own sim if Classic/Sport tab is active.
            try:
                mode = self._contest_mode()
                cap = self._safe_float(self.edit_cl_cap.text(), 50000.0)
                if self._current_sport() == "MLB" and mode != "showdown":
                    self._start_ownership_sim(num_sims=500, mode=mode, cap=cap, sport="MLB")
            except Exception:
                pass
            self._refresh_players_table()
            self._refresh_best_stacks_table()
            self.status.showMessage(
                f"Applied MLB factors to {result.get('matched', 0)}/{result.get('total', 0)} players.",
                7000,
            )
        except Exception as e:
            logger.exception("MLB factor load failed")
            QtWidgets.QMessageBox.critical(self, "MLB Factors Error", str(e))

    def on_clear_mlb_factors(self) -> None:
        if not self.players:
            return
        clear_mlb_factors(self.players)
        self.recalc_ownership_quick()
        self._refresh_players_table()
        self._refresh_best_stacks_table()
        self.status.showMessage("Cleared MLB factor adjustments and restored base projections.", 5000)

    def on_load_batting_order(self) -> None:
        if not self.players:
            self.status.showMessage("Load a DK player CSV first.", 3000)
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select MLB Batting Order CSV",
            "",
            "CSV Files (*.csv)",
        )
        if not path:
            return
        try:
            result = apply_batting_order(self.players, path)
            # Batting order affects strategy and best-stack reporting more than base projection.
            self._refresh_players_table()
            self._refresh_best_stacks_table()
            self.status.showMessage(
                f"Loaded batting order for {result.get('matched', 0)}/{result.get('total', 0)} players.",
                7000,
            )
        except Exception as e:
            logger.exception("Batting order load failed")
            QtWidgets.QMessageBox.critical(self, "Batting Order Error", str(e))

    def on_clear_batting_order(self) -> None:
        if not self.players:
            return
        clear_batting_order(self.players)
        self._refresh_players_table()
        self._refresh_best_stacks_table()
        self.status.showMessage("Cleared batting order / handedness fields.", 5000)

    def _refresh_best_stacks_table(self) -> None:
        if not hasattr(self, "tbl_best_stacks"):
            return
        rows = build_best_stacks(self.players or []) if self.players else []
        self.tbl_best_stacks.setSortingEnabled(False)
        self.tbl_best_stacks.setRowCount(0)
        self.tbl_best_stacks.setRowCount(len(rows))
        headers = ["Rank", "Team", "Score", "Top5Proj", "Top8Proj", "Form", "Matchup", "Park", "Wx", "Vegas", "TeamAdj", "Confirmed", "TopOrder", "Top Hitters"]
        self.tbl_best_stacks.setColumnCount(len(headers))
        self.tbl_best_stacks.setHorizontalHeaderLabels(headers)

        def num_item(text: str, val: float) -> QtWidgets.QTableWidgetItem:
            it = SortKeyItem(text)
            it.setData(QtCore.Qt.UserRole, float(val))
            return it

        for r, row in enumerate(rows):
            self.tbl_best_stacks.setItem(r, 0, num_item(str(r + 1), r + 1))
            self.tbl_best_stacks.setItem(r, 1, QtWidgets.QTableWidgetItem(str(row.get("Team", ""))))
            self.tbl_best_stacks.setItem(r, 2, num_item(f"{float(row.get('Score', 0.0)):.1f}", float(row.get("Score", 0.0))))
            self.tbl_best_stacks.setItem(r, 3, num_item(f"{float(row.get('ProjTop5', 0.0)):.1f}", float(row.get("ProjTop5", 0.0))))
            self.tbl_best_stacks.setItem(r, 4, num_item(f"{float(row.get('ProjTop8', 0.0)):.1f}", float(row.get("ProjTop8", 0.0))))
            self.tbl_best_stacks.setItem(r, 5, num_item(f"{float(row.get('Form', 0.0)):.1f}", float(row.get("Form", 0.0))))
            self.tbl_best_stacks.setItem(r, 6, num_item(f"{float(row.get('Matchup', 0.0)):.1f}", float(row.get("Matchup", 0.0))))
            self.tbl_best_stacks.setItem(r, 7, num_item(f"{float(row.get('Park', 0.0)):.1f}", float(row.get("Park", 0.0))))
            self.tbl_best_stacks.setItem(r, 8, num_item(f"{float(row.get('Weather', 0.0)):.1f}", float(row.get("Weather", 0.0))))
            self.tbl_best_stacks.setItem(r, 9, num_item(f"{float(row.get('Vegas', 0.0)):.1f}", float(row.get("Vegas", 0.0))))
            self.tbl_best_stacks.setItem(r, 10, num_item(f"{float(row.get('TeamAdj', 0.0)):.0f}%", float(row.get("TeamAdj", 0.0))))
            self.tbl_best_stacks.setItem(r, 11, num_item(str(int(row.get("Confirmed", 0) or 0)), float(row.get("Confirmed", 0) or 0)))
            self.tbl_best_stacks.setItem(r, 12, num_item(str(int(row.get("TopOrder", 0) or 0)), float(row.get("TopOrder", 0) or 0)))
            hit_item = QtWidgets.QTableWidgetItem(str(row.get("TopHitters", "")))
            hit_item.setToolTip(str(row.get("TopHitters", "")))
            self.tbl_best_stacks.setItem(r, 13, hit_item)
        self.tbl_best_stacks.setSortingEnabled(True)
        self.tbl_best_stacks.sortItems(2, QtCore.Qt.DescendingOrder)
        self.tbl_best_stacks.resizeColumnsToContents()

    def _detect_sport_from_players(self) -> str:
        """Best-effort sport detection from DK position labels.

        This is intentionally position-based instead of filename-based:
        - MLB has P/SP/RP plus infield/OF positions.
        - NBA/WNBA has PG/SG/SF/PF/C/G/F and no NFL/MLB-only positions.
        - NFL has QB/RB/WR/TE/DST/K.
        WNBA shares NBA position labels, so we default ambiguous basketball files to NBA;
        user can still switch the dropdown to WNBA.
        """
        tokens = set()
        for p in self.players or []:
            raw = str(p.get("Position", "") or "").upper()
            raw = raw.replace("/", ",").replace(";", ",")
            for part in raw.split(","):
                part = part.strip()
                if part:
                    tokens.add(part)

        mlb_tokens = {"P", "SP", "RP", "C", "1B", "2B", "3B", "SS", "OF", "LF", "CF", "RF"}
        nfl_tokens = {"QB", "RB", "WR", "TE", "DST", "K"}
        nba_tokens = {"PG", "SG", "SF", "PF", "C", "G", "F", "UTIL"}

        # MLB is easiest to distinguish because of baseball-only positions.
        if tokens & {"1B", "2B", "3B", "SS", "OF", "LF", "CF", "RF", "SP", "RP"}:
            return "MLB"

        if tokens & {"QB", "RB", "WR", "TE", "DST", "K"}:
            return "NFL"

        if tokens & {"PG", "SG", "SF", "PF", "G", "F"}:
            return "NBA"

        return "NFL"

    def _current_sport(self) -> str:
        try:
            return self.combo_sport.currentText().strip().upper()
        except Exception:
            return "NFL"

    def _on_sport_changed(self, sport: str) -> None:
        sport_u = (sport or "NFL").strip().upper()
        slots = get_roster_slots_for_sport(sport_u)
        headers = ["Save"] + slots + ["TotalSal", "Grade"]
        try:
            context_headers = {
                "NFL": ["NFL±", "Usage", "Matchup", "Role", "Wx", "Vegas"],
                "MLB": ["MLB±", "Form", "Matchup", "Park", "Wx", "Vegas"],
            }.get(sport_u, ["Adj±", "Context", "Matchup", "Role", "Wx", "Vegas"])
            player_headers = ["Name", "Team", "Pos", "Injury", "Salary", "BaseProj", "AdjProj"] + context_headers + ["TeamAdj", "Tags", "Own% Tot", "MaxCPT%", "Max%", "Order", "Bats", "Conf"]
            self.tbl_players.setHorizontalHeaderLabels(player_headers)
            self.tbl_cl.setColumnCount(len(headers))
            self.tbl_cl.setHorizontalHeaderLabels(headers)
            self.tbl_saved_cl.setColumnCount(len(slots))
            self.tbl_saved_cl.setHorizontalHeaderLabels(slots)
            self.saved_classic.clear()
            self.last_classic.clear()
            self.tbl_cl.setRowCount(0)
            self._refresh_saved_tables()
            if self.players:
                self._refresh_players_table()
            self.status.showMessage(f"Sport set to {sport_u}. Classic tab now uses: {', '.join(slots)}", 5000)
        except Exception:
            pass

    def _classic_display_cells(self, lu: List[Dict[str, Any]], sport: str) -> List[str]:
        return [self._display_name(p) for _, p in lineup_slots_for_sport(lu, sport)]

    def _classic_export_cells(self, lu: List[Dict[str, Any]], sport: str) -> List[str]:
        return [self._display_id(p, slot=slot) for slot, p in lineup_slots_for_sport(lu, sport)]

    def on_build_classic(self) -> None:
        if not self.players:
            QtWidgets.QMessageBox.warning(self, "No Data", "Load a player CSV first.")
            return

        cap = self._safe_float(self.edit_cl_cap.text(), 50000.0)
        num = int(self.spin_cl.value())
        sport = self._current_sport()
        logger.info("Building %s classic/sport lineups (n=%d cap=%.0f)", sport, num, cap)
        self._start_lineup_build(kind="classic", sport=sport, num=num, cap=cap)

    def on_results_learning(self) -> None:
        ResultsLearningDialog(self).exec_()

    def on_export_saved(self, kind: str) -> None:
        kind_l = str(kind or "classic").lower()
        sport = self._current_sport()
        if kind_l == "showdown":
            lineups: List[Any] = list(self.saved_showdown or [])
            headers = ["CPT", "FLEX", "FLEX", "FLEX", "FLEX", "FLEX"]
            rows = []
            for lineup in lineups:
                captain = lineup.get("Captain") or {}
                flex = list(lineup.get("Flex") or [])
                rows.append(
                    [self._display_id(captain, slot="CPT")]
                    + [self._display_id(player, slot="FLEX") for player in flex]
                )
            cap = self._safe_float(self.edit_sd_cap.text(), 50000.0)
        else:
            lineups = list(self.saved_classic or [])
            headers = get_roster_slots_for_sport(sport)
            rows = [self._classic_export_cells(lineup, sport) for lineup in lineups]
            cap = self._safe_float(self.edit_cl_cap.text(), 50000.0)

        if not lineups:
            QtWidgets.QMessageBox.information(
                self,
                "No Saved Lineups",
                "Save at least one lineup first, then export it for DraftKings and local learning.",
            )
            return

        expected = len(headers)
        incomplete = sum(1 for row in rows if len(row) != expected or any(not str(cell).strip() for cell in row))
        if incomplete:
            QtWidgets.QMessageBox.warning(
                self,
                "Incomplete Lineups",
                f"{incomplete} saved lineup(s) are missing a DraftKings player ID. Reload the salary CSV and rebuild them.",
            )
            return

        suggested = f"DK_{sport}_{kind_l}_lineups.csv"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Export Saved Lineups",
            suggested,
            "CSV Files (*.csv)",
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"

        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow(headers)
                writer.writerows(rows)
        except Exception as exc:
            logger.exception("Saved lineup export failed")
            QtWidgets.QMessageBox.critical(self, "Export Error", str(exc))
            return

        learning_note = ""
        try:
            settings = {
                "build_style": self.combo_build_style.currentText(),
                "own_mode": self.combo_build_own_mode.currentText(),
                "own_weight": self.spin_build_own_weight.value(),
                "mlb_stack_pref": self.combo_mlb_stack_pref.currentText(),
                "salary_strategy": self.combo_salary_strategy.currentText(),
            }
            validation = {
                "valid": True,
                "complete_lineups": len(rows),
                "expected_slots": expected,
            }
            saved = record_export(
                kind=kind_l,
                sport=sport,
                lineups=lineups,
                rows=rows,
                salary_cap=cap,
                export_path=path,
                validation=validation,
                settings=settings,
                grade_func=lineup_grade_for_sport,
                app_version="results-learning-v3",
            )
            archive_export_file(path, sport=sport, kind=kind_l)
            learning_note = f" Recorded {int(saved.get('lineups_recorded', 0))} lineup(s) for Results & Learning."
        except Exception as exc:
            logger.exception("Lineups exported but local learning record failed")
            learning_note = f" The CSV was saved, but its local learning record failed: {exc}"

        self.status.showMessage(f"Exported {len(rows)} saved lineup(s) to {path}.{learning_note}", 10000)
        QtWidgets.QMessageBox.information(
            self,
            "Export Complete",
            f"Saved {len(rows)} lineup(s) to:\n{path}\n\n"
            "These exact rosters are now ready to match when you import DraftKings results."
            + ("" if "failed" not in learning_note.lower() else f"\n\n{learning_note.strip()}"),
        )


    # ---------------- Save logic ----------------

    def _sd_checkbox_changed(self, row: int, state: int) -> None:
        if row < 0 or row >= len(self.last_showdown):
            return
        lu = self.last_showdown[row]
        if state == QtCore.Qt.Checked:
            if lu not in self.saved_showdown:
                self.saved_showdown.append(lu)
        else:
            if lu in self.saved_showdown:
                self.saved_showdown.remove(lu)
        self._refresh_saved_tables()

    def _cl_checkbox_changed(self, row: int, state: int) -> None:
        if row < 0 or row >= len(self.last_classic):
            return
        lu = self.last_classic[row]
        if state == QtCore.Qt.Checked:
            if lu not in self.saved_classic:
                self.saved_classic.append(lu)
        else:
            if lu in self.saved_classic:
                self.saved_classic.remove(lu)
        self._refresh_saved_tables()

    def on_sd_save_all(self) -> None:
        for r in range(self.tbl_sd.rowCount()):
            w = self.tbl_sd.cellWidget(r, 0)
            if isinstance(w, QtWidgets.QCheckBox):
                w.setChecked(True)

    def on_sd_unsave_all(self) -> None:
        for r in range(self.tbl_sd.rowCount()):
            w = self.tbl_sd.cellWidget(r, 0)
            if isinstance(w, QtWidgets.QCheckBox):
                w.setChecked(False)

    def on_cl_save_all(self) -> None:
        for r in range(self.tbl_cl.rowCount()):
            w = self.tbl_cl.cellWidget(r, 0)
            if isinstance(w, QtWidgets.QCheckBox):
                w.setChecked(True)

    def on_cl_unsave_all(self) -> None:
        for r in range(self.tbl_cl.rowCount()):
            w = self.tbl_cl.cellWidget(r, 0)
            if isinstance(w, QtWidgets.QCheckBox):
                w.setChecked(False)

    def on_clear_saved(self) -> None:
        self.saved_showdown.clear()
        self.saved_classic.clear()
        self.on_sd_unsave_all()
        self.on_cl_unsave_all()
        self._refresh_saved_tables()
        self.status.showMessage("Cleared all saved lineups.", 3000)

    # ---------------- Exposure ----------------

    def _exposure_showdown_rows(self) -> List[Dict[str, Any]]:
        total = len(self.saved_showdown)
        if total <= 0:
            return []

        counts_total: Dict[str, int] = {}
        counts_cpt: Dict[str, int] = {}
        counts_flex: Dict[str, int] = {}
        name_map: Dict[str, str] = {}
        cptid_map: Dict[str, str] = {}

        def bump(d: Dict[str, int], k: str) -> None:
            d[k] = d.get(k, 0) + 1

        for lu in self.saved_showdown:
            cpt = lu.get("Captain")
            if cpt:
                flex_id = str(cpt.get("FlexID") or "").strip()
                if flex_id:
                    bump(counts_total, flex_id)
                    bump(counts_cpt, flex_id)
                    name_map.setdefault(flex_id, self._display_name(cpt))
                    cptid_map.setdefault(flex_id, str(cpt.get("CptID") or "").strip())

            for p in (lu.get("Flex") or []):
                flex_id = str(p.get("FlexID") or "").strip()
                if not flex_id:
                    continue
                bump(counts_total, flex_id)
                bump(counts_flex, flex_id)
                name_map.setdefault(flex_id, self._display_name(p))
                cptid_map.setdefault(flex_id, str(p.get("CptID") or "").strip())

        rows: List[Dict[str, Any]] = []
        for flex_id, cnt in counts_total.items():
            cpt_cnt = counts_cpt.get(flex_id, 0)
            flex_cnt = counts_flex.get(flex_id, 0)
            rows.append({
                "Player": name_map.get(flex_id, ""),
                "FlexID": flex_id,
                "CptID": cptid_map.get(flex_id, ""),
                "Count": cnt,
                "TotalPct": (cnt / total) * 100.0,
                "CptPct": (cpt_cnt / total) * 100.0,
                "FlexPct": (flex_cnt / total) * 100.0,
            })

        rows.sort(key=lambda r: (r.get("TotalPct", 0.0), r.get("Count", 0)), reverse=True)
        return rows

    def _exposure_classic_rows(self) -> List[Dict[str, Any]]:
        total = len(self.saved_classic)
        if total <= 0:
            return []

        counts: Dict[str, int] = {}
        name_map: Dict[str, str] = {}
        pos_map: Dict[str, str] = {}

        for lu in self.saved_classic:
            for p in (lu or []):
                flex_id = str(p.get("FlexID") or "").strip()
                if not flex_id:
                    continue
                counts[flex_id] = counts.get(flex_id, 0) + 1
                name_map.setdefault(flex_id, self._display_name(p))
                pos_map.setdefault(flex_id, str(p.get("Position") or "").strip())

        rows: List[Dict[str, Any]] = []
        for flex_id, cnt in counts.items():
            rows.append({
                "Player": name_map.get(flex_id, ""),
                "FlexID": flex_id,
                "Pos": pos_map.get(flex_id, ""),
                "Count": cnt,
                "Pct": (cnt / total) * 100.0,
            })

        rows.sort(key=lambda r: (r.get("Pct", 0.0), r.get("Count", 0)), reverse=True)
        return rows

    def on_view_exposure(self) -> None:
        dlg = ExposureDialog(
            self,
            showdown_rows=self._exposure_showdown_rows(),
            classic_rows=self._exposure_classic_rows(),
        )
        dlg.show()

    # ---------------- Stack / Team / Salary Exposure Dashboard ----------------

    def _is_pitcher_for_exposure(self, p: Dict[str, Any], sport: str) -> bool:
        pos = str(p.get("Position", "") or "").upper().replace("/", ",").replace(";", ",")
        toks = {x.strip() for x in pos.split(",") if x.strip()}
        if sport.upper() == "MLB":
            return bool(toks & {"P", "SP", "RP"})
        return bool(toks & {"P", "SP", "RP"})

    def _lineup_salary_proj(self, lu: List[Dict[str, Any]]) -> tuple[float, float]:
        salary = 0.0
        proj = 0.0
        for p in lu or []:
            try:
                salary += float(p.get("FlexSalary", 0.0) or 0.0)
            except Exception:
                pass
            try:
                proj += float(p.get("FlexProjection", 0.0) or 0.0)
            except Exception:
                pass
        return salary, proj

    def _salary_band(self, salary: float) -> str:
        try:
            s = float(salary or 0.0)
        except Exception:
            s = 0.0
        if s >= 49500:
            return "$49.5k–$50k"
        if s >= 48500:
            return "$48.5k–$49.5k"
        if s >= 47000:
            return "$47k–$48.5k"
        if s >= 45000:
            return "$45k–$47k"
        return "<$45k"

    def _lineup_grade_value(self, lu: List[Dict[str, Any]], sport: str) -> float:
        """Best-effort grade value for exposure summaries; intentionally UI-only."""
        salary, proj = self._lineup_salary_proj(lu)
        grade = proj
        if salary >= 49500:
            grade += 10.0
        elif salary >= 48500:
            grade += 7.5
        elif salary >= 47000:
            grade += 4.0
        elif salary >= 45000:
            grade += 1.0

        team_counts: Dict[str, int] = {}
        for p in lu or []:
            if sport.upper() == "MLB" and self._is_pitcher_for_exposure(p, sport):
                continue
            t = str(p.get("Team", "") or "").strip().upper()
            if not t:
                continue
            team_counts[t] = team_counts.get(t, 0) + 1
        counts = sorted(team_counts.values(), reverse=True)
        if sport.upper() == "MLB" and counts:
            if counts[:2] == [5, 3]:
                grade += 10
            elif counts[:2] == [5, 2]:
                grade += 8
            elif counts[:2] == [4, 4]:
                grade += 8
            elif counts[:2] == [4, 3]:
                grade += 6
        return float(grade)

    def _lineup_stack_signature(self, lu: List[Dict[str, Any]], sport: str) -> tuple[str, str, str]:
        counts: Dict[str, int] = {}
        for p in lu or []:
            if sport.upper() == "MLB" and self._is_pitcher_for_exposure(p, sport):
                continue
            t = str(p.get("Team", "") or "").strip().upper()
            if not t:
                continue
            counts[t] = counts.get(t, 0) + 1
        parts = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
        shape = "-".join(str(v) for _, v in parts if v > 0) or "—"
        primary = f"{parts[0][0]} {parts[0][1]}" if len(parts) >= 1 else ""
        secondary = f"{parts[1][0]} {parts[1][1]}" if len(parts) >= 2 else ""
        return shape, primary, secondary

    def _stack_exposure_payload(self) -> Dict[str, Any]:
        sport = self._current_sport()
        lineups = list(self.saved_classic or [])
        total = len(lineups)

        team_acc: Dict[str, Dict[str, Any]] = {}
        stack_acc: Dict[tuple[str, str, str], Dict[str, Any]] = {}
        salary_acc: Dict[str, Dict[str, Any]] = {}
        pitcher_acc: Dict[str, Dict[str, Any]] = {}

        for lu in lineups:
            salary, proj = self._lineup_salary_proj(lu)
            grade = self._lineup_grade_value(lu, sport)

            # Salary bands
            band = self._salary_band(salary)
            sr = salary_acc.setdefault(band, {"Band": band, "Count": 0, "SalarySum": 0.0, "GradeSum": 0.0})
            sr["Count"] += 1
            sr["SalarySum"] += salary
            sr["GradeSum"] += grade

            # Team counts per lineup. For MLB, hitter stacks exclude pitchers.
            per_team_count: Dict[str, int] = {}
            per_team_salary: Dict[str, float] = {}
            per_team_proj: Dict[str, float] = {}
            for p in lu or []:
                t = str(p.get("Team", "") or "").strip().upper()
                if not t:
                    continue

                is_pitcher = self._is_pitcher_for_exposure(p, sport)
                if sport.upper() == "MLB" and is_pitcher:
                    pk = _pkey(p)
                    pr = pitcher_acc.setdefault(pk, {
                        "Pitcher": str(p.get("Name", "")),
                        "Team": t,
                        "Count": 0,
                        "SalarySum": 0.0,
                        "ProjSum": 0.0,
                    })
                    pr["Count"] += 1
                    pr["SalarySum"] += float(p.get("FlexSalary", 0.0) or 0.0)
                    pr["ProjSum"] += float(p.get("FlexProjection", 0.0) or 0.0)
                    continue

                per_team_count[t] = per_team_count.get(t, 0) + 1
                per_team_salary[t] = per_team_salary.get(t, 0.0) + float(p.get("FlexSalary", 0.0) or 0.0)
                per_team_proj[t] = per_team_proj.get(t, 0.0) + float(p.get("FlexProjection", 0.0) or 0.0)

            for t, cnt in per_team_count.items():
                tr = team_acc.setdefault(t, {
                    "Team": t,
                    "Count": 0,
                    "PlayersSum": 0.0,
                    "MaxPlayers": 0,
                    "SalarySum": 0.0,
                    "ProjSum": 0.0,
                })
                tr["Count"] += 1
                tr["PlayersSum"] += cnt
                tr["MaxPlayers"] = max(int(tr.get("MaxPlayers", 0)), int(cnt))
                tr["SalarySum"] += per_team_salary.get(t, 0.0)
                tr["ProjSum"] += per_team_proj.get(t, 0.0)

            # Stack signature rows
            shape, primary, secondary = self._lineup_stack_signature(lu, sport)
            key = (shape, primary, secondary)
            ex_names = ", ".join([f"{p.get('Name','')}({p.get('Team','')})" for p in (lu or [])[:3]])
            rr = stack_acc.setdefault(key, {
                "Shape": shape,
                "Primary": primary,
                "Secondary": secondary,
                "Count": 0,
                "SalarySum": 0.0,
                "ProjSum": 0.0,
                "Examples": "",
            })
            rr["Count"] += 1
            rr["SalarySum"] += salary
            rr["ProjSum"] += proj
            if not rr.get("Examples"):
                rr["Examples"] = ex_names

        team_rows: List[Dict[str, Any]] = []
        for t, r in team_acc.items():
            cnt = int(r.get("Count", 0) or 0)
            denom = max(1, cnt)
            team_rows.append({
                "Team": t,
                "Count": cnt,
                "Pct": (cnt / max(1, total)) * 100.0,
                "AvgPlayers": float(r.get("PlayersSum", 0.0) or 0.0) / denom,
                "MaxPlayers": float(r.get("MaxPlayers", 0) or 0),
                "AvgSalary": float(r.get("SalarySum", 0.0) or 0.0) / denom,
                "AvgProj": float(r.get("ProjSum", 0.0) or 0.0) / denom,
            })
        team_rows.sort(key=lambda x: (x.get("Pct", 0.0), x.get("AvgPlayers", 0.0)), reverse=True)

        stack_rows: List[Dict[str, Any]] = []
        for _, r in stack_acc.items():
            cnt = int(r.get("Count", 0) or 0)
            denom = max(1, cnt)
            stack_rows.append({
                "Shape": r.get("Shape", ""),
                "Primary": r.get("Primary", ""),
                "Secondary": r.get("Secondary", ""),
                "Count": cnt,
                "Pct": (cnt / max(1, total)) * 100.0,
                "AvgSalary": float(r.get("SalarySum", 0.0) or 0.0) / denom,
                "AvgProj": float(r.get("ProjSum", 0.0) or 0.0) / denom,
                "Examples": r.get("Examples", ""),
            })
        stack_rows.sort(key=lambda x: (x.get("Pct", 0.0), x.get("Count", 0)), reverse=True)

        salary_rows: List[Dict[str, Any]] = []
        preferred_order = ["$49.5k–$50k", "$48.5k–$49.5k", "$47k–$48.5k", "$45k–$47k", "<$45k"]
        for band in preferred_order:
            r = salary_acc.get(band, {"Band": band, "Count": 0, "SalarySum": 0.0, "GradeSum": 0.0})
            cnt = int(r.get("Count", 0) or 0)
            denom = max(1, cnt)
            salary_rows.append({
                "Band": band,
                "Count": cnt,
                "Pct": (cnt / max(1, total)) * 100.0,
                "AvgSalary": float(r.get("SalarySum", 0.0) or 0.0) / denom if cnt else 0.0,
                "AvgGrade": float(r.get("GradeSum", 0.0) or 0.0) / denom if cnt else 0.0,
            })

        pitcher_rows: List[Dict[str, Any]] = []
        for _, r in pitcher_acc.items():
            cnt = int(r.get("Count", 0) or 0)
            denom = max(1, cnt)
            pitcher_rows.append({
                "Pitcher": r.get("Pitcher", ""),
                "Team": r.get("Team", ""),
                "Count": cnt,
                "Pct": (cnt / max(1, total)) * 100.0,
                "AvgSalary": float(r.get("SalarySum", 0.0) or 0.0) / denom,
                "AvgProj": float(r.get("ProjSum", 0.0) or 0.0) / denom,
            })
        pitcher_rows.sort(key=lambda x: (x.get("Pct", 0.0), x.get("AvgProj", 0.0)), reverse=True)

        return {
            "sport": sport,
            "total": total,
            "team_rows": team_rows,
            "stack_rows": stack_rows,
            "salary_rows": salary_rows,
            "pitcher_rows": pitcher_rows,
        }

    def on_view_stack_exposure(self) -> None:
        payload = self._stack_exposure_payload()
        if int(payload.get("total", 0) or 0) <= 0:
            QtWidgets.QMessageBox.information(self, "No Saved Lineups", "Save some Classic/Sport lineups first, then open the stack exposure dashboard.")
            return
        dlg = StackExposureDialog(
            self,
            sport=str(payload.get("sport", self._current_sport())),
            total_lineups=int(payload.get("total", 0) or 0),
            team_rows=payload.get("team_rows", []),
            stack_rows=payload.get("stack_rows", []),
            salary_rows=payload.get("salary_rows", []),
            pitcher_rows=payload.get("pitcher_rows", []),
        )
        dlg.show()

    def _refresh_saved_tables(self) -> None:
        self.lbl_saved.setText(f"Saved: {len(self.saved_showdown)} showdown | {len(self.saved_classic)} classic")

        self.tbl_saved_sd.setRowCount(0)
        for lu in self.saved_showdown:
            row = self.tbl_saved_sd.rowCount()
            self.tbl_saved_sd.insertRow(row)
            cpt = lu.get("Captain")
            flex = sorted(lu.get("Flex", []), key=lambda x: x.get("FlexSalary", 0.0), reverse=True)
            self.tbl_saved_sd.setItem(row, 0, QtWidgets.QTableWidgetItem(self._display_id(cpt, slot="CPT")))
            for j in range(5):
                self.tbl_saved_sd.setItem(row, 1 + j, QtWidgets.QTableWidgetItem(self._display_id(flex[j], slot="FLEX") if j < len(flex) else ""))

        self.tbl_saved_cl.setRowCount(0)
        sport = self._current_sport()
        slots = get_roster_slots_for_sport(sport)
        self.tbl_saved_cl.setColumnCount(len(slots))
        self.tbl_saved_cl.setHorizontalHeaderLabels(slots)
        for lu in self.saved_classic:
            row = self.tbl_saved_cl.rowCount()
            self.tbl_saved_cl.insertRow(row)
            cells = self._classic_export_cells(lu, sport)
            for col, txt in enumerate(cells):
                self.tbl_saved_cl.setItem(row, col, QtWidgets.QTableWidgetItem(txt))
