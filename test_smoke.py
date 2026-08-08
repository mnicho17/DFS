# test_smoke.py
from __future__ import annotations

import sys
from logger_setup import setup_logging

from data_io import read_players_csv
from injury_api import enrich_players_with_injuries
from optimizers import ShowdownOptimizer, MultiSportClassicOptimizer


def main() -> int:
    log = setup_logging("dfs", log_file="dfs_debug.log")
    if len(sys.argv) < 2:
        print("Usage: python test_smoke.py <path_to_DK_csv>")
        return 2

    path = sys.argv[1]
    log.info("Smoke test starting with CSV: %s", path)

    players = read_players_csv(path)
    enrich_players_with_injuries(players)

    inj_count = sum(1 for p in players if p.get("InjuryStatus"))
    log.info("Players loaded: %d | InjuryStatus populated: %d", len(players), inj_count)

    # Showdown
    sd = ShowdownOptimizer(players, salary_cap=50000)
    sd_lineups = sd.build_lineups(num_lineups=3)
    log.info("Showdown built: %d", len(sd_lineups))

    # Classic
    cl = MultiSportClassicOptimizer(players, sport="NFL", salary_cap=50000, build_style="Strategic", salary_strategy="Near Cap")
    cl_lineups = cl.build_lineups(num_lineups=3)
    log.info("Classic built: %d", len(cl_lineups))

    print("OK ✅  (See dfs_debug.log for details)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
