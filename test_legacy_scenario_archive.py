from test_environment import install
install()
from array import array
import hashlib
import json
from pathlib import Path
import sqlite3
import struct
import tempfile
import time
import unittest
import zlib
from legacy_scenario_archive import inspect_legacy,validate_and_archive,restore,Stopped,publish_file
from scenario_cache_storage import recognized,Lease


def legacy(root,scores=None,payload=None):
    path=root/('a'*64+'.sqlite')
    payload=payload or zlib.compress(json.dumps([{'p':1.5},{'normal':1}]).encode())
    scores=scores if scores is not None else array('d',[1.,2.,3.]).tobytes()
    digest=hashlib.sha256(struct.pack('<QQ',len(payload),len(scores))+payload+scores).hexdigest()
    con=sqlite3.connect(path)
    with con:
        con.execute('CREATE TABLE frames(id INTEGER PRIMARY KEY,payload BLOB,scores BLOB)')
        con.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)')
        con.execute('INSERT INTO frames VALUES (0,?,?)',(payload,scores))
        con.executemany('INSERT INTO meta VALUES (?,?)',[('key',path.stem),('count','1'),('digest',digest)])
    con.close();return path


class LegacyArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.cache=self.root/'cache';self.backup=self.root/'backup';self.out=self.root/'restore'
        for p in (self.cache,self.backup,self.out):p.mkdir()
    def archive(self,**kw):return validate_and_archive(self.cache,self.backup,**kw)
    def test_verified_archive_restores_exact_bytes_and_never_authorizes_cleanup(self):
        path=legacy(self.cache);before=path.read_bytes();mtime=path.stat().st_mtime_ns
        result=self.archive();self.assertEqual(result['archived'],1);self.assertEqual(path.read_bytes(),before)
        self.assertEqual(path.stat().st_mtime_ns,mtime);self.assertFalse(recognized(path))
        manifest=next(self.backup.rglob('manifests/*.json'))
        restored=Path(restore(manifest,self.out));self.assertEqual(restored.read_bytes(),before)
        self.assertEqual(len(list(self.backup.rglob('*.sqlite'))),1)
        self.assertEqual(self.archive()['archived'],1)
    def test_digest_mismatch_preserves_source_without_archive(self):
        path=legacy(self.cache);con=sqlite3.connect(path)
        with con:con.execute("UPDATE meta SET value=? WHERE key='digest'",('0'*64,))
        con.close();before=path.read_bytes();r=self.archive()
        self.assertEqual(r['errors'],1);self.assertEqual(path.read_bytes(),before);self.assertFalse(list(self.backup.rglob('*.sqlite')))
    def test_invalid_frames_are_rejected_even_with_matching_digest(self):
        cases=[(b'bad',None),(zlib.compress(b'[{},{}]'),None),
               (zlib.compress(b'[{"p":NaN},{}]'),None),(None,b'bad'),
               (None,array('d',[float('nan')]).tobytes())]
        for payload,scores in cases:
            with self.subTest(payload=payload,scores=scores):
                path=legacy(self.cache,payload=payload,scores=scores)
                with self.assertRaises((ValueError,zlib.error)):inspect_legacy(path)
                path.unlink()
    def test_frame_count_sequence_and_schema_are_checked(self):
        for sql in ("UPDATE frames SET id=2","UPDATE meta SET value='2' WHERE key='count'",'CREATE TABLE unknown(x)'):
            path=legacy(self.cache);con=sqlite3.connect(path)
            with con:con.execute(sql)
            con.close()
            with self.assertRaises(ValueError):inspect_legacy(path)
            path.unlink()
    def test_cancel_and_deadline_do_not_publish(self):
        path=legacy(self.cache)
        self.assertTrue(self.archive(cancelled=lambda:True)['cancelled'])
        with self.assertRaises(Stopped):inspect_legacy(path,deadline=time.monotonic()-1)
        self.assertTrue(path.exists());self.assertFalse(list(self.backup.rglob('*.sqlite')))
    def test_protected_sources_and_overlap_are_preserved(self):
        path=legacy(self.cache)
        self.assertEqual(self.archive(protected=[self.cache])['preserved'],1)
        with self.assertRaises(ValueError):self.archive(protected=[self.backup])
        self.assertTrue(path.exists())
    def test_busy_cache_is_preserved(self):
        path=legacy(self.cache);lease=Lease(self.cache,path.stem+'.lock')
        try:self.assertEqual(self.archive()['preserved'],1)
        finally:lease.close()
        self.assertFalse(list(self.backup.rglob('*.sqlite')))
    def test_restore_refuses_existing_target_and_corrupt_archive(self):
        path=legacy(self.cache);self.archive();manifest=next(self.backup.rglob('manifests/*.json'))
        existing=self.out/path.name;existing.write_bytes(b'keep')
        with self.assertRaises(ValueError):restore(manifest,self.out)
        self.assertEqual(existing.read_bytes(),b'keep');existing.unlink()
        obj=next(self.backup.rglob('*.sqlite'));obj.write_bytes(b'corrupt')
        with self.assertRaises(ValueError):restore(manifest,self.out)
        self.assertFalse(existing.exists());self.assertTrue(path.exists())
    def test_publish_never_replaces_existing_file(self):
        source=self.out/'source';target=self.out/'target';source.write_bytes(b'new');target.write_bytes(b'keep')
        with self.assertRaises(OSError):publish_file(source,target)
        self.assertEqual(target.read_bytes(),b'keep')
    def test_current_schema_is_skipped(self):
        path=legacy(self.cache);con=sqlite3.connect(path)
        with con:con.execute("INSERT INTO meta VALUES ('scores_codec','zlib-double-v1')")
        con.close();self.assertEqual(self.archive()['archived'],0);self.assertTrue(path.exists())
    def test_hardlinked_sources_are_preserved(self):
        import os
        path=legacy(self.cache);alias=self.out/'alias'
        try:os.link(path,alias)
        except OSError:self.skipTest('Hard links unavailable')
        with self.assertRaises(ValueError):inspect_legacy(path)
        self.assertEqual(self.archive()['archived'],0)

    def test_restore_cancel_leaves_no_published_target(self):
        path=legacy(self.cache);self.archive();manifest=next(self.backup.rglob('manifests/*.json'))
        with self.assertRaises(Stopped):restore(manifest,self.out,cancelled=lambda:True)
        self.assertFalse(list(self.out.iterdir()));self.assertTrue(path.exists())
    def test_corrupt_existing_object_is_not_overwritten(self):
        path=legacy(self.cache);self.archive();obj=next(self.backup.rglob('*.sqlite'));obj.write_bytes(b'keep corrupt copy for review')
        result=self.archive();self.assertEqual(result['errors'],1)
        self.assertEqual(obj.read_bytes(),b'keep corrupt copy for review');self.assertTrue(path.exists())
    def test_unrecognized_archive_directory_is_preserved(self):
        path=legacy(self.cache);owned=self.backup/'legacy-scenario-cache-v1';owned.mkdir();(owned/'evidence').write_bytes(b'keep')
        self.assertEqual(self.archive()['errors'],1);self.assertEqual((owned/'evidence').read_bytes(),b'keep')
        self.assertTrue(path.exists())
    def test_other_backup_owns_destination(self):
        from PyQt5.QtCore import QLockFile
        path=legacy(self.cache);lock=QLockFile(str(self.backup/'.dfs-history-backup.lock'));lock.setStaleLockTime(0)
        self.assertTrue(lock.tryLock(0))
        try:
            with self.assertRaises(ValueError):self.archive()
        finally:lock.unlock()
        self.assertTrue(path.exists());self.assertFalse(list(self.backup.rglob('*.sqlite')))

    def test_cancel_inside_sql_validation_reports_incomplete(self):
        from unittest.mock import patch
        legacy(self.cache);stopped=[False]
        def interrupt(*args,**kwargs):
            stopped[0]=True;raise sqlite3.OperationalError('interrupted')
        with patch('legacy_scenario_archive.inspect_legacy',side_effect=interrupt):
            result=self.archive(cancelled=lambda:stopped[0])
        self.assertTrue(result['cancelled']);self.assertFalse(result['complete'])
        self.assertFalse(list(self.backup.rglob('*.sqlite')))

    def test_reclamation_restores_exact_original_and_keeps_all_recovery_files(self):
        path=legacy(self.cache);original=path.read_bytes();size=path.stat().st_size
        result=self.archive(reclaim=True)
        self.assertEqual(result['archived'],1);self.assertEqual(result['reclaimed_files'],1)
        self.assertEqual(result['reclaimed_bytes'],size);self.assertFalse(path.exists())
        manifest=next(self.backup.rglob('manifests/*.json'));obj=next(self.backup.rglob('*.sqlite'))
        self.assertEqual(obj.read_bytes(),original);self.assertTrue((manifest.parent.parent/'format.json').exists())
        self.assertEqual(Path(restore(manifest,self.out)).read_bytes(),original)
    def test_reclamation_requires_unchanged_recovery_object_manifest_and_marker(self):
        from unittest.mock import patch
        from legacy_scenario_archive import archive_verified
        for part in ('archive','manifest','marker'):
            with self.subTest(part=part):
                self.backup=self.root/('backup-'+part);self.backup.mkdir()
                path=legacy(self.cache)
                def change(*args,**kwargs):
                    receipt=archive_verified(*args,**kwargs)
                    target=Path(receipt[part]) if part!='marker' else Path(receipt['archive_root'])/'format.json'
                    target.write_bytes(b'changed');return receipt
                with patch('legacy_scenario_archive.archive_verified',side_effect=change):
                    result=self.archive(reclaim=True)
                self.assertEqual(result['reclaimed_files'],0);self.assertTrue(path.exists())
                path.unlink()
    def test_source_changed_after_archival_is_not_retired(self):
        from unittest.mock import patch
        from legacy_scenario_archive import archive_verified
        path=legacy(self.cache)
        def change(*args,**kwargs):
            receipt=archive_verified(*args,**kwargs);path.touch();return receipt
        with patch('legacy_scenario_archive.archive_verified',side_effect=change):
            result=self.archive(reclaim=True)
        self.assertEqual(result['reclaimed_files'],0);self.assertTrue(path.exists())
    def test_cancel_after_verified_archive_preserves_working_copy(self):
        from unittest.mock import patch
        from legacy_scenario_archive import archive_verified
        path=legacy(self.cache);cancelled=[False]
        def stop(*args,**kwargs):
            receipt=archive_verified(*args,**kwargs);cancelled[0]=True;return receipt
        with patch('legacy_scenario_archive.archive_verified',side_effect=stop):
            result=self.archive(reclaim=True,cancelled=lambda:cancelled[0])
        self.assertEqual(result['reclaimed_files'],0);self.assertTrue(result['cancelled']);self.assertTrue(path.exists())
        self.assertTrue(list(self.backup.rglob('manifests/*.json')))
    def test_busy_legacy_sqlite_reader_keeps_working_copy(self):
        path=legacy(self.cache);con=sqlite3.connect(path);con.execute('BEGIN');con.execute('SELECT * FROM frames').fetchall()
        try:result=self.archive(reclaim=True)
        finally:con.close()
        self.assertTrue(path.exists());self.assertEqual(result['reclaimed_files'],0)
        self.assertEqual(result['archived'],1)
    def test_unlink_failure_retains_verified_archive_and_working_copy(self):
        from unittest.mock import patch
        path=legacy(self.cache);unlink=Path.unlink
        def fail(target,*args,**kwargs):
            if target==path:raise PermissionError('busy')
            return unlink(target,*args,**kwargs)
        with patch.object(Path,'unlink',fail):result=self.archive(reclaim=True)
        self.assertEqual(result['reclaimed_files'],0);self.assertEqual(result['archived'],1);self.assertTrue(path.exists())
        self.assertTrue(list(self.backup.rglob('manifests/*.json')))
    def test_current_cache_and_unknown_evidence_never_retired_by_archive_action(self):
        path=legacy(self.cache);con=sqlite3.connect(path)
        with con:con.execute("INSERT INTO meta VALUES ('scores_codec','zlib-double-v1')")
        con.close();evidence=self.cache/'evidence.sqlite';evidence.write_bytes(b'keep')
        result=self.archive(reclaim=True)
        self.assertEqual(result['reclaimed_files'],0);self.assertTrue(path.exists());self.assertEqual(evidence.read_bytes(),b'keep')

    def test_fresh_byte_check_rejects_changed_object_even_with_updated_receipt_stamp(self):
        from unittest.mock import patch
        from legacy_scenario_archive import archive_verified,stamp
        path=legacy(self.cache)
        def change(*args,**kwargs):
            receipt=archive_verified(*args,**kwargs);obj=Path(receipt['archive'])
            obj.write_bytes(b'bad');receipt['archive_stamp']=stamp(obj);return receipt
        with patch('legacy_scenario_archive.archive_verified',side_effect=change):result=self.archive(reclaim=True)
        self.assertEqual(result['reclaimed_files'],0);self.assertTrue(path.exists())

if __name__=='__main__':unittest.main()
