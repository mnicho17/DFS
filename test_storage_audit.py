from test_environment import install
install()
import datetime as dt
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from storage_audit import audit,cache_hint,configured_roots,render

NOW=dt.datetime(2026,10,22,tzinfo=dt.timezone.utc)


def cache(root):
    path=root/('a'*64+'.sqlite');con=sqlite3.connect(path)
    with con:
        con.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)')
        con.execute('CREATE TABLE frames(id INTEGER PRIMARY KEY,payload BLOB,scores BLOB)')
        con.executemany('INSERT INTO meta VALUES (?,?)',
            [('key',path.stem),('count','1'),('digest','b'*64),('scores_codec','zlib-double-v1')])
    con.close();return path


def fingerprint(root):
    return {str(p.relative_to(root)):(hashlib.sha256(p.read_bytes()).hexdigest(),p.stat().st_mtime_ns)
            for p in root.rglob('*') if p.is_file()}


class StorageAuditTests(unittest.TestCase):
    def test_inventory_is_read_only_and_age_does_not_authorize_cleanup(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw);path=cache(root);os.utime(path,(1,1));unknown=root/'evidence.sqlite';unknown.write_bytes(b'evidence')
            before=fingerprint(root)
            result=audit([dict(path=raw,role='scenario')],as_of=NOW)
            self.assertEqual(before,fingerprint(root));self.assertTrue(result['complete'])
            rows={Path(r['path']).name:r for r in result['rows']}
            self.assertTrue(rows[path.name]['cache_metadata_hint']);self.assertTrue(rows[path.name]['older_than_30_days'])
            self.assertEqual(rows[unknown.name]['policy'],'Keep until identified')
            text=render(result);self.assertIn('no age-based retention policy',text)
            self.assertIn('Not verified by this audit',text);self.assertIn('not a last-use measurement',text)
            self.assertFalse(list(root.glob('*-shm')));self.assertFalse(list(root.glob('*-journal')))

    def test_legacy_cache_shape_is_distinguished_but_preserved_for_review(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw);path=cache(root)
            con=sqlite3.connect(path)
            with con:con.execute("DELETE FROM meta WHERE key='scores_codec'")
            con.close();before=fingerprint(root)
            result=audit([dict(path=raw,role='scenario')],as_of=NOW)
            self.assertEqual(fingerprint(root),before)
            row=result['rows'][0];self.assertEqual(row['role'],'legacy')
            self.assertEqual(row['policy'],'Keep legacy caches for review');self.assertFalse(row['cache_metadata_hint'])
            self.assertIn('not proof',row['dependencies']);self.assertEqual(row['cache_format_hint'],'legacy')

    def test_protected_source_and_backup_overlap_override_cache_hints(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw);cache(root)
            for role in ('sources','backup'):
                with patch('storage_audit.cache_hint',side_effect=AssertionError('protected source should not be probed')):
                    r=audit([dict(path=raw,role='scenario'),dict(path=raw,role=role)],as_of=NOW)
                self.assertEqual(r['files'],1);self.assertEqual(r['rows'][0]['role'],role)
                self.assertFalse(r['rows'][0]['cache_metadata_hint'])

    def test_prepared_manifest_partitions_and_unknown_files_are_kept(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw);(root/'full.sdfull').write_bytes(b'manifest')
            parts=root/'full.sdfull.parts';parts.mkdir();(parts/'part.sdlib').write_bytes(b'valuable work')
            (root/'research.sqlite').write_bytes(b'research')
            r=audit([dict(path=raw,role='history')],as_of=NOW)
            self.assertEqual(r['totals']['prepared']['files'],2)
            self.assertEqual(r['totals']['history']['files'],1)
            self.assertTrue(all(row['policy'].startswith('Keep') for row in r['rows']))
            self.assertTrue(all('not resolved' in row['dependencies'] for row in r['rows']))

    def test_nested_duplicate_scopes_and_root_aliases_count_once(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw);child=root/'child';child.mkdir();file=child/'result.csv';file.write_bytes(b'12345')
            r=audit([dict(path=raw,role='history'),dict(path=child/'..',role='history'),dict(path=file,role='sources')],as_of=NOW)
            self.assertEqual(r['files'],1);self.assertEqual(r['bytes'],5);self.assertEqual(r['rows'][0]['role'],'sources')

    def test_bounds_and_cancel_return_partial_without_fabricating_full_totals(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)
            for index in range(10):(root/str(index)).write_bytes(b'x'*(index+1))
            r=audit([dict(path=raw,role='history')],max_entries=5,max_rows=2,as_of=NOW)
            self.assertFalse(r['complete']);self.assertLess(r['files'],10);self.assertLessEqual(len(r['rows']),2)
            self.assertEqual(r['rows_omitted'],r['files']-len(r['rows']))
            stopped=audit([dict(path=raw,role='history')],cancelled=lambda:True,as_of=NOW)
            self.assertTrue(stopped['cancelled']);self.assertEqual(stopped['files'],0)
            self.assertFalse(audit([dict(path=raw,role='history')],seconds=0,as_of=NOW)['complete'])

    def test_largest_rows_are_bounded_but_totals_include_smaller_files(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)
            for index in range(5):(root/str(index)).write_bytes(b'x'*(index+1))
            r=audit([dict(path=raw,role='history')],max_rows=2,as_of=NOW)
            self.assertEqual([row['bytes'] for row in r['rows']],[5,4])
            self.assertEqual(r['bytes'],15);self.assertEqual(r['rows_omitted'],3)

    def test_missing_roots_are_reported_and_never_created(self):
        with tempfile.TemporaryDirectory() as raw:
            absent=Path(raw)/'absent'
            r=audit([dict(path=absent,role='backup')],as_of=NOW)
            self.assertFalse(absent.exists());self.assertFalse(r['complete']);self.assertEqual(r['errors'],1)
            self.assertIn('Missing',r['issues'][0]);self.assertIn('absent',r['issues'][0])

    def test_wal_sidecar_and_malformed_cache_are_preserved_without_probe_writes(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw);path=cache(root);sidecar=Path(str(path)+'-wal');sidecar.write_bytes(b'active')
            before=fingerprint(root);r=audit([dict(path=raw,role='scenario')],as_of=NOW)
            self.assertEqual(fingerprint(root),before);self.assertTrue(all(not row['cache_metadata_hint'] for row in r['rows']))
            sidecar.unlink();path.write_bytes(b'broken sqlite')
            self.assertFalse(cache_hint(path,lambda:False))

    def test_linked_file_is_kept_and_symlinks_are_not_followed(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw);source=root/'source';source.write_bytes(b'content');link=root/'hard'
            os.link(source,link)
            r=audit([dict(path=raw,role='history')],as_of=NOW)
            self.assertTrue(all(row['policy']=='Keep linked content' for row in r['rows']))
            symlink=root/'symbolic'
            try:symlink.symlink_to(source)
            except OSError:return
            r=audit([dict(path=raw,role='history')],as_of=NOW)
            self.assertEqual(r['skipped_links'],1);self.assertFalse(r['complete'])

    def test_reparse_directory_is_skipped_without_following_children(self):
        import stat
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw);junction=root/'junction';junction.mkdir();(junction/'important').write_text('retain')
            original=Path.lstat
            def info(path):
                if path==junction.resolve():return SimpleNamespace(st_mode=stat.S_IFDIR,st_file_attributes=0x400)
                return original(path)
            with patch.object(Path,'lstat',info):r=audit([dict(path=raw,role='history')],as_of=NOW)
            self.assertEqual(r['files'],0);self.assertEqual(r['skipped_links'],1);self.assertFalse(r['complete'])
            self.assertEqual((junction/'important').read_text(),'retain')

    def test_configured_roots_do_not_create_paths_or_modify_settings(self):
        with tempfile.TemporaryDirectory() as raw,patch.dict(os.environ,{'DFS_OPTIMIZER_DATA_DIR':raw}):
            settings=SimpleNamespace(value=lambda key,default='': {'learning/results_folder':str(Path(raw)/'results'),
                'build/known_libraries':'["'+str(Path(raw)/'manifest.sdfull').replace('\\','\\\\')+'"]'}.get(key,default))
            window=SimpleNamespace(settings=settings,idle_backup=SimpleNamespace(source=Path(raw)/'history',destination=lambda:str(Path(raw)/'backups')))
            roots=configured_roots(window)
            self.assertTrue(any(r['path'].endswith('.sdfull.parts') for r in roots))
            self.assertEqual(list(Path(raw).iterdir()),[])
