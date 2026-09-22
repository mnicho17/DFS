"""Versioned, verified incremental backups. Run after the DFS app has closed."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import time
import uuid


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def inventory(source):
    result = {}
    for path in sorted(source.rglob('*')):
        if not path.resolve().is_relative_to(source) or path.is_symlink():
            raise ValueError('History contains a link outside the backup tree or a symbolic link')
        if path.is_file():
            stat = path.stat()
            result[path.relative_to(source).as_posix()] = (stat.st_size, stat.st_mtime_ns)
    return result


def object_path(root, sha):
    if not re.fullmatch('[0-9a-f]{64}', str(sha)):
        raise ValueError('Invalid backup content ID')
    path = root / 'objects' / sha[:2] / sha
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Backup content path escapes the backup folder')
    return path


def backup_history(source, destination, *, verify_existing=False, progress=print):
    source = Path(source).resolve()
    destination = Path(destination).resolve()
    if not source.is_dir() or not destination.is_dir():
        raise ValueError('History and USB backup folders must already exist')
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError('History and backup folders must not overlap')
    root = destination / 'incremental-v1'
    if not root.resolve().is_relative_to(destination):
        raise ValueError('Backup store escapes the destination')
    started = time.perf_counter()
    before = inventory(source)
    if not before:
        raise ValueError('History is empty; no backup was created')
    entries = {}; copied = reused = written = 0
    source_seconds = copy_seconds = verify_seconds = 0.0
    for index, (name, stamp) in enumerate(before.items(), 1):
        path = source / name
        t = time.perf_counter(); sha = digest(path); source_seconds += time.perf_counter()-t
        obj = object_path(root, sha)
        if obj.exists():
            if obj.stat().st_size != stamp[0]:
                raise ValueError('Existing backup content has an unexpected size; run verification')
            if verify_existing:
                t=time.perf_counter()
                if digest(obj) != sha:
                    raise ValueError('Existing backup content failed verification')
                verify_seconds += time.perf_counter()-t
            reused += 1
        else:
            obj.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=obj.parent, suffix='.partial')
            os.close(fd)
            try:
                t=time.perf_counter();shutil.copyfile(path, temporary);copy_seconds += time.perf_counter()-t
                t=time.perf_counter()
                if digest(temporary) != sha:
                    raise ValueError('New backup content failed verification or source changed')
                verify_seconds += time.perf_counter()-t
                os.replace(temporary, obj)
            finally:
                if os.path.exists(temporary):os.unlink(temporary)
            copied += 1; written += stamp[0]
        entries[name] = dict(sha256=sha, size=stamp[0], mtime_ns=stamp[1])
        if index == 1 or index % 100 == 0 or index == len(before):
            progress(f'History backup: {index}/{len(before)} files; {copied} new, {reused} reused')
    if inventory(source) != before:
        raise ValueError('History changed during backup; close DFS before retrying. Earlier backups are unchanged.')
    stats = dict(files=len(entries), copied=copied, reused=reused, bytes_written=written,
        source_hash_seconds=round(source_seconds, 3), copy_seconds=round(copy_seconds, 3),
        verification_seconds=round(verify_seconds, 3), total_seconds=round(time.perf_counter()-started, 3),
        reverified_existing=verify_existing)
    manifest = dict(schema_version=1, created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        entries=entries, stats=stats)
    manifests = root / 'manifests'
    if not manifests.resolve().is_relative_to(root.resolve()):raise ValueError('Manifest folder escapes backup store')
    manifests.mkdir(parents=True, exist_ok=True)
    target = manifests / (datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'-'+uuid.uuid4().hex[:8]+'.json')
    fd, temporary = tempfile.mkstemp(dir=manifests, suffix='.partial')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as handle:
            json.dump(manifest, handle, sort_keys=True)
        os.replace(temporary,target)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)
    return dict(manifest=str(target), **stats)


def restore_history(manifest_path, target):
    manifest_path=Path(manifest_path).resolve();root=manifest_path.parent.parent
    value=json.loads(manifest_path.read_text(encoding='utf-8'))
    if value.get('schema_version')!=1 or not isinstance(value.get('entries'),dict) or not value['entries']:
        raise ValueError('Invalid backup manifest')
    target=Path(target).resolve()
    if target.exists():raise ValueError('Restore into a new folder, not an existing installation')
    if target.is_relative_to(root) or root.is_relative_to(target):raise ValueError('Restore must be outside the backup store')
    checked=[]
    for name,entry in value['entries'].items():
        rel=PurePosixPath(name)
        if rel.is_absolute() or any(part in ('..','.') for part in rel.parts) or ':' in name or '\\' in name:
            raise ValueError('Unsafe restore path')
        if not rel.parts:raise ValueError('Empty restore path')
        obj=object_path(root,entry['sha256'])
        if obj.stat().st_size!=entry['size'] or digest(obj)!=entry['sha256']:
            raise ValueError('Backup content failed restore verification')
        checked.append((rel,obj))
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary=Path(tempfile.mkdtemp(dir=target.parent,prefix='dfs-restore-'))
    try:
        for rel,obj in checked:
            out=temporary.joinpath(*rel.parts);out.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(obj,out)
        temporary.rename(target)
    finally:
        if temporary.exists():
            if temporary.resolve().parent != target.parent or not temporary.name.startswith("dfs-restore-"):
                raise ValueError("Unexpected temporary restore path; cleanup refused")
            shutil.rmtree(temporary)
    return len(checked)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source');parser.add_argument('--destination');parser.add_argument('--verify-existing',action='store_true')
    parser.add_argument('--restore');parser.add_argument('--target')
    args=parser.parse_args()
    if args.restore:
        if not args.target:parser.error('--target is required with --restore')
        print(f'Restored {restore_history(args.restore,args.target)} files.')
    else:
        if not args.source or not args.destination:parser.error('--source and --destination are required')
        print(json.dumps(backup_history(args.source,args.destination,verify_existing=args.verify_existing),indent=2))
