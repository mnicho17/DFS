Warning: truncated output (original token count: 93235)
Total output lines: 7876

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
                    "refinement_stop_reason", "refinement_seconds", "effective_min_unique",
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
from dk_entries import read_entries_template, write_updated_entries
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
        table.horizontalHeader().setSectionResizeMode(1, QtWi…43235 tokens truncated…True)
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

    def on_update_entries(self, kind: str) -> None:
        """Create a DK upload file by replacing rosters in an entries download."""
        kind_l = str(kind or "classic").strip().lower()
        sport = self._current_sport()
        if kind_l == "showdown":
            lineups: List[Any] = list(self.saved_showdown or [])
            rows = [
                [self._display_id(lineup.get("Captain") or {}, slot="CPT")]
                + [self._display_id(player, slot="FLEX") for player in lineup.get("Flex") or []]
                for lineup in lineups
            ]
        else:
            lineups = list(self.saved_classic or [])
            rows = [self._classic_export_cells(lineup, sport) for lineup in lineups]

        if not lineups:
            QtWidgets.QMessageBox.information(
                self,
                "No Saved Lineups",
                "Save the replacement lineups first, then choose the DraftKings entries file.",
            )
            return

        if not self._confirm_portfolio_export(kind_l, lineups):
            return

        source_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select DraftKings Entries CSV",
            "",
            "CSV Files (*.csv)",
        )
        if not source_path:
            return
        try:
            template = read_entries_template(source_path)
            expected_first = "CPT" if kind_l == "showdown" else get_roster_slots_for_sport(sport)[0]
            if template.roster_headers[0] != expected_first:
                raise ValueError(
                    f"This entries file starts with {template.roster_headers[0]}, but the saved "
                    f"{kind_l.title()} lineups start with {expected_first}."
                )
            if len(rows) != template.entry_count:
                raise ValueError(
                    f"The file has {template.entry_count} entries, but {len(rows)} lineups are saved. "
                    "Save exactly one lineup for every entry before creating the upload file."
                )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Entries File Error", str(exc))
            return

        base, _ = os.path.splitext(source_path)
        suggested = f"{base}-updated.csv"
        output_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save Updated DraftKings Entries",
            suggested,
            "CSV Files (*.csv)",
        )
        if not output_path:
            return
        if not output_path.lower().endswith(".csv"):
            output_path += ".csv"
        try:
            write_updated_entries(source_path, output_path, rows)
        except Exception as exc:
            logger.exception("DraftKings entries update failed")
            QtWidgets.QMessageBox.critical(self, "Entries Export Error", str(exc))
            return

        self.status.showMessage(
            f"Updated {template.entry_count} DraftKings entries in {output_path}.",
            10000,
        )
        QtWidgets.QMessageBox.information(
            self,
            "Entries File Ready",
            f"Updated {template.entry_count} entries and preserved their contest identifiers.\n\n"
            f"Upload this file to DraftKings:\n{output_path}",
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
        rules = self._portfolio_rules()
        previous = dict(self.last_portfolio_report or {})
        if (
            str(previous.get("kind") or "").lower() == kind_l
            and int(previous.get("lineup_count", 0) or 0) == len(selected)
            and previous.get("effective_min_unique") is not None
        ):
            rules["min_unique"] = int(previous["effective_min_unique"])
        return portfolio_report(selected, rules, kind=kind_l, requested=len(selected))

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

