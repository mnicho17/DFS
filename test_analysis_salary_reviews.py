from test_environment import install
install()
import csv
import copy
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from PyQt5 import QtWidgets
import analysis_imports as ai
from analysis_salary_reviews import save, latest
from analysis_imports_ui import SalaryMatchesDialog, CombinedImportWorker, import_summary
from test_pairing_state import PairingFixture
from test_analysis_imports import salary_file, results_file


class SalaryReviewTests(PairingFixture):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def seed_conflict(self,count=1):
        for i in range(count):
            results_file(self.results/f'contest-{i}.csv',fmt='classic',entries=2,contest_id=str(i),extra={'ContestDate':'09/21/2026'})
        path=salary_file(self.salaries/'salary.csv',fmt='classic')
        with path.open(encoding='utf-8-sig',newline='') as f:
            reader=csv.DictReader(f);fields=reader.fieldnames;rows=list(reader)
        for row in rows:
            if row['Name']=='Golf':row.update({'Position':'WR','Roster Position':'WR/FLEX'})
        with path.open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(rows)
        result=ai.import_folders(str(self.results),str(self.salaries),db_path=self.db)
        self.assertEqual(result['pairs_added'],0)
        return ai.pairing_state(self.db)

    def record(self,row,reason='This is the intended contest salary revision'):
        return save(row['hash'],row['candidates'][0]['hash'],reason,db_path=self.db)

    def test_role_conflict_is_precise_and_full_pair_is_still_rejected(self):
        row=self.seed_conflict()['results'][0];c=row['candidates'][0]
        self.assertIn('TE golf (role conflict; salary allows WR/FLEX)',c['reason'])
        self.assertEqual(c['matched']+1,c['observed'])
        with self.assertRaises(ValueError):ai.save_pair(row['hash'],c['hash'],db_path=self.db,confirm_date=True)

    def test_earlier_date_or_source_blockers_do_not_claim_zero_identity_coverage(self):
        row=self.seed_conflict()['results'][0]
        with closing(sqlite3.connect(self.db)) as c:
            sources=ai._sources(c)
        result=copy.deepcopy(next(s for s in sources if s['kind']=='results'))
        salary=next(s for s in sources if s['kind']=='salary')
        result['manifest']['dates']=['2026-09-22']
        q=ai.qualify_pair(result,salary)
        self.assertFalse(q['identity_checked']);self.assertIn('dates conflict',q['reason'])

    def test_review_is_persisted_separately_and_never_qualifies_consumers(self):
        row=self.seed_conflict()['results'][0];before=self.dump()
        with closing(sqlite3.connect(self.db)) as c:
            original={table:c.execute('SELECT * FROM '+table).fetchall() for table in ('analysis_sources','historical_results','analysis_salary_pairs')}
        self.assertTrue(self.record(row))
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(original,{table:c.execute('SELECT * FROM '+table).fetchall() for table in original})
        state=ai.pairing_state(self.db);r=state['results'][0]
        self.assertEqual(r['status'],'NEEDS_REVIEW_ASSOCIATION');self.assertIsNone(r['saved_pair'])
        self.assertEqual(state['saved_pairings'],0);self.assertEqual(state['qualified_saved_pairings'],0)
        self.assertEqual(state['unpaired_results'],1);self.assertEqual(state['review_associations'],1)
        with closing(sqlite3.connect(self.db)) as c:
            import_id=c.execute("SELECT import_id FROM analysis_sources WHERE kind='results'").fetchone()[0]
            self.assertEqual(ai.paired_metadata(c,import_id),({},None))
            self.assertEqual(latest(c)[row['hash']]['salary_hash'],row['candidates'][0]['hash'])
        self.assertIn('Needs review','\n'.join(ai.report_lines(state=state)))
        self.assertNotEqual(before,self.dump())

    def test_many_results_can_review_the_same_salary_without_consuming_it(self):
        state=self.seed_conflict(3)
        for row in state['results']:self.record(row)
        after=ai.pairing_state(self.db)
        self.assertEqual(after['review_associations'],3);self.assertEqual(after['saved_pairings'],0)
        self.assertEqual(len({r['review_association']['salary_hash'] for r in after['results']}),1)

    def test_idempotence_and_revisions_keep_earlier_review(self):
        row=self.seed_conflict()['results'][0]
        self.assertTrue(self.record(row));self.assertFalse(self.record(row))
        self.assertTrue(self.record(row,'Additional explicit review context'))
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM analysis_salary_reviews').fetchone()[0],2)
            self.assertEqual(latest(c)[row['hash']]['reason'],'Additional explicit review context')

    def test_cancel_after_insert_rolls_back_review(self):
        row=self.seed_conflict()['results'][0];before=self.dump();calls=0
        def stop():
            nonlocal calls
            calls+=1
            return calls>=7
        with self.assertRaises(ai.ImportCancelled):save(row['hash'],row['candidates'][0]['hash'],'Explicit review context',db_path=self.db,cancelled=stop)
        self.assertEqual(before,self.dump())

    def test_changed_source_and_invalid_reason_do_not_save(self):
        row=self.seed_conflict()['results'][0]
        with self.assertRaises(ValueError):self.record(row,'short')
        with closing(sqlite3.connect(self.db)) as c:
            path=Path(c.execute('SELECT snapshot FROM analysis_sources WHERE hash=?',(row['hash'],)).fetchone()[0])
        path.write_text('changed',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'saved source changed'):self.record(row)
        with closing(sqlite3.connect(self.db)) as c:self.assertEqual(latest(c),{})

    def test_automatic_refresh_cannot_retarget_review_but_explicit_valid_pair_can_resolve_it(self):
        row=self.seed_conflict()['results'][0];self.record(row)
        salary_file(self.salaries/'corrected.csv',fmt='classic')
        result=ai.import_folders(str(self.results),str(self.salaries),db_path=self.db)
        self.assertEqual(result['pairs_added'],0);self.assertEqual(result['errors'],[])
        r=ai.pairing_state(self.db)['results'][0];valid=next(c for c in r['candidates'] if c['compatible'])
        with self.assertRaisesRegex(ValueError,'explicit review'):ai.save_pair(r['hash'],valid['hash'],db_path=self.db,automatic=True)
        self.assertTrue(ai.save_pair(r['hash'],valid['hash'],db_path=self.db))
        self.assertEqual(ai.pairing_state(self.db)['qualified_saved_pairings'],1)
        with self.assertRaisesRegex(ValueError,'Existing qualified'):self.record(row)
        with closing(sqlite3.connect(self.db)) as c:self.assertIn(row['hash'],latest(c))

    def test_compatible_sources_use_normal_pairing_not_review_bypass(self):
        row=self.seed()['results'][0]
        with self.assertRaisesRegex(ValueError,'normal Save'):self.record(row)
        self.assertEqual(ai.pairing_state(self.db)['saved_pairings'],0)

    def test_dialog_manual_inspection_requires_reason_and_confirmation(self):
        state=self.seed_conflict();d=SalaryMatchesDialog(state=state);self.addCleanup(d.deleteLater)
        self.assertEqual(d.salaries.count(),0)
        d.inspect_rejected.setChecked(True)
        self.assertEqual(d.salaries.count(),1)
        self.assertIn('Observed identities matched:',d.candidate_details.text())
        self.assertIn('salary allows WR/FLEX',d.candidate_details.text())
        self.assertFalse(d.buttons.button(QtWidgets.QDialogButtonBox.Save).isEnabled())
        d._select();self.assertIsNone(d.selection)
        self.assertFalse(d.record_review.isEnabled());d.date_reason.setText('Intended source, role conflict remains')
        self.assertFalse(d.record_review.isEnabled());d.confirm_review.setChecked(True)
        self.assertTrue(d.record_review.isEnabled());d._record_review()
        self.assertEqual(d.review_selection[:2],(state['results'][0]['hash'],state['results'][0]['candidates'][0]['hash']))
        self.assertIsNone(d.selection)

    def test_reopened_review_shows_recorded_salary_and_source_conflict(self):
        row=self.seed_conflict()['results'][0];self.record(row)
        d=SalaryMatchesDialog(state=ai.pairing_state(self.db));self.addCleanup(d.deleteLater)
        self.assertEqual(d.salaries.currentData()['hash'],row['candidates'][0]['hash'])
        self.assertIn('full qualification still pending',d.details.toPlainText())
        self.assertIn('role conflict',d.candidate_details.text())
        self.assertFalse(d.confirm_review.isChecked())

    def test_worker_review_only_does_not_reconcile_or_index_or_analyze(self):
        row=self.seed_conflict()['results'][0]
        w=CombinedImportWorker(salary_review=(row['hash'],row['candidates'][0]['hash'],'Explicit intended source selection'),db_path=self.db,refresh_history=True)
        finished=[];errors=[];w.finished.connect(finished.append);w.error.connect(errors.append)
        with patch('historical_identity.reconcile',side_effect=AssertionError('review must not qualify')),patch('opponent_history.sync_saved',side_effect=AssertionError('review must not index')),patch('learning_db.generate_learning_report',side_effect=AssertionError('review must not analyze')):
            w.run()
        self.assertEqual(errors,[]);self.assertTrue(finished[0]['review_only'])
        self.assertIn('No qualified pairing',import_summary(finished[0]))
        self.assertEqual(ai.pairing_state(self.db)['saved_pairings'],0)

    def test_readonly_catalog_without_reviews_does_not_create_review_table(self):
        self.seed_conflict();before=self.dump();state=ai.pairing_state(self.db)
        self.assertEqual(state['review_associations'],0);self.assertEqual(self.dump(),before)
