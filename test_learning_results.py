from __future__ import annotations

import csv
import json
import os
import sqlite3
import tempfile
import unittest

from learning_db import (
    _extract_lineup_tokens,
    generate_learning_report,
    import_historical_result_csvs,
    record_export,
)
from nfl_simulation import SimLineup
from optimizers import lineup_grade_for_sport


def _player(index: int, *, adjustment: float = 0.5):
    base = 10.0 + index
    return {
        "Name": ["Alpha One", "Bravo Two", "Charlie Three", "Delta Four", "Echo Five", "Foxtrot Six"][index],
        "Team": "KC" if index < 3 else "BUF",
        "Opponent": "BUF" if index < 3 else "KC",
        "Position": "QB" if index == 0 else "WR",
        "FlexID": str(10001 + index),
        "CptID": str(20001 + index),
        "FlexNamePlusID": f"Player {index} ({10001 + index})",
        "FlexSalary": 7000 + index * 100,
        "CptSalary": (7000 + index * 100) * 1.5,
        "BaseProjection": base,
        "FlexProjection": base + adjustment,
        "CptProjection": (base + adjustment) * 1.5,
        "ProjOwnPct": 10 + index,
        "ProjCptOwnPct": 5 + index,
        "NFLAdjScore": adjustment,
        "NFLUsageScore": 0.2,
        "NFLMatchupScore": 0.1,
        "NFLRoleScore": 0.1,
        "NFLWeatherScore": 0.1,
        "NFLVegas": 0.0,
    }


def _showdown_lineup():
    players = [_player(i) for i in range(6)]
    return {"Captain": players[0], "Flex": players[1:]}


def _export_rows():
    return [["20001", "10002", "10003", "10004", "10005", "10006"]]


def _classic_sim_lineup():
    return SimLineup(
        [_player(i) for i in range(6)],
        metrics={
            "sim_edge": 82.0,
            "sim_win_rate": 0.4,
            "sim_top_one_pct": 3.2,
            "sim_top_five_pct": 11.5,
            "sim_cash_rate": 27.0,
            "sim_bust_rate": 18.0,
            "sim_average_percentile": 66.0,
            "sim_ceiling": 168.5,
            "sim_return_index": 76.0,
            "sim_leverage": 71.0,
            "duplicate_risk": 24.0,
            "sim_scenarios": 500,
            "sim_field_lineups": 1200,
        },
    )


def _write_csv(path: str, rows):
    headers = [
        "Sport", "Contest Name", "Entry Name", "Entry Fee", "Winnings",
        "Points", "Rank", "Entries", "Places Paid", "Lineup",
    ]
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


class ResultsLearningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp.name, "learning.sqlite")

    def tearDown(self):
        self.temp.cleanup()

    def _record(self):
        return record_export(
            kind="showdown",
            sport="NFL",
            lineups=[_showdown_lineup()],
            rows=_export_rows(),
            salary_cap=50000,
            export_path=os.path.join(self.temp.name, "export.csv"),
            validation={"valid": True},
            app_version="test",
            db_path=self.db_path,
        )

    def _record_sim(self):
        return record_export(
            kind="classic",
            sport="NFL",
            lineups=[_classic_sim_lineup()],
            rows=[[str(10001 + i) for i in range(6)]],
            salary_cap=50000,
            export_path=os.path.join(self.temp.name, "sim-export.csv"),
            validation={"valid": True},
            grade_func=lineup_grade_for_sport,
            app_version="test-sim",
            db_path=self.db_path,
        )

    def test_export_records_base_adjusted_and_context_features(self):
        result = self._record()
        self.assertEqual(result["lineups_recorded"], 1)
        self.assertEqual(result["db_path"], self.db_path)
        conn = sqlite3.connect(self.db_path)
        try:
            lineup = conn.execute(
                "SELECT projection, base_projection, context_adjustment FROM lineups"
            ).fetchone()
            self.assertAlmostEqual(lineup[0] - lineup[1], lineup[2])
            self.assertAlmostEqual(lineup[2], 3.25)
            player = conn.execute(
                "SELECT base_projection, context_adjustment, context_json FROM lineup_players WHERE slot='CPT'"
            ).fetchone()
            self.assertAlmostEqual(player[0], 15.0)
            self.assertAlmostEqual(player[1], 0.75)
            self.assertEqual(json.loads(player[2])["NFLVegas"], 0.0)
        finally:
            conn.close()

    def test_export_persists_nfl_sim_metrics(self):
        self._record_sim()
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                """
                SELECT sim_edge, sim_top_one_pct, sim_top_five_pct, sim_cash_rate,
                       sim_return_index, sim_leverage, sim_duplicate_risk,
                       sim_scenarios, sim_field_lineups
                FROM lineups
                """
            ).fetchone()
            self.assertEqual(row, (82.0, 3.2, 11.5, 27.0, 76.0, 71.0, 24.0, 500, 1200))
        finally:
            conn.close()

    def test_draftkings_lineup_names_match_exact_export_and_update_outcomes(self):
        self._record()
        results_path = os.path.join(self.temp.name, "NFL standings.csv")
        lineup = (
            "CPT Alpha One FLEX Bravo Two FLEX Charlie Three FLEX Delta Four "
            "FLEX Echo Five FLEX Foxtrot Six"
        )
        _write_csv(results_path, [{
            "Sport": "NFL", "Contest Name": "Prime Time", "Entry Name": "mine",
            "Entry Fee": "$10", "Winnings": "$25", "Points": "101.5", "Rank": "1",
            "Entries": "100", "Places Paid": "20", "Lineup": lineup,
        }])

        imported = import_historical_result_csvs(
            [results_path], db_path=self.db_path, archive_files=False
        )
        self.assertEqual(imported["matched_rows"], 1)
        self.assertEqual(imported["unmatched_rows"], 0)
        conn = sqlite3.connect(self.db_path)
        try:
            result = conn.execute(
                "SELECT roi, percentile, cashed, top_one_pct, match_method FROM historical_results"
            ).fetchone()
            self.assertEqual(result[0], 15.0)
            self.assertEqual(result[1], 100.0)
            self.assertEqual(result[2], 1)
            self.assertEqual(result[3], 1)
            self.assertEqual(result[4], "player_names")
            lineup_result = conn.execute(
                "SELECT actual_points, roi, cashed, top_one_pct FROM lineups"
            ).fetchone()
            self.assertEqual(lineup_result, (101.5, 15.0, 1, 1))
        finally:
            conn.close()

    def test_id_matching_unmatched_rows_and_duplicate_file_fallback(self):
        self._record()
        results_path = os.path.join(self.temp.name, "NFL entries.csv")
        _write_csv(results_path, [
            {
                "Sport": "NFL", "Contest Name": "IDs", "Entry Name": "matched",
                "Entry Fee": "5", "Winnings": "0", "Points": "90", "Rank": "50",
                "Entries": "100", "Places Paid": "20",
                "Lineup": "20001,10002,10003,10004,10005,10006",
            },
            {
                "Sport": "NFL", "Contest Name": "IDs", "Entry Name": "unmatched",
                "Entry Fee": "5", "Winnings": "0", "Points": "80", "Rank": "75",
                "Entries": "100", "Places Paid": "20",
                "Lineup": "30001,30002,30003,30004,30005,30006",
            },
        ])
        first = import_historical_result_csvs([results_path], db_path=self.db_path, archive_files=False)
        second = import_historical_result_csvs([results_path], db_path=self.db_path, archive_files=False)
        self.assertEqual(first["matched_rows"], 1)
        self.assertEqual(first["unmatched_rows"], 1)
        self.assertEqual(second["duplicates_skipped"], 1)
        self.assertEqual(second["rows_imported"], 0)

    def test_compact_draftkings_headers_and_rank_field_size(self):
        self._record()
        path = os.path.join(self.temp.name, "NFL compact.csv")
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "Sport", "ContestName", "EntryName", "EntryFee", "Winnings",
                "Points", "Rank", "PlacesPaid", "Lineup",
            ])
            writer.writeheader()
            writer.writerow({
                "Sport": "NFL", "ContestName": "Compact", "EntryName": "mine",
                "EntryFee": "$3", "Winnings": "$6", "Points": "99",
                "Rank": "1 of 100", "PlacesPaid": "25",
                "Lineup": "20001,10002,10003,10004,10005,10006",
            })
        imported = import_historical_result_csvs([path], db_path=self.db_path, archive_files=False)
        self.assertEqual(imported["matched_rows"], 1)
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT contest_name, entry_name, entry_fee, percentile FROM historical_results"
            ).fetchone()
            self.assertEqual(row, ("Compact", "mine", 3.0, 100.0))
        finally:
            conn.close()

    def test_report_exposes_calibration_and_small_sample_guardrail(self):
        self._record()
        path = os.path.join(self.temp.name, "NFL report.csv")
        _write_csv(path, [{
            "Sport": "NFL", "Contest Name": "Report", "Entry Name": "mine",
            "Entry Fee": "10", "Winnings": "20", "Points": "100", "Rank": "10",
            "Entries": "1000", "Places Paid": "200",
            "Lineup": "20001,10002,10003,10004,10005,10006",
        }])
        import_historical_result_csvs([path], db_path=self.db_path, archive_files=False)
        report = generate_learning_report(db_path=self.db_path)
        self.assertEqual(report["matched_rows"], 1)
        self.assertAlmostEqual(report["roi_pct"], 100.0)
        self.assertIsNotNone(report["adjusted_mae"])
        self.assertIn("Base vs context comparison is directional only", report["text"])
        self.assertIn("No strategy tuning is recommended yet", report["text"])
        self.assertIn("Performance by ownership", report["text"])

    def test_report_validates_sim_predictions_against_results(self):
        self._record_sim()
        path = os.path.join(self.temp.name, "NFL sim report.csv")
        rows = []
        for index in range(10):
            rows.append({
                "Sport": "NFL", "Contest Name": "SIM Review", "Entry Name": f"entry-{index}",
                "Entry Fee": "10", "Winnings": "25" if index < 3 else "0",
                "Points": str(130 - index), "Rank": str(1 + index * 25),
                "Entries": "1000", "Places Paid": "200",
                "Lineup": "10001,10002,10003,10004,10005,10006",
            })
        _write_csv(path, rows)
        imported = import_historical_result_csvs([path], db_path=self.db_path, archive_files=False)
        self.assertEqual(imported["matched_rows"], 10)

        report = generate_learning_report(db_path=self.db_path)
        self.assertEqual(report["sim_matched_rows"], 10)
        self.assertIn("NFL SIM validation", report["text"])
        self.assertIn("Predicted top 1%", report["text"])
        self.assertIn("Predicted top 5%", report["text"])
        self.assertIn("Predicted cash rate", report["text"])
        self.assertIn("Performance by SIM Edge", report["text"])
        self.assertIn("directional until 50 matched entries", report["text"])

    def test_lineup_parser_handles_slots_suffixes_and_ids(self):
        names = _extract_lineup_tokens({
            "lineup": "QB Patrick Mahomes WR Marvin Harrison Jr. WR Amon-Ra St. Brown DST Cowboys"
        })
        self.assertEqual(
            names,
            ["Patrick Mahomes", "Marvin Harrison Jr.", "Amon-Ra St. Brown", "Cowboys"],
        )
        ids = _extract_lineup_tokens({"lineup": "CPT 20001 FLEX 10002 FLEX 10003"})
        self.assertEqual(ids, ["20001", "10002", "10003"])


if __name__ == "__main__":
    unittest.main()
