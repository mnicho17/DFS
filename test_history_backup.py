import json
from contextlib import closing
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from history_backup import backup_history, restore_history, object_path


class HistoryBackupTests(unittest.TestCase):
    def fixture(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root=Path(temporary.name);source=root/'history';dest=root/'usb';source.mkdir();dest.mkdir()
        (source/'data').write_bytes(b'original')
        return root,source,dest

    def test_unchanged_receipt_skips_source_and_object_reads(self):
        root,source,dest=self.fixture()
        backup_history(source,dest,progress=lambda s:None)
        with patch('history_backup.digest',side_effect=AssertionError('unchanged content should not be read')):
            result=backup_history(source,dest,progress=lambda s:None)
        self.assertEqual(result['skipped_unchanged'],1)
        self.assertEqual(result['source_files_hashed'],0)
        restore_history(result['manifest'],root/'restored')
        self.assertEqual((root/'restored/data').read_bytes(),b'original')

    def test_changed_file_is_hashed_and_verified(self):
        root,source,dest=self.fixture()
        backup_history(source,dest,progress=lambda s:None)
        (source/'data').write_bytes(b'changed longer data')
        result=backup_history(source,dest,progress=lambda s:None)
        self.assertEqual(result['skipped_unchanged'],0)
        self.assertEqual(result['copied'],1)
        restore_history(result['manifest'],root/'restored')
        self.assertEqual((root/'restored/data').read_bytes(),b'changed longer data')

    def test_missing_object_is_rebuilt_even_with_unchanged_source_stamp(self):
        root,source,dest=self.fixture()
        first=backup_history(source,dest,progress=lambda s:None)
        value=json.loads(Path(first['manifest']).read_text())
        object_path(dest/'incremental-v1',value['entries']['data']['sha256']).unlink()
        result=backup_history(source,dest,progress=lambda s:None)
        self.assertEqual(result['copied'],1)
        self.assertEqual(result['skipped_unchanged'],0)

    def test_legacy_and_foreign_source_receipts_cannot_authorize_skip(self):
        root,source,dest=self.fixture()
        first=backup_history(source,dest,progress=lambda s:None)
        path=Path(first['manifest']);value=json.loads(path.read_text());value.pop('source_id');path.write_text(json.dumps(value))
        result=backup_history(source,dest,progress=lambda s:None)
        self.assertEqual(result['source_files_hashed'],1)
        other=root/'other';other.mkdir();(other/'data').write_bytes(b'original')
        result=backup_history(other,dest,progress=lambda s:None)
        self.assertEqual(result['skipped_unchanged'],0)

    def test_full_verification_bypasses_skip_and_detects_same_size_corruption(self):
        root,source,dest=self.fixture()
        first=backup_history(source,dest,progress=lambda s:None)
        result=backup_history(source,dest,verify_existing=True,progress=lambda s:None)
        self.assertEqual(result['source_files_hashed'],1)
        self.assertEqual(result['skipped_unchanged'],0)
        value=json.loads(Path(first['manifest']).read_text())
        object_path(dest/'incremental-v1',value['entries']['data']['sha256']).write_bytes(b'corrupt!')
        with self.assertRaises(ValueError):backup_history(source,dest,verify_existing=True,progress=lambda s:None)

    def test_progress_identifies_phase_and_file(self):
        root,source,dest=self.fixture();messages=[]
        with patch('history_backup.time.perf_counter',side_effect=range(0,1000,10)):
            backup_history(source,dest,progress=messages.append)
        self.assertTrue(any('hashing data:' in s for s in messages))
        self.assertTrue(any('copying data:' in s for s in messages))
        self.assertTrue(any('verifying new data:' in s for s in messages))

    def test_reuse_versions_deletions_and_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'history';dest=root/'usb';source.mkdir();dest.mkdir()
            (source/'one').write_text('original');(source/'two').write_text('same')
            first=backup_history(source,dest,progress=lambda s:None)
            again=backup_history(source,dest,progress=lambda s:None)
            self.assertEqual(again['bytes_written'],0);self.assertEqual(again['reused'],2)
            (source/'one').write_text('updated');(source/'two').unlink()
            last=backup_history(source,dest,progress=lambda s:None)
            self.assertEqual(last['copied'],1)
            restore_history(first['manifest'],root/'old')
            self.assertEqual((root/'old/one').read_text(),'original')
            self.assertTrue((root/'old/two').exists())
            restore_history(last['manifest'],root/'new')
            self.assertEqual((root/'new/one').read_text(),'updated')
            self.assertFalse((root/'new/two').exists())

    def test_corruption_and_unsafe_restore_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'history';dest=root/'usb';source.mkdir();dest.mkdir()
            (source/'data').write_text('original')
            saved=backup_history(source,dest,progress=lambda s:None)
            manifest=Path(saved['manifest']);value=json.loads(manifest.read_text())
            obj=object_path(dest/'incremental-v1',value['entries']['data']['sha256']);obj.write_text('tampered')
            with self.assertRaises(ValueError):restore_history(manifest,root/'restore')
            self.assertFalse((root/'restore').exists())
            with self.assertRaises(ValueError):backup_history(source,dest,verify_existing=True,progress=lambda s:None)
            value['entries']['../escape']=value['entries'].pop('data');manifest.write_text(json.dumps(value))
            with self.assertRaises(ValueError):restore_history(manifest,root/'restore')

    def test_source_change_does_not_publish_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'history';dest=root/'usb';source.mkdir();dest.mkdir()
            (source/'data').write_text('original')
            with self.assertRaises(ValueError):backup_history(source,dest,progress=lambda s:(source/'new').write_text('changed'))
            self.assertFalse(list(dest.rglob('manifests/*.json')))


class LiveHistoryBackupTests(unittest.TestCase):
    fixture=HistoryBackupTests.fixture

    def test_wal_database_restores_committed_rows_without_sidecars(self):
        import sqlite3
        root,source,dest=self.fixture();db=source/'exports.sqlite'
        con=sqlite3.connect(db);self.addCleanup(con.close)
        con.execute('PRAGMA journal_mode=WAL');con.execute('CREATE TABLE entries(value)')
        con.execute("INSERT INTO entries VALUES ('committed')");con.commit()
        con.execute("INSERT INTO entries VALUES ('uncommitted')")
        # An uncommitted writer remains invisible to the read-only snapshot.
        saved=backup_history(source,dest,live=True,progress=lambda s:None)
        self.assertEqual(saved['sqlite_snapshots'],1)
        value=json.loads(Path(saved['manifest']).read_text())
        self.assertNotIn('exports.sqlite-wal',value['entries']);self.assertNotIn('exports.sqlite-shm',value['entries'])
        restore_history(saved['manifest'],root/'restored')
        with closing(sqlite3.connect(root/'restored/exports.sqlite')) as restored:
            self.assertEqual(restored.execute('SELECT * FROM entries').fetchall(),[('committed',)])
            self.assertEqual(restored.execute('PRAGMA integrity_check').fetchone()[0],'ok')

    def test_wal_only_change_invalidates_fast_skip_and_reuses_other_files(self):
        import sqlite3
        root,source,dest=self.fixture();db=source/'unusual.data'
        con=sqlite3.connect(db);self.addCleanup(con.close)
        con.execute('PRAGMA journal_mode=WAL');con.execute('CREATE TABLE entries(value)');con.commit()
        first=backup_history(source,dest,live=True,skip_if_current=True,progress=lambda s:None)
        stamp=db.stat().st_mtime_ns
        con.execute('INSERT INTO entries VALUES (42)');con.commit()
        self.assertEqual(db.stat().st_mtime_ns,stamp)
        saved=backup_history(source,dest,live=True,skip_if_current=True,progress=lambda s:None)
        self.assertNotEqual(first['manifest'],saved['manifest']);self.assertEqual(saved['skipped_unchanged'],1)
        restore_history(saved['manifest'],root/'restored')
        with closing(sqlite3.connect(root/'restored/unusual.data')) as restored:
            self.assertEqual(restored.execute('SELECT * FROM entries').fetchall(),[(42,)])

    def test_current_inventory_reuses_manifest_without_content_reads(self):
        root,source,dest=self.fixture()
        first=backup_history(source,dest,live=True,skip_if_current=True,progress=lambda s:None)
        real=Path.open
        def guarded(path,*args,**kwargs):
            if path.is_relative_to(source) or 'objects' in path.parts:raise AssertionError('No source/object reads')
            return real(path,*args,**kwargs)
        with patch('pathlib.Path.open',guarded):
            again=backup_history(source,dest,live=True,skip_if_current=True,progress=lambda s:None)
        self.assertTrue(again['unchanged']);self.assertEqual(again['manifest'],first['manifest'])
        self.assertEqual(len(list((dest/'incremental-v1/manifests').glob('*.json'))),1)

    def test_cancelled_pass_preserves_manifest_and_can_resume_verified_objects(self):
        import threading
        from history_backup import BackupCancelled
        root,source,dest=self.fixture();first=backup_history(source,dest,live=True,progress=lambda s:None)
        (source/'new').write_bytes(b'new bytes');stop=threading.Event()
        def progress(message):
            if 'verifying new' in message or 'copying new' in message:stop.set()
        with self.assertRaises(BackupCancelled):
            backup_history(source,dest,live=True,progress=progress,cancelled=stop.is_set)
        self.assertEqual(len(list((dest/'incremental-v1/manifests').glob('*.json'))),1)
        self.assertFalse(list(dest.rglob('*.partial')))
        restore_history(first['manifest'],root/'old');self.assertFalse((root/'old/new').exists())
        later=backup_history(source,dest,live=True,progress=lambda s:None)
        restore_history(later['manifest'],root/'new-restore');self.assertEqual((root/'new-restore/new').read_bytes(),b'new bytes')

    def test_destination_lock_prevents_overlap(self):
        from PyQt5.QtCore import QLockFile
        root,source,dest=self.fixture();lock=QLockFile(str(dest/'.dfs-history-backup.lock'))
        self.assertTrue(lock.tryLock(0))
        try:
            with self.assertRaisesRegex(ValueError,'Another backup'):
                backup_history(source,dest,live=True,progress=lambda s:None)
        finally:lock.unlock()
        self.assertTrue(backup_history(source,dest,live=True,progress=lambda s:None)['manifest'])

    def test_source_mutation_does_not_publish_a_live_manifest(self):
        root,source,dest=self.fixture()
        with self.assertRaisesRegex(ValueError,'History changed'):
            backup_history(source,dest,live=True,progress=lambda s:(source/'new').write_text('changed'))
        self.assertFalse(list(dest.rglob('manifests/*.json')))


    def test_legacy_exit_call_inherits_live_mode_and_skips_sqlite_content_reads(self):
        import sqlite3
        root,source,dest=self.fixture();con=sqlite3.connect(source/'exports.sqlite');self.addCleanup(con.close)
        con.execute('PRAGMA journal_mode=WAL');con.execute('CREATE TABLE data(value)');con.commit()
        first=backup_history(source,dest,live=True,progress=lambda s:None)
        real=Path.open
        def guarded(path,*args,**kwargs):
            if path.is_relative_to(source) or 'objects' in path.parts:raise AssertionError('Unchanged content read')
            return real(path,*args,**kwargs)
        with patch('pathlib.Path.open',guarded):saved=backup_history(source,dest,progress=lambda s:None)
        self.assertTrue(saved['unchanged']);self.assertEqual(first['manifest'],saved['manifest'])

    def test_abandoned_process_lock_is_recovered(self):
        import subprocess,sys
        root,source,dest=self.fixture();path=dest/'.dfs-history-backup.lock'
        code="from PyQt5.QtCore import QLockFile; import os,sys; lock=QLockFile(sys.argv[1]); ok=lock.tryLock(0); os._exit(0 if ok else 1)"
        run=subprocess.run([sys.executable,'-c',code,str(path)],capture_output=True,text=True,timeout=10)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertTrue(backup_history(source,dest,live=True,progress=lambda s:None)['manifest'])

    def test_explicit_verification_checks_previous_sqlite_snapshot_object(self):
        import sqlite3
        root,source,dest=self.fixture()
        with closing(sqlite3.connect(source/'exports.sqlite')) as con:
            con.execute('CREATE TABLE data(value)');con.commit()
        first=backup_history(source,dest,live=True,progress=lambda s:None)
        receipt=json.loads(Path(first['manifest']).read_text())
        obj=object_path(dest/'incremental-v1',receipt['entries']['exports.sqlite']['sha256'])
        content=bytearray(obj.read_bytes());content[0]^=1;obj.write_bytes(content)
        with self.assertRaisesRegex(ValueError,'failed verification'):
            backup_history(source,dest,live=True,verify_existing=True,progress=lambda s:None)
        self.assertEqual(len(list((dest/'incremental-v1/manifests').glob('*.json'))),1)
