"""Cache lifecycle regressions run entirely in disposable folders."""
from test_environment import install
install()
import copy
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from scenario_cache_storage import Lease,make_room,APPLICATION_ID
from scenario_cache import ScenarioReplay


class CacheRecyclingTests(unittest.TestCase):
    def artifact(self,root,key,age=10,payload=12000,partial=False):
        path=root/(key+'.sqlite' if not partial else key)
        con=sqlite3.connect(path)
        with con:
            con.execute(f'PRAGMA application_id={APPLICATION_ID}')
            con.execute('CREATE TABLE frames(id INTEGER PRIMARY KEY,payload BLOB,scores BLOB)')
            con.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)')
            con.execute('INSERT INTO frames VALUES (0,zeroblob(?),?)',(payload,b'scores'))
            if not partial:
                con.executemany('INSERT INTO meta VALUES (?,?)',
                    [('key',key),('count','1'),('digest','a'*64),('scores_codec','zlib-double-v1')])
        con.close();os.utime(path,(age,age))
        return path

    def room(self,root,target=None,store=80000,limit=40000,cancelled=lambda:False):
        with patch('scenario_cache_storage.MIN_FREE',1000):
            return make_room(root,root/('f'*64+'.sqlite') if target is None else target,store,limit,cancelled)

    def test_oldest_first_only_as_much_as_needed_and_current_target_preserved(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw);current=self.artifact(root,'a'*64,age=1)
            oldest=self.artifact(root,'b'*64,age=2);recent=self.artifact(root,'c'*64,age=3)
            size=oldest.stat().st_size
            available,report=self.room(root,current,store=2*size+41000)
            self.assertFalse(oldest.exists());self.assertTrue(current.exists());self.assertTrue(recent.exists())
            self.assertEqual(report['retired_files'],1);self.assertEqual(report['reclaimed_bytes'],size)
            self.assertGreaterEqual(available,report['required_free_bytes'])

    def test_retirement_count_bound_and_verified_replay_refreshes_recency(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)
            for key in ('a','b','c'):self.artifact(root,key*64)
            with patch('scenario_cache_storage.MAX_RETIREMENTS',1):
                _,report=self.room(root,store=50000)
            self.assertEqual(report['retired_files'],1);self.assertEqual(report['status'],'maintenance bound')
        players,outcomes=self.inputs()
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw);opts=dict(kind='classic',seed=12,count=1,enabled=True,folder=raw)
            saved=ScenarioReplay(players,[[1]],**opts);saved.frame(0,lambda:(outcomes,[1.]),{});report=saved.finish(1)
            target=root/(report['cache_id']+'.sqlite');os.utime(target,(1,1))
            recent=ScenarioReplay(players,[[1]],**opts)
            self.assertEqual(recent.status,'reused');self.assertGreater(target.stat().st_mtime,1)
            recent.finish(0)
            old=self.artifact(root,'b'*64,age=2)
            used=sum(path.stat().st_size for path in root.iterdir() if path.is_file())
            self.room(root,store=used-old.stat().st_size+41000)
            self.assertFalse(old.exists());self.assertTrue(target.exists())

    def test_root_alias_is_resolved_without_allowing_links_inside_cache(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw);nested=root/'nested';nested.mkdir();alias=nested/'..'
            first=Lease(alias,'writer.lock')
            try:
                self.assertTrue(first)
                competing=Lease(root.resolve(),'writer.lock')
                self.assertFalse(competing);competing.close()
            finally:first.close()
            old=self.artifact(root,'a'*64)
            _,report=self.room(alias,store=30000,limit=20000)
            self.assertFalse(old.exists());self.assertEqual(report['retired_files'],1)
            with self.assertRaises(OSError):self.room(root,target=root.parent/'outside.sqlite',store=30000)

    def test_no_pressure_and_impossible_budget_never_remove_files(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw);old=self.artifact(root,'a'*64)
            _,report=self.room(root,store=100000)
            self.assertEqual(report['retired_files'],0);self.assertTrue(old.exists())
            _,report=self.room(root,store=0)
            self.assertEqual(report['status'],'storage bound');self.assertTrue(old.exists())

    def test_active_reader_and_legacy_sqlite_reader_preserved(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw);old=self.artifact(root,'a'*64);recent=self.artifact(root,'b'*64)
            lease=Lease(root,old.stem+'.lock')
            try:
                _,report=self.room(root,store=60000)
                self.assertTrue(old.exists());self.assertFalse(recent.exists());self.assertEqual(report['skipped_busy'],1)
            finally:lease.close()
            recent=self.artifact(root,'b'*64)
            con=sqlite3.connect(old);con.execute('BEGIN');con.execute('SELECT * FROM frames').fetchall()
            try:
                self.room(root,store=60000)
                self.assertTrue(old.exists());self.assertFalse(recent.exists())
            finally:con.close()

    def test_unknown_databases_evidence_nested_files_and_links_are_preserved(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw);unknown=root/('a'*64+'.sqlite');unknown.write_bytes(b'not a scenario cache')
            evidence=root/'history.sqlite';evidence.write_bytes(b'evidence'*2000)
            nested=root/'nested';nested.mkdir();child=self.artifact(nested,'c'*64)
            incomplete=self.artifact(root,'scenario-replay-abcdefgh.partial',partial=True)
            con=sqlite3.connect(incomplete);con.execute('PRAGMA application_id=0');con.close()
            self.room(root,store=1000)
            self.assertTrue(all(p.exists() for p in (unknown,evidence,child,incomplete)))
            # Hard links are also excluded, even if the contents match the schema.
            original=self.artifact(root,'d'*64)
            link=root/('e'*64+'.sqlite')
            try:os.link(original,link)
            except OSError:return
            self.room(root,store=30000,limit=10000)
            self.assertTrue(original.exists());self.assertTrue(link.exists())

    def test_symlink_target_outside_cache_is_never_retired(self):
        with tempfile.TemporaryDirectory() as raw:
            base=Path(raw);root=base/'cache';root.mkdir();outside=self.artifact(base,'a'*64)
            link=root/outside.name
            try:link.symlink_to(outside)
            except OSError:self.skipTest('Symlink creation unavailable')
            self.room(root,store=30000,limit=10000)
            self.assertTrue(outside.exists());self.assertTrue(link.is_symlink())

    def test_only_owned_abandoned_partials_are_retired(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)
            orphan=self.artifact(root,'scenario-replay-abcdefgh.partial',partial=True)
            unknown=self.artifact(root,'legacy.partial',partial=True)
            fresh=self.artifact(root,'scenario-replay-ijklmnop.partial',partial=True,age=time.time())
            _,report=self.room(root,store=80000)
            self.assertFalse(orphan.exists());self.assertTrue(unknown.exists());self.assertTrue(fresh.exists())
            self.assertEqual(report['retired_files'],1)

    def test_cancel_and_bounded_scan_do_not_sweep_directory(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw);old=self.artifact(root,'a'*64);other=self.artifact(root,'b'*64)
            _,report=self.room(root,store=60000,cancelled=lambda:True)
            self.assertEqual(report['status'],'cancelled');self.assertTrue(old.exists());self.assertTrue(other.exists())
            with patch('scenario_cache_storage.MAX_ENTRIES',1):
                with self.assertRaises(OSError):self.room(root,store=60000)
            self.assertTrue(old.exists());self.assertTrue(other.exists())

    def test_delete_failure_preserves_file_and_reports_no_available_room(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw);old=self.artifact(root,'a'*64)
            with patch.object(Path,'unlink',side_effect=PermissionError('locked')):
                available,report=self.room(root,store=30000,limit=20000)
            self.assertTrue(old.exists());self.assertLess(available,report['required_free_bytes'])
            self.assertEqual(report['retired_files'],0);self.assertEqual(report['skipped_busy'],1)

    def inputs(self):
        players=[{'Name':'A','ID':'1','Team':'A'}]
        from nfl_simulation import player_key
        return players,{player_key(players[0]):2.5}

    def test_writer_reservation_and_same_key_access_are_nonblocking(self):
        players,outcomes=self.inputs()
        with tempfile.TemporaryDirectory() as raw:
            opts=dict(kind='classic',count=1,enabled=True,folder=raw)
            first=ScenarioReplay(players,[[1]],seed=1,**opts)
            self.assertEqual(first.status,'recording')
            same=ScenarioReplay(players,[[1]],seed=1,**opts)
            other=ScenarioReplay(players,[[1]],seed=2,**opts)
            self.assertEqual(same.status,'not saved');self.assertIn('in use',same.reason)
            self.assertEqual(other.status,'not saved');self.assertIn('continued',other.reason)
            self.assertEqual(other.frame(0,lambda:(outcomes,[1.]),{}),(outcomes,[1.]))
            same.close();other.close();first.close()
            resumed=ScenarioReplay(players,[[1]],seed=2,**opts)
            self.assertEqual(resumed.status,'recording');resumed.close()

    def test_process_lock_released_on_abrupt_exit(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw);marker=root/'ready'
            code="from scenario_cache_storage import Lease;from pathlib import Path;import sys,time; lease=Lease(Path(sys.argv[1]),'writer.lock');Path(sys.argv[2]).write_text(str(bool(lease)));time.sleep(30)"
            child=subprocess.Popen([sys.executable,'-c',code,raw,str(marker)],cwd=str(Path(__file__).resolve().parent))
            try:
                until=time.monotonic()+5
                while not marker.exists() and child.poll() is None and time.monotonic()<until:time.sleep(.02)
                self.assertTrue(marker.exists());self.assertEqual(marker.read_text(),'True')
                blocked=Lease(root,'writer.lock');self.assertFalse(blocked);blocked.close()
            finally:child.kill();child.wait(timeout=5)
            released=Lease(root,'writer.lock')
            try:self.assertTrue(released)
            finally:released.close()

    def test_publication_rechecks_storage_and_abort_releases_leases(self):
        players,outcomes=self.inputs()
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw);opts=dict(kind='classic',seed=4,count=1,enabled=True,folder=raw)
            replay=ScenarioReplay(players,[[1]],**opts);replay.frame(0,lambda:(outcomes,[1.]),{})
            (root/'unknown').write_bytes(b'x'*50000)
            with patch('scenario_cache.STORE_LIMIT',40000):report=replay.finish(1)
            self.assertEqual(report['status'],'not saved');self.assertTrue((root/'unknown').exists())
            self.assertFalse(list(root.glob('*.partial')));self.assertFalse(list(root.glob('*.sqlite')))
            again=ScenarioReplay(players,[[1]],**opts);self.assertEqual(again.status,'recording');again.close()

    def test_recycling_then_exact_replay_preserves_metrics_in_both_formats(self):
        from test_scenario_cache import ScenarioCacheTests,result_values
        for kind,players,rows,sim,module in ScenarioCacheTests().fixtures():
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as raw,patch.dict(os.environ,{'DFS_OPTIMIZER_DATA_DIR':raw}):
                root=Path(raw)/'scenario-cache';root.mkdir()
                old=[self.artifact(root,key*64,payload=80000) for key in ('a','b','c')]
                opts=dict(scenarios=6,field_lineup_count=20,seed=14)
                baseline=copy.deepcopy(result_values(sim(rows,players,**opts)))
                with patch('scenario_cache.STORE_LIMIT',350000),patch('scenario_cache.FILE_LIMIT',120000),patch('scenario_cache_storage.MIN_FREE',1000):
                    cold=sim(rows,players,scenario_cache=True,**opts)
                    self.assertEqual(cold['report']['scenario_cache']['status'],'saved')
                    self.assertEqual(cold['report']['scenario_cache']['maintenance']['retired_files'],1)
                    self.assertFalse(all(path.exists() for path in old))
                    self.assertEqual(result_values(cold),baseline)
                    with patch(module+'._scenario_outcomes',side_effect=AssertionError('exact replay required')):
                        warm=sim(rows,players,scenario_cache=True,**opts)
                    self.assertEqual(result_values(warm),baseline)
                    self.assertEqual(warm['report']['scenario_cache']['reused_scenarios'],6)

    def test_maintenance_receipt_is_captured_saved_and_copied_without_paths(self):
        from build_diagnostics import create_build_diagnostic,format_build_report,save_build_diagnostic,load_build_history
        receipt=dict(status='space reclaimed',retired_files=2,reclaimed_bytes=32*1024**2,
                     available_bytes=600*1024**2,store_limit_bytes=2*1024**3,skipped_busy=1)
        record=create_build_diagnostic(context={},timing_report={},sim_report=dict(volatility_model='current',
            scenario_cache=dict(status='saved',maintenance=receipt)))
        receipt['retired_files']=99
        text=format_build_report(record)
        self.assertIn('2 old artifacts retired; 32.0 MiB reclaimed; 1 busy artifacts preserved',text)
        with tempfile.TemporaryDirectory() as raw:
            path=str(Path(raw)/'history.json');save_build_diagnostic(record,path=path)
            self.assertEqual(format_build_report(load_build_history(path=path)[0]),text)
        record['sim']['scenario_cache']['maintenance']['status']='C:/private/history'
        self.assertNotIn('private',format_build_report(record))

    def test_unknown_full_store_and_maintenance_error_do_not_change_scoring(self):
        from showdown_simulation import simulate_showdown
        from test_showdown_performance import _showdown_players
        from optimizers import ShowdownOptimizer
        from test_scenario_cache import result_values
        players=_showdown_players();rows=ShowdownOptimizer(players).build_lineups(2)
        opts=dict(scenarios=4,field_lineup_count=20)
        baseline=copy.deepcopy(result_values(simulate_showdown(rows,players,**opts)))
        with tempfile.TemporaryDirectory() as raw,patch.dict(os.environ,{'DFS_OPTIMIZER_DATA_DIR':raw}):
            root=Path(raw)/'scenario-cache';root.mkdir();evidence=root/'history.sqlite';evidence.write_bytes(b'preserve'*10000)
            with patch('scenario_cache.STORE_LIMIT',50000):
                run=simulate_showdown(rows,players,scenario_cache=True,**opts)
            self.assertEqual(run['report']['scenario_cache']['status'],'not saved');self.assertEqual(result_values(run),baseline)
            self.assertTrue(evidence.exists())
            with patch('scenario_cache_storage.make_room',side_effect=OSError('denied')):
                run=simulate_showdown(rows,players,scenario_cache=True,**opts)
            self.assertEqual(run['report']['scenario_cache']['status'],'unavailable');self.assertEqual(result_values(run),baseline)


if __name__=='__main__':unittest.main()
