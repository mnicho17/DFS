import csv
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from build_snapshots import create_snapshot,save_snapshot
from dk_entries import EntriesTemplate
from learning_db import import_historical_result_csvs,import_results_folder,generate_learning_report
from performance_review import analyze_saved_results
from results_snapshot_learning import match_snapshot,remember_contests,compare_snapshot


class ResultsSnapshotLearningTests(unittest.TestCase):
    def fixture(self,folder,kind='classic',dated=True):
        count=6 if kind=='showdown' else 9
        players=[dict(Name=['Alpha','Bravo','Charlie','Delta','Echo','Foxtrot','Golf','Hotel','India'][i],FlexID=str(100+i),Team='A' if i<3 else 'B',Opponent='B' if i<3 else 'A',
            Position=['QB','RB','WR','WR','TE','DST','RB','WR','WR'][i],FlexSalary=5000,CptSalary=7500,
            FlexProjection=8.,CptProjection=12.,ProjectionSource='Imported forecast',
            ProjOwnPct=50.,ProjFlexOwnPct=50.,ProjCptOwnPct=10.,OwnershipUnits='percent_of_entries',
            GameInfo='A@B 09/10/2026 08:00PM ET') for i in range(count)]
        snap=create_snapshot(players,dict(sport='NFL',contest_kind=kind),{});snap['created_at']='2026-09-10T19:00:00-04:00'
        path=Path(folder)/'snapshots'/(snap['input_id']+'.json');save_snapshot(str(path),snap)
        name='09_10_2026_NFL_'+kind+'.csv' if dated else 'contest-standings-123.csv'
        results=Path(folder)/name
        lineup=('CPT '+players[0]['Name']+' FLEX '+' FLEX '.join(p['Name'] for p in players[1:])) if kind=='showdown' else ' '.join(slot+' '+p['Name'] for slot,p in zip(['QB','RB','RB','WR','WR','WR','TE','FLEX','DST'],players))
        with results.open('w',newline='',encoding='utf-8') as f:
            w=csv.writer(f);w.writerow(['Rank','EntryId','EntryName','Points','Lineup','Player','Roster Position','%Drafted','FPTS'])
            for i in range(30):
                side=[players[i]['Name'],'FLEX' if kind=='showdown' else players[i]['Position'],100,10] if i<count else ['','','','']
                w.writerow([i+1,1000+i,'Example_User (1/20)' if i<2 else 'Other',65 if kind=='showdown' else 90,lineup]+side)
        return results,snap,path

    def test_results_and_username_without_any_export_both_formats(self):
        for kind in ('classic','showdown'):
            with tempfile.TemporaryDirectory() as folder:
                source,snap,path=self.fixture(folder,kind);db=str(Path(folder)/'history.sqlite')
                import_historical_result_csvs([str(source)],username='Example_User',db_path=db,archive_files=False)
                analyze_saved_results(db_path=db,username='Example_User')
                with closing(sqlite3.connect(db)) as conn:
                    p=json.loads(conn.execute('SELECT payload FROM result_snapshot_reviews').fetchone()[0])
                    self.assertEqual(conn.execute('SELECT COUNT(*) FROM exports').fetchone()[0],0)
                self.assertEqual(p['status'],'matched');self.assertEqual(p['personal_entries'],2)
                self.assertEqual(len(p['lineups']),1)
                self.assertEqual(p['lineups'][0]['projected'],52 if kind=='showdown' else 72)
                text=generate_learning_report(db_path=db,username='Example_User')['text']
                self.assertIn('Automatic results-to-snapshot comparison',text)
                self.assertIn('MAE 2.00',text)

    def test_future_snapshot_wrong_date_unknown_date_and_ambiguous_names(self):
        with tempfile.TemporaryDirectory() as folder:
            source,snap,path=self.fixture(folder);names={p['Name'].lower() for p in snap['inputs']['players']}
            self.assertIsNone(match_snapshot(folder,'','classic',names,'contest-standings-999.csv')[0])
            self.assertIsNone(match_snapshot(folder,'2026-09-11','classic',names,source.name)[0])
            snap['created_at']='2026-09-10T21:00:00-04:00';save_snapshot(str(path),snap)
            self.assertIsNone(match_snapshot(folder,'2026-09-10','classic',names,source.name)[0])
            snap['created_at']='2026-09-10T19:00:00-04:00';save_snapshot(str(path),snap)
            self.assertIsNone(match_snapshot(folder,'2026-09-10','showdown',names,source.name)[0])

    def test_contest_id_link_handles_standard_undated_filename(self):
        with tempfile.TemporaryDirectory() as folder:
            source,snap,path=self.fixture(folder,'showdown',False)
            template=EntriesTemplate([[],['1','Contest','123','$1']], [1],4,['CPT']+['FLEX']*5)
            remember_contests(template,'123',snap,folder)
            names={p['Name'].lower() for p in snap['inputs']['players']}
            found,reason=match_snapshot(folder,'','showdown',names,source.name)
            self.assertEqual(found['input_id'],snap['input_id']);self.assertEqual(reason,'contest-ID association')
            self.assertIsNone(match_snapshot(folder,'','showdown',names,'contest-standings-999.csv')[0])

    def test_folder_import_backfills_username_once_after_field_only_import(self):
        with tempfile.TemporaryDirectory() as folder:
            source,_,_=self.fixture(folder);db=str(Path(folder)/'history.sqlite')
            import_results_folder(folder,db_path=db,archive_files=False)
            r=import_results_folder(folder,username='Example_User',db_path=db,archive_files=False)
            self.assertEqual(r['personal_results_added'],2)
            r=import_results_folder(folder,username='Example_User',db_path=db,archive_files=False)
            self.assertEqual(r['personal_results_added'],0)
            with closing(sqlite3.connect(db)) as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM historical_results').fetchone()[0],2)

    def test_missing_snapshot_keeps_observed_personal_results(self):
        with tempfile.TemporaryDirectory() as folder:
            source,_,path=self.fixture(folder);path.unlink();db=str(Path(folder)/'history.sqlite')
            import_historical_result_csvs([str(source)],db_path=db,archive_files=False)
            analyze_saved_results(db_path=db,username='Example_User')
            with closing(sqlite3.connect(db)) as conn:
                p=json.loads(conn.execute('SELECT payload FROM result_snapshot_reviews').fetchone()[0])
            self.assertEqual(p['status'],'unavailable');self.assertEqual(p['personal_entries'],2)
            self.assertEqual(p['lineups'],[])

    def test_rejects_conflicting_snapshot_identity_and_preserves_first_timestamp(self):
        with tempfile.TemporaryDirectory() as folder:
            source,snap,path=self.fixture(folder,'showdown')
            template=EntriesTemplate([[],['1','Contest','123','$1']], [1],4,['CPT']+['FLEX']*5)
            original=path.read_bytes();snap['created_at']='2026-09-10T21:00:00-04:00'
            remember_contests(template,'123',snap,folder)
            self.assertEqual(path.read_bytes(),original)
            players=snap['inputs']['players'];names={p['Name'].lower() for p in players}
            players[0]['FlexProjection']=9
            other=create_snapshot(players,snap['inputs']['recipe'],{})
            other['created_at']='2026-09-10T19:00:00-04:00'
            save_snapshot(str(Path(folder)/'snapshots'/(other['input_id']+'.json')),other)
            self.assertIsNone(match_snapshot(folder,'2026-09-10','showdown',names,source.name)[0])

    def test_zero_forecast_and_score_reconciliation(self):
        with tempfile.TemporaryDirectory() as folder:
            source,snap,path=self.fixture(folder,'showdown');db=str(Path(folder)/'history.sqlite')
            players=snap['inputs']['players'];players[0].update(FlexProjection=0.,CptProjection=0.)
            zero=create_snapshot(players,snap['inputs']['recipe'],{});zero['created_at']=snap['created_at']
            path.unlink();save_snapshot(str(Path(folder)/'snapshots'/(zero['input_id']+'.json')),zero)
            import_historical_result_csvs([str(source)],username='Example_User',db_path=db,archive_files=False)
            analyze_saved_results(db_path=db,username='Example_User')
            with closing(sqlite3.connect(db)) as conn:
                p=json.loads(conn.execute('SELECT payload FROM result_snapshot_reviews').fetchone()[0])
                self.assertEqual(p['lineups'][0]['projected'],40.)
                conn.execute('UPDATE historical_results SET actual_points=99');conn.commit()
                import_id=conn.execute('SELECT import_id FROM historical_imports').fetchone()[0]
                p=compare_snapshot(conn,import_id,source.name,folder,'2026-09-10','showdown',
                    {r['Name'].lower():10 for r in players},{},'Example_User')
                self.assertEqual(p['personal_entries'],2);self.assertEqual(p['lineups'],[])


if __name__=='__main__':unittest.main()
