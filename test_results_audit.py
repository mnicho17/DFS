import csv
import tempfile
import unittest
from pathlib import Path
from learning_db import import_historical_result_csvs, generate_learning_report, record_export
from test_learning_results import _showdown_lineup, _export_rows

class ResultsAuditTests(unittest.TestCase):
    def test_scores_and_captain_checks_use_side_table_without_overwriting_entry_points(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=str(Path(tmp)/'history.sqlite');path=Path(tmp)/'NFL Showdown 20-Max.csv'
            lineup=_showdown_lineup();players=[lineup['Captain']]+lineup['Flex']
            names=[p['Name'] for p in players]
            for p in players:
                p.update(ProjOwnPct=40,ProjFlexOwnPct=30,ProjCptOwnPct=10,OwnershipUnits='percent_of_entries',OwnershipSource='Test forecast')
            record_export(kind='showdown',sport='NFL',lineups=[lineup],rows=_export_rows(),salary_cap=50000,export_path='test.csv',validation={},db_path=db)
            with path.open('w',newline='',encoding='utf-8') as f:
                w=csv.writer(f);w.writerow(['Rank','EntryId','EntryName','Points','Lineup','Player','Roster Position','%Drafted','FPTS'])
                for i in range(30):
                    side=[names[i],'FLEX',10,10] if i<6 else [names[0],'CPT',5,15] if i==6 else ['','','','']
                    w.writerow([i+1,1000+i,'Example_User (1/1)' if i==0 else 'Other',65,'CPT '+names[0]+' FLEX '+' FLEX '.join(names[1:])]+side)
            import_historical_result_csvs([str(path)],username='Example_User',db_path=db,archive_files=False)
            text=generate_learning_report(db_path=db,username='Example_User')['text']
            self.assertIn('export-linked forecast matches 1, without export links 0',text)
            self.assertIn('Snapshot comparisons are separate below',text)
            self.assertIn('1 checked, 0 mismatches',text)
            self.assertIn('1 player pairs checked; 0 differ',text)
            self.assertIn('Largest player forecast misses',text)
            self.assertIn('Ownership units are not recorded',text)
            self.assertIn('Captain: 1 unique players; MAE 5.00 pp',text)
            self.assertIn('FLEX: 5 unique players; MAE 20.00 pp',text)
            self.assertNotIn(str(path),text)
            renamed = path.with_name('renamed NFL standings.csv')
            path.rename(renamed)
            import_historical_result_csvs([str(renamed)],username='Example_User',db_path=db,archive_files=False)
            path = renamed
            self.assertIn('1 checked, 0 mismatches',generate_learning_report(db_path=db,username='Example_User')['text'])
            # A corrupt Captain score must flag both consistency checks.
            text_csv=path.read_text(encoding='utf-8').replace(',CPT,5,15',',CPT,5,16')
            path.write_text(text_csv,encoding='utf-8')
            text=generate_learning_report(db_path=db,username='Example_User')['text']
            self.assertIn('1 checked, 1 mismatches',text)
            self.assertIn('1 player pairs checked; 1 differ',text)
            path.unlink()
            self.assertIn('Original standings unavailable',generate_learning_report(db_path=db,username='Example_User')['text'])

    def test_classic_nine_player_results_audit_and_name_matching(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=str(Path(tmp)/'classic.sqlite');path=Path(tmp)/'NFL Classic 20-Max.csv'
            names=['Alpha One','Bravo Two','Charlie Three','Delta Four','Echo Five','Foxtrot Six','Golf Seven','Hotel Eight','India Nine']
            positions=['QB','RB','RB','WR','WR','WR','TE','RB','DST']
            players=[{'Name':name,'Team':'BUF' if i<5 else 'NE','Position':positions[i],'FlexID':str(10000+i),'FlexSalary':5000,'FlexProjection':10,'BaseProjection':10,'ProjOwnPct':20,'ProjectionSource':'Imported projection'} for i,name in enumerate(names)]
            for p in players:p.update(OwnershipUnits='percent_of_entries',OwnershipSource='Test forecast')
            record_export(kind='classic',sport='NFL',lineups=[players],rows=[[p['FlexID'] for p in players]],salary_cap=50000,export_path='classic.csv',validation={},db_path=db)
            lineup=' '.join(slot+' '+name for slot,name in zip(['QB','RB','RB','WR','WR','WR','TE','FLEX','DST'],names))
            with path.open('w',newline='',encoding='utf-8') as f:
                w=csv.writer(f);w.writerow(['Rank','EntryId','EntryName','Points','Lineup','Player','Roster Position','%Drafted','FPTS'])
                for i in range(30):
                    side=[names[i],positions[i],20,5] if i<9 else ['','','','']
                    w.writerow([i+1,1000+i,'Example_User (1/2)' if i<2 else 'Other',45,lineup]+side)
            result=import_historical_result_csvs([str(path)],username='Example_User',db_path=db,archive_files=False)
            self.assertEqual(result['personal_results_added'],2)
            self.assertEqual(result['matched_rows'],2)
            audit=generate_learning_report(db_path=db,username='Example_User')['text'].split('Results audit',1)[1]
            self.assertIn('2 checked, 0 mismatches',audit)
            self.assertIn('Total: 9 unique players; MAE 0.00 pp',audit)
            self.assertNotIn('Captain actual scoring:',audit)
            self.assertIn('MAE 45.00',audit)
            self.assertIn('source Imported projection',audit)
