"""Bounded retirement of disposable scenario artifacts, coordinated across processes."""
import os
import hashlib
import re
import sqlite3
import stat
import time

CACHE_NAME=re.compile(r'[0-9a-f]{64}\.sqlite\Z')
PARTIAL_NAME=re.compile(r'scenario-replay-[a-z0-9_]{8}\.partial\Z')
APPLICATION_ID=0x44534653
MAX_ENTRIES=512
MAX_RETIREMENTS=64
MIN_FREE=8*1024*1024


def plain_file(path, root):
    """Never follow links or Windows junction/reparse points for retirement."""
    try:
        info=path.lstat()
        return (path.parent==root and stat.S_ISREG(info.st_mode) and not path.is_symlink()
                and info.st_nlink==1 and not getattr(info,'st_file_attributes',0)&0x400 and path.resolve()==path)
    except OSError:
        return False


class Lease:
    """Nonblocking OS lock. A crash releases ownership; the tiny lock file can persist."""
    def __init__(self, root, name):
        self.handle=None
        directory=root/'.leases'
        directory.mkdir(exist_ok=True)
        if directory.is_symlink() or directory.resolve()!=directory:
            raise OSError('Unsafe cache lock directory')
        # Fixed buckets keep lock-file count bounded; collisions only preserve extra caches.
        slot='writer.lock' if name=='writer.lock' else hashlib.sha256(name.encode()).hexdigest()[:2]+'.lock'
        path=directory/slot
        if path.exists() and not plain_file(path,directory):raise OSError('Unsafe cache lock file')
        handle=open(path,'a+b')
        try:
            if not path.stat().st_size:handle.write(b'0');handle.flush()
            handle.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return
        self.handle=handle

    def close(self):
        if self.handle is not None:
            handle=self.handle;self.handle=None
            try:
                handle.seek(0)
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
            except OSError:pass
            finally:
                try:handle.close()
                except OSError:pass

    def __bool__(self):return self.handle is not None


def inventory(root):
    """Bounded direct children only. Unknown files count toward the budget."""
    files=[]
    for index,path in enumerate(root.iterdir()):
        if index>=MAX_ENTRIES:raise OSError('Cache directory scan bound reached')
        if path.is_file():files.append((path,path.stat().st_size,path.stat().st_mtime_ns))
    return files


def recognized(path, incomplete=False):
    """Only the exact disposable schema; SQLite locks also exclude legacy readers."""
    con=None
    try:
        con=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=0)
        con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,1024*1024)
        deadline=time.monotonic()+.1
        con.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
        con.execute('BEGIN EXCLUSIVE')
        if incomplete:
            return (con.execute('PRAGMA application_id').fetchone()[0]==APPLICATION_ID
                    and {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}=={'frames','meta'})
        meta=dict(con.execute('SELECT key,value FROM meta LIMIT 5'))
        columns=[r[1] for r in con.execute('PRAGMA table_info(frames)')]
        tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if (tables!={'frames','meta'} or set(meta)!={'key','count','digest','scores_codec'} or meta['key']!=path.stem
                or meta['scores_codec']!='zlib-double-v1' or int(meta['count'])<=0
                or not re.fullmatch('[0-9a-f]{64}',meta['digest']) or columns!=['id','payload','scores']):
            return False
        return True
    except (sqlite3.Error,ValueError,TypeError):
        return False
    finally:
        if con is not None:con.close()


def make_room(root, target, store_limit, file_limit, cancelled):
    """Caller holds the writer lease until publication/abort. Reserve a full file."""
    required=max(MIN_FREE,file_limit)+MIN_FREE
    report=dict(status='not needed',retired_files=0,reclaimed_bytes=0,skipped_busy=0,
                store_limit_bytes=store_limit,required_free_bytes=required)
    files=inventory(root);used=sum(size for _,size,_ in files)
    report['used_bytes_before']=used
    if required>store_limit:
        report.update(status='storage bound',available_bytes=max(0,store_limit-used))
        return store_limit-used,report
    deadline=time.monotonic()+1.0
    for path,size,modified in sorted(files,key=lambda item:(item[2],item[0].name)):
        if store_limit-used>=required:break
        if cancelled():report['status']='cancelled';break
        if time.monotonic()>deadline or report['retired_files']>=MAX_RETIREMENTS:
            report['status']='maintenance bound';break
        incomplete=bool(PARTIAL_NAME.fullmatch(path.name))
        if path==target or not (CACHE_NAME.fullmatch(path.name) or incomplete) or not plain_file(path,root):continue
        if incomplete and time.time_ns()-modified<3600*10**9:continue
        lease=Lease(root,path.stem+'.lock')
        if not lease:
            report['skipped_busy']+=1;continue
        try:
            # Recheck under the key lease; do not retire a recently replaced artifact.
            current=path.stat()
            if current.st_size!=size or current.st_mtime_ns!=modified or not recognized(path,incomplete):continue
            if cancelled():report['status']='cancelled';break
            if time.monotonic()>deadline:report['status']='maintenance bound';break
            if not plain_file(path,root):continue
            path.unlink()
            used-=size;report['retired_files']+=1;report['reclaimed_bytes']+=size
        except OSError:
            report['skipped_busy']+=1
        finally:lease.close()
    available=store_limit-sum(size for _,size,_ in inventory(root))
    if report['status']=='not needed':
        report['status']=('space reclaimed' if report['retired_files'] else 'not needed') if available>=required else 'protected files or storage bound'
    report['available_bytes']=max(0,available)
    return available,report
