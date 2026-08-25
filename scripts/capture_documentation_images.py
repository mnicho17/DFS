from __future__ import annotations

"""Capture reproducible, representative screenshots for the user guide."""

import argparse
import copy
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
from main_window import BuildDiagnosticsDialog, BuildRecipesDialog, ContestProfileDialog, EntrySafetyDialog, FinalLockCheckDialog, MainWindow, PortfolioInsightsDialog, ResultsLearningDialog, SlateReadinessDialog, StackExposureDialog  # noqa: E402
from build_diagnostics import create_build_diagnostic, save_build_diagnostic  # noqa: E402
from nfl_simulation import SimLineup  # noqa: E402
from optimizers import MultiSportClassicOptimizer  # noqa: E402
from portfolio_insights import build_portfolio_insights  # noqa: E402
from portfolio_rules import portfolio_report  # noqa: E402


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
    sources = [
        ("scenario_built", "Ceiling"),
        ("optimizer", ""),
        ("scenario_built", "Leverage"),
        ("field_shaped", ""),
        ("optimizer", ""),
        ("scenario_built", "Low-Dup"),
        ("field_shaped", ""),
        ("optimizer", ""),
    ]
    lineups: List[SimLineup] = []
    for index, lineup in enumerate(raw_lineups):
        edge = edges[index]
        source, archetype = sources[index]
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
            "sim_expected_payout": 24.0 - index * 0.6,
            "sim_expected_profit": 4.0 - index * 0.6,
            "sim_expected_roi_pct": 20.0 - index * 3.0,
            "sim_contest_name": "Sunday Main $20",
            "sim_entry_fee": 20.0,
            "sim_contest_field_size": 177_258,
            "sim_joint_portfolio": True,
            "sim_portfolio_cash_rate": 27.0 - index * 0.8,
            "sim_portfolio_scenarios": 750,
            "sim_portfolio_entry_count": 8,
            "sim_portfolio_expected_total_payout": 183.60,
            "sim_portfolio_expected_total_profit": 23.60,
            "sim_portfolio_expected_roi_pct": 14.75,
            "sim_portfolio_profit_probability_pct": 29.3,
            "sim_portfolio_roi_ci_low": -7.8,
            "sim_portfolio_roi_ci_high": 37.3,
        }, top_hits={index * 3 + 1, index * 3 + 2, index * 3 + 5},
            candidate_source=source, candidate_archetype=archetype))
    return lineups


def save_widget(widget: QtWidgets.QWidget, path: Path) -> None:
    QtWidgets.QApplication.processEvents()
    pixmap = widget.grab()
    if pixmap.isNull() or not pixmap.save(str(path), "PNG"):
        raise RuntimeError(f"Could not capture {path}")
    print(path.resolve())


def capture_contest_profile(
    output_dir: Path,
    parent: QtWidgets.QWidget | None = None,
) -> None:
    contest_dialog = ContestProfileDialog({
        "Sunday Main $20": {
            "name": "Sunday Main $20",
            "field_size": 177_258,
            "entry_fee": 20.0,
            "user_entries": 150,
            "payouts": [
                {"start": 1, "end": 1, "amount": 1_000_000.0},
                {"start": 2, "end": 2, "amount": 250_000.0},
                {"start": 3, "end": 5, "amount": 100_000.0},
                {"start": 6, "end": 10, "amount": 25_000.0},
                {"start": 11, "end": 100, "amount": 1_000.0},
                {"start": 101, "end": 1_000, "amount": 100.0},
                {"start": 1_001, "end": 35_000, "amount": 40.0},
            ],
        },
    }, "Sunday Main $20", parent)
    contest_dialog.resize(760, 680)
    contest_dialog.show()
    QtWidgets.QApplication.processEvents()
    save_widget(contest_dialog, output_dir / "contest-aware-sim.png")
    contest_dialog.close()


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
        window.last_portfolio_report = portfolio_report(
            lineups,
            window._portfolio_rules(),
            kind="classic",
            requested=len(lineups),
        )
        window.last_sim_report = {
            "field_preset": "150-Max",
            "field_lineup_count": 1500,
            "candidate_sources": {
                "generated": {"optimizer": 300, "field_shaped": 60, "scenario_built": 140},
                "selected": {"optimizer": 3, "field_shaped": 2, "scenario_built": 3},
                "selected_archetypes": {"Ceiling": 1, "Leverage": 1, "Low-Dup": 1},
            },
            "preset_comparison": {
                "available": True,
                "preset": "150-Max",
                "fit_score": 91,
                "summary": "Portfolio closely matches the 150-Max preset (91/100); largest gap is bring-back mix.",
            },
            "joint_portfolio": {
                "model": "joint-contest-portfolio-v2",
                "joint_portfolio": True,
                "contest_name": "Sunday Main $20",
                "field_size": 177_258,
                "entries_simulated": 8,
                "planned_entries": 8,
                "entry_count_match": True,
                "total_entry_cost": 160.0,
                "expected_total_payout": 183.60,
                "expected_total_profit": 23.60,
                "expected_roi_pct": 14.75,
                "roi_ci_low": -7.8,
                "roi_ci_high": 37.3,
                "profit_probability_pct": 29.3,
                "double_probability_pct": 11.1,
                "any_first_probability_pct": 0.4,
                "any_top_ten_probability_pct": 3.2,
                "payout_p10": 0.0,
                "payout_p50": 120.0,
                "payout_p90": 420.0,
                "scenarios": 750,
                "scenario_target": 750,
                "opponent_field_samples": 3,
                "stability": "Moderate",
                "adaptive_stopped": False,
                "volatility_model": "role-aware-player-volatility-v1",
                "rare_event_model": "guardrailed-breakout-tails-v1",
                "game_script_mix": {
                    "Balanced": 56.0, "Blowout": 14.0,
                    "Defensive": 13.0, "Shootout": 17.0,
                },
            },
        }
        window.tabs_lineups.setCurrentIndex(1)
        window.tabs_workspace_controls.setCurrentIndex(0)
        window.tbl_players.selectRow(0)
        window.status.showMessage("NFL Classic ready | 8 representative SIM-ranked lineups")
        window._build_progress.setVisible(False)
        window._build_eta.setVisible(False)
        window.show()
        app.processEvents()
        save_widget(window, output_dir / "main-workspace.png")

        window.combo_nfl_compute_mode.setCurrentText("Deep (up to 5 min)")
        window.action_show_build_controls.setChecked(True)
        window.tabs_workspace_controls.setCurrentIndex(0)
        app.processEvents()
        save_widget(window.tabs_workspace_controls, output_dir / "deep-build.png")
        window.action_show_build_controls.setChecked(False)
        window.combo_nfl_compute_mode.setCurrentText("Fast (default)")

        # Show the compact lineup-space dashboard under realistic pruning and
        # mid-build progress. The full workspace context makes the small strip
        # easy to locate when a reader returns to the live application.
        for player in players:
            if player["Name"] in {"MIA WR3", "DEN RB2", "DAL WR3", "SEA RB2"}:
                player["FadeFlex"] = True
                player["FadeCpt"] = True
        window.chk_nfl_contest_sim.setChecked(False)
        window._lineup_space_phase = "Generate 314/500"
        window._refresh_players_table()
        app.processEvents()
        save_widget(window, output_dir / "lineup-space.png")
        window.chk_nfl_contest_sim.setChecked(True)

        insights = build_portfolio_insights(
            lineups,
            sport="NFL",
            kind="classic",
            salary_cap=50000,
            field_preset="150-Max",
            source_label="generated",
            portfolio_report=window.last_portfolio_report,
            sim_report=window.last_sim_report,
        )
        insights_dialog = PortfolioInsightsDialog(insights, window)
        insights_dialog.resize(1240, 760)
        insights_dialog.tabs.setCurrentIndex(0)
        insights_dialog.show()
        app.processEvents()
        save_widget(insights_dialog, output_dir / "contest-portfolio-outlook.png")
        insights_dialog.tabs.setCurrentIndex(1)
        flagged_index = insights_dialog.filter_combo.findData("flagged")
        if flagged_index >= 0:
            insights_dialog.filter_combo.setCurrentIndex(flagged_index)
        insights_dialog._select_flagged()
        insights_dialog.show()
        app.processEvents()
        save_widget(insights_dialog, output_dir / "portfolio-insights.png")

        insights_dialog.tabs.setCurrentIndex(2)
        if insights_dialog.exposure_table.rowCount():
            insights_dialog.exposure_table.selectRow(0)
        app.processEvents()
        save_widget(insights_dialog, output_dir / "portfolio-exposure.png")
        insights_dialog.close()

        window.saved_classic = list(lineups)
        stack_payload = window._stack_exposure_payload()
        stack_dialog = StackExposureDialog(
            window,
            sport=str(stack_payload.get("sport", "NFL")),
            total_lineups=int(stack_payload.get("total", 0) or 0),
            team_rows=stack_payload.get("team_rows", []),
            stack_rows=stack_payload.get("stack_rows", []),
            salary_rows=stack_payload.get("salary_rows", []),
            pitcher_rows=stack_payload.get("pitcher_rows", []),
        )
        stack_dialog.resize(1040, 740)
        stack_dialog.show()
        app.processEvents()
        save_widget(stack_dialog, output_dir / "stack-exposure-nfl.png")
        stack_dialog.close()

        diagnostic = create_build_diagnostic(
            context={
                "sport": "NFL",
                "kind": "classic",
                "salary_cap": 50000,
                "requested_count": 150,
                "lineup_space": window._calculate_lineup_space(),
                "settings": {
                    "build_style": "Strategic",
                    "salary_strategy": "Near Cap",
                    "ownership_mode": "Leverage",
                    "ownership_weight": 0.15,
                    "sim_enabled": True,
                    "sim_scenarios": 750,
                    "field_preset": "150-Max",
                    "contest_profile": {
                        "name": "Sunday Main $20", "field_size": 177_258,
                        "entry_fee": 20.0, "user_entries": 8,
                    },
                },
                "portfolio_rules": {
                    "min_unique": 2,
                    "max_team_pct": 70,
                    "max_game_pct": 65,
                    "balance_ownership": True,
                    "groups": [{"kind": "never_together"}],
                    "player_constraints": {"representative-limit": {"MaxPct": 35}},
                },
            },
            timing_report={
                "generation_seconds": 14.02,
                "simulation_seconds": 5.11,
                "selection_seconds": 4.16,
                "total_seconds": 23.30,
                "candidate_target": 500,
                "optimizer_candidate_target": 300,
                "ownership_candidate_target": 60,
                "scenario_candidate_target": 140,
                "candidate_count": 500,
                "selected_count": 150,
                "requested_count": 150,
            },
            portfolio_report=window.last_portfolio_report,
            sim_report=window.last_sim_report,
            displayed_count=150,
        )
        earlier_diagnostic = copy.deepcopy(diagnostic)
        earlier_diagnostic["created_at"] = "2026-08-14T19:58:12-04:00"
        earlier_diagnostic["settings"]["field_preset"] = "20-Max"
        earlier_diagnostic["timing"].update({
            "generation_seconds": 17.80,
            "simulation_seconds": 5.40,
            "selection_seconds": 3.80,
            "total_seconds": 27.00,
        })
        earlier_diagnostic["sim"].update({
            "preset_fit": 84.0,
            "average_edge": 69.0,
            "average_return_index": 67.0,
            "average_duplicate_risk": 46.0,
            "top_one_scenarios_covered": 17,
        })
        diagnostic["created_at"] = "2026-08-14T20:03:35-04:00"
        save_build_diagnostic(earlier_diagnostic)
        save_build_diagnostic(diagnostic)
        build_history = BuildDiagnosticsDialog(window)
        build_history.resize(1500, 650)
        build_history.history_list.item(0).setSelected(True)
        build_history.history_list.item(1).setSelected(True)
        build_history.compare_selected()
        build_history.show()
        app.processEvents()
        save_widget(build_history, output_dir / "build-history.png")
        build_history.close()

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

        final_lock_dialog = FinalLockCheckDialog({
            "status": "attention",
            "title": "3 Saved Lineups Need Review",
            "used_cached_check": False,
            "sleeper_matches": len(players),
            "player_count": len(players),
            "affected_lineups": 3,
            "lineup_count": len(lineups),
            "affected_indexes": [0, 3, 6],
            "unavailable_players": ["MIA RB2"],
            "changes": [{
                "name": "MIA RB2",
                "team": "MIA",
                "availability": "OUT",
                "change": "Questionable -> Out",
                "lineup_numbers": [1, 4, 7],
            }, {
                "name": "BUF RB2",
                "team": "BUF",
                "availability": "QUESTIONABLE",
                "change": "Full practice -> Limited",
                "lineup_numbers": [4],
            }],
            "text": "FINAL LOCK CHECK - 3 saved lineups need review",
        }, window)
        final_lock_dialog.resize(1040, 620)
        final_lock_dialog.show()
        app.processEvents()
        save_widget(final_lock_dialog, output_dir / "final-lock-check.png")
        final_lock_dialog.close()

        for player in players:
            player["MaxPct"] = None
            player["MinPct"] = None
            if player["Name"] == "MIA RB2":
                player["NFLAvailability"] = "OUT"
        export_rows = [window._classic_export_cells(lineup, "NFL") for lineup in lineups]
        safety = window._entry_safety_report("classic", list(lineups), export_rows, 50000.0)
        safety_dialog = EntrySafetyDialog(safety, window)
        safety_dialog.resize(1040, 720)
        safety_dialog.show()
        app.processEvents()
        save_widget(safety_dialog, output_dir / "entry-safety.png")
        safety_dialog.close()
        for player in players:
            if player["Name"] == "MIA RB2":
                player["NFLAvailability"] = "STARTER"

        recipes_dialog = BuildRecipesDialog({
            "NFL 20-Max Fast": {
                "sport": "NFL", "contest_kind": "classic", "requested_lineups": 20,
                "build_style": "Strategic", "salary_strategy": "Near Cap",
                "nfl_sim_enabled": True, "nfl_field_preset": "20-Max",
                "nfl_compute_mode": "Fast (default)", "min_unique": 2,
            },
            "NFL 150-Max Deep": {
                "sport": "NFL", "contest_kind": "classic", "requested_lineups": 150,
                "build_style": "Strategic", "salary_strategy": "Near Cap",
                "nfl_sim_enabled": True, "nfl_field_preset": "150-Max",
                "nfl_compute_mode": "Deep (up to 5 min)", "min_unique": 2,
            },
            "NFL Single Entry": {
                "sport": "NFL", "contest_kind": "classic", "requested_lineups": 1,
                "build_style": "Balanced", "salary_strategy": "Maximize Salary",
                "nfl_sim_enabled": True, "nfl_field_preset": "Single Entry",
                "nfl_compute_mode": "Fast (default)", "min_unique": 1,
            },
        }, window)
        recipes_dialog.resize(720, 300)
        recipes_dialog.show()
        app.processEvents()
        save_widget(recipes_dialog, output_dir / "build-recipes.png")
        recipes_dialog.close()

        capture_contest_profile(output_dir, window)

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
    parser.add_argument("--only", choices=("all", "contest-aware-sim"), default="all")
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    if args.only == "contest-aware-sim":
        output_dir.mkdir(parents=True, exist_ok=True)
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
        app.setStyle("Fusion")
        app.setStyleSheet(DARK_QSS)
        capture_contest_profile(output_dir)
        app.processEvents()
    else:
        capture(output_dir)


if __name__ == "__main__":
    main()
