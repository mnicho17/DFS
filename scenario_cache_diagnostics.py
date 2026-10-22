"""Bounded fingerprint receipts for explanation only; never authorize scenario replay."""
import json
import re
import sqlite3
import time
from contextlib import closing
from build_snapshots import fingerprint

LABELS=('player forecasts','player availability and roles','player ownership','other player inputs',
        'opponent field','contest kind','random seed','scenario count','app/model version','runtime/codec','context/history settings')
LIMIT=64
MAX_BYTES=2*1024*1024


def components(identity):
    original=identity.get('context')
    context=dict(original) if isinstance(original,dict) else {'value':original}
    present=[key for key in ('players','opponent_players') if key in context]
    groups=[identity['players'],context.pop('players',None),context.pop('opponent_players',None)]
    buckets=[[],[],[],[]]
    for group in groups:
        if group is None:
            for bucket in buckets:bucket.append(None)
            continue
        separated=[[],[],[],[]]
        for player in group:
            parts=[{}, {}, {}, {}]
            for key,value in player.items():
                name=str(key).casefold()
                ownership=('ownership' in name or name.startswith('own') or name.endswith(('own','ownpct','ownpercent')))
                index=(2 if ownership else
                       0 if any(t in name for t in ('projection','proj','forecast','ppg','ceiling','floor','stddev')) else
                       1 if any(t in name for t in ('injury','status','active','available','eligible','depth','role')) else 3)
                parts[index][key]=value
            for index,part in enumerate(parts):separated[index].append(part)
        for index,part in enumerate(separated):buckets[index].append(part)
    values=buckets+[identity['fields'],identity['kind'],identity['seed'],identity['count'],identity['model'],
                   {key:identity[key] for key in ('schema','scores_codec','byteorder','python')},[isinstance(original,dict),present,context]]
    return {label:fingerprint(value) for label,value in zip(LABELS,values)}


def scope(identity):
    from candidate_library import slate_id
    context=identity.get('context') or {}
    players=context.get('players') if isinstance(context,dict) else None
    return slate_id(players or identity['players'],identity['kind'])


def _path(root):
    path=root/'diagnostics.db'
    if path.is_symlink() or (path.exists() and (not path.is_file() or path.stat().st_size>MAX_BYTES)):
        raise ValueError('Diagnostic receipt store unavailable')
    return path


def _valid(parts):
    return isinstance(parts,dict) and set(parts)==set(LABELS) and all(isinstance(v,str) and re.fullmatch('[0-9a-f]{64}',v) for v in parts.values())


def compare(root, slate, parts, cancelled=lambda:False):
    """Compare recent same-slate receipts, without treating any as valid cache evidence."""
    try:
        path=_path(root)
        if not path.exists():return dict(status='No comparable receipt recorded')
        started=time.monotonic();best=None
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.05)) as con:
            rows=con.execute('SELECT cache_key,parts FROM receipts WHERE scope=? AND length(parts)<12000 ORDER BY rowid DESC LIMIT 12',(slate,)).fetchall()
        for key,raw in rows:
            if cancelled() or time.monotonic()-started>.5:break
            if not re.fullmatch('[0-9a-f]{64}',key) or not (root/(key+'.sqlite')).is_file():continue
            prior=json.loads(raw)
            if not _valid(prior):continue
            changed=[label for label in LABELS if prior[label]!=parts[label]]
            if best is None or len(changed)<len(best['changed_components']):
                best=dict(status='Compared with a recent same-slate receipt',changed_components=changed)
        return best or dict(status='No comparable receipt recorded')
    except (OSError,ValueError,TypeError,sqlite3.Error):
        return dict(status='Diagnostic comparison unavailable')


def record(root, slate, key, parts):
    try:
        path=_path(root)
        if not _valid(parts) or not re.fullmatch('[0-9a-f]{64}',key):return
        with closing(sqlite3.connect(str(path),timeout=.05)) as con:
            with con:
                con.execute('CREATE TABLE IF NOT EXISTS receipts (cache_key TEXT PRIMARY KEY,scope TEXT,parts TEXT)')
                con.execute('CREATE INDEX IF NOT EXISTS receipt_scope ON receipts(scope)')
                con.execute('INSERT OR REPLACE INTO receipts VALUES (?,?,?)',(key,slate,json.dumps(parts)))
                con.execute('DELETE FROM receipts WHERE rowid NOT IN (SELECT rowid FROM receipts ORDER BY rowid DESC LIMIT ?)',(LIMIT,))
    except (OSError,ValueError,TypeError,sqlite3.Error):
        pass
