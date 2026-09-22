import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
import test_results_snapshot_learning as fixtures
from learning_db import import_historical_result_csvs,generate_learning_report,_ImportCancelled
from performance_review import analyze_saved_results
from results_field_ownership import refresh_cached_profile,field_profile,VERSION
from results_snapshot_learning import forecast_group


class ResultsReportCorrectionsTests(unittest.TestCase):
    def test_observed_ownership_wins_over_wrong_listed_percentages_both_formats(self):
        for kind in ('classic','showdown'):
            with tempfile.TemporaryDirectory() as folder:
                source,_,_=fixtures.ResultsSnapshotLearningTests().fixture(folder,kind)
                source.write_text(source.read_text(encoding='utf-8').replace(',100,10',',1,10'),encoding='utf-8')
                db=str(Path(folder)/'history.sqlite')
                import_historical_result_csvs([str(source)],username='Example_User',db_path=db,archive_files=False)
                with closing(sqlite3.connect(db)) as c:
                    profile=json.loads(c.execute('SELECT ownership_profile_json FROM contest_field_summaries').fetchone()[0])
                    self.assertEqual(profile['version'],VERSION)
                    self.assertEqual(profile['field']['avg_twenty_plus_players'],6 if kind=='showdown' else 9)
                    self.assertEqual(profile['field']['avg_total_ownership'],600 if kind=='showdown' else 900)
                    self.assertGreater(profile['source_vs_computed_mae'],90)
                    c.execute("UPDATE contest_field_summaries SET ownership_profile_json='{}'");c.commit()
                analyze_saved_results(db_path=db,username='Example_User')
                with closing(sqlite3.connect(db)) as c:
                    updated=json.loads(c.execute('SELECT ownership_profile_json FROM contest_field_summaries').fetchone()[0])
                    self.assertEqual(updated,profile)
                    with patch('learning_db._preflight_complete_field_csv',side_effect=AssertionError('unnecessary reread')):
                        refresh_cached_profile(c,c.execute('SELECT import_id FROM historical_imports').fetchone()[0],source)
                report=generate_learning_report(db_path=db,username='Example_User')
                self.assertEqual(report['personal_results_count'],2)
                self.assertEqual(report['matched_rows'],0)
                self.assertEqual(report['snapshot_comparisons'],dict(contests=1,entries=2,unique=1))
                self.assertIn('Export-linked matches',report['text'])

    def test_cancelled_profile_stops_before_computation(self):
        with tempfile.TemporaryDirectory() as folder:
            source,_,_=fixtures.ResultsSnapshotLearningTests().fixture(folder)
            with self.assertRaises(_ImportCancelled):field_profile(source,30,cancelled=lambda:True)

    def test_roles_use_saved_evidence_and_do_not_treat_zero_as_inactive(self):
        cases=[(dict(Position='QB',NFLQBEligible=True,NFLDepthOrder=2),'Recorded starters / eligible QBs'),
               (dict(Position='QB',NFLQBEligible=False,NFLDepthOrder=2),'Backup QBs'),
               (dict(Position='WR',NFLDepthOrder=2),'Other depth roles / rotation'),
               (dict(Position='RB',NFLDepthOrder=1),'Recorded starters / eligible QBs'),
               (dict(Position='RB',NFLDepthOrder=1,NFLActive=False),'Recorded unavailable'),
               (dict(Position='WR',FlexProjection=0),'Unknown recorded role'),
               (dict(Position='QB',NFLQBEligible=False),'Unverified / excluded QBs')]
        for player,expected in cases:
            before=dict(player);self.assertEqual(forecast_group(player),expected);self.assertEqual(player,before)


if __name__=='__main__':unittest.main()
