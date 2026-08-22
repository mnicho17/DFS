from __future__ import annotations

import unittest

from contest_profiles import (
    dump_profiles_json,
    format_payout_text,
    load_profiles_json,
    normalize_contest_profile,
    parse_payout_text,
    payout_for_tied_ranks,
)


class ContestProfileTests(unittest.TestCase):
    def _profile(self):
        return normalize_contest_profile({
            "name": "Sunday Main",
            "field_size": 1000,
            "entry_fee": "$20",
            "user_entries": 20,
            "payouts": "1 = $1,000\n2 = $500\n3-5 = $100\n6-100 = $40",
        })

    def test_readable_payouts_normalize_and_round_trip(self):
        profile = self._profile()
        self.assertEqual(profile["cash_places"], 100)
        self.assertEqual(profile["top_prize"], 1000.0)
        self.assertEqual(profile["prize_pool"], 5600.0)
        self.assertEqual(parse_payout_text(format_payout_text(profile["payouts"])), profile["payouts"])
        loaded = load_profiles_json(dump_profiles_json({profile["name"]: profile}))
        self.assertEqual(loaded["Sunday Main"], profile)

    def test_invalid_or_overlapping_payout_ranges_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "overlap"):
            normalize_contest_profile({
                "name": "Overlap", "field_size": 100, "entry_fee": 10,
                "user_entries": 1, "payouts": "1-5 = 20\n5-10 = 15",
            })
        with self.assertRaisesRegex(ValueError, "beyond"):
            normalize_contest_profile({
                "name": "Too Long", "field_size": 100, "entry_fee": 10,
                "user_entries": 1, "payouts": "1-101 = 20",
            })

    def test_ties_split_every_prize_in_the_rank_range(self):
        payouts = self._profile()["payouts"]
        self.assertEqual(payout_for_tied_ranks(payouts, 1, 2), 750.0)
        self.assertEqual(payout_for_tied_ranks(payouts, 2, 3), 300.0)
        self.assertEqual(payout_for_tied_ranks(payouts, 101, 105), 0.0)


if __name__ == "__main__":
    unittest.main()
