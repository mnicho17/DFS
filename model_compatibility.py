"""Conservative model compatibility separate from build/evidence identity.

Only audited presentation surfaces are omitted. Unknown production modules remain
strict. Full code_id() continues to qualify saved SIM/evidence artifacts.
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
SCOPES = ('ownership','preparation')


def source_identity(root):
    sources={}
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
            tree=ast.parse(path.read_text(encoding='utf-8-sig'))
            if relative=='main_window.py':
                # The worker classes, globals/imports and pure helpers stay strict.
                tree.body=[node for node in tree.body if not isinstance(node,ast.ClassDef) or node.name!='MainWindow']
            sources[relative]=hashlib.sha256(ast.dump(tree,include_attributes=False).encode()).hexdigest()
    dependencies=sorted((str(d.metadata.get('Name','')).lower(),d.version)
                        for d in importlib.metadata.distributions())
    payload=dict(policy=1,sources=sources,python=list(sys.version_info[:3]),
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
