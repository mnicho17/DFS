from test_environment import install
install()
import json
import threading
from types import SimpleNamespace
import unittest
from PyQt5 import QtWidgets,QtCore
import analysis_imports as ai
from salary_availability import counts,review_count,summary,label
from test_hindsight_evidence import SourceFixture
from test_task_dashboard import FakeWorker
from task_dashboard import TaskHub
from task_attention import needs_attention


class SalaryAvailabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def fixture(self):
        f=SourceFixture();self.addCleanup(f.close)
        f.change('DELETE FROM analysis_salary_pairs')
        return f

    def refresh(self,f):
        return ai.import_folders(f.results,f.salaries,db_path=str(f.db),username='Synthetic_User')

    def no_match(self,f):
        f.change("UPDATE analysis_sources SET manifest=replace(manifest,'2026-09-21','2026-09-22') WHERE kind='salary'")

    def retired(self,result):
        hub=TaskHub();worker=FakeWorker();thread=QtCore.QThread()
        binding=hub.watch('History refresh',worker,thread,worker.stop.set)
        worker.finished.emit(result);thread.finished.emit()
        return hub.rows[binding.key]

    def test_missing_salary_is_informational_and_resolves_when_real_salary_arrives(self):
        f=self.fixture();original=f.raw_result.read_bytes();salary=f.raw_salary.read_bytes()
        f.change("DELETE FROM analysis_sources WHERE kind='salary'");f.raw_salary.unlink()
        result=self.refresh(f)
        self.assertEqual(counts(result),(1,0));self.assertEqual(result['errors'],[])
        row=self.retired(result)
        self.assertEqual(row['state'],'Completed');self.assertFalse(needs_attention(row))
        self.assertIn('Salary data not available',row['detail'])
        state=ai.pairing_state(f.db)
        self.assertEqual(state['results'][0]['status'],'NO_COMPATIBLE_MATCH')
        self.assertEqual(label(state['results'][0]),'Salary data not available')
        self.assertEqual(f.query("SELECT COUNT(*) FROM analysis_sources WHERE kind='salary'")[0][0],0)
        self.assertEqual(f.query('SELECT COUNT(*) FROM historical_imports')[0][0],1)
        self.assertEqual(f.query('SELECT COUNT(*) FROM analysis_salary_pairs')[0][0],0)
        self.assertEqual(f.raw_result.read_bytes(),original)
        f.raw_salary.write_bytes(salary)
        resolved=self.refresh(f)
        self.assertEqual(counts(resolved),(0,0));self.assertEqual(resolved['pairs_added'],1)
        self.assertEqual(f.raw_result.read_bytes(),original)

    def test_no_compatible_revision_does_not_invent_or_force_a_pair(self):
        f=self.fixture();self.no_match(f)
        result=self.refresh(f)
        self.assertEqual(counts(result),(1,0));self.assertFalse(needs_attention(self.retired(result)))
        self.assertEqual(f.query('SELECT COUNT(*) FROM analysis_salary_pairs')[0][0],0)

    def test_compatible_revision_awaiting_date_confirmation_still_needs_review(self):
        f=self.fixture()
        manifest=json.loads(f.query("SELECT manifest FROM analysis_sources WHERE kind='results'")[0][0])
        manifest['dates']=[]
        f.change("UPDATE analysis_sources SET manifest=? WHERE kind='results'",(json.dumps(manifest),))
        result=self.refresh(f)
        self.assertEqual(counts(result),(0,1));self.assertTrue(needs_attention(self.retired(result)))
        self.assertEqual(f.query('SELECT COUNT(*) FROM analysis_salary_pairs')[0][0],0)

    def test_supported_date_correction_remains_an_actionable_review(self):
        f=self.fixture()
        manifest=json.loads(f.query("SELECT manifest FROM analysis_sources WHERE kind='results'")[0][0])
        manifest.update(dates=['2026-09-21','2026-09-22'],csv_dates=['2026-09-21'])
        f.change("UPDATE analysis_sources SET manifest=? WHERE kind='results'",(json.dumps(manifest),))
        result=self.refresh(f)
        self.assertEqual(counts(result),(0,1));self.assertTrue(needs_attention(self.retired(result)))
        row=ai.pairing_state(f.db)['results'][0]
        self.assertEqual(label(row),'Date correction needs review')
        self.assertEqual(f.query('SELECT COUNT(*) FROM analysis_salary_pairs')[0][0],0)

    def test_manual_conflict_stays_reviewable_and_invalidates_notification_checkpoint(self):
        import history_refresh_cache as cache
        from analysis_salary_reviews import save
        f=self.fixture();self.no_match(f);result=self.refresh(f)
        worker=SimpleNamespace(results_folder=str(f.results),salary_folder=str(f.salaries),db_path=str(f.db),username='Synthetic_User',cancelled=threading.Event())
        fingerprint,_=cache.inputs(worker)
        cache.save(worker,fingerprint,result)
        cached=cache.read(worker,fingerprint)
        self.assertEqual(counts(cached),(1,0));self.assertFalse(needs_attention(self.retired(cached)))
        save(f.source['hash'],f.salary['hash'],'Intended contest revision needs date review.',db_path=f.db)
        self.assertIsNone(cache.read(worker,fingerprint))
        result=self.refresh(f)
        self.assertEqual(counts(result),(0,1));self.assertTrue(needs_attention(self.retired(result)))
        f.change("UPDATE analysis_sources SET manifest=replace(manifest,'2026-09-22','2026-09-21') WHERE kind='salary'")
        result=self.refresh(f)
        self.assertEqual(counts(result),(0,1))
        self.assertEqual(f.query('SELECT COUNT(*) FROM analysis_salary_pairs')[0][0],0)

    def test_changed_saved_result_is_an_error_instead_of_unavailable_salary(self):
        f=self.fixture();self.no_match(f)
        source=__import__('pathlib').Path(f.source['snapshot'])
        source.write_bytes(source.read_bytes()+b'changed')
        result=self.refresh(f)
        self.assertEqual(counts(result),(0,1));self.assertTrue(result['errors'])
        self.assertTrue(needs_attention(self.retired(result)))
        self.assertEqual(label(ai.pairing_state(f.db)['results'][0]),'Source needs review')

    def test_unreadable_results_remain_reviewable(self):
        f=self.fixture();self.no_match(f)
        manifest=json.loads(f.query("SELECT manifest FROM analysis_sources WHERE kind='results'")[0][0])
        manifest['unreadable']=1
        f.change("UPDATE analysis_sources SET manifest=? WHERE kind='results'",(json.dumps(manifest),))
        result=self.refresh(f)
        self.assertEqual(counts(result),(0,1));self.assertTrue(needs_attention(self.retired(result)))
        self.assertEqual(label(ai.pairing_state(f.db)['results'][0]),'Result data needs review')

    def test_real_errors_changes_and_cancellation_keep_existing_outcomes(self):
        base=dict(unpaired=2,salary_unavailable=2,salary_review_needed=0)
        for extra,state in ((dict(errors=['Disk unavailable']),'Needs review'),(dict(changed_sources=['Revision']),'Needs review'),(dict(error='Failed'),'Failed'),(dict(cancelled=True),'Cancelled')):
            with self.subTest(extra=extra):self.assertEqual(self.retired(dict(base,**extra))['state'],state)
        mixed=dict(unpaired=3,salary_unavailable=2,salary_review_needed=1)
        self.assertTrue(needs_attention(self.retired(mixed)))
        self.assertIn('Salary data not available',summary(mixed));self.assertIn('to review',summary(mixed))

    def test_legacy_and_inconsistent_counts_fail_closed_for_notifications(self):
        for result in (dict(unpaired=2),dict(unpaired=2,salary_unavailable=2),dict(unpaired=2,salary_unavailable=3,salary_review_needed=-1),dict(unpaired=1,salary_unavailable=True,salary_review_needed=0)):
            with self.subTest(result=result):
                self.assertIsNone(counts(result));self.assertTrue(review_count(result))
                self.assertTrue(needs_attention(self.retired(result)))
