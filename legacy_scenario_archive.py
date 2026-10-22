"""Verified, recoverable schema-1 scenario archives; never authorize SIM replay."""
from array import array
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import struct
import sys
import tempfile
import time
import zlib
from scenario_cache_storage import CACHE_NAME,Lease,plain_file,inventory

VERSION=1
MAX_FILES=64
MAX_BYTES=512*1024**2
MAX_FRAME=4*1024**2
MARKER={'schema':1,'purpose':'recoverable-legacy-scenario-cache'}


class Stopped(Exception):pass


def stamp(path):
    s=path.stat()
    return dict(size=s.st_size,mtime_ns=s.st_mtime_ns,ctime_ns=s.st_ctime_ns,device=s.st_dev,inode=s.st_ino)


def guard(cancelled,deadline):
    if cancelled():raise Stopped('Cancelled; completed recovery archives remain available and unfinished retirements are skipped.')
    if time.monotonic()>deadline:raise Stopped('Validation time allowance reached; retry to continue.')


def file_hash(path,cancelled,deadline):
    digest=hashlib.sha256();size=0
    with path.open('rb') as handle:
        while chunk:=handle.read(1024**2):
            guard(cancelled,deadline);size+=len(chunk)
            if size>MAX_BYTES:raise ValueError('Artifact exceeds legacy size bound')
            digest.update(chunk)
    return digest.hexdigest()


def metadata(con,path):
    tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 3")}
    schema={name:[tuple(row[1:6]) for row in con.execute('PRAGMA table_info('+name+')')]
            for name in ('meta','frames')}
    if tables!={'meta','frames'} or schema!={
        'frames':[('id','INTEGER',0,None,1),('payload','BLOB',0,None,0),('scores','BLOB',0,None,0)],
        'meta':[('key','TEXT',0,None,1),('value','TEXT',0,None,0)]}:
        raise ValueError('Not the original scenario-cache schema')
    meta=dict(con.execute('SELECT key,value FROM meta LIMIT 4'))
    if (set(meta)!={'key','count','digest'} or meta['key']!=path.stem
            or not re.fullmatch('[0-9a-f]{64}',meta['digest']) or not 0<int(meta['count'])<=100000):
        raise ValueError('Not completed legacy scenario metadata')
    return meta


def inspect_legacy(path,*,cancelled=lambda:False,deadline=None,progress=lambda text:None):
    """Read every frame, its original digest and all bytes. No filesystem writes."""
    path=Path(path);path=path.parent.resolve()/path.name;deadline=deadline or time.monotonic()+120
    if not CACHE_NAME.fullmatch(path.name) or not plain_file(path,path.parent):raise ValueError('Not an ordinary legacy cache file')
    before=stamp(path)
    if before['size']>MAX_BYTES:raise ValueError('Legacy artifact exceeds size bound')
    if any(Path(str(path)+suffix).exists() for suffix in ('-wal','-journal')):raise ValueError('Active or unfinished database preserved')
    con=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=0)
    try:
        con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,MAX_FRAME)
        con.set_progress_handler(lambda:int(cancelled() or time.monotonic()>deadline),1000)
        con.execute('BEGIN');meta=metadata(con,path)
        digest=hashlib.sha256();seen=0;keys=None
        for index,payload,scores in con.execute('SELECT id,payload,scores FROM frames ORDER BY id'):
            guard(cancelled,deadline)
            if index!=seen or seen>=int(meta['count']) or not isinstance(payload,bytes) or not isinstance(scores,bytes):raise ValueError('Invalid frame sequence or blob type')
            decoder=zlib.decompressobj();raw=decoder.decompress(payload,MAX_FRAME)
            if not decoder.eof or decoder.unused_data:raise ValueError('Invalid legacy outcome compression')
            decoded=json.loads(raw)
            if not isinstance(decoded,list) or len(decoded)!=2:raise ValueError('Invalid outcome frame')
            outcomes,scripts=decoded
            if (not isinstance(outcomes,dict) or not 0<len(outcomes)<=4096
                    or any(not isinstance(k,str) or not k or len(k)>512 for k in outcomes)
                    or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in outcomes.values())):
                raise ValueError('Invalid legacy player outcomes')
            if keys is None:keys=set(outcomes)
            if set(outcomes)!=keys:raise ValueError('Legacy player coverage changed')
            if (not isinstance(scripts,dict) or len(scripts)>128 or any(not isinstance(k,str) or len(k)>256 for k in scripts)
                    or any(isinstance(v,bool) or not isinstance(v,int) or not 0<=v<=10**9 for v in scripts.values())):
                raise ValueError('Invalid script counters')
            if not scores or len(scores)%8 or len(scores)>MAX_FRAME:raise ValueError('Invalid raw double scores')
            values=array('d');values.frombytes(scores)
            if not all(math.isfinite(v) for v in values) or not all(a<=b for a,b in zip(values,values[1:])):
                raise ValueError('Nonfinite or unsorted native-byte-order scores')
            digest.update(struct.pack('<QQ',len(payload),len(scores)));digest.update(payload);digest.update(scores);seen+=1
            if seen%100==0:progress(f'Validated {seen:,}/{int(meta["count"]):,} legacy frames')
        if seen!=int(meta['count']) or digest.hexdigest()!=meta['digest']:raise ValueError('Legacy coverage or digest mismatch')
        sha=file_hash(path,cancelled,deadline);guard(cancelled,deadline)
        if before!=stamp(path):raise ValueError('Legacy source changed during validation')
        return dict(version=VERSION,key=meta['key'],count=int(meta['count']),digest=meta['digest'],
                    source_stamp=before,file_sha256=sha,byteorder=sys.byteorder,source_root=str(path.parent))
    finally:con.close()


def read_json(path,limit=16384):
    if not plain_file(path,path.parent) or path.stat().st_size>limit:raise ValueError('Invalid archive metadata')
    value=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value,dict):raise ValueError('Archive metadata must be an object')
    return value


def safe_directory(path):
    path.mkdir(exist_ok=True)
    if path.is_symlink() or path.resolve()!=path or getattr(path.stat(),'st_file_attributes',0)&0x400:
        raise ValueError('Archive directory link preserved')


def publish_file(temporary,target):
    """Atomic publication without replacing an existing name."""
    if os.name=='nt':os.rename(temporary,target)
    else:
        os.link(temporary,target)
        temporary.unlink()


def publish_json(path,value):
    if path.exists():
        if read_json(path)!=value:raise ValueError('Existing archive metadata differs')
        return
    fd,name=tempfile.mkstemp(dir=path.parent,prefix='.manifest-',suffix='.partial');temporary=Path(name)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as handle:
            json.dump(value,handle,sort_keys=True);handle.flush();os.fsync(handle.fileno())
        publish_file(temporary,path)
    finally:
        if temporary.exists() and plain_file(temporary,path.parent):temporary.unlink()


def archive_verified(path,proof,destination,cancelled,deadline):
    source=path.parent;destination=Path(destination).resolve()
    if not destination.is_dir() or destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError('Choose an existing backup folder outside the cache')
    root=destination/'legacy-scenario-cache-v1'
    if root.exists() and (not root.is_dir() or (not (root/'format.json').exists() and any(root.iterdir()))):
        raise ValueError('Unrecognized archive folder preserved')
    safe_directory(root);publish_json(root/'format.json',MARKER)
    objects=root/'objects';manifests=root/'manifests';safe_directory(objects);safe_directory(manifests)
    sha=proof['file_sha256'];target=objects/(sha+'.sqlite');temporary=None
    try:
        if target.exists():
            if not plain_file(target,objects) or file_hash(target,cancelled,deadline)!=sha:raise ValueError('Existing archive object failed verification')
        else:
            fd,name=tempfile.mkstemp(dir=objects,prefix='.legacy-',suffix='.partial');temporary=Path(name)
            digest=hashlib.sha256();size=0
            with os.fdopen(fd,'wb') as writer,path.open('rb') as reader:
                while chunk:=reader.read(1024**2):
                    guard(cancelled,deadline);size+=len(chunk)
                    if size>MAX_BYTES:raise ValueError('Source grew during archival')
                    writer.write(chunk);digest.update(chunk)
                writer.flush();os.fsync(writer.fileno())
            if digest.hexdigest()!=sha or proof['source_stamp']!=stamp(path):raise ValueError('Source changed during archival')
            if file_hash(temporary,cancelled,deadline)!=sha:raise ValueError('Archive copy failed verification')
            guard(cancelled,deadline)
            publish_file(temporary,target);temporary=None
        guard(cancelled,deadline)
        if proof['source_stamp']!=stamp(path):raise ValueError('Source changed before archive publication')
        tag=hashlib.sha256(json.dumps(proof['source_stamp'],sort_keys=True).encode()).hexdigest()[:12]
        manifest=manifests/(proof['key']+'-'+sha+'-'+tag+'.json')
        value=dict(proof,format='schema-1-zlib-json-raw-double',object=target.name,purpose='Recover original legacy cache bytes; not current replay authority')
        publish_json(manifest,value)
        return dict(proof,archive=str(target),archive_stamp=stamp(target),manifest=str(manifest),
                    manifest_stamp=stamp(manifest),archive_root=str(root),marker_stamp=stamp(root/'format.json'))
    finally:
        if temporary is not None and plain_file(temporary,objects):temporary.unlink()


def reclaim_verified(path,receipt,*,cancelled,deadline):
    """Only a fresh archive receipt from this operation can retire its working copy.

    Caller holds the source key lease and destination backup lock. No stored receipt,
    age, metadata hint or restored file independently grants retirement authority.
    """
    path=Path(path);obj=Path(receipt['archive']);manifest=Path(receipt['manifest'])
    root=Path(receipt['archive_root']);marker=root/'format.json'
    guard(cancelled,deadline)
    if (not plain_file(path,path.parent) or receipt['source_stamp']!=stamp(path)
            or not plain_file(obj,root/'objects') or not plain_file(manifest,root/'manifests')
            or receipt['archive_stamp']!=stamp(obj) or receipt['manifest_stamp']!=stamp(manifest)
            or receipt['marker_stamp']!=stamp(marker) or read_json(marker)!=MARKER):
        raise ValueError('Source or recovery archive changed; working copy preserved')
    saved=read_json(manifest)
    for name in ('version','key','count','digest','source_stamp','file_sha256','source_root','byteorder'):
        if saved.get(name)!=receipt[name]:raise ValueError('Recovery manifest differs; working copy preserved')
    if (obj.name!=receipt['file_sha256']+'.sqlite' or saved.get('object')!=obj.name
            or saved.get('format')!='schema-1-zlib-json-raw-double'):
        raise ValueError('Recovery object identity differs; working copy preserved')
    if (file_hash(obj,cancelled,deadline)!=receipt['file_sha256']
            or file_hash(path,cancelled,deadline)!=receipt['file_sha256']):
        raise ValueError('Recovery or source bytes differ; working copy preserved')
    con=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=0)
    try:
        con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,MAX_FRAME)
        con.set_progress_handler(lambda:int(cancelled() or time.monotonic()>deadline),1000)
        con.execute('BEGIN EXCLUSIVE')
        meta=metadata(con,path)
        if meta['digest']!=receipt['digest'] or int(meta['count'])!=receipt['count']:
            raise ValueError('Legacy metadata changed; working copy preserved')
    finally:con.close()
    # Windows refuses removal with incompatible open handles. Cooperative app readers
    # remain excluded by the caller's key lease throughout archive and retirement.
    guard(cancelled,deadline)
    if (not plain_file(path,path.parent) or receipt['source_stamp']!=stamp(path)
            or not plain_file(obj,obj.parent) or receipt['archive_stamp']!=stamp(obj)
            or not plain_file(manifest,manifest.parent) or receipt['manifest_stamp']!=stamp(manifest)
            or not plain_file(marker,root) or receipt['marker_stamp']!=stamp(marker)):
        raise ValueError('Source or recovery archive changed before retirement')
    path.unlink()
    return receipt['source_stamp']['size']


def validate_and_archive(folder,destination,*,protected=(),cancelled=lambda:False,progress=lambda text:None,seconds=120,reclaim=False):
    """Explicit operation; optional retirement follows fresh verified archival only."""
    from PyQt5.QtCore import QLockFile
    root=Path(folder).resolve();destination=Path(destination).resolve();deadline=time.monotonic()+seconds
    protected=[Path(p).resolve() for p in protected]
    archive_root=destination/'legacy-scenario-cache-v1'
    if any(archive_root==p or p in archive_root.parents or archive_root in p.parents for p in protected):
        raise ValueError('Backup archive overlaps a protected research or preparation location')
    if not destination.is_dir():raise ValueError('Configure an available backup folder first')
    lock=QLockFile(str(destination/'.dfs-history-backup.lock'));lock.setStaleLockTime(0)
    if not lock.tryLock(0):raise ValueError('Another backup or archive operation owns this destination')
    result=dict(archived=0,reclaimed_files=0,reclaimed_bytes=0,preserved=0,errors=0,cancelled=False,complete=True,bytes_archived=0,archive_root=str(archive_root),messages=[])
    try:
        files=sorted(inventory(root),key=lambda item:(item[2],item[0].name));processed=0
        for path,_,_ in files:
            try:guard(cancelled,deadline)
            except Stopped as exc:result.update(cancelled=cancelled(),complete=False);result['messages'].append(str(exc));break
            if not CACHE_NAME.fullmatch(path.name) or not plain_file(path,root):continue
            if any(path==p or p in path.parents for p in protected):result['preserved']+=1;continue
            if processed>=MAX_FILES:result['complete']=False;result['messages'].append('File bound reached; retry to continue.');break
            from storage_audit import cache_hint
            if cache_hint(path,cancelled)!='legacy':result['preserved']+=1;continue
            processed+=1;lease=Lease(root,path.stem+'.lock')
            if not lease:result['preserved']+=1;continue
            try:
                progress(f'Checking legacy artifact {processed}; recovery copy must verify before retirement' if reclaim else f'Checking legacy artifact {processed}; source files remain in place')
                proof=inspect_legacy(path,cancelled=cancelled,deadline=deadline,progress=progress)
                receipt=archive_verified(path,proof,destination,cancelled,deadline)
                result['archived']+=1;result['bytes_archived']+=proof['source_stamp']['size']
                guard(cancelled,deadline)
                if reclaim:
                    size=reclaim_verified(path,receipt,cancelled=cancelled,deadline=deadline)
                    result['reclaimed_files']+=1;result['reclaimed_bytes']+=size
            except Stopped as exc:
                result.update(cancelled=cancelled(),complete=False);result['messages'].append(str(exc));break
            except (OSError,sqlite3.Error,ValueError,TypeError,KeyError,zlib.error,OverflowError) as exc:
                if cancelled() or time.monotonic()>deadline:
                    result.update(cancelled=cancelled(),complete=False)
                    result['messages'].append('Validation stopped; working files remain in place.');break
                result['preserved']+=1;result['errors']+=1
                result['messages'].append(path.name+': '+str(exc)[:240])
            finally:lease.close()
        return result
    finally:lock.unlock()


def restore(manifest,destination,*,cancelled=lambda:False):
    """Restore exact original bytes into a new file; never overwrite a working copy."""
    manifest=Path(manifest);manifest=manifest.parent.resolve()/manifest.name;root=manifest.parent.parent;value=read_json(manifest)
    if value.get('version')!=VERSION or value.get('format')!='schema-1-zlib-json-raw-double' or manifest.parent!=root/'manifests' or read_json(root/'format.json')!=MARKER:raise ValueError('Invalid archive manifest')
    key=value['key'];sha=value['file_sha256']
    if not re.fullmatch('[0-9a-f]{64}',key) or not re.fullmatch('[0-9a-f]{64}',sha) or value['object']!=sha+'.sqlite':raise ValueError('Invalid archive identity')
    obj=root/'objects'/value['object'];destination=Path(destination).resolve()
    if destination.is_relative_to(root) or root.is_relative_to(destination) or not destination.is_dir():raise ValueError('Restore into an existing folder outside the archive')
    if not plain_file(obj,obj.parent):raise ValueError('Archive object link preserved')
    target=destination/(key+'.sqlite')
    if target.exists():raise ValueError('Restore target already exists; preserved')
    deadline=time.monotonic()+120;temporary=None
    try:
        fd,name=tempfile.mkstemp(dir=destination,prefix='.legacy-restore-',suffix='.partial');temporary=Path(name)
        digest=hashlib.sha256();size=0
        with os.fdopen(fd,'wb') as writer,obj.open('rb') as reader:
            while chunk:=reader.read(1024**2):
                guard(cancelled,deadline);size+=len(chunk)
                if size>MAX_BYTES:raise ValueError('Archive exceeds size bound')
                writer.write(chunk);digest.update(chunk)
            writer.flush();os.fsync(writer.fileno())
        if digest.hexdigest()!=sha:raise ValueError('Archive object failed SHA-256 verification')
        guard(cancelled,deadline);publish_file(temporary,target);temporary=None
        return str(target)
    finally:
        if temporary is not None and plain_file(temporary,destination):temporary.unlink()
