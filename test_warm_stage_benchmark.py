from test_environment import install
install()
import copy,unittest
from pathlib import Path
from scripts.benchmark_reuse_stages import summarize,run,verify_profiles


class WarmStageBenchmarkTests(unittest.TestCase):
    def observations(self):
        return [dict(repeat=trial,mode=mode,result_digest='same',wall_seconds=seconds,
            stages={'simulation_seconds':seconds-1,'selection_seconds':1},
            simulation_profile={'ranking_audit':{'timings':{'total_seconds':seconds/2}}})
            for trial,seconds in enumerate((10,30,20)) for mode in ('fresh','cold_record','warm_replay')]
    def test_medians_use_trials_and_keep_nested_phase_times_separate(self):
        rows=self.observations();before=copy.deepcopy(rows);summary=summarize(rows)
        self.assertEqual(summary['warm_replay']['wall_seconds'],20)
        self.assertEqual(summary['warm_replay']['stage_seconds']['simulation_seconds'],19)
        self.assertEqual(summary['warm_replay']['simulation_phase_seconds']['ranking_audit'],10)
        self.assertEqual(rows,before)
        self.assertEqual(summary,summarize(list(reversed(rows))))
    def test_incomplete_or_duplicate_trials_cannot_produce_a_summary(self):
        rows=self.observations()
        for bad in ([],rows[:-1],rows+[rows[0]]):
            with self.assertRaises(ValueError):summarize(bad)
    def test_changed_scoring_or_selection_digest_prevents_timing_claim(self):
        rows=self.observations();rows[-1]['result_digest']='different'
        with self.assertRaisesRegex(ValueError,'parity'):summarize(rows)
    def test_bounds_fail_before_generating_or_accessing_caches(self):
        for kwargs in (dict(repeats=0),dict(repeats=True),dict(candidates=2001),dict(field=10001),dict(scenarios=0),dict(lineups=151),dict(lineups=21,shortlist=20),dict(candidates=19),dict(shortlist=19)):
            with self.assertRaises(ValueError):run('showdown',Path('unused'),**kwargs)

    def test_warm_replay_requires_actual_reuse_but_audit_remains_fresh(self):
        profile={key:dict(completed_scenarios=10,reused_scenarios=0) for key in ('screening','validation','ranking_audit')}
        verify_profiles(profile,'fresh');verify_profiles(profile,'cold_record')
        with self.assertRaisesRegex(ValueError,'reuse'):verify_profiles(profile,'warm_replay')
        for key in ('screening','validation'):profile[key]['reused_scenarios']=10
        verify_profiles(profile,'warm_replay')
        profile['ranking_audit']['reused_scenarios']=1
        with self.assertRaisesRegex(ValueError,'reuse'):verify_profiles(profile,'warm_replay')
    def test_missing_scenario_evidence_cannot_qualify_a_trial(self):
        with self.assertRaisesRegex(ValueError,'Incomplete'):verify_profiles({},'fresh')
