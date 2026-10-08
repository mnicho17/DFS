"""Full-slate roster manifest with independently checkpointed Captain partitions."""
from contextlib import closing
from pathlib import Path
import json
import math
import sqlite3
import time
from build_snapshots import fingerprint


def _read(path):
    path=Path(path)
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as con:
        try:meta=json.loads(con.execute('SELECT payload FROM full_meta WHERE id=1').fetchone()[0])
        except (sqlite3.Error,TypeError,json.JSONDecodeError) as exc:
            raise ValueError('Unsupported or damaged full-library manifest.') from exc
    if meta.get('schema')!=1 or meta.get('identity')!=fingerprint(meta.get('structure')):
        raise ValueError('Full-library identity is damaged.')
    return meta


def partition_path(path,key):
    root=Path(str(path)+'.parts')
    result=root/(fingerprint(key)+'.sdlib')
    if root.is_symlink() or result.is_symlink() or result.resolve().parent!=root.resolve():
        raise ValueError('Full-library partitions must stay inside their storage folder.')
    return result


def states(path):
    from showdown_library import _read as read_part
    meta=_read(path);structure=meta['structure'];result={}
    for key in structure['captains']:
        part=partition_path(path,key)
        if not part.exists():result[key]=None;continue
        with closing(sqlite3.connect(part.resolve().as_uri()+'?mode=ro',uri=True)) as con:
            state=read_part(con)
        if state['structure']!=dict(structure,captains=[key]):
            raise ValueError('A Captain partition belongs to different inputs.')
        result[key]=state
    return meta,result


def status(path):
    meta,parts=states(path)
    return dict(identity=meta['identity'],captains=meta['structure']['captains'],
        saved=sum(p['saved'] for p in parts.values() if p),
        checked=sum(p['checked'] for p in parts.values() if p),
        complete=all(p and p['complete'] for p in parts.values()),
        completed_partitions=sum(bool(p and p['complete']) for p in parts.values()),
        partition_count=len(parts),
        structural_total=len(parts)*math.comb(len(meta['structure']['players'])-1,5))


def validate(path,players,*,salary_cap=50000,allow_partial=False):
    from showdown_library import _structure
    meta=_read(path)
    if _structure(players,meta['structure']['salary_cap'],meta['structure']['captains'])!=meta['structure']:
        raise ValueError('Salary, player identity or slate changed; use a new full library.')
    if not math.isfinite(float(salary_cap)) or not 0<float(salary_cap)<=meta['structure']['salary_cap']:
        raise ValueError('Requested salary cap exceeds the prepared library.')
    result=status(path)
    if not result['complete'] and not allow_partial:
        raise ValueError('Full library is incomplete; resume partition preparation before building.')
    return {k:result[k] for k in ('identity','saved','complete','captains')}


def prepare(path,players,*,salary_cap=50000,seconds=3600,max_bytes=8*1024**3,
            cancelled=lambda:False,progress=lambda value:None):
    from showdown_library import _structure,prepare as prepare_part
    if not math.isfinite(seconds) or not 0<seconds<=43200:raise ValueError('Invalid full-library time allowance.')
    if type(max_bytes) is not int or not 1024<=max_bytes<=32*1024**3:raise ValueError('Invalid full-library storage budget.')
    path=Path(path);structure=_structure(players,salary_cap)
    if path.exists():
        if _read(path)['structure']!=structure:raise ValueError('Full-library inputs changed; use a new manifest.')
    else:
        path.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(path)) as con:
            with con:
                con.execute('CREATE TABLE full_meta(id INTEGER PRIMARY KEY,payload TEXT NOT NULL)')
                con.execute('INSERT INTO full_meta VALUES(1,?)',(json.dumps(dict(schema=1,structure=structure,
                    identity=fingerprint(structure))),))
    _,parts=states(path)
    partition_path(path,structure['captains'][0]).parent.mkdir(parents=True,exist_ok=True)
    sizes={key:partition_path(path,key).stat().st_size if partition_path(path,key).exists() else 0 for key in parts}
    deadline=time.monotonic()+seconds;storage_stop=[False]
    for key in parts:
        if parts[key] and parts[key]['complete']:continue
        if cancelled() or time.monotonic()>=deadline or sum(sizes.values())>=max_bytes:break
        part=partition_path(path,key)
        def update(value):
            parts[key]=value;sizes[key]=part.stat().st_size
            storage_stop[0]=sum(sizes.values())>=max_bytes
            progress(dict(saved=sum(p['saved'] for p in parts.values() if p),
                checked=sum(p['checked'] for p in parts.values() if p),
                complete=False,completed_partitions=sum(bool(p and p['complete']) for p in parts.values()),
                partition_count=len(parts),storage_bytes=sum(sizes.values())))
        result=prepare_part(part,players,salary_cap=salary_cap,seconds=max(.001,deadline-time.monotonic()),
            max_candidates=50_000_000,captain_keys=[key],
            cancelled=lambda:cancelled() or storage_stop[0],progress=update)
        parts[key]=result
        if storage_stop[0]:break
    result=status(path)
    result.update(cancelled=bool(cancelled()),storage_bytes=sum(sizes.values()),
        pause_reason='storage budget' if sum(sizes.values())>=max_bytes else ('time/cancellation' if not result['complete'] else ''))
    progress(result)
    return result


def iter_candidates(path,players,**kwargs):
    from showdown_library import iter_candidates as stream_part,_structure
    from portfolio_rules import player_key
    meta=_read(path);key=kwargs.pop('captain_key',None);validated=kwargs.pop('_validated_info',None)
    if _structure(players,meta['structure']['salary_cap'],meta['structure']['captains'])!=meta['structure']:
        raise ValueError('Full-library inputs changed.')
    if validated is None:
        validate(path,players,salary_cap=kwargs.get('salary_cap',50000),allow_partial=kwargs.get('allow_partial',False))
    elif validated['identity']!=meta['identity']:
        raise ValueError('Full-library identity changed during loading.')
    locked={player_key(p) for p in players if p.get('LockCpt')}
    from showdown_simulation import active_showdown_players
    eligible={player_key(p) for p in active_showdown_players(players)
              if not p.get('FadeCpt') and not p.get('LockFlex')}
    keys=[k for k in meta['structure']['captains'] if k in eligible and
          (key is None or k==key) and (not locked or k in locked)]
    for captain in keys:
        part=partition_path(path,captain)
        if part.exists():yield from stream_part(part,players,captain_key=captain,**kwargs)
