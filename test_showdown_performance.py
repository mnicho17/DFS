from __future__ import annotations

import time
import unittest

from optimizers import ShowdownOptimizer, _pkey


def _showdown_players():
    players = []
    for team_index, team in enumerate(("ARI", "CAR")):
        for index in range(18):
            salary = 2200 + ((index * 700 + team_index * 300) % 7600)
            projection = 5.0 + ((index * 2.1 + team_index * 1.3) % 21.0)
            player_id = f"{team_index + 1}{index:02d}"
            players.append({
                "Name": f"{team} Player {index + 1}",
                "Team": team,
                "Position": "QB" if index == 0 else ("RB" if index < 5 else "WR"),
                "FlexID": player_id,
                "FlexNamePlusID": f"{team} Player {index + 1} ({player_id})",
                "CptID": f"9{player_id}",
                "FlexSalary": float(salary),
                "CptSalary": float(round(salary * 1.5)),
                "FlexProjection": projection,
                "CptProjection": projection * 1.5,
                "ProjOwnPct": 2.0 + (index % 12),
            })
    return players


class ShowdownPerformanceTests(unittest.TestCase):
    def test_high_volume_build_is_fast_complete_unique_and_reports_progress(self):
        events = []
        started = time.perf_counter()
        lineups = ShowdownOptimizer(
            _showdown_players(),
            salary_cap=50000,
            build_style="Strategic",
        ).build_lineups(150, progress_callback=lambda done, total, text: events.append((done, total, text)))
        elapsed = time.perf_counter() - started

        self.assertEqual(len(lineups), 150)
        self.assertLess(elapsed, 15.0)
        self.assertEqual(events[0][:2], (0, 150))
        self.assertEqual(events[-1][:2], (150, 150))

        signatures = set()
        for lineup in lineups:
            captain = lineup["Captain"]
            flex = lineup["Flex"]
            self.assertEqual(len(flex), 5)
            keys = [_pkey(captain)] + [_pkey(player) for player in flex]
            self.assertEqual(len(set(keys)), 6)
            salary = float(captain["CptSalary"]) + sum(float(player["FlexSalary"]) for player in flex)
            self.assertLessEqual(salary, 50000)
            signatures.add((_pkey(captain), tuple(sorted(_pkey(player) for player in flex))))
        self.assertEqual(len(signatures), 150)

    def test_high_volume_build_honors_locks_fades_and_exposure_caps(self):
        players = _showdown_players()
        captain_lock = players[0]
        flex_lock = players[18]
        fade_flex = players[1]
        fade_cpt = players[19]
        capped_flex = players[2]
        captain_lock["LockCpt"] = True
        flex_lock["LockFlex"] = True
        fade_flex["FadeFlex"] = True
        fade_cpt["FadeCpt"] = True
        capped_flex["MaxPct"] = 20.0

        lineups = ShowdownOptimizer(players, build_style="Strategic").build_lineups(30)

        self.assertEqual(len(lineups), 30)
        self.assertTrue(all(lineup["Captain"] is captain_lock for lineup in lineups))
        self.assertTrue(all(flex_lock in lineup["Flex"] for lineup in lineups))
        self.assertTrue(all(fade_flex not in lineup["Flex"] for lineup in lineups))
        self.assertTrue(all(lineup["Captain"] is not fade_cpt for lineup in lineups))
        self.assertLessEqual(sum(capped_flex in lineup["Flex"] for lineup in lineups), 6)

    def test_high_volume_build_honors_captain_cap(self):
        players = _showdown_players()
        capped_captain = players[0]
        capped_captain["MaxCptPct"] = 10.0

        lineups = ShowdownOptimizer(players, build_style="Strategic").build_lineups(30)

        self.assertEqual(len(lineups), 30)
        self.assertLessEqual(sum(lineup["Captain"] is capped_captain for lineup in lineups), 3)

    def test_high_volume_build_can_be_cancelled(self):
        progress = []

        def on_progress(done, total, text):
            progress.append(done)

        def cancelled():
            return bool(progress and progress[-1] >= 7)

        lineups = ShowdownOptimizer(_showdown_players()).build_lineups(
            150,
            progress_callback=on_progress,
            cancel_callback=cancelled,
        )

        self.assertEqual(len(lineups), 7)
        self.assertEqual(progress[-1], 7)


if __name__ == "__main__":
    unittest.main()
