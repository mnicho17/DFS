"""Bound original audits only; no hindsight-inferred rejection causes."""
from test_environment import install
install()
import copy
import gzip
import hashlib
import json
import unittest
from unittest.mock import patch
import zipfile

import build_explanations as explanations
import hindsight_evidence as he
import hindsight_solver as hs
import historical_identity as hi
from build_snapshots import fingerprint
from hindsight_contract import Cancelled, Frozen, validate_roster
from screening_audit import ScreeningAudit, load_audit_bytes
from showdown_simulation import showdown_signature
from optimizers import ShowdownLineup
from test_hindsight_evidence import SourceFixture
from test_portfolio_risk_evidence import logical_db, source_bytes
import screening_explanation_evidence as evidence


def rewrite(path, edit):
    with zipfile.ZipFile(path) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    payload = json.loads(files['lineups.json'])
    edit(files, payload)
    files['lineups.json'] = json.dumps(payload).encode()
    files['manifest.json'] = json.dumps(dict(metadata=payload['metadata'],
        sha256={name: hashlib.sha256(raw).hexdigest() for name, raw in files.items() if name != 'manifest.json'})).encode()
    with zipfile.ZipFile(path, 'w') as archive:
        for name, raw in files.items(): archive.writestr(name, raw)


def rebind(files, payload, change):
    envelope = json.loads(gzip.decompress(files['screening-audit.json.gz']))
    change(envelope['payload'])
    envelope['audit_id'] = fingerprint(envelope['payload'])
    raw = gzip.compress(json.dumps(envelope).encode(), mtime=0)
    files['screening-audit.json.gz'] = raw
    payload['metadata']['screening_evidence'].update(audit_id=envelope['audit_id'],
        sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))


class OriginalScreeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def fixture(self, scoring='complete'):
        f = SourceFixture(); self.addCleanup(f.close)
        f.snap['inputs']['recipe'].update(salary_cap=50000, salary_strategy='Near Cap', build_style='Strategic',
            ownership_mode='Balanced', ownership_weight=.15, requested_lineups=1)
        path = f.archive()
        players = f.snap['inputs']['players']; six = players[:6]
        rows = [ShowdownLineup(six[i], [p for j, p in enumerate(six) if i != j]) for i in range(3)]
        generated = [rows[0], rows[0], rows[1]]; retained = [rows[2]]
        context = dict(salary_cap=50000, salary_strategy='Near Cap', build_style='Strategic',
            own_mode='Balanced', own_weight=.15, requested_count=1, rules={}, screening=dict(scenarios=250))
        audit = ScreeningAudit(generated, generated, retained, players,
            input_id=f.snap['input_id'], context=context)
        if scoring != 'not run':
            audit.screened(dict(lineups=retained + generated,
                report=dict(scenarios=250 if scoring=='complete' else 100)),
                [rows[0], rows[2]], {showdown_signature(rows[0])}, [rows[0]])
        receipt = audit.save(rows[:1], folder=f.root/'screening-audits')
        self.assertEqual(receipt['status'], 'saved')
        envelope = load_audit_bytes((f.root/'screening-audits'/receipt['filename']).read_bytes())
        envelope['payload'].update(frozen_at='2026-09-21T18:05:00-04:00', saved_at='2026-09-21T18:08:00-04:00')
        envelope['audit_id'] = fingerprint(envelope['payload'])
        raw = gzip.compress(json.dumps(envelope).encode(), mtime=0)
        def embed(files, payload):
            files['screening-audit.json.gz'] = raw
            payload['metadata']['screening_evidence'] = dict(status='bound', audit_id=envelope['audit_id'],
                sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw), member='screening-audit.json.gz')
        rewrite(path, embed); hi.reconcile(f.db)
        return f, path

    def capture(self, f):
        hi.reconcile(f.db)
        d = f.one()
        return he.capture(f.db, f.selection(True, d['build_evidence'][0]['archive_id']))

    def test_exact_original_stage_membership_counts_and_readonly_solver(self):
        f, path = self.fixture(); before = logical_db(f.db); files = source_bytes(f.root)
        with patch('showdown_simulation.simulate_showdown', side_effect=AssertionError('must not simulate')):
            c = self.capture(f); result = hs.calculate(c, seconds=5).data
        row = result['build_explanation']['targets'][0]
        self.assertEqual(c.data['build_explanation_evidence']['screening']['status'], 'ready')
        self.assertEqual(row['recorded_stages']['generated'], dict(status='observed', occurrences=2))
        self.assertEqual(row['recorded_stages']['screened']['occurrences'], 2)
        self.assertEqual(row['recorded_stages']['selected']['occurrences'], 1)
        self.assertEqual(row['pipeline']['independent_validation'], 'not_recorded')
        self.assertEqual(row['pipeline']['portfolio_selection'], 'not_recorded')
        without = c.data
        without['build_explanation_evidence'].pop('screening')
        baseline = hs.calculate(Frozen.freeze(without), seconds=5).data
        for scope in ('supplied','snapshot'):
            for key in ('status','score_units','lineups','ties'):
                self.assertEqual(result['scopes'][scope][key], baseline['scopes'][scope][key])
        self.assertEqual(result['gaps'], baseline['gaps'])
        self.assertEqual(before, logical_db(f.db)); self.assertEqual(files, source_bytes(f.root))

    def test_partial_and_not_run_are_not_promoted_to_complete(self):
        for status in ('partial', 'not run'):
            with self.subTest(status=status):
                f, path = self.fixture(status); c = self.capture(f)
                result = hs.calculate(c, seconds=5).data['build_explanation']
                self.assertEqual(result['screening_evidence']['screening_status'], status)
                self.assertIn('Captured coarse scoring: '+status, '\n'.join(explanations.lines(result)))
                if status=='not run': self.assertEqual(result['targets'][0]['pipeline']['screening'], 'not_run')

    def test_absence_is_not_a_rejection_and_captain_role_is_exact(self):
        f, path = self.fixture(); c = self.capture(f).data
        roster = [dict(key=p['key'], role='CPT' if i==3 else 'FLEX') for i,p in enumerate(c['pool'][:6])]
        row = validate_roster(roster, c['pool'], 'showdown')
        report = explanations.explain(c, dict(supplied=dict(lineups=[row]), snapshot=dict(lineups=[])))
        self.assertEqual(report['targets'][0]['recorded_stages']['generated'], dict(status='not_observed', occurrences=0))
        self.assertIn('not rejection evidence', '\n'.join(explanations.lines(report, c['pool'], True)))

    def test_default_copy_is_aggregate_and_private_copy_keeps_stage_details(self):
        f, path = self.fixture(); c = self.capture(f)
        report = hs.calculate(c, seconds=5).data['build_explanation']
        public = '\n'.join(explanations.lines(report, c.data['pool']))
        private = '\n'.join(explanations.lines(report, c.data['pool'], True))
        self.assertNotIn('CPT A', public); self.assertIn('CPT A', private)
        self.assertIn('Generated: recorded 2 occurrence(s)', private)
        self.assertIn('Recorded generated:', public)

    def test_missing_or_modified_member_withholds_only_stage_evidence(self):
        for mode in ('missing', 'changed'):
            with self.subTest(mode=mode):
                f, path = self.fixture()
                def edit(files, payload):
                    if mode=='missing': files.pop('screening-audit.json.gz')
                    else: files['screening-audit.json.gz'] = gzip.compress(b'{}')
                rewrite(path, edit); c = self.capture(f)
                self.assertEqual(c.data['build_explanation_evidence']['archive']['status'], 'ready')
                self.assertEqual(c.data['build_explanation_evidence']['screening']['status'], 'unavailable_evidence')
                self.assertEqual(hs.calculate(c, seconds=5).data['scopes']['supplied']['points'], '70')

    def test_original_capture_failure_is_unavailable_rather_than_never_recorded(self):
        f, path = self.fixture()
        def failed(files, payload):
            files.pop('screening-audit.json.gz')
            payload['metadata']['screening_evidence'] = dict(status='unavailable')
        rewrite(path, failed)
        self.assertEqual(self.capture(f).data['build_explanation_evidence']['screening']['status'], 'unavailable_evidence')

    def test_resigned_conflicts_do_not_make_a_valid_binding(self):
        mutations = [lambda p: p['players'][0].__setitem__('CptID', 'other'),
                     lambda p: p.__setitem__('input_id', 'f'*64),
                     lambda p: p['context'].__setitem__('own_weight', .9),
                     lambda p: p.__setitem__('saved_at', '2026-09-22T18:08:00-04:00'),
                     lambda p: p.__setitem__('selected', [p['selected'][0]]*2),
                     lambda p: p['screening_report'].__setitem__('scenarios', 100),
                     lambda p: p.__setitem__('screening_status', 'not run')]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                f, path = self.fixture()
                rewrite(path, lambda files, payload: rebind(files, payload, mutation))
                self.assertEqual(self.capture(f).data['build_explanation_evidence']['screening']['status'], 'unavailable_evidence')

    def test_conflicting_embedded_occurrences_are_order_independent(self):
        f, path = self.fixture(); reader = he.CaptureReader(lambda:False); reader.archive(path)
        matches = next(iter(reader.archives.values())); key = next(iter(reader.screening))
        raw = reader.screening[key][0]
        for copies in ([raw, raw+b'changed'], [raw+b'changed', raw]):
            reader.screening[key] = copies
            self.assertEqual(evidence.capture(matches, matches[0][0], f.pool, 'showdown', reader)['status'], 'unavailable_evidence')

    def test_cancellation_during_decode_is_not_swallowed(self):
        f, path = self.fixture(); reader = he.CaptureReader(lambda:False); reader.archive(path)
        matches = next(iter(reader.archives.values())); phase = [False]
        def decode(raw, **kwargs):
            phase[0] = True
            return load_audit_bytes(raw, **kwargs)
        with patch('screening_audit.load_audit_bytes', side_effect=decode):
            with self.assertRaises(Cancelled):
                evidence.capture(matches, matches[0][0], f.pool, 'showdown', reader, lambda:phase[0])

    def test_expanded_audit_consumes_the_shared_read_budget(self):
        f, path = self.fixture(); reader = he.CaptureReader(lambda:False); reader.archive(path)
        matches = next(iter(reader.archives.values()))
        with patch.object(reader, 'consume', side_effect=OverflowError('read budget')):
            with self.assertRaisesRegex(OverflowError, 'read budget'):
                evidence.capture(matches, matches[0][0], f.pool, 'showdown', reader)

    def test_role_id_conflict_in_the_qualified_pool_cannot_supply_membership(self):
        f, path = self.fixture(); reader = he.CaptureReader(lambda:False); reader.archive(path)
        matches = next(iter(reader.archives.values())); pool = copy.deepcopy(f.pool)
        pool[0]['roles']['CPT']['id'] = 'conflicting-role-id'
        self.assertEqual(evidence.capture(matches, matches[0][0], pool, 'showdown', reader)['status'], 'unavailable_evidence')

    def test_explanation_tab_uses_frozen_membership_and_keeps_copy_privacy(self):
        from PyQt5 import QtWidgets
        from hindsight_ui import HindsightWidget
        app = self.app
        f, path = self.fixture(); c = self.capture(f)
        view = HindsightWidget(str(f.db)); self.addCleanup(view.deleteLater)
        view.report = hs.calculate(c, seconds=5); view._render()
        self.assertIn('Generated: recorded 2 occurrence(s)', view.explanation.toPlainText())
        self.assertIn('Captured coarse scoring: complete', view.explanation.toPlainText())
        view.copy_button.setEnabled(True); view.copy_summary()
        self.assertNotIn('CPT A', app.clipboard().text())
        view.private.setChecked(True); view.copy_summary()
        self.assertIn('Generated: recorded 2 occurrence(s)', app.clipboard().text())

    def test_original_zip_is_revalidated_after_member_capture(self):
        f, path = self.fixture(); reader = he.CaptureReader(lambda:False); reader.archive(path)
        rewrite(path, lambda files, payload: files.__setitem__('screening-audit.json.gz', b'changed'))
        with self.assertRaisesRegex(ValueError, 'bytes changed'): reader.revalidate()
