"""Review grouping is read-only guidance, never salary or date qualification."""
from test_environment import install
install()
import copy
from contextlib import closing
import sqlite3
import unittest
from unittest.mock import patch
from PyQt5 import QtCore, QtWidgets
from PyQt5.QtTest import QTest
import analysis_imports as ai
from analysis_imports_ui import SalaryMatchesDialog
from salary_match_review import candidate_details,plan,groups,group_text,issue_text
import test_analysis_salary_reviews as reviews
import test_analysis_date_corrections as dates
from test_pairing_state import PairingFixture


class SalaryMatchReviewGroupsTests(PairingFixture):
    seed_conflict = reviews.SalaryReviewTests.seed_conflict
    record = reviews.SalaryReviewTests.record
    seed_wrong_date = dates.DateCorrectionTests.seed_wrong_date

    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_shared_revision_groups_three_contests_without_any_database_writes(self):
        state=self.seed_conflict(3);before=self.dump();group=groups(state['results'])[0]
        self.assertEqual(len(groups(state['results'])),1)
        self.assertEqual(len(group['result_hashes']),3)
        self.assertEqual(group['categories'],{'role_conflict':3})
        self.assertEqual(len(group['examples']),1)
        self.assertIn('Grouping is navigation only',group_text(group))
        self.assertEqual(self.dump(),before);self.assertEqual(state['saved_pairings'],0)

    def test_identity_categories_are_precise_and_do_not_change_qualification(self):
        result=dict(manifest=dict(format='showdown',dates=['day'],contest_ids=['one'],observed=[('FLEX','same name','1')]))
        cases=[([], 'missing_player'),
               ([dict(name='Same Name',id='2',role='FLEX')],'id_conflict'),
               ([dict(name='Different Name',id='1',role='FLEX')],'name_conflict'),
               ([dict(name='Same Name',id='1',role='CPT')],'role_conflict'),
               ([dict(name='Same Name',id='1',role='FLEX')]*2,'ambiguous_identity')]
        for players,category in cases:
            salary=dict(manifest=dict(format='showdown',dates=['day'],games=[],players=players))
            index=ai._salary_identity_index(salary);q=ai.qualify_pair(result,salary,_identity_index=index)
            original=copy.deepcopy((result,salary,q))
            info=candidate_details(result,salary,q,index)
            self.assertFalse(q['compatible']);self.assertEqual(info['categories'],{category:1})
            self.assertEqual(info['issues'][0]['player_id'],'1')
            self.assertEqual((result,salary,q),original)

    def test_detailed_issue_text_shows_both_identities_without_guessing_aliases(self):
        result=dict(manifest=dict(format='showdown',dates=['day'],contest_ids=['one'],observed=[('FLEX','same name','1')]))
        salary=dict(manifest=dict(format='showdown',dates=['day'],games=[],players=[dict(name='Same Name',id='2',role='FLEX')]))
        index=ai._salary_identity_index(salary);q=ai.qualify_pair(result,salary,_identity_index=index)
        text=issue_text(dict(review_details=candidate_details(result,salary,q,index)))
        self.assertIn('explicit ID 1',text);self.assertIn('IDs 2',text)
        self.assertIn('Explicit ID conflict',text);self.assertFalse(q['compatible'])

    def test_metadata_and_source_blockers_keep_identity_coverage_unknown(self):
        state=self.seed_conflict();row=state['results'][0]
        with closing(sqlite3.connect(self.db)) as c:sources=ai._sources(c)
        result=copy.deepcopy(next(s for s in sources if s['kind']=='results'))
        salary=next(s for s in sources if s['kind']=='salary');result['manifest']['dates']=['2026-09-22']
        q=ai.qualify_pair(result,salary);info=candidate_details(result,salary,q,ai._salary_identity_index(salary))
        self.assertFalse(info['identity_checked']);self.assertEqual(info['issues'],[])
        self.assertEqual(info['categories'],{'date':1})
        info=candidate_details(result,salary,dict(compatible=False),{},source_problem='Source changed')
        self.assertEqual(info['categories'],{'source':1});self.assertFalse(info['identity_checked'])

    def test_equal_candidates_never_choose_first_and_permutations_are_deterministic(self):
        state=self.seed_conflict(2);rows=copy.deepcopy(state['results'])
        for row in rows:
            second=copy.deepcopy(row['candidates'][0]);second.update(hash='another-revision',name='Other revision.csv')
            row['candidates'].append(second)
            self.assertIsNone(plan(row)['candidate']);self.assertEqual(plan(row)['kind'],'ambiguous')
        expected=groups(rows)
        reverse=list(reversed(copy.deepcopy(rows)))
        for row in reverse:row['candidates'].reverse()
        self.assertEqual(groups(reverse),expected)
        self.assertIsNone(expected[0]['candidate'])

    def test_ambiguous_compatible_revisions_require_a_user_selection_in_dialog(self):
        state=self.seed(salaries=2);before=self.dump()
        dialog=SalaryMatchesDialog(state=state);self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.salaries.count(),2);self.assertIsNone(dialog.salaries.currentData())
        self.assertFalse(dialog.inspect_group.isEnabled())
        self.assertFalse(dialog.buttons.button(QtWidgets.QDialogButtonBox.Save).isEnabled())
        dialog.salaries.setCurrentIndex(0)
        self.assertTrue(dialog.buttons.button(QtWidgets.QDialogButtonBox.Save).isEnabled())
        self.assertFalse(dialog.confirm_date.isChecked());self.assertIsNone(dialog.selection)
        self.assertEqual(self.dump(),before)

    def test_recorded_manual_choice_remains_review_target_over_a_better_alternative(self):
        state=self.seed_conflict();row=state['results'][0];self.record(row)
        state=ai.pairing_state(self.db);row=copy.deepcopy(state['results'][0]);chosen=row['candidates'][0]['hash']
        alternative=copy.deepcopy(row['candidates'][0]);alternative.update(hash='alternative',compatible=True,matched=9999)
        row['candidates'].append(alternative)
        self.assertEqual(plan(row)['candidate']['hash'],chosen)
        self.assertEqual(plan(row)['kind'],'recorded');self.assertIsNone(row['saved_pair'])

    def test_group_navigation_and_inspection_do_not_save_or_confirm(self):
        state=self.seed_conflict(3);before=self.dump();dialog=SalaryMatchesDialog(state=state);self.addCleanup(dialog.deleteLater)
        with patch.object(ai,'_verify',side_effect=AssertionError('UI must not reverify sources')):
            dialog.review_groups.setCurrentIndex(1)
            self.assertEqual(dialog.results.count(),3)
            self.assertIn('3 contests',dialog.group_details.toPlainText())
            dialog.inspect_group.click()
        self.assertEqual(dialog.salaries.count(),1)
        self.assertIn('Roster role conflict',dialog.candidate_details.text())
        self.assertFalse(dialog.buttons.button(QtWidgets.QDialogButtonBox.Save).isEnabled())
        self.assertFalse(dialog.confirm_review.isChecked());self.assertFalse(dialog.record_review.isEnabled())
        self.assertIsNone(dialog.selection);self.assertIsNone(dialog.review_selection)
        self.assertEqual(self.dump(),before)

    def test_date_review_action_still_requires_reason_and_confirmation(self):
        _,row,candidate=self.seed_wrong_date();before=self.dump()
        dialog=SalaryMatchesDialog(state=ai.pairing_state(self.db));self.addCleanup(dialog.deleteLater)
        dialog.review_groups.setCurrentIndex(1);dialog.inspect_group.click()
        self.assertTrue(dialog.correct_date.isChecked());self.assertFalse(dialog.confirm_date.isChecked())
        self.assertEqual(dialog.date_reason.text(),'');self.assertEqual(dialog.salaries.currentData()['hash'],candidate['hash'])
        with patch.object(QtWidgets.QMessageBox,'information'):
            dialog._select();self.assertIsNone(dialog.selection)
            dialog.confirm_date.setChecked(True);dialog._select();self.assertIsNone(dialog.selection)
        self.assertEqual(self.dump(),before)
        dialog.date_reason.setText('Verified recorded contest date');dialog._select()
        self.assertEqual(dialog.selection,(row['hash'],candidate['hash'],True,'Verified recorded contest date'))
        self.assertEqual(self.dump(),before)

    def test_filtering_clears_confirmation_and_reason_without_changing_rows(self):
        state=self.seed_conflict(2);before=copy.deepcopy(state)
        dialog=SalaryMatchesDialog(state=state);self.addCleanup(dialog.deleteLater)
        dialog.inspect_group.click();dialog.date_reason.setText('Recorded intended revision');dialog.confirm_review.setChecked(True)
        self.assertTrue(dialog.record_review.isEnabled())
        dialog.review_groups.setCurrentIndex(1)
        self.assertFalse(dialog.confirm_review.isChecked());self.assertEqual(dialog.date_reason.text(),'')
        dialog.review_groups.setCurrentIndex(0);self.assertEqual(dialog.results.count(),2)
        self.assertEqual(state,before)

    def test_long_names_do_not_force_review_dialog_wider_than_small_window(self):
        state=self.seed_conflict();state=copy.deepcopy(state)
        state['results'][0]['name']='Long contest title '*30+'.csv'
        state['results'][0]['candidates'][0]['name']='Long salary revision title '*30+'.csv'
        dialog=SalaryMatchesDialog(state=state);self.addCleanup(dialog.deleteLater)
        # Match the application's actual theme/font rather than offscreen Qt's
        # platform-default font; keep this test's styling local to the dialog.
        from app import DARK_QSS
        dialog.setStyleSheet(DARK_QSS)
        dialog.review_groups.setCurrentIndex(1);dialog.inspect_group.click()
        dialog.show();self.app.processEvents();dialog.resize(780,650);self.app.processEvents()
        self.assertLessEqual(dialog.minimumSizeHint().width(),780)
        self.assertEqual(dialog.width(),780);self.assertEqual(dialog.height(),650)
        self.assertLess(dialog.buttons.geometry().bottom(),dialog.height())
        self.assertIn(state['results'][0]['candidates'][0]['name'],dialog.group_details.toPlainText())
        self.assertIn(state['results'][0]['name'],dialog.details.toPlainText())
        self.assertEqual(dialog.salaries.currentData()['hash'],state['results'][0]['candidates'][0]['hash'])

    def test_wrapped_confirmation_keeps_full_wording_and_click_does_not_save(self):
        state=self.seed_conflict();before=self.dump()
        dialog=SalaryMatchesDialog(state=state);self.addCleanup(dialog.deleteLater)
        dialog.inspect_group.click();dialog.date_reason.setText('This is the intended source revision')
        dialog.show();self.app.processEvents()
        self.assertIn('conflicts still prevent full qualification',dialog.confirm_review.text())
        self.assertFalse(dialog.record_review.isEnabled())
        QTest.mouseClick(dialog.confirm_review.label,QtCore.Qt.LeftButton)
        self.assertTrue(dialog.confirm_review.isChecked());self.assertTrue(dialog.record_review.isEnabled())
        self.assertIsNone(dialog.review_selection);self.assertEqual(self.dump(),before)

    def test_saved_pair_is_excluded_from_pending_groups_and_never_retargeted(self):
        state=self.seed();row=state['results'][0];self.save(row);state=ai.pairing_state(self.db);before=self.dump()
        self.assertEqual(groups(state['results']),[])
        dialog=SalaryMatchesDialog(state=state);self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog.inspect_group.isEnabled());self.assertIn('preserved',dialog.review_guidance.text())
        self.assertFalse(dialog.buttons.button(QtWidgets.QDialogButtonBox.Save).isEnabled());self.assertEqual(self.dump(),before)

    def test_issue_examples_are_bounded_but_conflict_counts_cover_all_observations(self):
        salary=dict(manifest=dict(format='showdown',dates=['day'],games=[],players=[]))
        result=dict(manifest=dict(format='showdown',dates=['day'],contest_ids=['one'],observed=[('FLEX','missing '+str(i),str(i)) for i in range(25)]))
        q=ai.qualify_pair(result,salary);info=candidate_details(result,salary,q,{})
        self.assertEqual(info['categories'],{'missing_player':25});self.assertEqual(len(info['issues']),12)
        self.assertFalse(q['compatible'])


if __name__=='__main__':unittest.main()
