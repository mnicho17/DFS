from __future__ import annotations

"""Serialization helpers for reusable, slate-independent build settings."""

import json
from typing import Any, Dict, Mapping


RECIPE_KEYS = (
    "sport", "contest_kind", "requested_lineups", "salary_cap",
    "ownership_sims", "showdown_field_templates", "ownership_mode", "ownership_weight",
    "build_style", "mlb_stack_preference", "salary_strategy", "nfl_sim_enabled",
    "nfl_sim_scenarios", "nfl_field_preset", "nfl_compute_mode", "min_unique",
    "team_max_pct", "game_max_pct", "balance_ownership",
)


def normalize_recipe(recipe: Mapping[str, Any]) -> Dict[str, Any]:
    """Keep only portable build settings; never retain player-specific rules."""
    cleaned = {key: recipe[key] for key in RECIPE_KEYS if key in recipe}
    cleaned["sport"] = str(cleaned.get("sport") or "NFL").strip().upper()
    cleaned["contest_kind"] = (
        "showdown" if str(cleaned.get("contest_kind") or "classic").lower() == "showdown" else "classic"
    )
    for key in ("requested_lineups", "ownership_sims", "nfl_sim_scenarios", "min_unique"):
        if key in cleaned:
            try:
                cleaned[key] = int(cleaned[key])
            except (TypeError, ValueError):
                cleaned.pop(key, None)
    for key in ("salary_cap", "ownership_weight", "team_max_pct", "game_max_pct"):
        if key in cleaned:
            try:
                cleaned[key] = float(cleaned[key])
            except (TypeError, ValueError):
                cleaned.pop(key, None)
    for key in ("showdown_field_templates", "nfl_sim_enabled", "balance_ownership"):
        if key in cleaned:
            cleaned[key] = bool(cleaned[key])
    return cleaned


def load_recipes_json(raw: Any) -> Dict[str, Dict[str, Any]]:
    try:
        parsed = json.loads(str(raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    recipes: Dict[str, Dict[str, Any]] = {}
    for name, recipe in parsed.items():
        clean_name = str(name or "").strip()
        if clean_name and isinstance(recipe, Mapping):
            recipes[clean_name] = normalize_recipe(recipe)
    return dict(sorted(recipes.items(), key=lambda item: item[0].casefold()))


def dump_recipes_json(recipes: Mapping[str, Mapping[str, Any]]) -> str:
    cleaned = {
        str(name).strip(): normalize_recipe(recipe)
        for name, recipe in recipes.items()
        if str(name).strip()
    }
    return json.dumps(dict(sorted(cleaned.items(), key=lambda item: item[0].casefold())), separators=(",", ":"))
