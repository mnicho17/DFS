from __future__ import annotations

import unittest

from build_recipes import dump_recipes_json, load_recipes_json, normalize_recipe


class BuildRecipeTests(unittest.TestCase):
    def test_recipe_keeps_build_settings_but_drops_slate_specific_rules(self):
        recipe = normalize_recipe({
            "sport": "nfl",
            "contest_kind": "classic",
            "requested_lineups": "20",
            "nfl_field_preset": "20-Max",
            "min_unique": 3,
            "groups": [{"type": "at_least_one", "player_keys": ["123"]}],
            "player_constraints": {"123": {"MaxPct": 20}},
            "locks": ["123"],
        })
        self.assertEqual(recipe["sport"], "NFL")
        self.assertEqual(recipe["requested_lineups"], 20)
        self.assertNotIn("groups", recipe)
        self.assertNotIn("player_constraints", recipe)
        self.assertNotIn("locks", recipe)

    def test_named_recipes_round_trip_in_stable_order(self):
        raw = dump_recipes_json({
            "Sunday Deep": {
                "sport": "NFL", "contest_kind": "classic", "requested_lineups": 150,
                "nfl_compute_mode": "Deep (up to 5 min)", "nfl_sim_enabled": True,
            },
            "20 Max": {
                "sport": "NFL", "contest_kind": "classic", "requested_lineups": 20,
                "nfl_field_preset": "20-Max",
            },
        })
        loaded = load_recipes_json(raw)
        self.assertEqual(list(loaded), ["20 Max", "Sunday Deep"])
        self.assertEqual(loaded["20 Max"]["requested_lineups"], 20)
        self.assertTrue(loaded["Sunday Deep"]["nfl_sim_enabled"])

    def test_malformed_recipe_storage_fails_closed(self):
        self.assertEqual(load_recipes_json("not-json"), {})
        self.assertEqual(load_recipes_json("[]"), {})


if __name__ == "__main__":
    unittest.main()
