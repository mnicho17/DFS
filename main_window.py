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
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence, Tuple

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

        if self.mode != "showdown" and self.sport == "NFL":
            return simulate_nfl_field_ownership(
                eligible,
                self.num_sims,
                salary_cap=self.salary_cap,
                progress_callback=lambda done, total, text: self.progress.emit(done, total, text),
                cancel_callback=lambda: self._cancel,
            )

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


def _lineup_signature(lineup: Sequence[Dict[str, Any]]) -> Tuple[str, ...]:
    return tuple(sorted(player_key(player) for player in lineup if player_key(player)))


def _deep_candidate_quality(lineup: Any) -> Tuple[float, float, float, float, Tuple[str, ...]]:
    metrics = getattr(lineup, "sim_metrics", {})
    metrics = metrics if isinstance(metrics, dict) else {}

    def number(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    projection = sum(number(player.get("FlexProjection")) for player in lineup)
    return (
        number(metrics.get("sim_edge")),
        number(metrics.get("sim_top_one_pct")),
        number(metrics.get("sim_return_index")),
        projection,
        _lineup_signature(lineup),
    )


def _deep_shortlist(
    lineups: Sequence[Any],
    limit: int,
    *,
    reserved_signatures: Optional[Sequence[Tuple[str, ...]]] = None,
) -> List[Any]:
    """Keep the strongest coarse-SIM candidates without erasing rare sources.

    One quarter of the shortlist is filled round-robin across candidate source
    and scenario-archetype buckets.  The rest is pure coarse-SIM quality.  This
    preserves distinct winning paths while preventing a weak construction from
    surviving solely because its label is unusual.
    """
    target = max(0, min(int(limit or 0), len(lineups)))
    if target <= 0:
        return []
    unique: Dict[Tuple[str, ...], Any] = {}
    for lineup in lineups:
        signature = _lineup_signature(lineup)
        if signature and signature not in unique:
            unique[signature] = lineup
    reserved = {tuple(signature) for signature in (reserved_signatures or [])}
    chosen: List[Any] = []
    chosen_signatures: set[Tuple[str, ...]] = set()
    for signature in reserved:
        lineup = unique.get(signature)
        if lineup is not None and len(chosen) < target:
            chosen.append(lineup)
            chosen_signatures.add(signature)

    buckets: Dict[Tuple[str, str], List[Any]] = {}
    for signature, lineup in unique.items():
        if signature in chosen_signatures:
            continue
        source = str(getattr(lineup, "candidate_source", "") or "optimizer")
        archetype = str(getattr(lineup, "candidate_archetype", "") or "general")
        buckets.setdefault((source, archetype), []).append(lineup)
    for bucket in buckets.values():
        bucket.sort(key=_deep_candidate_quality, reverse=True)

    diversity_slots = min(target - len(chosen), max(len(buckets), target // 4))
    bucket_keys = sorted(buckets)
    while diversity_slots > 0 and bucket_keys:
        next_keys: List[Tuple[str, str]] = []
        for key in bucket_keys:
            bucket = buckets[key]
            if not bucket:
                continue
            lineup = bucket.pop(0)
            signature = _lineup_signature(lineup)
            if signature not in chosen_signatures:
                chosen.append(lineup)
                chosen_signatures.add(signature)
                diversity_slots -= 1
                if diversity_slots <= 0 or len(chosen) >= target:
                    break
            if bucket:
                next_keys.append(key)
        else:
            bucket_keys = next_keys
            continue
        break

    remaining = sorted(
        (
            lineup for signature, lineup in unique.items()
            if signature not in chosen_signatures
        ),
        key=_deep_candidate_quality,
        reverse=True,
    )
    chosen.extend(remaining[: max(0, target - len(chosen))])
    return chosen[:target]


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
        portfolio_rules: Optional[Dict[str, Any]] = None,
        sim_enabled: bool = False,
        sim_scenarios: int = 750,
        field_preset: str = "150-Max",
        field_calibration: Optional[Dict[str, Any]] = None,
        contest_profile: Optional[Dict[str, Any]] = None,
        compute_mode: str = "Fast",
        deep_time_limit_seconds: float = 300.0,
        retained_lineups: Optional[List[Any]] = None,
        repair_source: str = "",
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
        self.portfolio_rules = dict(portfolio_rules or {})
        self.sim_enabled = bool(sim_enabled)
        self.sim_scenarios = max(100, int(sim_scenarios or 750))
        self.field_preset = field_preset or "150-Max"
        self.field_calibration = dict(field_calibration or {})
        self.contest_profile = (
            normalize_contest_profile(contest_profile)
            if isinstance(contest_profile, dict) and contest_profile
            else None
        )
        self.compute_mode = str(compute_mode or "Fast").strip()
        self.deep_time_limit_seconds = max(1.0, float(deep_time_limit_seconds or 300.0))
        self.retained_lineups = list(retained_lineups or [])[:self.num_lineups]
        self.repair_source = str(repair_source or "")
        self._cancel_event = threading.Event()

    def request_cancel(self) -> None:
        """Thread-safe cancellation request checked between lineup candidates."""
        self._cancel_event.set()

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            build_started = time.perf_counter()
            deep_requested = self.compute_mode.casefold().startswith("deep")
            deep_build = bool(
                deep_requested
                and self.kind != "showdown"
                and self.sport == "NFL"
                and self.sim_enabled
            )
            joint_validation_requested = bool(
                self.kind != "showdown"
                and self.sport == "NFL"
                and self.sim_enabled
                and self.contest_profile
            )
            total_phases = 5 if deep_build and joint_validation_requested else 4 if deep_build or joint_validation_requested else 3
            deep_deadline = (
                build_started + self.deep_time_limit_seconds
                if deep_build else float("inf")
            )
            generation_deadline = (
                build_started + self.deep_time_limit_seconds * 0.38
                if deep_build else float("inf")
            )
            screening_deadline = (
                build_started + self.deep_time_limit_seconds * 0.58
                if deep_build else float("inf")
            )
            selection_reserve = (
                min(60.0, max(1.0, self.deep_time_limit_seconds * 0.20), self.deep_time_limit_seconds * 0.35)
                if deep_build else 0.0
            )
            validation_deadline = deep_deadline - selection_reserve
            joint_validation_reserve = (
                min(25.0, max(0.5, self.deep_time_limit_seconds * 0.08), self.deep_time_limit_seconds * 0.20)
                if deep_build and self.contest_profile else 0.0
            )
            refinement_deadline = deep_deadline - joint_validation_reserve

            def generation_should_stop() -> bool:
                return self._cancel_event.is_set() or time.perf_counter() >= generation_deadline

            def screening_should_stop() -> bool:
                return self._cancel_event.is_set() or time.perf_counter() >= screening_deadline

            def validation_should_stop() -> bool:
                return self._cancel_event.is_set() or time.perf_counter() >= validation_deadline

            def refinement_should_stop() -> bool:
                return self._cancel_event.is_set() or time.perf_counter() >= refinement_deadline

            build_request = max(0, self.num_lineups - len(self.retained_lineups))
            if build_request <= 0:
                retained_report = portfolio_report(
                    self.retained_lineups,
                    self.portfolio_rules,
                    kind=self.kind,
                    requested=self.num_lineups,
                )
                self.finished.emit({
                    "kind": self.kind,
                    "sport": self.sport,
                    "lineups": list(self.retained_lineups),
                    "requested": self.num_lineups,
                    "cancelled": False,
                    "portfolio_report": retained_report,
                    "candidate_count": 0,
                    "sim_report": {},
                    "timing_report": {
                        "generation_seconds": 0.0,
                        "simulation_seconds": 0.0,
                        "selection_seconds": 0.0,
                        "total_seconds": 0.0,
                        "candidate_target": 0,
                        "candidate_count": 0,
                        "selected_count": len(self.retained_lineups),
                        "requested_count": self.num_lineups,
                        "retained_count": len(self.retained_lineups),
                        "replacement_requested": 0,
                        "compute_mode": "Deep" if deep_build else "Fast",
                        "deep_time_limit_seconds": self.deep_time_limit_seconds if deep_build else 0.0,
                    },
                    "repair_source": self.repair_source,
                })
                return
            self.progress.emit(
                0,
                build_request,
                f"Phase 1 of {total_phases} - starting Deep explore"
                if deep_build else f"Phase 1 of {total_phases} - starting lineup build",
            )
            expanded_target = min(450, max(build_request + 30, int(math.ceil(build_request * 1.5))))
            constraints = self.portfolio_rules.get("player_constraints") or {}

            def number(value: Any, default: float = 0.0) -> float:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return default

            hard_player_rules = any(
                number(item.get("MinPct"), 0.0) > 0.0
                or (
                    item.get("MaxPct") not in (None, "")
                    and number(item.get("MaxPct"), 100.0) < 100.0
                )
                for item in constraints.values()
            )
            style = self.build_style.strip().lower()
            if self.sport == "NFL" and style in ("contrarian", "leverage"):
                built_in_unique = 3
            elif self.sport == "NFL" and style not in ("chalk", "optimal"):
                built_in_unique = 2
            else:
                built_in_unique = 1
            hard_portfolio_rules = (
                hard_player_rules
                or bool(self.portfolio_rules.get("groups"))
                or number(self.portfolio_rules.get("max_team_pct"), 100.0) < 100.0
                or number(self.portfolio_rules.get("max_game_pct"), 100.0) < 100.0
                or int(self.portfolio_rules.get("min_unique", 1) or 1) > built_in_unique
            )
            use_nfl_sim = self.kind != "showdown" and self.sport == "NFL" and self.sim_enabled
            joint_validation_enabled = bool(use_nfl_sim and self.contest_profile)
            has_locks = any(bool(player.get("LockFlex")) for player in self.players)
            use_role_pool = should_use_nfl_role_pool(
                sport=self.sport,
                kind=self.kind,
                build_style=self.build_style,
                sim_enabled=self.sim_enabled,
            )
            ownership_candidate_target = 0
            scenario_candidate_target = 0
            if use_nfl_sim:
                alternate_sources_allowed = not hard_portfolio_rules and not has_locks
                if deep_build:
                    # A Deep build explores roughly nine times the normal 150-Max
                    # bank.  Generation is deadline-aware, so this is a ceiling,
                    # not a promise that slower hardware must fill every slot.
                    total_candidate_budget = min(
                        6000,
                        max(1200, int(math.ceil(build_request * 30.0))),
                    )
                    if alternate_sources_allowed:
                        candidate_target = int(math.ceil(total_candidate_budget * 0.55))
                        remaining_budget = max(0, total_candidate_budget - candidate_target)
                        scenario_candidate_target = int(math.ceil(remaining_budget * 0.68))
                        ownership_candidate_target = max(0, remaining_budget - scenario_candidate_target)
                    else:
                        candidate_target = min(
                            5000,
                            max(1200, int(math.ceil(build_request * 24.0))),
                        )
                else:
                    # Keep the measured 500-candidate budget for a normal 150-Max
                    # build, but diversify its sources. Projection-led optimizer
                    # lineups remain the majority; field-shaped and correlated
                    # scenario-built lineups cover constructions it may not create.
                    total_candidate_budget = min(
                        750,
                        max(expanded_target, int(math.ceil(build_request * 10.0 / 3.0))),
                    )
                    if alternate_sources_allowed:
                        candidate_target = max(
                            expanded_target,
                            int(math.ceil(total_candidate_budget * 0.60)),
                        )
                        remaining_budget = max(0, total_candidate_budget - candidate_target)
                        scenario_candidate_target = int(math.ceil(remaining_budget * 0.70))
                        ownership_candidate_target = max(0, remaining_budget - scenario_candidate_target)
                    else:
                        # Locks and hard portfolio rules belong in the optimizer's
                        # own search so every candidate respects them by construction.
                        candidate_target = min(
                            750,
                            max(expanded_target, int(math.ceil(build_request * 8.0 / 3.0))),
                        )
            elif self.kind == "showdown":
                # Showdown needs a materially wider bank so the portfolio
                # selector can diversify Captain and game-script outcomes.
                candidate_target = min(
                    4000,
                    max(expanded_target, 320, build_request * 16),
                )
            elif hard_portfolio_rules:
                candidate_target = expanded_target
            else:
                candidate_target = build_request
            candidate_budget = candidate_target + ownership_candidate_target + scenario_candidate_target
            if use_nfl_sim:
                phase_total = total_phases
                phase_label = "Deep explore" if deep_build else "starting diversified candidate bank"
                self.progress.emit(0, candidate_budget, f"Phase 1 of {phase_total} - {phase_label}")
            build_players = [dict(player) for player in self.players]
            unfiltered_build_pool_size = len(build_players)
            required_group_keys = {
                str(key)
                for group in self.portfolio_rules.get("groups") or []
                if str(group.get("type") or "") == "at_least_one"
                for key in group.get("player_keys") or []
            }
            required_role_pool_keys = set(required_group_keys)
            for player in build_players:
                key = player_key(player)
                configured = constraints.get(key) or {}
                min_total = float(configured.get("MinPct", player.get("MinPct", 0.0)) or 0.0)
                min_cpt = float(configured.get("MinCptPct", player.get("MinCptPct", 0.0)) or 0.0)
                if min_total > 0.0 or min_cpt > 0.0:
                    required_role_pool_keys.add(key)
                player["_PortfolioCandidateBoost"] = min(12.0, min_total * 0.10) + (5.0 if key in required_group_keys else 0.0)
                player["_PortfolioCptCandidateBoost"] = min(14.0, min_cpt * 0.12) + (3.0 if key in required_group_keys else 0.0)
            if use_role_pool:
                build_players = build_nfl_role_pool(
                    build_players,
                    preserve_locks=True,
                    preserve_player_keys=required_role_pool_keys,
                )
            if self.kind == "showdown":
                opt = ShowdownOptimizer(
                    build_players,
                    salary_cap=self.salary_cap,
                    own_mode=self.own_mode,
                    own_weight=self.own_weight,
                    build_style=self.build_style,
                )
                lineups = opt.build_lineups(
                    num_lineups=candidate_target,
                    progress_callback=lambda done, total, text: self.progress.emit(
                        (
                            min(candidate_budget, int(done * candidate_target / max(1, total)))
                            if use_nfl_sim
                            else min(build_request, int(done * build_request / max(1, total)))
                        ),
                        candidate_budget if use_nfl_sim else build_request,
                        f"Phase 1 of {total_phases} - generating portfolio candidates",
                    ),
                    cancel_callback=self._cancel_event.is_set,
                )
                lineups = attach_showdown_metrics(lineups, self.salary_cap)
            else:
                retained_signatures_for_build = [
                    tuple(sorted(player_key(player) for player in lineup))
                    for lineup in self.retained_lineups
                ]
                if deep_build:
                    # Independent seeds expose different QB stacks, bring-backs,
                    # salary shapes, and value combinations.  Batching also keeps
                    # the generator's pairwise uniqueness work bounded.
                    lineups = []
                    seeds = (1337, 4241, 7919, 12007)
                    remaining_optimizer = candidate_target
                    completed_optimizer = 0
                    for batch_index, seed in enumerate(seeds):
                        if remaining_optimizer <= 0 or generation_should_stop():
                            break
                        batches_left = len(seeds) - batch_index
                        batch_target = int(math.ceil(remaining_optimizer / max(1, batches_left)))
                        opt = MultiSportClassicOptimizer(
                            build_players,
                            sport=self.sport,
                            salary_cap=self.salary_cap,
                            seed=seed,
                            own_mode=self.own_mode,
                            own_weight=self.own_weight,
                            build_style=self.build_style,
                            mlb_stack_pref=self.mlb_stack_pref,
                            salary_strategy=self.salary_strategy,
                        )
                        batch_lineups = opt.build_lineups(
                            num_lineups=batch_target,
                            progress_callback=lambda done, total, text, offset=completed_optimizer: self.progress.emit(
                                min(candidate_budget, offset + int(done)),
                                candidate_budget,
                                f"Phase 1 of {total_phases} - Deep explore (seed {batch_index + 1}/{len(seeds)})",
                            ),
                            cancel_callback=generation_should_stop,
                            excluded_signatures=retained_signatures_for_build,
                            minimum_unique=int(self.portfolio_rules.get("min_unique", 1) or 1),
                        )
                        lineups.extend(batch_lineups)
                        completed_optimizer += len(batch_lineups)
                        remaining_optimizer = max(0, candidate_target - completed_optimizer)
                else:
                    opt = MultiSportClassicOptimizer(
                        build_players,
                        sport=self.sport,
                        salary_cap=self.salary_cap,
                        own_mode=self.own_mode,
                        own_weight=self.own_weight,
                        build_style=self.build_style,
                        mlb_stack_pref=self.mlb_stack_pref,
                        salary_strategy=self.salary_strategy,
                    )
                    lineups = opt.build_lineups(
                        num_lineups=candidate_target,
                        progress_callback=lambda done, total, text: self.progress.emit(
                            (
                                min(candidate_budget, int(done * candidate_target / max(1, total)))
                                if use_nfl_sim
                                else min(build_request, int(done * build_request / max(1, total)))
                            ),
                            candidate_budget if use_nfl_sim else build_request,
                            f"Phase 1 of {total_phases} - {text}",
                        ),
                        cancel_callback=self._cancel_event.is_set,
                        excluded_signatures=retained_signatures_for_build,
                        minimum_unique=int(self.portfolio_rules.get("min_unique", 1) or 1),
                    )
            generation_seconds = time.perf_counter() - build_started
            sim_report: Dict[str, Any] = {}
            scenario_candidate_report: Dict[str, Any] = {}
            source_additions = {
                "optimizer": len(lineups),
                "field_shaped": 0,
                "scenario_built": 0,
            }
            simulation_seconds = 0.0
            deep_report: Dict[str, Any] = {
                "enabled": deep_build,
                "time_limit_seconds": self.deep_time_limit_seconds if deep_build else 0.0,
                "screening_scenarios": 0,
                "validation_scenarios": 0,
                "candidate_bank_count": 0,
                "shortlist_count": 0,
                "refinement_swaps": 0,
                "duplication_refinement_swaps": 0,
                "refinement_attempts": 0,
                "refinement_seconds": 0.0,
                "refinement_stop_reason": "disabled",
                "time_remaining_seconds": 0.0,
                "validation_top_overlap_pct": None,
                "validation_time_limit_reached": False,
                "time_limit_reached": False,
            }
            if use_nfl_sim and lineups and not self._cancel_event.is_set():
                field_config = nfl_field_preset(self.field_preset, self.field_calibration)
                if self.contest_profile:
                    field_config["contest_profile"] = dict(self.contest_profile)
                    field_config["field_size"] = int(self.contest_profile["field_size"])
                strategy_l = self.salary_strategy.strip().lower()
                generation_cancel_callback = generation_should_stop if deep_build else self._cancel_event.is_set
                if ownership_candidate_target > 0 or scenario_candidate_target > 0:
                    # Add two independent sources. Field-shaped candidates mimic
                    # realistic opponent constructions; scenario-built candidates
                    # optimize for correlated ceiling, leverage, and low-dup paths.
                    extras: List[List[Dict[str, Any]]] = []
                    optimizer_generated_count = len(lineups)
                    if ownership_candidate_target > 0:
                        extras, _ = generate_nfl_field_lineups(
                            build_players,
                            ownership_candidate_target,
                            salary_cap=self.salary_cap,
                            min_salary=max(0.0, self.salary_cap - 1000.0),
                            seed=20260913,
                            cancel_callback=generation_cancel_callback,
                            candidate_mode=True,
                            unique=True,
                            field_config=field_config,
                        )
                        self.progress.emit(
                            min(candidate_budget, optimizer_generated_count + len(extras)),
                            candidate_budget,
                            f"Phase 1 of {total_phases} - adding field-shaped candidates",
                        )
                    scenario_progress_offset = optimizer_generated_count + len(extras)
                    scenario_extras, scenario_candidate_report = generate_nfl_scenario_lineups(
                        build_players,
                        scenario_candidate_target,
                        salary_cap=self.salary_cap,
                        min_salary=max(0.0, self.salary_cap - 1000.0),
                        seed=20261004,
                        progress_callback=lambda done, total, text: self.progress.emit(
                            min(
                                candidate_budget,
                                scenario_progress_offset
                                + int(done * scenario_candidate_target / max(1, total)),
                            ),
                            candidate_budget,
                            f"Phase 1 of {total_phases} - {text}",
                        ),
                        cancel_callback=generation_cancel_callback,
                        field_config=field_config,
                    )
                else:
                    extras = []
                    scenario_extras = []

                # Deep optimizer batches can overlap across seeds, so all NFL
                # SIM sources pass through one exact-signature dedupe step.
                unique_lineups: Dict[tuple[str, ...], List[Dict[str, Any]]] = {}
                source_additions = {"optimizer": 0, "field_shaped": 0, "scenario_built": 0}
                for source, source_lineups in (
                    ("optimizer", lineups),
                    ("field_shaped", extras),
                    ("scenario_built", scenario_extras),
                ):
                    for lineup in source_lineups:
                        signature = tuple(sorted(player_key(player) for player in lineup))
                        if signature not in unique_lineups:
                            unique_lineups[signature] = SimLineup(
                                lineup,
                                candidate_source=source,
                                candidate_archetype=str(
                                    getattr(lineup, "candidate_archetype", "") or ""
                                ),
                            )
                            source_additions[source] += 1
                lineups = list(unique_lineups.values())
                scenario_candidate_report["unique_source_additions"] = source_additions
                if "leverage" not in strategy_l and "balanced" not in strategy_l:
                    hard_floor = self.salary_cap - (500.0 if "max" in strategy_l else 1000.0)
                    near_cap = [
                        lineup for lineup in lineups
                        if sum(float(player.get("FlexSalary", 0.0) or 0.0) for player in lineup) >= hard_floor
                    ]
                    if len(near_cap) >= build_request:
                        lineups = near_cap
                generation_seconds = time.perf_counter() - build_started
                simulation_started = time.perf_counter()
                retained_signatures = {
                    tuple(sorted(player_key(player) for player in retained))
                    for retained in self.retained_lineups
                }
                candidate_bank_count = len(lineups)
                deep_report["candidate_bank_count"] = candidate_bank_count

                if deep_build:
                    screening_scenarios = max(250, min(600, self.sim_scenarios))
                    screening_field_count = max(800, min(1600, build_request * 8))
                    self.progress.emit(
                        0,
                        screening_scenarios,
                        f"Phase 2 of {total_phases} - screening the expanded candidate bank",
                    )
                    coarse_result = simulate_nfl_contest(
                        list(self.retained_lineups) + list(lineups),
                        build_players,
                        scenarios=screening_scenarios,
                        field_lineup_count=screening_field_count,
                        salary_cap=self.salary_cap,
                        seed=73129,
                        progress_callback=lambda done, total, text: self.progress.emit(
                            done, total, f"Phase 2 of {total_phases} - {text}"
                        ),
                        cancel_callback=screening_should_stop,
                        field_config=field_config,
                    )
                    coarse_report = dict(coarse_result.get("report") or {})
                    deep_report["screening_scenarios"] = int(coarse_report.get("scenarios", 0) or 0)
                    coarse_lineups = list(coarse_result.get("lineups") or [])
                    shortlist_limit = min(
                        len(coarse_lineups),
                        max(
                            400,
                            self.num_lineups + len(self.retained_lineups) + 100,
                            build_request * 6 + len(self.retained_lineups),
                        ),
                    )
                    if coarse_lineups and deep_report["screening_scenarios"] > 0:
                        shortlist_all = _deep_shortlist(
                            coarse_lineups,
                            shortlist_limit,
                            reserved_signatures=list(retained_signatures),
                        )
                        coarse_by_signature = {
                            _lineup_signature(lineup): lineup for lineup in shortlist_all
                        }
                        self.retained_lineups = [
                            coarse_by_signature.get(_lineup_signature(retained), retained)
                            for retained in self.retained_lineups
                        ]
                        lineups = [
                            lineup for lineup in shortlist_all
                            if _lineup_signature(lineup) not in retained_signatures
                        ]
                        coarse_result = {
                            "lineups": shortlist_all,
                            "report": coarse_report,
                        }
                        stability_count = min(
                            len(shortlist_all),
                            max(50, self.num_lineups),
                        )
                        coarse_top_signatures = {
                            _lineup_signature(lineup)
                            for lineup in sorted(
                                shortlist_all,
                                key=_deep_candidate_quality,
                                reverse=True,
                            )[:stability_count]
                        }
                    else:
                        coarse_top_signatures = set()
                    deep_report["shortlist_count"] = len(lineups) + len(self.retained_lineups)

                    validation_scenarios = max(2500, self.sim_scenarios)
                    validation_field_count = max(1800, min(3600, build_request * 18))
                    sim_result = coarse_result
                    if lineups and not validation_should_stop():
                        self.progress.emit(
                            0,
                            validation_scenarios,
                            f"Phase 3 of {total_phases} - validating the shortlist with independent scenarios",
                        )
                        validation_result = simulate_nfl_contest(
                            list(self.retained_lineups) + list(lineups),
                            build_players,
                            scenarios=validation_scenarios,
                            field_lineup_count=validation_field_count,
                            salary_cap=self.salary_cap,
                            seed=90210,
                            progress_callback=lambda done, total, text: self.progress.emit(
                                done, total, f"Phase 3 of {total_phases} - {text}"
                            ),
                            cancel_callback=validation_should_stop,
                            field_config=field_config,
                        )
                        validation_report = dict(validation_result.get("report") or {})
                        deep_report["validation_scenarios"] = int(
                            validation_report.get("scenarios", 0) or 0
                        )
                        if deep_report["validation_scenarios"] > 0:
                            sim_result = validation_result
                            if coarse_top_signatures:
                                validation_top = {
                                    _lineup_signature(lineup)
                                    for lineup in list(validation_result.get("lineups") or [])[
                                        : len(coarse_top_signatures)
                                    ]
                                }
                                deep_report["validation_top_overlap_pct"] = round(
                                    len(coarse_top_signatures.intersection(validation_top))
                                    / max(1, len(coarse_top_signatures))
                                    * 100.0,
                                    1,
                                )
                    deep_report["validation_time_limit_reached"] = bool(
                        time.perf_counter() >= validation_deadline
                    )
                else:
                    field_count = max(600, min(2400, build_request * 8))
                    self.progress.emit(0, self.sim_scenarios, f"Phase 2 of {total_phases} - preparing NFL contest simulation")
                    sim_result = simulate_nfl_contest(
                        list(self.retained_lineups) + list(lineups),
                        build_players,
                        scenarios=self.sim_scenarios,
                        field_lineup_count=field_count,
                        salary_cap=self.salary_cap,
                        progress_callback=lambda done, total, text: self.progress.emit(
                            done, total, f"Phase 2 of {total_phases} - {text}"
                        ),
                        cancel_callback=self._cancel_event.is_set,
                        field_config=field_config,
                    )
                simulated_lineups = list(sim_result.get("lineups") or [])
                if simulated_lineups:
                    simulated_by_signature = {
                        tuple(sorted(player_key(player) for player in lineup)): lineup
                        for lineup in simulated_lineups
                    }
                    self.retained_lineups = [
                        simulated_by_signature.get(
                            tuple(sorted(player_key(player) for player in retained)),
                            retained,
                        )
                        for retained in self.retained_lineups
                    ]
                    lineups = [
                        lineup
                        for lineup in simulated_lineups
                        if tuple(sorted(player_key(player) for player in lineup))
                        not in retained_signatures
                    ]
                sim_report = dict(sim_result.get("report") or {})
                if deep_build:
                    sim_report["deep_build"] = dict(deep_report)
                simulation_seconds = time.perf_counter() - simulation_started

            selection_started = time.perf_counter()
            self.progress.emit(
                min(self.num_lineups, len(lineups) + len(self.retained_lineups)),
                self.num_lineups,
                f"Phase 4 of {total_phases} - selecting, then polishing duplication with remaining time"
                if deep_build else f"Phase 3 of {total_phases} - selecting portfolio",
            )
            selected = select_portfolio(
                lineups,
                self.num_lineups,
                rules=self.portfolio_rules,
                kind=self.kind,
                retained_lineups=self.retained_lineups,
                refinement_passes=256 if deep_build else 0,
                refinement_stop_callback=refinement_should_stop if deep_build else None,
                refinement_polish_duplication=deep_build,
            )
            if deep_build:
                selection_report = selected.get("report", {})
                deep_report["refinement_swaps"] = int(selection_report.get("refinement_swaps", 0) or 0)
                deep_report["duplication_refinement_swaps"] = int(
                    selection_report.get("duplication_refinement_swaps", 0) or 0
                )
                deep_report["refinement_attempts"] = int(
                    selection_report.get("refinement_attempts", 0) or 0
                )
                deep_report["refinement_seconds"] = float(
                    selection_report.get("refinement_seconds", 0.0) or 0.0
                )
                deep_report["refinement_stop_reason"] = str(
                    selection_report.get("refinement_stop_reason") or "completed"
                )
                deep_report["time_remaining_seconds"] = max(
                    0.0,
                    deep_deadline - time.perf_counter(),
                )
                deep_report["time_limit_reached"] = bool(
                    time.perf_counter() >= deep_deadline
                )
                if sim_report:
                    sim_report["deep_build"] = dict(deep_report)
            lineups = selected["lineups"]
            selection_core_seconds = time.perf_counter() - selection_started
            joint_report: Dict[str, Any] = {}
            if joint_validation_enabled and lineups and not self._cancel_event.is_set():
                joint_scenarios = max(750, min(1200, self.sim_scenarios)) if deep_build else self.sim_scenarios
                joint_field_count = max(600, min(1800, len(lineups) * 8))
                joint_phase = 5 if deep_build else 4
                self.progress.emit(
                    0,
                    joint_scenarios,
                    f"Phase {joint_phase} of {total_phases} - validating all selected entries together",
                )
                joint_started = time.perf_counter()
                joint_result = simulate_nfl_portfolio_contest(
                    lineups,
                    build_players,
                    contest_profile=dict(self.contest_profile or {}),
                    scenarios=joint_scenarios,
                    field_lineup_count=joint_field_count,
                    salary_cap=self.salary_cap,
                    field_config=field_config,
                    seed=271828,
                    progress_callback=lambda done, total, text: self.progress.emit(
                        done, total, f"Phase {joint_phase} of {total_phases} - {text}"
                    ),
                    cancel_callback=(
                        (lambda: self._cancel_event.is_set() or time.perf_counter() >= deep_deadline)
                        if deep_build else self._cancel_event.is_set
                    ),
                    adaptive=True,
                )
                joint_report = dict(joint_result.get("report") or {})
                if int(joint_report.get("scenarios", 0) or 0) > 0:
                    lineups = list(joint_result.get("lineups") or lineups)
                simulation_seconds += max(0.0, time.perf_counter() - joint_started)

                previous_report = dict(selected.get("report") or {})
                refreshed_report = portfolio_report(
                    lineups,
                    self.portfolio_rules,
                    kind=self.kind,
                    requested=self.num_lineups,
                )
                for key in (
                    "refinement_swaps", "duplication_refinement_swaps", "refinement_attempts",
                    "refinement_stop_reason", "refinement_seconds",
                ):
                    if key in previous_report:
                        refreshed_report[key] = previous_report[key]
                for warning in previous_report.get("warnings") or []:
                    if warning not in refreshed_report["warnings"]:
                        refreshed_report["warnings"].append(warning)
                if not joint_report.get("entry_count_match", True):
                    mismatch_warning = (
                        f"Contest profile plans {int(joint_report.get('planned_entries', 0) or 0):,} entries; "
                        f"this build jointly simulated {int(joint_report.get('entries_simulated', 0) or 0):,}."
                    )
                    if mismatch_warning not in refreshed_report["warnings"]:
                        refreshed_report["warnings"].append(mismatch_warning)
                refreshed_report["joint_contest"] = dict(joint_report)
                refreshed_report["compliant"] = bool(
                    not refreshed_report.get("warnings")
                    and len(lineups) >= self.num_lineups
                )
                refreshed_report["text"] = format_portfolio_report_text(refreshed_report)
                selected["report"] = refreshed_report
                if sim_report:
                    sim_report["joint_portfolio"] = dict(joint_report)
            if sim_report and selected["report"].get("sim_summary"):
                sim_report["portfolio"] = dict(selected["report"]["sim_summary"])
            if sim_report:
                selected_source_counts = Counter(
                    str(getattr(lineup, "candidate_source", "") or "optimizer")
                    for lineup in lineups
                )
                selected_archetypes = Counter(
                    str(getattr(lineup, "candidate_archetype", "") or "")
                    for lineup in lineups
                    if str(getattr(lineup, "candidate_archetype", "") or "")
                )
                sim_report["candidate_sources"] = {
                    "generated": dict(source_additions),
                    "selected": dict(selected_source_counts),
                    "selected_archetypes": dict(selected_archetypes),
                }
                sim_report["preset_comparison"] = compare_nfl_lineups_to_preset(
                    lineups,
                    nfl_field_preset(self.field_preset, self.field_calibration),
                    salary_cap=self.salary_cap,
                )
            selection_seconds = selection_core_seconds
            reported_candidate_count = (
                int(deep_report.get("candidate_bank_count", 0) or 0)
                if deep_build
                else int(selected.get("candidate_count", 0) or 0)
            )
            timing_report = {
                "generation_seconds": max(0.0, generation_seconds),
                "simulation_seconds": max(0.0, simulation_seconds),
                "selection_seconds": max(0.0, selection_seconds),
                "total_seconds": max(0.0, time.perf_counter() - build_started),
                "candidate_target": int(candidate_budget),
                "optimizer_candidate_target": int(candidate_target),
                "ownership_candidate_target": int(ownership_candidate_target),
                "scenario_candidate_target": int(scenario_candidate_target),
                "candidate_count": reported_candidate_count,
                "selected_count": len(lineups),
                "requested_count": self.num_lineups,
                "retained_count": len(self.retained_lineups),
                "replacement_requested": build_request,
                "build_pool_size": len(build_players),
                "unfiltered_build_pool_size": int(unfiltered_build_pool_size),
                "role_pool_applied": bool(use_role_pool),
                "role_pool_omitted": max(0, int(unfiltered_build_pool_size - len(build_players))),
                "sim_scenarios": self.sim_scenarios if use_nfl_sim else 0,
                "portfolio_simulation_scenarios": int(joint_report.get("scenarios", 0) or 0),
                "scenario_candidate_report": scenario_candidate_report,
                "compute_mode": "Deep" if deep_build else "Fast",
                "deep_time_limit_seconds": self.deep_time_limit_seconds if deep_build else 0.0,
                "screening_scenarios": int(deep_report.get("screening_scenarios", 0) or 0),
                "validation_scenarios": int(deep_report.get("validation_scenarios", 0) or 0),
                "shortlist_count": int(deep_report.get("shortlist_count", 0) or 0),
                "refinement_swaps": int(deep_report.get("refinement_swaps", 0) or 0),
                "duplication_refinement_swaps": int(
                    deep_report.get("duplication_refinement_swaps", 0) or 0
                ),
                "refinement_attempts": int(deep_report.get("refinement_attempts", 0) or 0),
                "refinement_seconds": float(deep_report.get("refinement_seconds", 0.0) or 0.0),
                "refinement_stop_reason": str(
                    deep_report.get("refinement_stop_reason") or "disabled"
                ),
                "time_remaining_seconds": float(
                    deep_report.get("time_remaining_seconds", 0.0) or 0.0
                ),
                "validation_time_limit_reached": bool(
                    deep_report.get("validation_time_limit_reached")
                ),
                "validation_top_overlap_pct": deep_report.get("validation_top_overlap_pct"),
                "time_limit_reached": bool(deep_report.get("time_limit_reached")),
            }
            self.finished.emit({
                "kind": self.kind,
                "sport": self.sport,
                "lineups": lineups,
                "requested": self.num_lineups,
                "cancelled": self._cancel_event.is_set(),
                "portfolio_report": selected["report"],
                "candidate_count": reported_candidate_count,
                "sim_report": sim_report,
                "timing_report": timing_report,
                "repair_source": self.repair_source,
            })
        except Exception:
            self.error.emit(traceback.format_exc())


from data_io import read_players_csv
from injury_api import enrich_players_with_injuries
from optimizers import ShowdownOptimizer, ClassicOptimizer, MultiSportClassicOptimizer, attach_showdown_metrics, get_roster_slots_for_sport, lineup_slots_for_sport, _eligible_for_slot, lineup_grade_for_sport
from widgets import CopyRowTableWidget
from mlb_enrichment import apply_mlb_factors, clear_mlb_factors
from mlb_batting_order import apply_batting_order, clear_batting_order, build_best_stacks
from mlb_auto_data import apply_auto_mlb_context
from nfl_auto_data import apply_auto_nfl_context, refresh_live_nfl_data
from learning_db import (
    archive_export_file,
    attach_salary_csv_to_latest_field,
    generate_learning_report,
    history_folder_structure,
    import_historical_result_csvs,
    load_nfl_field_calibration,
    record_export,
)
from portfolio_rules import format_portfolio_report_text, player_key, portfolio_report, select_portfolio
from nfl_simulation import SimLineup, build_nfl_role_pool, compare_nfl_lineups_to_preset, generate_nfl_field_lineups, generate_nfl_scenario_lineups, nfl_field_preset, should_use_nfl_role_pool, simulate_nfl_contest, simulate_nfl_field_ownership, simulate_nfl_portfolio_contest
from lineup_space import calculate_lineup_space
from portfolio_insights import build_portfolio_insights
from slate_readiness import audit_slate
from entry_safety import build_entry_safety_report
from game_day_safety import build_final_lock_report
from build_recipes import dump_recipes_json, load_recipes_json, normalize_recipe
from contest_profiles import (
    dump_profiles_json,
    format_payout_text,
    load_profiles_json,
    normalize_contest_profile,
    parse_payout_text,
)
from build_diagnostics import (
    build_history_label,
    clear_build_history,
    create_build_diagnostic,
    format_build_comparison,
    format_build_report,
    load_build_history,
    save_build_diagnostic,
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
    """Saved-lineup exposure dashboard with sport-specific views."""

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
        tabs.setObjectName("stackExposureTabs")
        layout.addWidget(tabs, 1)

        self.tbl_team = self._make_table(["Team", "Lineups", "Exposure %", "Avg Players", "Max Players", "Avg Salary", "Avg Proj"])
        self._add_tab(tabs, "Team Exposure", self.tbl_team)

        self.tbl_stack = self._make_table(["Stack Shape", "Primary", "Secondary", "Count", "Exposure %", "Avg Salary", "Avg Proj", "Examples"])
        self._add_tab(tabs, "Stack Shapes", self.tbl_stack)

        self.tbl_salary = self._make_table(["Salary Band", "Count", "Exposure %", "Avg Salary", "Avg Grade"])
        self._add_tab(tabs, "Salary Bands", self.tbl_salary)

        self.tbl_pitcher = None
        if self.sport == "MLB":
            self.tbl_pitcher = self._make_table(["Pitcher", "Team", "Count", "Exposure %", "Avg Salary", "Avg Proj"])
            self.tbl_pitcher.setObjectName("stackExposurePitchers")
            self._add_tab(tabs, "Pitchers", self.tbl_pitcher)

        self._load_team(team_rows)
        self._load_stack(stack_rows)
        self._load_salary(salary_rows)
        if self.tbl_pitcher is not None:
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


class ResultsImportWorker(QtCore.QObject):
    """Read large standings files without blocking the desktop interface."""

    progress = QtCore.pyqtSignal(int, int, str)
    finished = QtCore.pyqtSignal(dict)
    error = QtCore.pyqtSignal(str)

    def __init__(self, paths: List[str]):
        super().__init__()
        self.paths = list(paths or [])
        self._cancel_event = threading.Event()

    def request_cancel(self) -> None:
        self._cancel_event.set()

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            result = import_historical_result_csvs(
                self.paths,
                progress_callback=lambda done, total, text: self.progress.emit(done, total, text),
                cancel_callback=self._cancel_event.is_set,
            )
            self.finished.emit(result)
        except Exception:
            self.error.emit(traceback.format_exc())


class FieldSalaryAttachWorker(QtCore.QObject):
    progress = QtCore.pyqtSignal(int, int, str)
    finished = QtCore.pyqtSignal(dict)
    error = QtCore.pyqtSignal(str)

    def __init__(self, salary_path: str):
        super().__init__()
        self.salary_path = salary_path
        self._cancel_event = threading.Event()

    def request_cancel(self) -> None:
        self._cancel_event.set()

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            result = attach_salary_csv_to_latest_field(
                self.salary_path,
                progress_callback=lambda done, total, text: self.progress.emit(done, total, text),
                cancel_callback=self._cancel_event.is_set,
            )
            self.finished.emit(result)
        except Exception:
            self.error.emit(traceback.format_exc())


class ResultsLearningDialog(QtWidgets.QDialog):
    """Local results import and learning report."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Results & Learning")
        self.resize(820, 700)
        self._import_thread: Optional[QtCore.QThread] = None
        self._import_worker: Optional[QtCore.QObject] = None
        self._close_after_import = False
        layout = QtWidgets.QVBoxLayout(self)

        intro = QtWidgets.QLabel(
            "Import DraftKings contest standings or contest-history CSV files. "
            "The app matches exact rosters to lineups exported from this app and can summarize "
            "complete NFL fields. Attach the matching salary file for salary and construction detail; "
            "all data stays local."
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
        self.import_button = QtWidgets.QPushButton("Import DraftKings Results")
        self.import_button.setObjectName("importResultsButton")
        self.import_button.clicked.connect(self.import_results)
        buttons.addWidget(self.import_button)

        self.attach_salary_button = QtWidgets.QPushButton("Attach Matching Salaries")
        self.attach_salary_button.setObjectName("attachFieldSalaryButton")
        self.attach_salary_button.setToolTip(
            "Attach the DraftKings salary CSV from the same historical slate to the latest complete NFL field."
        )
        self.attach_salary_button.clicked.connect(self.attach_matching_salaries)
        buttons.addWidget(self.attach_salary_button)

        self.refresh_button = QtWidgets.QPushButton("Refresh Report")
        self.refresh_button.clicked.connect(self.refresh_report)
        buttons.addWidget(self.refresh_button)

        folder_button = QtWidgets.QPushButton("Open Local History Folder")
        folder_button.clicked.connect(self.open_history_folder)
        buttons.addWidget(folder_button)
        buttons.addStretch(1)

        close_button = QtWidgets.QPushButton("Close")
        close_button.clicked.connect(self.close)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        import_status = QtWidgets.QHBoxLayout()
        self.import_progress = QtWidgets.QProgressBar(self)
        self.import_progress.setObjectName("resultsImportProgress")
        self.import_progress.setVisible(False)
        import_status.addWidget(self.import_progress, 1)
        self.import_cancel = QtWidgets.QPushButton("Cancel Import")
        self.import_cancel.setObjectName("cancelResultsImportButton")
        self.import_cancel.setVisible(False)
        self.import_cancel.clicked.connect(self.cancel_import)
        import_status.addWidget(self.import_cancel)
        layout.addLayout(import_status)
        self.import_status_label = QtWidgets.QLabel("")
        self.import_status_label.setObjectName("resultsImportStatus")
        self.import_status_label.setVisible(False)
        layout.addWidget(self.import_status_label)
        self.refresh_report()

    def refresh_report(self) -> None:
        try:
            payload = generate_learning_report()
            roi = payload.get("roi_pct")
            roi_text = f"{float(roi):+.1f}% ROI" if roi is not None else "ROI unavailable"
            self.summary.setText(
                f"{int(payload.get('exported_lineups', 0)):,} exported lineups  |  "
                f"{int(payload.get('matched_rows', 0)):,} matched results  |  "
                f"{int(payload.get('sim_matched_rows', 0)):,} SIM results  |  "
                f"{int(payload.get('field_entries', 0)):,} field entries  |  "
                f"{float(payload.get('match_rate', 0.0)):.1f}% match rate  |  {roi_text}"
            )
            self.report.setPlainText(str(payload.get("text", "")))
        except Exception as exc:
            logger.exception("Learning report refresh failed")
            self.summary.setText("Results report is temporarily unavailable.")
            self.report.setPlainText(str(exc))

    def import_results(self) -> None:
        if self._import_thread is not None and self._import_thread.isRunning():
            return
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Select DraftKings Result CSV Files",
            "",
            "CSV Files (*.csv)",
        )
        if not paths:
            return
        self.import_button.setEnabled(False)
        self.attach_salary_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.import_progress.setRange(0, 0)
        self.import_progress.setVisible(True)
        self.import_cancel.setEnabled(True)
        self.import_cancel.setVisible(True)
        self.import_status_label.setText("Inspecting selected DraftKings files...")
        self.import_status_label.setVisible(True)

        self._start_background_import(
            ResultsImportWorker(paths),
            self._on_import_finished,
        )

    def _start_background_import(self, worker: QtCore.QObject, finished_slot: Any) -> None:
        self._import_thread = QtCore.QThread(self)
        self._import_worker = worker
        self._import_worker.moveToThread(self._import_thread)
        self._import_thread.started.connect(self._import_worker.run)
        self._import_worker.progress.connect(self._on_import_progress)
        self._import_worker.finished.connect(finished_slot)
        self._import_worker.error.connect(self._on_import_error)
        self._import_worker.finished.connect(self._import_thread.quit)
        self._import_worker.error.connect(self._import_thread.quit)
        self._import_worker.finished.connect(self._import_worker.deleteLater)
        self._import_worker.error.connect(self._import_worker.deleteLater)
        self._import_thread.finished.connect(self._on_import_thread_finished)
        self._import_thread.finished.connect(self._import_thread.deleteLater)
        self._import_thread.start()

    def attach_matching_salaries(self) -> None:
        if self._import_thread is not None and self._import_thread.isRunning():
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select Matching DraftKings Salary CSV",
            "",
            "CSV Files (*.csv)",
        )
        if not path:
            return
        self.import_button.setEnabled(False)
        self.attach_salary_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.import_progress.setRange(0, 0)
        self.import_progress.setVisible(True)
        self.import_cancel.setEnabled(True)
        self.import_cancel.setVisible(True)
        self.import_status_label.setText("Checking salary-file slate match...")
        self.import_status_label.setVisible(True)
        self._start_background_import(
            FieldSalaryAttachWorker(path),
            self._on_salary_attach_finished,
        )

    def _on_import_progress(self, done: int, total: int, text: str) -> None:
        if total > 0:
            self.import_progress.setRange(0, total)
            self.import_progress.setValue(min(done, total))
        else:
            self.import_progress.setRange(0, 0)
        self.import_status_label.setText(text)

    def cancel_import(self) -> None:
        if self._import_worker is not None:
            self._import_worker.request_cancel()
            self.import_cancel.setEnabled(False)
            self.import_status_label.setText("Cancelling after the current batch...")

    def _finish_import_ui(self) -> None:
        self.import_button.setEnabled(True)
        self.attach_salary_button.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.import_progress.setVisible(False)
        self.import_cancel.setVisible(False)
        self.import_status_label.setVisible(False)

    def _on_salary_attach_finished(self, result: Dict[str, Any]) -> None:
        self._finish_import_ui()
        self.refresh_report()
        title = "Salary Attachment Cancelled" if result.get("cancelled") else (
            "Salaries Attached" if result.get("attached") else "Salary File Not Attached"
        )
        QtWidgets.QMessageBox.information(
            self,
            title,
            str(result.get("message") or "Salary attachment finished."),
        )

    def _on_import_finished(self, result: Dict[str, Any]) -> None:
        self._finish_import_ui()
        self.refresh_report()
        if result.get("cancelled"):
            QtWidgets.QMessageBox.information(
                self, "Import Cancelled",
                f"Import cancelled. Completed files: {int(result.get('files_imported', 0)):,}.",
            )
        else:
            message = (
                f"Imported {int(result.get('rows_imported', 0)):,} rows from "
                f"{int(result.get('files_imported', 0)):,} file(s).\n\n"
                f"Exact lineup matches: {int(result.get('matched_rows', 0)):,}\n"
                f"Unmatched personal entries: {int(result.get('unmatched_rows', 0)):,}\n"
                f"Complete fields analyzed: {int(result.get('field_contests_analyzed', 0)):,} "
                f"({int(result.get('field_entries_analyzed', 0)):,} valid lineups)"
            )
            if int(result.get("duplicates_skipped", 0)):
                message += f"\nAlready imported files skipped: {int(result.get('duplicates_skipped', 0)):,}"
            if int(result.get("field_only_files", 0)):
                message += (
                    "\n\nComplete standings were summarized as opponent-field data without "
                    "creating a result record for every opponent entry. Import personal contest "
                    "history separately when you want exact results matched to your exports."
                )
            if result.get("errors"):
                message += "\n\nSome files could not be imported:\n" + "\n".join(result["errors"])
            QtWidgets.QMessageBox.information(self, "Results Imported", message)

    def _on_import_error(self, message: str) -> None:
        self._finish_import_ui()
        logger.error("Results import failed:\n%s", message)
        QtWidgets.QMessageBox.critical(self, "Results Import Error", message)

    def _on_import_thread_finished(self) -> None:
        self._import_thread = None
        self._import_worker = None
        if self._close_after_import:
            QtCore.QTimer.singleShot(0, self.accept)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self._import_thread is not None and self._import_thread.isRunning():
            self._close_after_import = True
            self.cancel_import()
            event.ignore()
            return
        super().closeEvent(event)

    def reject(self) -> None:
        if self._import_thread is not None and self._import_thread.isRunning():
            self._close_after_import = True
            self.cancel_import()
            return
        super().reject()

    def open_history_folder(self) -> None:
        folder = history_folder_structure()["history"]
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(folder))


class SlateReadinessDialog(QtWidgets.QDialog):
    """Visual, report-only slate preflight summary."""

    def __init__(self, report: Dict[str, Any], parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.report = dict(report or {})
        self.setWindowTitle("Slate Readiness")
        self.setModal(True)
        self.resize(960, 560)

        layout = QtWidgets.QVBoxLayout(self)
        status = str(self.report.get("status") or "review")
        color = {"ready": "#8FE3A1", "review": "#FFD180", "blocked": "#FF8A80"}.get(status, "#FFD180")
        title = QtWidgets.QLabel(
            f"{self.report.get('title', 'Review')} - {int(self.report.get('score', 0) or 0)}/100"
        )
        title.setObjectName("slateReadinessTitle")
        title.setStyleSheet(f"color: {color}; font-size: 18pt; font-weight: 700;")
        layout.addWidget(title)

        context = QtWidgets.QLabel(
            f"{self.report.get('sport', '')} {str(self.report.get('mode', '')).title()} | "
            f"{int(self.report.get('players', 0) or 0)} players | "
            f"{int(self.report.get('eligible_players', 0) or 0)} eligible | "
            f"{int(self.report.get('blockers', 0) or 0)} blockers | "
            f"{int(self.report.get('reviews', 0) or 0)} items to review"
        )
        context.setWordWrap(True)
        layout.addWidget(context)

        sources = list(self.report.get("sources") or [])
        if sources:
            source_text = "Data confidence: " + " | ".join(
                f"{source.get('name')} {source.get('confidence')} ({source.get('freshness')})"
                for source in sources
            )
            source_label = QtWidgets.QLabel(source_text)
            source_label.setObjectName("slateReadinessSources")
            source_label.setWordWrap(True)
            source_label.setStyleSheet("color: #AEB7C5;")
            layout.addWidget(source_label)

        checks = list(self.report.get("checks") or [])
        table = QtWidgets.QTableWidget(len(checks), 4, self)
        self.checks = checks
        self.table = table
        table.setObjectName("slateReadinessChecks")
        table.setHorizontalHeaderLabels(["State", "Check", "Finding", "Next step"])
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        for row, check in enumerate(checks):
            check_status = str(check.get("status") or "review")
            state = QtWidgets.QTableWidgetItem(check_status.upper())
            state.setForeground(QtGui.QColor({"pass": "#8FE3A1", "review": "#FFD180", "block": "#FF8A80"}.get(check_status, "#FFD180")))
            state.setTextAlignment(QtCore.Qt.AlignCenter)
            table.setItem(row, 0, state)
            table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(check.get("label") or "")))
            table.setItem(row, 2, QtWidgets.QTableWidgetItem(str(check.get("summary") or "")))
            table.setItem(row, 3, QtWidgets.QTableWidgetItem(str(check.get("action") or "")))
        table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        table.resizeRowsToContents()
        layout.addWidget(table, 1)

        note = QtWidgets.QLabel(
            "This is a report-only preflight. It does not change projections, ownership, locks, fades, or lineups."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        show_players = buttons.addButton("Show Players", QtWidgets.QDialogButtonBox.ActionRole)
        show_players.setObjectName("showReadinessPlayers")
        show_players.setToolTip("Close this report and filter the player table to this finding.")
        show_players.setEnabled(False)
        self.show_players_button = show_players
        show_players.clicked.connect(self._show_selected_players)
        table.currentCellChanged.connect(lambda *_args: self._update_show_players_button())
        table.cellDoubleClicked.connect(lambda *_args: self._show_selected_players())
        copy_button = buttons.addButton("Copy Report", QtWidgets.QDialogButtonBox.ActionRole)
        copy_button.setObjectName("copySlateReadinessReport")
        copy_button.clicked.connect(
            lambda: QtWidgets.QApplication.clipboard().setText(str(self.report.get("text") or ""))
        )
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _selected_check(self) -> Dict[str, Any]:
        row = self.table.currentRow()
        if 0 <= row < len(self.checks):
            return dict(self.checks[row] or {})
        return {}

    def _update_show_players_button(self) -> None:
        details = dict(self._selected_check().get("details") or {})
        self.show_players_button.setEnabled(bool(details.get("player_names")))

    def _show_selected_players(self) -> None:
        check = self._selected_check()
        names = list((check.get("details") or {}).get("player_names") or [])
        if not names:
            return
        parent = self.parent()
        if parent is not None and hasattr(parent, "focus_readiness_players"):
            self.accept()
            parent.focus_readiness_players(check)


class FinalLockCheckDialog(QtWidgets.QDialog):
    """Show the last live refresh and map changes to exact saved lineups."""

    def __init__(self, report: Dict[str, Any], parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.report = dict(report or {})
        self.repair_requested = False
        self.setWindowTitle("Final Lock Check")
        self.setModal(True)
        self.resize(920, 500)

        layout = QtWidgets.QVBoxLayout(self)
        status = str(self.report.get("status") or "attention")
        color = {"ready": "#8FE3A1", "attention": "#FFD180", "unavailable": "#FFB071"}.get(status, "#FFD180")
        title = QtWidgets.QLabel(str(self.report.get("title") or "Saved Lineups Need Review"))
        title.setObjectName("finalLockTitle")
        title.setStyleSheet(f"color: {color}; font-size: 18pt; font-weight: 700;")
        layout.addWidget(title)

        source_text = "cached check" if self.report.get("used_cached_check") else "fresh refresh"
        context = QtWidgets.QLabel(
            f"{source_text.title()} | Players {int(self.report.get('sleeper_matches', 0) or 0)}/"
            f"{int(self.report.get('player_count', 0) or 0)} matched | "
            f"{int(self.report.get('affected_lineups', 0) or 0)}/"
            f"{int(self.report.get('lineup_count', 0) or 0)} saved lineups affected"
        )
        context.setWordWrap(True)
        layout.addWidget(context)

        changes = list(self.report.get("changes") or [])
        table = QtWidgets.QTableWidget(max(1, len(changes)), 5, self)
        table.setObjectName("finalLockChanges")
        table.setHorizontalHeaderLabels(["Player", "Team", "Availability", "Change", "Saved lineups"])
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        if changes:
            for row, change in enumerate(changes):
                values = [
                    str(change.get("name") or "Unknown"),
                    str(change.get("team") or ""),
                    str(change.get("availability") or "Updated"),
                    str(change.get("change") or "Updated"),
                    ", ".join(str(value) for value in change.get("lineup_numbers") or []) or "None",
                ]
                for column, value in enumerate(values):
                    table.setItem(row, column, QtWidgets.QTableWidgetItem(value))
        else:
            item = QtWidgets.QTableWidgetItem("No player-status changes were returned by the final check.")
            table.setItem(0, 0, item)
            table.setSpan(0, 0, 1, 5)
        table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeToContents)
        table.resizeRowsToContents()
        layout.addWidget(table, 1)

        notes: List[str] = []
        if self.report.get("unavailable_players"):
            notes.append(
                "Unavailable players still rostered: "
                + ", ".join(str(value) for value in self.report.get("unavailable_players") or [])
            )
        if status == "unavailable":
            notes.append("The live source could not be confirmed. Continuing uses cached player data and Entry Safety will keep the uncertainty visible.")
        if not notes:
            notes.append("Continue to Entry Safety for the complete roster, salary, team, slate, and portfolio-rule audit.")
        note = QtWidgets.QLabel("\n".join(notes))
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        copy_button = buttons.addButton("Copy Report", QtWidgets.QDialogButtonBox.ActionRole)
        copy_button.setObjectName("copyFinalLockReport")
        copy_button.clicked.connect(
            lambda: QtWidgets.QApplication.clipboard().setText(str(self.report.get("text") or ""))
        )
        if self.report.get("affected_indexes"):
            repair_button = buttons.addButton("Replace Affected Lineups", QtWidgets.QDialogButtonBox.ActionRole)
            repair_button.setObjectName("repairFinalLockLineups")
            repair_button.clicked.connect(self._request_repair)
        continue_button = buttons.addButton(
            "Continue with Cached Data" if status == "unavailable" else "Continue to Entry Safety",
            QtWidgets.QDialogButtonBox.AcceptRole,
        )
        continue_button.setObjectName("continueFinalLockCheck")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _request_repair(self) -> None:
        self.repair_requested = True
        self.done(2)


class EntrySafetyDialog(QtWidgets.QDialog):
    """Final report for the exact saved portfolio about to be exported."""

    def __init__(self, report: Dict[str, Any], parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.report = dict(report or {})
        self.repair_requested = False
        self.setWindowTitle("Entry Safety")
        self.setModal(True)
        self.resize(920, 520)

        layout = QtWidgets.QVBoxLayout(self)
        status = str(self.report.get("status") or "review")
        color = {"ready": "#8FE3A1", "review": "#FFD180", "blocked": "#FF8A80"}.get(status, "#FFD180")
        title = QtWidgets.QLabel(str(self.report.get("title") or "Review Before Export"))
        title.setObjectName("entrySafetyTitle")
        title.setStyleSheet(f"color: {color}; font-size: 18pt; font-weight: 700;")
        layout.addWidget(title)

        context = QtWidgets.QLabel(
            f"{self.report.get('sport', '')} {str(self.report.get('kind', '')).title()} | "
            f"{int(self.report.get('lineup_count', 0) or 0)} saved lineups | "
            f"{int(self.report.get('blockers', 0) or 0)} blockers | "
            f"{int(self.report.get('reviews', 0) or 0)} items to review"
        )
        context.setWordWrap(True)
        layout.addWidget(context)

        checks = sorted(
            list(self.report.get("checks") or []),
            key=lambda item: {"block": 0, "review": 1, "pass": 2}.get(str(item.get("status") or "review"), 1),
        )
        table = QtWidgets.QTableWidget(len(checks), 4, self)
        table.setObjectName("entrySafetyChecks")
        table.setHorizontalHeaderLabels(["State", "Check", "Finding", "Next step"])
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        for row, check in enumerate(checks):
            check_status = str(check.get("status") or "review")
            state = QtWidgets.QTableWidgetItem(check_status.upper())
            state.setForeground(QtGui.QColor(
                {"pass": "#8FE3A1", "review": "#FFD180", "block": "#FF8A80"}.get(check_status, "#FFD180")
            ))
            state.setTextAlignment(QtCore.Qt.AlignCenter)
            table.setItem(row, 0, state)
            table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(check.get("label") or "")))
            table.setItem(row, 2, QtWidgets.QTableWidgetItem(str(check.get("summary") or "")))
            table.setItem(row, 3, QtWidgets.QTableWidgetItem(str(check.get("action") or "")))
        table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        table.resizeRowsToContents()
        layout.addWidget(table, 1)

        note_text = (
            "Resolve every blocker before export. Review items may be intentional, but should be checked before lock. "
            "Nothing changes unless you explicitly choose and confirm replacement."
        )
        note = QtWidgets.QLabel(note_text)
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        copy_button = buttons.addButton("Copy Report", QtWidgets.QDialogButtonBox.ActionRole)
        copy_button.setObjectName("copyEntrySafetyReport")
        copy_button.clicked.connect(
            lambda: QtWidgets.QApplication.clipboard().setText(str(self.report.get("text") or ""))
        )
        if status == "blocked" and self.report.get("blocked_lineup_indexes"):
            repair_button = buttons.addButton("Replace Blocked Lineups", QtWidgets.QDialogButtonBox.ActionRole)
            repair_button.setObjectName("repairBlockedEntrySafetyLineups")
            repair_button.clicked.connect(self._request_repair)
        export_button = buttons.addButton(
            "Export CSV" if status == "ready" else "Export Anyway",
            QtWidgets.QDialogButtonBox.AcceptRole,
        )
        export_button.setObjectName("confirmSafeExport")
        export_button.setEnabled(status != "blocked")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _request_repair(self) -> None:
        self.repair_requested = True
        self.done(2)


class BuildRecipesDialog(QtWidgets.QDialog):
    """Apply or remove named, slate-independent build configurations."""

    def __init__(
        self,
        recipes: Dict[str, Dict[str, Any]],
        parent: Optional[QtWidgets.QWidget] = None,
    ):
        super().__init__(parent)
        self.recipes = dict(recipes or {})
        self.applied_name = ""
        self.changed = False
        self.setWindowTitle("Build Recipes")
        self.setModal(True)
        self.resize(660, 300)

        layout = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel(
            "Apply a saved build setup without bringing old player locks, fades, exposure limits, or groups into the new slate."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.recipe_list = QtWidgets.QListWidget(self)
        self.recipe_list.setObjectName("buildRecipeList")
        self.recipe_list.itemSelectionChanged.connect(self._update_details)
        self.recipe_list.itemDoubleClicked.connect(lambda *_args: self._apply_selected())
        layout.addWidget(self.recipe_list, 1)

        self.details = QtWidgets.QLabel("")
        self.details.setObjectName("buildRecipeDetails")
        self.details.setWordWrap(True)
        self.details.setStyleSheet("color: #AEB7C5;")
        layout.addWidget(self.details)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        delete_button = buttons.addButton("Delete", QtWidgets.QDialogButtonBox.DestructiveRole)
        delete_button.setObjectName("deleteBuildRecipe")
        delete_button.clicked.connect(self._delete_selected)
        apply_button = buttons.addButton("Apply Recipe", QtWidgets.QDialogButtonBox.AcceptRole)
        apply_button.setObjectName("applyBuildRecipe")
        apply_button.clicked.connect(self._apply_selected)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.delete_button = delete_button
        self.apply_button = apply_button
        self._refresh()

    def _selected_name(self) -> str:
        item = self.recipe_list.currentItem()
        return str(item.data(QtCore.Qt.UserRole) or "") if item is not None else ""

    def _refresh(self, selected_name: str = "") -> None:
        self.recipe_list.clear()
        for name, recipe in sorted(self.recipes.items(), key=lambda item: item[0].casefold()):
            label = f"{name}  —  {recipe.get('sport', 'NFL')} {str(recipe.get('contest_kind', 'classic')).title()}"
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.UserRole, name)
            self.recipe_list.addItem(item)
            if name == selected_name:
                self.recipe_list.setCurrentItem(item)
        if self.recipe_list.count() and self.recipe_list.currentRow() < 0:
            self.recipe_list.setCurrentRow(0)
        self._update_details()

    def _update_details(self) -> None:
        name = self._selected_name()
        recipe = dict(self.recipes.get(name) or {})
        enabled = bool(recipe)
        self.apply_button.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)
        if not recipe:
            self.details.setText("No saved recipes yet. Close this window and choose Save Current Recipe from Settings.")
            return
        sim_text = "SIM Off"
        if recipe.get("nfl_sim_enabled"):
            depth = "Deep" if str(recipe.get("nfl_compute_mode") or "").startswith("Deep") else "Fast"
            sim_text = f"SIM {depth} • {recipe.get('nfl_field_preset', '150-Max')}"
        self.details.setText(
            f"{recipe.get('build_style', 'Strategic')} • {recipe.get('salary_strategy', 'Near Cap')} • "
            f"{int(recipe.get('requested_lineups', 1) or 1)} lineups • {sim_text} • "
            f"minimum unique {int(recipe.get('min_unique', 1) or 1)}"
        )

    def _apply_selected(self) -> None:
        name = self._selected_name()
        if not name:
            return
        self.applied_name = name
        self.accept()

    def _delete_selected(self) -> None:
        name = self._selected_name()
        if not name:
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "Delete Build Recipe",
            f"Delete the saved recipe '{name}'?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Cancel,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        self.recipes.pop(name, None)
        self.changed = True
        self._refresh()


class ContestProfileDialog(QtWidgets.QDialog):
    """Create, reuse, or disable an exact contest payout profile."""

    def __init__(
        self,
        profiles: Dict[str, Dict[str, Any]],
        active_name: str = "",
        parent: Optional[QtWidgets.QWidget] = None,
    ):
        super().__init__(parent)
        self.profiles = dict(profiles or {})
        self.active_name = str(active_name or "").strip()
        self.selected_profile: Optional[Dict[str, Any]] = None
        self.changed = False
        self.setWindowTitle("Contest-Aware SIM")
        self.setModal(True)
        self.resize(720, 620)

        layout = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel(
            "Attach the real field size, entry fee, and payout table to NFL SIM Edge. "
            "The selected entry-limit preset still shapes the opponent field; this profile replaces only the payout economics."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        saved_row = QtWidgets.QHBoxLayout()
        saved_row.addWidget(QtWidgets.QLabel("Saved profile"))
        self.profile_combo = QtWidgets.QComboBox(self)
        self.profile_combo.setObjectName("contestProfileSelector")
        self.profile_combo.currentIndexChanged.connect(self._load_selected)
        saved_row.addWidget(self.profile_combo, 1)
        layout.addLayout(saved_row)

        form = QtWidgets.QFormLayout()
        self.name_edit = QtWidgets.QLineEdit(self)
        self.name_edit.setObjectName("contestProfileName")
        self.name_edit.setPlaceholderText("Example: NFL Sunday Million")
        form.addRow("Contest name", self.name_edit)

        self.field_size = QtWidgets.QSpinBox(self)
        self.field_size.setObjectName("contestFieldSize")
        self.field_size.setRange(2, 5_000_000)
        self.field_size.setSingleStep(100)
        self.field_size.setValue(100_000)
        self.field_size.setGroupSeparatorShown(True)
        form.addRow("Field size", self.field_size)

        self.entry_fee = QtWidgets.QDoubleSpinBox(self)
        self.entry_fee.setObjectName("contestEntryFee")
        self.entry_fee.setRange(0.01, 1_000_000.0)
        self.entry_fee.setDecimals(2)
        self.entry_fee.setPrefix("$")
        self.entry_fee.setValue(20.0)
        form.addRow("Entry fee", self.entry_fee)

        self.user_entries = QtWidgets.QSpinBox(self)
        self.user_entries.setObjectName("contestUserEntries")
        self.user_entries.setRange(1, 5_000_000)
        self.user_entries.setValue(150)
        form.addRow("Your entries", self.user_entries)
        layout.addLayout(form)

        payout_label = QtWidgets.QLabel(
            "Payouts — one rank or range per line. Examples: 1 = $100,000 or 2-10 = $5,000"
        )
        payout_label.setWordWrap(True)
        layout.addWidget(payout_label)
        self.payouts_edit = QtWidgets.QPlainTextEdit(self)
        self.payouts_edit.setObjectName("contestPayoutTable")
        self.payouts_edit.setPlaceholderText(
            "1 = $100,000\n2 = $50,000\n3-5 = $20,000\n6-10 = $10,000"
        )
        self.payouts_edit.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        layout.addWidget(self.payouts_edit, 1)

        self.preview = QtWidgets.QLabel("")
        self.preview.setObjectName("contestProfilePreview")
        self.preview.setWordWrap(True)
        self.preview.setStyleSheet("color: #AEB7C5;")
        layout.addWidget(self.preview)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        self.delete_button = buttons.addButton("Delete Saved", QtWidgets.QDialogButtonBox.DestructiveRole)
        self.delete_button.setObjectName("deleteContestProfile")
        self.delete_button.clicked.connect(self._delete_selected)
        preset_button = buttons.addButton("Use Preset Only", QtWidgets.QDialogButtonBox.ActionRole)
        preset_button.setObjectName("disableContestProfile")
        preset_button.setToolTip("Keep NFL SIM Edge on, but return to the preset's payout-shape proxy.")
        preset_button.clicked.connect(self._use_preset_only)
        save_button = buttons.addButton("Save and Use", QtWidgets.QDialogButtonBox.AcceptRole)
        save_button.setObjectName("saveContestProfile")
        save_button.clicked.connect(self._save_and_use)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        for widget in (self.name_edit, self.payouts_edit):
            if isinstance(widget, QtWidgets.QLineEdit):
                widget.textChanged.connect(self._update_preview)
            else:
                widget.textChanged.connect(self._update_preview)
        self.field_size.valueChanged.connect(self._update_preview)
        self.entry_fee.valueChanged.connect(self._update_preview)
        self.user_entries.valueChanged.connect(self._update_preview)
        self._refresh_profiles(self.active_name)

    def _selected_name(self) -> str:
        return str(self.profile_combo.currentData() or "")

    def _refresh_profiles(self, selected_name: str = "") -> None:
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        self.profile_combo.addItem("New profile", "")
        selected_index = 0
        for name in sorted(self.profiles, key=str.casefold):
            self.profile_combo.addItem(name, name)
            if name == selected_name:
                selected_index = self.profile_combo.count() - 1
        self.profile_combo.setCurrentIndex(selected_index)
        self.profile_combo.blockSignals(False)
        self._load_selected()

    def _load_selected(self, *_args: Any) -> None:
        name = self._selected_name()
        profile = dict(self.profiles.get(name) or {})
        self.delete_button.setEnabled(bool(profile))
        if profile:
            self.name_edit.setText(name)
            self.field_size.setValue(int(profile.get("field_size", 100_000) or 100_000))
            self.entry_fee.setValue(float(profile.get("entry_fee", 20.0) or 20.0))
            self.user_entries.setValue(int(profile.get("user_entries", 1) or 1))
            self.payouts_edit.setPlainText(format_payout_text(profile.get("payouts") or []))
        else:
            self.name_edit.clear()
            self.field_size.setValue(100_000)
            self.entry_fee.setValue(20.0)
            self.user_entries.setValue(150)
            self.payouts_edit.clear()
        self._update_preview()

    def _profile_from_fields(self) -> Dict[str, Any]:
        return normalize_contest_profile({
            "name": self.name_edit.text(),
            "field_size": self.field_size.value(),
            "entry_fee": self.entry_fee.value(),
            "user_entries": self.user_entries.value(),
            "payouts": parse_payout_text(self.payouts_edit.toPlainText()),
        })

    def _update_preview(self, *_args: Any) -> None:
        try:
            profile = self._profile_from_fields()
        except ValueError as exc:
            self.preview.setText(str(exc))
            return
        paid_pct = profile["cash_places"] / max(1, profile["field_size"]) * 100.0
        self.preview.setText(
            f"Prize pool entered: ${profile['prize_pool']:,.2f} • pays through {profile['cash_places']:,} "
            f"({paid_pct:.1f}% of field) • top prize ${profile['top_prize']:,.2f}. "
            "Ties and duplicate lineups split all prizes covered by their finishing range."
        )

    def _save_and_use(self) -> None:
        try:
            profile = self._profile_from_fields()
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Contest Profile", str(exc))
            return
        name = str(profile["name"])
        self.profiles[name] = profile
        self.active_name = name
        self.selected_profile = profile
        self.changed = True
        self.accept()

    def _use_preset_only(self) -> None:
        self.active_name = ""
        self.selected_profile = None
        self.changed = True
        self.accept()

    def reject(self) -> None:
        # Deleting a saved profile is an intentional edit even if the user then
        # closes the dialog instead of choosing another profile.
        if self.changed:
            self.accept()
            return
        super().reject()

    def _delete_selected(self) -> None:
        name = self._selected_name()
        if not name:
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "Delete Contest Profile",
            f"Delete the saved contest profile '{name}'?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Cancel,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        self.profiles.pop(name, None)
        if self.active_name == name:
            self.active_name = ""
            self.selected_profile = None
        self.changed = True
        self._refresh_profiles()


class BuildDiagnosticsDialog(QtWidgets.QDialog):
    """Review and copy recent aggregate build diagnostics."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Build History")
        self.setModal(True)
        self.resize(980, 620)

        layout = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel(
            "Recent lineup builds are saved locally so timing and settings can be compared between app releases. "
            "Reports contain aggregate counts only—never player names, lineups, file paths, or API keys."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal, self)
        self.history_list = QtWidgets.QListWidget(splitter)
        self.history_list.setObjectName("buildHistoryList")
        self.history_list.setMinimumWidth(360)
        self.history_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.report = QtWidgets.QPlainTextEdit(splitter)
        self.report.setObjectName("buildHistoryReport")
        self.report.setReadOnly(True)
        self.report.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
        self.report.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        splitter.setSizes([390, 590])
        layout.addWidget(splitter, 1)

        self.note = QtWidgets.QLabel("")
        self.note.setObjectName("buildHistoryNote")
        self.note.setStyleSheet("color: #AEB7C5;")
        layout.addWidget(self.note)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        self.copy_button = buttons.addButton("Copy Selected Report", QtWidgets.QDialogButtonBox.ActionRole)
        self.copy_button.setObjectName("copySelectedBuildReport")
        self.copy_button.clicked.connect(self.copy_selected_report)
        self.compare_button = buttons.addButton("Compare Two Builds", QtWidgets.QDialogButtonBox.ActionRole)
        self.compare_button.setObjectName("compareBuildsButton")
        self.compare_button.setToolTip("Select exactly two build-history rows, then compare their timing, settings, and SIM portfolio metrics.")
        self.compare_button.clicked.connect(self.compare_selected)
        self.clear_button = buttons.addButton("Clear History", QtWidgets.QDialogButtonBox.DestructiveRole)
        self.clear_button.setObjectName("clearBuildHistory")
        self.clear_button.clicked.connect(self.clear_history)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.history_list.currentItemChanged.connect(lambda *_args: self._show_selected())
        self.history_list.itemSelectionChanged.connect(self._update_compare_button)
        self.reload()

    def reload(self) -> None:
        self.history_list.clear()
        records = load_build_history()
        for record in records:
            item = QtWidgets.QListWidgetItem(build_history_label(record))
            item.setData(QtCore.Qt.UserRole, record)
            self.history_list.addItem(item)
        has_records = bool(records)
        self.copy_button.setEnabled(has_records)
        self.clear_button.setEnabled(has_records)
        self.compare_button.setEnabled(False)
        if has_records:
            self.history_list.setCurrentRow(0)
            self.note.setText(f"Showing {len(records)} most recent build{'s' if len(records) != 1 else ''}.")
        else:
            self.report.setPlainText("No completed lineup builds have been recorded yet.")
            self.note.setText("Generate lineups to create the first local build report.")

    def selected_record(self) -> Dict[str, Any]:
        item = self.history_list.currentItem()
        if item is None:
            return {}
        value = item.data(QtCore.Qt.UserRole)
        return dict(value) if isinstance(value, dict) else {}

    def _show_selected(self) -> None:
        record = self.selected_record()
        self.report.setPlainText(format_build_report(record) if record else "Select a build to view its report.")
        self.copy_button.setEnabled(bool(record))

    def _update_compare_button(self) -> None:
        self.compare_button.setEnabled(len(self.history_list.selectedItems()) == 2)

    def compare_selected(self) -> None:
        items = self.history_list.selectedItems()
        if len(items) != 2:
            self.note.setText("Select exactly two build rows to compare.")
            return
        records = [item.data(QtCore.Qt.UserRole) for item in items]
        records = [dict(record) for record in records if isinstance(record, dict)]
        if len(records) != 2:
            return
        self.report.setPlainText(format_build_comparison(records[0], records[1]))
        self.copy_button.setEnabled(True)
        self.note.setText("Comparing two aggregate build reports. Negative total-time change means the newer run was faster.")

    def copy_selected_report(self) -> None:
        report_text = self.report.toPlainText().strip()
        if not report_text:
            return
        QtWidgets.QApplication.clipboard().setText(report_text)
        self.note.setText("Displayed build report copied to the clipboard.")

    def clear_history(self) -> None:
        if not load_build_history(limit=1):
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "Clear Build History",
            "Clear all locally saved build diagnostics? This does not remove exported lineups or contest results.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        clear_build_history()
        parent = self.parent()
        if parent is not None and hasattr(parent, "_on_build_history_cleared"):
            parent._on_build_history_cleared()
        self.reload()


class PortfolioInsightsDialog(QtWidgets.QDialog):
    """Explain a portfolio and return user-selected repair actions to MainWindow."""

    FILTERS = [
        ("All lineups", "all"),
        ("Any review signal", "flagged"),
        ("C/D grades", "weak_grade"),
        ("High duplication", "high_duplication"),
        ("More than $2k left", "unused_salary"),
        ("No QB stack", "unstacked"),
        ("Concentrated core", "concentrated_core"),
    ]

    def __init__(
        self,
        insights: Dict[str, Any],
        parent: Optional[QtWidgets.QWidget] = None,
        *,
        actions_enabled: bool = True,
        source_label: str = "generated",
    ):
        super().__init__(parent)
        self.insights = dict(insights or {})
        self.source_label = str(source_label or "generated")
        self.requested_action = ""
        self.requested_indexes: List[int] = []
        self._lineup_rows = list(self.insights.get("lineup_rows") or [])
        self._lineup_by_number = {
            int(row.get("number", index + 1) or index + 1): row
            for index, row in enumerate(self._lineup_rows)
        }
        self.setObjectName("portfolioInsightsDialog")
        self.setWindowTitle("Portfolio Insights")
        self.setModal(True)
        self.resize(1240, 760)

        layout = QtWidgets.QVBoxLayout(self)
        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Portfolio Insights")
        title.setObjectName("portfolioInsightsTitle")
        title.setStyleSheet("font-size: 18px; font-weight: 700; color: #F1F5F9;")
        header.addWidget(title)
        header.addStretch(1)
        status = QtWidgets.QLabel(str(self.insights.get("status") or "Ready"))
        status.setObjectName("portfolioInsightsStatus")
        has_flags = bool(self.insights.get("review_flags") or self.insights.get("flagged_count"))
        status.setStyleSheet(
            "padding: 5px 10px; border-radius: 9px; font-weight: 700; "
            + ("background: #4A2B12; color: #FFD28A;" if has_flags else "background: #123B31; color: #8BE0C3;")
        )
        header.addWidget(status)
        layout.addLayout(header)

        intro = QtWidgets.QLabel(
            "Find the lineups behind each warning, inspect player exposure, and replace only the rows you do not want."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #AEB7C5;")
        layout.addWidget(intro)

        self.tabs = QtWidgets.QTabWidget(self)
        self.tabs.setObjectName("portfolioInsightsTabs")
        overview = QtWidgets.QPlainTextEdit(self)
        overview.setObjectName("portfolioInsightsReport")
        overview.setReadOnly(True)
        overview.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
        overview.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        overview.setPlainText(str(self.insights.get("text") or "No portfolio insights are available."))
        self.tabs.addTab(overview, "Overview")

        lineup_panel = QtWidgets.QWidget(self)
        lineup_layout = QtWidgets.QVBoxLayout(lineup_panel)
        lineup_layout.setContentsMargins(6, 6, 6, 6)
        filter_row = QtWidgets.QHBoxLayout()
        filter_row.addWidget(QtWidgets.QLabel("Show:"))
        self.filter_combo = QtWidgets.QComboBox(self)
        self.filter_combo.setObjectName("portfolioInsightsFilter")
        for label, code in self.FILTERS:
            self.filter_combo.addItem(label, code)
        self.filter_combo.currentIndexChanged.connect(self._apply_filter)
        filter_row.addWidget(self.filter_combo)
        select_flagged = QtWidgets.QPushButton("Select flagged", self)
        select_flagged.setObjectName("selectFlaggedLineups")
        select_flagged.clicked.connect(self._select_flagged)
        filter_row.addWidget(select_flagged)
        filter_row.addStretch(1)
        filter_row.addWidget(QtWidgets.QLabel("Select one or more rows to remove or replace."))
        lineup_layout.addLayout(filter_row)

        self.table = QtWidgets.QTableWidget(self)
        self.table.setObjectName("portfolioInsightsLineups")
        headers = [
            "#", "Review", "Grade", "Source", "Scenario", "Salary", "Stack", "Bring-back",
            "FLEX", "Own sum", "Edge", "Leverage", "Dup risk", "Top 1%", "Return", "Top paths",
        ]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(self._lineup_rows))
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)

        def metric(value: Any, suffix: str = "") -> str:
            if value is None:
                return "—"
            try:
                return f"{float(value):.1f}{suffix}"
            except (TypeError, ValueError):
                return "—"

        numeric_columns = {
            0: "number", 5: "salary", 9: "ownership", 10: "edge", 11: "leverage",
            12: "duplication", 13: "top_one_pct", 14: "return_index", 15: "top_scenarios",
        }
        for row_index, row in enumerate(self._lineup_rows):
            number = int(row.get("number", row_index + 1) or row_index + 1)
            values = [
                str(number),
                str(row.get("review") or "—"),
                str(row.get("grade") or "—"),
                str(row.get("source") or "—"),
                str(row.get("archetype") or "—"),
                f"${float(row.get('salary', 0.0) or 0.0):,.0f}",
                str(row.get("stack") or "—"),
                str(row.get("bringback") or "—"),
                str(row.get("flex") or "—"),
                metric(row.get("ownership"), "%"),
                metric(row.get("edge")),
                metric(row.get("leverage")),
                metric(row.get("duplication")),
                metric(row.get("top_one_pct"), "%"),
                metric(row.get("return_index")),
                str(int(row.get("top_scenarios", 0) or 0)),
            ]
            for column, value in enumerate(values):
                item = SortKeyItem(value)
                item.setData(QtCore.Qt.UserRole + 1, number)
                numeric = row.get(numeric_columns.get(column, ""))
                if numeric is not None:
                    item.setData(QtCore.Qt.UserRole, float(numeric))
                if column == 1 and row.get("review"):
                    item.setToolTip(str(row.get("review")))
                    item.setForeground(QtGui.QColor("#FFD28A"))
                self.table.setItem(row_index, column, item)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._update_action_buttons)
        lineup_layout.addWidget(self.table, 1)
        self.tabs.addTab(lineup_panel, "Lineup details")

        exposure_panel = QtWidgets.QWidget(self)
        exposure_layout = QtWidgets.QVBoxLayout(exposure_panel)
        exposure_layout.setContentsMargins(6, 6, 6, 6)
        exposure_help = QtWidgets.QLabel(
            "Select a player to see exactly which portfolio rows contain that player."
        )
        exposure_layout.addWidget(exposure_help)
        self.exposure_table = QtWidgets.QTableWidget(self)
        self.exposure_table.setObjectName("portfolioInsightsExposure")
        exposure_headers = ["Player", "Team", "Pos", "Count", "Exposure", "Lineups"]
        self.exposure_table.setColumnCount(len(exposure_headers))
        self.exposure_table.setHorizontalHeaderLabels(exposure_headers)
        exposure_rows = list(self.insights.get("exposure_rows") or [])
        self.exposure_table.setRowCount(len(exposure_rows))
        self.exposure_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.exposure_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.exposure_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.exposure_table.setAlternatingRowColors(True)
        self.exposure_table.verticalHeader().setVisible(False)
        for row_index, row in enumerate(exposure_rows):
            lineup_numbers = [int(value) for value in row.get("lineup_numbers") or []]
            values = [
                str(row.get("name") or ""),
                str(row.get("team") or ""),
                str(row.get("position") or ""),
                str(int(row.get("count", 0) or 0)),
                f"{float(row.get('pct', 0.0) or 0.0):.1f}%",
                ", ".join(str(value) for value in lineup_numbers),
            ]
            for column, value in enumerate(values):
                item = SortKeyItem(value)
                item.setData(QtCore.Qt.UserRole + 1, lineup_numbers)
                numeric = row.get("count") if column == 3 else row.get("pct") if column == 4 else None
                if numeric is not None:
                    item.setData(QtCore.Qt.UserRole, float(numeric))
                self.exposure_table.setItem(row_index, column, item)
        self.exposure_table.setSortingEnabled(True)
        self.exposure_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        self.exposure_table.horizontalHeader().setStretchLastSection(True)
        self.exposure_table.itemDoubleClicked.connect(lambda _item: self._show_player_lineups())
        exposure_layout.addWidget(self.exposure_table, 1)
        show_player = QtWidgets.QPushButton("Show selected player's lineups", self)
        show_player.setObjectName("showExposureLineups")
        show_player.clicked.connect(self._show_player_lineups)
        exposure_layout.addWidget(show_player, 0, QtCore.Qt.AlignRight)
        self.tabs.addTab(exposure_panel, "Player exposure")
        layout.addWidget(self.tabs, 1)

        note = QtWidgets.QLabel(
            "Remove changes only the current in-memory set. Replace keeps every unselected lineup and fills the open slots using the current build settings."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #AEB7C5;")
        layout.addWidget(note)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        copy_button = buttons.addButton("Copy Insights", QtWidgets.QDialogButtonBox.ActionRole)
        copy_button.setObjectName("copyPortfolioInsights")
        copy_button.clicked.connect(
            lambda: QtWidgets.QApplication.clipboard().setText(str(self.insights.get("text") or ""))
        )
        self.remove_button = buttons.addButton("Remove selected", QtWidgets.QDialogButtonBox.ActionRole)
        self.remove_button.setObjectName("removeInsightLineups")
        self.remove_button.clicked.connect(lambda: self._request_action("remove"))
        self.replace_button = buttons.addButton("Replace selected", QtWidgets.QDialogButtonBox.ActionRole)
        self.replace_button.setObjectName("replaceInsightLineups")
        self.replace_button.clicked.connect(lambda: self._request_action("replace"))
        self.remove_button.setVisible(actions_enabled)
        self.replace_button.setVisible(actions_enabled)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_action_buttons()

    def _number_for_table_row(self, row_index: int) -> int:
        item = self.table.item(row_index, 0)
        return int(item.data(QtCore.Qt.UserRole + 1) or 0) if item is not None else 0

    def selected_lineup_indexes(self) -> List[int]:
        numbers = {
            self._number_for_table_row(index.row())
            for index in self.table.selectionModel().selectedRows()
        }
        return sorted(number - 1 for number in numbers if number > 0)

    def _apply_filter(self) -> None:
        code = str(self.filter_combo.currentData() or "all")
        for table_row in range(self.table.rowCount()):
            number = self._number_for_table_row(table_row)
            row = self._lineup_by_number.get(number, {})
            flags = set(row.get("flag_codes") or [])
            visible = code == "all" or (code == "flagged" and bool(flags)) or code in flags
            self.table.setRowHidden(table_row, not visible)

    def _select_flagged(self) -> None:
        self.tabs.setCurrentIndex(1)
        self.filter_combo.setCurrentIndex(self.filter_combo.findData("flagged"))
        self.table.clearSelection()
        selection_model = self.table.selectionModel()
        for table_row in range(self.table.rowCount()):
            number = self._number_for_table_row(table_row)
            if self._lineup_by_number.get(number, {}).get("flag_codes"):
                selection_model.select(
                    self.table.model().index(table_row, 0),
                    QtCore.QItemSelectionModel.Select | QtCore.QItemSelectionModel.Rows,
                )
        self._update_action_buttons()

    def _show_player_lineups(self) -> None:
        selected = self.exposure_table.selectionModel().selectedRows()
        if not selected:
            return
        item = self.exposure_table.item(selected[0].row(), 0)
        lineup_numbers = set(item.data(QtCore.Qt.UserRole + 1) or []) if item is not None else set()
        self.tabs.setCurrentIndex(1)
        self.filter_combo.setCurrentIndex(self.filter_combo.findData("all"))
        self.table.clearSelection()
        selection_model = self.table.selectionModel()
        for table_row in range(self.table.rowCount()):
            number = self._number_for_table_row(table_row)
            self.table.setRowHidden(table_row, number not in lineup_numbers)
            if number in lineup_numbers:
                selection_model.select(
                    self.table.model().index(table_row, 0),
                    QtCore.QItemSelectionModel.Select | QtCore.QItemSelectionModel.Rows,
                )
        self._update_action_buttons()

    def _update_action_buttons(self) -> None:
        enabled = bool(self.selected_lineup_indexes())
        if hasattr(self, "remove_button"):
            self.remove_button.setEnabled(enabled)
        if hasattr(self, "replace_button"):
            self.replace_button.setEnabled(enabled)

    def _request_action(self, action: str) -> None:
        indexes = self.selected_lineup_indexes()
        if not indexes:
            QtWidgets.QMessageBox.information(self, "Select Lineups", "Select one or more lineup rows first.")
            return
        self.requested_action = str(action or "")
        self.requested_indexes = indexes
        self.accept()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DFS Optimizer")
        self.resize(1450, 900)

        self.players: List[Dict[str, Any]] = []
        self.last_showdown: List[Dict[str, Any]] = []
        self.last_classic: List[List[Dict[str, Any]]] = []

        self.saved_showdown: List[Dict[str, Any]] = []
        self.saved_classic: List[List[Dict[str, Any]]] = []
        self.portfolio_groups: List[Dict[str, Any]] = []
        self.last_portfolio_report: Dict[str, Any] = {}
        self.last_sim_report: Dict[str, Any] = {}
        self.last_live_check_summary: Dict[str, Any] = {}
        self.last_readiness_report: Dict[str, Any] = {}
        self.last_final_lock_report: Dict[str, Any] = {}
        self.last_entry_safety_report: Dict[str, Any] = {}
        self.last_build_timing_report: Dict[str, Any] = {}
        history = load_build_history(limit=1)
        self.last_build_diagnostic: Dict[str, Any] = dict(history[0]) if history else {}
        self._active_build_context: Dict[str, Any] = {}
        self._readiness_filter_names: set[str] = set()
        self._lineup_space_phase = ""
        self._last_live_check_epoch = 0.0
        self._active_recipe_name = ""
        self.app_settings = QtCore.QSettings("DFS Optimizer", "DFS Optimizer")
        self._active_contest_profile_name = str(
            self.app_settings.value("contest/active_profile_name", "") or ""
        ).strip()

        self._build_ui()

    # ---------------- UI ----------------

    def _build_ui(self) -> None:
        root = QtWidgets.QSplitter(QtCore.Qt.Horizontal, self)
        root.setObjectName("workspaceSplitter")
        self.workspace_splitter = root
        self.setCentralWidget(root)

        # Left panel
        left = QtWidgets.QWidget(self)
        left_layout = QtWidgets.QVBoxLayout(left)


        # Top controls (multi-row so buttons/toggles don't scrunch)
        top_box = QtWidgets.QVBoxLayout()

        # --- Row 1: load + injuries + ownership sim ---
        row1 = QtWidgets.QHBoxLayout()

        btn_load = QtWidgets.QPushButton("Load Player CSV")
        btn_load.setText("Load CSV")
        btn_load.setToolTip("Load a DraftKings player salary CSV.")
        btn_load.clicked.connect(self.on_load_csv)
        row1.addWidget(btn_load)

        btn_refresh_inj = QtWidgets.QPushButton("Game-Day Check")
        btn_refresh_inj.setObjectName("gameDayCheckButton")
        btn_refresh_inj.setToolTip("Check current NFL availability, injuries, practice participation, and depth-chart roles.")
        btn_refresh_inj.clicked.connect(self.on_refresh_injuries)
        row1.addWidget(btn_refresh_inj)

        btn_learning = QtWidgets.QPushButton("Results and Learning")
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

        live_row = QtWidgets.QHBoxLayout()
        self.lbl_live_data = QtWidgets.QLabel("Live player data: not checked")
        self.lbl_live_data.setObjectName("liveDataStatusLabel")
        self.lbl_live_data.setWordWrap(True)
        self.lbl_live_data.setStyleSheet("color: #AEB7C5; padding: 1px 3px;")
        self.lbl_live_data.setToolTip("Loads automatically with an NFL salary file and is checked again when stale before generation.")
        live_row.addWidget(self.lbl_live_data, 1)
        top_box.addLayout(live_row)

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
            "Randomized: mostly projection/value, while retaining anti-correlation safety rails. "
            "With NFL SIM Edge off, it also keeps the broad player pool, including deep backups.\n"
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
            "Near Cap: NFL prefers $49k+ on a $50k cap.\n"
            "Maximize Salary: NFL usually uses $49.5k+.\n"
            "Balanced Spend: allows around $47k+.\n"
            "Salary Leverage: allows more unused salary for contrarian builds."
        )
        row2.addWidget(self.combo_salary_strategy)

        row2.addSpacing(18)
        self.chk_nfl_contest_sim = QtWidgets.QCheckBox("NFL SIM Edge")
        self.chk_nfl_contest_sim.setObjectName("nflSimEdgeCheck")
        self.chk_nfl_contest_sim.setChecked(True)
        self.chk_nfl_contest_sim.setToolTip(
            "NFL Classic only: rank candidate lineups against correlated outcome scenarios\n"
            "and a representative field, then select a diversified SIM Edge portfolio."
        )
        row2.addWidget(self.chk_nfl_contest_sim)

        row2.addWidget(QtWidgets.QLabel("Scenarios:"))
        self.spin_nfl_sim_scenarios = QtWidgets.QSpinBox()
        self.spin_nfl_sim_scenarios.setObjectName("nflSimScenarios")
        self.spin_nfl_sim_scenarios.setRange(250, 5000)
        self.spin_nfl_sim_scenarios.setSingleStep(250)
        self.spin_nfl_sim_scenarios.setValue(750)
        self.spin_nfl_sim_scenarios.setToolTip(
            "More scenarios stabilize SIM Edge estimates but take longer. 750 is the Fast default.\n"
            "Deep uses this as a minimum and independently validates with at least 2,500 scenarios."
        )
        row2.addWidget(self.spin_nfl_sim_scenarios)

        self.lbl_field_preset = QtWidgets.QLabel("Contest preset")
        self.combo_field_preset = QtWidgets.QComboBox()
        self.combo_field_preset.setObjectName("nflFieldPreset")
        self.combo_field_preset.addItems(["Single Entry", "3-Max", "20-Max", "150-Max"])
        self.combo_field_preset.setCurrentText("150-Max")
        self.combo_field_preset.setToolTip(
            "Shapes the simulated opponent field for the contest's entry limit.\n"
            "Complete imported standings can refine this preset after the learning guardrails are met."
        )
        row2.addWidget(self.lbl_field_preset)
        row2.addWidget(self.combo_field_preset)

        self.lbl_nfl_compute_mode = QtWidgets.QLabel("Build depth")
        self.combo_nfl_compute_mode = QtWidgets.QComboBox()
        self.combo_nfl_compute_mode.setObjectName("nflComputeMode")
        self.combo_nfl_compute_mode.addItems(["Fast (default)", "Deep (up to 5 min)"])
        self.combo_nfl_compute_mode.setCurrentText("Fast (default)")
        self.combo_nfl_compute_mode.setToolTip(
            "Fast uses the normal candidate bank and selected scenario count.\n"
            "Deep spends up to five minutes exploring thousands of candidates, then screens,\n"
            "independently validates, and locally refines the final portfolio. NFL Classic only."
        )
        self.chk_nfl_contest_sim.toggled.connect(self.combo_nfl_compute_mode.setEnabled)

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
        btn_max_exposure.setToolTip("Set a maximum total portfolio exposure % for selected players.")
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

        # --- Row 4: portfolio-wide exposure and diversity rules ---
        row4 = QtWidgets.QHBoxLayout()
        row4.addWidget(QtWidgets.QLabel("Portfolio:"))

        btn_min_cpt = QtWidgets.QPushButton("Min CPT%")
        btn_min_cpt.setToolTip("Set a minimum Captain exposure for selected players in Showdown portfolios.")
        btn_min_cpt.clicked.connect(lambda: self.set_min_pct(kind="cpt"))
        row4.addWidget(btn_min_cpt)

        btn_min_exposure = QtWidgets.QPushButton("Min Exposure%")
        btn_min_exposure.setToolTip("Set a minimum total portfolio exposure for selected players.")
        btn_min_exposure.clicked.connect(lambda: self.set_min_pct(kind="exposure"))
        row4.addWidget(btn_min_exposure)

        btn_clear_min = QtWidgets.QPushButton("Clear Min%")
        btn_clear_min.clicked.connect(self.clear_min_pct)
        row4.addWidget(btn_clear_min)

        row4.addSpacing(8)
        row4.addWidget(QtWidgets.QLabel("Min unique:"))
        self.spin_portfolio_unique = QtWidgets.QSpinBox()
        self.spin_portfolio_unique.setObjectName("portfolioMinUnique")
        self.spin_portfolio_unique.setRange(1, 5)
        self.spin_portfolio_unique.setValue(2)
        self.spin_portfolio_unique.setToolTip("Minimum different players between lineups; relaxes safely only if necessary.")
        row4.addWidget(self.spin_portfolio_unique)

        row4.addWidget(QtWidgets.QLabel("Team max:"))
        self.spin_team_exposure = QtWidgets.QDoubleSpinBox()
        self.spin_team_exposure.setObjectName("portfolioTeamMax")
        self.spin_team_exposure.setRange(1.0, 100.0)
        self.spin_team_exposure.setDecimals(0)
        self.spin_team_exposure.setSuffix("%")
        self.spin_team_exposure.setValue(100.0)
        row4.addWidget(self.spin_team_exposure)

        row4.addWidget(QtWidgets.QLabel("Game max:"))
        self.spin_game_exposure = QtWidgets.QDoubleSpinBox()
        self.spin_game_exposure.setObjectName("portfolioGameMax")
        self.spin_game_exposure.setRange(1.0, 100.0)
        self.spin_game_exposure.setDecimals(0)
        self.spin_game_exposure.setSuffix("%")
        self.spin_game_exposure.setValue(100.0)
        row4.addWidget(self.spin_game_exposure)

        self.chk_portfolio_balance = QtWidgets.QCheckBox("Balance ownership/dup risk")
        self.chk_portfolio_balance.setObjectName("portfolioBalance")
        self.chk_portfolio_balance.setChecked(True)
        row4.addWidget(self.chk_portfolio_balance)

        btn_group_one = QtWidgets.QPushButton("Group: At Least 1")
        btn_group_one.setToolTip("Require at least one of the selected players in every lineup.")
        btn_group_one.clicked.connect(lambda: self.add_portfolio_group("at_least_one"))
        row4.addWidget(btn_group_one)

        btn_group_never = QtWidgets.QPushButton("Group: Never Together")
        btn_group_never.setToolTip("Allow at most one of the selected players in any lineup.")
        btn_group_never.clicked.connect(lambda: self.add_portfolio_group("never_together"))
        row4.addWidget(btn_group_never)

        btn_groups_clear = QtWidgets.QPushButton("Clear Groups")
        btn_groups_clear.clicked.connect(self.clear_portfolio_groups)
        row4.addWidget(btn_groups_clear)

        self.lbl_portfolio_groups = QtWidgets.QLabel("Groups: 0")
        self.lbl_portfolio_groups.setObjectName("portfolioGroupCount")
        row4.addWidget(self.lbl_portfolio_groups)
        row4.addStretch(1)
        top_box.addLayout(row4)

        # Compact command bar. The detailed controls still exist below, but the
        # most common workflow now fits in a single, predictable row.
        command_bar = QtWidgets.QFrame(self)
        command_bar.setObjectName("compactCommandBar")
        command_layout = QtWidgets.QHBoxLayout(command_bar)
        command_layout.setContentsMargins(10, 7, 10, 7)
        command_layout.setSpacing(8)

        brand = QtWidgets.QLabel("DFS")
        brand.setObjectName("workspaceBrand")
        command_layout.addWidget(brand)
        command_layout.addSpacing(8)
        command_layout.addWidget(btn_load)
        command_layout.addWidget(btn_refresh_inj)
        self.btn_slate_readiness = QtWidgets.QPushButton("Slate Readiness")
        self.btn_slate_readiness.setObjectName("slateReadinessButton")
        self.btn_slate_readiness.setToolTip(
            "Refresh stale NFL news, then audit projections, ownership, roles, locks, salary use, and preset fit."
        )
        self.btn_slate_readiness.clicked.connect(self.on_slate_readiness)
        command_layout.addWidget(self.btn_slate_readiness)
        command_layout.addSpacing(8)
        command_layout.addWidget(QtWidgets.QLabel("Sport"))
        command_layout.addWidget(self.combo_sport)

        self.lbl_workspace_summary = QtWidgets.QLabel("")
        self.lbl_workspace_summary.setObjectName("workspaceSummary")
        self.lbl_workspace_summary.setToolTip(
            "Current build recipe. Use Settings > Show Build Controls to change it."
        )
        command_layout.addWidget(self.lbl_workspace_summary)
        command_layout.addStretch(1)

        settings_button = QtWidgets.QToolButton(self)
        settings_button.setObjectName("workspaceSettingsButton")
        settings_button.setText("Settings")
        settings_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        settings_menu = QtWidgets.QMenu(settings_button)
        settings_menu.addSection("Data")
        settings_menu.addAction("Results and Learning", self.on_results_learning)
        settings_menu.addSection("Build Recipes")
        save_recipe_action = settings_menu.addAction("Save Current Recipe...", self.on_save_build_recipe)
        save_recipe_action.setObjectName("saveBuildRecipeAction")
        manage_recipes_action = settings_menu.addAction("Apply or Delete Recipes...", self.on_manage_build_recipes)
        manage_recipes_action.setObjectName("manageBuildRecipesAction")
        settings_menu.addSection("Contest")
        contest_profile_action = settings_menu.addAction("Contest-Aware SIM...", self.on_contest_profiles)
        contest_profile_action.setObjectName("contestAwareSimAction")
        settings_menu.addSection("Review")
        portfolio_insights_action = settings_menu.addAction("Portfolio Insights...", self.on_portfolio_summary)
        portfolio_insights_action.setObjectName("portfolioInsightsAction")
        self.action_copy_build_report = settings_menu.addAction(
            "Copy Last Build Report", self.copy_last_build_report
        )
        self.action_copy_build_report.setObjectName("copyLastBuildReportAction")
        self.action_copy_build_report.setEnabled(bool(self.last_build_diagnostic))
        build_history_action = settings_menu.addAction("Build History...", self.on_build_history)
        build_history_action.setObjectName("buildHistoryAction")

        settings_menu.addSection("Workspace")
        self.action_show_build_controls = settings_menu.addAction("Show Build Controls")
        self.action_show_build_controls.setObjectName("showBuildControlsAction")
        self.action_show_build_controls.setCheckable(True)
        self.action_show_build_controls.setChecked(False)
        self.action_show_build_controls.toggled.connect(self._set_build_controls_visible)

        self.action_show_saved_portfolio = settings_menu.addAction("Show Saved Portfolio")
        self.action_show_saved_portfolio.setObjectName("showSavedPortfolioAction")
        self.action_show_saved_portfolio.setCheckable(True)
        self.action_show_saved_portfolio.setChecked(False)
        self.action_show_saved_portfolio.toggled.connect(self._set_saved_portfolio_visible)
        settings_button.setMenu(settings_menu)
        command_layout.addWidget(settings_button)

        self.btn_primary_build = QtWidgets.QPushButton("Generate")
        self.btn_primary_build.setObjectName("primaryGenerateButton")
        self.btn_primary_build.setToolTip("Generate lineups for the selected lineup tab.")
        self.btn_primary_build.clicked.connect(self._on_primary_build)
        command_layout.addWidget(self.btn_primary_build)
        left_layout.addWidget(command_bar)

        live_strip = QtWidgets.QFrame(self)
        live_strip.setObjectName("liveStatusStrip")
        live_strip_layout = QtWidgets.QHBoxLayout(live_strip)
        live_strip_layout.setContentsMargins(8, 2, 8, 2)
        live_strip_layout.addWidget(self.lbl_live_data, 1)
        self.lbl_lineup_space = QtWidgets.QLabel("Lineup space: load a slate")
        self.lbl_lineup_space.setObjectName("lineupSpaceStatus")
        self.lbl_lineup_space.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.lbl_lineup_space.setStyleSheet("color: #C7D2E3; padding: 1px 6px;")
        live_strip_layout.addWidget(self.lbl_lineup_space)
        self.btn_clear_readiness_filter = QtWidgets.QToolButton(self)
        self.btn_clear_readiness_filter.setObjectName("clearReadinessFilterButton")
        self.btn_clear_readiness_filter.setText("Clear player filter")
        self.btn_clear_readiness_filter.setToolTip("Show the full player pool again.")
        self.btn_clear_readiness_filter.clicked.connect(self.clear_readiness_player_filter)
        self.btn_clear_readiness_filter.setVisible(False)
        live_strip_layout.addWidget(self.btn_clear_readiness_filter)
        self.lbl_readiness = QtWidgets.QLabel("Readiness: load a slate")
        self.lbl_readiness.setObjectName("slateReadinessStatus")
        self.lbl_readiness.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.lbl_readiness.setStyleSheet("color: #AEB7C5; padding: 1px 3px;")
        live_strip_layout.addWidget(self.lbl_readiness)
        left_layout.addWidget(live_strip)

        # Secondary controls are grouped by intent instead of competing for
        # space in four permanent button rows.
        self.tabs_workspace_controls = QtWidgets.QTabWidget(self)
        self.tabs_workspace_controls.setObjectName("workspaceControlTabs")
        self.tabs_workspace_controls.setMaximumHeight(184)

        strategy_panel = QtWidgets.QWidget(self)
        strategy_grid = QtWidgets.QGridLayout(strategy_panel)
        strategy_grid.setContentsMargins(10, 7, 10, 7)
        strategy_grid.setHorizontalSpacing(8)
        strategy_grid.setVerticalSpacing(6)
        strategy_grid.addWidget(QtWidgets.QLabel("Build style"), 0, 0)
        strategy_grid.addWidget(self.combo_build_style, 0, 1)
        strategy_grid.addWidget(QtWidgets.QLabel("Ownership mode"), 0, 2)
        strategy_grid.addWidget(self.combo_build_own_mode, 0, 3)
        strategy_grid.addWidget(QtWidgets.QLabel("Weight"), 1, 0)
        strategy_grid.addWidget(self.spin_build_own_weight, 1, 1)
        strategy_grid.addWidget(QtWidgets.QLabel("Salary use"), 1, 2)
        strategy_grid.addWidget(self.combo_salary_strategy, 1, 3)
        strategy_grid.addWidget(QtWidgets.QLabel("Ownership sims"), 2, 0)
        strategy_grid.addWidget(self.spin_own_sims, 2, 1)
        strategy_grid.addWidget(self.chk_sd_template_sim, 2, 2)
        strategy_grid.addWidget(btn_own_sim, 2, 3)
        self.lbl_mlb_stack_pref = QtWidgets.QLabel("MLB stack")
        strategy_grid.addWidget(self.lbl_mlb_stack_pref, 3, 0)
        strategy_grid.addWidget(self.combo_mlb_stack_pref, 3, 1)
        strategy_grid.addWidget(self.lbl_field_preset, 3, 2)
        strategy_grid.addWidget(self.combo_field_preset, 3, 3)
        strategy_grid.addWidget(self.chk_nfl_contest_sim, 3, 4)
        self.lbl_nfl_scenarios = QtWidgets.QLabel("Scenarios")
        strategy_grid.addWidget(self.lbl_nfl_scenarios, 3, 5)
        strategy_grid.addWidget(self.spin_nfl_sim_scenarios, 3, 6)
        strategy_grid.addWidget(self.lbl_nfl_compute_mode, 4, 4)
        strategy_grid.addWidget(self.combo_nfl_compute_mode, 4, 5, 1, 2)
        strategy_grid.setColumnStretch(7, 1)
        self.tabs_workspace_controls.addTab(strategy_panel, "Build Strategy")

        portfolio_panel = QtWidgets.QWidget(self)
        portfolio_grid = QtWidgets.QGridLayout(portfolio_panel)
        portfolio_grid.setContentsMargins(10, 7, 10, 7)
        portfolio_grid.setHorizontalSpacing(8)
        portfolio_grid.setVerticalSpacing(6)
        portfolio_grid.addWidget(QtWidgets.QLabel("Minimum unique"), 0, 0)
        portfolio_grid.addWidget(self.spin_portfolio_unique, 0, 1)
        portfolio_grid.addWidget(self.chk_portfolio_balance, 0, 2, 1, 2)
        portfolio_grid.addWidget(QtWidgets.QLabel("Team maximum"), 1, 0)
        portfolio_grid.addWidget(self.spin_team_exposure, 1, 1)
        portfolio_grid.addWidget(QtWidgets.QLabel("Game maximum"), 1, 2)
        portfolio_grid.addWidget(self.spin_game_exposure, 1, 3)
        portfolio_grid.addWidget(btn_group_one, 2, 0)
        portfolio_grid.addWidget(btn_group_never, 2, 1)
        portfolio_grid.addWidget(btn_groups_clear, 2, 2)
        portfolio_grid.addWidget(self.lbl_portfolio_groups, 2, 3)
        portfolio_grid.setColumnStretch(4, 1)
        self.tabs_workspace_controls.addTab(portfolio_panel, "Portfolio Rules")

        data_panel = QtWidgets.QWidget(self)
        data_grid = QtWidgets.QGridLayout(data_panel)
        data_grid.setContentsMargins(10, 7, 10, 7)
        data_grid.setHorizontalSpacing(8)
        data_grid.setVerticalSpacing(6)
        data_grid.addWidget(btn_learning, 0, 0)
        data_grid.addWidget(btn_clear_team_adj, 0, 1)
        data_grid.addWidget(btn_save_tags, 1, 0)
        data_grid.addWidget(btn_load_tags, 1, 1)
        data_grid.addWidget(btn_load_mlb, 2, 0)
        data_grid.addWidget(btn_clear_mlb, 2, 1)
        data_grid.addWidget(btn_load_order, 3, 0)
        data_grid.addWidget(btn_clear_order, 3, 1)
        data_grid.setColumnStretch(3, 1)
        btn_clear_team_adj.setVisible(True)
        btn_save_tags.setVisible(True)
        btn_load_tags.setVisible(True)
        self._mlb_data_controls = [btn_load_mlb, btn_clear_mlb, btn_load_order, btn_clear_order]
        self.tabs_workspace_controls.addTab(data_panel, "Data and Learning")
        left_layout.addWidget(self.tabs_workspace_controls)
        self.tabs_workspace_controls.setVisible(False)


        # Player table
        self.tbl_players = QtWidgets.QTableWidget(self)
        self.tbl_players.setColumnCount(23)
        self.tbl_players.setHorizontalHeaderLabels(["Name", "Team", "Pos", "Status", "Salary", "BaseProj", "AdjProj", "NFL±", "Usage", "Matchup", "Role", "Wx", "", "TeamAdj", "Tags", "Own% Tot", "MaxCPT%", "MinCPT%", "Max%", "Min%", "Order", "Bats", "Conf"] )
        self.tbl_players.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl_players.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl_players.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        # Enable click-to-sort without changing the visual layout.
        self.tbl_players.setSortingEnabled(True)
        # Allow user to drag column widths; responsive defaults are applied
        # after sport-specific columns are shown.
        self.tbl_players.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        self.tbl_players.setAlternatingRowColors(True)
        self.tbl_players.verticalHeader().setDefaultSectionSize(27)
        self.tbl_players.verticalHeader().setVisible(False)
        self.tbl_players.horizontalHeader().setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.tbl_players.horizontalHeader().customContextMenuRequested.connect(self._show_player_columns_menu)
        self.tbl_players.setToolTip("Right-click a column heading to show or hide player fields.")

        player_area = QtWidgets.QSplitter(QtCore.Qt.Horizontal, self)
        player_area.setObjectName("playerWorkspaceSplitter")
        player_area.addWidget(self.tbl_players)

        self.player_inspector = QtWidgets.QGroupBox("Selected player", self)
        self.player_inspector.setObjectName("playerInspector")
        inspector_layout = QtWidgets.QVBoxLayout(self.player_inspector)
        inspector_layout.setContentsMargins(10, 11, 10, 10)
        inspector_layout.setSpacing(7)
        self.lbl_player_inspector_title = QtWidgets.QLabel("Select a player")
        self.lbl_player_inspector_title.setObjectName("playerInspectorTitle")
        self.lbl_player_inspector_title.setWordWrap(True)
        inspector_layout.addWidget(self.lbl_player_inspector_title)
        self.lbl_player_inspector_meta = QtWidgets.QLabel("Player actions will appear here.")
        self.lbl_player_inspector_meta.setObjectName("playerInspectorMeta")
        self.lbl_player_inspector_meta.setWordWrap(True)
        inspector_layout.addWidget(self.lbl_player_inspector_meta)

        status_group = QtWidgets.QGroupBox("Lineup status", self.player_inspector)
        status_group.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
        status_grid = QtWidgets.QGridLayout(status_group)
        status_grid.setContentsMargins(7, 7, 7, 6)
        status_grid.setVerticalSpacing(3)
        status_grid.addWidget(btn_lock, 0, 0)
        status_grid.addWidget(btn_fade, 0, 1)
        status_grid.addWidget(btn_cpt_lock, 1, 0)
        status_grid.addWidget(btn_cpt_fade, 1, 1)
        status_grid.addWidget(btn_clear, 2, 0, 1, 2)
        inspector_layout.addWidget(status_group)

        exposure_group = QtWidgets.QGroupBox("Portfolio exposure", self.player_inspector)
        exposure_group.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
        exposure_grid = QtWidgets.QGridLayout(exposure_group)
        exposure_grid.setContentsMargins(7, 7, 7, 6)
        exposure_grid.setVerticalSpacing(3)
        exposure_grid.addWidget(btn_max_exposure, 0, 0)
        exposure_grid.addWidget(btn_min_exposure, 0, 1)
        exposure_grid.addWidget(btn_max_cpt, 1, 0)
        exposure_grid.addWidget(btn_min_cpt, 1, 1)
        exposure_grid.addWidget(btn_clear_max, 2, 0)
        exposure_grid.addWidget(btn_clear_min, 2, 1)
        exposure_grid.addWidget(btn_copy_own_to_max, 3, 0, 1, 2)
        inspector_layout.addWidget(exposure_group)

        btn_max_exposure.setText("Set Max %")
        btn_min_exposure.setText("Set Min %")
        btn_max_cpt.setText("Set CPT Max")
        btn_min_cpt.setText("Set CPT Min")
        btn_clear_max.setText("Clear Max %")
        btn_clear_min.setText("Clear Min %")
        btn_copy_own_to_max.setText("Use Own% as Max")

        team_group = QtWidgets.QGroupBox("Team adjustment", self.player_inspector)
        team_group.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
        team_layout = QtWidgets.QHBoxLayout(team_group)
        team_layout.setContentsMargins(7, 7, 7, 6)
        btn_team_boost.setText("Boost Team")
        btn_team_fade.setText("Fade Team")
        team_layout.addWidget(btn_team_boost)
        team_layout.addWidget(btn_team_fade)
        inspector_layout.addWidget(team_group)
        inspector_layout.addStretch(1)

        self._player_action_buttons = [
            btn_lock, btn_fade, btn_cpt_lock, btn_cpt_fade, btn_clear,
            btn_max_exposure, btn_min_exposure, btn_max_cpt, btn_min_cpt,
            btn_clear_max, btn_clear_min, btn_team_boost, btn_team_fade,
        ]
        self._captain_action_buttons = [btn_cpt_lock, btn_cpt_fade, btn_max_cpt, btn_min_cpt]
        for button in self._player_action_buttons + [btn_copy_own_to_max]:
            button.setMinimumHeight(22)
        self.player_inspector.setMinimumWidth(270)
        self.player_inspector.setMaximumWidth(330)
        player_area.addWidget(self.player_inspector)
        player_area.setSizes([690, 290])
        player_area.setStretchFactor(0, 1)
        player_area.setStretchFactor(1, 0)
        self.tbl_players.itemSelectionChanged.connect(self._update_player_inspector)
        left_layout.addWidget(player_area, 2)

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
        self.spin_sd.valueChanged.connect(self._update_lineup_space_dashboard)
        sd_controls.addWidget(self.spin_sd)

        sd_controls.addWidget(QtWidgets.QLabel("Cap:"))
        self.edit_sd_cap = QtWidgets.QLineEdit("50000")
        self.edit_sd_cap.setFixedWidth(90)
        sd_controls.addWidget(self.edit_sd_cap)

        btn_sd_save_all = QtWidgets.QPushButton("Save All")
        btn_sd_save_all.clicked.connect(self.on_sd_save_all)
        sd_controls.addWidget(btn_sd_save_all)

        btn_sd_unsave_all = QtWidgets.QPushButton("Unsave")
        btn_sd_unsave_all.clicked.connect(self.on_sd_unsave_all)
        sd_controls.addWidget(btn_sd_unsave_all)

        btn_export_sd = QtWidgets.QPushButton("Export CSV")
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
        self.tbl_sd.setAlternatingRowColors(True)
        self.tbl_sd.verticalHeader().setVisible(False)
        self.tbl_sd.verticalHeader().setDefaultSectionSize(27)
        self.tbl_sd.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        self._fit_lineup_table_columns(self.tbl_sd)
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
        self.spin_cl.valueChanged.connect(self._update_lineup_space_dashboard)
        cl_controls.addWidget(self.spin_cl)

        cl_controls.addWidget(QtWidgets.QLabel("Cap:"))
        self.edit_cl_cap = QtWidgets.QLineEdit("50000")
        self.edit_cl_cap.setFixedWidth(90)
        cl_controls.addWidget(self.edit_cl_cap)

        btn_cl_save_all = QtWidgets.QPushButton("Save All")
        btn_cl_save_all.clicked.connect(self.on_cl_save_all)
        cl_controls.addWidget(btn_cl_save_all)

        btn_cl_unsave_all = QtWidgets.QPushButton("Unsave")
        btn_cl_unsave_all.clicked.connect(self.on_cl_unsave_all)
        cl_controls.addWidget(btn_cl_unsave_all)

        btn_export_cl = QtWidgets.QPushButton("Export CSV")
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
        self.tbl_cl.setAlternatingRowColors(True)
        self.tbl_cl.verticalHeader().setVisible(False)
        self.tbl_cl.verticalHeader().setDefaultSectionSize(27)
        self.tbl_cl.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        self._fit_lineup_table_columns(self.tbl_cl)
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
        self.tabs_lineups.currentChanged.connect(self._on_lineup_tab_changed)
        root.addWidget(left)

        # Right panel (Saved)
        right = QtWidgets.QWidget(self)
        right.setObjectName("savedPortfolioPanel")
        self.saved_portfolio_panel = right
        right_layout = QtWidgets.QVBoxLayout(right)

        self.lbl_saved = QtWidgets.QLabel("Saved: 0 showdown | 0 classic")
        self.lbl_saved.setAlignment(QtCore.Qt.AlignCenter)
        right_layout.addWidget(self.lbl_saved)

        btn_clear_saved = QtWidgets.QPushButton("Clear All Saved")
        btn_clear_saved.clicked.connect(self.on_clear_saved)

        btn_view_exposure = QtWidgets.QPushButton("Exposure")
        btn_view_exposure.setToolTip("Show player exposure based on the lineups currently saved on the right.")
        btn_view_exposure.clicked.connect(self.on_view_exposure)

        btn_portfolio_summary = QtWidgets.QPushButton("Portfolio Insights")
        btn_portfolio_summary.setObjectName("portfolioSummaryButton")
        btn_portfolio_summary.setToolTip(
            "Explain quality grades, candidate sources, stacks, salary, ownership, duplication risk, concentration, and scenario coverage."
        )
        btn_portfolio_summary.clicked.connect(self.on_portfolio_summary)
        btn_portfolio_summary.setText("Insights")

        btn_view_stack_exp = QtWidgets.QPushButton("Stack Exposure Dashboard")
        btn_view_stack_exp.setToolTip("Show saved-lineup team, stack-shape, salary-band, and pitcher exposure.")
        btn_view_stack_exp.clicked.connect(self.on_view_stack_exposure)

        saved_actions = QtWidgets.QHBoxLayout()
        saved_actions.setSpacing(6)
        saved_actions.addWidget(btn_portfolio_summary)
        saved_actions.addWidget(btn_view_exposure)
        saved_more = QtWidgets.QToolButton(self)
        saved_more.setText("More")
        saved_more.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        saved_more_menu = QtWidgets.QMenu(saved_more)
        saved_more_menu.addAction("Stack Exposure Dashboard", self.on_view_stack_exposure)
        saved_more_menu.addSeparator()
        saved_more_menu.addAction("Clear All Saved", self.on_clear_saved)
        saved_more.setMenu(saved_more_menu)
        saved_actions.addWidget(saved_more)
        right_layout.addLayout(saved_actions)

        self.tbl_saved_sd = CopyRowTableWidget(self)
        self.tbl_saved_sd.setColumnCount(6)
        self.tbl_saved_sd.setHorizontalHeaderLabels(["CPT", "FLEX1", "FLEX2", "FLEX3", "FLEX4", "FLEX5"])
        self.tbl_saved_sd.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl_saved_sd.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl_saved_sd.setAlternatingRowColors(True)
        self.tbl_saved_sd.verticalHeader().setVisible(False)
        self.tbl_saved_sd.verticalHeader().setDefaultSectionSize(27)
        self.tbl_saved_sd.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)

        self.tbl_saved_cl = CopyRowTableWidget(self)
        self.tbl_saved_cl.setColumnCount(9)
        self.tbl_saved_cl.setHorizontalHeaderLabels(["QB", "RB1", "RB2", "WR1", "WR2", "WR3", "TE", "FLEX", "DST"])
        self.tbl_saved_cl.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl_saved_cl.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl_saved_cl.setAlternatingRowColors(True)
        self.tbl_saved_cl.verticalHeader().setVisible(False)
        self.tbl_saved_cl.verticalHeader().setDefaultSectionSize(27)
        self.tbl_saved_cl.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)

        self.tabs_saved = QtWidgets.QTabWidget(self)
        self.tabs_saved.setObjectName("savedLineupTabs")
        self.tabs_saved.addTab(self.tbl_saved_sd, "Showdown")
        self.tabs_saved.addTab(self.tbl_saved_cl, "Classic")
        self.tabs_saved.setToolTip("Ctrl+C copies selected saved lineup rows.")
        right_layout.addWidget(self.tabs_saved, 1)

        root.addWidget(right)
        right.setVisible(self.action_show_saved_portfolio.isChecked())
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

        # Small, scoped accents layer on top of the application's existing dark
        # palette without changing dialog or table behavior.
        self.setStyleSheet(
            "#compactCommandBar { background: #171C25; border: 1px solid #30394A; border-radius: 8px; }"
            "#workspaceBrand { color: #F6F8FA; font-size: 11pt; font-weight: 700; letter-spacing: 1px; }"
            "#workspaceSummary { color: #AEB7C5; padding: 0 8px; }"
            "#primaryGenerateButton { background: #2F6FED; border-color: #4D83F3; font-weight: 700; padding: 6px 14px; }"
            "#primaryGenerateButton:hover { background: #3B78EE; }"
            "#liveStatusStrip { background: #111722; border-left: 3px solid #2F6FED; border-radius: 3px; }"
            "#playerInspectorTitle { font-size: 12pt; font-weight: 700; color: #F6F8FA; }"
            "#playerInspectorMeta { color: #AEB7C5; }"
        )
        self._on_sport_changed(self._current_sport())
        self._on_lineup_tab_changed(self.tabs_lineups.currentIndex())
        self._update_player_inspector()
        self.chk_nfl_contest_sim.toggled.connect(self._update_lineup_space_dashboard)
        self.combo_build_style.currentTextChanged.connect(self._update_lineup_space_dashboard)
        self.combo_build_style.currentTextChanged.connect(self._update_workspace_summary)
        self.combo_salary_strategy.currentTextChanged.connect(self._update_workspace_summary)
        self.combo_field_preset.currentTextChanged.connect(self._update_workspace_summary)
        self.combo_nfl_compute_mode.currentTextChanged.connect(self._update_workspace_summary)
        self.chk_nfl_contest_sim.toggled.connect(self._update_workspace_summary)
        self._update_workspace_summary()
        self._update_lineup_space_dashboard()

    def _set_build_controls_visible(self, visible: bool) -> None:
        """Reveal detailed controls only when the user asks for them."""
        if hasattr(self, "tabs_workspace_controls"):
            self.tabs_workspace_controls.setVisible(bool(visible))

    @staticmethod
    def _fit_lineup_table_columns(table: QtWidgets.QTableWidget) -> None:
        """Use the output width while keeping summary fields compact and aligned."""
        header = table.horizontalHeader()
        if table.columnCount() <= 0:
            return
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Fixed)
        header.resizeSection(0, 64)
        save_header = table.horizontalHeaderItem(0)
        if save_header is not None:
            save_header.setTextAlignment(int(QtCore.Qt.AlignCenter))
        for column in range(1, table.columnCount()):
            header_item = table.horizontalHeaderItem(column)
            label = header_item.text() if header_item is not None else ""
            if label == "TotalSal":
                header.setSectionResizeMode(column, QtWidgets.QHeaderView.Fixed)
                header.resizeSection(column, 88)
                header_item.setTextAlignment(int(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter))
            elif label in {"Grade", "SIM Edge"}:
                header.setSectionResizeMode(column, QtWidgets.QHeaderView.Fixed)
                header.resizeSection(column, 126 if label == "SIM Edge" else 92)
                header_item.setTextAlignment(int(QtCore.Qt.AlignCenter))
            else:
                header.setSectionResizeMode(column, QtWidgets.QHeaderView.Stretch)
                if header_item is not None:
                    header_item.setTextAlignment(int(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter))

    @staticmethod
    def _player_column_alignment(column: int) -> int:
        """Match cell alignment to the kind of information in each column."""
        if column in {0, 3, 10, 14}:
            return int(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        if column in {1, 2, 20, 21, 22}:
            return int(QtCore.Qt.AlignCenter)
        return int(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

    def _fit_player_table_columns(self) -> None:
        """Fit visible player fields predictably without oversized text columns."""
        if not hasattr(self, "tbl_players"):
            return
        table = self.tbl_players
        header = table.horizontalHeader()
        header.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        header.setMinimumSectionSize(38)
        header.setStretchLastSection(False)

        preferred = {
            0: 168, 1: 48, 2: 42, 3: 104, 4: 64, 5: 68, 6: 64,
            7: 58, 8: 58, 9: 68, 10: 108, 11: 46, 12: 74,
            13: 66, 14: 100, 15: 74, 16: 76, 17: 76, 18: 64,
            19: 64, 20: 54, 21: 48, 22: 54,
        }
        visible = [
            column for column in range(table.columnCount())
            if not table.isColumnHidden(column)
        ]
        for column in visible:
            header.resizeSection(column, preferred.get(column, 64))
            header_item = table.horizontalHeaderItem(column)
            if header_item is not None:
                header_item.setTextAlignment(self._player_column_alignment(column))

        # Share any spare width among descriptive fields. This keeps Role near
        # its values while letting names, injuries, and tags breathe.
        available = max(0, table.viewport().width() - 2)
        used = sum(header.sectionSize(column) for column in visible)
        spare = max(0, available - used)
        elastic = [(0, 0.40), (3, 0.15), (10, 0.20), (14, 0.25)]
        elastic = [(column, weight) for column, weight in elastic if column in visible]
        weight_total = sum(weight for _, weight in elastic)
        distributed = 0
        for index, (column, weight) in enumerate(elastic):
            if index == len(elastic) - 1:
                addition = spare - distributed
            else:
                addition = int(spare * weight / max(weight_total, 1e-9))
                distributed += addition
            header.resizeSection(column, preferred[column] + max(0, addition))

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "tbl_players"):
            QtCore.QTimer.singleShot(0, self._fit_player_table_columns)

    def _set_saved_portfolio_visible(self, visible: bool) -> None:
        """Let the main player and lineup workspace use the full window."""
        if hasattr(self, "saved_portfolio_panel"):
            self.saved_portfolio_panel.setVisible(bool(visible))
        if visible and hasattr(self, "workspace_splitter"):
            sizes = self.workspace_splitter.sizes()
            if len(sizes) == 2 and sizes[1] <= 0:
                total = max(1, sum(sizes))
                self.workspace_splitter.setSizes([max(650, total - 360), 360])

    def _update_workspace_summary(self, *_args: Any) -> None:
        """Keep the engine's active recipe visible while its controls are folded away."""
        if not hasattr(self, "lbl_workspace_summary"):
            return
        style = self.combo_build_style.currentText() if hasattr(self, "combo_build_style") else "Strategic"
        salary = self.combo_salary_strategy.currentText() if hasattr(self, "combo_salary_strategy") else "Near Cap"
        parts = [style, salary]
        if self._current_sport() == "NFL" and self._contest_mode() == "classic":
            if self.chk_nfl_contest_sim.isChecked():
                depth = "Deep" if self.combo_nfl_compute_mode.currentText().startswith("Deep") else "Fast"
                parts.extend([f"SIM {depth}", self.combo_field_preset.currentText()])
                contest_profile = self._active_contest_profile()
                if contest_profile:
                    parts.append(f"ROI {contest_profile['name']}")
            else:
                parts.append("SIM Off")
        if self._active_recipe_name:
            parts.insert(0, self._active_recipe_name)
        self.lbl_workspace_summary.setText(" | ".join(parts))

    def _load_build_recipes(self) -> Dict[str, Dict[str, Any]]:
        try:
            return load_recipes_json(self.app_settings.value("build/recipes_json", "{}"))
        except Exception:
            logger.exception("Saved build recipes could not be loaded")
            return {}

    def _store_build_recipes(self, recipes: Dict[str, Dict[str, Any]]) -> None:
        self.app_settings.setValue("build/recipes_json", dump_recipes_json(recipes))
        self.app_settings.sync()

    def _load_contest_profiles(self) -> Dict[str, Dict[str, Any]]:
        try:
            return load_profiles_json(self.app_settings.value("contest/profiles_json", "{}"))
        except Exception:
            logger.exception("Saved contest profiles could not be loaded")
            return {}

    def _store_contest_profiles(
        self,
        profiles: Dict[str, Dict[str, Any]],
        active_name: str,
    ) -> None:
        cleaned = load_profiles_json(dump_profiles_json(profiles))
        selected = str(active_name or "").strip()
        if selected not in cleaned:
            selected = ""
        self.app_settings.setValue("contest/profiles_json", dump_profiles_json(cleaned))
        self.app_settings.setValue("contest/active_profile_name", selected)
        self.app_settings.sync()
        self._active_contest_profile_name = selected

    def _active_contest_profile(self) -> Optional[Dict[str, Any]]:
        name = str(getattr(self, "_active_contest_profile_name", "") or "").strip()
        if not name:
            return None
        profile = self._load_contest_profiles().get(name)
        return dict(profile) if profile else None

    def on_contest_profiles(self) -> None:
        profiles = self._load_contest_profiles()
        dialog = ContestProfileDialog(
            profiles,
            str(getattr(self, "_active_contest_profile_name", "") or ""),
            self,
        )
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return
        if dialog.changed:
            self._store_contest_profiles(dialog.profiles, dialog.active_name)
        if dialog.active_name:
            self.chk_nfl_contest_sim.setChecked(True)
            profile = dict(dialog.profiles.get(dialog.active_name) or {})
            self.status.showMessage(
                f"Contest-Aware SIM active: {dialog.active_name} • {int(profile.get('field_size', 0) or 0):,} entries • "
                f"${float(profile.get('entry_fee', 0.0) or 0.0):,.2f} entry.",
                7000,
            )
        else:
            self.status.showMessage("Contest profile disabled; NFL SIM Edge will use the preset payout proxy.", 6000)
        self._update_workspace_summary()

    def _current_build_recipe(self) -> Dict[str, Any]:
        kind = self._contest_mode()
        requested = self.spin_sd.value() if kind == "showdown" else self.spin_cl.value()
        cap_text = self.edit_sd_cap.text() if kind == "showdown" else self.edit_cl_cap.text()
        return normalize_recipe({
            "sport": self._current_sport(),
            "contest_kind": kind,
            "requested_lineups": requested,
            "salary_cap": self._safe_float(cap_text, 50000.0),
            "ownership_sims": self.spin_own_sims.value(),
            "showdown_field_templates": self.chk_sd_template_sim.isChecked(),
            "ownership_mode": self.combo_build_own_mode.currentText(),
            "ownership_weight": self.spin_build_own_weight.value(),
            "build_style": self.combo_build_style.currentText(),
            "mlb_stack_preference": self.combo_mlb_stack_pref.currentText(),
            "salary_strategy": self.combo_salary_strategy.currentText(),
            "nfl_sim_enabled": self.chk_nfl_contest_sim.isChecked(),
            "nfl_sim_scenarios": self.spin_nfl_sim_scenarios.value(),
            "nfl_field_preset": self.combo_field_preset.currentText(),
            "nfl_compute_mode": self.combo_nfl_compute_mode.currentText(),
            "min_unique": self.spin_portfolio_unique.value(),
            "team_max_pct": self.spin_team_exposure.value(),
            "game_max_pct": self.spin_game_exposure.value(),
            "balance_ownership": self.chk_portfolio_balance.isChecked(),
        })

    @staticmethod
    def _set_recipe_combo(combo: QtWidgets.QComboBox, value: Any) -> None:
        text = str(value or "").strip()
        index = combo.findText(text)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _apply_build_recipe(self, name: str, recipe: Dict[str, Any]) -> None:
        value = normalize_recipe(recipe)
        self._set_recipe_combo(self.combo_sport, value.get("sport"))
        kind = str(value.get("contest_kind") or "classic")
        self.tabs_lineups.setCurrentIndex(0 if kind == "showdown" else 1)
        requested = int(value.get("requested_lineups", 1) or 1)
        salary_cap = float(value.get("salary_cap", 50000.0) or 50000.0)
        if kind == "showdown":
            self.spin_sd.setValue(requested)
            self.edit_sd_cap.setText(f"{salary_cap:.0f}")
        else:
            self.spin_cl.setValue(requested)
            self.edit_cl_cap.setText(f"{salary_cap:.0f}")

        if "ownership_sims" in value:
            self.spin_own_sims.setValue(int(value["ownership_sims"]))
        if "showdown_field_templates" in value:
            self.chk_sd_template_sim.setChecked(bool(value["showdown_field_templates"]))
        self._set_recipe_combo(self.combo_build_own_mode, value.get("ownership_mode"))
        if "ownership_weight" in value:
            self.spin_build_own_weight.setValue(float(value["ownership_weight"]))
        self._set_recipe_combo(self.combo_build_style, value.get("build_style"))
        self._set_recipe_combo(self.combo_mlb_stack_pref, value.get("mlb_stack_preference"))
        self._set_recipe_combo(self.combo_salary_strategy, value.get("salary_strategy"))
        if "nfl_sim_enabled" in value:
            self.chk_nfl_contest_sim.setChecked(bool(value["nfl_sim_enabled"]))
        if "nfl_sim_scenarios" in value:
            self.spin_nfl_sim_scenarios.setValue(int(value["nfl_sim_scenarios"]))
        self._set_recipe_combo(self.combo_field_preset, value.get("nfl_field_preset"))
        self._set_recipe_combo(self.combo_nfl_compute_mode, value.get("nfl_compute_mode"))
        if "min_unique" in value:
            self.spin_portfolio_unique.setValue(int(value["min_unique"]))
        if "team_max_pct" in value:
            self.spin_team_exposure.setValue(float(value["team_max_pct"]))
        if "game_max_pct" in value:
            self.spin_game_exposure.setValue(float(value["game_max_pct"]))
        if "balance_ownership" in value:
            self.chk_portfolio_balance.setChecked(bool(value["balance_ownership"]))
        self._active_recipe_name = str(name or "").strip()
        self._update_workspace_summary()
        self._update_lineup_space_dashboard()
        self.status.showMessage(f"Applied build recipe: {self._active_recipe_name}.", 5000)

    def on_save_build_recipe(self) -> None:
        recipe = self._current_build_recipe()
        default_name = self._active_recipe_name or (
            f"{recipe.get('sport', 'NFL')} {recipe.get('nfl_field_preset', 'Classic')}"
            if recipe.get("contest_kind") == "classic"
            else f"{recipe.get('sport', 'NFL')} Showdown"
        )
        name, ok = QtWidgets.QInputDialog.getText(
            self,
            "Save Build Recipe",
            "Recipe name:",
            QtWidgets.QLineEdit.Normal,
            default_name,
        )
        name = str(name or "").strip()
        if not ok or not name:
            return
        recipes = self._load_build_recipes()
        if name in recipes:
            answer = QtWidgets.QMessageBox.question(
                self,
                "Replace Build Recipe",
                f"Replace the existing recipe '{name}' with the current build settings?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
                QtWidgets.QMessageBox.Cancel,
            )
            if answer != QtWidgets.QMessageBox.Yes:
                return
        recipes[name] = recipe
        self._store_build_recipes(recipes)
        self._active_recipe_name = name
        self._update_workspace_summary()
        self.status.showMessage(f"Saved build recipe: {name}.", 5000)

    def on_manage_build_recipes(self) -> None:
        recipes = self._load_build_recipes()
        dialog = BuildRecipesDialog(recipes, self)
        result = dialog.exec_()
        if dialog.changed:
            self._store_build_recipes(dialog.recipes)
            if self._active_recipe_name not in dialog.recipes:
                self._active_recipe_name = ""
                self._update_workspace_summary()
        if result != QtWidgets.QDialog.Accepted or not dialog.applied_name:
            return
        recipe = dict(dialog.recipes.get(dialog.applied_name) or {})
        requested_sport = str(recipe.get("sport") or "NFL").upper()
        if requested_sport != self._current_sport() and (self.last_classic or self.saved_classic):
            answer = QtWidgets.QMessageBox.question(
                self,
                "Change Sport and Apply Recipe",
                "Changing sport clears the current Classic generated and saved lineups. Apply this recipe?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
                QtWidgets.QMessageBox.Cancel,
            )
            if answer != QtWidgets.QMessageBox.Yes:
                return
        self._apply_build_recipe(dialog.applied_name, recipe)

    def _on_primary_build(self) -> None:
        """Run the action represented by the primary command-bar button."""
        index = self.tabs_lineups.currentIndex() if hasattr(self, "tabs_lineups") else 0
        if index == 0:
            self.on_build_showdown()
        elif index == 1:
            self.on_build_classic()
        else:
            self._refresh_best_stacks_table()

    def _on_lineup_tab_changed(self, index: int) -> None:
        sport = self._current_sport()
        if index == 0:
            self.btn_primary_build.setText("Generate")
            self.btn_primary_build.setToolTip("Generate Showdown lineups.")
        elif index == 1:
            self.btn_primary_build.setText("Generate")
            self.btn_primary_build.setToolTip(f"Generate {sport} Classic lineups.")
        else:
            self.btn_primary_build.setText("Refresh Stacks")
            self.btn_primary_build.setToolTip("Refresh the MLB best-stack report.")

        if hasattr(self, "tabs_saved") and index in (0, 1):
            self.tabs_saved.setCurrentIndex(index)

        self._apply_player_column_visibility(sport)
        self._update_sport_controls(sport)
        self._update_player_inspector()
        self._update_readiness_badge()
        self._lineup_space_phase = ""
        self._update_workspace_summary()
        self._update_lineup_space_dashboard()

    def _update_sport_controls(self, sport: str) -> None:
        """Keep sport-only choices out of the way until they apply."""
        sport_u = (sport or "NFL").strip().upper()
        is_mlb = sport_u == "MLB"
        is_nfl_classic = sport_u == "NFL" and self._contest_mode() == "classic"
        is_showdown = self._contest_mode() == "showdown"

        for control in getattr(self, "_mlb_data_controls", []):
            control.setVisible(is_mlb)
        if hasattr(self, "lbl_mlb_stack_pref"):
            self.lbl_mlb_stack_pref.setVisible(is_mlb)
            self.combo_mlb_stack_pref.setVisible(is_mlb)
        if hasattr(self, "chk_nfl_contest_sim"):
            self.chk_nfl_contest_sim.setVisible(is_nfl_classic)
            self.lbl_nfl_scenarios.setVisible(is_nfl_classic)
            self.spin_nfl_sim_scenarios.setVisible(is_nfl_classic)
            self.lbl_field_preset.setVisible(is_nfl_classic)
            self.combo_field_preset.setVisible(is_nfl_classic)
            self.lbl_nfl_compute_mode.setVisible(is_nfl_classic)
            self.combo_nfl_compute_mode.setVisible(is_nfl_classic)
            self.combo_nfl_compute_mode.setEnabled(
                is_nfl_classic and self.chk_nfl_contest_sim.isChecked()
            )
        if hasattr(self, "chk_sd_template_sim"):
            self.chk_sd_template_sim.setVisible(is_showdown)

        if hasattr(self, "tabs_lineups"):
            stack_index = 2
            if hasattr(self.tabs_lineups, "setTabVisible"):
                self.tabs_lineups.setTabVisible(stack_index, is_mlb)
            else:
                self.tabs_lineups.setTabEnabled(stack_index, is_mlb)

    def _default_player_columns(self, sport: str) -> set:
        """Return the useful-at-a-glance player columns for this workspace."""
        sport_u = (sport or "NFL").strip().upper()
        # Exposure limits live in the selected-player inspector. They remain
        # available from the header's column menu when side-by-side comparison
        # is useful, but no longer consume permanent table width.
        visible = {0, 1, 2, 3, 4, 6, 14, 15}
        if sport_u == "NFL":
            visible.update({7, 8, 9, 10, 11})
        elif sport_u == "MLB":
            visible.update({7, 8, 9, 10, 11, 12, 13, 20, 21, 22})
        return visible

    def _apply_player_column_visibility(self, sport: str) -> None:
        if not hasattr(self, "tbl_players"):
            return
        visible = self._default_player_columns(sport)
        for column in range(self.tbl_players.columnCount()):
            self.tbl_players.setColumnHidden(column, column not in visible)
        self._fit_player_table_columns()

    def _show_player_columns_menu(self, point: QtCore.QPoint) -> None:
        """Allow any compacted column to be restored on demand."""
        menu = QtWidgets.QMenu(self)
        reset_action = menu.addAction("Use sport defaults")
        reset_action.triggered.connect(
            lambda: self._apply_player_column_visibility(self._current_sport())
        )
        menu.addSeparator()
        for column in range(self.tbl_players.columnCount()):
            header_item = self.tbl_players.horizontalHeaderItem(column)
            label = header_item.text() if header_item is not None else f"Column {column + 1}"
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(not self.tbl_players.isColumnHidden(column))
            action.toggled.connect(
                lambda checked, col=column: self.tbl_players.setColumnHidden(col, not checked)
            )
        header = self.tbl_players.horizontalHeader()
        menu.exec_(header.mapToGlobal(point))

    def _update_player_inspector(self) -> None:
        if not hasattr(self, "lbl_player_inspector_title"):
            return
        rows = self._get_selected_player_rows()
        has_selection = bool(rows)
        for button in getattr(self, "_player_action_buttons", []):
            button.setEnabled(has_selection)

        is_showdown = self._contest_mode() == "showdown"
        for button in getattr(self, "_captain_action_buttons", []):
            button.setVisible(is_showdown)

        if not rows:
            self.player_inspector.setTitle("Selected player")
            self.lbl_player_inspector_title.setText("Select a player")
            self.lbl_player_inspector_meta.setText(
                "Choose one or more rows to lock, fade, cap exposure, or adjust a team."
            )
            return

        if len(rows) > 1:
            selected = [self.players[row] for row in rows]
            teams = sorted({str(player.get("Team") or "").strip() for player in selected if player.get("Team")})
            self.player_inspector.setTitle("Selected players")
            self.lbl_player_inspector_title.setText(f"{len(rows)} players selected")
            team_text = ", ".join(teams[:4]) + ("…" if len(teams) > 4 else "")
            self.lbl_player_inspector_meta.setText(
                "Actions apply to every selected player."
                + (f"\nTeams: {team_text}" if team_text else "")
            )
            return

        player = self.players[rows[0]]
        name = str(player.get("Name") or "Unknown player")
        team = str(player.get("Team") or "—")
        position = str(player.get("Position") or "—")
        salary = int(float(player.get("FlexSalary", 0) or 0))
        projection = float(player.get("FlexProjection", 0.0) or 0.0)
        ownership = float(player.get("ProjOwnPct", 0.0) or 0.0)
        status = str(player.get("InjuryStatus") or player.get("NFLAvailability") or "No status flag")
        tags = self._tags_to_text(player) or "No lineup tags"
        min_pct = player.get("MinPct")
        max_pct = player.get("MaxPct")
        exposure_text = (
            f"Exposure {float(min_pct):.0f}–{float(max_pct):.0f}%"
            if min_pct not in (None, "") and max_pct not in (None, "")
            else f"Exposure max {float(max_pct):.0f}%"
            if max_pct not in (None, "")
            else f"Exposure min {float(min_pct):.0f}%"
            if min_pct not in (None, "")
            else "No exposure limits"
        )
        if is_showdown:
            min_cpt = player.get("MinCptPct")
            max_cpt = player.get("MaxCptPct")
            if min_cpt not in (None, "") and max_cpt not in (None, ""):
                exposure_text += f" · CPT {float(min_cpt):.0f}–{float(max_cpt):.0f}%"
            elif max_cpt not in (None, ""):
                exposure_text += f" · CPT max {float(max_cpt):.0f}%"
            elif min_cpt not in (None, ""):
                exposure_text += f" · CPT min {float(min_cpt):.0f}%"
        self.player_inspector.setTitle("Selected player")
        self.lbl_player_inspector_title.setText(name)
        self.lbl_player_inspector_meta.setText(
            f"{team} · {position} · ${salary:,}\n"
            f"Projection {projection:.2f} · Own {ownership:.1f}%\n"
            f"{status} · {tags}\n{exposure_text}"
        )

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

    def _portfolio_rules(self) -> Dict[str, Any]:
        constraints = {}
        for player in self.players:
            if not any(player.get(field) not in (None, "") for field in ("MinPct", "MaxPct", "MinCptPct", "MaxCptPct")):
                continue
            key = player_key(player)
            if not key:
                continue
            constraints[key] = {
                "Name": str(player.get("Name") or key),
                "MinPct": player.get("MinPct"),
                "MaxPct": player.get("MaxPct"),
                "MinCptPct": player.get("MinCptPct"),
                "MaxCptPct": player.get("MaxCptPct"),
            }
        return {
            "min_unique": int(self.spin_portfolio_unique.value()),
            "max_team_pct": float(self.spin_team_exposure.value()),
            "max_game_pct": float(self.spin_game_exposure.value()),
            "balance_ownership": bool(self.chk_portfolio_balance.isChecked()),
            "groups": list(self.portfolio_groups),
            "player_constraints": constraints,
        }

    def set_min_pct(self, *, kind: str) -> None:
        rows = self._get_selected_player_rows()
        if not rows:
            self.status.showMessage("Select one or more players first.", 3000)
            return
        kind_u = str(kind or "exposure").strip().lower()
        key = "MinCptPct" if kind_u == "cpt" else "MinPct"
        label = "Min CPT%" if kind_u == "cpt" else "Min Exposure%"
        current = [self.players[row].get(key) for row in rows]
        values = [float(value) for value in current if value not in (None, "")]
        default = values[0] if values and all(abs(value - values[0]) < 1e-9 for value in values) else 10.0
        value, ok = QtWidgets.QInputDialog.getDouble(
            self,
            f"Set {label}",
            f"{label} (0-100). Minimums are prioritized across the complete portfolio and reported if infeasible.",
            default,
            0.0,
            100.0,
            1,
        )
        if not ok:
            return
        for row in rows:
            self.players[row][key] = float(value)
        self._refresh_players_table()
        self.status.showMessage(f"Set {label} = {value:.1f}% for {len(rows)} players.", 5000)

    def clear_min_pct(self) -> None:
        rows = self._get_selected_player_rows()
        if not rows:
            self.status.showMessage("Select one or more players first.", 3000)
            return
        for row in rows:
            self.players[row]["MinCptPct"] = None
            self.players[row]["MinPct"] = None
        self._refresh_players_table()
        self.status.showMessage(f"Cleared minimum exposure for {len(rows)} players.", 4000)

    def add_portfolio_group(self, group_type: str) -> None:
        rows = self._get_selected_player_rows()
        minimum = 1 if group_type == "at_least_one" else 2
        if len(rows) < minimum:
            self.status.showMessage(f"Select at least {minimum} player(s) for this group.", 4000)
            return
        selected = [self.players[row] for row in rows]
        keys = sorted({player_key(player) for player in selected if player_key(player)})
        names = [str(player.get("Name") or player_key(player)) for player in selected]
        if len(keys) < minimum:
            return
        label = ("At least one: " if group_type == "at_least_one" else "Never together: ") + ", ".join(names)
        group = {"type": group_type, "player_keys": keys, "label": label}
        if group not in self.portfolio_groups:
            self.portfolio_groups.append(group)
        self.lbl_portfolio_groups.setText(f"Groups: {len(self.portfolio_groups)}")
        self.lbl_portfolio_groups.setToolTip("\n".join(item.get("label", "") for item in self.portfolio_groups))
        self.status.showMessage(label, 5000)

    def clear_portfolio_groups(self) -> None:
        self.portfolio_groups.clear()
        self.lbl_portfolio_groups.setText("Groups: 0")
        self.lbl_portfolio_groups.setToolTip("")
        self.status.showMessage("Cleared portfolio player groups.", 3000)

    def set_max_pct(self, *, kind: str) -> None:
        """Set max ownership/exposure percent for selected players.

        - kind="cpt": sets MaxCptPct (Captain slot cap) — used in Showdown only.
        - kind="exposure": sets MaxPct, the total portfolio appearance cap.

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
            help_text = "Caps total appearances across the selected portfolio."

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
        """Return the exact DraftKings ID for the requested roster slot."""
        if not p:
            return ""
        slot_u = (slot or "").upper()
        if slot_u in ("CPT", "CAPTAIN"):
            return str(p.get("CptID") or "").strip()
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
            self.last_showdown = []
            self.last_classic = []
            self.last_portfolio_report = {}
            self.last_sim_report = {}
            self.last_readiness_report = {}
            self.last_final_lock_report = {}
            self.last_build_timing_report = {}
            self._active_build_context = {}
            self._readiness_filter_names.clear()
            self._lineup_space_phase = ""
            self.last_live_check_summary = {}
            self._last_live_check_epoch = 0.0
            if hasattr(self, "tbl_sd"):
                self.tbl_sd.setRowCount(0)
            if hasattr(self, "tbl_cl"):
                self.tbl_cl.setRowCount(0)
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
                p.setdefault("MinCptPct", None)
                p.setdefault("MinPct", None)
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
                    self._record_live_check(ctx)
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
            self._update_readiness_badge()
        except Exception as e:
            logger.exception("Failed to load CSV")
            traceback.print_exc()
            QtWidgets.QMessageBox.critical(self, "Load Error", str(e))
        finally:
            self._finish_load_progress()

    def _calculate_slate_readiness(self) -> Dict[str, Any]:
        sport = self._current_sport()
        mode = self._contest_mode()
        if mode == "showdown":
            cap = self._safe_float(self.edit_sd_cap.text(), 50000.0)
            lineups: List[Any] = list(self.last_showdown)
        else:
            cap = self._safe_float(self.edit_cl_cap.text(), 50000.0)
            lineups = list(self.last_classic)
        preset_name = self.combo_field_preset.currentText() if hasattr(self, "combo_field_preset") else "150-Max"
        calibration: Dict[str, Any] = {}
        if sport == "NFL" and mode == "classic":
            try:
                calibration = load_nfl_field_calibration(preset_name)
            except Exception:
                logger.exception("Slate readiness could not load NFL field calibration")
        selected_preset = (
            nfl_field_preset(preset_name, calibration)
            if sport == "NFL" and mode == "classic"
            else {"name": "", "min_salary_pct": 0.90 if mode == "showdown" else 0.94}
        )
        report = audit_slate(
            self.players,
            sport=sport,
            mode=mode,
            salary_cap=cap,
            field_preset=selected_preset,
            live_summary=self.last_live_check_summary,
            generated_lineups=lineups,
            sim_report=self.last_sim_report if mode == "classic" else {},
        )
        self.last_readiness_report = report
        return report

    def _calculate_lineup_space(self) -> Dict[str, Any]:
        mode = self._contest_mode()
        sport = self._current_sport()
        requested = 0
        if mode == "showdown" and hasattr(self, "spin_sd"):
            requested = int(self.spin_sd.value())
        elif hasattr(self, "spin_cl"):
            requested = int(self.spin_cl.value())

        pool = list(self.players or [])
        pool_label = "active player pool"
        sim_enabled = bool(
            hasattr(self, "chk_nfl_contest_sim") and self.chk_nfl_contest_sim.isChecked()
        )
        build_style = (
            self.combo_build_style.currentText()
            if hasattr(self, "combo_build_style")
            else "Strategic"
        )
        use_role_pool = should_use_nfl_role_pool(
            sport=sport,
            kind=mode,
            build_style=build_style,
            sim_enabled=sim_enabled,
        )
        if use_role_pool and pool:
            def has_minimum_exposure(player: Dict[str, Any]) -> bool:
                try:
                    return (
                        float(player.get("MinPct") or 0.0) > 0.0
                        or float(player.get("MinCptPct") or 0.0) > 0.0
                    )
                except (TypeError, ValueError):
                    return False

            required_role_pool_keys = {
                player_key(player)
                for player in pool
                if has_minimum_exposure(player)
            }
            required_role_pool_keys.update(
                str(key)
                for group in self.portfolio_groups
                if str(group.get("type") or "") == "at_least_one"
                for key in group.get("player_keys") or []
            )
            pool = build_nfl_role_pool(
                pool,
                preserve_locks=True,
                preserve_player_keys=required_role_pool_keys,
            )
            pool_label = "NFL starter/rotation pool"
        return calculate_lineup_space(
            pool,
            sport=sport,
            mode=mode,
            requested=requested,
            loaded_total=len(self.players or []),
            pool_label=pool_label,
        )

    def _update_lineup_space_dashboard(self, *_args: Any) -> None:
        if not hasattr(self, "lbl_lineup_space"):
            return
        if not self.players:
            self.lbl_lineup_space.setText("Lineup space: load a slate")
            self.lbl_lineup_space.setToolTip(
                "Load a salary file to see how fades, inactive players, locks, and the NFL role pool shrink the build space."
            )
            return
        report = self._calculate_lineup_space()
        count = int(report.get("structural_combinations", 0) or 0)
        count_prefix = "" if report.get("exact") else "≤"
        text = (
            f"Space: {int(report.get('eligible', 0))}/{int(report.get('loaded', 0))} pool • "
            f"{count_prefix}{report.get('compact_combinations', '0')} possible • "
            f"{int(report.get('requested', 0))} target"
        )
        if self._lineup_space_phase:
            text += f" • {self._lineup_space_phase}"
        self.lbl_lineup_space.setText(text)
        tooltip = [
            f"Pool: {int(report.get('eligible', 0)):,} of {int(report.get('loaded', 0)):,} ({report.get('pool_label')})",
            f"Omitted from this pool: {int(report.get('omitted', 0)):,}",
            f"Locked in every lineup: {int(report.get('locked', 0)):,}",
            f"Structural combinations: {count:,}" + (" (exact roster shapes)" if report.get("exact") else " (upper bound)"),
            str(report.get("explanation") or ""),
            "This intentionally excludes salary-cap, stacking, correlation, exposure, and uniqueness checks.",
        ]
        timing = dict(self.last_build_timing_report or {})
        if timing:
            tooltip.extend([
                "",
                "Last build timing:",
                f"Generate {float(timing.get('generation_seconds', 0.0)):.2f}s • "
                f"SIM {float(timing.get('simulation_seconds', 0.0)):.2f}s • "
                f"Select {float(timing.get('selection_seconds', 0.0)):.2f}s • "
                f"Total {float(timing.get('total_seconds', 0.0)):.2f}s",
                f"Candidates {int(timing.get('candidate_count', 0)):,}/"
                f"{int(timing.get('candidate_target', 0)):,} budget → selected "
                f"{int(timing.get('selected_count', 0)):,}",
                "Use Settings > Copy Last Build Report to share the full diagnostic.",
            ])
        self.lbl_lineup_space.setToolTip("\n".join(line for line in tooltip if line is not None))

    def _apply_readiness_player_filter(self) -> None:
        if not hasattr(self, "tbl_players"):
            return
        wanted = {name.casefold() for name in self._readiness_filter_names if name}
        first_visible = -1
        visible = 0
        for row in range(self.tbl_players.rowCount()):
            item = self.tbl_players.item(row, 0)
            matches = not wanted or (item is not None and item.text().strip().casefold() in wanted)
            self.tbl_players.setRowHidden(row, not matches)
            if matches:
                visible += 1
                if first_visible < 0:
                    first_visible = row
        if hasattr(self, "btn_clear_readiness_filter"):
            self.btn_clear_readiness_filter.setVisible(bool(wanted))
        if wanted and first_visible >= 0:
            self.tbl_players.selectRow(first_visible)
            self.tbl_players.scrollToItem(self.tbl_players.item(first_visible, 0))
        if wanted:
            self.status.showMessage(f"Showing {visible} player(s) from the readiness finding.", 5000)

    def focus_readiness_players(self, check: Dict[str, Any]) -> None:
        details = dict((check or {}).get("details") or {})
        self._readiness_filter_names = {
            str(name).strip() for name in details.get("player_names") or [] if str(name).strip()
        }
        self._apply_readiness_player_filter()

    def clear_readiness_player_filter(self) -> None:
        self._readiness_filter_names.clear()
        self._apply_readiness_player_filter()
        self.status.showMessage("Showing the full player pool.", 3000)

    def _update_readiness_badge(self) -> None:
        if not hasattr(self, "lbl_readiness"):
            return
        if not self.players:
            self.lbl_readiness.setText("Readiness: load a slate")
            self.lbl_readiness.setStyleSheet("color: #AEB7C5; padding: 1px 3px;")
            return
        report = self._calculate_slate_readiness()
        status = str(report.get("status") or "review")
        color = {"ready": "#8FE3A1", "review": "#FFD180", "blocked": "#FF8A80"}.get(status, "#FFD180")
        self.lbl_readiness.setText(
            f"Readiness: {str(report.get('title') or 'Review')} {int(report.get('score', 0) or 0)}/100"
        )
        self.lbl_readiness.setStyleSheet(f"color: {color}; font-weight: 700; padding: 1px 3px;")
        self.lbl_readiness.setToolTip(
            f"{int(report.get('blockers', 0) or 0)} blocker(s); "
            f"{int(report.get('reviews', 0) or 0)} item(s) to review. Click Slate Readiness for details."
        )

    def on_slate_readiness(self) -> None:
        if self.players and self._current_sport() == "NFL":
            stale = not self._last_live_check_epoch or (time.time() - self._last_live_check_epoch) > 15 * 60
            if stale:
                try:
                    self._run_live_nfl_check(show_dialog=False, full_context=False)
                except Exception:
                    logger.exception("Slate Readiness live refresh failed; reporting cached state")
                    self.status.showMessage("Live refresh was unavailable; readiness uses the last known data.", 6000)
        report = self._calculate_slate_readiness()
        self._update_readiness_badge()
        SlateReadinessDialog(report, self).exec_()

    def _record_live_check(self, summary: Dict[str, Any]) -> None:
        had_previous = bool(self.last_live_check_summary)
        self.last_live_check_summary = dict(summary or {})
        sleeper_ok = summary.get("sleeper_state") == "ok"
        if sleeper_ok:
            self._last_live_check_epoch = time.time()
        matched = int(summary.get("sleeper", 0) or 0)
        total = int(summary.get("total", len(self.players)) or 0)
        flags = int(summary.get("status_flags", 0) or 0)
        changes = int(summary.get("status_changes", 0) or 0) if had_previous else 0
        promotions = int(summary.get("replacement_promotions", 0) or 0)
        status_text = f"Players {matched}/{total}" if sleeper_ok else "Player status unavailable"
        checked = time.strftime("%I:%M %p").lstrip("0")
        self.lbl_live_data.setText(
            f"Live data {checked} • {status_text} • {flags} unavailable flags • "
            f"{promotions} next-up boosts • {changes} changes"
        )
        details = [
            f"Player source: {'Sleeper' if sleeper_ok else 'unavailable'}",
            f"Matched players: {matched}/{total}",
            f"Unavailable/out flags: {flags}",
            f"Next active players promoted: {promotions}",
            f"Status changes: {changes}",
        ]
        self.lbl_live_data.setToolTip("\n".join(details))
        if not sleeper_ok:
            color = "#FF8A80"
        else:
            color = "#8FE3A1"
        self.lbl_live_data.setStyleSheet(f"color: {color}; padding: 1px 3px;")
        self._update_readiness_badge()

    def _locked_live_conflicts(self) -> List[Dict[str, Any]]:
        return [
            player for player in self.players
            if bool(player.get("LiveStatusConflict"))
            or (
                (bool(player.get("LockFlex")) or bool(player.get("LockCpt")))
                and str(player.get("NFLAvailability") or "").strip().upper() == "OUT"
            )
        ]

    def _run_live_nfl_check(self, *, show_dialog: bool, full_context: bool) -> Dict[str, Any]:
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            if full_context:
                summary = apply_auto_nfl_context(self.players)
            else:
                summary = refresh_live_nfl_data(self.players)
            self._record_live_check(summary)
            faded = self._auto_fade_out_players()
            self.recalc_ownership_quick()
            self._refresh_players_table()
            self.status.showMessage(
                f"Game-day check complete: {int(summary.get('sleeper', 0))}/{len(self.players)} players matched."
                + (f" Auto-faded {faded}." if faded else ""),
                7000,
            )
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        if show_dialog:
            change_rows = list(summary.get("changes") or [])
            change_text = ""
            if change_rows:
                rendered = [f"• {row.get('name')}: {row.get('availability') or 'updated'}" for row in change_rows[:12]]
                if len(change_rows) > 12:
                    rendered.append(f"• plus {len(change_rows) - 12} more")
                change_text = "\n\nChanges since the prior check:\n" + "\n".join(rendered)
            QtWidgets.QMessageBox.information(
                self,
                "Game-Day Check",
                f"Player status matched {int(summary.get('sleeper', 0))} of {len(self.players)} players.\n"
                f"Unavailable/out flags: {int(summary.get('status_flags', 0))}.\n"
                f"Next active players promoted: {int(summary.get('replacement_promotions', 0))}."
                f"{change_text}",
            )
        return summary

    def _ensure_live_nfl_before_build(self) -> bool:
        if self._current_sport() != "NFL":
            return True
        stale = not self._last_live_check_epoch or (time.time() - self._last_live_check_epoch) > 15 * 60
        if stale:
            try:
                summary = self._run_live_nfl_check(show_dialog=False, full_context=False)
            except Exception as exc:
                logger.exception("Pre-build NFL game-day check failed")
                answer = QtWidgets.QMessageBox.question(
                    self,
                    "Live Check Unavailable",
                    f"The app could not complete the final player-status check:\n{exc}\n\nContinue using the last known data?",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    QtWidgets.QMessageBox.No,
                )
                if answer != QtWidgets.QMessageBox.Yes:
                    return False
            else:
                if summary.get("sleeper_state") != "ok":
                    answer = QtWidgets.QMessageBox.question(
                        self,
                        "Player Status Unavailable",
                        "Sleeper did not return current player data. Continue using the last known statuses?",
                        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                        QtWidgets.QMessageBox.No,
                    )
                    if answer != QtWidgets.QMessageBox.Yes:
                        return False

        conflicts = self._locked_live_conflicts()
        if conflicts:
            names = "\n".join(f"• {player.get('Name')} ({player.get('NFLAvailability') or player.get('InjuryStatus')})" for player in conflicts[:12])
            QtWidgets.QMessageBox.warning(
                self,
                "Locked Unavailable Player",
                "Generation stopped because an unavailable player is still locked. Unlock or manually override the player, then try again:\n\n" + names,
            )
            return False
        return True

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
                ctx = self._run_live_nfl_check(show_dialog=True, full_context=False)
                logger.info("Manual NFL game-day check applied: %s", ctx)
                return
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
        out_tokens = ("OUT", "IR", "INACTIVE", "PUP", "SUSP", "PRACTICE SQUAD", "NFI")
        faded = 0
        for p in self.players:
            status = str(p.get("InjuryStatus") or "").strip().upper()
            if not status:
                continue
            availability = str(p.get("NFLAvailability") or "").strip().upper()
            is_out = availability == "OUT" or any(t in status for t in out_tokens) or status == "O"
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
        meta = (own_map or {}).get("meta", {}) or {}
        eligible_keys = set(meta.get("eligible_keys") or [])
        for p in self.players:
            k = _pkey(p)
            p["ProjOwnPct"] = float(tot.get(k, 0.0) or 0.0)
            p["ProjCptOwnPct"] = float(cpt.get(k, 0.0) or 0.0)
            p["ProjFlexOwnPct"] = float(flx.get(k, 0.0) or 0.0)
            if eligible_keys:
                p["NFLFieldEligible"] = k in eligible_keys
        self._own_progress.setVisible(False)
        self._own_eta.setVisible(False)
        self._refresh_players_table()
        if meta:
            self.status.showMessage(
                f"Ownership simulation complete: {int(meta.get('valid_lineups', 0) or 0):,} valid field lineups "
                f"from a {int(meta.get('role_pool_size', 0) or 0)}-player role pool.",
                6000,
            )
        else:
            self.status.showMessage("Ownership simulation complete.", 4000)

    def _on_own_sim_error(self, msg: str) -> None:
        self._own_progress.setVisible(False)
        self._own_eta.setVisible(False)
        self.status.showMessage("Ownership simulation failed.", 5000)
        QtWidgets.QMessageBox.warning(self, "Ownership Simulation Error", msg)

    def _refresh_players_table(self) -> None:
        selected_keys = set()
        for item in self.tbl_players.selectedItems():
            name_item = self.tbl_players.item(item.row(), 0)
            if name_item is not None and name_item.data(QtCore.Qt.UserRole):
                selected_keys.add(str(name_item.data(QtCore.Qt.UserRole)))

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
            injury_raw = str(p.get("InjuryStatus", "") or "").strip()
            availability = str(p.get("NFLAvailability", "") or "").strip()
            inj = injury_raw or (availability.title() if availability else "")
            sal = int(float(p.get("FlexSalary", 0.0) or 0.0))
            proj = float(p.get("FlexProjection", 0.0) or 0.0)
            tag_txt = self._tags_to_text(p)

            name_item = QtWidgets.QTableWidgetItem(name)
            name_item.setData(QtCore.Qt.UserRole, _pkey(p))
            self.tbl_players.setItem(r, 0, name_item)
            self.tbl_players.setItem(r, 1, QtWidgets.QTableWidgetItem(team))
            self.tbl_players.setItem(r, 2, QtWidgets.QTableWidgetItem(pos))
            injury_item = QtWidgets.QTableWidgetItem(inj)
            injury_details = [
                f"Availability: {availability or 'Unknown'}",
                f"Roster: {str(p.get('NFLRosterStatus') or 'Unknown')}",
                f"Depth: {str(p.get('NFLDepthPosition') or '')}{int(float(p.get('NFLDepthOrder', 0) or 0)) or ''}",
                f"Practice: {str(p.get('NFLPractice') or 'Not reported')}",
                f"Injury: {injury_raw or 'None reported'}",
                f"Source: {str(p.get('InjurySource') or 'DraftKings file')}",
                f"Checked: {str(p.get('LiveStatusUpdatedAt') or 'Not checked')}",
            ]
            if p.get("NFLNewsNote"):
                injury_details.append(f"News note: {p.get('NFLNewsNote')}")
            if p.get("NFLNewsUpdatedAt"):
                injury_details.append(f"News updated: {p.get('NFLNewsUpdatedAt')}")
            injury_item.setToolTip("\n".join(injury_details))
            self.tbl_players.setItem(r, 3, injury_item)
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
                if p.get("NFLReplacementFor"):
                    role_item.setToolTip(
                        f"Next active player after {p.get('NFLReplacementFor')} was ruled unavailable.\n"
                        f"Opportunity adjustment: {float(p.get('NFLReplacementBoost', 0.0) or 0.0):+.2f}\n"
                        "This boost is removed automatically if the starter becomes available."
                    )
                self.tbl_players.setItem(r, 10, role_item)
                weather_val = float(p.get("NFLWeatherScore", 0.0) or 0.0)
                weather_item = SortKeyItem(f"{weather_val:+.1f}")
                weather_item.setData(QtCore.Qt.UserRole, weather_val)
                self.tbl_players.setItem(r, 11, weather_item)

                team_total = float(p.get("NFLVegasTeamTotal", 0.0) or 0.0)
                vegas_state = str(p.get("NFLVegasState") or "")
                if team_total > 0:
                    vegas_text = f"{team_total:.1f}"
                elif vegas_state == "not_configured":
                    vegas_text = "Key"
                elif vegas_state == "no_games":
                    vegas_text = "None"
                else:
                    vegas_text = "—"
                vegas_item = SortKeyItem(vegas_text)
                vegas_item.setData(QtCore.Qt.UserRole, team_total if team_total > 0 else -1.0)
                vegas_item.setToolTip(
                    f"Implied team total: {team_total:.1f}\n"
                    f"Game total: {float(p.get('NFLVegasGameTotal', 0.0) or 0.0):.1f}\n"
                    f"Team spread: {float(p.get('NFLVegasSpread', 0.0) or 0.0):+.1f}\n"
                    f"Projection adjustment: {float(p.get('NFLVegas', 0.0) or 0.0):+.2f}\n"
                    f"Sportsbooks: {int(float(p.get('NFLVegasBookmakers', 0) or 0))}\n"
                    f"Updated: {str(p.get('NFLVegasUpdatedAt') or 'Not available')}\n"
                    f"State: {vegas_state or 'not checked'}"
                )
                self.tbl_players.setItem(r, 12, vegas_item)
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
            min_cpt = p.get("MinCptPct", None)
            max_pct = p.get("MaxPct", None)
            min_pct = p.get("MinPct", None)

            mc_item = SortKeyItem("" if max_cpt in (None, "", 0) else f"{float(max_cpt):.0f}%")
            mc_item.setData(QtCore.Qt.UserRole, float(max_cpt) if max_cpt not in (None, "", 0) else -1.0)
            self.tbl_players.setItem(r, 16, mc_item)

            min_cpt_item = SortKeyItem("" if min_cpt in (None, "", 0) else f"{float(min_cpt):.0f}%")
            min_cpt_item.setData(QtCore.Qt.UserRole, float(min_cpt) if min_cpt not in (None, "", 0) else -1.0)
            self.tbl_players.setItem(r, 17, min_cpt_item)

            mp_item = SortKeyItem("" if max_pct in (None, "", 0) else f"{float(max_pct):.0f}%")
            mp_item.setData(QtCore.Qt.UserRole, float(max_pct) if max_pct not in (None, "", 0) else -1.0)
            self.tbl_players.setItem(r, 18, mp_item)

            min_pct_item = SortKeyItem("" if min_pct in (None, "", 0) else f"{float(min_pct):.0f}%")
            min_pct_item.setData(QtCore.Qt.UserRole, float(min_pct) if min_pct not in (None, "", 0) else -1.0)
            self.tbl_players.setItem(r, 19, min_pct_item)

            order_val = int(p.get("BattingOrder", 0) or 0)
            is_pitcher = bool(set(str(p.get("Position", "") or "").upper().replace("/", ",").split(",")) & {"P", "SP", "RP"})
            order_text = "P" if (is_mlb and is_pitcher and order_val <= 0) else ("" if order_val <= 0 else str(order_val))
            order_item = SortKeyItem(order_text)
            order_item.setData(QtCore.Qt.UserRole, float(order_val if order_val > 0 else 99))
            self.tbl_players.setItem(r, 20, order_item)

            self.tbl_players.setItem(r, 21, QtWidgets.QTableWidgetItem(str(p.get("Bats", "") or "")))
            status = str(p.get("LineupStatus", "") or "").strip().lower()
            conf_text = "Y" if bool(p.get("ConfirmedLineup")) else ("Proj" if status == "projected" else "")
            conf_item = QtWidgets.QTableWidgetItem(conf_text)
            self.tbl_players.setItem(r, 22, conf_item)

            # Visual-only highlighting
            self._set_player_row_style(r, p)

            for column in range(self.tbl_players.columnCount()):
                item = self.tbl_players.item(r, column)
                if item is not None:
                    item.setTextAlignment(self._player_column_alignment(column))

        # Keep widths stable across data refreshes while still filling the
        # current player-table viewport.
        self._fit_player_table_columns()

        # Restore sorting state.
        if was_sorting:
            self.tbl_players.setSortingEnabled(True)
            if sort_col >= 0:
                self.tbl_players.sortItems(sort_col, sort_order)

        # Contextual actions should stay anchored to the same players after a
        # lock, fade, ownership update, or table sort refresh.
        if selected_keys:
            selection_model = self.tbl_players.selectionModel()
            selection_model.clearSelection()
            for row in range(self.tbl_players.rowCount()):
                item = self.tbl_players.item(row, 0)
                if item is not None and str(item.data(QtCore.Qt.UserRole) or "") in selected_keys:
                    selection_model.select(
                        self.tbl_players.model().index(row, 0),
                        QtCore.QItemSelectionModel.Select | QtCore.QItemSelectionModel.Rows,
                    )
        self._update_player_inspector()
        self._apply_readiness_player_filter()
        self._update_readiness_badge()
        self._update_lineup_space_dashboard()

    def _confirm_contest_entry_count(
        self,
        requested: int,
        profile: Optional[Dict[str, Any]],
    ) -> Optional[int]:
        """Resolve an attached contest's planned-entry mismatch before a build."""
        planned = int((profile or {}).get("user_entries", 0) or 0)
        requested = max(1, int(requested or 1))
        if not profile or planned <= 0 or planned == requested:
            return requested

        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setWindowTitle("Contest entry count differs")
        box.setText(
            f"The attached contest profile plans {planned:,} entries, but this build requests {requested:,}."
        )
        box.setInformativeText(
            "Joint portfolio results are most useful when the generated lineup count matches the entries you will submit."
        )
        keep_button = box.addButton(f"Keep {requested:,}", QtWidgets.QMessageBox.ActionRole)
        use_button = None
        maximum = self.spin_cl.maximum() if hasattr(self, "spin_cl") else requested
        if planned <= maximum:
            use_button = box.addButton(f"Use {planned:,}", QtWidgets.QMessageBox.AcceptRole)
            box.setDefaultButton(use_button)
        else:
            box.setDefaultButton(keep_button)
        cancel_button = box.addButton(QtWidgets.QMessageBox.Cancel)
        box.exec_()
        clicked = box.clickedButton()
        if clicked is cancel_button:
            return None
        if use_button is not None and clicked is use_button:
            if hasattr(self, "spin_cl"):
                self.spin_cl.setValue(planned)
            return planned
        return requested

    def _start_lineup_build(
        self,
        *,
        kind: str,
        sport: str,
        num: int,
        cap: float,
        retained_lineups: Optional[List[Any]] = None,
        repair_source: str = "",
    ) -> None:
        """Run a lineup build in a worker thread and show status-bar progress."""
        if str(sport or "").strip().upper() == "NFL" and not self._ensure_live_nfl_before_build():
            self.status.showMessage("Lineup generation cancelled until the game-day status check is resolved.", 6000)
            return
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
        sim_checkbox = getattr(self, "chk_nfl_contest_sim", None)
        sim_spin = getattr(self, "spin_nfl_sim_scenarios", None)
        field_preset_widget = getattr(self, "combo_field_preset", None)
        compute_mode_widget = getattr(self, "combo_nfl_compute_mode", None)
        sim_enabled = bool(sim_checkbox.isChecked()) if sim_checkbox is not None else True
        sim_scenarios = int(sim_spin.value()) if sim_spin is not None else 750
        field_preset = field_preset_widget.currentText() if field_preset_widget is not None else "150-Max"
        compute_mode = compute_mode_widget.currentText() if compute_mode_widget is not None else "Fast (default)"
        field_calibration: Dict[str, Any] = {}
        if str(sport or "").strip().upper() == "NFL" and kind != "showdown" and sim_enabled:
            try:
                field_calibration = load_nfl_field_calibration(field_preset)
            except Exception:
                logger.exception("NFL field calibration could not be loaded; using baseline preset")

        portfolio_rules = self._portfolio_rules()
        effective_sim_enabled = bool(
            str(sport or "").strip().upper() == "NFL" and kind != "showdown" and sim_enabled
        )
        contest_profile = self._active_contest_profile() if effective_sim_enabled else None
        if contest_profile and not str(repair_source or "").strip():
            resolved_num = self._confirm_contest_entry_count(num, contest_profile)
            if resolved_num is None:
                self.status.showMessage("Lineup generation cancelled; contest entry counts were not confirmed.", 5000)
                return
            num = resolved_num
        retained = list(retained_lineups or [])[:max(0, int(num))]
        replacement_count = max(0, int(num) - len(retained))
        repairing = bool(str(repair_source or "").strip())
        self._active_build_context = {
            "sport": str(sport or "NFL").strip().upper(),
            "kind": str(kind or "classic").strip().lower(),
            "salary_cap": float(cap),
            "requested_count": int(num),
            "lineup_space": self._calculate_lineup_space(),
            "settings": {
                "build_style": build_style,
                "salary_strategy": salary_strategy,
                "ownership_mode": mode,
                "ownership_weight": lam,
                "sim_enabled": effective_sim_enabled,
                "sim_scenarios": sim_scenarios if effective_sim_enabled else 0,
                "field_preset": field_preset if effective_sim_enabled else "",
                "contest_profile": dict(contest_profile or {}),
                "compute_mode": (
                    "Deep" if effective_sim_enabled and compute_mode.casefold().startswith("deep") else "Fast"
                ),
            },
            "portfolio_rules": portfolio_rules,
            "repair_source": str(repair_source or ""),
            "retained_count": len(retained),
            "replacement_count": replacement_count,
        }

        label_sport = sport if kind != "showdown" else "Showdown"
        self._build_progress.setRange(0, max(1, replacement_count if repairing else num))
        self._build_progress.setValue(0)
        self._build_progress.setVisible(True)
        self._build_eta.setText(
            f"Replacing {replacement_count} {label_sport} lineup{'s' if replacement_count != 1 else ''}…"
            if repairing else f"Building {label_sport} lineups…"
        )
        self._build_eta.setVisible(True)
        self._build_cancel.setEnabled(True)
        self._build_cancel.setVisible(True)
        self._lineup_space_phase = f"Generate 0/{replacement_count if repairing else num:,}"
        self._update_lineup_space_dashboard()
        self.status.showMessage(
            (
                f"Repairing {replacement_count:,} of {num:,} {label_sport} lineups; "
                f"preserving {len(retained):,}…"
            )
            if repairing else f"Building {label_sport} lineups ({num:,}) • {build_style} • {salary_strategy}…"
        )

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
            portfolio_rules=portfolio_rules,
            sim_enabled=sim_enabled,
            sim_scenarios=sim_scenarios,
            field_preset=field_preset,
            field_calibration=field_calibration,
            contest_profile=contest_profile,
            compute_mode=compute_mode,
            retained_lineups=retained,
            repair_source=repair_source,
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

        phase_text = str(text or "").lower()
        if "phase 4" in phase_text or "selecting" in phase_text or "refining" in phase_text:
            self._lineup_space_phase = "Selecting"
        elif "phase 2" in phase_text or "phase 3 of 4" in phase_text:
            self._lineup_space_phase = f"SIM {done:,}/{total:,}" if total > 0 else "SIM"
        else:
            self._lineup_space_phase = f"Generate {done:,}/{total:,}" if total > 0 else "Generating"
        self._update_lineup_space_dashboard()

    def _cancel_lineup_build(self) -> None:
        worker = getattr(self, "_build_worker", None)
        if worker is None:
            return
        worker.request_cancel()
        self._build_cancel.setEnabled(False)
        self._build_eta.setText("Cancelling after the current candidate…")
        self.status.showMessage("Cancelling lineup build…")

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

            captain_item = QtWidgets.QTableWidgetItem(self._display_name(cpt))
            captain_item.setTextAlignment(int(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter))
            self.tbl_sd.setItem(i, 1, captain_item)
            for j in range(5):
                flex_item = QtWidgets.QTableWidgetItem(
                    self._display_name(flex[j]) if j < len(flex) else ""
                )
                flex_item.setTextAlignment(int(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter))
                self.tbl_sd.setItem(i, 2 + j, flex_item)
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
        has_sim_edge = any(bool(getattr(lineup, "sim_metrics", None)) for lineup in self.last_classic)
        headers = ["Save"] + slots + ["TotalSal", "SIM Edge" if has_sim_edge else "Grade"]
        self.tbl_cl.setColumnCount(len(headers))
        self.tbl_cl.setHorizontalHeaderLabels(headers)
        self._fit_lineup_table_columns(self.tbl_cl)
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
                player_item = QtWidgets.QTableWidgetItem(txt)
                player_item.setTextAlignment(int(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter))
                self.tbl_cl.setItem(i, col, player_item)

            total_sal = int(sum(float(p.get("FlexSalary", 0.0) or 0.0) for p in lu))
            salary_item = QtWidgets.QTableWidgetItem(f"{total_sal:,}")
            salary_item.setTextAlignment(int(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter))
            self.tbl_cl.setItem(i, len(headers)-2, salary_item)

            try:
                grade_info = lineup_grade_for_sport(lu, sport, self._safe_float(self.edit_cl_cap.text(), 50000.0))
                if grade_info.get("sim_scenarios"):
                    if grade_info.get("sim_expected_roi_pct") is not None:
                        grade_txt = (
                            f"{float(grade_info.get('sim_edge', 0.0)):.0f} | "
                            f"ROI {float(grade_info.get('sim_expected_roi_pct', 0.0)):+.1f}%"
                        )
                    else:
                        grade_txt = (
                            f"{float(grade_info.get('sim_edge', 0.0)):.0f} | "
                            f"T1 {float(grade_info.get('sim_top_one_pct', 0.0)):.1f}%"
                        )
                else:
                    grade_txt = f"{grade_info.get('grade', '')} ({float(grade_info.get('score', 0.0)):.0f})"
                grade_item = QtWidgets.QTableWidgetItem(grade_txt)
                grade_item.setTextAlignment(int(QtCore.Qt.AlignCenter))
                detail = (
                    f"Salary Used: ${float(grade_info.get('salary_used', 0.0)):,.0f}\n"
                    f"Salary Left: ${float(grade_info.get('salary_left', 0.0)):,.0f}\n"
                    f"Stack/Shape: {grade_info.get('stack_shape', '')}\n"
                    f"Warnings: {grade_info.get('warnings', '') or 'None'}\n"
                )
                if grade_info.get("sim_scenarios"):
                    detail += (
                        f"Top 1% Rate: {float(grade_info.get('sim_top_one_pct', 0.0)):.2f}%\n"
                        f"Top 5% Rate: {float(grade_info.get('sim_top_five_pct', 0.0)):.1f}%\n"
                        f"Representative Win Rate: {float(grade_info.get('sim_win_rate', 0.0)):.2f}%\n"
                        f"Cash Rate: {float(grade_info.get('sim_cash_rate', 0.0)):.1f}%\n"
                        f"Bust Rate: {float(grade_info.get('sim_bust_rate', 0.0)):.1f}%\n"
                        f"Average Field Percentile: {float(grade_info.get('sim_average_percentile', 0.0)):.1f}\n"
                        f"90th-Percentile Score: {float(grade_info.get('sim_ceiling', 0.0)):.1f}\n"
                        f"Tournament Return Index: {float(grade_info.get('sim_return_index', 0.0)):.0f}/100\n"
                        f"Leverage: {float(grade_info.get('sim_leverage', 0.0)):.0f}/100\n"
                        f"Duplication Risk: {float(grade_info.get('duplicate_risk', 0.0)):.0f}/100\n"
                        f"Simulation: {int(grade_info.get('sim_scenarios', 0)):,} scenarios vs "
                        f"{int(grade_info.get('sim_field_lineups', 0)):,} field lineups\n"
                    )
                    if grade_info.get("sim_expected_roi_pct") is not None:
                        joint_label = "Portfolio-adjusted " if grade_info.get("sim_joint_portfolio") else ""
                        detail += (
                            f"Contest: {grade_info.get('sim_contest_name') or 'Attached contest'}\n"
                            f"Contest Field: {int(grade_info.get('sim_contest_field_size', 0) or 0):,}\n"
                            f"Entry Fee: ${float(grade_info.get('sim_entry_fee', 0.0) or 0.0):,.2f}\n"
                            f"{joint_label}Expected Payout: ${float(grade_info.get('sim_expected_payout', 0.0) or 0.0):,.2f}\n"
                            f"{joint_label}Expected Profit: ${float(grade_info.get('sim_expected_profit', 0.0) or 0.0):+,.2f}\n"
                            f"{joint_label}Expected ROI: {float(grade_info.get('sim_expected_roi_pct', 0.0) or 0.0):+.2f}%\n"
                        )
                        if grade_info.get("sim_joint_portfolio"):
                            detail += (
                                f"Joint Portfolio: {int(grade_info.get('sim_portfolio_entry_count', 0) or 0):,} entries across "
                                f"{int(grade_info.get('sim_portfolio_scenarios', 0) or 0):,} scenarios\n"
                                f"Portfolio Total Payout: ${float(grade_info.get('sim_portfolio_expected_total_payout', 0.0) or 0.0):,.2f}\n"
                                f"Portfolio Total Profit: ${float(grade_info.get('sim_portfolio_expected_total_profit', 0.0) or 0.0):+,.2f}\n"
                                f"Portfolio Profit Chance: {float(grade_info.get('sim_portfolio_profit_probability_pct', 0.0) or 0.0):.1f}%\n"
                                f"Portfolio 95% ROI Range: {float(grade_info.get('sim_portfolio_roi_ci_low', 0.0) or 0.0):+.1f}% to "
                                f"{float(grade_info.get('sim_portfolio_roi_ci_high', 0.0) or 0.0):+.1f}%\n"
                            )
                    drivers = list(grade_info.get("sim_edge_drivers") or [])
                    if drivers:
                        detail += "Why this SIM Edge:\n"
                        for driver in drivers:
                            detail += (
                                f"  {driver.get('label')}: {float(driver.get('percentile', 0.0)):.0f}th percentile "
                                f"({driver.get('direction')}, {int(driver.get('weight_pct', 0) or 0)}% weight)\n"
                            )
                    if grade_info.get("learned_profile_fit") is not None:
                        detail += f"Learned winning-profile fit: {float(grade_info.get('learned_profile_fit', 0.0)):.0f}/100\n"
                detail += (
                    "Slate-relative simulation metrics; not included in DK export/saved ID table."
                    if grade_info.get("sim_scenarios")
                    else "UI-only grade; not included in DK export/saved ID table."
                )
                grade_item.setToolTip(detail)
                self.tbl_cl.setItem(i, len(headers)-1, grade_item)
            except Exception:
                grade_item = QtWidgets.QTableWidgetItem("")
                grade_item.setTextAlignment(int(QtCore.Qt.AlignCenter))
                self.tbl_cl.setItem(i, len(headers)-1, grade_item)

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
                captains = Counter()
                archetypes = Counter()
                correlation_flags = Counter()
                duplication = []
                for lu in lineups:
                    captain = lu.get("Captain") or {}
                    teams = [str(captain.get("Team", ""))] + [str(p.get("Team", "")) for p in lu.get("Flex", [])]
                    counts = sorted([c for _, c in Counter(t for t in teams if t).items()], reverse=True)
                    if counts:
                        team_splits["-".join(map(str, counts))] += 1
                    captain_name = str(captain.get("Name") or "").strip()
                    if captain_name:
                        captains[captain_name] += 1
                    metrics = dict(getattr(lu, "sim_metrics", {}) or {})
                    archetype = str(getattr(lu, "candidate_archetype", "") or metrics.get("candidate_archetype") or "")
                    if archetype:
                        archetypes[archetype] += 1
                    if metrics.get("duplicate_risk") is not None:
                        duplication.append(float(metrics.get("duplicate_risk") or 0.0))
                    for flag in metrics.get("showdown_correlation_flags") or []:
                        correlation_flags[str(flag)] += 1
                common = ", ".join(f"{k}: {v}" for k, v in team_splits.most_common(3))
                captain_count = len(captains)
                script_count = len(archetypes)
                dup_text = (
                    f" | avg duplicate risk {sum(duplication) / len(duplication):.0f}/100"
                    if duplication else ""
                )
                exception_text = (
                    f" | correlation exceptions {sum(correlation_flags.values())}"
                    if correlation_flags else ""
                )
                return (
                    f"Quality: {len(lineups)} Showdown lineups | {captain_count} captains | "
                    f"{script_count} scripts | common splits {common or 'n/a'}{dup_text}{exception_text}."
                )
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
            if sport_u == "NFL":
                sim_rows = [getattr(lineup, "sim_metrics", None) for lineup in lineups]
                sim_rows = [row for row in sim_rows if isinstance(row, dict) and row]
                if sim_rows:
                    avg_edge = sum(float(row.get("sim_edge", 0.0) or 0.0) for row in sim_rows) / len(sim_rows)
                    avg_top = sum(float(row.get("sim_top_one_pct", 0.0) or 0.0) for row in sim_rows) / len(sim_rows)
                    avg_return = sum(float(row.get("sim_return_index", 0.0) or 0.0) for row in sim_rows) / len(sim_rows)
                    roi_rows = [
                        float(row.get("sim_expected_roi_pct"))
                        for row in sim_rows
                        if row.get("sim_expected_roi_pct") is not None
                    ]
                    scenarios = max(int(row.get("sim_scenarios", 0) or 0) for row in sim_rows)
                    covered = set()
                    for lineup in lineups:
                        covered.update(set(getattr(lineup, "sim_top_hits", set()) or set()))
                    joint_row = next(
                        (row for row in sim_rows if row.get("sim_joint_portfolio")),
                        None,
                    )
                    if joint_row:
                        roi_text = (
                            f" | joint ROI {float(joint_row.get('sim_portfolio_expected_roi_pct', 0.0) or 0.0):+.1f}% "
                            f"(${float(joint_row.get('sim_portfolio_expected_total_profit', 0.0) or 0.0):+,.0f}; "
                            f"{float(joint_row.get('sim_portfolio_profit_probability_pct', 0.0) or 0.0):.0f}% profit chance)"
                        )
                    else:
                        roi_text = f" | avg contest ROI {sum(roi_rows) / len(roi_rows):+.1f}%" if roi_rows else ""
                    return (
                        f"Quality: {len(lineups)} NFL lineups | avg salary left ${avg_left:,.0f} | "
                        f"avg SIM Edge {avg_edge:.0f} | avg top-1% {avg_top:.2f}% | "
                        f"return index {avg_return:.0f}{roi_text} | top-1% paths {len(covered)}/{scenarios}."
                    )
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
            self.last_portfolio_report = dict(payload.get("portfolio_report") or {})
            self.last_sim_report = dict(payload.get("sim_report") or {})
            self.last_build_timing_report = dict(payload.get("timing_report") or {})
            repair_source = str(payload.get("repair_source") or "")
            retained_count = int(self.last_build_timing_report.get("retained_count", 0) or 0)
            replacement_count = int(self.last_build_timing_report.get("replacement_requested", 0) or 0)
            warning_count = len(self.last_portfolio_report.get("warnings") or [])
            portfolio_note = f" Portfolio warnings: {warning_count}." if warning_count else " Portfolio rules satisfied."
            comparison_note = ""
            preset_comparison = dict(self.last_sim_report.get("preset_comparison") or {})
            if preset_comparison.get("available"):
                comparison_note = f" Preset fit: {float(preset_comparison.get('fit_score', 0.0)):.0f}/100."
            field_comparison = dict(self.last_sim_report.get("field_comparison") or {})
            if field_comparison.get("available"):
                simulated = dict(field_comparison.get("simulated") or {})
                real = dict(field_comparison.get("real") or {})

                def optional_pct(value: Any) -> str:
                    try:
                        number = float(value)
                    except (TypeError, ValueError):
                        return "n/a"
                    return f"{number:.1f}%" if math.isfinite(number) else "n/a"

                comparison_note += (
                    f" Field check: SIM duplication {optional_pct(simulated.get('duplicate_entry_pct'))} "
                    f"vs real {optional_pct(real.get('duplicate_entry_pct'))}"
                    + (" (report-only)." if field_comparison.get("report_only") else " (learned blend active).")
                )
            timing_note = ""
            if self.last_build_timing_report:
                timing_note = (
                    f" Timing: generate {float(self.last_build_timing_report.get('generation_seconds', 0.0)):.1f}s, "
                    f"SIM {float(self.last_build_timing_report.get('simulation_seconds', 0.0)):.1f}s, "
                    f"select {float(self.last_build_timing_report.get('selection_seconds', 0.0)):.1f}s."
                )

            if kind == "showdown":
                self._populate_showdown_lineups(lineups)
                built = len(self.last_showdown)
                if repair_source == "saved":
                    self.saved_showdown = list(self.last_showdown)
                    self._refresh_saved_tables()
                    self._sync_saved_checkboxes(kind)
                result = (
                    f"Repaired {replacement_count} slot{'s' if replacement_count != 1 else ''}; preserved {retained_count}"
                    if repair_source and not cancelled
                    else f"Cancelled after {built} of {requested}" if cancelled
                    else f"Built {built} of {requested}"
                )
                self.status.showMessage(f"{result} showdown lineups. {self._lineup_quality_summary(self.last_showdown, sport, kind)}{portfolio_note}{timing_note}", 9000)
            else:
                self._populate_classic_lineups(lineups, sport)
                built = len(self.last_classic)
                if repair_source == "saved":
                    self.saved_classic = list(self.last_classic)
                    self._refresh_saved_tables()
                    self._sync_saved_checkboxes(kind)
                result = (
                    f"Repaired {replacement_count} slot{'s' if replacement_count != 1 else ''}; preserved {retained_count}"
                    if repair_source and not cancelled
                    else f"Built {built} of {requested}"
                )
                self.status.showMessage(f"{result} {sport} lineups. {self._lineup_quality_summary(self.last_classic, sport, kind)}{portfolio_note}{comparison_note}{timing_note}", 12000)
            self._record_build_diagnostic(payload, displayed_count=built)
            self._lineup_space_phase = ""
            self._update_readiness_badge()
            self._update_lineup_space_dashboard()
        finally:
            self._finish_lineup_build_ui()

    def _on_lineup_build_error(self, msg: str) -> None:
        self._finish_lineup_build_ui()
        self._active_build_context = {}
        self._lineup_space_phase = ""
        self._update_lineup_space_dashboard()
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
                "NFL": ["NFL±", "Usage", "Matchup", "Role", "Wx", ""],
                "MLB": ["MLB±", "Form", "Matchup", "Park", "Wx", "Vegas"],
            }.get(sport_u, ["Adj±", "Context", "Matchup", "Role", "Wx", "Vegas"])
            player_headers = ["Name", "Team", "Pos", "Injury", "Salary", "BaseProj", "AdjProj"] + context_headers + ["TeamAdj", "Tags", "Own% Tot", "MaxCPT%", "MinCPT%", "Max%", "Min%", "Order", "Bats", "Conf"]
            self.tbl_players.setHorizontalHeaderLabels(player_headers)
            self.tbl_cl.setColumnCount(len(headers))
            self.tbl_cl.setHorizontalHeaderLabels(headers)
            self._fit_lineup_table_columns(self.tbl_cl)
            self.tbl_saved_cl.setColumnCount(len(slots))
            self.tbl_saved_cl.setHorizontalHeaderLabels(slots)
            self.saved_classic.clear()
            self.last_classic.clear()
            self.tbl_cl.setRowCount(0)
            self._refresh_saved_tables()
            if self.players:
                self._refresh_players_table()
            self._apply_player_column_visibility(sport_u)
            self._update_sport_controls(sport_u)
            if hasattr(self, "btn_primary_build") and self._contest_mode() == "classic":
                self.btn_primary_build.setToolTip(f"Generate {sport_u} Classic lineups.")
            self.status.showMessage(f"Sport set to {sport_u}. Classic tab now uses: {', '.join(slots)}", 5000)
            self._update_readiness_badge()
            self._update_workspace_summary()
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

    def _record_build_diagnostic(self, payload: Dict[str, Any], *, displayed_count: int) -> None:
        context = dict(self._active_build_context or {})
        self._active_build_context = {}
        if not context:
            kind = str(payload.get("kind") or "classic").strip().lower()
            context = {
                "sport": str(payload.get("sport") or self._current_sport()).strip().upper(),
                "kind": kind,
                "salary_cap": self._safe_float(
                    self.edit_sd_cap.text() if kind == "showdown" else self.edit_cl_cap.text(),
                    50000.0,
                ),
                "requested_count": int(payload.get("requested", 0) or 0),
                "lineup_space": self._calculate_lineup_space(),
                "settings": {
                    "build_style": self.combo_build_style.currentText(),
                    "salary_strategy": self.combo_salary_strategy.currentText(),
                    "ownership_mode": self.combo_build_own_mode.currentText(),
                    "ownership_weight": self.spin_build_own_weight.value(),
                    "sim_enabled": bool(
                        kind == "classic"
                        and self._current_sport() == "NFL"
                        and self.chk_nfl_contest_sim.isChecked()
                    ),
                    "sim_scenarios": self.spin_nfl_sim_scenarios.value(),
                    "field_preset": self.combo_field_preset.currentText(),
                    "compute_mode": (
                        "Deep"
                        if self.combo_nfl_compute_mode.currentText().casefold().startswith("deep")
                        else "Fast"
                    ),
                },
                "portfolio_rules": self._portfolio_rules(),
            }
        diagnostic = create_build_diagnostic(
            context=context,
            timing_report=dict(payload.get("timing_report") or {}),
            portfolio_report=dict(payload.get("portfolio_report") or {}),
            sim_report=dict(payload.get("sim_report") or {}),
            displayed_count=displayed_count,
            cancelled=bool(payload.get("cancelled")),
            lineups=list(payload.get("lineups") or []),
        )
        self.last_build_diagnostic = diagnostic
        try:
            self.last_build_diagnostic = save_build_diagnostic(diagnostic)
        except Exception:
            logger.exception("Build diagnostic could not be saved locally")
            self.status.showMessage(
                "Lineups were built, but the local diagnostic history could not be saved.",
                6000,
            )
        if hasattr(self, "action_copy_build_report"):
            self.action_copy_build_report.setEnabled(True)

    def copy_last_build_report(self) -> None:
        record = dict(self.last_build_diagnostic or {})
        if not record:
            history = load_build_history(limit=1)
            record = dict(history[0]) if history else {}
        if not record:
            QtWidgets.QMessageBox.information(
                self,
                "No Build Report",
                "Generate lineups first, then the completed build report can be copied.",
            )
            return
        self.last_build_diagnostic = record
        QtWidgets.QApplication.clipboard().setText(format_build_report(record))
        self.status.showMessage("Last build report copied to the clipboard.", 4000)

    def on_build_history(self) -> None:
        BuildDiagnosticsDialog(self).exec_()

    def _on_build_history_cleared(self) -> None:
        self.last_build_diagnostic = {}
        if hasattr(self, "action_copy_build_report"):
            self.action_copy_build_report.setEnabled(False)

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
        if not self._confirm_portfolio_export(kind_l, lineups):
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
                "field_preset": self.combo_field_preset.currentText(),
                "compute_mode": (
                    "Deep"
                    if self.combo_nfl_compute_mode.currentText().casefold().startswith("deep")
                    else "Fast"
                ),
                "portfolio_rules": self._portfolio_rules(),
            }
            validation = {
                "valid": True,
                "complete_lineups": len(rows),
                "expected_slots": expected,
                "entry_safety_status": str(self.last_entry_safety_report.get("status") or "ready"),
                "entry_safety_reviews": int(self.last_entry_safety_report.get("reviews", 0) or 0),
            }
            if kind_l == "classic" and sport == "NFL" and self.last_sim_report:
                validation["sim_report"] = self.last_sim_report
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
                app_version="entry-safety-v1",
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
            self.action_show_saved_portfolio.setChecked(True)
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
            self.action_show_saved_portfolio.setChecked(True)
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

    def _sync_saved_checkboxes(self, kind: str) -> None:
        kind_l = str(kind or "classic").lower()
        table = self.tbl_sd if kind_l == "showdown" else self.tbl_cl
        generated = self.last_showdown if kind_l == "showdown" else self.last_classic
        saved = self.saved_showdown if kind_l == "showdown" else self.saved_classic
        saved_ids = {id(lineup) for lineup in saved}
        for row, lineup in enumerate(generated):
            widget = table.cellWidget(row, 0)
            if not isinstance(widget, QtWidgets.QCheckBox):
                continue
            widget.blockSignals(True)
            widget.setChecked(id(lineup) in saved_ids)
            widget.blockSignals(False)

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

    def _saved_portfolio_report(self, kind: str, lineups: Optional[List[Any]] = None) -> Dict[str, Any]:
        kind_l = str(kind or "classic").lower()
        selected = list(lineups if lineups is not None else (
            self.saved_showdown if kind_l == "showdown" else self.saved_classic
        ))
        return portfolio_report(selected, self._portfolio_rules(), kind=kind_l, requested=len(selected))

    def on_portfolio_summary(self) -> None:
        kind = "showdown" if self.tabs_lineups.currentIndex() == 0 else "classic"
        saved = self.saved_showdown if kind == "showdown" else self.saved_classic
        generated = self.last_showdown if kind == "showdown" else self.last_classic
        lineups = list(saved or generated)
        if not lineups:
            QtWidgets.QMessageBox.information(
                self,
                "Portfolio Insights",
                "Generate lineups first, then open Portfolio Insights to review their patterns.",
            )
            return
        report = self._saved_portfolio_report(kind, list(lineups))
        sport = self._current_sport()
        cap = self._safe_float(
            self.edit_sd_cap.text() if kind == "showdown" else self.edit_cl_cap.text(),
            50000.0,
        )
        insights = build_portfolio_insights(
            lineups,
            sport=sport,
            kind=kind,
            salary_cap=cap,
            field_preset=(
                self.combo_field_preset.currentText()
                if sport == "NFL" and kind == "classic"
                else ""
            ),
            source_label="saved" if saved else "generated",
            portfolio_report=report,
            sim_report=self.last_sim_report if sport == "NFL" and kind == "classic" else {},
        )
        source_label = "saved" if saved else "generated"
        dialog = PortfolioInsightsDialog(
            insights,
            self,
            actions_enabled=True,
            source_label=source_label,
        )
        dialog.exec_()
        if dialog.requested_action:
            self._handle_portfolio_insights_action(
                kind=kind,
                sport=sport,
                source_label=source_label,
                lineups=lineups,
                action=dialog.requested_action,
                indexes=dialog.requested_indexes,
                salary_cap=cap,
            )

    def _handle_portfolio_insights_action(
        self,
        *,
        kind: str,
        sport: str,
        source_label: str,
        lineups: List[Any],
        action: str,
        indexes: List[int],
        salary_cap: float,
    ) -> None:
        selected_indexes = sorted({index for index in indexes if 0 <= index < len(lineups)})
        if not selected_indexes:
            return
        selected_set = set(selected_indexes)
        retained = [lineup for index, lineup in enumerate(lineups) if index not in selected_set]
        count = len(selected_indexes)

        if action == "remove":
            answer = QtWidgets.QMessageBox.question(
                self,
                "Remove Selected Lineups",
                f"Remove {count} selected lineup{'s' if count != 1 else ''} from the current {source_label} set?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
                QtWidgets.QMessageBox.Yes,
            )
            if answer != QtWidgets.QMessageBox.Yes:
                return
            if source_label == "saved":
                if kind == "showdown":
                    self.saved_showdown = retained
                else:
                    self.saved_classic = retained
                self._refresh_saved_tables()
                self._sync_saved_checkboxes(kind)
            elif kind == "showdown":
                self._populate_showdown_lineups(retained)
                self._finish_lineup_build_ui()
            else:
                self._populate_classic_lineups(retained, sport)
                self._finish_lineup_build_ui()
            self.status.showMessage(
                f"Removed {count} lineup{'s' if count != 1 else ''}; {len(retained)} remain in the {source_label} set.",
                6000,
            )
            return

        if action != "replace":
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "Replace Selected Lineups",
            (
                f"Keep {len(retained)} lineup{'s' if len(retained) != 1 else ''} fixed and generate "
                f"{count} replacement{'s' if count != 1 else ''} using the current settings?"
            ),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Yes,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        self._start_lineup_build(
            kind=kind,
            sport=sport,
            num=len(lineups),
            cap=salary_cap,
            retained_lineups=retained,
            repair_source=source_label,
        )

    def _final_lock_report(self, kind: str, lineups: List[Any]) -> Dict[str, Any]:
        used_cached_check = bool(
            self.last_live_check_summary
            and self._last_live_check_epoch
            and (time.time() - self._last_live_check_epoch) <= 60.0
        )
        if used_cached_check:
            summary = dict(self.last_live_check_summary)
        else:
            try:
                summary = self._run_live_nfl_check(show_dialog=False, full_context=False)
            except Exception as exc:
                logger.exception("Final Lock Check live refresh failed; cached player data retained")
                summary = dict(self.last_live_check_summary or {})
                summary.update({
                    "total": len(self.players),
                    "sleeper_state": "unavailable",
                    "status_changes": 0,
                    "changes": [],
                    "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "message": str(exc),
                })
                self._record_live_check(summary)
        report = build_final_lock_report(
            lineups,
            kind=kind,
            player_pool=self.players,
            live_summary=summary,
            used_cached_check=used_cached_check,
        )
        self.last_final_lock_report = report
        return report

    def _repair_saved_lineups(
        self,
        kind: str,
        lineups: List[Any],
        indexes: Sequence[int],
        salary_cap: float,
    ) -> None:
        selected_indexes = sorted({
            int(index) for index in indexes if 0 <= int(index) < len(lineups)
        })
        if not selected_indexes:
            return
        self._handle_portfolio_insights_action(
            kind=kind,
            sport=self._current_sport(),
            source_label="saved",
            lineups=list(lineups),
            action="replace",
            indexes=selected_indexes,
            salary_cap=salary_cap,
        )

    def _confirm_final_lock_export(
        self,
        kind: str,
        lineups: List[Any],
        salary_cap: float,
    ) -> bool:
        if self._current_sport() != "NFL" or not self.players:
            return True
        report = self._final_lock_report(kind, lineups)
        dialog = FinalLockCheckDialog(report, self)
        result = dialog.exec_()
        if dialog.repair_requested:
            self._repair_saved_lineups(
                kind,
                lineups,
                report.get("affected_indexes") or [],
                salary_cap,
            )
            return False
        return result == QtWidgets.QDialog.Accepted

    def _entry_safety_report(
        self,
        kind: str,
        lineups: List[Any],
        rows: List[List[str]],
        salary_cap: float,
    ) -> Dict[str, Any]:
        sport = self._current_sport()
        preset_name = self.combo_field_preset.currentText() if sport == "NFL" and kind == "classic" else ""
        calibration: Dict[str, Any] = {}
        if preset_name:
            try:
                calibration = load_nfl_field_calibration(preset_name)
            except Exception:
                logger.exception("Entry Safety could not load NFL field calibration")
        field_preset = (
            nfl_field_preset(preset_name, calibration)
            if preset_name
            else {"name": "", "min_salary_pct": 0.90 if kind == "showdown" else 0.94}
        )
        readiness = audit_slate(
            self.players,
            sport=sport,
            mode=kind,
            salary_cap=salary_cap,
            field_preset=field_preset,
            live_summary=self.last_live_check_summary,
            generated_lineups=lineups,
            sim_report=self.last_sim_report if kind == "classic" else {},
        )
        portfolio = self._saved_portfolio_report(kind, lineups)
        report = build_entry_safety_report(
            lineups,
            kind=kind,
            sport=sport,
            salary_cap=salary_cap,
            export_rows=rows,
            portfolio_report=portfolio,
            readiness_report=readiness,
            player_pool=self.players,
            min_salary_pct=float(field_preset.get("min_salary_pct", 0.94) or 0.94),
        )
        self.last_entry_safety_report = report
        return report

    def _confirm_entry_safety_export(
        self,
        kind: str,
        lineups: List[Any],
        rows: List[List[str]],
        salary_cap: float,
    ) -> bool:
        report = self._entry_safety_report(kind, lineups, rows, salary_cap)
        dialog = EntrySafetyDialog(report, self)
        result = dialog.exec_()
        if dialog.repair_requested:
            self._repair_saved_lineups(
                kind,
                lineups,
                report.get("blocked_lineup_indexes") or [],
                salary_cap,
            )
            return False
        return result == QtWidgets.QDialog.Accepted

    def _confirm_portfolio_export(self, kind: str, lineups: List[Any]) -> bool:
        """Compatibility wrapper for integrations that used the former export confirmation."""
        sport = self._current_sport()
        if kind == "showdown":
            rows = [
                [self._display_id(lineup.get("Captain") or {}, slot="CPT")]
                + [self._display_id(player, slot="FLEX") for player in list(lineup.get("Flex") or [])]
                for lineup in lineups
            ]
            cap = self._safe_float(self.edit_sd_cap.text(), 50000.0)
        else:
            rows = [self._classic_export_cells(lineup, sport) for lineup in lineups]
            cap = self._safe_float(self.edit_cl_cap.text(), 50000.0)
        if not self._confirm_final_lock_export(kind, lineups, cap):
            return False
        return self._confirm_entry_safety_export(kind, lineups, rows, cap)

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

