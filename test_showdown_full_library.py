from test_environment import install
install()
from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from showdown_full_library import prepare,status,partition_path
from showdown_library import prepare as single_prepare,iter_candidates,load_bounded,validate_library,is_prepared_library
from test_showdown_library import players,signatures


class FullLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'full.sdfull';self.players=players()

    def test_all_captains_match_independent_single_library_despite_locks(self):
        self.players[0]['LockCpt']=True
        result=prepare(self.path,self.players)
        self.assertTrue(result['complete']);self.assertEqual(result['partition_count'],8)
        self.assertEqual(result['checked'],168)
        self.players[0]['LockCpt']=False
        mono=self.path.parent/'mono.sdlib';single_prepare(mono,self.players)
        self.assertEqual(signatures(iter_candidates(self.path,self.players)),signatures(iter_candidates(mono,self.players)))
        self.assertTrue(is_prepared_library(self.path))
        rows,report=load_bounded(self.path,self.players,limit=16,salary_strategy='Flexible')
        self.assertEqual(len(rows),16);self.assertTrue(report['scan_complete'])

    def test_cancel_resume_preserves_completed_partition_and_input_order(self):
        stop=[False]
        result=prepare(self.path,self.players,cancelled=lambda:stop[0],progress=lambda p:stop.__setitem__(0,True))
        self.assertFalse(result['complete']);self.assertTrue(result['cancelled'])
        before={p.name:p.read_bytes() for p in Path(str(self.path)+'.parts').glob('*.sdlib')}
        with self.assertRaisesRegex(ValueError,'incomplete'):list(iter_candidates(self.path,self.players))
        final=prepare(self.path,list(reversed(self.players)))
        self.assertTrue(final['complete'])
        for name,content in before.items():self.assertEqual((Path(str(self.path)+'.parts')/name).read_bytes(),content)

    def test_storage_budget_pauses_and_can_be_raised_without_coverage_claim(self):
        result=prepare(self.path,self.players,max_bytes=1024)
        self.assertFalse(result['complete']);self.assertEqual(result['pause_reason'],'storage budget')
        self.assertTrue(prepare(self.path,self.players)['complete'])

    def test_changed_identity_missing_partition_and_mixed_partition_rejected(self):
        prepare(self.path,self.players)
        changed=copy.deepcopy(self.players);changed[0]['FlexSalary']+=100
        with self.assertRaises(ValueError):validate_library(self.path,changed)
        from portfolio_rules import player_key
        part=partition_path(self.path,player_key(self.players[0]))
        with closing(sqlite3.connect(part)) as con:
            data=json.loads(con.execute('SELECT payload FROM preparation').fetchone()[0])
            data['structure']['captains']=[player_key(self.players[1])]
            from build_snapshots import fingerprint
            data['identity']=fingerprint(data['structure'])
            with con:con.execute('UPDATE preparation SET payload=?',(json.dumps(data),))
        with self.assertRaisesRegex(ValueError,'different inputs'):status(self.path)

    def test_missing_partition_cannot_remain_complete(self):
        prepare(self.path,self.players)
        from portfolio_rules import player_key
        partition_path(self.path,player_key(self.players[0])).unlink()
        self.assertFalse(status(self.path)['complete'])
        with self.assertRaisesRegex(ValueError,'incomplete'):validate_library(self.path,self.players)

    def test_manifest_corruption_and_unrelated_database_are_not_overwritten(self):
        with closing(sqlite3.connect(self.path)) as con:
            with con:con.execute('CREATE TABLE unrelated(id INTEGER)')
        before=self.path.read_bytes()
        with self.assertRaises(ValueError):prepare(self.path,self.players)
        self.assertEqual(self.path.read_bytes(),before)
