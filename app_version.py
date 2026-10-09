"""Running-build identity, independent of the working directory or network."""
import subprocess
import sys
from pathlib import Path

RELEASE_URL = 'https://github.com/mnicho17/DFS/releases/latest'


def _git(*args):
    return subprocess.check_output(
        ['git', *args], cwd=Path(__file__).resolve().parent, text=True,
        stderr=subprocess.DEVNULL, timeout=2,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).strip()


def identity():
    if not getattr(sys, 'frozen', False):
        try:
            description = _git('describe', '--tags', '--long', '--always')
            commit = _git('rev-parse', 'HEAD')
            dirty = bool(_git('status', '--porcelain', '--untracked-files=no'))
            parts = description.rsplit('-', 2)
            version = (parts[0] + ('+' + parts[1] if parts[1] != '0' else '')
                       if len(parts) == 3 and parts[1].isdigit() else 'Development')
            return dict(version=version, commit=commit, modified=dirty, source='source')
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        from build_info_generated import BUILD_INFO
        return dict(BUILD_INFO, source='packaged')
    except ImportError:
        return dict(version='Version unavailable', commit='', modified=False, source='unknown')


def label(info):
    return (info['version'] + (' · ' + info['commit'][:7] if info.get('commit') else '')
            + (' · modified' if info.get('modified') else ''))
