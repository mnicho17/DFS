import json
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
