"""Explicit shared-sample Showdown capture; never production build selection."""
from copy import deepcopy
from pathlib import Path
import datetime,json,math,os,sys,tempfile
from build_snapshots import fingerprint
from repeatability import atomic_json,model_version
from review_build_evidence import snapshot as validate_snapshot
from portfolio_risk_evidence import _archive_rosters
from showdown_common_groups import catalog
from nfl_simulation import player_key
from optimizers import ShowdownLineup
from showdown_simulation import simulate_showdown,active_showdown_players,validate_showdown_lineup

MAX_BYTES=64*1024*1024
MAX_PLAYERS=200
MAX_CANDIDATES=2000
MAX_FIELD=5000
MAX_FRAMES=10000
MAX_VALUES=1000000

class TrialCancelled(ValueError):pass

def runtime_identity():
    return dict(python=sys.version,byteorder=sys.byteorder)


def _publish(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(dir=path.parent,suffix='.trial.tmp');os.close(fd)
    temporary=Path(name)
    try:
        atomic_json(temporary,value)
        os.link(temporary,path)  # Atomic create; never replace another capture.
    finally:temporary.unlink(missing_ok=True)


def _check(cancelled):
    if cancelled():raise TrialCancelled('Shared-sample capture cancelled; no partial trial published.')


def _active(players):
    if any(str(p.get('Position') or '').upper()=='QB' and type(p.get('NFLQBEligible')) is not bool for p in players):
        raise ValueError('Frozen QB eligibility is missing; no historical roles are inferred.')
    return active_showdown_players(deepcopy(players))


def _rosters(rows,players,*,field=False):
    if not isinstance(rows,list) or not 0<len(rows)<=(MAX_FIELD if field else MAX_CANDIDATES):raise ValueError('Shared roster count exceeds capture bounds.')
    raw=[dict(slots=[dict(slot='CPT',player=r['Captain'])]+[dict(slot='FLEX',player=p) for p in r['Flex']]) for r in rows]
    parsed=_archive_rosters(raw,players,'showdown',lambda:False)
    if not field:
        identities=[tuple(sorted((s['role'],s['key']) for s in r)) for r in parsed]
        if len(set(identities))!=len(identities):raise ValueError('Duplicate candidate identities are not a distinct shared universe.')
    return parsed


def _decode(rows,players):
    lookup={str(p['FlexID']):p for p in players};out=[]
    for row in rows:
        if not isinstance(row,list) or len(row)!=6 or any(not isinstance(s,dict) for s in row) or [s.get('role') for s in row]!=['CPT']+['FLEX']*5:raise ValueError('Invalid shared roster shape.')
        if len({s.get('key') for s in row})!=6 or any(s.get('key') not in lookup for s in row):raise ValueError('Unknown or repeated shared player identity.')
        out.append(ShowdownLineup(lookup[row[0]['key']],[lookup[s['key']] for s in row[1:]]))
    return out


def _validate(payload):
    snap=validate_snapshot(payload['snapshot'])
    if snap['kind']!='showdown' or not snap['pregame']:raise ValueError('A frozen pre-game Showdown snapshot is required; no historical means are reconstructed.')
    players=snap['players']
    if len(players)>MAX_PLAYERS:raise ValueError('Player capture bound exceeded.')
    catalog(players)
    parameters=payload['parameters']
    if set(parameters)!={'scenarios','seed','salary_cap'} or type(parameters['seed']) is not int or type(parameters['salary_cap']) is not int or parameters['salary_cap']<=0:raise ValueError('Invalid shared simulation parameters.')
    n=parameters['scenarios']
    if type(n) is not int or not 1<=n<=MAX_FRAMES or n*len(players)>MAX_VALUES:raise ValueError('Shared scenario/value bound exceeded.')
    for key,field in (('candidates',False),('field',True)):
        rows=payload[key]
        if not isinstance(rows,list) or not 0<len(rows)<=(MAX_FIELD if field else MAX_CANDIDATES):raise ValueError('Shared roster count exceeds capture bounds.')
        decoded=_decode(rows,players)
        if _rosters(decoded,players,field=field)!=rows:raise ValueError('Shared identity encoding changed.')
        active=_active(players)
        for row in decoded:validate_showdown_lineup(row,active,parameters['salary_cap'])
    frames=payload['frames'];keys={player_key(p) for p in _active(players)}
    if not isinstance(frames,list) or len(frames)!=n:raise ValueError('Incomplete shared scenario sequence.')
    for row in frames:
        if not isinstance(row,dict) or set(row)!=keys or any(type(v) not in (int,float) or not math.isfinite(v) for v in row.values()):raise ValueError('Shared outcomes have missing identities or nonfinite values.')
    if payload.get('historical_qualified') is not False or payload.get('selection_evaluated') is not False or payload.get('scope')!='diagnostic_shared_sample':raise ValueError('Capture is not historical qualification or a feasible portfolio trial.')
    return snap


def load_trial(path):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>MAX_BYTES:raise ValueError('Unsupported shared trial artifact.')
    with path.open('rb') as handle:raw=handle.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:raise ValueError('Shared trial byte bound exceeded.')
    value=json.loads(raw)
    if value.get('schema')!=1 or value.get('trial_id')!=fingerprint(value.get('payload')):raise ValueError('Shared trial checksum mismatch.')
    if value['payload'].get('model_version')!=model_version():raise ValueError('Simulation implementation changed; shared evidence cannot be replayed with a different runtime.')
    if value['payload'].get('runtime')!=runtime_identity():raise ValueError('Python/runtime identity changed; shared evidence cannot be replayed here.')
    _validate(value['payload'])
    return value


def capture_trial(snapshot,candidates,field,path,*,scenarios=1000,seed=90210,salary_cap=50000,cancelled=lambda:False,progress=lambda a,b,s:None):
    """One explicit SIM, one candidate universe and one frozen opponent field.

    Uses the existing diagnostic transform to observe exact draws unchanged.
    Capture is opt-in through this API; normal builds and ranking-bank capture
    are untouched. Selection/rule variants and historical outcomes are not run.
    """
    _check(cancelled);path=Path(path)
    if path.exists():raise FileExistsError('Preserve the existing trial; choose a new destination.')
    snap=validate_snapshot(deepcopy(snapshot));players=snap['players'];catalog(players);_active(players)
    if snap['kind']!='showdown' or not snap['pregame']:raise ValueError('A frozen pre-game Showdown snapshot is required.')
    if type(scenarios) is not int or not 1<=scenarios<=MAX_FRAMES or len(players)>MAX_PLAYERS or scenarios*len(players)>MAX_VALUES:raise ValueError('Shared scenario/player bound exceeded.')
    if type(seed) is not int or type(salary_cap) is not int or salary_cap<=0:raise ValueError('Invalid shared simulation parameters.')
    payload=dict(scope='diagnostic_shared_sample',historical_qualified=False,selection_evaluated=False,snapshot=deepcopy(snapshot),model_version=model_version(),runtime=runtime_identity(),
        candidates=_rosters(candidates,players),field=_rosters(field,players,field=True),parameters=dict(scenarios=scenarios,seed=seed,salary_cap=salary_cap),frames=[])
    if len(json.dumps(payload,allow_nan=False).encode('utf-8'))>MAX_BYTES:raise ValueError('Shared trial byte bound exceeded.')
    def record(outcomes):
        _check(cancelled);payload['frames'].append(dict(outcomes));return outcomes
    result=simulate_showdown(_decode(payload['candidates'],players),deepcopy(players),scenarios=scenarios,field_lineup_count=len(field),
        salary_cap=salary_cap,seed=seed,diagnostic_field=_decode(payload['field'],players),outcome_transform=record,
        cancel_callback=cancelled,progress_callback=progress,scenario_cache=False)
    _check(cancelled)
    if result['report'].get('scenarios')!=scenarios:raise ValueError('Incomplete shared capture; no partial artifact saved.')
    _validate(payload)
    value=dict(schema=1,trial_id=fingerprint(payload),created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),payload=payload)
    if len(json.dumps(value,allow_nan=False).encode('utf-8'))>MAX_BYTES:raise ValueError('Shared trial byte bound exceeded.')
    _check(cancelled);_publish(path,value)
    return dict(path=str(path),trial_id=value['trial_id'],candidates=len(candidates),field=len(field),scenarios=scenarios,result=result)


def replay_trial(path,*,cancelled=lambda:False,progress=lambda a,b,s:None):
    _check(cancelled);value=load_trial(path);p=value['payload'];players=p['snapshot']['inputs']['players'];index=0
    def frozen(unused):
        nonlocal index
        _check(cancelled);row=dict(p['frames'][index]);index+=1;return row
    result=simulate_showdown(_decode(p['candidates'],players),deepcopy(players),field_lineup_count=len(p['field']),
        **p['parameters'],diagnostic_field=_decode(p['field'],players),outcome_transform=frozen,
        cancel_callback=cancelled,progress_callback=progress,scenario_cache=False)
    _check(cancelled)
    if index!=p['parameters']['scenarios'] or result['report'].get('scenarios')!=index:raise ValueError('Incomplete shared replay; no comparison published.')
    return dict(trial_id=value['trial_id'],result=result,scope=p['scope'],historical_qualified=False)
