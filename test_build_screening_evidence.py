"""Original audit binding; never reconstruct missing pipeline causes."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from build_snapshots import create_snapshot, save_snapshot
from build_screening_evidence import capture
from screening_audit import ScreeningAudit
from test_showdown_performance import _showdown_players
from optimizers import ShowdownLineup


class ScreeningBindingTests(unittest.TestCase):
    def setUp(self):
        self.players = _showdown_players()
        six = self.players[:3] + self.players[18:21]
        self.rows = [ShowdownLineup(six[0], six[1:])]
        self.recipe = dict(sport='NFL', contest_kind='showdown', salary_cap=50000,
            salary_strategy='Near Cap', build_style='Strategic', ownership_mode='Balanced',
            ownership_weight=.15, requested_lineups=1)
        self.snapshot = create_snapshot(self.players, self.recipe, {})
        self.context = dict(salary_cap=50000, salary_strategy='Near Cap', build_style='Strategic',
            own_mode='Balanced', own_weight=.15, requested_count=1, rules={}, screening=dict(scenarios=250))

    def audit(self, folder, rows=None):
        audit = ScreeningAudit(self.rows, self.rows, [], self.players,
            input_id=self.snapshot['input_id'], context=self.context)
        return audit.save(self.rows if rows is None else rows, folder=folder)

    def bind(self, folder, receipt, **kwargs):
        return capture(folder, receipt, self.snapshot, self.rows, kind='showdown', **kwargs)

    def test_exact_bytes_and_partial_not_run_capture_are_bound_without_mutation(self):
        before = copy.deepcopy(self.snapshot)
        with tempfile.TemporaryDirectory() as folder:
            receipt = self.audit(folder)
            result, raw = self.bind(folder, receipt)
            self.assertEqual(result['status'], 'bound')
            self.assertEqual(raw, (Path(folder)/receipt['filename']).read_bytes())
            self.assertEqual(receipt['screening_status'], 'not run')
        self.assertEqual(before, self.snapshot)

    def test_repeated_inputs_do_not_allow_a_different_audit_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            first = self.audit(folder)
            second = self.audit(folder)
            swapped = dict(first, filename=second['filename'])
            # Payload timestamp makes each observation distinct even for identical inputs.
            self.assertNotEqual(first['audit_id'], second['audit_id'])
            self.assertEqual(self.bind(folder, swapped)[0]['status'], 'unavailable')

    def test_selected_duplicate_occurrences_are_not_collapsed(self):
        with tempfile.TemporaryDirectory() as folder:
            receipt = self.audit(folder, self.rows * 2)
            self.assertEqual(self.bind(folder, receipt)[0]['status'], 'unavailable')
            self.assertEqual(capture(folder, receipt, self.snapshot, self.rows * 2,
                                    kind='showdown')[0]['status'], 'bound')

    def test_snapshot_identity_rules_and_context_conflicts_withhold_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            receipt = self.audit(folder)
            for section, key, value in [('players', 'FlexID', 'other'), ('rules', 'salary_cap', 1),
                                        ('recipe', 'ownership_weight', .9)]:
                snapshot = copy.deepcopy(self.snapshot)
                if section == 'players': snapshot['inputs']['players'][0][key] = value
                else: snapshot['inputs'][section][key] = value
                result, raw = capture(folder, receipt, snapshot, self.rows, kind='showdown')
                self.assertEqual(result['status'], 'unavailable')
                self.assertIsNone(raw)

    def test_conflicting_explicit_output_id_cannot_hide_behind_the_same_player_key(self):
        with tempfile.TemporaryDirectory() as folder:
            receipt = self.audit(folder)
            changed = copy.deepcopy(self.rows)
            changed[0]['Captain']['FlexID'] = 'conflicting-explicit-id'
            self.assertEqual(capture(folder, receipt, self.snapshot, changed,
                                     kind='showdown')[0]['status'], 'unavailable')

    def test_validly_fingerprinted_audit_with_different_context_is_not_bound(self):
        with tempfile.TemporaryDirectory() as folder:
            self.context['own_weight'] = .9
            receipt = self.audit(folder)
            self.assertEqual(self.bind(folder, receipt)[0]['status'], 'unavailable')
            self.context['own_weight'] = .15
            self.context['rules'] = dict(min_unique=4)
            receipt = self.audit(folder)
            self.assertEqual(self.bind(folder, receipt)[0]['status'], 'unavailable')

    def test_missing_changed_path_escape_cancel_and_legacy_are_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            receipt = self.audit(folder)
            self.assertEqual(self.bind(folder, receipt, cancelled=True)[0]['status'], 'unavailable')
            self.assertEqual(self.bind(folder, {})[0]['status'], 'not_recorded')
            self.assertEqual(self.bind(folder, None)[0]['status'], 'unavailable')
            self.assertIsNone(self.bind(folder, dict(receipt, filename='../outside.gz'))[1])
            path = Path(folder)/receipt['filename']
            path.write_bytes(b'changed')
            self.assertIsNone(self.bind(folder, receipt)[1])
            path.unlink()
            self.assertIsNone(self.bind(folder, receipt)[1])

    def test_malformed_optional_audit_cannot_abort_the_output_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            receipt = self.audit(folder)
            with patch('screening_audit.load_audit_bytes', side_effect=AttributeError('malformed payload')):
                self.assertEqual(self.bind(folder, receipt)[0]['status'], 'unavailable')

    def test_archive_embeds_exact_original_bytes_and_manifest_without_extra_reader_api(self):
        from build_archives import save_build_archive, archive_folder
        from review_build_evidence import Reader
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, DFS_OPTIMIZER_DATA_DIR=folder), patch('build_archives.implementation_id', return_value='test'):
            root = archive_folder().parent
            snapshots = root/'snapshots'; snapshots.mkdir(parents=True)
            save_snapshot(str(snapshots/(self.snapshot['input_id']+'.json')), self.snapshot)
            receipt = self.audit(root/'screening-audits')
            original = (root/'screening-audits'/receipt['filename']).read_bytes()
            result = save_build_archive(dict(kind='showdown', lineups=self.rows),
                dict(input_id=self.snapshot['input_id'], kind='showdown', sport='NFL'),
                dict(screening_audit=receipt))
            path = archive_folder()/result['filename']
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(archive.read('screening-audit.json.gz'), original)
                self.assertEqual(json.loads(archive.read('manifest.json'))['metadata']['screening_evidence']['status'], 'bound')
            reader = Reader(lambda: False)
            self.assertEqual(len(reader.archive_details(path)), 5)
