from test_environment import install
install()
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import model_compatibility as model
from build_snapshots import create_snapshot
from candidate_library import run_search,load_candidates,metadata
from test_showdown_library import players


class ModelCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)

    def test_ui_only_edits_keep_identity_but_workers_helpers_and_unknown_modules_invalidate(self):
        path=self.root/'main_window.py'
        path.write_text('VALUE=1\nclass MainWindow:\n def paint(self):return 1\nclass OwnershipSimWorker:\n def run(self):return VALUE\n')
        before=model.source_identity(self.root)
        path.write_text(path.read_text().replace('paint(self):return 1','paint(self):return 2'))
        (self.root/'slate_picker.py').write_text('UI=2\n')
        self.assertEqual(before,model.source_identity(self.root))
        path.write_text(path.read_text().replace('return VALUE','return VALUE+1'))
        self.assertNotEqual(before,model.source_identity(self.root))
        before=model.source_identity(self.root);(self.root/'unknown_future_model.py').write_text('ALGORITHM=1\n')
        self.assertNotEqual(before,model.source_identity(self.root))

    def test_frozen_build_uses_embedded_stamp_and_unknown_build_falls_back_strictly(self):
        model.model_id.cache_clear();self.addCleanup(model.model_id.cache_clear)
        with patch.object(model.sys,'frozen',True,create=True),patch.dict('sys.modules',build_info_generated=types.SimpleNamespace(MODEL_COMPATIBILITY={'ownership':'a'*64})):
            self.assertEqual(model.model_id('ownership'),'a'*64)
        model.model_id.cache_clear()
        with patch.object(model.sys,'frozen',True,create=True),patch.dict('sys.modules',build_info_generated=None),patch('candidate_library.code_id',return_value='full-build'):
            self.assertEqual(model.model_id('ownership'),'full-build')

    def snapshot(self):
        return create_snapshot(players(),dict(sport='NFL',contest_kind='showdown',salary_cap=50000,salary_strategy='Near Cap'),{})

    def test_opt_in_rosters_resume_and_load_after_ui_update_with_batch_build_provenance(self):
        import sqlite3
        from contextlib import closing
        path=self.root/'prepared.dfslib';snap=self.snapshot()
        with patch('candidate_library.code_id',return_value='build-a'),patch('candidate_library.preparation_model_id',return_value='math-a'):
            run_search(path,snap,seconds=.1,candidate_limit=5,compatibility=True)
        original=metadata(path)
        with patch('candidate_library.code_id',return_value='build-b'),patch('candidate_library.preparation_model_id',return_value='math-a'):
            run_search(path,snap,seconds=.1,candidate_limit=10,compatibility=True)
            result,report=load_candidates(path,snap['inputs']['players'],kind='showdown',salary_cap=50000)
        self.assertTrue(result);self.assertTrue(report['compatibility_reused'])
        self.assertEqual(metadata(path)['code_id'],'build-a')
        self.assertGreaterEqual(metadata(path)['count'],original['count'])
        with closing(sqlite3.connect(path)) as con:
            self.assertEqual({r[0] for r in con.execute('SELECT code_id FROM batch_builds')},{'build-a','build-b'})

    def test_algorithm_and_snapshot_changes_still_fail_closed(self):
        path=self.root/'prepared.dfslib';snap=self.snapshot()
        with patch('candidate_library.code_id',return_value='build-a'),patch('candidate_library.preparation_model_id',return_value='math-a'):
            run_search(path,snap,seconds=.1,candidate_limit=5,compatibility=True)
        with patch('candidate_library.code_id',return_value='build-b'),patch('candidate_library.preparation_model_id',return_value='math-b'):
            with self.assertRaises(ValueError):run_search(path,snap,seconds=.1,compatibility=True)
            with self.assertRaises(ValueError):load_candidates(path,snap['inputs']['players'],kind='showdown',salary_cap=50000)
        changed=self.snapshot();changed['inputs']['recipe']['salary_cap']=49000
        from build_snapshots import fingerprint
        changed['input_id']=fingerprint(changed['inputs'])
        with self.assertRaises(ValueError):run_search(path,changed,seconds=.1,compatibility=True)

    def test_legacy_library_keeps_full_build_gate(self):
        path=self.root/'legacy.dfslib';snap=self.snapshot()
        with patch('candidate_library.code_id',return_value='build-a'):
            run_search(path,snap,seconds=.1,candidate_limit=5)
        with patch('candidate_library.code_id',return_value='build-b'):
            with self.assertRaises(ValueError):run_search(path,snap,seconds=.1,compatibility=True)
            with self.assertRaises(ValueError):load_candidates(path,snap['inputs']['players'],kind='showdown',salary_cap=50000)


    def test_nested_model_modules_are_strict_and_build_stamp_does_not_change_identity(self):
        before=model.source_identity(self.root)
        (self.root/'new_model').mkdir();module=self.root/'new_model'/'test_named_runtime.py';module.write_text('PARAM=1\n')
        self.assertNotEqual(before,model.source_identity(self.root))
        before=model.source_identity(self.root);(self.root/'build_info_generated.py').write_text('BUILD_INFO=123\n')
        self.assertEqual(before,model.source_identity(self.root))
        module.write_text('PARAM=2\n');self.assertNotEqual(before,model.source_identity(self.root))

    def test_packaging_script_embeds_compatibility_stamp_without_relying_on_runtime_sources(self):
        import runpy,shutil,ast,sys
        script=self.root/'scripts'/'write_build_info.py';script.parent.mkdir()
        shutil.copyfile(Path(__file__).parent/'scripts'/'write_build_info.py',script)
        with patch('app_version.identity',return_value={'commit':'abc','version':'test'}),patch('model_compatibility.source_identity',return_value='b'*64),patch.object(sys,'path',list(sys.path)):
            runpy.run_path(str(script),run_name='__main__')
        values={node.targets[0].id:ast.literal_eval(node.value) for node in ast.parse((self.root/'build_info_generated.py').read_text()).body if isinstance(node,ast.Assign)}
        self.assertEqual(values['MODEL_COMPATIBILITY'],{scope:'b'*64 for scope in model.SCOPES})
        self.assertEqual(values['BUILD_INFO']['commit'],'abc')

    def test_opt_in_model_match_still_revalidates_current_salaries(self):
        import copy
        path=self.root/'prepared.dfslib';snap=self.snapshot()
        with patch('candidate_library.preparation_model_id',return_value='math-a'):
            run_search(path,snap,seconds=.1,candidate_limit=5,compatibility=True)
            changed=copy.deepcopy(snap['inputs']['players'])
            for p in changed:p.update(FlexSalary=60000,CptSalary=90000)
            with patch('candidate_library.code_id',return_value='ui-new'):
                with self.assertRaises(ValueError):load_candidates(path,changed,kind='showdown',salary_cap=50000)


    def test_stamped_model_mismatch_cannot_fall_back_to_matching_full_source_hash(self):
        path=self.root/'prepared.dfslib';snap=self.snapshot()
        with patch('candidate_library.code_id',return_value='same-source'),patch('candidate_library.preparation_model_id',return_value='deps-a'):
            run_search(path,snap,seconds=.1,candidate_limit=5,compatibility=True)
        with patch('candidate_library.code_id',return_value='same-source'),patch('candidate_library.preparation_model_id',return_value='deps-b'):
            with self.assertRaises(ValueError):run_search(path,snap,seconds=.1,compatibility=True)
            with self.assertRaises(ValueError):run_search(path,snap,seconds=.1)
            with self.assertRaises(ValueError):load_candidates(path,snap['inputs']['players'],kind='showdown',salary_cap=50000)


if __name__=='__main__':unittest.main()
