from test_environment import install
install()
import copy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from showdown_library import prepare
from showdown_screening import prepare_screening,load_screening,settings,target,OBSERVATION_TIMES
from showdown_simulation import simulate_showdown
from test_showdown_library import players


class ScreeningTests(unittest.TestCase):
    def timed_players(self, stamp):
        rows = copy.deepcopy(self.players)
        for player in rows:
            for key in OBSERVATION_TIMES: player[key] = stamp
            player['NFLUsageHistory'] = dict(version=1, checked_at=stamp, current={'games':4})
        return rows

    def test_observation_refresh_reuses_scores_and_preserves_raw_evidence(self):
        original = self.timed_players('2026-10-08T10:00:00Z')
        current = self.timed_players('2026-10-08T11:00:00Z')
        before = copy.deepcopy(current)
        prepare_screening(self.library, original, **self.kw)
        rows, report = load_screening(self.library, current, **self.kw)
        self.assertEqual(report['screening_reused'], 16)
        self.assertEqual(rows[0]['Captain']['LiveStatusUpdatedAt'], '2026-10-08T11:00:00Z')
        fresh = simulate_showdown(rows, current, salary_cap=50000, **self.kw['screening'])['lineups']
        for saved, scored in zip(rows, fresh):
            self.assertEqual(saved.sim_metrics['sim_mean'], scored.sim_metrics['sim_mean'])
            self.assertEqual(saved.sim_top_hits, scored.sim_top_hits)
            self.assertEqual(saved.sim_scenario_values, scored.sim_scenario_values)
        with patch('showdown_simulation.simulate_showdown', side_effect=AssertionError('no rescoring')):
            self.assertTrue(prepare_screening(self.library, current, **self.kw)['screening_complete'])
        self.assertEqual(current, before)
        self.assertEqual(target(self.library, original, **self.kw), target(self.library, current, **self.kw))
        from build_snapshots import create_snapshot
        recipe = dict(sport='NFL', contest_kind='showdown')
        self.assertNotEqual(create_snapshot(original, recipe, {})['input_id'],
                            create_snapshot(current, recipe, {})['input_id'])

    def test_refresh_does_not_hide_status_roles_history_or_unknown_fields(self):
        original = self.timed_players('2026-10-08T10:00:00Z')
        prepare_screening(self.library, original, **self.kw)
        for field, value in [('InjuryStatus','OUT'), ('NFLDepthOrder',2),
                ('NFLQBEligible',False), ('FadeCpt',True), ('LockFlex',True),
                ('FlexProjection',99), ('FlexOwnership',99), ('NFLNewsScore',2),
                ('NFLVegasGameTotal',80), ('UnknownTimestamp','new')]:
            changed = self.timed_players('2026-10-08T11:00:00Z'); changed[0][field] = value
            with self.subTest(field=field):
                self.assertIsNone(load_screening(self.library, changed, **self.kw))
        changed = self.timed_players('2026-10-08T11:00:00Z')
        changed[0]['NFLUsageHistory']['current']['games'] = 5
        self.assertIsNone(load_screening(self.library, changed, **self.kw))

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.library=self.root/'rosters.sdlib'
        self.players=players()
        for i,p in enumerate(self.players):
            p['FlexSalary']=6500+i*200;p['CptSalary']=p['FlexSalary']*1.5
        prepare(self.library,self.players)
        self.kw=dict(limit=16,salary_cap=50000,salary_strategy='Flexible',rules={},
            screening=dict(scenarios=250,field_lineup_count=20,seed=73129),folder=self.root/'cache')

    def test_complete_batches_reuse_exact_scores_and_global_ranks(self):
        result=prepare_screening(self.library,self.players,batch_size=5,**self.kw)
        self.assertTrue(result['screening_complete'])
        rows,report=load_screening(self.library,self.players,**self.kw)
        fresh=simulate_showdown(rows,self.players,salary_cap=50000,**self.kw['screening'])['lineups']
        for saved,current in zip(rows,fresh):
            self.assertEqual(saved.sim_metrics['sim_top_one_pct'],current.sim_metrics['sim_top_one_pct'])
            self.assertEqual(saved.sim_metrics['sim_edge'],current.sim_metrics['sim_edge'])
            self.assertEqual(saved.sim_top_hits,current.sim_top_hits)
            self.assertEqual(saved.sim_scenario_values,current.sim_scenario_values)
        self.assertEqual(report['screening_reused'],16)
        with patch('showdown_simulation.simulate_showdown',side_effect=AssertionError('must resume without rescoring')):
            self.assertTrue(prepare_screening(self.library,self.players,**self.kw)['screening_complete'])

    def test_cancel_checkpoint_resumes_without_releasing_incomplete_bank(self):
        stop=[False]
        first=prepare_screening(self.library,self.players,batch_size=5,cancelled=lambda:stop[0],
            progress=lambda _:stop.__setitem__(0,True),**self.kw)
        self.assertEqual(first['screened'],5);self.assertFalse(first['screening_complete'])
        self.assertIsNone(load_screening(self.library,self.players,**self.kw))
        result=prepare_screening(self.library,self.players,batch_size=5,**self.kw)
        self.assertTrue(result['screening_complete'])

    def test_changed_forecasts_ownership_settings_rules_and_code_do_not_reuse(self):
        prepare_screening(self.library,self.players,**self.kw)
        for field in ('FlexProjection','FlexOwnership'):
            changed=copy.deepcopy(self.players);changed[0][field]=99
            self.assertIsNone(load_screening(self.library,changed,**self.kw))
        for key,value in [('rules',{'min_unique':3}),('salary_strategy','Near Cap'),
                          ('screening',dict(scenarios=500,field_lineup_count=20,seed=73129))]:
            self.assertIsNone(load_screening(self.library,self.players,**dict(self.kw,**{key:value})))
        with patch('showdown_screening.code_id',return_value='changed-model'):
            self.assertIsNone(load_screening(self.library,self.players,**self.kw))

    def test_corrupt_score_and_cancelled_loading_are_rejected(self):
        prepare_screening(self.library,self.players,**self.kw)
        with self.assertRaises(InterruptedError):
            load_screening(self.library,self.players,cancelled=lambda:True,**self.kw)
        path,_=target(self.library,self.players,**self.kw)
        with closing(sqlite3.connect(path)) as con:
            with con:con.execute("UPDATE scores SET payload='{}' WHERE id=0")
        with self.assertRaisesRegex(ValueError,'damaged'):
            load_screening(self.library,self.players,**self.kw)

    def test_partial_sim_batch_never_becomes_saved_evidence(self):
        with patch('showdown_simulation.simulate_showdown',return_value=dict(lineups=[],report=dict(scenarios=10))):
            result=prepare_screening(self.library,self.players,**self.kw)
        self.assertEqual(result['screened'],0)
        self.assertIsNone(load_screening(self.library,self.players,**self.kw))

    def test_player_iteration_order_is_preserved_and_part_of_seeded_identity(self):
        reversed_players=list(reversed(self.players))
        prepare_screening(self.library,reversed_players,**self.kw)
        rows,_=load_screening(self.library,reversed_players,**self.kw)
        fresh=simulate_showdown(rows,reversed_players,salary_cap=50000,**self.kw['screening'])['lineups']
        self.assertEqual([r.sim_metrics['sim_mean'] for r in rows],[r.sim_metrics['sim_mean'] for r in fresh])
        self.assertIsNone(load_screening(self.library,self.players,**self.kw))
