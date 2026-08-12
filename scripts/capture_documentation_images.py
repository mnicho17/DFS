from __future__ import annotations

"""Capture reproducible, representative screenshots for the user guide."""

import argparse
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List


os.environ.setdefault("QT_QPA_PLATFORM", "windows" if sys.platform == "win32" else "offscreen")
os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "0")
os.environ.setdefault("QT_SCALE_FACTOR", "1")

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PyQt5 import QtCore, QtWidgets  # noqa: E402

from app import DARK_QSS  # noqa: E402
from main_window import MainWindow, ResultsLearningDialog, SlateReadinessDialog  # noqa: E402
from nfl_simulation import SimLineup  # noqa: E402
from optimizers import MultiSportClassicOptimizer  # noqa: E402


GAMES = [
    ("BUF", "MIA", 48.5, -2.5),
    ("KC", "DEN", 46.0, -5.5),
    ("PHI", "DAL", 51.0, -1.5),
    ("SF", "SEA", 44.5, -3.0),
]


def representative_players() -> List[Dict[str, Any]]:
    players: List[Dict[str, Any]] = []
    player_id = 1001
    for game_index, (away, home, total, home_spread) in enumerate(GAMES):
        game = f"{away}@{home}"
        for team_index, (team, opponent) in enumerate(((away, home), (home, away))):
            is_home = team == home
            team_spread = home_spread if is_home else -home_spread
            implied_total = (total / 2.0) - (team_spread / 2.0)
            templates = [
                ("QB", "QB", 6600 + game_index * 100, 22.4 - game_index * 0.6, 10.8, 1),
                ("WR1", "WR", 6900 - game_index * 150, 18.1 - game_index * 0.4, 17.4, 1),
                ("WR2", "WR", 5500 + team_index * 100, 14.7 + game_index * 0.2, 11.2, 2),
                ("WR3", "WR", 4300 + game_index * 100, 10.9 + team_index * 0.4, 5.8, 3),
                ("RB1", "RB", 6500 - game_index * 100, 17.6 - team_index * 0.5, 14.6, 1),
                ("RB2", "RB", 4700 + game_index * 100, 11.8 + team_index * 0.3, 6.9, 2),
                ("TE", "TE", 4200 + team_index * 100, 11.2 + game_index * 0.2, 7.6, 1),
                ("DST", "DST", 3000 + game_index * 100, 7.8 - team_index * 0.3, 6.2, 1),
            ]
            for role_name, position, salary, projection, ownership, depth_order in templates:
                context_adjustment = round(((player_id % 5) - 2) * 0.18, 2)
                is_questionable = team == "MIA" and role_name == "WR2"
                player = {
                    "Name": f"{team} {role_name}",
                    "Team": team,
                    "Opponent": opponent,
                    "HomeAway": "H" if is_home else "A",
                    "GameKey": game,
                    "GameInfo": f"{game} Sun 1:00PM ET",
                    "Position": position,
                    "FlexSalary": float(salary),
                    "FlexProjection": round(projection + context_adjustment, 2),
                    "BaseProjection": float(projection),
                    "FlexID": str(player_id),
                    "FlexNamePlusID": f"{team} {role_name} ({player_id})",
                    "ProjOwnPct": ownership + game_index * 0.7 + team_index * 0.4,
                    "NFLAvailability": "QUESTIONABLE" if is_questionable else "STARTER",
                    "InjuryStatus": "Questionable" if is_questionable else "",
                    "NFLRosterStatus": "Active",
                    "NFLPractice": "Limited" if is_questionable else "Full",
                    "InjurySource": "Representative game-day data",
                    "LiveStatusUpdatedAt": "Today 11:42 AM ET",
                    "NFLDepthPosition": position,
                    "NFLDepthOrder": depth_order,
                    "NFLRole": "Starter" if depth_order == 1 else "Rotation",
                    "NFLRoleScore": 1.0 if depth_order == 1 else 0.3,
                    "NFLUsageScore": round(1.7 - depth_order * 0.35, 1),
                    "NFLMatchupScore": round(((game_index + team_index) % 4 - 1.5) * 0.5, 1),
                    "NFLWeatherScore": -0.2 if game == "BUF@MIA" else 0.0,
                    "NFLAdjScore": context_adjustment,
                    "NFLVegas": round((implied_total - 22.5) * 0.08, 2),
                    "NFLVegasTeamTotal": round(implied_total, 1),
                    "NFLVegasGameTotal": total,
                    "NFLVegasSpread": team_spread,
                    "NFLVegasBookmakers": 8,
                    "NFLVegasUpdatedAt": "Today 11:40 AM ET",
                    "NFLVegasState": "ok",
                    "LockFlex": False,
                    "FadeFlex": False,
                    "LockCpt": False,
                    "FadeCpt": False,
                    "MaxCptPct": None,
                    "MinCptPct": None,
                    "MaxPct": 35.0 if role_name in {"WR1", "RB1"} else None,
                    "MinPct": None,
                    "TeamAdjPct": 0.0,
                    "BattingOrder": 0,
                    "Bats": "",
                    "ConfirmedLineup": False,
                    "LineupStatus": "",
                }
                players.append(player)
                player_id += 1
    return players


def representative_lineups(players: List[Dict[str, Any]]) -> List[SimLineup]:
    raw_lineups = MultiSportClassicOptimizer(
        players,
        sport="NFL",
        build_style="Strategic",
        salary_strategy="Near Cap",
        own_mode="Leverage",
        own_weight=0.15,
    ).build_lineups(8)
    if len(raw_lineups) < 4:
        raise RuntimeError("Could not create enough representative NFL lineups for screenshots.")

    edges = [84.0, 79.0, 75.0, 71.0, 68.0, 65.0, 62.0, 59.0]
    lineups: List[SimLineup] = []
    for index, lineup in enumerate(raw_lineups):
        edge = edges[index]
        lineups.append(SimLineup(lineup, metrics={
            "sim_edge": edge,
            "sim_top_one_pct": round(3.4 - index * 0.22, 2),
            "sim_top_five_pct": round(13.1 - index * 0.55, 1),
            "sim_win_rate": round(0.46 - index * 0.035, 2),
            "sim_cash_rate": round(28.4 - index * 0.8, 1),
            "sim_bust_rate": round(15.8 + index * 0.9, 1),
            "sim_average_percentile": round(68.1 - index * 1.4, 1),
            "sim_ceiling": round(178.6 - index * 1.8, 1),
            "sim_return_index": max(50.0, 83.0 - index * 4.0),
            "sim_leverage": max(45.0, 76.0 - index * 3.0),
            "duplicate_risk": min(55.0, 21.0 + index * 4.0),
            "field_duplicate_estimate": round(0.8 + index * 0.18, 2),
            "sim_scenarios": 750,
            "sim_field_lineups": 1500,
        }))
    return lineups


def save_widget(widget: QtWidgets.QWidget, path: Path) -> None:
    QtWidgets.QApplication.processEvents()
    pixmap = widget.grab()
    if pixmap.isNull() or not pixmap.save(str(path), "PNG"):
        raise RuntimeError(f"Could not capture {path}")
    print(path.resolve())


def capture(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dfs-guide-") as data_dir:
        os.environ["DFS_OPTIMIZER_DATA_DIR"] = data_dir
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
        app.setStyle("Fusion")
        app.setStyleSheet(DARK_QSS)

        players = representative_players()
        lineups = representative_lineups(players)

        window = MainWindow()
        window.resize(1500, 940)
        window.players = players
        window.combo_sport.setCurrentText("NFL")
        window.spin_cl.setValue(150)
        window.combo_build_style.setCurrentText("Strategic")
        window.combo_build_own_mode.setCurrentText("Leverage")
        window.combo_salary_strategy.setCurrentText("Near Cap")
        window.spin_nfl_sim_scenarios.setValue(750)
        window.chk_nfl_contest_sim.setChecked(True)
        window.lbl_live_data.setText(
            "Live data 11:42 AM | Players 64/64 | 0 unavailable flags | "
            "4 slate games with Vegas lines"
        )
        window.lbl_live_data.setStyleSheet("color: #8FE3A1; padding: 1px 3px;")
        window._refresh_players_table()
        window._apply_player_column_visibility("NFL")
        window._populate_classic_lineups(lineups, "NFL")
        window.tabs_lineups.setCurrentIndex(1)
        window.tabs_workspace_controls.setCurrentIndex(0)
        window.tbl_players.selectRow(0)
        window.status.showMessage("NFL Classic ready | 8 representative SIM-ranked lineups")
        window._build_progress.setVisible(False)
        window._build_eta.setVisible(False)
        window.show()
        app.processEvents()
        save_widget(window, output_dir / "main-workspace.png")

        # Show the compact lineup-space dashboard under realistic pruning and
        # mid-build progress. The full workspace context makes the small strip
        # easy to locate when a reader returns to the live application.
        for player in players:
            if player["Name"] in {"MIA WR3", "DEN RB2", "DAL WR3", "SEA RB2"}:
                player["FadeFlex"] = True
                player["FadeCpt"] = True
        window._lineup_space_phase = "SIM 420/750"
        window._refresh_players_table()
        app.processEvents()
        save_widget(window, output_dir / "lineup-space.png")

        window.last_live_check_summary = {
            "sleeper_state": "ok",
            "sleeper": len(players),
            "total": len(players),
            "checked_at": "2026-09-13T15:55:00Z",
            "odds_state": "ok",
            "odds_matched_games": len(GAMES),
        }
        readiness = window._calculate_slate_readiness()
        readiness_dialog = SlateReadinessDialog(readiness, window)
        readiness_dialog.resize(1000, 600)
        for row, check in enumerate(readiness.get("checks") or []):
            if check.get("key") == "roles":
                readiness_dialog.table.selectRow(row)
                readiness_dialog.table.setCurrentCell(row, 0)
                break
        readiness_dialog.show()
        app.processEvents()
        save_widget(readiness_dialog, output_dir / "slate-readiness.png")
        readiness_dialog.close()
        window._lineup_space_phase = ""

        window.spin_portfolio_unique.setValue(2)
        window.spin_team_exposure.setValue(70.0)
        window.spin_game_exposure.setValue(65.0)
        window.portfolio_groups = [
            {"kind": "at_least_one", "label": "At least one: BUF WR1, MIA WR1"},
            {"kind": "never_together", "label": "Never together: KC RB1, DEN RB1"},
        ]
        window.lbl_portfolio_groups.setText("Groups: 2")
        window.lbl_portfolio_groups.setToolTip(
            "At least one: BUF WR1, MIA WR1\nNever together: KC RB1, DEN RB1"
        )
        window.tabs_workspace_controls.setCurrentIndex(1)
        app.processEvents()
        save_widget(window.tabs_workspace_controls, output_dir / "portfolio-rules.png")

        window.tabs_lineups.setCurrentIndex(1)
        window.tbl_cl.resizeColumnsToContents()
        app.processEvents()
        window.tbl_cl.horizontalScrollBar().setValue(window.tbl_cl.horizontalScrollBar().maximum())
        app.processEvents()
        save_widget(window.tabs_lineups, output_dir / "nfl-sim-results.png")

        dialog = ResultsLearningDialog()
        dialog.resize(900, 720)
        dialog.summary.setText(
            "150 exported lineups | 150 matched results | 150 SIM results | "
            "100.0% match rate | +18.4% ROI"
        )
        dialog.report.setPlainText(
            "DFS RESULTS & LEARNING\n"
            "\n"
            "NFL SIM validation\n"
            "Matched NFL SIM entries: 150\n"
            "Sample status: validation enabled (50-entry minimum met)\n"
            "\n"
            "Predicted vs actual\n"
            "Top 1%     Predicted 2.64%     Actual 2.00%\n"
            "Top 5%     Predicted 10.82%    Actual 10.00%\n"
            "Cash       Predicted 24.70%    Actual 26.00%\n"
            "\n"
            "Signal checks\n"
            "SIM Edge / finish-percentile correlation: +0.31\n"
            "Return index / net-results correlation: +0.27\n"
            "\n"
            "Performance by SIM Edge\n"
            "80-100:  28 entries | +31.6% ROI | 32.1% cash\n"
            "60-79:   77 entries | +16.8% ROI | 26.0% cash\n"
            "40-59:   45 entries |  +4.7% ROI | 20.0% cash\n"
            "\n"
            "Use these comparisons directionally. A larger sample across\n"
            "multiple slates is more useful than one contest result."
        )
        dialog.show()
        app.processEvents()
        save_widget(dialog, output_dir / "results-learning.png")

        dialog.close()
        window.close()
        app.processEvents()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="docs/images")
    args = parser.parse_args()
    capture(Path(args.output_dir))


if __name__ == "__main__":
    main()
