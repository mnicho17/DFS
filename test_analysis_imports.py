import csv
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import analysis_imports as imports


def salary_file(path, fmt='showdown', day='09/21/2026', offset=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    names = ['Alpha', 'Bravo', 'Charlie', 'Delta', 'Echo', 'Foxtrot', 'Golf', 'Hotel', 'Defense']
    positions = ['QB', 'RB', 'RB', 'WR', 'WR', 'WR', 'TE', 'RB', 'DST']
    with path.open('w', newline='', encoding='utf-8-sig') as handle:
        w = csv.writer(handle)
        w.writerow(['Position','Name + ID','Name','ID','Roster Position','Salary','Game Info','TeamAbbrev','AvgPointsPerGame'])
        for i, (name, pos) in enumerate(zip(names[:6 if fmt == 'showdown' else 9], positions)):
            for role in (['CPT', 'FLEX'] if fmt == 'showdown' else [pos + ('/FLEX' if pos in ('RB','WR','TE') else '')]):
                player_id = 100+i+(1000 if role == 'CPT' else 0)
                w.writerow([pos,f'{name} ({player_id})',name,player_id,role,
                            (7500 if role == 'CPT' else 5000)+offset,f'NE@SEA {day} 08:15PM ET','NE' if i%2 else 'SEA',0])
    return path


def results_file(path, fmt='showdown', entries=30, contest_id='one', extra=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    roster = ('CPT Alpha FLEX Bravo FLEX Charlie FLEX Delta FLEX Echo FLEX Foxtrot' if fmt == 'showdown' else
              'QB Alpha RB Bravo RB Charlie WR Delta WR Echo WR Foxtrot TE Golf FLEX Hotel DST Defense')
    with path.open('w', newline='', encoding='utf-8-sig') as handle:
        w = csv.writer(handle)
        w.writerow(['Rank','EntryId','EntryName','Points','Lineup','ContestId'] + (list(extra) if extra else []))
        for i in range(entries):
            w.writerow([i+1,1000+i,'Example_User',0 if i == 0 else 50,roster,contest_id] + (list(extra.values()) if extra else []))
    return path


class CombinedImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.results = self.root/'results'; self.results.mkdir()
        self.salary = self.root/'salary'; self.salary.mkdir()
        self.db = str(self.root/'history.sqlite')

    def tearDown(self):
        self.tmp.cleanup()

    def run_import(self, **kwargs):
        return imports.import_folders(str(self.results), str(self.salary), db_path=self.db, username='Example_User', **kwargs)

    def sources(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return imports._sources(conn)

    def fixture(self, fmt='showdown'):
        return (results_file(self.results/f'9_21_2026_NFL {fmt}.csv', fmt),
                salary_file(self.salary/'salaries.csv', fmt))

    def test_both_formats_real_import_pair_and_duplicate_skip(self):
        for fmt in ('showdown','classic'):
            with self.subTest(fmt=fmt):
                results, salary = self.fixture(fmt)
                original = (results.read_bytes(), salary.read_bytes())
                first = self.run_import()
                self.assertEqual(first['errors'], [])
                self.assertEqual((first['results_imported'], first['salaries_imported'], first['pairs_added']), (1,1,1))
                with closing(sqlite3.connect(self.db)) as conn:
                    result = next(s for s in imports._sources(conn) if s['kind']=='results' and s['manifest']['format']==fmt)
                    metadata, receipt = imports.paired_metadata(conn,result['import_id'])
                    self.assertTrue(receipt['salary_hash'])
                    self.assertEqual(metadata['@cpt:alpha' if fmt=='showdown' else 'alpha']['salary'],7500 if fmt=='showdown' else 5000)
                    self.assertNotIn('ownership',next(iter(metadata.values())))
                    self.assertEqual(conn.execute('SELECT actual_points FROM historical_results WHERE import_id=? ORDER BY row_index LIMIT 1',(result['import_id'],)).fetchone()[0],0)
                with patch('learning_db.import_historical_result_csvs',side_effect=AssertionError('duplicate must not reimport')):
                    again = self.run_import()
                self.assertEqual(again['results_imported'],0)
                self.assertEqual(again['salaries_imported'],0)
                self.assertEqual(again['pairs_added'],0)
                self.assertEqual(again['analysis_import_ids'],[])
                self.assertEqual((results.read_bytes(),salary.read_bytes()),original)
                results.unlink(); salary.unlink()

    def test_moved_renamed_and_overlapping_roots_are_idempotent(self):
        results,salary = self.fixture()
        self.run_import()
        results.rename(self.results/'renamed.csv')
        salary.rename(self.salary/'renamed.csv')
        shutil.copy2(self.salary/'renamed.csv',self.results/'salary-copy.csv')
        r = imports.import_folders(str(self.root),str(self.results),db_path=self.db)
        self.assertEqual(r['errors'],[])
        # Includes the two immutable snapshots, but overlapping roots never scan a path twice.
        self.assertEqual(r['duplicates_skipped'],5)
        self.assertEqual(len(self.sources()),2)

    def test_same_slate_multiple_contests_pair_separately(self):
        self.fixture()
        results_file(self.results/'9_21_2026_NFL-second.csv',contest_id='two')
        r = self.run_import()
        self.assertEqual(r['pairs_added'],2)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(DISTINCT salary_hash) FROM analysis_salary_pairs').fetchone()[0],1)

    def test_revised_salary_never_retargets_prior_pair(self):
        self.fixture();self.run_import()
        pair = imports.pairing_rows(self.db)[0]
        salary_file(self.salary/'revision.csv',offset=100)
        r = self.run_import()
        self.assertEqual(r['pairs_added'],0)
        new = next(s for s in self.sources() if s['name']=='revision.csv')
        with self.assertRaisesRegex(ValueError,'already paired'):
            imports.save_pair(pair['hash'],new['hash'],db_path=self.db)
        self.assertEqual(imports.pairing_rows(self.db)[0]['salary_hash'],pair['salary_hash'])

    def test_ambiguous_revisions_require_explicit_selection(self):
        self.fixture();salary_file(self.salary/'revision.csv',offset=100)
        r = self.run_import()
        self.assertEqual((r['pairs_added'],r['unpaired']),(0,1))
        row = imports.pairing_rows(self.db)[0]
        chosen = row['candidates'][0]['hash']
        self.assertTrue(imports.save_pair(row['hash'],chosen,db_path=self.db))
        self.assertFalse(imports.save_pair(row['hash'],chosen,db_path=self.db))

    def test_missing_date_requires_confirmation_and_wrong_date_cannot_be_overridden(self):
        results_file(self.results/'contest-standings.csv');salary_file(self.salary/'salary.csv')
        self.assertEqual(self.run_import()['pairs_added'],0)
        row = imports.pairing_rows(self.db)[0]
        with self.assertRaisesRegex(ValueError,'date missing'):
            imports.save_pair(row['hash'],row['candidates'][0]['hash'],db_path=self.db)
        self.assertTrue(imports.save_pair(row['hash'],row['candidates'][0]['hash'],db_path=self.db,confirm_date=True))
        results_file(self.results/'9_22_2026_NFL.csv',contest_id='wrong-day')
        self.run_import()
        wrong = next(r for r in imports.pairing_rows(self.db) if '9_22' in r['name'])
        with self.assertRaisesRegex(ValueError,'dates conflict'):
            imports.save_pair(wrong['hash'],wrong['candidates'][0]['hash'],db_path=self.db,confirm_date=True)

    def test_wrong_format_missing_players_and_namesakes_do_not_pair(self):
        result,salary = self.fixture()
        salary_file(salary,fmt='classic')
        self.assertEqual(self.run_import()['pairs_added'],0)
        salary_file(salary)
        text = salary.read_text(encoding='utf-8-sig').replace('Foxtrot','Unrelated')
        salary.write_text(text,encoding='utf-8')
        r = self.run_import()
        self.assertEqual(r['pairs_added'],0)
        salary_file(salary)
        with salary.open('a',encoding='utf-8',newline='') as f:
            csv.writer(f).writerow(['QB','Alpha (8888)','Alpha','8888','CPT',7500,'NE@SEA 09/21/2026','NE',0])
        self.assertEqual(self.run_import()['pairs_added'],0)
        row = imports.pairing_rows(self.db)[0]
        self.assertTrue(any('ambiguous' in c['reason'] for c in row['candidates']))

    def test_explicit_wrong_player_id_and_contest_conflicts_reject_pair(self):
        result,salary = self.fixture()
        result.write_text(result.read_text(encoding='utf-8-sig').replace('CPT Alpha','CPT Alpha (99999)'),encoding='utf-8')
        self.assertEqual(self.run_import()['pairs_added'],0)
        result.write_text(result.read_text().replace('Alpha (99999)','Alpha')+'31,9000,Example_User,20,CPT Alpha FLEX Bravo FLEX Charlie FLEX Delta FLEX Echo FLEX Foxtrot,two\n',encoding='utf-8')
        self.assertEqual(self.run_import()['pairs_added'],0)

    def test_partial_rosters_are_reported_without_claiming_full_pool(self):
        result,salary = self.fixture()
        with result.open('a',encoding='utf-8',newline='') as f:
            csv.writer(f).writerow([31,5000,'Example_User',0,'LOCKED','one'])
        self.run_import()
        row = imports.pairing_rows(self.db)[0]
        self.assertEqual(row['unreadable'],1)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertIn('unverified',' '.join(imports.report_lines(conn)))

    def test_missing_drive_fails_before_any_writes(self):
        self.fixture()
        with self.assertRaisesRegex(ValueError,'Folder unavailable'):
            imports.import_folders(str(self.results),str(self.root/'unplugged'),db_path=self.db)
        self.assertFalse(Path(self.db).exists())

    def test_unsupported_csv_ignored_bad_salary_retries_after_fix(self):
        (self.results/'notes.csv').write_text('Hello,World\n1,2\n')
        salary = salary_file(self.salary/'bad.csv')
        salary.write_text(salary.read_text(encoding='utf-8-sig').replace(',7500,',',,',1),encoding='utf-8')
        r = self.run_import()
        self.assertEqual(r['ignored'],1)
        self.assertEqual(len(r['errors']),1)
        self.assertIn('unknown is not zero',r['errors'][0])
        self.assertEqual(len(self.run_import()['errors']),1)
        salary_file(salary)
        self.assertEqual(self.run_import()['salaries_imported'],1)

    def test_zero_salary_is_preserved_but_missing_is_not_zero(self):
        path = salary_file(self.salary/'zero.csv')
        path.write_text(path.read_text(encoding='utf-8-sig').replace(',7500,',',0,',1),encoding='utf-8')
        self.assertEqual(self.run_import()['errors'],[])
        row = self.sources()[0]['manifest']['players'][0]
        self.assertEqual(row['salary'],0)
        self.assertEqual(row['raw']['salary'],'0')

    def test_embedded_entry_templates_import_only_the_salary_table(self):
        path = salary_file(self.salary/'entries.csv')
        with path.open(newline='',encoding='utf-8-sig') as f:
            rows = list(csv.reader(f))
        with path.open('w',newline='',encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['CPT']+['FLEX']*5+['','Instructions'])
            w.writerow(['123']*6+['','Help text'])
            for row in rows:w.writerow(['123']*6+['']+row)
            w.writerow(['123']*6+['','Trailing entry only'])
        result = self.run_import()
        self.assertEqual(result['errors'],[])
        self.assertEqual((result['results_imported'],result['salaries_imported']),(0,1))
        self.assertEqual(len(self.sources()[0]['manifest']['players']),12)
        self.assertEqual(self.run_import()['duplicates_skipped'],1)

    def test_conflicting_explicit_game_cannot_be_overridden(self):
        result,salary = self.fixture()
        result.rename(self.results/'9_21_2026_NFL (BUF @ NYJ).csv')
        self.assertEqual(self.run_import()['pairs_added'],0)
        row = imports.pairing_rows(self.db)[0]
        with self.assertRaisesRegex(ValueError,'game/team'):
            imports.save_pair(row['hash'],row['candidates'][0]['hash'],db_path=self.db,confirm_date=True)

    def test_distinct_names_that_collapse_in_legacy_consumer_are_withheld(self):
        result,salary = self.fixture()
        for path in (result,salary):
            text = path.read_text(encoding='utf-8-sig').replace('Bravo','Alpha-One').replace('Charlie','Alpha One')
            path.write_text(text,encoding='utf-8')
        self.assertEqual(self.run_import()['pairs_added'],0)
        self.assertIn('collapse',imports.pairing_rows(self.db)[0]['candidates'][0]['reason'])

    def test_known_zero_salary_keeps_the_common_cohort(self):
        from performance_review import add,bucket
        self.fixture()
        path = self.salary/'salaries.csv'
        path.write_text(path.read_text(encoding='utf-8-sig').replace(',7500,',',0,',1),encoding='utf-8')
        self.run_import()
        with closing(sqlite3.connect(self.db)) as conn:
            result = next(s for s in imports._sources(conn) if s['kind']=='results')
            meta,_ = imports.paired_metadata(conn,result['import_id'])
        b = bucket();add(b,['@cpt:alpha','bravo','charlie','delta','echo','foxtrot'],meta,{})
        self.assertEqual((b['salary_n'],b['salary_sum']),(1,25000))
        meta['@cpt:alpha']['salary']=None
        b = bucket();add(b,['@cpt:alpha','bravo','charlie','delta','echo','foxtrot'],meta,{})
        self.assertEqual(b['salary_n'],0)

    def test_existing_legacy_result_is_skipped_without_rewriting_history(self):
        from learning_db import import_historical_result_csvs
        result,salary = self.fixture()
        import_historical_result_csvs([str(result)],db_path=self.db,username='Example_User',archive_files=False)
        with closing(sqlite3.connect(self.db)) as conn:
            before = conn.execute('SELECT * FROM historical_imports').fetchall()
        r = self.run_import()
        self.assertEqual((r['results_imported'],r['duplicates_skipped'],r['pairs_added']),(0,1,1))
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT * FROM historical_imports').fetchall(),before)

    def test_snapshot_survives_original_move_and_changed_snapshot_is_withheld(self):
        result,salary = self.fixture();self.run_import()
        result.unlink();salary.unlink()
        with closing(sqlite3.connect(self.db)) as conn:
            sources = imports._sources(conn)
            r = next(s for s in sources if s['kind']=='results')
            s = next(s for s in sources if s['kind']=='salary')
            self.assertTrue(imports.paired_metadata(conn,r['import_id'])[0])
            Path(s['snapshot']).write_text('changed',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'saved source changed'):
                imports.paired_metadata(conn,r['import_id'])

    def test_changed_source_during_copy_is_rejected(self):
        self.fixture()
        original = imports._snapshot
        def mutate(path,digest,root,cancelled):
            path.write_bytes(path.read_bytes()+b'\n')
            return original(path,digest,root,cancelled)
        with patch.object(imports,'_snapshot',side_effect=mutate):
            r = self.run_import()
        self.assertEqual(len(r['errors']),2)
        self.assertEqual(self.sources(),[])

    def test_cancel_during_real_worker_import_rolls_back_active_result(self):
        from learning_db import _import_username_entries
        self.fixture()
        cancel = threading.Event()
        def after_field(*args,**kwargs):
            cancel.set()
            return _import_username_entries(*args,**kwargs)
        with patch('learning_db._import_username_entries',side_effect=after_field):
            r = self.run_import(cancelled=cancel.is_set)
        self.assertTrue(r['cancelled'])
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM historical_imports').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM historical_results').fetchone()[0],0)
        self.assertEqual(self.run_import()['results_imported'],1)

    def test_cancel_after_completed_file_keeps_it_and_resume_skips_it(self):
        salary_file(self.salary/'a.csv');salary_file(self.salary/'b.csv',offset=10)
        cancel = threading.Event()
        def progress(done,total,text):
            if 'b.csv' in text:cancel.set()
        r = self.run_import(cancelled=cancel.is_set,progress=progress)
        self.assertTrue(r['cancelled'])
        self.assertEqual(r['salaries_imported'],1)
        again = self.run_import()
        self.assertEqual((again['salaries_imported'],again['duplicates_skipped']),(1,1))

    def test_review_consumer_is_scoped_and_records_pair_provenance(self):
        from performance_review import analyze_saved_results
        self.fixture();r = self.run_import()
        review = analyze_saved_results(db_path=self.db,username='Example_User',import_ids=set(r['analysis_import_ids']))
        self.assertEqual(review['completed'],1)
        with closing(sqlite3.connect(self.db)) as conn:
            payload = json.loads(conn.execute('SELECT payload FROM construction_reviews').fetchone()[0])
            self.assertTrue(payload['salary_receipt']['salary_hash'])
            self.assertEqual(payload['groups']['field']['salary_sum'],32500*30)
            self.assertEqual(payload['groups']['field']['salary_n'],30)
        self.assertEqual(analyze_saved_results(db_path=self.db,import_ids=set())['completed'],0)

    def test_rejected_snapshot_preserves_existing_review_and_ownership(self):
        from performance_review import analyze_saved_results
        self.fixture();self.run_import()
        analyze_saved_results(db_path=self.db,username='Example_User')
        with closing(sqlite3.connect(self.db)) as conn:
            before = conn.execute('SELECT * FROM construction_reviews').fetchall()
            field = conn.execute('SELECT * FROM contest_field_summaries').fetchall()
            salary = next(s for s in imports._sources(conn) if s['kind']=='salary')
        Path(salary['snapshot']).write_bytes(b'changed')
        after = analyze_saved_results(db_path=self.db,username='Example_User')
        self.assertEqual(after['completed'],0)
        self.assertIn('saved source changed',after['message'])
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT * FROM construction_reviews').fetchall(),before)
            self.assertEqual(conn.execute('SELECT * FROM contest_field_summaries').fetchall(),field)

    def test_failed_file_does_not_block_other_files_and_can_retry(self):
        bad = salary_file(self.salary/'a.csv')
        bad.write_text(bad.read_text(encoding='utf-8-sig').replace(',7500,',',unknown,',1),encoding='utf-8')
        salary_file(self.salary/'b.csv',offset=100)
        first = self.run_import()
        self.assertEqual((len(first['errors']),first['salaries_imported']),(1,1))
        salary_file(bad)
        again = self.run_import()
        self.assertEqual((again['salaries_imported'],again['duplicates_skipped']),(1,1))

    def test_result_revision_keeps_both_original_sources(self):
        result,salary = self.fixture();self.run_import()
        original = next(s for s in self.sources() if s['kind']=='results')
        result.write_text(result.read_text(encoding='utf-8-sig').replace(',50,',',51,',1),encoding='utf-8')
        again = self.run_import()
        self.assertEqual(again['results_imported'],1)
        result_sources = [s for s in self.sources() if s['kind']=='results']
        self.assertEqual(len(result_sources),2)
        self.assertEqual(imports._hash(original['snapshot']),original['hash'])

    def test_multiple_named_contests_need_separate_sources(self):
        result = results_file(self.results/'9_21_2026_NFL.csv',extra={'ContestName':'First'})
        result.write_text(result.read_text(encoding='utf-8-sig').replace(',First',',Second',1),encoding='utf-8')
        salary_file(self.salary/'salary.csv')
        self.assertEqual(self.run_import()['pairs_added'],0)
        self.assertIn('multiple contests',imports.pairing_rows(self.db)[0]['candidates'][0]['reason'])


class CombinedImportGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict('os.environ',{'DFS_OPTIMIZER_DATA_DIR':str(self.root/'data')})
        self.env.start()
        self.messages = patch('PyQt5.QtWidgets.QMessageBox.information',return_value=0)
        self.info = self.messages.start()
        self.errors = patch('PyQt5.QtWidgets.QMessageBox.critical',return_value=0)
        self.critical = self.errors.start()
        from main_window import ResultsLearningDialog
        self.dialog = ResultsLearningDialog()

    def tearDown(self):
        self.dialog.reject()
        self.drain()
        self.dialog.deleteLater();self.app.processEvents()
        self.errors.stop();self.messages.stop();self.env.stop();self.tmp.cleanup()

    def drain(self):
        end = time.monotonic()+20
        while self.dialog._import_thread is not None and time.monotonic()<end:
            self.app.processEvents();time.sleep(.005)
        self.assertIsNone(self.dialog._import_thread,'worker did not retire')
        self.app.processEvents()

    def configure(self):
        results_file(self.root/'results'/'9_21_2026_NFL.csv')
        salary_file(self.root/'salary'/'salaries.csv')
        self.dialog.results_folder.setText(str(self.root/'results'))
        self.dialog.salary_folder.setText(str(self.root/'salary'))
        self.dialog.username_edit.setText('Example_User')

    def test_one_button_runs_real_worker_then_duplicate_scan_skips_analysis(self):
        self.configure()
        self.dialog.import_new_button.click()
        self.assertFalse(self.dialog.analyze_button.isEnabled())
        self.drain()
        self.assertEqual(self.critical.call_count,0)
        self.assertIn('New result files: 1',self.info.call_args.args[2])
        old_job = {'completion':None}
        with patch('performance_review.analyze_saved_results',side_effect=AssertionError('duplicates must not analyze')):
            self.dialog.import_new_button.click()
            job = self.dialog._import_job
            self.dialog._on_import_thread_finished(old_job)
            self.assertIs(self.dialog._import_job,job)
            self.drain()
        self.assertIn('Already imported files skipped: 2',self.info.call_args.args[2])
        self.assertTrue(self.dialog.import_new_button.isEnabled())

    def test_close_cancels_real_import_without_late_dialog_or_deleted_thread(self):
        from analysis_imports_ui import CombinedImportWorker
        self.configure()
        entered,release = threading.Event(),threading.Event()
        original = imports._hash
        def held(path,cancelled=lambda:False):
            entered.set();release.wait(5)
            return original(path,cancelled)
        with patch.object(imports,'_hash',side_effect=held):
            self.dialog.show();self.dialog.import_new_results()
            end = time.monotonic()+3
            while not entered.is_set() and time.monotonic()<end:
                self.app.processEvents();time.sleep(.005)
            self.assertTrue(entered.is_set())
            self.assertIsInstance(self.dialog._import_worker,CombinedImportWorker)
            self.dialog.close()
            self.assertTrue(self.dialog._import_worker.cancelled.is_set())
            self.assertIsNotNone(self.dialog._import_thread)
            release.set();self.drain()
        self.assertEqual(self.info.call_count,0)
        self.assertEqual(self.critical.call_count,0)
        self.assertFalse(self.dialog.isVisible())

    def test_error_retires_and_all_controls_reenable(self):
        self.dialog.results_folder.setText(str(self.root/'missing'))
        self.dialog.import_new_button.click();self.drain()
        self.assertEqual(self.critical.call_count,1)
        self.assertIn('Folder unavailable',self.critical.call_args.args[2])
        self.assertTrue(self.dialog.stats_button.isEnabled())
        self.assertTrue(self.dialog.choose_salary_button.isEnabled())

    def test_folder_choices_are_saved_in_verified_temporary_settings(self):
        from PyQt5 import QtCore
        with patch('PyQt5.QtWidgets.QFileDialog.getExistingDirectory',return_value=str(self.root)):
            self.dialog.choose_results_folder();self.dialog.choose_salary_folder()
        settings = self.dialog.learning_settings
        self.assertEqual(settings.format(),QtCore.QSettings.IniFormat)
        self.assertFalse(settings.fallbacksEnabled())
        self.assertEqual(settings.value('learning/salary_folder'),str(self.root))
        self.assertEqual(settings.value('learning/results_folder'),str(self.root))
        self.dialog.clear_salary_folder()
        self.assertEqual(settings.value('learning/salary_folder'),'')

    def test_match_review_requires_explicit_date_confirmation(self):
        from analysis_imports_ui import SalaryMatchesDialog
        results_file(self.root/'results'/'unknown-date.csv')
        salary_file(self.root/'salary'/'salary.csv')
        imports.import_folders(str(self.root/'results'),str(self.root/'salary'))
        dialog = SalaryMatchesDialog(self.dialog)
        dialog._select()
        self.assertIsNone(dialog.selection)
        dialog.confirm_date.setChecked(True);dialog._select()
        self.assertIsNotNone(dialog.selection)
        dialog.deleteLater()

    def test_cancel_during_report_refresh_preserves_displayed_report(self):
        from learning_db import generate_learning_report
        self.configure()
        self.dialog.report.setPlainText('Previous completed report')
        def cancel_after_report(*args,**kwargs):
            report = generate_learning_report(*args,**kwargs)
            self.dialog._import_worker.request_cancel()
            return report
        with patch('learning_db.generate_learning_report',side_effect=cancel_after_report):
            self.dialog.import_new_button.click();self.drain()
        self.assertIn('Import cancelled',self.info.call_args.args[2])
        self.assertEqual(self.dialog.report.toPlainText(),'Previous completed report')

    def test_explicit_pair_uses_real_worker_and_review_shows_saved_revision(self):
        from analysis_imports_ui import CombinedImportWorker,SalaryMatchesDialog
        from learning_db import history_db_path
        self.configure()
        salary_file(self.root/'salary'/'revision.csv',offset=100)
        imports.import_folders(str(self.root/'results'),str(self.root/'salary'))
        row = imports.pairing_rows()[0]
        chosen = row['candidates'][1]['hash']
        self.dialog._start_background_import(CombinedImportWorker(pair=(row['hash'],chosen,False)),self.dialog._on_combined_import_finished)
        self.drain()
        self.assertEqual(imports.pairing_rows()[0]['salary_hash'],chosen)
        with closing(sqlite3.connect(history_db_path())) as conn:
            self.assertEqual(conn.execute('SELECT basis FROM analysis_salary_pairs').fetchone()[0],'user-selected-compatible-revision')
        review = SalaryMatchesDialog(self.dialog)
        self.assertEqual(review.salaries.currentData()['hash'],chosen)
        self.assertFalse(review.salaries.isEnabled())
        review.deleteLater()


if __name__ == '__main__':
    unittest.main()
