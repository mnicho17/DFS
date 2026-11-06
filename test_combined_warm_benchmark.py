"""Combined benchmark receipts must prove parity and actual fresh-audit reuse."""
from test_environment import install
install()
import copy
import unittest
from scripts.benchmark_combined_warm_build import digest, verify_profile, summarize


class CombinedWarmBenchmarkTests(unittest.TestCase):
    def records(self):
        return [dict(engine=engine, mode='warm_replay', repeat=trial,
            result_digest='identical', wall_seconds=seconds * scale,
            stages=dict(simulation_seconds=seconds * scale - 1, selection_seconds=1),
            simulation_profile={phase: {'timings': {'total_seconds': seconds * scale / 3}}
                for phase in ('screening', 'validation', 'ranking_audit')})
            for trial, seconds in enumerate((10, 30, 20))
            for engine, scale in (('before', 1), ('after', .5))]

    def profile(self, warm):
        return {phase: dict(completed_scenarios=count,
            reused_scenarios=count if warm and phase != 'ranking_audit' else 0)
            for phase, count in (('screening', 250), ('validation', 2500), ('ranking_audit', 2000))}

    def test_paired_medians_preserve_nested_times_and_inputs(self):
        records = self.records()
        original = copy.deepcopy(records)
        result = summarize(records, 3)
        self.assertEqual(result['before']['wall_seconds'], 20)
        self.assertEqual(result['after']['wall_seconds'], 10)
        self.assertEqual(result['before']['stage_seconds']['simulation_seconds'], 19)
        self.assertEqual(result['before']['simulation_phase_seconds']['ranking_audit'], 20 / 3)
        self.assertEqual(result, summarize(list(reversed(records)), 3))
        self.assertEqual(records, original)

    def test_changed_evidence_prevents_timing_summary(self):
        records = self.records()
        records[-1]['result_digest'] = 'different portfolio'
        with self.assertRaisesRegex(ValueError, 'parity'):
            summarize(records, 3)

    def test_missing_duplicate_or_unpaired_trials_are_rejected(self):
        records = self.records()
        for invalid in (records[:-1], records + [records[0]], []):
            with self.subTest(length=len(invalid)), self.assertRaises(ValueError):
                summarize(invalid, 3)

    def test_profiles_prove_cold_record_and_complete_warm_replay(self):
        verify_profile(self.profile(False), False)
        verify_profile(self.profile(True), True)
        for phase in ('screening', 'validation'):
            profile = self.profile(True)
            profile[phase]['reused_scenarios'] -= 1
            with self.assertRaises(ValueError):
                verify_profile(profile, True)
        profile = self.profile(False)
        profile['screening']['reused_scenarios'] = 250
        with self.assertRaises(ValueError):
            verify_profile(profile, False)

    def test_partial_replayed_or_missing_fresh_audit_is_rejected(self):
        for change in ({'completed_scenarios': 1999}, {'reused_scenarios': 2000},
                       {'completed_scenarios': True}, {'reused_scenarios': 0.0}):
            profile = self.profile(True)
            profile['ranking_audit'].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                verify_profile(profile, True)
        profile = self.profile(True)
        del profile['ranking_audit']
        with self.assertRaises(ValueError):
            verify_profile(profile, True)

    def test_exact_digest_preserves_float_bits_order_and_identity(self):
        self.assertNotEqual(digest([1., 2.]), digest([2., 1.]))
        self.assertNotEqual(digest(-0.), digest(0.))
        self.assertNotEqual(digest(1.0000000000000002), digest(1.))
        self.assertNotEqual(digest({'id': 'a'}), digest({'id': 'b'}))
        self.assertEqual(digest({'a': 1, 'b': 2}), digest({'b': 2, 'a': 1}))


if __name__ == '__main__':
    unittest.main()
