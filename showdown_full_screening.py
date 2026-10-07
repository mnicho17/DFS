"""Streaming full legal-roster screening; bounded leaders and atomic resume."""
from contextlib import closing
import copy
import heapq
import json
import math
from pathlib import Path
import sqlite3
import time
from build_snapshots import fingerprint
from lineup_ranking import finish_rank
from portfolio_rules import player_key
from optimizers import ShowdownLineup
from showdown_screening import target,_payload,_restore,MAX_BYTES


def _location(path,context):
    context=dict(context,mode='full-stream-v1')
    return path.parent/(fingerprint(context)+'.sqlite'),context


def _roster(row):return [player_key(row['Captain']),[player_key(p) for p in row['Flex']]]


class Leaders:
    def __init__(self,players,limit):
        from showdown_simulation import active_showdown_players
        pool=active_showdown_players(players);locks={player_key(p) for p in pool if p.get('LockCpt')}
        self.captains=sorted(player_key(p) for p in pool if not p.get('FadeCpt') and not p.get('LockFlex') and (not locks or player_key(p) in locks))
        if not self.captains or limit<len(self.captains):raise ValueError('Retention budget must cover every eligible Captain.')
        self.quota=limit//len(self.captains);self.reserve=self.quota//120
        self.top=self.quota-60*self.reserve;self.heaps={}

    def add(self,row):
        cpt=player_key(row['Captain']);roster=[row['Captain'],*row['Flex']]
        if cpt not in self.captains:raise ValueError('Screened Captain is outside the captured pool.')
        team=sum(p['Team']==row['Captain']['Team'] for p in roster)
        qb=min(2,sum(str(p.get('Position')).upper()=='QB' for p in roster))
        kd=min(3,sum(str(p.get('Position')).upper() in ('K','DST','DEF','D') for p in roster))
        signature=(cpt,tuple(player_key(p) for p in row['Flex']))
        entry=(finish_rank(row),signature,row)
        for key,cap in [((cpt,),self.top),((cpt,team,qb,kd),self.reserve)]:
            if not cap:continue
            heap=self.heaps.setdefault(key,[])
            if len(heap)<cap:heapq.heappush(heap,entry)
            elif entry[:2]>heap[0][:2]:heapq.heapreplace(heap,entry)

    def rows(self):
        unique={entry[1]:entry[2] for heap in self.heaps.values() for entry in heap}
        return [unique[key] for key in sorted(unique)]


def _read(con,context):
    raw=con.execute('SELECT payload,digest FROM screen_progress WHERE id=1').fetchone()
    if raw is None:return None
    state=json.loads(raw[0])
    if fingerprint(state)!=raw[1] or state['context']!=context:
        raise ValueError('Full-screening checkpoint is damaged or belongs to different inputs.')
    return state


def _leaders(state,players,limit,screening):
    leaders=Leaders(players,limit);lookup={player_key(p):p for p in players}
    from showdown_simulation import validate_showdown_lineup
    for cpt,flex,payload in state.get('leaders',[]):
        row=ShowdownLineup(lookup[cpt],[lookup[k] for k in flex])
        validate_showdown_lineup(row,players,state['context']['salary_cap'])
        leaders.add(_restore(row,payload,screening))
    return leaders


def prepare(library,players,*,limit,salary_cap,salary_strategy,rules,screening,seconds=3600,
            batch_size=256,cancelled=lambda:False,progress=lambda value:None,folder=None):
    from showdown_library import iter_candidates,validate_library
    from showdown_simulation import simulate_showdown,salary_floor
    if type(batch_size) is not int or not 1<=batch_size<=1000:raise ValueError('Invalid screening batch size.')
    if not math.isfinite(seconds) or not 0<seconds<=43200:raise ValueError('Invalid screening allowance.')
    if type(limit) is not int or not 1<=limit<=20000:raise ValueError('Retention budget must be 1–20,000.')
    if not 250<=screening['scenarios']<=1000 or not 1<=screening['field_lineup_count']<=1600:raise ValueError('Invalid full-screening settings.')
    path,context=target(library,players,limit=limit,salary_cap=salary_cap,salary_strategy=salary_strategy,
        rules=rules,screening=screening,folder=folder)
    path,context=_location(path,context);path.parent.mkdir(parents=True,exist_ok=True)
    started=time.monotonic();deadline=started+seconds;players=copy.deepcopy(players)
    with closing(sqlite3.connect(path,timeout=15)) as con:
        con.execute('CREATE TABLE IF NOT EXISTS screen_progress(id INTEGER PRIMARY KEY,payload TEXT,digest TEXT)')
        existing=_read(con,context)
        state=existing or dict(context=context,cursor=None,screened=0,complete=False,leaders=[])
        committed_digest=fingerprint(existing) if existing is not None else None
        leaders=_leaders(state,players,limit,screening);batch=[];storage_stop=False
        def checkpoint():
            nonlocal committed_digest
            payload=json.loads(json.dumps(state));raw=json.dumps(payload)
            if len(raw.encode('utf-8'))>128*1024**2:return False
            digest=fingerprint(payload)
            with con:
                if committed_digest is None:
                    try:con.execute('INSERT INTO screen_progress VALUES(1,?,?)',(raw,digest))
                    except sqlite3.IntegrityError as exc:raise ValueError('Another screening writer changed this checkpoint.') from exc
                elif con.execute('UPDATE screen_progress SET payload=?,digest=? WHERE id=1 AND digest=?',
                        (raw,digest,committed_digest)).rowcount!=1:
                    raise ValueError('Another screening writer changed this checkpoint.')
            committed_digest=digest
            return True
        def score():
            nonlocal storage_stop
            result=simulate_showdown(batch,players,salary_cap=salary_cap,**screening,scenario_cache=True,
                cancel_callback=lambda:cancelled() or time.monotonic()>=deadline)
            if cancelled() or result['report']['scenarios']!=screening['scenarios']:return False
            for row in result['lineups']:leaders.add(row)
            previous=dict(state)
            state.update(cursor=_roster(batch[-1]),screened=state['screened']+len(batch),
                leaders=[[*_roster(row),_payload(row)] for row in leaders.rows()])
            if not checkpoint():
                state.clear();state.update(previous);storage_stop=True;return False
            progress(dict(screened=state['screened'],screening_total=None,screening_complete=False,
                retained=len(state['leaders']),scope='every currently legal roster'))
            batch.clear();return True
        if not state['complete']:
            if committed_digest is None:checkpoint()
            exhausted=True
            for row in iter_candidates(library,players,salary_cap=salary_cap,salary_floor=salary_floor(salary_cap,salary_strategy),
                    rules=rules,cancelled=lambda:cancelled() or time.monotonic()>=deadline,
                    _after_roster=state['cursor'],_validated_info=context['library']):
                batch.append(row)
                if len(batch)>=batch_size and not score():exhausted=False;break
            if batch and exhausted:exhausted=score()
            if exhausted and not cancelled() and time.monotonic()<deadline:
                current=validate_library(library,players,salary_cap=salary_cap)
                if current!=context['library']:raise ValueError('Source library changed during full screening.')
                state['complete']=True
                if not checkpoint():state['complete']=False;storage_stop=True
    return dict(screened=state['screened'],screening_total=state['screened'] if state['complete'] else None,
        screening_complete=state['complete'],retained=len(state['leaders']),elapsed_seconds=time.monotonic()-started,
        pause_reason='retention storage budget' if storage_stop else ('' if state['complete'] else 'time/cancellation'))


def load(base_path,context,players,screening,cancelled=lambda:False,diagnostic=None):
    path,context=_location(base_path,context)
    if not path.exists() or path.stat().st_size>MAX_BYTES:return None
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as con:
        state=_read(con,context)
    if state is None or not state['complete']:
        if diagnostic is not None:diagnostic['reason']='Full-roster screening is partial; resume the captured-input job.'
        return None
    if cancelled():raise InterruptedError('Full-screening loading cancelled.')
    rows=_leaders(state,players,context['limit'],screening).rows()
    if not rows:return None
    from showdown_simulation import rank_screening_metrics
    rank_screening_metrics([r.sim_metrics for r in rows])
    return rows,dict(type='prepared_showdown',saved=context['library']['saved'],accepted=len(rows),
        preparation_complete=True,valid_seen=state['screened'],scan_complete=True,
        screening_reused=len(rows),full_screened=state['screened'],loading_seconds=0)
