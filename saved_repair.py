"""Session-local saved-repair validation; no optimization or persistence policy."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from entry_safety import build_entry_safety_report
from portfolio_rules import _candidate_signature, lineup_players, player_key, portfolio_report


def signatures(lineups, kind):
    return tuple(_candidate_signature(lineup, kind) for lineup in lineups)


@dataclass
class BuildReceipt:
    kind: str
    sport: str
    requested: int
    context: dict
    repair: bool = False
    job_id: str = field(default_factory=lambda: uuid4().hex)
    source: Any = None
    operational: Any = None
    retained: Counter = field(default_factory=Counter)
    cancelled: bool = False
    disposition: str = "pending"
    recorded: bool = False
    thread_finished: bool = False


def validate_proposal(receipt, payload, players):
    """Check count, retained occurrences and accepted hard validation semantics."""
    lineups = list(payload.get("lineups") or [])
    if payload.get("requested") != receipt.requested or len(lineups) != receipt.requested:
        return "incomplete"
    if receipt.retained - Counter(signatures(lineups, receipt.kind)):
        return "retained_mismatch"
    rules = deepcopy(receipt.context["portfolio_rules"])
    effective = (payload.get("portfolio_report") or {}).get("effective_min_unique")
    if effective is not None:
        if not 1 <= int(effective) <= rules["min_unique"]:
            return "validation_failed"
        rules["min_unique"] = int(effective)
    # Minimum exposure is a selection priority/advisory, not a hard blocker.
    # Clear only these soft constraints for validation; keep the original report.
    validation_lineups = deepcopy(lineups)
    for constraint in rules.get("player_constraints", {}).values():
        constraint["MinPct"] = constraint["MinCptPct"] = None
    for lineup in validation_lineups:
        for player in lineup_players(lineup, receipt.kind):
            player["MinPct"] = player["MinCptPct"] = None
    report = portfolio_report(validation_lineups, rules, kind=receipt.kind, requested=receipt.requested)
    safety = build_entry_safety_report(
        lineups, kind=receipt.kind, sport=receipt.sport,
        salary_cap=receipt.context["salary_cap"], player_pool=players,
        portfolio_report=report,
    )
    if safety["blockers"]:
        return "validation_failed"
    # Entry Safety checks availability/roster legality; enforce selected locks
    # and fades as well, without rerunning the optimizer.
    for lineup in lineups:
        members = lineup_players(lineup, receipt.kind)
        keys = {player_key(p) for p in members}
        captain = player_key(lineup["Captain"]) if receipt.kind == "showdown" else ""
        for player in players:
            key = player_key(player)
            if player.get("LockFlex") and (key not in keys or key == captain):
                return "validation_failed"
            if receipt.kind == "showdown" and player.get("LockCpt") and key != captain:
                return "validation_failed"
            if key in keys and ((key == captain and player.get("FadeCpt")) or (key != captain and player.get("FadeFlex"))):
                return "validation_failed"
    return ""
