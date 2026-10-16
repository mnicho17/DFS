"""Date corrections preserve immutable evidence and one-to-many salary associations."""
from test_environment import install
install()
import copy
import json
import sqlite3
from contextlib import closing
from unittest.mock import patch
from PyQt5 import QtWidgets
import analysis_imports as ai
from analysis_date_corrections import save
from test_pairing_state import PairingFixture
from test_analysis_imports import results_file,salary_file


class DateCorrectionTests(PairingFixture):
    def seed_wrong_date(self,extra=None):
        result=results_file(self.results/'09_19_2026_NFL showdown.csv',entries=2,extra=extra)
        salary_file(self.salaries/'salary.csv')
        ai.import_folders(str(self.results),str(self.salaries),db_path=self.db)
        row=ai.pairing_state(self.db)['results'][0]
        return result,row,row['candidates'][0]

    def test_correction_preserves_source_and_manifest_and_qualifies_effective_date(self):
        result,row,salary=self.seed_wrong_date();original=result.read_bytes()
        with closing(sqlite3.connect(self.db)) as c:
            raw=c.execute('SELECT manifest FROM analysis_sources WHERE hash=?',(row['hash'],)).fetchone()[0]
        self.assertTrue(salary['date_correctable']);self.assertFalse(salary['compatible'])
        save(row['hash'],salary['hash'],'Filename contains wrong date',db_path=self.db)
        state=ai.pairing_state(self.db);fixed=state['results'][0]
        self.assertEqual(fixed['status'],'PAIRED');self.assertEqual(fixed['dates'],['2026-09-21'])
        self.assertEqual(fixed['date_correction']['original_dates'],['2026-09-19'])
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(raw,c.execute('SELECT manifest FROM analysis_sources WHERE hash=?',(row['hash'],)).fetchone()[0])
            ident=c.execute('SELECT import_id FROM analysis_sources WHERE hash=?',(row['hash'],)).fetchone()[0]
            _,receipt=ai.paired_metadata(c,ident)
            self.assertEqual(receipt['date_correction']['corrected_date'],'2026-09-21')
        self.assertEqual(result.read_bytes(),original)
        self.assertIn('Corrected contest date','\n'.join(ai.report_lines(state=state)))

    def test_two_contests_can_correct_to_same_salary_without_consuming_it(self):
        _,row,salary=self.seed_wrong_date()
        results_file(self.results/'09_20_2026_NFL showdown.csv',entries=2,contest_id='second')
        ai.import_folders(str(self.results),str(self.salaries),db_path=self.db)
        for r in ai.pairing_state(self.db)['results']:
            save(r['hash'],salary['hash'],'Incorrect filename date',db_path=self.db)
        state=ai.pairing_state(self.db)
        self.assertEqual(state['qualified_saved_pairings'],2)
        self.assertTrue(all(r['candidates'][0]['linked_contests']==2 for r in state['results']))

    def test_csv_date_conflict_is_never_overridden_even_for_legacy_manifest(self):
        _,row,salary=self.seed_wrong_date(extra={'ContestDate':'09/19/2026'})
        # Old manifests did not distinguish CSV dates; saving must read the original.
        with closing(sqlite3.connect(self.db)) as c:
            raw=json.loads(c.execute('SELECT manifest FROM analysis_sources WHERE hash=?',(row['hash'],)).fetchone()[0]);raw.pop('csv_dates')
            c.execute('UPDATE analysis_sources SET manifest=? WHERE hash=?',(json.dumps(raw),row['hash']));c.commit()
        with self.assertRaisesRegex(ValueError,'Explicit CSV dates'):
            save(row['hash'],salary['hash'],'Incorrect filename date',db_path=self.db)
        self.assertEqual(ai.pairing_state(self.db)['saved_pairings'],0)

    def test_missing_id_and_changed_sources_remain_blocked(self):
        _,row,salary=self.seed_wrong_date()
        with closing(sqlite3.connect(self.db)) as c:
            src=next(s for s in ai._sources(c) if s['hash']==row['hash'])
            raw=copy.deepcopy(src['manifest']);raw['observed'].append(['FLEX','unknown player','9999'])
            c.execute('UPDATE analysis_sources SET manifest=? WHERE hash=?',(json.dumps(raw),row['hash']));c.commit()
        with self.assertRaisesRegex(ValueError,'identities'):
            save(row['hash'],salary['hash'],'Incorrect filename date',db_path=self.db)
        self.assertEqual(ai.pairing_state(self.db)['saved_pairings'],0)

    def test_cancellation_rolls_back_both_correction_and_pair(self):
        _,row,salary=self.seed_wrong_date()
        with self.assertRaises(ai.ImportCancelled):
            save(row['hash'],salary['hash'],'Incorrect filename date',db_path=self.db,cancelled=lambda:True)
        self.assertEqual(ai.pairing_state(self.db)['saved_pairings'],0)

    def test_tampered_correction_and_existing_pair_are_not_replaced(self):
        _,row,salary=self.seed_wrong_date()
        save(row['hash'],salary['hash'],'Incorrect filename date',db_path=self.db)
        with self.assertRaisesRegex(ValueError,'Existing pairing'):
            save(row['hash'],salary['hash'],'Different proposed reason',db_path=self.db)
        with closing(sqlite3.connect(self.db)) as c:
            c.execute("UPDATE analysis_date_corrections SET payload='{}'");c.commit()
        with self.assertRaisesRegex(ValueError,'date correction.*(conflicts|damaged)'):
            ai.pairing_state(self.db)

    def test_dialog_requires_explicit_date_confirmation_and_reason(self):
        _,row,salary=self.seed_wrong_date()
        self.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from analysis_imports_ui import SalaryMatchesDialog
        dialog=SalaryMatchesDialog(state=ai.pairing_state(self.db));self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.salaries.count(),0)
        dialog.correct_date.setChecked(True)
        self.assertEqual(dialog.salaries.count(),1)
        self.assertIn('additional contests',dialog.candidate_details.text())
        dialog.date_reason.setText('Incorrect filename date')
        with patch.object(QtWidgets.QMessageBox,'information'):
            dialog._select()
        self.assertIsNone(dialog.selection)
        dialog.confirm_date.setChecked(True);dialog._select()
        self.assertEqual(dialog.selection,(row['hash'],salary['hash'],True,'Incorrect filename date'))

    def test_corrected_result_can_match_exact_pregame_capture(self):
        import csv
        import test_distribution_build_selection as fixtures
        from distribution_build_selection import catalog
        from performance_review import analyze_saved_results
        fixture=fixtures.SIMBuildSelectionTests()
        fixture.setUp();self.addCleanup(fixture.tearDown)
        with fixture.results.open(newline='') as f:
            rows=list(csv.reader(f))
        for row in rows[1:]:row[1]=str(int(row[1])+1000)
        for i in range(6,100):
            rows.append([i+1,1100+i,'Example_User',65,rows[1][4],'','',''])
        wrong=fixture.results.with_name('9_19_2099_NFL showdown.csv')
        with wrong.open('w',newline='') as f:csv.writer(f).writerows(rows)
        ai.import_folders(str(wrong.parent),str(fixture.salary.parent),db_path=fixture.db)
        state=ai.pairing_state(fixture.db)
        row=next(r for r in state['results'] if r['name']==wrong.name)
        salary=row['candidates'][0]
        ident=save(row['hash'],salary['hash'],'Incorrect filename date',db_path=fixture.db)
        with closing(sqlite3.connect(fixture.db)) as c:
            choices=catalog(c,ident,fixture.root)
            self.assertEqual(len(choices['candidates']),1)
            self.assertEqual(choices['candidates'][0]['input_id'],fixture.snap['input_id'])
        review=analyze_saved_results(db_path=fixture.db,username='Example_User',import_ids={ident})
        self.assertEqual(review['completed'],1,review['message'])
        with closing(sqlite3.connect(fixture.db)) as c:
            payload=json.loads(c.execute('SELECT payload FROM distribution_validations WHERE import_id=?',(ident,)).fetchone()[0])
            self.assertEqual(payload['status'],'matched')
            self.assertEqual(len(payload['rows']),6)
