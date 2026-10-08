import test_showdown_screening as fixtures
from test_showdown_library import signatures
from showdown_full_screening import prepare
from showdown_screening import load_screening
from showdown_library import iter_candidates
from unittest.mock import patch
import unittest
from contextlib import closing

class FullScreeningTests(unittest.TestCase):
    setUp=fixtures.ScreeningTests.setUp
    def test_timestamp_refresh_resumes_full_stream_and_reuses_completed_scores(self):
        original = fixtures.ScreeningTests.timed_players(self, '2026-10-08T10:00:00Z')
        current = fixtures.ScreeningTests.timed_players(self, '2026-10-08T11:00:00Z')
        stop = [False]
        first = prepare(self.library, original, batch_size=23, cancelled=lambda:stop[0],
            progress=lambda _:stop.__setitem__(0, True), **self.kw)
        self.assertEqual(first['screened'], 23)
        resumed = prepare(self.library, current, batch_size=23, **self.kw)
        saved, _ = load_screening(self.library, current, **self.kw)
        complete = prepare(self.library, current, batch_size=23, **dict(self.kw, folder=self.root/'other'))
        fresh, _ = load_screening(self.library, current, **dict(self.kw, folder=self.root/'other'))
        self.assertEqual(resumed['screened'], complete['screened'])
        self.assertEqual([r.sim_metrics for r in saved], [r.sim_metrics for r in fresh])
        self.assertEqual(signatures(saved), signatures(fresh))
        with patch('showdown_simulation.simulate_showdown', side_effect=AssertionError('rescored')):
            self.assertTrue(prepare(self.library, current, **self.kw)['screening_complete'])

    def test_reused_full_scores_have_fresh_construction_metadata(self):
        from optimizers import attach_showdown_metrics,ShowdownLineup
        from showdown_simulation import simulate_showdown
        prepare(self.library,self.players,**self.kw)
        rows,_=load_screening(self.library,self.players,**self.kw)
        for row in rows:
            fresh=attach_showdown_metrics([ShowdownLineup(row['Captain'],row['Flex'])],50000)[0]
            for key in ('duplicate_risk','sim_leverage','showdown_projection','showdown_correlation_flags',
                        'showdown_cpt_ownership','showdown_total_ownership','candidate_archetype'):
                self.assertEqual(row.sim_metrics[key],fresh.sim_metrics[key])
            self.assertEqual(row.candidate_archetype,fresh.candidate_archetype)
            self.assertTrue(row.sim_metrics['showdown_correlation_flags'])
            self.assertGreater(row.sim_metrics['duplicate_risk'],0)
        validated=simulate_showdown(rows,self.players,salary_cap=50000,**self.kw['screening'])['lineups']
        for old,new in zip(rows,validated):
            self.assertEqual(old.sim_metrics['duplicate_risk'],new.sim_metrics['duplicate_risk'])
            self.assertEqual(old.sim_metrics['showdown_correlation_flags'],new.sim_metrics['showdown_correlation_flags'])
            self.assertEqual(old.sim_top_hits,new.sim_top_hits)

    def test_reused_construction_uses_the_requested_salary_cap(self):
        kw=dict(self.kw,salary_cap=46000)
        prepare(self.library,self.players,**kw)
        rows,_=load_screening(self.library,self.players,**kw)
        self.assertTrue(rows)
        for row in rows:
            salary=float(row['Captain']['CptSalary'])+sum(float(p['FlexSalary']) for p in row['Flex'])
            self.assertEqual(row.sim_metrics['showdown_salary_left'],46000-salary)

    def test_full_stream_covers_tail_and_reuses_bounded_leaders(self):
        expected=list(iter_candidates(self.library,self.players,salary_cap=50000,rules={}))
        result=prepare(self.library,self.players,batch_size=23,**self.kw)
        self.assertTrue(result['screening_complete'])
        self.assertEqual(result['screened'],len(expected))
        rows,report=load_screening(self.library,self.players,**self.kw)
        self.assertLessEqual(len(rows),16)
        self.assertEqual(report['full_screened'],len(expected))
        with patch('showdown_simulation.simulate_showdown',side_effect=AssertionError('rescored')):
            self.assertTrue(prepare(self.library,self.players,**self.kw)['screening_complete'])

    def test_full_cancel_resume_matches_uninterrupted(self):
        stop=[False]
        first=prepare(self.library,self.players,batch_size=23,cancelled=lambda:stop[0],
            progress=lambda _:stop.__setitem__(0,True),**self.kw)
        self.assertEqual(first['screened'],23)
        diagnostic={}
        self.assertIsNone(load_screening(self.library,self.players,diagnostic=diagnostic,**self.kw))
        self.assertIn('partial',diagnostic['reason'])
        resumed=prepare(self.library,self.players,batch_size=23,**self.kw)
        saved,_=load_screening(self.library,self.players,**self.kw)
        other=dict(self.kw,folder=self.root/'other')
        complete=prepare(self.library,self.players,batch_size=23,**other)
        fresh,_=load_screening(self.library,self.players,**other)
        self.assertEqual(resumed['screened'],complete['screened'])
        self.assertEqual([r.sim_metrics for r in saved],[r.sim_metrics for r in fresh])
        self.assertEqual(signatures(saved),signatures(fresh))

    def test_full_partial_sim_does_not_advance_cursor(self):
        with patch('showdown_simulation.simulate_showdown',return_value=dict(lineups=[],report=dict(scenarios=10))):
            result=prepare(self.library,self.players,**self.kw)
        self.assertEqual(result['screened'],0)
        self.assertFalse(result['screening_complete'])
        self.assertIsNone(load_screening(self.library,self.players,**self.kw))

    def test_partitioned_full_screen_resume_crosses_captains(self):
        from showdown_full_library import prepare as prepare_library
        library=self.root/'full.sdfull'
        prepare_library(library,self.players)
        stop=[False]
        first=prepare(library,self.players,batch_size=23,cancelled=lambda:stop[0],
            progress=lambda _:stop.__setitem__(0,True),**self.kw)
        self.assertFalse(first['screening_complete'])
        result=prepare(library,self.players,batch_size=23,**self.kw)
        self.assertEqual(result['screened'],len(list(iter_candidates(library,self.players))))
        rows,report=load_screening(library,self.players,**self.kw)
        self.assertEqual(report['full_screened'],result['screened'])
        self.assertLessEqual(len(rows),16)

    def test_full_cache_rejects_changed_inputs_and_checksum(self):
        import copy,json,sqlite3
        from showdown_full_screening import _location
        from showdown_screening import target
        prepare(self.library,self.players,**self.kw)
        changed=copy.deepcopy(self.players);changed[0]['FlexProjection']=99
        self.assertIsNone(load_screening(self.library,changed,**self.kw))
        path,context=target(self.library,self.players,**self.kw)
        path,_=_location(path,context)
        with closing(sqlite3.connect(path)) as con:
            con.execute("UPDATE screen_progress SET payload='{}'");con.commit()
        with self.assertRaisesRegex(ValueError,'damaged'):
            load_screening(self.library,self.players,**self.kw)

if __name__=='__main__':unittest.main()
