from __future__ import annotations

import csv
import os
import tempfile
import unittest

from data_io import read_players_csv
from optimizers import (
    MultiSportClassicOptimizer,
    _nfl_lineup_features,
    lineup_grade_for_sport,
    lineup_is_complete_for_sport,
)


def _fixture_players():
    players = []
    pid = 1
    for away, home in [("BUF", "MIA"), ("KC", "DEN"), ("PHI", "DAL"), ("SF", "SEA")]:
        game = f"{away}@{home}"
        for team, opp in [(away, home), (home, away)]:
            players.append({
                "Name": f"{team} QB", "Team": team, "Opponent": opp,
                "GameKey": game, "GameInfo": game, "Position": "QB",
                "FlexSalary": 6500 + (pid % 5) * 100,
                "FlexProjection": 20 + (pid % 4), "FlexID": str(pid),
            })
            pid += 1
            for j in range(3):
                players.append({
                    "Name": f"{team} WR{j+1}", "Team": team, "Opponent": opp,
                    "GameKey": game, "GameInfo": game, "Position": "WR",
                    "FlexSalary": 4800 + j * 900 + (pid % 3) * 100,
                    "FlexProjection": 11 + j * 2.3 + (pid % 4), "FlexID": str(pid),
                })
                pid += 1
            for j in range(2):
                players.append({
                    "Name": f"{team} RB{j+1}", "Team": team, "Opponent": opp,
                    "GameKey": game, "GameInfo": game, "Position": "RB",
                    "FlexSalary": 5200 + j * 1200 + (pid % 4) * 100,
                    "FlexProjection": 13 + j * 2 + (pid % 3), "FlexID": str(pid),
                })
                pid += 1
            players.append({
                "Name": f"{team} TE", "Team": team, "Opponent": opp,
                "GameKey": game, "GameInfo": game, "Position": "TE",
                "FlexSalary": 3900 + (pid % 5) * 100,
                "FlexProjection": 9 + (pid % 4), "FlexID": str(pid),
            })
            pid += 1
            players.append({
                "Name": f"{team} DST", "Team": team, "Opponent": opp,
                "GameKey": game, "GameInfo": game, "Position": "DST",
                "FlexSalary": 2800 + (pid % 5) * 100,
                "FlexProjection": 7 + (pid % 3), "FlexID": str(pid),
            })
            pid += 1
    return players


class NFLLogicTests(unittest.TestCase):
    def test_dk_game_info_populates_opponent_and_home_away(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "dk.csv")
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=[
                    "Position", "Name + ID", "Name", "ID", "Roster Position",
                    "Salary", "Game Info", "TeamAbbrev", "AvgPointsPerGame", "Status",
                ])
                w.writeheader()
                w.writerow({
                    "Position": "QB", "Name + ID": "Josh Example (1)",
                    "Name": "Josh Example", "ID": "1", "Roster Position": "QB",
                    "Salary": "6500", "Game Info": "BUF@MIA 09/13/2026 01:00PM ET",
                    "TeamAbbrev": "BUF", "AvgPointsPerGame": "22.5", "Status": "Q",
                })
                w.writerow({
                    "Position": "WR", "Name + ID": "Miami Example (2)",
                    "Name": "Miami Example", "ID": "2", "Roster Position": "WR",
                    "Salary": "5900", "Game Info": "BUF@MIA 09/13/2026 01:00PM ET",
                    "TeamAbbrev": "MIA", "AvgPointsPerGame": "15.0", "Status": "",
                })
            players = read_players_csv(path)
            by_team = {p["Team"]: p for p in players}
            self.assertEqual(by_team["BUF"]["Opponent"], "MIA")
            self.assertEqual(by_team["BUF"]["HomeAway"], "A")
            self.assertEqual(by_team["MIA"]["Opponent"], "BUF")
            self.assertEqual(by_team["MIA"]["HomeAway"], "H")
            self.assertEqual(by_team["BUF"]["GameKey"], "BUF@MIA")
            self.assertEqual(by_team["BUF"]["Status"], "Q")

    def test_strategic_nfl_build_has_correlation_and_salary_quality(self):
        players = _fixture_players()
        opt = MultiSportClassicOptimizer(
            players,
            sport="NFL",
            salary_cap=50000,
            build_style="Strategic",
            salary_strategy="Near Cap",
        )
        lineups = opt.build_lineups(20)
        self.assertEqual(len(lineups), 20)
        for lineup in lineups:
            self.assertTrue(lineup_is_complete_for_sport(lineup, "NFL"))
            self.assertLessEqual(sum(float(p["FlexSalary"]) for p in lineup), 50000)
            self.assertGreaterEqual(sum(float(p["FlexSalary"]) for p in lineup), 48500)
            feat = _nfl_lineup_features(lineup)
            self.assertGreaterEqual(feat["qb_stack"], 1)
            self.assertFalse(feat["qb_vs_opp_dst"])
            self.assertLessEqual(feat["max_team"], 4)
            self.assertLessEqual(feat["max_game"], 5)

        # Strategic portfolios should prefer at least two different players between lineups.
        for i in range(1, min(8, len(lineups))):
            a = {p["FlexID"] for p in lineups[i]}
            for j in range(i):
                b = {p["FlexID"] for p in lineups[j]}
                self.assertGreaterEqual(9 - len(a & b), 2)

    def test_zero_max_pct_is_a_real_block(self):
        players = _fixture_players()
        target = next(p for p in players if p["Name"] == "BUF WR1")
        target["MaxPct"] = 0.0
        opt = MultiSportClassicOptimizer(players, sport="NFL", build_style="Strategic")
        lineups = opt.build_lineups(12)
        self.assertTrue(lineups)
        self.assertTrue(all(target not in lineup for lineup in lineups))

    def test_portfolio_max_pct_is_not_exceeded(self):
        players = _fixture_players()
        target = next(p for p in players if p["Name"] == "BUF WR2")
        target["MaxPct"] = 20.0
        lineups = MultiSportClassicOptimizer(
            players, sport="NFL", build_style="Strategic"
        ).build_lineups(10)
        self.assertEqual(len(lineups), 10)
        appearances = sum(1 for lineup in lineups if target in lineup)
        self.assertLessEqual(appearances, 2)

    def test_nfl_grade_reports_stack_shape(self):
        lineup = MultiSportClassicOptimizer(
            _fixture_players(), sport="NFL", build_style="Strategic"
        ).build_lineups(1)[0]
        grade = lineup_grade_for_sport(lineup, "NFL", 50000)
        self.assertIn("QB+", grade["stack_shape"])
        self.assertIn("BB", grade["stack_shape"])
        self.assertGreater(grade["score"], 0)


if __name__ == "__main__":
    unittest.main()
