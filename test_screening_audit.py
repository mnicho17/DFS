import copy
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from build_snapshots import fingerprint
from screening_audit import ScreeningAudit, load_audit
from showdown_simulation import showdown_signature
from optimizers import ShowdownLineup
from test_showdown_performance import _showdown_players
from build_diagnostics import create_build_diagnostic, format_build_report


class ScreeningAuditTests(unittest.TestCase):
    def setUp(self):
        self.players = _showdown_players()
        six = self.players[:3] + self.players[18:21]
        self.rows = [ShowdownLineup(six[0], six[1:]), ShowdownLineup(six[1], [six[0]] + six[2:])]
        for i, lu in enumerate(self.rows):
            lu.sim_metrics = dict(sim_top_one_pct=2+i, sim_scenarios=250)
        self.context = dict(screening=dict(scenarios=250), salary_cap=50000)

    def capture(self, **kwargs):
        return ScreeningAudit(self.rows, self.rows[:1], [], self.players,
            input_id='a'*64, context=self.context, **kwargs)

    def test_full_bank_salary_exclusions_and_exact_coarse_metrics_are_frozen(self):
        original = copy.deepcopy(self.players)
        capture = self.capture()
        coarse = dict(lineups=self.rows[:1], report=dict(scenarios=250, field_lineups=80))
        capture.screened(coarse, self.rows[:1], {showdown_signature(self.rows[0])}, self.rows[:1])
        self.rows[0].sim_metrics['sim_top_one_pct'] = 99
        self.players[0]['FlexProjection'] = 999
        with tempfile.TemporaryDirectory() as folder:
            receipt = capture.save(self.rows[:1], folder=folder)
            self.assertEqual(receipt['status'], 'saved')
            payload = load_audit(Path(folder)/receipt['filename'])['payload']
        self.assertEqual(payload['players'], original)
        self.assertEqual(len(payload['candidates']), 2)
        self.assertEqual(payload['candidates'][0]['coarse_metrics']['sim_top_one_pct'], 2)
        self.assertIsNone(payload['candidates'][1]['coarse_metrics'])
        self.assertEqual(payload['captain_reserved'], [0])
        self.assertEqual(payload['feasible_reserved'], [0])
        self.assertEqual(payload['shortlisted'], [0])
        self.assertEqual(payload['selected'], [0])

    def test_retained_input_order_and_cached_screening_are_explicit(self):
        capture = ScreeningAudit(self.rows[:1], self.rows[:1], self.rows[1:], self.players,
            input_id='b'*64, context=self.context)
        ordered = self.rows[1:] + self.rows[:1]
        capture.screened(dict(lineups=ordered,report=dict(scenarios=250,field_lineups=80,screening_reused=2)), ordered, set(), [])
        with tempfile.TemporaryDirectory() as folder:
            receipt = capture.save(ordered, folder=folder)
            payload = load_audit(Path(folder)/receipt['filename'])['payload']
        self.assertEqual(payload['screening_input_order'], [1,0])
        self.assertEqual(payload['coarse_order'], [1,0])
        self.assertEqual(payload['screening_report']['screening_reused'], 2)

    def test_partial_screening_and_no_screening_never_claim_complete(self):
        capture = self.capture()
        capture.screened(dict(lineups=self.rows[:1],report=dict(scenarios=100,field_lineups=80)), self.rows[:1], set(), [])
        with tempfile.TemporaryDirectory() as folder:
            receipt = capture.save(self.rows[:1],folder=folder)
            self.assertEqual(receipt['screening_status'], 'partial')
            self.assertEqual(load_audit(Path(folder)/receipt['filename'])['payload']['screening_report']['scenarios'], 100)
            self.assertEqual(self.capture().save([],folder=folder)['screening_status'], 'not run')

    def test_missing_input_cancel_and_count_limit_do_not_publish_partial_banks(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(self.capture(cancelled=lambda:True).save([],folder=folder)['status'], 'not saved')
            self.assertEqual(self.capture().save([],build_cancelled=True,folder=folder)['status'], 'not saved')
            invalid = ScreeningAudit([],[],[],[],input_id='',context=self.context)
            self.assertEqual(invalid.save([],folder=folder)['status'], 'not saved')
            with patch('screening_audit.MAX_CANDIDATES',1):
                self.assertIn('no truncated bank', self.capture().save([],folder=folder)['reason'])
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_size_and_storage_failures_leave_no_published_or_temporary_files(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch('screening_audit.MAX_BYTES',1):
                self.assertEqual(self.capture().save([],folder=folder)['status'], 'not saved')
            with patch('screening_audit.os.replace',side_effect=OSError('disk error')):
                self.assertEqual(self.capture().save([],folder=folder)['status'], 'not saved')
            with patch('screening_audit.MAX_COMPRESSED_BYTES',1):
                self.assertEqual(self.capture().save([],folder=folder)['status'], 'not saved')
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_integrity_and_bounded_decompression_reject_modified_artifacts(self):
        with tempfile.TemporaryDirectory() as folder:
            receipt = self.capture().save([],folder=folder); path=Path(folder)/receipt['filename']
            with patch('screening_audit.MAX_BYTES',1):
                with self.assertRaisesRegex(ValueError,'decompression'):load_audit(path)
            raw=json.loads(gzip.decompress(path.read_bytes()));raw['payload']['shortlisted']=[999]
            path.write_bytes(gzip.compress(json.dumps(raw).encode()))
            with self.assertRaisesRegex(ValueError,'integrity'):load_audit(path)
            raw['audit_id']=fingerprint(raw['payload']);path.write_bytes(gzip.compress(json.dumps(raw).encode()))
            with self.assertRaisesRegex(ValueError,'stage index'):load_audit(path)

    def test_receipt_roundtrip_in_build_report_and_legacy_reports(self):
        with tempfile.TemporaryDirectory() as folder:
            receipt=self.capture().save([],folder=folder)
        record=create_build_diagnostic(context=dict(sport='NFL',kind='showdown'),timing_report=dict(compute_mode='Deep'),
            sim_report=dict(deep_build=dict(enabled=True,screening_audit=receipt)))
        text=format_build_report(json.loads(json.dumps(record)))
        self.assertIn('Screening audit saved',text)
        self.assertIn('coarse scoring not run',text)
        record.pop('screening_audit')
        self.assertNotIn('Screening audit saved',format_build_report(record))

    def test_diagnostic_capture_does_not_mutate_candidates_or_inputs(self):
        before=copy.deepcopy(self.rows);players=copy.deepcopy(self.players)
        capture=self.capture();capture.screened(dict(lineups=self.rows[:1],report=dict(scenarios=250)),self.rows[:1],set(),[])
        with tempfile.TemporaryDirectory() as folder:capture.save(self.rows[:1],folder=folder)
        self.assertEqual(self.players,players)
        for actual,original in zip(self.rows,before):
            self.assertEqual(showdown_signature(actual),showdown_signature(original))
            self.assertEqual(actual.sim_metrics,original.sim_metrics)
