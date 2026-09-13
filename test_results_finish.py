import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
import test_results_snapshot_learning as fixtures
from learning_db import import_historical_result_csvs,generate_learning_report
from performance_review import analyze_saved_results
from results_field_ownership import observed_ownership
from results_finish import username_finishes


class ResultsFinishTests(unittest.TestCase):
    def test_username_finishes_without_exports_and_unknown_denominators(self):
        with tempfile.TemporaryDirectory() as folder:
            source,_,_=fixtures.ResultsSnapshotLearningTests().fixture(folder)
            db=str(Path(folder)/'history.sqlite')
            import_historical_result_csvs([str(source)],username='Example_User',db_path=db,archive_files=False)
            result=generate_learning_report(db_path=db,username='Example_User')
            self.assertEqual(result['matched_rows'],0)
            self.assertEqual(result['username_finishes']['covered'],2)
            self.assertAlmostEqual(result['username_finishes']['avg_percentile'],100-50/30)
            self.assertEqual(result['username_finishes']['top_one_pct'],50)
            self.assertIn('2 username-matched entries',result['text'])
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(username_finishes(conn,'Example.User')['entries'],0)
                conn.execute('UPDATE historical_results SET rank_text="30"')
                f=username_finishes(conn,'Example_User');self.assertAlmostEqual(f['avg_percentile'],100/30);self.assertEqual(f['top_one_pct'],0)
                conn.execute('UPDATE historical_results SET rank_text="31"')
                self.assertEqual(username_finishes(conn,'Example_User')['covered'],0)
                conn.execute('UPDATE historical_results SET field_size=0,rank_text="1"')
                conn.execute('DELETE FROM contest_field_summaries')
                self.assertIsNone(username_finishes(conn,'Example_User')['avg_percentile'])
                conn.execute('UPDATE historical_results SET rank_text=""');conn.commit()
            report=generate_learning_report(db_path=db,username='Example_User')
            self.assertEqual(report['username_finishes']['covered'],0)
            self.assertIn('best rank: unavailable',report['text'])

    def test_snapshot_accuracy_uses_field_rosters_and_keeps_captain_zeros_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            source,_,_=fixtures.ResultsSnapshotLearningTests().fixture(folder,'showdown')
            db=str(Path(folder)/'history.sqlite')
            import_historical_result_csvs([str(source)],username='Example_User',db_path=db,archive_files=False)
            analyze_saved_results(db_path=db,username='Example_User')
            with closing(sqlite3.connect(db)) as conn:
                p=json.loads(conn.execute('SELECT payload FROM result_snapshot_reviews').fetchone()[0])
                alpha=next(r for r in p['players'] if r['player']=='alpha' and r['slot']=='FLEX')
                captain=next(r for r in p['players'] if r['player']=='alpha' and r['slot']=='Captain')
                self.assertEqual(alpha['actual_ownership'],0)
                self.assertEqual(captain['actual_ownership'],100)
                self.assertIn('Observed roster ownership',p['ownership_source'])
                import_id=conn.execute('SELECT import_id FROM historical_imports').fetchone()[0]
                conn.execute('DELETE FROM contest_field_summaries')
                own,reason=observed_ownership(conn,import_id,['alpha'])
                self.assertEqual(own,{});self.assertIn('unavailable',reason)


if __name__=='__main__':unittest.main()
