"""Conservative model compatibility separate from build/evidence identity.

Only audited presentation and unreferenced benchmark surfaces are omitted.
Unknown production modules remain strict. Full code_id() continues to qualify saved SIM/evidence artifacts.
"""
import ast
from functools import lru_cache
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sys

PRESENTATION_ONLY = frozenset(('app.py','app_version.py','pane_styles.py','slate_picker.py',
    'preparation_status_ui.py','ownership_review_ui.py','ownership_sensitivity_ui.py',
    'entry_review_ui.py','core_plays_ui.py','ownership_ui.py','build_review_ui.py',
    'ownership_refresh_notice.py'))

# Exact audited entry points, not a filename-prefix exclusion. New files stay strict.
AUDITED_BENCHMARKS = frozenset((
    'scripts/benchmark_candidate_metrics.py',
    'scripts/benchmark_classic_field_scoring.py',
    'scripts/benchmark_combined_warm_build.py',
    'scripts/benchmark_compute_ledger.py',
    'scripts/benchmark_game_input_reuse.py',
    'scripts/benchmark_historical_fitting_factors.py',
    'scripts/benchmark_portfolio_value_normalization.py',
    'scripts/benchmark_portfolio_value_total.py',
    'scripts/benchmark_primary_scoring.py',
    'scripts/benchmark_refinement_arithmetic.py',
    'scripts/benchmark_refinement_count_deltas.py',
    'scripts/benchmark_refinement_log_counts.py',
    'scripts/benchmark_reuse_stages.py',
    'scripts/benchmark_scenario_replay.py',
    'scripts/benchmark_showdown_construction_cache.py',
    'scripts/benchmark_showdown_indexed_candidates.py',
    'scripts/benchmark_showdown_indexed_field.py',
    'scripts/benchmark_volatility_reuse.py',
))

SCOPES = ('ownership','preparation')



def _required_benchmarks(trees):
    """Keep benchmark code strict if reachable from a strict module.

    Static imports are followed transitively. Opaque executable/dynamic imports
    conservatively retain every audited benchmark rather than guessing a target.
    """
    modules = {path[:-3].replace('/', '.').removesuffix('.__init__'): path
               for path in trees}
    pending = [path for path in trees if path not in AUDITED_BENCHMARKS]
    visited, required = set(), set()
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        if trees[path] is None:
            return set(AUDITED_BENCHMARKS).intersection(trees)
        package = path[:-3].replace('/', '.').split('.')[:-1]
        call_aliases = {alias.asname or alias.name: alias.name
                        for node in ast.walk(trees[path]) if isinstance(node, ast.ImportFrom)
                        for alias in node.names
                        if alias.name in ('exec', 'eval', 'run_path', 'spec_from_file_location',
                                          'exec_module', 'import_module', '__import__')}
        for node in ast.walk(trees[path]):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = '.'.join(package[:len(package)-node.level+1]) if node.level else ''
                base = '.'.join(part for part in (base, node.module) if part)
                names = [base] + [base + '.' + alias.name for alias in node.names]
            elif isinstance(node, ast.Call):
                name = node.func.id if isinstance(node.func, ast.Name) else (
                    node.func.attr if isinstance(node.func, ast.Attribute) else '')
                name = call_aliases.get(name, name)
                if name in ('exec', 'eval', 'run_path', 'spec_from_file_location', 'exec_module'):
                    return set(AUDITED_BENCHMARKS).intersection(trees)
                if name in ('import_module', '__import__'):
                    if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
                        return set(AUDITED_BENCHMARKS).intersection(trees)
                    if node.args[0].value.startswith('.') or (name == '__import__' and (len(node.args) > 1 or node.keywords)):
                        return set(AUDITED_BENCHMARKS).intersection(trees)
                    names = [node.args[0].value]
            for name in names:
                for module, target in modules.items():
                    if module == name or module.startswith(name + '.') or name.startswith(module + '.'):
                        if target in AUDITED_BENCHMARKS:
                            required.add(target)
                        pending.append(target)
    return required


def source_identity(root):
    sources={}
    trees={}
    invalid={}
    root=Path(root)
    ignored={'.git','.venv','__pycache__','build','dist','history','output','tmp','tests','node_modules','.codex','.agents'}
    for directory,subdirs,files in os.walk(root):
        subdirs[:]=sorted(d for d in subdirs if d not in ignored)
        for filename in sorted(files):
            path=Path(directory)/filename
            if path.suffix!='.py':continue
            relative=path.relative_to(root).as_posix()
            if relative=='build_info_generated.py' or relative in PRESENTATION_ONLY or ('/' not in relative and filename.startswith('test_')):
                continue
            try:
                tree=ast.parse(path.read_text(encoding='utf-8-sig'), filename=relative)
            except SyntaxError as error:
                if relative not in AUDITED_BENCHMARKS:
                    raise
                invalid[relative]=error
                trees[relative]=None
                continue
            if relative=='main_window.py':
                # The worker classes, globals/imports and pure helpers stay strict.
                tree.body=[node for node in tree.body if not isinstance(node,ast.ClassDef) or node.name!='MainWindow']
            trees[relative]=tree
    required = _required_benchmarks(trees)
    for relative, tree in trees.items():
        if relative in AUDITED_BENCHMARKS and relative not in required:
            continue
        if relative in invalid:
            raise invalid[relative]
        sources[relative]=hashlib.sha256(ast.dump(tree,include_attributes=False).encode()).hexdigest()
    dependencies=sorted((str(d.metadata.get('Name','')).lower(),d.version)
                        for d in importlib.metadata.distributions())
    payload=dict(policy=2,sources=sources,python=list(sys.version_info[:3]),
                 platform=[sys.platform,platform.machine(),sys.implementation.cache_tag,sys.maxsize],dependencies=dependencies)
    return hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()


@lru_cache(maxsize=1)
def _runtime_source_identity():
    return source_identity(Path(__file__).resolve().parent)


@lru_cache(maxsize=2)
def model_id(scope):
    if scope not in SCOPES:raise ValueError('Unknown model compatibility scope.')
    if getattr(sys,'frozen',False):
        try:
            from build_info_generated import MODEL_COMPATIBILITY
            value=MODEL_COMPATIBILITY[scope]
            if isinstance(value,str) and len(value)==64 and all(c in '0123456789abcdef' for c in value):return value
        except (ImportError,KeyError,TypeError):pass
        # Older/unidentified packages retain the original strict build boundary.
        from candidate_library import code_id
        return code_id()
    return _runtime_source_identity()
