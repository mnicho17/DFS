from test_environment import install
install()
import unittest
from unittest.mock import patch
import test_history_refresh as refresh_tests
from analysis_imports_ui import CombinedImportWorker
from history_refresh_ui import refresh_summary


class CompletionCheckpointTests(unittest.TestCase):
    setUpClass = classmethod(refresh_tests.HistoryRefreshTests.setUpClass.__func__)
    fixture = refresh_tests.HistoryRefreshTests.fixture
    worker = refresh_tests.HistoryRefreshTests.worker
    run_worker = refresh_tests.HistoryRefreshTests.run_worker
    def automatic(self, f):
        return CombinedImportWorker(str(f.results), str(f.salaries), 'Synthetic_User',
            db_path=f.db, refresh_history=True, automatic=True)

    def prime(self, f):
        result = self.run_worker(self.automatic(f))
        self.assertFalse(result.get('unchanged_checkpoint'))

    def test_restart_skips_hashing_and_indexing_but_manual_refresh_verifies(self):
        f = self.fixture()
        self.prime(f)
        with patch('analysis_imports_ui.import_folders', side_effect=AssertionError('reimport')), \
                patch('opponent_history.sync_saved', side_effect=AssertionError('reindex')):
            result = self.run_worker(self.automatic(f))
        self.assertTrue(result['unchanged_checkpoint'])
        self.assertIn('no source hashing or indexing', refresh_summary(result))
        self.assertNotIn('unchanged_checkpoint', self.run_worker(self.worker(f)))

    def test_changed_file_invalidates(self):
        import os
        import history_refresh_cache as cache
        f = self.fixture()
        self.prime(f)
        worker = self.automatic(f)
        before, _ = cache.inputs(worker)
        stat = f.raw_result.stat()
        data = f.raw_result.read_bytes()
        f.raw_result.write_bytes(data.replace(b'Synthetic_User', b'Different_User'))
        os.utime(f.raw_result, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000000))
        after, _ = cache.inputs(worker)
        self.assertNotEqual(before, after)

    def test_database_mapping_change_invalidates(self):
        import history_refresh_cache as cache
        f = self.fixture()
        self.prime(f)
        f.change('DELETE FROM analysis_salary_pairs')
        worker = self.automatic(f)
        fingerprint, _ = cache.inputs(worker)
        self.assertIsNone(cache.read(worker, fingerprint))

    def test_snapshot_change_and_missing_root_invalidate(self):
        import history_refresh_cache as cache
        f = self.fixture()
        self.prime(f)
        worker = self.automatic(f)
        before, _ = cache.inputs(worker)
        snapshot = f.query('SELECT snapshot FROM analysis_sources LIMIT 1')[0][0]
        from pathlib import Path
        path = Path(snapshot)
        path.write_bytes(path.read_bytes() + b'\n')
        after, _ = cache.inputs(worker)
        self.assertNotEqual(before, after)
        worker.results_folder = str(f.root / 'missing')
        with self.assertRaises(ValueError):
            cache.inputs(worker)

    def test_errors_cancellation_and_changes_during_work_never_mark_complete(self):
        import history_refresh_cache as cache
        f = self.fixture()
        worker = self.automatic(f)
        fingerprint, _ = cache.inputs(worker)
        for result in ({'errors':['retry']}, {'cancelled':True},
                       {'username_history':{'errors':['retry']}}):
            cache.save(worker, fingerprint, result)
            self.assertFalse(cache.cache_path(worker).exists())
        (f.results / 'new.csv').write_text('changed', encoding='utf-8')
        cache.save(worker, fingerprint, {})
        self.assertFalse(cache.cache_path(worker).exists())

    def test_corrupt_cache_falls_back_and_username_change_invalidates(self):
        import history_refresh_cache as cache
        f = self.fixture()
        self.prime(f)
        worker = self.automatic(f)
        before, _ = cache.inputs(worker)
        worker.username = 'another'
        after, _ = cache.inputs(worker)
        self.assertNotEqual(before, after)
        cache.cache_path(worker).write_text('{broken', encoding='utf-8')
        self.assertIsNone(cache.read(worker, before))

    def test_unrelated_build_write_does_not_invalidate(self):
        import history_refresh_cache as cache
        f = self.fixture()
        self.prime(f)
        worker = self.automatic(f)
        fingerprint, _ = cache.inputs(worker)
        f.change('CREATE TABLE unrelated_builds (id INTEGER)')
        f.change('INSERT INTO unrelated_builds VALUES (1)')
        self.assertIsNotNone(cache.read(worker, fingerprint))

    def test_added_file_and_packaged_update_invalidate(self):
        import history_refresh_cache as cache
        f = self.fixture()
        self.prime(f)
        worker = self.automatic(f)
        before, _ = cache.inputs(worker)
        (f.results / 'added.csv').write_text('new file', encoding='utf-8')
        after, _ = cache.inputs(worker)
        self.assertNotEqual(before, after)
        with patch.object(cache.sys, 'frozen', True, create=True):
            packaged, _ = cache.inputs(worker)
        self.assertNotEqual(after, packaged)

    def test_cache_storage_failure_does_not_fail_successful_import(self):
        f = self.fixture()
        with patch('history_refresh_cache.save', side_effect=OSError('read only')):
            result = self.run_worker(self.automatic(f))
        self.assertFalse(result['cancelled'])
        self.assertEqual(result['errors'], [])
