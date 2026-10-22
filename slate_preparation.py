"""Bounded read-only coverage planning through existing validation/reuse gates."""
from contextlib import closing
from itertools import islice
from pathlib import Path
import copy
import sqlite3
import time
from build_snapshots import validate_snapshot, fingerprint
from showdown_library import _structure, validate_library

PREPARATION_SECONDS = 12*3600
STORAGE_BYTES = 8*1024**3
ROSTER_LIMIT = 50_000_000


def screening_request(snapshot):
    from captain_pool import prepare_captain_pool
    from compute_settings import deep_candidate_budget
    from showdown_screening import settings
    inputs=snapshot['inputs'];recipe=inputs['recipe']
    count=recipe.get('requested_lineups',150);options=recipe.get('deep_compute',{})
    players,rules,_=prepare_captain_pool(copy.deepcopy(inputs['players']),inputs['rules'],count)
    profile=inputs.get('calibration',{}).get('showdown_field_profile')
    return players,dict(limit=deep_candidate_budget(count,options,False),
        salary_cap=recipe.get('salary_cap',50000),salary_strategy=recipe.get('salary_strategy','Near Cap'),
        rules=rules,screening=settings(options,recipe.get('nfl_sim_scenarios',1000),profile))


def plan_preparation(snapshot, known, folder, *, cancelled=lambda:False, progress=lambda text:None,
                     seconds=10, screening_folder=None):
    from portfolio_rules import player_key
    from showdown_simulation import active_showdown_players
    from showdown_screening import load_screening
    snapshot=validate_snapshot(snapshot)
    if snapshot['inputs']['recipe']['contest_kind']!='showdown':
        raise ValueError('Prepare this slate currently supports NFL Showdown. Classic Long Search remains available in Advanced.')
    players,request=screening_request(snapshot)
    structure=_structure(snapshot['inputs']['players'],request['salary_cap'])
    active=active_showdown_players(copy.deepcopy(players))
    locked={player_key(p) for p in active if p.get('LockCpt')}
    captains={player_key(p) for p in active if not p.get('FadeCpt') and not p.get('LockFlex') and (not locked or player_key(p) in locked)}
    if not captains or request['limit']<len(captains):
        raise ValueError('Preparation requires an eligible Captain pool and a screening budget covering every Captain.')
    profile=snapshot['inputs'].get('calibration',{}).get('showdown_field_profile')
    if profile:
        from learning_db import history_db_path
        from showdown_history_field import revalidate_profile
        revalidate_profile(profile,history_db_path(),players,
            snapshot['inputs']['recipe'].get('nfl_field_preset','150-Max'),cancelled,progress)
    root=Path(folder)
    automatic=root/('auto-showdown-'+fingerprint(structure)+'.sdfull')
    paths=[str(automatic),*list(known)[:25]]
    if root.is_dir():paths.extend(str(p) for p in islice(root.iterdir(),48))
    seen=set();complete=[];partial=[];deadline=time.monotonic()+seconds
    def check():
        if cancelled():raise InterruptedError('Slate preparation coverage check cancelled.')
    for raw in paths:
        check()
        if time.monotonic()>deadline:
            progress('Coverage discovery allowance reached; uninspected libraries are not certified.')
            break
        path=Path(raw)
        if path.suffix.lower() not in ('.sdlib','.sdfull','.sqlite'):continue
        key=str(path.resolve()).casefold()
        if key in seen:continue
        seen.add(key)
        if not path.exists():continue
        try:
            if path.is_symlink():raise ValueError('Automatic preparation does not write through a library symlink.')
            info=validate_library(path,players,salary_cap=request['salary_cap'],allow_partial=True)
            if not captains.issubset(info['captains']):raise ValueError('Saved library does not cover the current Captain pool.')
            if path.suffix=='.sdfull':
                from showdown_full_library import _read
                stored=_read(path)['structure']
            else:
                from showdown_library import _read
                with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=1)) as con:
                    stored=_read(con,verify_count=False)['structure']
            if not info['complete'] and stored['salary_cap']!=request['salary_cap']:
                raise ValueError('Partial library uses a different preparation cap; resume explicitly in Advanced.')
        except (OSError,ValueError,sqlite3.Error) as exc:
            if path==automatic:raise ValueError('Automatic library needs review: '+str(exc)) from exc
            progress('Library skipped: '+path.name+' — '+str(exc));continue
        check()
        item=dict(path=str(path.resolve()),scope=info['captains'],info=info)
        progress(f"Coverage checked: {path.name}; {info['saved']:,} saved; "+('complete' if info['complete'] else 'partial'))
        if info['complete']:complete.append(item)
        else:partial.append(item)
    # A valid exact cache wins over rebuilding screening elsewhere. These are
    # the same digest, score, coverage, model and Python gates used by Generate.
    for item in complete:
        check()
        reused=load_screening(item['path'],players,**request,folder=screening_folder,cancelled=cancelled)
        if reused:
            return dict(item,stage='ready',screening_reused=reused[1]['screening_reused'])
    check()
    if complete:return dict(complete[0],stage='screening')
    if partial:return dict(partial[0],stage='rosters')
    if automatic.exists():raise ValueError('Automatic library was not verified; inspect it in Advanced before retrying.')
    return dict(path=str(automatic),scope=None,info=dict(saved=0,complete=False),stage='rosters')
