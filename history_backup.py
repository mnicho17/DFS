"""Versioned, verified incremental backups. Closed-app copies and optional consistent live SQLite snapshots."""
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
import sqlite3
from contextlib import closing


class BackupCancelled(Exception):
    pass


class HistoryChanged(ValueError):
    pass


def check_cancelled(cancelled):
    if cancelled():
        raise BackupCancelled('Backup paused before publishing a new manifest.')


def digest(path, progress=None, cancelled=lambda:False):
    h = hashlib.sha256()
    size=Path(path).stat().st_size; done=0; last=time.perf_counter()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            check_cancelled(cancelled)
            h.update(chunk)
            done+=len(chunk)
            if progress and time.perf_counter()-last >= 5:
                progress(done,size);last=time.perf_counter()
    return h.hexdigest()


def inventory(source, cancelled=lambda:False):
    result = {}
    for path in source.rglob('*'):
        check_cancelled(cancelled)
        if not path.resolve().is_relative_to(source) or path.is_symlink():
            raise ValueError('History contains a link outside the backup tree or a symbolic link')
        if path.is_file():
            stat = path.stat()
            result[path.relative_to(source).as_posix()] = (stat.st_size, stat.st_mtime_ns,stat.st_ctime_ns,stat.st_dev,stat.st_ino)
    return result


def object_path(root, sha):
    if not re.fullmatch('[0-9a-f]{64}', str(sha)):
        raise ValueError('Invalid backup content ID')
    path = root / 'objects' / sha[:2] / sha
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Backup content path escapes the backup folder')
    return path


def previous_entries(root, source_id):
    """Only the newest complete, same-source receipt can authorize a fast skip."""
    paths=sorted((root/'manifests').glob('*.json'),reverse=True)
    if not paths:
        return {}
    path=paths[0]
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Backup manifest escapes the backup store')
    try:
        value=json.loads(path.read_text(encoding='utf-8'))
        if value.get('schema_version')==1 and value.get('source_id')==source_id and isinstance(value.get('entries'),dict):
            return value['entries']
    except (OSError,ValueError,TypeError):
        pass
    return {}


def copy_progress(source, target, progress, cancelled=lambda:False):
    size=source.stat().st_size;done=0;last=time.perf_counter()
    with source.open('rb') as reader,open(target,'wb') as writer:
        while chunk:=reader.read(1024*1024):
            check_cancelled(cancelled)
            writer.write(chunk);done+=len(chunk)
            if time.perf_counter()-last >= 5:
                progress(done,size);last=time.perf_counter()


def latest_manifest(root, source_id):
    paths=sorted((root/'manifests').glob('*.json'),reverse=True)
    if not paths:return None, {}
    path=paths[0]
    if not path.resolve().is_relative_to(root.resolve()):raise ValueError('Backup manifest escapes the backup store')
    try:
        value=json.loads(path.read_text(encoding='utf-8'))
        if value.get('schema_version')==1 and value.get('source_id')==source_id and isinstance(value.get('entries'),dict):
            return path,value
    except (OSError,ValueError,TypeError):pass
    return None,{}


def backup_history(source, destination, *, verify_existing=False, progress=print,
                   live=None, skip_if_current=True, cancelled=lambda:False, observed_paths=None):
    source=Path(source).resolve();destination=Path(destination).resolve()
    if not source.is_dir() or not destination.is_dir():
        raise ValueError('History and backup folders must already exist')
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError('History and backup folders must not overlap')
    # Shared by idle workers, other instances, and the post-exit CLI. Qt checks
    # process ownership and recovers abandoned process locks; never wait on it.
    from PyQt5.QtCore import QLockFile
    lock=QLockFile(str(destination/'.dfs-history-backup.lock'))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        if lock.error()==QLockFile.LockFailedError:
            raise ValueError('Another backup owns this destination. Retry after it finishes.')
        raise OSError('Backup destination could not be locked. Check drive availability and write access.')
    try:
        return _backup_history(source,destination,verify_existing=verify_existing,progress=progress,
            live=live,skip_if_current=skip_if_current,cancelled=cancelled,observed_paths=observed_paths)
    finally:lock.unlock()


def _backup_history(source, destination, *, verify_existing, progress, live, skip_if_current, cancelled, observed_paths):
    root=destination/'incremental-v1'
    if not root.resolve().is_relative_to(destination):raise ValueError('Backup store escapes the destination')
    started=time.perf_counter();check_cancelled(cancelled)
    before=inventory(source,cancelled)
    if observed_paths is not None:
        watched={str(source)}
        for name in before:
            path=source/name
            for candidate in (path.parent,path):
                if len(watched)<4096:watched.add(str(candidate))
        observed_paths.extend(sorted(watched))
    if not before:raise ValueError('History is empty; no backup was created')
    source_id=hashlib.sha256(os.path.normcase(str(source)).encode()).hexdigest()
    prior_path,prior_manifest=latest_manifest(root,source_id)
    prior=prior_manifest.get('entries',{}) if not verify_existing else {}
    verified_existing=set();prior_verification_seconds=0.0
    if verify_existing:
        t=time.perf_counter()
        for name,entry in prior_manifest.get('entries',{}).items():
            check_cancelled(cancelled)
            obj=object_path(root,entry.get('sha256'))
            if obj.stat().st_size!=entry.get('size'):raise ValueError('Existing backup content failed verification')
            if entry['sha256'] not in verified_existing:
                progress(f'History backup: verifying previous content {name}')
                if digest(obj,lambda done,total:progress(f'History backup: verifying previous {name}: {done}/{total} bytes'),cancelled)!=entry['sha256']:
                    raise ValueError('Existing backup content failed verification')
                verified_existing.add(entry['sha256'])
        prior_verification_seconds=time.perf_counter()-t
    if live is None:live=bool(prior_manifest.get('sqlite_safe'))
    databases={name for name,entry in prior.items() if live and prior_manifest.get('sqlite_safe')
        and isinstance(entry,dict) and entry.get('sqlite_snapshot')
        and entry.get('source_stamp')==list(before.get(name,()))}
    known_sidecars={name+suffix for name in databases for suffix in ('-wal','-shm','-journal')}
    if live:
        for name,stamp in sorted(before.items(),key=lambda row:(row[0].endswith(('-wal','-shm','-journal')),row[0])):
            check_cancelled(cancelled)
            if name in known_sidecars:continue
            old=prior.get(name,{})
            if (prior_manifest.get('sqlite_safe') and isinstance(old,dict)
                    and old.get('source_stamp')==list(stamp)):
                if old.get('sqlite_snapshot'):databases.add(name)
                continue
            # Detect SQLite by content, not a filename extension. Unchanged
            # files reuse their prior classification without reading content.
            with (source/name).open('rb') as handle:
                if handle.read(16)==b'SQLite format 3\x00':
                    databases.add(name)
                    known_sidecars.update(name+suffix for suffix in ('-wal','-shm','-journal'))
    # SQLite's shared-memory read marks are not persistent database content.
    # WAL/journal stamps still participate in the final source-change check.
    def effective(rows):
        return {n:stamp for n,stamp in rows.items() if not any(n==db+'-shm' for db in databases)}
    before=effective(before)
    omitted={db+suffix for db in databases for suffix in ('-wal','-shm','-journal')}
    planned={n:stamp for n,stamp in before.items() if n not in omitted}
    stamp_receipt={n:list(stamp) for n,stamp in before.items()}
    if (skip_if_current and not verify_existing and prior_manifest.get('sqlite_safe')==bool(live)
            and prior_manifest.get('source_inventory')==stamp_receipt and set(prior)==set(planned)):
        intact=True
        for entry in prior.values():
            check_cancelled(cancelled)
            try:
                obj=object_path(root,entry['sha256'])
                if not obj.is_file() or obj.stat().st_size!=entry['size']:intact=False;break
            except (KeyError,TypeError,ValueError,OSError):intact=False;break
        if intact and effective(inventory(source,cancelled))==before:
            check_cancelled(cancelled)
            progress('History backup: no changes since the last complete manifest; no content read or copied.')
            return dict(manifest=str(prior_path),files=len(prior),copied=0,reused=len(prior),
                        skipped_unchanged=len(prior),source_files_hashed=0,bytes_written=0,
                        unchanged=True,source_hash_seconds=0.0,copy_seconds=0.0,verification_seconds=0.0,
                        reverified_existing=False,sqlite_snapshots=0,total_seconds=round(time.perf_counter()-started,3))
    entries={};copied=reused=written=skipped=hashed=snapshots=0
    source_seconds=copy_seconds=0.0;verify_seconds=prior_verification_seconds
    for index,(name,stamp) in enumerate(planned.items(),1):
        check_cancelled(cancelled)
        path=source/name;old=prior.get(name,{})
        sqlite_mode=name in databases
        wal_stamp=list(before[name+'-wal']) if name+'-wal' in before else None
        journal_stamp=list(before[name+'-journal']) if name+'-journal' in before else None
        sha=None
        if (isinstance(old,dict) and old.get('source_stamp')==list(stamp)
                and bool(old.get('sqlite_snapshot'))==sqlite_mode
                and (not sqlite_mode or (old.get('wal_stamp')==wal_stamp and old.get('journal_stamp')==journal_stamp))):
            try:
                obj=object_path(root,old['sha256'])
                if obj.is_file() and obj.stat().st_size==(old.get('size') if sqlite_mode else stamp[0]):
                    sha=old['sha256'];skipped+=1
            except (ValueError,KeyError,TypeError):pass
        def bytes_progress(phase):
            return lambda done,total:progress(f'History backup: {phase} {name}: {done/1024**2:,.0f}/{total/1024**2:,.0f} MiB')
        staged=None
        try:
            if sha is None:
                progress(f'History backup: checking {index}/{len(planned)}: {name} ({stamp[0]/1024**2:,.1f} MiB)')
                if sqlite_mode:
                    root.mkdir(parents=True,exist_ok=True)
                    fd,temporary=tempfile.mkstemp(dir=root,suffix='.sqlite.partial');os.close(fd);staged=Path(temporary)
                    last_sqlite_progress=[time.perf_counter()]
                    progress(f'History backup: snapshotting SQLite {name}')
                    def sqlite_progress(status,remaining,total):
                        check_cancelled(cancelled)
                        now=time.perf_counter()
                        if remaining==0 or now-last_sqlite_progress[0]>=5:
                            progress(f'History backup: snapshotting {name}: {total-remaining:,}/{total:,} pages')
                            last_sqlite_progress[0]=now
                    t=time.perf_counter()
                    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=1)) as reader, closing(sqlite3.connect(staged)) as writer:
                        reader.backup(writer,pages=256,progress=sqlite_progress,sleep=.05)
                    copy_seconds+=time.perf_counter()-t;snapshots+=1
                    path=staged
                t=time.perf_counter();sha=digest(path,bytes_progress('hashing'),cancelled);source_seconds+=time.perf_counter()-t;hashed+=1
            obj=object_path(root,sha)
            size=path.stat().st_size if sha is not None and staged is not None else (old.get('size',stamp[0]) if sqlite_mode and sha==old.get('sha256') else stamp[0])
            if obj.exists():
                if obj.stat().st_size!=size:raise ValueError('Existing backup content has an unexpected size; run verification')
                if verify_existing and sha not in verified_existing:
                    t=time.perf_counter()
                    if digest(obj,bytes_progress('verifying existing'),cancelled)!=sha:raise ValueError('Existing backup content failed verification')
                    verify_seconds+=time.perf_counter()-t
                reused+=1
            else:
                obj.parent.mkdir(parents=True,exist_ok=True)
                if staged is not None:
                    temporary=str(staged)
                else:
                    fd,temporary=tempfile.mkstemp(dir=obj.parent,suffix='.partial');os.close(fd)
                try:
                    if staged is None:
                        progress(f'History backup: copying {name} ({size/1024**2:,.1f} MiB)')
                        t=time.perf_counter();copy_progress(path,temporary,bytes_progress('copying'),cancelled);copy_seconds+=time.perf_counter()-t
                    t=time.perf_counter()
                    if digest(temporary,bytes_progress('verifying new'),cancelled)!=sha:raise ValueError('New backup content failed verification or source changed')
                    verify_seconds+=time.perf_counter()-t
                    check_cancelled(cancelled);os.replace(temporary,obj)
                finally:
                    if os.path.exists(temporary):os.unlink(temporary)
                copied+=1;written+=size
            entries[name]=dict(sha256=sha,size=size,mtime_ns=stamp[1],source_stamp=list(stamp))
            if sqlite_mode:entries[name].update(sqlite_snapshot=True,wal_stamp=wal_stamp,journal_stamp=journal_stamp)
        finally:
            if staged is not None and staged.exists():staged.unlink()
        if index==1 or index%100==0 or index==len(planned):
            progress(f'History backup: {index}/{len(planned)} files; {copied} new, {reused} reused; {skipped} unchanged files skipped without reading')
    if effective(inventory(source,cancelled))!=before:
        raise HistoryChanged('History changed during backup; retry when idle. Earlier backups are unchanged.')
    check_cancelled(cancelled)
    stats=dict(files=len(entries),copied=copied,reused=reused,skipped_unchanged=skipped,source_files_hashed=hashed,
        sqlite_snapshots=snapshots,bytes_written=written,source_hash_seconds=round(source_seconds,3),
        copy_seconds=round(copy_seconds,3),verification_seconds=round(verify_seconds,3),
        total_seconds=round(time.perf_counter()-started,3),reverified_existing=verify_existing)
    manifest=dict(schema_version=1,source_id=source_id,created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        entries=entries,stats=stats,sqlite_safe=bool(live),source_inventory=stamp_receipt)
    manifests=root/'manifests'
    if not manifests.resolve().is_relative_to(root.resolve()):raise ValueError('Manifest folder escapes backup store')
    manifests.mkdir(parents=True,exist_ok=True)
    target=manifests/(datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'-'+uuid.uuid4().hex[:8]+'.json')
    fd,temporary=tempfile.mkstemp(dir=manifests,suffix='.partial')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as handle:json.dump(manifest,handle,sort_keys=True)
        check_cancelled(cancelled);os.replace(temporary,target)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)
    return dict(manifest=str(target),**stats)


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
    parser.add_argument('--live',action='store_const',const=True,default=None,help='Snapshot SQLite databases safely while DFS is open')
    parser.add_argument('--skip-if-current',action='store_true',default=True,help='Reuse the latest complete manifest when inventory is unchanged')
    parser.add_argument('--restore');parser.add_argument('--target')
    args=parser.parse_args()
    if args.restore:
        if not args.target:parser.error('--target is required with --restore')
        print(f'Restored {restore_history(args.restore,args.target)} files.')
    else:
        if not args.source or not args.destination:parser.error('--source and --destination are required')
        print(json.dumps(backup_history(args.source,args.destination,verify_existing=args.verify_existing,live=args.live,skip_if_current=args.skip_if_current),indent=2))
