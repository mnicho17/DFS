from test_environment import install
install()
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from automatic_compute import CAPS, select_profile, plan, apply, summary
from compute_settings import DEEP_PROFILES
from preparation_audit import capture_build_audit, build_audit_lines


class AutomaticComputeTests(unittest.TestCase):
    def test_every_tier_stays_inside_explicit_cap(self):
        for cap in CAPS:
            for count in (1, 3, 20, 21, 150, 151, 1000):
                for prepared in (False, True):
                    d=select_profile(count, cap, prepared)
                    self.assertLessEqual(DEEP_PROFILES[d['profile']]['minutes'], cap)
                    self.assertEqual(d['minutes'], DEEP_PROFILES[d['profile']]['minutes'])

    def test_count_boundaries(self):
        self.assertEqual([select_profile(n,60)['minutes'] for n in (1,20,21,150,151)], [5,5,15,15,20])

    def test_recorded_preparation_selects_baseline_without_claiming_reuse(self):
        d=select_profile(150,30,True)
        self.assertEqual(d['minutes'],5)
        self.assertIn('files still require validation',d['reason'])

    def test_invalid_caps_and_counts_rejected(self):
        for cap in (None,0,1,10,61,True,'15'):
            with self.assertRaises(ValueError):select_profile(150,cap)
        for count in (0,-1,True,'150',1.5):
            with self.assertRaises(ValueError):select_profile(count,15)

    def test_replay_manual_and_other_sports_do_not_plan(self):
        for cap,replay,sport in ((None,False,'NFL'),(15,True,'NFL'),(15,False,'MLB')):
            w=SimpleNamespace(_automatic_compute_cap=cap,_snapshot_replay=replay,_current_sport=lambda:sport)
            self.assertIsNone(plan(w,150))

    def test_changed_partial_or_empty_evidence_does_not_count_as_ready(self):
        w=SimpleNamespace(_automatic_compute_cap=30,_current_sport=lambda:'NFL')
        for view in ({},dict(match='Matches captured inputs',record=dict(complete=True,saved=10)),
                     dict(match='Roster inputs match; screening inputs changed',record=dict(complete=True,screening_complete=True,saved=10)),
                     dict(match='Matches captured inputs',record=dict(complete=True,screening_complete=True,saved=0))):
            with patch('preparation_readiness.current_view',return_value=view):
                self.assertFalse(plan(w,150)['prepared'])

    def test_matching_evidence_is_used_only_as_planning_hint(self):
        w=SimpleNamespace(_automatic_compute_cap=30,_current_sport=lambda:'NFL')
        view=dict(match='Matches captured inputs',record=dict(complete=True,screening_complete=True,saved=10))
        with patch('preparation_readiness.current_view',return_value=view):self.assertTrue(plan(w,150)['prepared'])

    def test_repair_bypasses_planning(self):
        self.assertIsNone(apply(object(),150,repair=True))

    def test_report_captures_decision_and_ignores_later_changes(self):
        w=SimpleNamespace()
        decision=select_profile(150,15)
        audit=capture_build_audit(w,decision)
        before=copy.deepcopy(audit)
        decision['cap']=60
        self.assertEqual(audit,before)
        self.assertIn('chosen maximum 15 min',' '.join(build_audit_lines(audit)))

    def test_report_rejects_forged_profile_or_reason(self):
        d=select_profile(150,15);d['reason']='unvalidated source path'
        audit=capture_build_audit(SimpleNamespace(),d)
        self.assertEqual(audit['automatic_compute'],{})
        self.assertNotIn('unvalidated source path',' '.join(build_audit_lines(audit)))
