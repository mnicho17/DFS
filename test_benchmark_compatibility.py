"""Only audited, non-runtime benchmarks may avoid model invalidation."""
from test_environment import install
install()
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import model_compatibility as model
from candidate_library import run_search, load_candidates
from build_snapshots import create_snapshot
from test_showdown_library import players


class BenchmarkCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'scripts').mkdir()
        self.engine = self.root/'engine.py'
        self.engine.write_text('VALUE=1\n',encoding='utf-8')
        self.benchmark = self.root/'scripts/benchmark_reuse_stages.py'
        self.benchmark.write_text('VALUE=1\n',encoding='utf-8')
        self.helper = self.root/'scripts/benchmark_scenario_replay.py'
        self.helper.write_text('VALUE=1\n',encoding='utf-8')

    def identity(self):
        return model.source_identity(self.root)

    def test_only_exact_audited_names_are_omitted(self):
        before = self.identity()
        self.benchmark.write_text('VALUE=2\n',encoding='utf-8')
        self.assertEqual(self.identity(),before)
        unknown = self.root/'scripts/benchmark_unknown_model.py'
        unknown.write_text('VALUE=1\n',encoding='utf-8')
        self.assertNotEqual(self.identity(),before)
        before = self.identity()
        unknown.write_text('VALUE=2\n',encoding='utf-8')
        self.assertNotEqual(self.identity(),before)

    def test_unused_benchmark_syntax_is_irrelevant_but_imported_syntax_fails_closed(self):
        before = self.identity()
        self.benchmark.write_text('invalid python @@@',encoding='utf-8')
        self.assertEqual(self.identity(),before)
        self.engine.write_text('import scripts.benchmark_reuse_stages\\n',encoding='utf-8')
        with self.assertRaises(SyntaxError):
            self.identity()

    def test_dependency_version_changes_remain_strict(self):
        def library(version):
            package = SimpleNamespace(metadata={'Name':'synthetic-dependency'},version=version)
            return SimpleNamespace(metadata=SimpleNamespace(distributions=lambda:[package]))
        with patch.object(model,'importlib',library('1')):
            before = self.identity()
        with patch.object(model,'importlib',library('2')):
            self.assertNotEqual(self.identity(),before)

    def test_static_and_relative_transitive_runtime_imports_remain_strict(self):
        self.engine.write_text('from scripts.benchmark_reuse_stages import VALUE\n',encoding='utf-8')
        self.benchmark.write_text('from .benchmark_scenario_replay import VALUE\n',encoding='utf-8')
        before = self.identity()
        self.helper.write_text('VALUE=2\n',encoding='utf-8')
        self.assertNotEqual(self.identity(),before)
        before = self.identity()
        self.benchmark.write_text(self.benchmark.read_text(encoding='utf-8')+'PARAM=2\n',encoding='utf-8')
        self.assertNotEqual(self.identity(),before)

    def test_literal_and_aliased_dynamic_imports_remain_strict(self):
        for expression in ("import importlib\nimportlib.import_module('scripts.benchmark_reuse_stages')\n",
                           "from importlib import import_module as load\nload('scripts.benchmark_reuse_stages')\n"):
            self.engine.write_text(expression,encoding='utf-8')
            before = self.identity()
            self.benchmark.write_text(self.benchmark.read_text(encoding='utf-8')+'NEXT=1\n',encoding='utf-8')
            self.assertNotEqual(self.identity(),before)

    def test_opaque_dynamic_execution_conservatively_keeps_all_benchmarks(self):
        for expression in ("import importlib\nimportlib.import_module(name)\n",
                           "from importlib import import_module as load\nload(name)\n",
                           "import importlib\nimportlib.import_module('.benchmark_reuse_stages',package)\n",
                           "__import__('benchmark_reuse_stages', globals(), locals(), [], 1)\n",
                           "exec(source)\n"):
            self.engine.write_text(expression,encoding='utf-8')
            before = self.identity()
            self.helper.write_text(self.helper.read_text(encoding='utf-8')+'NEXT=1\n',encoding='utf-8')
            self.assertNotEqual(self.identity(),before)

    def test_prepared_rosters_reuse_after_benchmark_edit_but_runtime_change_is_rejected(self):
        snapshot = create_snapshot(players(),dict(sport='NFL',contest_kind='showdown',salary_cap=50000,salary_strategy='Near Cap'),{})
        library = self.root/'prepared.dfslib'
        with patch('candidate_library.preparation_model_id',side_effect=self.identity):
            with patch('candidate_library.code_id',return_value='build-a'):
                run_search(library,snapshot,seconds=.1,candidate_limit=5,compatibility=True)
            self.benchmark.write_text('VALUE=2\n',encoding='utf-8')
            with patch('candidate_library.code_id',return_value='build-b'):
                rows,report = load_candidates(library,snapshot['inputs']['players'],kind='showdown',salary_cap=50000)
                self.assertTrue(rows)
                self.assertTrue(report['compatibility_reused'])
                run_search(library,snapshot,seconds=.1,candidate_limit=10,compatibility=True)
            self.engine.write_text('VALUE=2\n',encoding='utf-8')
            with patch('candidate_library.code_id',return_value='build-a'):
                with self.assertRaises(ValueError):
                    load_candidates(library,snapshot['inputs']['players'],kind='showdown',salary_cap=50000)
                with self.assertRaises(ValueError):
                    run_search(library,snapshot,seconds=.1,compatibility=True)


if __name__=='__main__':
    unittest.main()
