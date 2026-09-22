import csv
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from opponent_analysis import AnalysisCancelled, analyze_standings, render_report, share_payload


ROSTER = 'CPT Alpha FLEX Bravo FLEX Charlie FLEX Delta FLEX Echo FLEX Foxtrot'
SWAPPED = 'CPT Bravo FLEX Alpha FLEX Charlie FLEX Delta FLEX Echo FLEX Foxtrot'
CLASSIC = 'QB Alpha RB Bravo RB Charlie WR Delta WR Echo WR Foxtrot TE Golf FLEX Hotel DST India'
HEADERS = ['Rank', 'EntryId', 'EntryName', 'Points', 'Lineup', 'Player', 'Roster Position', '%Drafted', 'FPTS']


class StandingsFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'standings.csv'

    def write(self, rows, headers=HEADERS, delimiter=','):
        with self.path.open('w', newline='', encoding='utf-8-sig') as handle:
            writer = csv.writer(handle, delimiter=delimiter)
            writer.writerow(headers)
            writer.writerows(rows)
        return self.path

    def analyze(self, rows, **kwargs):
        return analyze_standings(self.write(rows), **kwargs)


class OpponentAnalysisTests(StandingsFixture):
    def test_username_counts_identity_and_original_labels(self):
        result = self.analyze([[1, 1, 'User_Name (1/3)', 10, ROSTER], [2, 2, 'user_name (2/3)', 0, ROSTER],
                               [3, 3, 'UserName', 5, ROSTER], [4, 4, 'User.Name', 4, ROSTER]])
        users = {p['username_key']: p for p in result['portfolios']}
        self.assertEqual(len(users), 3)
        self.assertEqual(users['user_name']['entries'], 2)
        self.assertEqual(users['user_name']['username'], 'User_Name')
        self.assertEqual(users['user_name']['mean_points'], 5)

    def test_captain_identity_overlap_and_shared_pair_denominator(self):
        result = self.analyze([[1, 1, 'U', 10, ROSTER], [2, 2, 'U', 20, SWAPPED]])
        p = result['portfolios'][0]
        self.assertEqual(p['unique_lineups'], 2)
        self.assertEqual(p['mean_shared_players'], 6)
        self.assertEqual(p['mean_shared_role_slots'], 4)
        self.assertEqual(p['captain_pool'], 2)
        alpha = next(x for x in p['players'] if x['player'] == 'Alpha')
        self.assertEqual((alpha['total_pct'], alpha['captain_pct'], alpha['flex_pct']), (100, 50, 50))
        self.assertEqual(p['pairs'][0]['denominator'], 2)
        self.assertEqual(p['pairs'][0]['pct'], 100)

    def test_classic_slot_swap_equivalence(self):
        swapped = CLASSIC.replace('RB Bravo', 'RB Hotel').replace('FLEX Hotel', 'FLEX Bravo')
        p = self.analyze([[1, 1, 'U', 10, CLASSIC], [2, 2, 'U', 10, swapped]], contest_format='classic')['portfolios'][0]
        self.assertEqual(p['unique_lineups'], 1)
        self.assertEqual(p['mean_shared_players'], 9)
        self.assertIsNone(p['captain_pool'])
        self.assertIsNone(p['players'][0]['captain_pct'])

    def test_field_duplication_includes_own_entries_but_detects_other_users(self):
        result = self.analyze([[1, 1, 'U', 10, ROSTER], [1, 2, 'U', 10, ROSTER],
                               [1, 3, 'V', 10, ROSTER], [2, 4, 'U', 20, SWAPPED]])
        p = result['portfolios'][0]
        self.assertEqual(p['repeated_entries'], 1)
        self.assertEqual(p['entries_shared_with_other_users'], 2)
        self.assertAlmostEqual(p['mean_field_copies'], 7/3)
        self.assertEqual(p['mean_shared_players'], 6)

    def test_duplicate_ids_do_not_inflate_counts_and_conflicts_exclude_both(self):
        rows = [[1, 1, 'U', 10, ROSTER], [1, 1, 'U', 10, ROSTER],
                [2, 2, 'V', 20, ROSTER], [3, 2, 'V', 30, SWAPPED], [3, 3, 'V', 0, SWAPPED]]
        result = self.analyze(rows)
        self.assertEqual(result['audit']['accepted_entries'], 2)
        self.assertEqual(result['audit']['identical_duplicate_rows'], 1)
        self.assertEqual(result['audit']['conflicting_entry_ids_excluded'], 1)
        self.assertEqual(result['portfolios'][1]['mean_points'], 0)

    def test_missing_rosters_and_scores_are_unknown_zero_is_known(self):
        result = self.analyze([[1, 1, 'U', '', 'hidden'], [2, 2, 'V', 0, ROSTER], [3, 3, 'V', '', '']])
        u, v = result['portfolios']
        self.assertIsNone(u['unique_lineups'])
        self.assertIsNone(u['mean_points'])
        self.assertIsNone(u['repeated_pct'])
        self.assertEqual(v['mean_points'], 0)
        self.assertEqual(v['roster_coverage_pct'], 50)
        self.assertEqual(v['players'][0]['total_pct'], 100)
        self.assertEqual(v['players'][0]['denominator'], 1)
        self.assertIsNone(v['mean_shared_players'])

    def test_points_not_side_table_fpts_and_nonfinite_is_unknown(self):
        result = self.analyze([[1, 1, 'U', 80, ROSTER, 'Alpha', 'FLEX', '30%', 7],
                               [2, 2, 'U', 'nan', ROSTER, 'Bravo', 'FLEX', '20%', 10],
                               ['', '', '', '', '', 'Charlie', 'FLEX', '15%', 0]])
        self.assertEqual(result['portfolios'][0]['mean_points'], 80)
        self.assertEqual(result['portfolios'][0]['scored_entries'], 1)
        self.assertEqual(result['audit']['side_table_or_empty_rows'], 1)
        headers = [h for h in HEADERS if h != 'Points']
        self.write([[1, 1, 'U', ROSTER, 'Alpha', 'FLEX', '30%', 7]], headers)
        self.assertIsNone(analyze_standings(self.path)['portfolios'][0]['mean_points'])

    def test_invalid_shapes_and_duplicate_athlete_do_not_count_as_rosters(self):
        result = self.analyze([[1, 1, 'U', 10, ROSTER], [2, 2, 'U', 8, ROSTER.replace('Foxtrot', 'Alpha')],
                               [3, 3, 'U', 8, 'CPT Alpha FLEX Bravo'], [4, 4, 'U', 8, CLASSIC]])
        self.assertEqual(result['audit']['readable_rosters'], 1)
        self.assertEqual(result['portfolios'][0]['roster_coverage_pct'], 25)

    def test_captain_and_flex_ids_do_not_split_the_same_athlete(self):
        first = ROSTER.replace('Alpha', 'Alpha (1234)').replace('Bravo', 'Bravo (1235)')
        second = SWAPPED.replace('Alpha', 'Alpha (2234)').replace('Bravo', 'Bravo (2235)')
        result = self.analyze([[1, 1, 'U', 10, first], [2, 2, 'U', 10, second]])
        p = result['portfolios'][0]
        self.assertEqual(p['player_pool'], 6)
        self.assertEqual(p['mean_shared_players'], 6)
        self.assertEqual(p['unique_lineups'], 2)
        invalid = 'CPT Alpha (1234) FLEX Alpha (2234) FLEX Charlie FLEX Delta FLEX Echo FLEX Foxtrot'
        with self.assertRaisesRegex(ValueError, 'No readable'):
            self.analyze([[1, 1, 'U', 10, invalid]])

    def test_coverage_never_infers_full_field_from_max_rank(self):
        result = self.analyze([[1, 1, 'U', 10, ROSTER], [2, 2, 'U', 8, SWAPPED]])
        self.assertIsNone(result['supplied_field_size'])
        self.assertIn('unverified', result['coverage'])
        self.write([[1, 1, 'U', 10, ROSTER, 20], [2, 2, 'U', 8, SWAPPED, 21]], HEADERS[:5]+['FieldSize'])
        result = analyze_standings(self.path)
        self.assertIsNone(result['supplied_field_size'])
        self.assertIn('Conflicting', result['coverage'])

    def test_multiple_contests_rejected(self):
        self.write([[1, 1, 'U', 10, ROSTER, 'A'], [2, 2, 'U', 8, SWAPPED, 'B']], HEADERS[:5]+['ContestId'])
        with self.assertRaisesRegex(ValueError, 'Multiple contests'):
            analyze_standings(self.path)

    def test_cohorts_include_losing_users_and_weight_entrants_equally(self):
        result = self.analyze([[1, 1, 'A', 100, ROSTER], [2, 2, 'A', 100, ROSTER],
                               [3, 3, 'B', 0, ROSTER], [4, 4, 'B', 0, SWAPPED], [5, 5, 'B', 0, SWAPPED]])
        c = result['cohorts'][0]
        self.assertEqual(c['entry_band'], '2–5')
        self.assertEqual(c['median_mean_points'], 50)
        self.assertEqual(c['mean_points_entrants'], 2)
        self.assertEqual(c['entrants'], 2)
        self.assertEqual([c['entry_count'] for c in result['exact_count_cohorts']], [2, 3])

    def test_missing_ids_excluded_and_unparseable_files_fail_closed(self):
        result = self.analyze([[1, '', 'U', 10, ROSTER], [2, 2, '', 10, ROSTER], [3, 3, 'V', 10, ROSTER]])
        self.assertEqual(result['audit']['missing_id_or_username_rows'], 2)
        self.write([['Alpha', 1000]], ['Name', 'Salary'])
        with self.assertRaisesRegex(ValueError, 'EntryId'):
            analyze_standings(self.path)
        with self.assertRaisesRegex(ValueError, 'No readable'):
            self.analyze([[1, 1, 'U', 10, 'hidden']])

    def test_source_and_directory_unchanged_and_hash_recorded(self):
        self.write([[1, 1, 'U', 10, ROSTER]])
        before = self.path.read_bytes()
        result = analyze_standings(self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [self.path])
        self.assertEqual(result['source_sha256'], hashlib.sha256(before).hexdigest())

    def test_cancel_during_read_returns_no_partial_result(self):
        self.write([[i+1, i+1, 'U', 10, ROSTER] for i in range(1500)])
        stopped = False
        def progress(text):
            nonlocal stopped
            stopped = True
        with self.assertRaises(AnalysisCancelled):
            analyze_standings(self.path, progress=progress, cancelled=lambda: stopped)

    def test_source_mutation_rejected(self):
        self.write([[i+1, i+1, 'U', 10, ROSTER] for i in range(501)])
        def progress(text):
            with self.path.open('a') as handle:
                handle.write('\n')
        with self.assertRaisesRegex(ValueError, 'changed while reading'):
            analyze_standings(self.path, progress=progress)

    def test_summary_export_omits_lineups_ids_and_paths(self):
        result = self.analyze([[1, 1234567890, 'U', 10, ROSTER], [2, 2, 'V', 8, SWAPPED]])
        payload = share_payload(result, 'u')
        self.assertEqual(len(payload['portfolios']), 1)
        self.assertNotIn('lineups', payload['portfolios'][0])
        text = json.dumps(payload, allow_nan=False)
        self.assertNotIn('1234567890', text)
        self.assertNotIn(self.tmp.name, text)
        self.assertIn('lineups', share_payload(result, 'u', True)['portfolios'][0])
        self.assertNotIn('Username: V', render_report(result, 'u'))
        self.assertIn('0.0%', render_report(result, 'u'))

    def test_delimiters_and_quoted_usernames(self):
        self.write([[1, 1, 'User,Name (1/2)', -1, ROSTER]], delimiter=';')
        result = analyze_standings(self.path)
        self.assertEqual(result['portfolios'][0]['username'], 'User,Name')
        self.assertEqual(result['portfolios'][0]['mean_points'], -1)


class OpponentDialogTests(StandingsFixture):
    # These tests are run with the disposable QSettings/network runner.
    def setUp(self):
        super().setUp()
        from PyQt5 import QtWidgets
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from opponent_analysis_ui import OpponentAnalysisDialog
        self.dialog = OpponentAnalysisDialog('U')
        self.addCleanup(self.cleanup_dialog)

    def cleanup_dialog(self):
        self.dialog.close()
        self.drain()
        self.dialog.deleteLater()
        self.app.processEvents()

    def drain(self):
        deadline = time.monotonic() + 10
        while self.dialog._thread is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.002)
        self.assertIsNone(self.dialog._thread, 'Worker must retire before isolation cleanup')

    def test_real_worker_applies_completed_result_and_selects_user(self):
        self.write([[1, 1, 'U', 10, ROSTER], [2, 2, 'V', 8, SWAPPED]])
        self.dialog.start(str(self.path))
        self.drain()
        self.assertEqual(self.dialog.result['audit']['accepted_entries'], 2)
        self.assertEqual(self.dialog.table.rowCount(), 1)
        self.assertIn('Username: U', self.dialog.report.toPlainText())
        self.dialog.search.setText('no such user')
        self.assertEqual(self.dialog.report.toPlainText(), '')

    def test_cancel_latches_even_after_result_delivery(self):
        previous = self.analyze([[1, 1, 'U', 10, ROSTER]])
        self.dialog.result = previous
        self.dialog.start(str(self.path))
        self.dialog._receive(dict(previous, source_name='must-not-apply'))
        self.dialog.cancel()
        self.drain()
        self.assertIs(self.dialog.result, previous)
        self.assertIn('cancelled', self.dialog.status.text())

    def test_close_and_escape_wait_for_worker_without_partial_application(self):
        self.write([[i+1, i+1, 'U', 10, ROSTER] for i in range(1000)])
        for close in (self.dialog.close, self.dialog.reject):
            self.dialog.start(str(self.path))
            close()
            self.drain()
            self.assertIsNone(self.dialog.result)

    def test_worker_error_preserves_previous_and_allows_retry(self):
        previous = self.analyze([[1, 1, 'U', 10, ROSTER]])
        self.dialog.result = previous
        self.dialog.start(str(self.path.parent / 'missing.csv'))
        self.drain()
        self.assertIs(self.dialog.result, previous)
        self.assertIn('failed', self.dialog.status.text())
        self.dialog.start(str(self.path))
        self.drain()
        self.assertEqual(self.dialog.result['source_sha256'], previous['source_sha256'])

    def test_save_selected_detail_and_compact_all_user_summary(self):
        from PyQt5 import QtWidgets
        self.dialog.result = self.analyze([[1, 1, 'U', 10, ROSTER], [2, 2, 'V', 8, SWAPPED]])
        self.dialog.populate()
        destination = self.path.parent / 'analysis.json'
        with patch.object(QtWidgets.QFileDialog, 'getSaveFileName', return_value=(str(destination), '')):
            self.dialog.save_json()
            saved = json.loads(destination.read_text(encoding='utf-8'))
            self.assertEqual(len(saved['portfolios']), 1)
            self.assertNotIn('lineups', saved['portfolios'][0])
            self.dialog.details.setChecked(True)
            self.dialog.save_json()
            self.assertIn('lineups', json.loads(destination.read_text(encoding='utf-8'))['portfolios'][0])
            self.dialog.all_users.setChecked(True)
            self.assertFalse(self.dialog.details.isChecked())
            self.assertFalse(self.dialog.details.isEnabled())
            self.dialog.save_json()
        saved = json.loads(destination.read_text(encoding='utf-8'))
        self.assertEqual(len(saved['portfolios']), 2)
        self.assertNotIn('lineups', saved['portfolios'][0])
        self.assertNotIn('pairs', saved['portfolios'][0])
        self.assertFalse(saved['detailed_lineups_included'])

    def test_results_learning_opens_separate_dialog_with_saved_context(self):
        from main_window import ResultsLearningDialog
        with patch('main_window.generate_learning_report', return_value={}):
            parent = ResultsLearningDialog()
        try:
            parent.username_edit.setText('Example')
            parent.results_folder.setText(self.tmp.name)
            with patch('opponent_analysis_ui.OpponentAnalysisDialog') as factory:
                parent.opponents_button.click()
                factory.assert_called_once_with('Example', self.tmp.name, parent)
                factory.return_value.exec_.assert_called_once()
        finally:
            parent.close()

    def test_export_cannot_overwrite_original_after_failed_reload(self):
        from PyQt5 import QtWidgets
        self.write([[1, 1, 'U', 10, ROSTER]])
        original = self.path.read_bytes()
        self.dialog.start(str(self.path))
        self.drain()
        self.dialog.start(str(self.path.parent / 'missing.csv'))
        self.drain()
        with patch.object(QtWidgets.QFileDialog, 'getSaveFileName', return_value=(str(self.path), '')), \
             patch.object(QtWidgets.QMessageBox, 'warning') as warning:
            self.dialog.save_json()
            warning.assert_called_once()
        self.assertEqual(self.path.read_bytes(), original)

    def test_failed_export_preserves_existing_file_and_cleans_temporary_file(self):
        from PyQt5 import QtWidgets
        self.dialog.result = self.analyze([[1, 1, 'U', 10, ROSTER]])
        self.dialog.populate()
        destination = self.path.parent / 'existing.json'
        destination.write_text('keep', encoding='utf-8')
        files = set(self.path.parent.iterdir())
        with patch.object(QtWidgets.QFileDialog, 'getSaveFileName', return_value=(str(destination), '')), \
             patch('opponent_analysis_ui.os.replace', side_effect=OSError('blocked')), \
             patch.object(QtWidgets.QMessageBox, 'warning') as warning:
            self.dialog.save_json()
            warning.assert_called_once()
        self.assertEqual(destination.read_text(encoding='utf-8'), 'keep')
        self.assertEqual(set(self.path.parent.iterdir()), files)

    def test_sort_covers_entire_field_before_display_limit(self):
        result = self.analyze([[1, 1, 'U', 10, ROSTER]])
        sample = result['portfolios'][0]
        result['portfolios'] = [dict(sample, username=f'Entrant{i:04}', username_key=f'entrant{i:04}',
                                    mean_points=i) for i in range(1005)]
        self.dialog.result = result
        self.dialog.search.clear()
        self.dialog.populate()
        self.dialog.show()
        self.app.processEvents()
        self.assertEqual(self.dialog.table.rowCount(), 1000)
        started = time.monotonic()
        self.dialog.sort_rows(6)
        self.app.processEvents()
        self.assertLess(time.monotonic()-started, 5, 'Sorting the visible 1,000-row page must not rescan columns for every cell')
        self.assertEqual(self.dialog.selected_username(), 'entrant1004')
        self.dialog.sort_rows(6)
        self.assertEqual(self.dialog.selected_username(), 'entrant0000')
        self.dialog.search.setText('entrant1004')
        self.assertEqual(self.dialog.table.rowCount(), 1)
