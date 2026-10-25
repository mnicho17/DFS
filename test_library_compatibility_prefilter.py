"""Stale metadata cannot consume full-load attempts or authorize candidate reuse."""
from test_environment import install
install()
import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from build_preparation import discover_libraries
from candidate_library import run_search,load_candidates,CandidateCodeMismatch,_candidate_code_compatibility,CLASSIC_ROSTER_FORMAT
from build_snapshots import create_snapshot
from optimizers import ShowdownOptimizer,ShowdownLineup
from test_showdown_library import players

class CompatibilityPrefilterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.players=players()
        for name,value in (('code_id','current-code'),('preparation_model_id','current-model')):
            mock=patch('candidate_library.'+name,return_value=value);mock.start();self.addCleanup(mock.stop)
    def library(self,name,old=False,invalid=False):
        path=self.root/name
        snapshot=create_snapshot(self.players,dict(sport='NFL',contest_kind='showdown',salary_strategy='Flexible'),{})
        row=ShowdownLineup(self.players[0],self.players[1:6])
        with patch.object(ShowdownOptimizer,'build_lineups',return_value=[row]):run_search(path,snapshot,candidate_limit=1,compatibility=True)
        with sqlite3.connect(path) as con:
            if old:con.execute("UPDATE library_meta SET value='old-model' WHERE key='generation_model_id'")
            if invalid:con.execute("UPDATE candidates SET roster='[]'")
        con.close();return path
    def worker(self,paths):
        from main_window import LineupBuildWorker
        return LineupBuildWorker(copy.deepcopy(self.players),kind='showdown',num_lineups=1,salary_cap=50000,
            salary_strategy='Flexible',sim_enabled=True,compute_mode='Deep',automatic_libraries=tuple(map(str,paths)))
    def test_three_stale_headers_do_not_hide_fourth_usable_library(self):
        from build_workflow import prepare_build
        paths=[self.library(f'old-{i}.dfslib',old=True) for i in range(3)]+[self.library('valid.dfslib')]
        before={p:p.read_bytes() for p in paths};worker=self.worker(paths)
        with patch.object(worker,'_load_candidate_library',wraps=worker._load_candidate_library) as loader:
            self.assertTrue(prepare_build(worker))
        self.assertEqual(loader.call_count,1);self.assertEqual(worker.candidate_library,str(paths[-1].resolve()))
        self.assertTrue(worker.library_candidates)
        audit=worker.preparation.rows[2]['discovery']
        self.assertEqual(audit['rejected_model'],3);self.assertEqual(audit['accepted'],1)
        self.assertNotIn('load_attempt_limit',audit)
        for p in paths:self.assertEqual(p.read_bytes(),before[p])
    def test_matching_headers_still_obey_three_full_load_attempts(self):
        from build_workflow import prepare_build
        paths=[self.library(f'invalid-{i}.dfslib',invalid=True) for i in range(3)]+[self.library('valid.dfslib')]
        worker=self.worker(paths)
        with patch.object(worker,'_load_candidate_library',wraps=worker._load_candidate_library) as loader:
            self.assertTrue(prepare_build(worker))
        self.assertEqual(loader.call_count,3);self.assertFalse(worker.candidate_library)
        self.assertEqual(worker.preparation.rows[2]['discovery']['load_attempt_limit'],1)
    def test_loader_rechecks_model_if_file_changes_after_discovery(self):
        path=self.library('valid.dfslib')
        self.assertEqual(list(discover_libraries((str(path),),'',self.players,'showdown',50000)),[str(path.resolve())])
        with sqlite3.connect(path) as con:con.execute("UPDATE library_meta SET value='changed-model' WHERE key='generation_model_id'")
        con.close()
        with self.assertRaises(CandidateCodeMismatch):load_candidates(path,self.players,kind='showdown',salary_cap=50000)
    def test_shared_gate_preserves_legacy_model_and_portable_classic_policy(self):
        base=dict(snapshot={'inputs':{'recipe':{'contest_kind':'showdown'}}},code_id='current-code')
        self.assertEqual(_candidate_code_compatibility(base,'showdown'),('current-code',False))
        with self.assertRaises(CandidateCodeMismatch):_candidate_code_compatibility(dict(base,code_id='old-code'),'showdown')
        with self.assertRaises(CandidateCodeMismatch):_candidate_code_compatibility(dict(base,generation_model_id='old-model'),'showdown')
        self.assertEqual(_candidate_code_compatibility(dict(base,code_id='old-code',generation_model_id='current-model'),'showdown'),('current-code',True))
        portable=dict(base,snapshot={'inputs':{'recipe':{'contest_kind':'classic'}}},code_id='old-code',generation_model_id='old-model',roster_format=CLASSIC_ROSTER_FORMAT)
        self.assertEqual(_candidate_code_compatibility(portable,'classic'),('current-code',False))
        with self.assertRaisesRegex(ValueError,'Unsupported candidate roster format'):_candidate_code_compatibility(portable,'showdown')

if __name__=='__main__':unittest.main()
