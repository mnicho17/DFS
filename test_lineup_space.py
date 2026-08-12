from __future__ import annotations

import math
import unittest

from lineup_space import calculate_lineup_space, format_compact_count
from nfl_simulation import build_nfl_role_pool


def _players(position: str, count: int, *, team_prefix: str = "T"):
    return [
        {
            "Name": f"{position} {index}",
            "FlexID": f"{position}{index}",
            "Position": position,
            "Team": f"{team_prefix}{index % 8}",
            "FlexSalary": 3000 + index * 100,
            "CptSalary": 4500 + index * 150,
            "FlexProjection": 8 + index,
            "NFLAvailability": "ACTIVE",
            "NFLDepthOrder": 1,
        }
        for index in range(count)
    ]


class LineupSpaceTests(unittest.TestCase):
    def test_nfl_classic_matches_the_three_valid_flex_shapes(self):
        players = (
            _players("QB", 3) + _players("RB", 6) + _players("WR", 8)
            + _players("TE", 4) + _players("DST", 3)
        )
        report = calculate_lineup_space(players, sport="NFL", mode="classic", requested=150)
        expected = (
            math.comb(3, 1) * math.comb(6, 3) * math.comb(8, 3) * math.comb(4, 1) * math.comb(3, 1)
            + math.comb(3, 1) * math.comb(6, 2) * math.comb(8, 4) * math.comb(4, 1) * math.comb(3, 1)
            + math.comb(3, 1) * math.comb(6, 2) * math.comb(8, 3) * math.comb(4, 2) * math.comb(3, 1)
        )
        self.assertTrue(report["exact"])
        self.assertEqual(report["structural_combinations"], expected)
        self.assertEqual(report["requested"], 150)

    def test_fades_and_locks_reduce_the_space(self):
        players = (
            _players("QB", 3) + _players("RB", 6) + _players("WR", 8)
            + _players("TE", 4) + _players("DST", 3)
        )
        baseline = calculate_lineup_space(players, sport="NFL", mode="classic")
        players[0]["FadeFlex"] = True
        faded = calculate_lineup_space(players, sport="NFL", mode="classic")
        self.assertLess(faded["eligible"], baseline["eligible"])
        self.assertLess(faded["structural_combinations"], baseline["structural_combinations"])

        players[0]["FadeFlex"] = False
        players[0]["LockFlex"] = True
        locked = calculate_lineup_space(players, sport="NFL", mode="classic")
        self.assertEqual(locked["locked"], 1)
        self.assertLess(locked["structural_combinations"], baseline["structural_combinations"])

    def test_nfl_role_pool_omits_deep_backups_and_shrinks_space(self):
        players = []
        for team in ("A", "B", "C", "D"):
            for position, total in (("QB", 3), ("RB", 5), ("WR", 8), ("TE", 4), ("DST", 1)):
                group = _players(position, total, team_prefix=team)
                for index, player in enumerate(group):
                    player["Team"] = team
                    player["FlexID"] = f"{team}{position}{index}"
                    player["Name"] = f"{team} {position} {index}"
                    player["NFLDepthOrder"] = index + 1
                players.extend(group)
        full = calculate_lineup_space(players, sport="NFL", mode="classic")
        role_pool = build_nfl_role_pool(players, preserve_locks=True)
        narrowed = calculate_lineup_space(
            role_pool, sport="NFL", mode="classic", loaded_total=len(players), pool_label="NFL SIM role pool"
        )
        self.assertLess(narrowed["eligible"], full["eligible"])
        self.assertLess(narrowed["structural_combinations"], full["structural_combinations"])
        self.assertEqual(narrowed["omitted"], len(players) - len(role_pool))

    def test_compact_count_keeps_round_hundreds_and_large_suffixes(self):
        self.assertEqual(format_compact_count(100), "100")
        self.assertEqual(format_compact_count(1200), "1.2K")
        self.assertEqual(format_compact_count(2_400_000_000), "2.4B")


if __name__ == "__main__":
    unittest.main()
