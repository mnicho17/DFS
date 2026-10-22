import copy
import csv
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import analysis_imports as ai
from build_snapshots import create_snapshot, save_snapshot, fingerprint
from distribution_build_selection import catalog, save_choice, saved_choice
from distribution_validation import compare_distributions, validation_report
from scoring_distributions import DistributionCapture, save_distribution
from test_analysis_imports import salary_file


class SIMBuildSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.build_fixture()

    def build_fixture(self, kind='showdown'):
        self.root = Path(self.tmp.name)/kind
        self.root.mkdir()
        self.db = str(self.root/'history.sqlite')
        self.salary = salary_file(self.root/'salary'/'salary.csv', fmt=kind, day='09/21/2099')
        self.results = self.root/'results'/('9_21_2099_NFL '+kind+'.csv')
        self.results.parent.mkdir()
        names = ['Alpha', 'Bravo', 'Charlie', 'Delta', 'Echo', 'Foxtrot', 'Golf', 'Hotel', 'Defense'][:6 if kind=='showdown' else 9]
        roster = ('CPT Alpha FLEX '+' FLEX '.join(names[1:])) if kind=='showdown' else 'QB Alpha RB Bravo RB Charlie WR Delta WR Echo WR Foxtrot TE Golf FLEX Hotel DST Defense'
        with self.results.open('w', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(['Rank','EntryId','EntryName','Points','Lineup','Player','Roster Position','FPTS'])
            for i, name in enumerate(names):
                writer.writerow([i+1, i+100, 'Example_User', 65 if kind=='showdown' else 90, roster, name, 'FLEX' if kind=='showdown' else ['QB','RB','RB','WR','WR','WR','TE','RB','DST'][i], 10])
        imported = ai.import_folders(str(self.results.parent), str(self.salary.parent), db_path=self.db, username='Example_User')
        self.assertEqual(imported['errors'], [])
        with closing(sqlite3.connect(self.db)) as conn:
            sources = ai._sources(conn)
        self.ident = next(s['import_id'] for s in sources if s['kind']=='results')
        manifest = next(s['manifest'] for s in sources if s['kind']=='salary')
        lookup = {}
        for row in manifest['players']:
            player = lookup.setdefault(row['name'], dict(Name=row['name'], Team=row['team'], Position=row['position'],
                GameInfo='NE@SEA 09/21/2099 08:15PM ET', FlexProjection=10, CptProjection=15))
            prefix = 'Cpt' if row['role']=='CPT' else 'Flex'
            player[prefix+'ID'] = row['id']; player[prefix+'Salary'] = row['salary']
        self.kind = kind
        self.players = list(lookup.values())
        self.snap, self.path = self.snapshot(self.players, '2099-09-21T18:00:00-04:00')
        self.capture_path = self.capture(self.snap)

    def tearDown(self):
        self.tmp.cleanup()

    def snapshot(self, players, created):
        snap = create_snapshot(players, dict(sport='NFL', contest_kind=self.kind), {})
        snap['created_at'] = created
        path = self.root/'snapshots'/(snap['input_id']+'.json')
        save_snapshot(str(path), snap)
        return snap, path

    def capture(self, snap, start='2099-09-21T18:01:00-04:00', finish='2099-09-21T18:02:00-04:00'):
        capture = DistributionCapture(snap['inputs']['players'], self.kind, 2, 7)
        capture.started_at = start
        for _ in range(2):
            capture.record({p['FlexID']:10 for p in snap['inputs']['players']})
        raw = capture.finish(2); raw['finished_at'] = finish
        status = save_distribution(dict(player_distributions=raw), snap['input_id'], self.root)
        self.assertEqual(status['status'], 'saved')
        return self.root/'scoring-distributions'/(snap['input_id']+'-'+status['capture_id']+'.json')

    def read(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return catalog(conn, self.ident, self.root)

    def test_earlier_sim_selectable_without_replacing_latest_forecast_snapshot(self):
        later = copy.deepcopy(self.players); later[0]['FlexProjection'] = 12
        self.snapshot(later, '2099-09-21T19:00:00-04:00')
        data = self.read()
        self.assertEqual(len(data['candidates']), 1)
        before = (self.path.read_bytes(), self.capture_path.read_bytes())
        self.assertTrue(save_choice(self.db, self.ident, data['candidates'][0])['committed'])
        with closing(sqlite3.connect(self.db)) as conn:
            result = compare_distributions(conn, self.ident, 'b'*64, 'showdown', data['scores'], self.root)
            self.assertEqual(result['input_id'], self.snap['input_id'])
            self.assertEqual(len(result['rows']), 6)
            self.assertIn('submission not established', '\n'.join(validation_report(conn)))
            self.assertFalse(conn.execute("SELECT 1 FROM sqlite_master WHERE name='result_snapshot_reviews'").fetchone())
        self.assertEqual(before, (self.path.read_bytes(), self.capture_path.read_bytes()))

    def test_multiple_captures_require_exact_explicit_selection(self):
        self.capture(self.snap, finish='2099-09-21T18:03:00-04:00')
        data = self.read(); self.assertEqual(len(data['candidates']), 2)
        selected = data['candidates'][-1]
        save_choice(self.db, self.ident, selected)
        with closing(sqlite3.connect(self.db)) as conn:
            result = json.loads(conn.execute('SELECT payload FROM distribution_validations').fetchone()[0])
        self.assertEqual(result['capture_id'], selected['capture_id'])

    def test_wrong_ids_salary_pool_date_and_postkickoff_are_ineligible(self):
        for mutation in ('ids', 'salary', 'date', 'pool'):
            players = copy.deepcopy(self.players)
            if mutation=='ids': players[0]['FlexID']='999999'
            if mutation=='salary': players[0]['FlexSalary']=4000
            if mutation=='date': players[0]['GameInfo']='NE@SEA 09/22/2099 08:15PM ET'
            if mutation=='pool': players.pop()
            snap, _ = self.snapshot(players, '2099-09-21T17:00:00-04:00')
            self.capture(snap)
        self.capture(self.snap, finish='2099-09-21T20:15:00-04:00')
        data = self.read()
        self.assertEqual(len(data['candidates']), 1)
        self.assertTrue(any('kickoff' in r['reason'] for r in data['rejected']))

    def test_changed_capture_invalidates_saved_choice_without_fallback(self):
        data = self.read(); save_choice(self.db, self.ident, data['candidates'][0])
        self.capture_path.write_text('{}')
        with closing(sqlite3.connect(self.db)) as conn:
            old = saved_choice(conn, self.ident)
            result = compare_distributions(conn, self.ident, self.snap['input_id'], 'showdown', data['scores'], self.root)
            self.assertEqual(result['status'], 'unavailable')
            self.assertEqual(result['rows'], [])
            self.assertEqual(saved_choice(conn, self.ident), old)

    def test_preview_staleness_and_cancellation_preserve_database(self):
        from analysis_imports import ImportCancelled
        data = self.read(); choice = data['candidates'][0]
        with self.assertRaises(ImportCancelled): save_choice(self.db, self.ident, choice, cancelled=lambda:True)
        self.capture_path.write_text('{}')
        with self.assertRaises(ValueError): save_choice(self.db, self.ident, choice)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertIsNone(saved_choice(conn, self.ident))

    def test_dialog_requires_a_row_and_shows_full_identity(self):
        from PyQt5 import QtWidgets
        from distribution_build_selection_ui import SIMBuildDialog
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        dialog = SIMBuildDialog(self.read())
        try:
            self.assertFalse(dialog.use.isEnabled())
            dialog.table.setCurrentCell(0, 0)
            self.assertTrue(dialog.use.isEnabled())
            self.assertIn(self.snap['input_id'], dialog.details.toPlainText())
            dialog._accept()
            self.assertEqual(dialog.choice['input_id'], self.snap['input_id'])
        finally: dialog.close()

    def test_classic_qualification_and_explicit_validation(self):
        self.build_fixture('classic')
        data = self.read()
        self.assertEqual(data['kind'], 'classic')
        self.assertEqual(len(data['candidates']), 1)
        save_choice(self.db, self.ident, data['candidates'][0])
        with closing(sqlite3.connect(self.db)) as conn:
            result = json.loads(conn.execute('SELECT payload FROM distribution_validations').fetchone()[0])
        self.assertEqual(len(result['rows']), 9)

    def test_identical_snapshot_copies_preserved_and_conflicting_copies_rejected(self):
        duplicate = self.path.with_name('identical-copy.json')
        duplicate.write_bytes(self.path.read_bytes())
        self.assertEqual(len(self.read()['candidates']), 1)
        changed = json.loads(duplicate.read_text()); changed['created_at']='2099-09-21T17:30:00-04:00'
        duplicate.write_text(json.dumps(changed))
        self.assertEqual(len(self.read()['candidates']), 0)

    def test_replaced_saved_sources_and_changed_scores_are_rejected(self):
        data = self.read(); save_choice(self.db, self.ident, data['candidates'][0])
        with closing(sqlite3.connect(self.db)) as conn:
            changed = dict(data['scores']); changed['alpha'] = 99
            self.assertEqual(compare_distributions(conn,self.ident,self.snap['input_id'],'showdown',changed,self.root)['status'],'unavailable')
            result_source = next(s for s in ai._sources(conn) if s['kind']=='results')
        Path(result_source['snapshot']).write_text('changed')
        with self.assertRaises(ValueError): self.read()

    def test_actual_duplicate_scores_are_order_independent(self):
        from distribution_build_selection import _scores
        from unittest.mock import patch
        path = self.root/'score-table.csv'
        for scores in ((10,10), (10,20), (20,10)):
            with path.open('w', newline='') as handle:
                writer=csv.writer(handle)
                writer.writerow(['Player','Roster Position','FPTS'])
                for score in scores: writer.writerow(['Alpha','FLEX',score])
            with patch.object(ai,'_verify'):
                if scores[0]==scores[1]:
                    self.assertEqual(_scores({'snapshot':str(path)}, {'players':[]}, lambda:False), {'alpha':10})
                else:
                    with self.assertRaisesRegex(ValueError,'Conflicting actual'):
                        _scores({'snapshot':str(path)}, {'players':[]}, lambda:False)

    def test_preview_worker_cancel_does_not_save_choice(self):
        from distribution_build_selection_ui import SIMBuildWorker
        worker=SIMBuildWorker(self.db,self.ident)
        results=[];errors=[]
        worker.finished.connect(results.append);worker.error.connect(errors.append)
        worker.request_cancel();worker.run()
        self.assertEqual(errors,[])
        self.assertTrue(results[0]['cancelled'])
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertIsNone(saved_choice(conn,self.ident))
