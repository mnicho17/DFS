"""Explicit review associations, separate from qualified historical salary pairs."""
from contextlib import closing


def latest(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='analysis_salary_reviews'").fetchone():
        return {}
    rows = conn.execute("""SELECT r.result_hash,r.salary_hash,r.reason,r.qualification_reason,r.created_at,r.id
        FROM analysis_salary_reviews r JOIN
        (SELECT result_hash,MAX(id) AS id FROM analysis_salary_reviews GROUP BY result_hash) x ON r.id=x.id""")
    return {r:dict(salary_hash=s,reason=why,qualification_reason=q,created_at=at,review_id=i)
            for r,s,why,q,at,i in rows}


def save(result_hash, salary_hash, reason, *, db_path=None, cancelled=lambda:False):
    from learning_db import _connect
    from analysis_imports import ensure_tables, _sources, _verify, _check, qualify_pair
    reason=str(reason).strip()
    if not 8 <= len(reason) <= 1000:
        raise ValueError('Record why this salary revision belongs to the contest (8–1000 characters).')
    _check(cancelled)
    with closing(_connect(db_path)) as conn:
        ensure_tables(conn)
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            sources={s['hash']:s for s in _sources(conn)}
            result,salary=sources[result_hash],sources[salary_hash]
            if result['kind']!='results' or salary['kind']!='salary':
                raise ValueError('Select a results source and a salary revision.')
            if conn.execute('SELECT 1 FROM analysis_salary_pairs WHERE result_hash=?',(result_hash,)).fetchone():
                raise ValueError('Existing qualified pairing preserved; a review association cannot replace it.')
            _verify(result,cancelled);_verify(salary,cancelled)
            q=qualify_pair(result,salary)
            if q['compatible']:
                raise ValueError('This revision is compatible. Use the normal Save action and required date confirmation.')
            _check(cancelled)
            previous=latest(conn).get(result_hash)
            if previous and all(previous[k]==v for k,v in (('salary_hash',salary_hash),('reason',reason),('qualification_reason',q['reason']))):
                return False
            conn.execute("""CREATE TABLE IF NOT EXISTS analysis_salary_reviews (
                id INTEGER PRIMARY KEY, result_hash TEXT NOT NULL REFERENCES analysis_sources(hash),
                salary_hash TEXT NOT NULL REFERENCES analysis_sources(hash), reason TEXT NOT NULL,
                qualification_reason TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
            conn.execute('INSERT INTO analysis_salary_reviews(result_hash,salary_hash,reason,qualification_reason) VALUES(?,?,?,?)',
                         (result_hash,salary_hash,reason,q['reason']))
            _check(cancelled)
        return True
