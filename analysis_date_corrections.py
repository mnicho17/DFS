"""Explicit, immutable date corrections bound to original result/salary revisions."""
import copy
import json
from contextlib import closing
from build_snapshots import fingerprint


def candidate(result, salary, identity_index=None):
    from analysis_imports import qualify_pair
    if result.get('date_correction') or result['manifest']['dates']==salary['manifest']['dates']:
        return False
    if len(salary['manifest']['dates'])!=1:
        return False
    csv_dates=result['manifest'].get('csv_dates',[])
    if csv_dates and csv_dates!=salary['manifest']['dates']:
        return False
    trial=copy.deepcopy(result)
    trial['manifest']['dates']=salary['manifest']['dates']
    return qualify_pair(trial,salary,_identity_index=identity_index)['compatible']


def apply(conn, sources):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='analysis_date_corrections'").fetchone():
        return sources
    by_hash={s['hash']:s for s in sources}
    pairs=dict(conn.execute('SELECT result_hash,salary_hash FROM analysis_salary_pairs'))
    for result_hash,encoded,digest in conn.execute('SELECT result_hash,payload,digest FROM analysis_date_corrections'):
        try:payload=json.loads(encoded)
        except (ValueError,TypeError) as exc:raise ValueError('Saved date correction is damaged.') from exc
        if not isinstance(payload,dict) or payload.get('version')!=1:
            raise ValueError('Saved date correction is damaged or unsupported.')
        source=by_hash.get(result_hash);salary=by_hash.get(payload.get('salary_hash'))
        if (fingerprint(payload)!=digest or payload.get('result_hash')!=result_hash or
                not source or source['kind']!='results' or not salary or salary['kind']!='salary' or
                pairs.get(result_hash)!=salary['hash'] or
                fingerprint(source['manifest'])!=payload.get('original_manifest_digest') or
                salary['manifest']['dates']!=[payload.get('corrected_date')]):
            raise ValueError('Saved date correction conflicts with its original source/pairing; no replacement inferred.')
        source['date_correction']=dict(payload,correction_id=digest)
        source['manifest']=dict(source['manifest'],dates=[payload['corrected_date']],date_source='explicit saved date correction')
    return sources


def save(result_hash,salary_hash,reason,*,db_path=None,cancelled=lambda:False):
    from learning_db import _connect
    from analysis_imports import ensure_tables,_sources,_verify,_check,_results_manifest,qualify_pair
    reason=str(reason).strip()
    if not 8<=len(reason)<=1000:
        raise ValueError('Record a reason for correcting this contest date (8–1000 characters).')
    with closing(_connect(db_path)) as conn:
        ensure_tables(conn)
        with conn:
            # Lock before qualification so a competing pair save cannot replace the reviewed revision.
            conn.execute('BEGIN IMMEDIATE')
            sources={s['hash']:s for s in _sources(conn)}
            result,salary=sources[result_hash],sources[salary_hash]
            if conn.execute('SELECT 1 FROM analysis_salary_pairs WHERE result_hash=?',(result_hash,)).fetchone():
                raise ValueError('Existing pairing preserved; date correction requires an unpaired result.')
            if not candidate(result,salary):
                raise ValueError('Date correction cannot resolve missing/conflicting identities, games, or formats.')
            _verify(result,cancelled);_verify(salary,cancelled)
            fresh=_results_manifest(result['snapshot'],cancelled)
            if fresh['csv_dates'] and fresh['csv_dates']!=salary['manifest']['dates']:
                raise ValueError('Explicit CSV dates conflict; a filename correction cannot override them.')
            trial=dict(result,manifest=dict(fresh,dates=salary['manifest']['dates']))
            if not qualify_pair(trial,salary)['compatible']:
                raise ValueError('Corrected date does not qualify this salary revision.')
            payload=dict(version=1,result_hash=result_hash,salary_hash=salary_hash,
                original_dates=result['manifest']['dates'],corrected_date=salary['manifest']['dates'][0],
                original_manifest_digest=fingerprint(result['manifest']),reason=reason,
                csv_dates=fresh['csv_dates'])
            _verify(result,cancelled);_verify(salary,cancelled);_check(cancelled)
            conn.execute('CREATE TABLE IF NOT EXISTS analysis_date_corrections(result_hash TEXT PRIMARY KEY,payload TEXT NOT NULL,digest TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
            conn.execute('INSERT INTO analysis_date_corrections(result_hash,payload,digest) VALUES(?,?,?)',
                (result_hash,json.dumps(payload,allow_nan=False),fingerprint(payload)))
            conn.execute('INSERT INTO analysis_salary_pairs(result_hash,salary_hash,basis,evidence_version) VALUES(?,?,?,1)',
                (result_hash,salary_hash,'user-confirmed-date-correction-and-identities'))
            _check(cancelled)
        return result.get('import_id')
