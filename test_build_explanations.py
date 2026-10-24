"""Recorded-only RL-07B witnesses; exact identities, no generation calls."""
from test_environment import install
install()
import copy
import unittest
from unittest.mock import patch

from hindsight_contract import Frozen, Cancelled, restrictions, validate_roster
import build_explanations as be
import hindsight_solver as hs
from test_hindsight_solver import athletes,captured
from test_hindsight_evidence import SourceFixture
from test_portfolio_risk_evidence import logical_db,source_bytes,rewrite_archive,repeat_archive
import hindsight_evidence as he
import historical_identity as hi


def witness(pool=None,kind='showdown',captain=0):
    pool=pool or athletes(kind)
    if kind=='showdown':roster=[dict(key=p['key'],role='CPT' if i==captain else 'FLEX') for i,p in enumerate(pool[:6])]
    else:
        ids=[0,2,3,5,6,7,9,4,11];roles=['QB','RB','RB','WR','WR','WR','TE','FLEX','DST']
        roster=[dict(key=pool[i]['key'],role=r) for i,r in zip(ids,roles)]
    return validate_roster(roster,pool,kind)


def review(local=None,archive=None,kind='showdown',row=None):
    d=captured(kind,local=local).data
    if archive is not None:d['build_explanation_evidence']=dict(archive=archive)
    r=row or witness(d['pool'],kind)
    scopes={k:dict(lineups=[r] if k=='supplied' else []) for k in ('supplied','snapshot')}
    return be.explain(d,scopes),d


class RecordedChecksTests(unittest.TestCase):
    def test_missing_snapshot_and_archive_are_not_successes_or_zeros(self):
        r,d=review();row=r['targets'][0]
        self.assertEqual(row['snapshot']['status'],'not_requested')
        self.assertEqual(row['generated_output'],dict(status='not_requested',occurrences=None))
        self.assertEqual(set(row['pipeline'].values()),{'not_recorded'})

    def test_supported_local_pass_does_not_certify_full_build(self):
        r,d=review(restrictions());self.assertEqual(r['targets'][0]['snapshot']['status'],'passes_supported_checks')
        self.assertIn('portfolio feasibility',r['limitation'])

    def test_reports_all_local_conflicts_including_role_specific_locks(self):
        rules=restrictions();rules.update(salary_cap=38000,require=[['101','CPT']],exclude=[['100','CPT']],
            groups=[dict(type='at_least_one',keys=['106','107']),dict(type='never_together',keys=['102','103'])])
        r,d=review(rules);issues=r['targets'][0]['snapshot']['issues']
        self.assertEqual({x['code'] for x in issues},{'recorded_salary_cap','recorded_requirement_missing','recorded_exclusion_present','recorded_group_at_least_one','recorded_group_never_together'})
        self.assertEqual(r['targets'][0]['snapshot']['status'],'conflicts')

    def test_flex_requirement_cannot_be_satisfied_at_captain(self):
        rules=restrictions();rules['require']=[['100','FLEX']]
        r,_=review(rules);self.assertEqual(r['targets'][0]['snapshot']['status'],'conflicts')

    def test_role_specific_fade_does_not_exclude_other_role(self):
        rules=restrictions();rules['exclude']=[['100','FLEX']]
        r,_=review(rules);self.assertEqual(r['targets'][0]['snapshot']['status'],'passes_supported_checks')

    def test_unsupported_local_rules_do_not_claim_partial_pass(self):
        d=captured(local=restrictions()).data;d['gates']['snapshot']['status']='unsupported_rules'
        r=be.explain(d,dict(supplied=dict(lineups=[witness()]),snapshot=dict(lineups=[])))
        self.assertEqual(r['targets'][0]['snapshot']['status'],'unsupported_rules')

    def test_exact_output_presence_retains_occurrences(self):
        row=witness();archive=dict(status='ready',outputs=3,distinct=1,signatures=[dict(signature=row['signature'],occurrences=3)])
        r,_=review(archive=archive);self.assertEqual(r['targets'][0]['generated_output'],dict(status='present',occurrences=3))
        self.assertEqual(r['archive']['outputs'],3);self.assertNotIn('signatures',r['archive'])

    def test_different_captain_is_absent_without_invented_reason(self):
        row=witness(captain=1);archive=dict(status='ready',signatures=[dict(signature=row['signature'],occurrences=1)])
        r,_=review(archive=archive);self.assertEqual(r['targets'][0]['generated_output'],dict(status='absent',occurrences=0))
        self.assertEqual(set(r['targets'][0]['pipeline'].values()),{'not_recorded'})

    def test_classic_flex_assignment_and_slot_permutations_are_equivalent(self):
        pool=athletes('classic');row=witness(pool,'classic');other=copy.deepcopy(row)
        for slot in other['roster']:
            if slot['key']=='102':slot['role']='FLEX'
            if slot['key']=='104':slot['role']='RB'
        other['roster'].reverse()
        archive=dict(status='ready',signatures=[dict(signature=row['signature'],occurrences=1)])
        r,_=review(archive=archive,kind='classic',row=other)
        self.assertEqual(r['targets'][0]['generated_output']['status'],'present')

    def test_forged_witness_signature_is_unavailable(self):
        row=witness();row['signature'][0]='forged';r,_=review(row=row)
        self.assertEqual(r['targets'][0]['status'],'unavailable_witness')

    def test_aggregate_copy_omits_names_ids_and_individual_constraints(self):
        rules=restrictions();rules['exclude']=[['100','ANY']];r,d=review(rules)
        text='\n'.join(be.lines(r,d['pool']))
        for secret in ('100','CPT A','recorded_exclusion_present'):self.assertNotIn(secret,text)
        private='\n'.join(be.lines(r,d['pool'],True));self.assertIn('Contains recorded excluded player (ANY): A',private)

    def test_duplicate_observed_witness_examples_are_distinct_rosters_only(self):
        d=captured().data;d['observed']['highest_witnesses']=[witness(),witness()]
        r=be.explain(d,dict(supplied=dict(lineups=[]),snapshot=dict(lineups=[])))
        self.assertEqual(len(r['targets']),1)

    def test_cancel_is_checked_before_witness_work(self):
        d=captured().data
        with self.assertRaises(Cancelled):be.explain(d,dict(supplied=dict(lineups=[witness()]),snapshot=dict(lineups=[])),lambda:True)

    def test_legacy_report_has_explicit_unavailable_message(self):self.assertIn('older report',be.lines(None)[0])

    def test_solver_receipts_and_scores_are_unchanged(self):
        c=captured(local=restrictions());a=hs.calculate(c,seconds=5)
        with patch.object(be,'explain',return_value=None):b=hs.calculate(c,seconds=5)
        for scope in ('supplied','snapshot'):
            for key in ('status','score_units','lineups','ties'):self.assertEqual(a.data['scopes'][scope][key],b.data['scopes'][scope][key])
        self.assertEqual(a.data['gaps'],b.data['gaps'])


class ArchiveExplanationTests(unittest.TestCase):
    def fixture(self,kind='showdown'):
        f=SourceFixture(kind);self.addCleanup(f.close);path=f.archive();hi.reconcile(f.db)
        return f,path
    def capture(self,f):
        data=f.one();return he.capture(f.db,f.selection(True,data['build_evidence'][0]['archive_id']))

    def test_fresh_archive_membership_is_readonly_and_keeps_repeats(self):
        f,path=self.fixture();repeat_archive(path,3);hi.reconcile(f.db)
        before=logical_db(f.db);files=source_bytes(f.root);c=self.capture(f)
        r=hs.calculate(c,seconds=5);receipt=c.data['build_explanation_evidence']['archive']
        self.assertEqual(receipt['status'],'ready');self.assertEqual(receipt['outputs'],3);self.assertEqual(receipt['distinct'],1)
        self.assertEqual(r.data['build_explanation']['targets'][0]['generated_output'],dict(status='present',occurrences=3))
        self.assertEqual(before,logical_db(f.db));self.assertEqual(files,source_bytes(f.root))

    def test_invalid_explicit_archive_id_withholds_membership_without_changing_solve(self):
        f,path=self.fixture()
        rewrite_archive(path,lambda p:p['lineups'][0]['slots'][0]['player'].__setitem__('CptID','99999'))
        hi.reconcile(f.db);c=self.capture(f);r=hs.calculate(c,seconds=5).data
        self.assertEqual(c.data['build_explanation_evidence']['archive']['status'],'unavailable_evidence')
        self.assertEqual(r['scopes']['supplied']['points'],'70')
        self.assertEqual(r['scopes']['snapshot']['points'],'70')

    def test_cancel_during_archive_adaptation_is_not_swallowed(self):
        f,path=self.fixture()
        with patch('build_explanations.archive_receipt',side_effect=Cancelled):
            with self.assertRaises(Cancelled):self.capture(f)

    def test_changed_snapshot_revision_cannot_supply_archive_membership(self):
        f,path=self.fixture();reader=he.CaptureReader(lambda:False);reader.archive(path)
        matches=next(iter(reader.archives.values()));chosen=copy.deepcopy(matches[0][0]);chosen['raw']['created_at']='2026-09-21T17:00:00-04:00'
        with self.assertRaisesRegex(ValueError,'revision_differs'):be.archive_receipt(matches,chosen,f.pool,f.kind)

    def test_conflicting_archive_occurrences_rejected_in_both_orders(self):
        f,path=self.fixture();reader=he.CaptureReader(lambda:False);reader.archive(path)
        a=next(iter(reader.archives.values()))[0];b=copy.deepcopy(a);b[4][0]['slots'][0]['player']['CptID']='bad'
        for matches in ([a,b],[b,a]):
            with self.assertRaisesRegex(ValueError,'conflicting_or_missing'):be.archive_receipt(matches,a[0],f.pool,f.kind)

    def test_classic_archive_exact_role_validation(self):
        f,path=self.fixture('classic');c=self.capture(f);r=hs.calculate(c,seconds=5).data
        self.assertEqual(c.data['build_explanation_evidence']['archive']['status'],'ready')
        self.assertEqual(r['scopes']['supplied']['points'],'111')


class ExplanationViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5 import QtWidgets
        cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_readonly_tab_and_private_copy_use_frozen_report(self):
        from hindsight_ui import HindsightWidget
        f=SourceFixture();self.addCleanup(f.close);path=f.archive();hi.reconcile(f.db)
        selected=f.one()['build_evidence'][0]['archive_id'];c=he.capture(f.db,f.selection(True,selected))
        view=HindsightWidget(str(f.db));self.addCleanup(view.deleteLater);view.report=hs.calculate(c,seconds=5);view._render()
        self.assertTrue(view.explanation.isReadOnly())
        self.assertIn('Build explanation',[view.tabs.tabText(i) for i in range(view.tabs.count())])
        text=view.explanation.toPlainText();self.assertIn('occurrences 1',text);self.assertIn('CPT A',text)
        self.assertIn('not recorded per witness',text)
        view.copy_button.setEnabled(True);view.copy_summary();self.assertNotIn('CPT A',self.app.clipboard().text())
        view.private.setChecked(True);view.copy_summary();self.assertIn('CPT A',self.app.clipboard().text())

    def test_legacy_frozen_report_still_renders(self):
        from hindsight_ui import HindsightWidget
        f=SourceFixture();self.addCleanup(f.close);hi.reconcile(f.db)
        report=hs.calculate(he.capture(f.db,f.selection()),seconds=5).data;report.pop('build_explanation')
        view=HindsightWidget(str(f.db));self.addCleanup(view.deleteLater);view.report=Frozen.freeze(report);view._render()
        self.assertIn('older report',view.explanation.toPlainText())
