from __future__ import annotations

"""Validation and serialization for reusable contest payout profiles."""

import json
import math
import re
from typing import Any, Dict, Iterable, List, Mapping, Sequence


_PAYOUT_LINE = re.compile(
    r"^\s*(\d+)\s*(?:-\s*(\d+)\s*)?(?:=|:|,|\t)\s*\$?\s*([0-9][0-9,]*(?:\.\d+)?)\s*$"
)


def _positive_int(value: Any, label: str, *, minimum: int = 1) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a whole number.") from None
    if number < minimum:
        raise ValueError(f"{label} must be at least {minimum:,}.")
    return number


def _money(value: Any, label: str, *, positive: bool = False) -> float:
    try:
        number = float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a dollar amount.") from None
    if not math.isfinite(number) or number < 0 or (positive and number <= 0):
        qualifier = "greater than zero" if positive else "zero or greater"
        raise ValueError(f"{label} must be {qualifier}.")
    return round(number, 2)


def parse_payout_text(text: Any) -> List[Dict[str, Any]]:
    """Parse readable payout rows such as ``1 = $100,000`` or ``2-10 = 5,000``."""
    tiers: List[Dict[str, Any]] = []
    for line_number, raw in enumerate(str(text or "").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _PAYOUT_LINE.match(line)
        if not match:
            raise ValueError(
                f"Payout line {line_number} is not valid. Use a rank or range, then '=' and the payout."
            )
        start = int(match.group(1))
        end = int(match.group(2) or start)
        amount = _money(match.group(3), f"Payout on line {line_number}")
        tiers.append({"start": start, "end": end, "amount": amount})
    return tiers


def format_payout_text(tiers: Iterable[Mapping[str, Any]]) -> str:
    rows: List[str] = []
    for tier in tiers:
        start = int(tier.get("start", 0) or 0)
        end = int(tier.get("end", start) or start)
        amount = float(tier.get("amount", 0.0) or 0.0)
        rank = f"{start}" if start == end else f"{start}-{end}"
        rows.append(f"{rank} = ${amount:,.2f}")
    return "\n".join(rows)


def normalize_contest_profile(profile: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a JSON-safe, non-overlapping contest profile or raise ``ValueError``."""
    name = str(profile.get("name") or "").strip()
    if not name:
        raise ValueError("Contest name is required.")
    field_size = _positive_int(profile.get("field_size"), "Field size", minimum=2)
    if field_size > 5_000_000:
        raise ValueError("Field size cannot exceed 5,000,000 entries.")
    entry_fee = _money(profile.get("entry_fee"), "Entry fee", positive=True)
    user_entries = _positive_int(profile.get("user_entries", 1), "Your entries")
    if user_entries > field_size:
        raise ValueError("Your entries cannot exceed the contest field size.")

    raw_tiers = profile.get("payouts") or []
    if isinstance(raw_tiers, str):
        raw_tiers = parse_payout_text(raw_tiers)
    if not isinstance(raw_tiers, Sequence) or not raw_tiers:
        raise ValueError("Enter at least one payout tier.")

    tiers: List[Dict[str, Any]] = []
    for index, raw_tier in enumerate(raw_tiers, start=1):
        if not isinstance(raw_tier, Mapping):
            raise ValueError(f"Payout tier {index} is not valid.")
        start = _positive_int(raw_tier.get("start"), f"Payout tier {index} start")
        end = _positive_int(raw_tier.get("end", start), f"Payout tier {index} end")
        if end < start:
            raise ValueError(f"Payout tier {index} ends before it starts.")
        if end > field_size:
            raise ValueError(f"Payout tier {index} extends beyond the {field_size:,}-entry field.")
        amount = _money(raw_tier.get("amount"), f"Payout tier {index}")
        tiers.append({"start": start, "end": end, "amount": amount})

    tiers.sort(key=lambda tier: (tier["start"], tier["end"]))
    normalized: List[Dict[str, Any]] = []
    for tier in tiers:
        if normalized and tier["start"] <= normalized[-1]["end"]:
            raise ValueError(
                f"Payout ranges overlap at place {tier['start']:,}. Each finishing place can appear only once."
            )
        if (
            normalized
            and tier["start"] == normalized[-1]["end"] + 1
            and tier["amount"] == normalized[-1]["amount"]
        ):
            normalized[-1]["end"] = tier["end"]
        else:
            normalized.append(dict(tier))

    paid = [tier for tier in normalized if tier["amount"] > 0]
    if not paid:
        raise ValueError("At least one finishing place must pay more than $0.")
    cash_places = max(tier["end"] for tier in paid)
    prize_pool = sum(
        (tier["end"] - tier["start"] + 1) * tier["amount"]
        for tier in normalized
    )
    return {
        "name": name,
        "field_size": field_size,
        "entry_fee": entry_fee,
        "user_entries": user_entries,
        "payouts": normalized,
        "cash_places": cash_places,
        "top_prize": next(
            (tier["amount"] for tier in normalized if tier["start"] <= 1 <= tier["end"]),
            0.0,
        ),
        "prize_pool": round(prize_pool, 2),
        "model": "exact-payout-profile-v1",
    }


def payout_for_tied_ranks(
    payouts: Sequence[Mapping[str, Any]],
    first_rank: int,
    last_rank: int,
) -> float:
    """Split all prizes covered by a tie range equally across tied entries."""
    first = max(1, int(first_rank or 1))
    last = max(first, int(last_rank or first))
    total = 0.0
    for tier in payouts:
        start = max(first, int(tier.get("start", 0) or 0))
        end = min(last, int(tier.get("end", 0) or 0))
        if end >= start:
            total += (end - start + 1) * float(tier.get("amount", 0.0) or 0.0)
    return total / float(last - first + 1)


def load_profiles_json(raw: Any) -> Dict[str, Dict[str, Any]]:
    try:
        parsed = json.loads(str(raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    profiles: Dict[str, Dict[str, Any]] = {}
    for name, profile in parsed.items():
        if not isinstance(profile, Mapping):
            continue
        try:
            normalized = normalize_contest_profile({**dict(profile), "name": str(name or "").strip()})
        except ValueError:
            continue
        profiles[normalized["name"]] = normalized
    return dict(sorted(profiles.items(), key=lambda item: item[0].casefold()))


def dump_profiles_json(profiles: Mapping[str, Mapping[str, Any]]) -> str:
    cleaned: Dict[str, Dict[str, Any]] = {}
    for name, profile in profiles.items():
        clean_name = str(name or "").strip()
        if not clean_name:
            continue
        try:
            cleaned[clean_name] = normalize_contest_profile({**dict(profile), "name": clean_name})
        except ValueError:
            continue
    return json.dumps(
        dict(sorted(cleaned.items(), key=lambda item: item[0].casefold())),
        separators=(",", ":"),
    )
