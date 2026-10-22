"""Bounded storage observations. Never authorize deletion, move, backup or reuse."""
import datetime as dt
import heapq
import os
from pathlib import Path
import re
import sqlite3
import stat
import time

LIBRARIES={'.dfslib','.sdlib','.sdfull'}
CACHE_NAME=re.compile(r'[0-9a-f]{64}\.sqlite\Z')
POLICIES={'sources':'Keep research sources','history':'Keep research and evidence',
          'backup':'Keep recoverable backups','prepared':'Keep expensive preparation',
          'scenario':'Review disposable cache','legacy':'Keep legacy caches for review','unknown':'Keep until identified'}


def configured_roots(window):
    """Collect paths only. Do not call helpers that create folders or databases."""
    from data_paths import data_root
    from scenario_cache import cache_folder
    from build_preparation import remembered_libraries
    roots=[dict(path=str(data_root()/'history'),role='history'),dict(path=str(cache_folder()),role='scenario')]
    history=getattr(window,'history_refresh',None)
    if history and getattr(history,'db_path',None):roots.append(dict(path=str(history.db_path),role='history'))
    settings=window.settings
    for key in ('learning/results_folder','learning/salary_folder'):
        value=str(settings.value(key,'') or '').strip()
        if value:roots.append(dict(path=value,role='sources'))
    backup=getattr(window,'idle_backup',None)
    if backup:
        roots.append(dict(path=str(backup.source),role='history'))
        if backup.destination():roots.append(dict(path=backup.destination(),role='backup'))
    known=list(remembered_libraries(settings))+[str(getattr(window,'_candidate_library','') or '')]
    for value in known:
        if not value:continue
        roots.append(dict(path=value,role='prepared'))
        if Path(value).suffix.lower()=='.sdfull':roots.append(dict(path=value+'.parts',role='prepared'))
    return roots


def cache_hint(path, cancelled):
    """Small immutable metadata read; ignores WAL, so never certifies live contents."""
    if not CACHE_NAME.fullmatch(path.name):return None
    con=None
    try:
        if any(Path(str(path)+suffix).exists() for suffix in ('-wal','-journal')):return None
        before=path.stat()
        if before.st_size>512*1024**2:return None
        con=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True,timeout=0)
        con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,1024*1024)
        deadline=time.monotonic()+.05
        con.set_progress_handler(lambda:int(cancelled() or time.monotonic()>deadline),1000)
        meta=dict(con.execute('SELECT key,value FROM meta LIMIT 5'))
        columns=[r[1] for r in con.execute('PRAGMA table_info(frames)').fetchmany(4)]
        tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 3")}
        after=path.stat()
        if (before.st_size==after.st_size and before.st_mtime_ns==after.st_mtime_ns
                and columns==['id','payload','scores'] and tables=={'meta','frames'}
                and meta.get('key')==path.stem and int(meta.get('count',0))>0
                and bool(re.fullmatch('[0-9a-f]{64}',meta.get('digest','')))):
            if set(meta)=={'key','count','digest','scores_codec'} and meta['scores_codec']=='zlib-double-v1':return 'current'
            if set(meta)=={'key','count','digest'}:return 'legacy'
        return None
    except (OSError,sqlite3.Error,TypeError,ValueError):return None
    finally:
        if con is not None:con.close()


def audit(roots, *, cancelled=lambda:False, progress=lambda text:None, seconds=10,
          max_entries=20000, max_rows=500, as_of=None):
    started=time.monotonic();as_of=as_of or dt.datetime.now(dt.timezone.utc)
    if as_of.tzinfo is None:raise ValueError('Audit date needs a timezone')
    cutoff=as_of.timestamp()-30*86400
    scopes=[];issues=[]
    for spec in roots:
        try:
            path=Path(spec['path']).resolve();role=spec.get('role','unknown')
            scopes.append((path,role if role in POLICIES else 'unknown'))
        except (OSError,RuntimeError,ValueError):issues.append('A configured location could not be resolved.')
    # Narrowest explicit scope wins; protected sources/backups override cache hints on overlap.
    rank={'sources':0,'backup':1,'prepared':2,'history':3,'unknown':4,'legacy':4,'scenario':5}
    def role_for(path):
        matches=[(rank[role],-len(str(root)),role) for root,role in scopes if path==root or root in path.parents]
        return min(matches)[2] if matches else 'unknown'
    stack=[root for root,_ in scopes];seen=set();largest=[];totals={};entries=0;complete=True;metadata_reads=0
    skipped_links=0;errors=0;stopped=''
    while stack:
        if cancelled():complete=False;stopped='Cancelled';break
        if entries>=max_entries or time.monotonic()-started>=seconds:
            complete=False;stopped='Scan bound reached';break
        path=stack.pop();identity=os.path.normcase(str(path))
        if identity in seen:continue
        seen.add(identity);entries+=1
        try:
            info=path.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
                skipped_links+=1;continue
            if stat.S_ISDIR(info.st_mode):
                with os.scandir(path) as children:
                    for child in children:
                        if cancelled() or entries+len(stack)>=max_entries or time.monotonic()-started>=seconds:
                            complete=False;stopped='Cancelled' if cancelled() else 'Scan bound reached';break
                        stack.append(Path(child.path))
                continue
            if not stat.S_ISREG(info.st_mode):continue
            role=role_for(path)
            if role not in ('sources','backup') and (path.suffix.lower() in LIBRARIES or any(parent.name.endswith('.sdfull.parts') for parent in path.parents)):
                role='prepared'
            recognized=False;cache_format=None
            if role=='scenario':
                if info.st_nlink==1 and metadata_reads<64:
                    cache_format=cache_hint(path,cancelled);recognized=cache_format=='current';metadata_reads+=1
                if not recognized:role='legacy' if cache_format=='legacy' else 'unknown'
            policy=POLICIES[role]
            if info.st_nlink!=1:policy='Keep linked content'
            row=dict(path=str(path),bytes=info.st_size,role=role,policy=policy,
                modified_utc=dt.datetime.fromtimestamp(info.st_mtime,dt.timezone.utc).isoformat(),
                older_than_30_days=info.st_mtime<cutoff,cache_metadata_hint=recognized,
                cache_format_hint=cache_format or 'Not identified',
                last_use='Not established by file timestamps',backup='Not verified by this audit',
                rebuild=('SIM recomputation; cost not measured' if recognized else
                         'Potentially hours; preserve inputs, manifests and partitions' if role=='prepared' else
                         'Legacy SIM format; reconstruction inputs and cost not verified' if role=='legacy' else
                         'Research or recovery value; do not assume replaceable'),
                dependencies=('Exact inputs and compatible app/model required; active-use and integrity gates still apply' if recognized else
                              'Legacy metadata shape is not proof of provenance or safe removal' if role=='legacy' else
                              'References and recovery coverage not resolved; preserve'))
            total=totals.setdefault(role,dict(files=0,bytes=0));total['files']+=1;total['bytes']+=info.st_size
            if max_rows>0:
                item=(info.st_size,entries,row)
                if len(largest)<max_rows:heapq.heappush(largest,item)
                elif item[:2]>largest[0][:2]:heapq.heapreplace(largest,item)
            if entries%100==0:progress(f'Observed {sum(r["files"] for r in totals.values()):,} files; reading metadata only')
        except FileNotFoundError:
            errors+=1
            if len(issues)<10:issues.append('Missing or changed during scan: '+str(path))
        except (OSError,ValueError,OverflowError):
            errors+=1
            if len(issues)<10:issues.append('Unreadable; preserve for review: '+str(path))
    if errors or skipped_links:complete=False
    return dict(as_of=as_of.isoformat(),complete=complete,cancelled=stopped=='Cancelled',
        state=stopped or ('Observed inventory' if complete else 'Partial inventory'),
        rows=[row for _,_,row in sorted(largest,key=lambda item:(-item[0],item[2]['path']))],
        totals=totals,files=sum(r['files'] for r in totals.values()),bytes=sum(r['bytes'] for r in totals.values()),
        metadata_reads=metadata_reads,inspected_entries=entries,skipped_links=skipped_links,errors=errors,issues=sorted(set(issues)),
        rows_omitted=max(0,sum(r['files'] for r in totals.values())-len(largest)),
        roots=[dict(path=str(root),role=role) for root,role in scopes],seconds=time.monotonic()-started)


def render(report):
    lines=['DFS Storage Audit (read-only)',f'Captured: {report["as_of"]}',
           f'{report["state"]}: {report["files"]:,} observed files, {report["bytes"]/1024**3:.3f} GiB.',
           'Local paths included. Snapshot only; files can change during scanning.',
           'Logical file sizes; links, compression and sparse files can differ from physical disk usage.',
           'No deletion, move, archive or backup verification. Age is not last use or permission to remove.',
           'Research data, expensive preparation, backups and unidentified files remain protected.',
           '30-day age is informational; no age-based retention policy is enabled.',
           f'{report["rows_omitted"]:,} smaller files omitted from details; totals include observed files.',
           f'{report["skipped_links"]} links/reparse items skipped; {report["errors"]} read errors.',
           f'{report["metadata_reads"]} cache metadata hint reads (maximum 64); unverified files are retained.']
    lines.append('Configured scope only; not a whole-drive scan:')
    lines.extend('  '+root['role']+': '+root['path'] for root in report['roots'])
    for role,counts in sorted(report['totals'].items()):
        lines.append(f'{POLICIES[role]}: {counts["files"]:,} files, {counts["bytes"]/1024**3:.3f} GiB')
    for row in report['rows']:
        lines.extend(['',row['path'],f'{row["bytes"]/1024**2:.2f} MiB | {row["policy"]} | Modified {row["modified_utc"]}',
                      'Older than 30 days: '+str(row['older_than_30_days'])+' (not a last-use measurement)',
                      'Cache format hint: '+row['cache_format_hint'],row['rebuild'],row['dependencies'],'Backup: '+row['backup']])
    lines.extend(report['issues'])
    return '\n'.join(lines)
