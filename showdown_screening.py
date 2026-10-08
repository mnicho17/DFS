"""Resumable, exact-input screening of a bounded prepared candidate bank.

Only coarse screening is reusable; final scoring and audits stay independent.
"""
from contextlib import closing
import copy
import json
import math
from pathlib import Path
import sqlite3
import sys
import time

from build_snapshots import fingerprint
from candidate_library import code_id
from portfolio_rules import player_key
from optimizers import ShowdownLineup
from scenario_cache import cache_folder
from showdown_library import validate_library, load_bounded

MAX_BYTES=512*1024**2

# Observation times do not enter coarse scoring or eligibility. Preserve them
# in caller inputs, snapshots and safety evidence; exclude only these audited
# paths from screening compatibility. Unknown fields remain strict by default.
OBSERVATION_TIMES = frozenset(('NFLUsageCheckedAt', 'NFLUsageFetchedAt',
    'LiveStatusUpdatedAt', 'NFLVegasUpdatedAt', 'NFLNewsUpdatedAt'))

OBSERVATION_FLAGS = frozenset(("LiveStatusChanged",))


def scoring_players(players):
    rows = copy.deepcopy(players)
    for player in rows:
        for key in OBSERVATION_TIMES | OBSERVATION_FLAGS:
            player.pop(key, None)
        history = player.get('NFLUsageHistory')
        if isinstance(history, dict):
            history.pop('checked_at', None)
    return rows


def settings(options, sim_scenarios):
    from compute_settings import normalize_deep_settings
    options=normalize_deep_settings(options)
    return dict(scenarios=min(options['screening'],max(250,sim_scenarios)),
                field_lineup_count=min(1600,options['field'] or 1200),seed=73129)


def target(library,players,*,limit,salary_cap,salary_strategy,rules,screening,folder=None):
    info=validate_library(library,players,salary_cap=salary_cap)
    # Player iteration order participates in seeded scenario generation.
    context=dict(schema=2,library=info,players=scoring_players(players),
        limit=limit,salary_cap=float(salary_cap),salary_strategy=salary_strategy,rules=rules,
        screening=screening,model=code_id(),python=sys.version)
    identity=fingerprint(context)
    root=Path(folder) if folder is not None else cache_folder().parent/'showdown-screening'
    return root/(identity+'.sqlite'),context


def _payload(row):
    return json.loads(json.dumps(dict(metrics=row.sim_metrics,
        hits=[sorted(getattr(row,k,set())) for k in ('sim_top_hits','sim_top_five_hits','sim_win_hits')],
        values=getattr(row,'sim_scenario_values',{}))))


def _restore(row,payload,screening,*,salary_cap=50000):
    metrics=payload['metrics'];n=screening['scenarios']
    if metrics.get('sim_scenarios')!=n or metrics.get('sim_field_lineups',0)<=0:
        raise ValueError('Incomplete screening scores.')
    if any(isinstance(v,(int,float)) and not math.isfinite(v) for v in metrics.values()):
        raise ValueError('Invalid screening scores.')
    for name,hits in zip(('sim_top_hits','sim_top_five_hits','sim_win_hits'),payload['hits']):
        if any(type(i) is not int or not 0<=i<n for i in hits):raise ValueError('Invalid screening hits.')
        setattr(row,name,set(hits))
    # SIM evidence is reusable; construction metadata must be rebuilt from
    # the current roster just as it is on ordinary prepared-library loading.
    from optimizers import attach_showdown_metrics
    attach_showdown_metrics([row],salary_cap)
    fresh={k:v for k,v in row.sim_metrics.items() if k not in ('sim_edge','sim_return_index')}
    row.sim_metrics=dict(metrics)
    row.sim_metrics.update(fresh)
    row.sim_metrics['candidate_source']='prepared_roster_library'
    row.sim_scenario_values={int(k):v for k,v in payload['values'].items()}
    row.candidate_source='prepared_roster_library'
    return row


def _bank(con,players):
    from showdown_simulation import validate_showdown_lineup
    lookup={player_key(p):p for p in players}
    meta=json.loads(con.execute('SELECT payload FROM meta WHERE id=1').fetchone()[0])
    if fingerprint(meta['bank'])!=meta['bank_id']:raise ValueError('Screening bank identity is damaged.')
    if not 1<=len(meta['bank'])<=20000:raise ValueError('Screening bank size is invalid.')
    rows=[]
    for cpt,flex in meta['bank']:
        row=ShowdownLineup(lookup[cpt],[lookup[k] for k in flex])
        validate_showdown_lineup(row,players,meta['context']['salary_cap']);rows.append(row)
    return meta,rows


def prepare_screening(library,players,*,limit=20000,salary_cap=50000,salary_strategy='Near Cap',
                      rules=None,screening=None,seconds=3600,batch_size=256,
                      cancelled=lambda:False,progress=lambda value:None,folder=None):
    """Checkpoint whole completed batches; cancellation never commits partial SIMs."""
    if type(batch_size) is not int or not 1<=batch_size<=1000:raise ValueError('Invalid screening batch size.')
    if not math.isfinite(seconds) or not 0<seconds<=43200:raise ValueError('Invalid screening allowance.')
    players=copy.deepcopy(players)
    screening=screening or settings({},1000)
    if not 250<=screening['scenarios']<=1000 or not 1<=screening['field_lineup_count']<=1600:
        raise ValueError('Invalid screening settings.')
    path,context=target(library,players,limit=limit,salary_cap=salary_cap,
        salary_strategy=salary_strategy,rules=rules,screening=screening,folder=folder)
    started=time.monotonic();deadline=started+seconds
    path.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(path,timeout=15)) as con:
        con.execute('CREATE TABLE IF NOT EXISTS meta(id INTEGER PRIMARY KEY,payload TEXT NOT NULL)')
        con.execute('CREATE TABLE IF NOT EXISTS scores(id INTEGER PRIMARY KEY,payload TEXT NOT NULL,digest TEXT NOT NULL)')
        if con.execute('SELECT 1 FROM meta').fetchone() is None:
            rows,report=load_bounded(library,players,limit=limit,salary_cap=salary_cap,
                salary_strategy=salary_strategy,rules=rules,seconds=min(30,seconds),cancelled=cancelled)
            bank=[[player_key(r['Captain']),[player_key(p) for p in r['Flex']]] for r in rows]
            with con:
                con.execute('INSERT INTO meta VALUES(1,?)',(json.dumps(dict(context=context,
                    bank=bank,bank_id=fingerprint(bank),library_report=report)),))
        meta,rows=_bank(con,players)
        if meta['context']!=context:raise ValueError('Screening cache inputs changed.')
        done=set()
        for index,payload,digest in con.execute('SELECT id,payload,digest FROM scores'):
            data=json.loads(payload)
            if not 0<=index<len(rows) or fingerprint(data)!=digest:raise ValueError('Screening cache is damaged.')
            _restore(rows[index],data,screening,salary_cap=salary_cap);done.add(index)
        from showdown_simulation import simulate_showdown
        pending=[i for i in range(len(rows)) if i not in done]
        for start in range(0,len(pending),batch_size):
            if cancelled() or time.monotonic()>=deadline or path.stat().st_size>=MAX_BYTES:break
            indices=pending[start:start+batch_size]
            result=simulate_showdown([rows[i] for i in indices],players,salary_cap=salary_cap,
                **screening,scenario_cache=True,cancel_callback=lambda:cancelled() or time.monotonic()>=deadline)
            if cancelled() or result['report']['scenarios']!=screening['scenarios']:break
            with con:
                for i,row in zip(indices,result['lineups']):
                    data=_payload(row)
                    con.execute('INSERT INTO scores VALUES(?,?,?)',(i,json.dumps(data),fingerprint(data)))
            done.update(indices)
            progress(dict(screened=len(done),screening_total=len(rows),screening_complete=len(done)==len(rows)))
    elapsed=time.monotonic()-started
    return dict(screened=len(done),screening_total=len(rows),screening_complete=len(done)==len(rows),
        elapsed_seconds=elapsed,rosters_per_second=(len(pending)-len([i for i in pending if i not in done]))/max(.001,elapsed))


def load_screening(library,players,*,limit,salary_cap,salary_strategy,rules,screening,folder=None,cancelled=lambda:False,diagnostic=None):
    """Return a fully screened compatible bank, or fall back to fresh screening."""
    started=time.monotonic()
    path,context=target(library,players,limit=limit,salary_cap=salary_cap,
        salary_strategy=salary_strategy,rules=rules,screening=screening,folder=folder)
    from showdown_full_screening import load as load_full
    full=load_full(path,context,players,screening,cancelled=cancelled,diagnostic=diagnostic)
    if full:
        rows,report=full
        report['loading_seconds']=time.monotonic()-started
        return rows,report
    if not path.is_file():
        if diagnostic is not None:
            diagnostic.setdefault('reason','No exact compatible screening cache; prepare screening with current inputs and settings.')
        return None
    if path.stat().st_size>MAX_BYTES:
        if diagnostic is not None:diagnostic['reason']='Screening cache exceeds its supported size.'
        return None
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as con:
        con.execute('BEGIN');meta,rows=_bank(con,players)
        if meta['context']!=context:
            if diagnostic is not None:diagnostic['reason']='Screening cache identity does not match current inputs.'
            return None
        records=con.execute('SELECT id,payload,digest FROM scores ORDER BY id').fetchall()
        if len(records)!=len(rows):
            if diagnostic is not None:diagnostic['reason']=f'Screening incomplete: {len(records):,}/{len(rows):,} candidates. Resume preparation.'
            return None
        for expected,(index,payload,digest) in enumerate(records):
            if cancelled():raise InterruptedError('Screening cache loading cancelled.')
            data=json.loads(payload)
            if index!=expected or fingerprint(data)!=digest:raise ValueError('Screening cache is damaged.')
            _restore(rows[index],data,screening,salary_cap=salary_cap)
        from showdown_simulation import rank_screening_metrics
        rank_screening_metrics([r.sim_metrics for r in rows])
        return rows,dict(meta['library_report'],screening_reused=len(rows),
            loading_seconds=time.monotonic()-started)
