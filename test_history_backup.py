import json
import tempfile
import unittest
from pathlib import Path
from history_backup import backup_history, restore_history, object_path


class HistoryBackupTests(unittest.TestCase):
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
