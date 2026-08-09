from __future__ import annotations

import time
import unittest

from optimizers import MultiSportClassicOptimizer, _pkey, lineup_is_complete_for_sport
from test_nfl_logic import _fixture_players


class ClassicPerformanceTests(unittest.TestCase):
    def test_high_volume_build_is_fast_complete_unique_and_reports_progress(self):
        events = []
        started = time.perf_counter()
        lineups = MultiSportClassicOptimizer(
            _fixture_players(),
            sport="NFL",
            salary_cap=50000,
            build_style="Strategic",
            salary_strategy="Near Cap",
        ).build_lineups(
            150,
            progress_callback=lambda done, total, text: events.append((done, total, text)),
        )
        elapsed = time.perf_counter() - started

        self.assertEqual(len(lineups), 150)
        self.assertLess(elapsed, 10.0)
        self.assertEqual(events[0][:2], (0, 150))
        self.assertEqual(events[-1][:2], (150, 150))
        self.assertEqual([event[0] for event in events], sorted(event[0] for event in events))

        signatures = set()
        for lineup in lineups:
            self.assertTrue(lineup_is_complete_for_sport(lineup, "NFL"))
            self.assertLessEqual(sum(float(player["FlexSalary"]) for player in lineup), 50000)
            signatures.add(tuple(sorted(_pkey(player) for player in lineup)))
        self.assertEqual(len(signatures), 150)


if __name__ == "__main__":
    unittest.main()
