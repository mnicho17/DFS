"""Shared, side-effect-free history resolution and external report destinations."""
import os
from pathlib import Path
import sys


def data_root():
    override = os.environ.get('DFS_OPTIMIZER_DATA_DIR', '').strip()
    if override:
        return Path(override)
    if getattr(sys, 'frozen', False):
        return Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'DFS Optimizer'
    return Path(__file__).resolve().parent


def history_source_paths():
    root = data_root() / 'history'
    return root / 'exports.sqlite', root / 'build-diagnostics.json'


def review_reports_directory(*, excluded_roots=()):
    """Keep publications outside checkouts and the captured read-only sources.

    Use a sibling of history, since SourceDestination protects the entire
    history tree. A source-mode data override inside a checkout is not suitable.
    """
    source = Path(__file__).resolve().parent
    roots = [data_root(),
             Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'DFS Optimizer',
             Path.home() / '.local' / 'share' / 'DFS Optimizer']
    excluded = [source, *(Path(p).resolve() for p in excluded_roots)]
    for root in roots:
        target = (root / 'review-reports').resolve()
        if any(target.is_relative_to(p) for p in excluded):
            continue
        if any((p / '.git').exists() for p in (target, *target.parents)):
            continue
        target.mkdir(parents=True, exist_ok=True)
        return target
    raise OSError('No external review-report location is available.')
