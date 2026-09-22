"""Summaries of the exact player outcomes consumed by a completed simulation."""
from array import array
from bisect import bisect_left,bisect_right
import datetime as dt
import json
import math
from pathlib import Path
import re
import statistics
from build_snapshots import fingerprint


def game_identity(player):
    from results_snapshot_learning import game_start
    start=game_start(player.get('GameInfo'))
    match=re.search(r'\b([A-Za-z]{1,4})@([A-Za-z]{1,4})\b',str(player.get('GameInfo') or ''))
    if not start or not match:return None
    teams=sorted([match[1].upper(),match[2].upper()])
    if str(player.get('Team') or '').upper() not in teams:return None
    return '|'.join(teams)+'|'+start.astimezone(dt.timezone.utc).isoformat()


class DistributionCapture:
    def __init__(self,players,kind,requested,seed):
        from nfl_simulation import player_key
        from learning_db import _normalize_roster_token
        from results_snapshot_learning import forecast_group
        self.kind=kind;self.requested=int(requested);self.seed=seed
        self.started_at=dt.datetime.now(dt.timezone.utc).isoformat()
        self.players={player_key(p):dict(name=_normalize_roster_token(p.get('Name')),position=str(p.get('Position') or 'Unknown'),
            role=forecast_group(p),game=game_identity(p),source=p.get('ProjectionSource','Unknown')) for p in players}
        self.values={key:array('d') for key in self.players}
        self.invalid=False

    def record(self,outcomes):
        for key,values in self.values.items():
            v=outcomes.get(key)
            if not isinstance(v,(int,float)) or not math.isfinite(v):self.invalid=True;continue
            values.append(v)

    def finish(self,completed):
        if self.invalid or completed!=self.requested or completed<1 or any(len(v)!=completed for v in self.values.values()):
            return dict(status='not captured',reason='Incomplete or invalid scenario outcomes')
        rows=[]
        for key,values in self.values.items():
            ordered=sorted(values)
            q=lambda fraction:ordered[int((completed-1)*fraction)]
            lo,hi=q(.1),q(.9)
            rows.append(dict(self.players[key],mean=statistics.mean(values),p10=lo,p50=q(.5),p90=hi,p025=q(.025),p975=q(.975),
                expected_below_p10=bisect_left(ordered,lo)/completed,expected_above_p90=(completed-bisect_right(ordered,hi))/completed))
        return dict(status='complete',kind=self.kind,scenarios=completed,seed=self.seed,started_at=self.started_at,
            finished_at=dt.datetime.now(dt.timezone.utc).isoformat(),players=rows)


def save_distribution(report,input_id,folder=None):
    """Consume the temporary summary; failed persistence never discards a build."""
    raw=report.pop('player_distributions',None)
    status=dict(status='not saved',reason='No complete scoring distribution was captured')
    try:
        if raw and raw.get('status')=='complete' and re.fullmatch('[0-9a-f]{64}',input_id or ''):
            from repeatability import model_version,atomic_json
            from build_diagnostics import build_history_path
            payload=dict(raw,input_id=input_id,model_version=model_version(),schema=1)
            capture_id=fingerprint(payload)
            root=Path(folder) if folder else Path(build_history_path()).parent
            atomic_json(root/'scoring-distributions'/(input_id+'-'+capture_id+'.json'),dict(capture_id=capture_id,payload=payload))
            status=dict(status='saved',capture_id=capture_id,scenarios=raw['scenarios'],players=len(raw['players']),
                reason='Actual simulation outcomes recorded; results validation requires capture before kickoff')
        elif raw:status['reason']=raw.get('reason') or 'Build input ID unavailable'
    except Exception as exc:
        status=dict(status='not saved',reason=str(exc))
    report['distribution_capture']=status
    return status


def load_distribution(path):
    path=Path(path)
    if path.stat().st_size>5*1024*1024:raise ValueError('Distribution file exceeds limit')
    value=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value,dict) or not isinstance(value.get('payload'),dict):raise ValueError('Invalid distribution record')
    p=value['payload']
    if p.get('schema')!=1 or p.get('status')!='complete' or value.get('capture_id')!=fingerprint(p):raise ValueError('Distribution integrity mismatch')
    if not isinstance(p.get('scenarios'),int) or p['scenarios']<1:raise ValueError('Invalid scenario count')
    if p.get('kind') not in ('classic','showdown') or not isinstance(p.get('players'),list):raise ValueError('Invalid distribution format')
    if not isinstance(p.get('model_version'),str) or not p['model_version']:raise ValueError('Missing model identity')
    for row in p['players']:
        if not isinstance(row,dict) or not all(isinstance(row.get(k),str) and row[k] for k in ('name','position','role')):raise ValueError('Invalid player identity')
        if row.get('game') is not None and not isinstance(row['game'],str):raise ValueError('Invalid game identity')
        vals=[row.get(k) for k in ('mean','p025','p10','p50','p90','p975','expected_below_p10','expected_above_p90')]
        if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in vals):raise ValueError('Invalid player distribution')
        if not row['p025']<=row['p10']<=row['p50']<=row['p90']<=row['p975']:raise ValueError('Invalid quantiles')
        if not all(0<=row[k]<=1 for k in ('expected_below_p10','expected_above_p90')):raise ValueError('Invalid tail rates')
    return value
