from contextlib import closing
import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path
from data_io import read_players_csv
from learning_db import import_historical_result_csvs,attach_salary_csv_to_latest_field,_field_roster_signature


class ShowdownSalaryAttachmentTests(unittest.TestCase):
    def fixture(self,root):
        standings=root/'contest-standings-123.csv';salary=root/'DKEntries.csv'
        names=['Alpha One','Bravo Two','Charlie Three','Delta Four','Echo Five','Foxtrot Six']
        prices=[6000,7000,8000,4000,5000,3000]
        with standings.open('w',newline='',encoding='utf-8') as f:
            w=csv.writer(f);w.writerow(['Rank','EntryId','EntryName','Points','Lineup'])
            for i in range(30):
                captain=i%2;others=[n for j,n in enumerate(names) if j!=captain]
                w.writerow([i+1,1000+i,'entry',100-i,'CPT '+names[captain]+' FLEX '+' FLEX '.join(others)])
        with salary.open('w',newline='',encoding='utf-8') as f:
            w=csv.writer(f);w.writerow(['Entry ID','Contest Name','CPT','FLEX','Instructions'])
            w.writerow(['','','','','Position','Name + ID','Name','ID','Roster Position','Salary','Game Info','TeamAbbrev','AvgPointsPerGame'])
            for j,(name,price) in enumerate(zip(names,prices)):
                for slot,mult,pid in [('CPT',1.5,20000+j),('FLEX',1,10000+j)]:
                    w.writerow(['','','','','QB' if j==0 else 'WR',f'{name} ({pid})',name,pid,slot,price*mult,'NE@SEA 09/09/2026','NE' if j<3 else 'SEA',10])
        return standings,salary

    def test_embedded_prices_and_captain_swaps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);standings,salary=self.fixture(root);db=str(root/'test.sqlite')
            players=read_players_csv(str(salary));self.assertEqual(len(players),6)
            self.assertEqual(players[0]['CptSalary'],9000)
            import_historical_result_csvs([str(standings)],db_path=db,archive_files=False)
            result=attach_salary_csv_to_latest_field(str(salary),db_path=db)
            self.assertTrue(result['attached']);self.assertEqual(result['match_pct'],100)
            self.assertEqual(result['avg_salary'],36250)
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(conn.execute('select sport,roster_size,unique_lineups,metadata_coverage_pct from contest_field_summaries').fetchone(),('NFL',6,2,100.))
                self.assertEqual(conn.execute("select count(*) from contest_field_summaries where roster_size=9").fetchone()[0],0)

    def test_wrong_slate_and_missing_salary_table_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);standings,salary=self.fixture(root);db=str(root/'test.sqlite')
            import_historical_result_csvs([str(standings)],db_path=db,archive_files=False)
            text=salary.read_text();salary.write_text(text.replace('Alpha One','Other One').replace('Bravo Two','Other Two').replace('Charlie Three','Other Three'))
            result=attach_salary_csv_to_latest_field(str(salary),db_path=db)
            self.assertFalse(result['attached']);self.assertLess(result['match_pct'],70)
            salary.write_text('Entry ID,CPT,FLEX\n1,12345,67890\n')
            with self.assertRaisesRegex(ValueError,'No player salary table'):
                read_players_csv(str(salary))

    def test_captain_identity_and_classic_signature(self):
        a='CPT Alpha FLEX Bravo FLEX Charlie FLEX Delta FLEX Echo FLEX Foxtrot'
        b='CPT Bravo FLEX Alpha FLEX Charlie FLEX Delta FLEX Echo FLEX Foxtrot'
        self.assertNotEqual(_field_roster_signature(a),_field_roster_signature(b))
        self.assertIn('@cpt:alpha',_field_roster_signature(a))

    def test_submitted_entry_results_are_linked_once_with_captain_identity(self):
        from learning_db import record_export, generate_learning_report
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            standings, salary = self.fixture(root)
            db = str(root / 'personal.sqlite')
            with standings.open(newline='', encoding='utf-8') as handle:
                result_rows = list(csv.reader(handle))
            with standings.open('w', newline='', encoding='utf-8') as handle:
                csv.writer(handle).writerows([result_rows[0] + ['FPTS']] + [r + ['12.5'] for r in result_rows[1:]])
            players = read_players_csv(str(salary))
            with salary.open(newline='', encoding='utf-8') as handle:
                salary_rows = list(csv.reader(handle))
            headers = ['Entry ID','Contest Name','Contest ID','Entry Fee','CPT'] + ['FLEX'] * 5
            entries = []
            lineups = []
            rosters = []
            for captain in (0, 1):
                ids = [str(20000 + captain)] + [str(10000+j) for j in range(6) if j != captain]
                entries.append([str(1000+captain),'NFL Showdown 150-Max','123','0.50']+ids)
                lineups.append({'Captain': players[captain], 'Flex': [p for j,p in enumerate(players) if j != captain]})
                rosters.append(ids)
            # Third row names the wrong Captain for this actual entry; reject it.
            entries.append(['1002','NFL Showdown 150-Max','123','0.50'] + rosters[1])
            with salary.open('w', newline='', encoding='utf-8') as handle:
                csv.writer(handle).writerows([headers] + entries + salary_rows[1:])
            record_export(kind='showdown', sport='NFL', lineups=lineups, rows=rosters,
                          salary_cap=50000, export_path='test.csv', validation={}, db_path=db)
            import_historical_result_csvs([str(standings)], db_path=db, archive_files=False)
            result = attach_salary_csv_to_latest_field(str(salary), db_path=db)
            self.assertEqual(result['personal_results_added'], 2)
            self.assertEqual(result['personal_results_matched'], 2)
            again = attach_salary_csv_to_latest_field(str(salary), db_path=db)
            self.assertEqual(again['personal_results_added'], 0)
            with closing(sqlite3.connect(db)) as conn:
                rows = conn.execute('select actual_points, roi, winnings, matched_lineup_id from historical_results order by actual_points desc').fetchall()
                self.assertEqual([r[0] for r in rows], [100,99])
                self.assertTrue(all(r[1] is None and r[2] is None for r in rows))
                self.assertEqual(len({r[3] for r in rows}), 2)
