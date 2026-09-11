import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from learning_db import record_export,import_historical_result_csvs,generate_learning_report
from performance_review import (analyze_saved_results,refresh_season_stats,construction,snapshot_metadata,result_date)
from test_learning_results import _showdown_lineup
from build_snapshots import create_snapshot,save_snapshot

class PerformanceReviewTests(unittest.TestCase):
    def test_showdown_cohorts_persist_and_same_day_scores_do_not_multiply(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=str(Path(tmp)/'history.sqlite')
            a=_showdown_lineup();roster=[a['Captain']]+a['Flex']
            b={'Captain':roster[1],'Flex':[roster[0]]+roster[2:]}
            record_export(kind='showdown',sport='NFL',lineups=[a,b],rows=[],salary_cap=50000,export_path='test.csv',validation={},db_path=db)
            paths=[]
            for contest in range(2):
                path=Path(tmp)/f'09_10_2026_NFL Showdown {contest}.csv';paths.append(path)
                with path.open('w',newline='',encoding='utf-8') as f:
                    writer=csv.writer(f);writer.writerow(['Rank','EntryId','EntryName','Points','Lineup','Player','Roster Position','%Drafted','FPTS'])
                    for i in range(100):
                        lu=a if i<10 else b
                        text='CPT '+lu['Captain']['Name']+' FLEX '+' FLEX '.join(p['Name'] for p in lu['Flex'])
                        side=[roster[i]['Name'],'FLEX',30,10] if i<6 else [roster[i-6]['Name'],'CPT',10,15] if i<8 else ['','','','']
                        writer.writerow([1 if i<2 else i+1,1000+contest*100+i,'Example_User' if i in (0,99) else 'Other',65,text]+side)
                import_historical_result_csvs([str(path)],username='Example_User',db_path=db,archive_files=False)
            result=analyze_saved_results(db_path=db,username='Example_User')
            self.assertEqual(result['completed'],2)
            con=sqlite3.connect(db)
            payload=json.loads(con.execute('SELECT payload FROM construction_reviews LIMIT 1').fetchone()[0])
            self.assertEqual(payload['groups']['field']['n'],100)
            self.assertEqual(payload['groups']['top1']['n'],2)
            self.assertEqual(payload['groups']['winners']['features']['Captain QB'],2)
            self.assertEqual(payload['groups']['field']['features']['Captain QB'],10)
            self.assertEqual(payload['groups']['yours']['mapped'],2)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM contest_player_scores').fetchone()[0],12)
            con.close()
            text=generate_learning_report(db_path=db,username='Example_User')['text']
            self.assertIn('6 deduplicated player/date observations',text)
            self.assertIn('Captain QB',text)
            self.assertIn('Saved forecast bias by player',text)
            self.assertEqual(analyze_saved_results(db_path=db,username='Example_User')['completed'],0)
            for path in paths:path.unlink()
            analyze_saved_results(db_path=db,username='Example_User')
            self.assertIn('Captain QB',generate_learning_report(db_path=db,username='Example_User')['text'])

    def test_classic_stack_and_flex_require_covered_metadata(self):
        positions=['QB','RB','RB','WR','WR','WR','TE','WR','DST']
        teams=['A','A','B','A','A','B','C','D','E']
        meta={str(i):dict(position=p,team=t,opponent={'A':'B','B':'A','C':'D','D':'C','E':'F'}[t]) for i,(p,t) in enumerate(zip(positions,teams))}
        result=construction(tuple(meta),meta)
        self.assertIn('QB + 2 receivers',result)
        self.assertIn('With opposing skill player',result)
        self.assertIn('FLEX WR',result)
        self.assertIn('Secondary opposing pair',result)
        del meta['8'];self.assertIsNone(construction(tuple(str(i) for i in range(9)),meta))

    def test_free_stats_are_idempotent_season_specific_and_preserve_cache_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=str(Path(tmp)/'history.sqlite')
            def fetch(year):return [dict(season=year,week=w,season_type='REG',player_id='id1',player_display_name='Example Player',position='RB',recent_team='SEA',fantasy_points_ppr=w*5,carries=w,targets=2) for w in range(1,5)]
            refresh_season_stats(2025,db_path=db,fetcher=fetch)
            refresh_season_stats(2025,db_path=db,fetcher=fetch)
            con=sqlite3.connect(db);self.assertEqual(con.execute('SELECT COUNT(*) FROM seasonal_player_stats').fetchone()[0],8);con.close()
            refresh_season_stats(2025,db_path=db,fetcher=lambda y:[])
            text=generate_learning_report(db_path=db)['text']
            self.assertIn('2025: season PPR 12.50 (4 observed games); last 3 15.00; earlier 5.00',text)
            self.assertIn('2024: season PPR',text)
            self.assertIn('unavailable; existing cache retained',text)
            self.assertIn('not DraftKings scoring',text)

    def test_snapshot_metadata_requires_matching_date_names_and_consensus(self):
        with tempfile.TemporaryDirectory() as tmp:
            players=[dict(Name='Example Player',Team='SEA',Opponent='NE',Position='RB',FlexSalary=5000,CptSalary=7500,GameInfo='NE@SEA 09/10/2026 08:00PM ET')]
            snap=create_snapshot(players,dict(sport='NFL',contest_kind='showdown'),{})
            save_snapshot(str(Path(tmp)/'one.json'),snap)
            self.assertEqual(snapshot_metadata(tmp,'2026-09-10',{'example player'})['example player']['salary'],5000)
            self.assertFalse(snapshot_metadata(tmp,'2026-09-11',{'example player'}))
            players[0]['FlexSalary']=6000
            save_snapshot(str(Path(tmp)/'two.json'),create_snapshot(players,dict(sport='NFL',contest_kind='showdown'),{}))
            self.assertNotIn('example player',snapshot_metadata(tmp,'2026-09-10',{'example player'}))
            self.assertEqual(result_date('2026-09-10.csv'),'2026-09-10')
            self.assertEqual(result_date('contest-193391004.csv'),'')
