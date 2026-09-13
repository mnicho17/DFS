"""Automatic, provenance-preserving results comparisons without lineup exports."""
import datetime as dt
import json
import re
import statistics
from pathlib import Path
from zoneinfo import ZoneInfo
from build_snapshots import load_snapshot,save_snapshot
from repeatability import atomic_json
from projection_sources import number


def forecast_group(player):
    """Use frozen pre-game evidence, never the player's eventual score or current depth."""
    from nfl_eligibility import unavailable,depth
    if unavailable(player):return 'Recorded unavailable'
    pos=str(player.get('Position') or '').upper()
    order=depth(player)
    if pos=='QB':
        if player.get('NFLQBEligible') is True:return 'Recorded starters / eligible QBs'
        if order>1:return 'Backup QBs'
        if player.get('NFLQBEligible') is False:return 'Unverified / excluded QBs'
    if order==1:return 'Recorded starters / eligible QBs'
    if order>1:return 'Other depth roles / rotation'
    return 'Unknown recorded role'


def snapshot_counts(conn,username):
    from learning_db import _dk_username
    result=dict(contests=0,entries=0,unique=0)
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='result_snapshot_reviews'").fetchone():return result
    for encoded, in conn.execute('SELECT payload FROM result_snapshot_reviews'):
        p=json.loads(encoded)
        if p.get('status')!='matched' or p.get('username')!=_dk_username(username):continue
        result['contests']+=1
        result['unique']+=len(p.get('lineups',[]))
        result['entries']+=sum(r.get('entry_count',1) for r in p.get('lineups',[]))
    return result


def game_start(value):
    m=re.search(r'(\d{2}/\d{2}/20\d{2})\s+(\d{1,2}:\d{2}\s*[AP]M)\s+ET\b',str(value),re.I)
    if not m:return None
    try:return dt.datetime.strptime(m[1]+' '+m[2].replace(' ',''),'%m/%d/%Y %I:%M%p').replace(tzinfo=ZoneInfo('America/New_York'))
    except ValueError:return None


def remember_contests(template,contest_id,snapshot,folder):
    """Entry-update workflow records contest IDs automatically, never export evidence."""
    folder=Path(folder);path=folder/'snapshots'/(snapshot['input_id']+'.json')
    if not path.exists():save_snapshot(str(path),snapshot)
    registry=folder/'contest-snapshots.json'
    data=json.loads(registry.read_text(encoding='utf-8')) if registry.exists() else {}
    for c in template.contests():
        if contest_id is None or str(contest_id)==c['id']:
            data[c['id']]=sorted(set(data.get(c['id'],[])+[snapshot['input_id']]))
    atomic_json(registry,data)


def match_snapshot(folder,date,kind,observed,filename):
    from learning_db import _normalize_roster_token
    folder=Path(folder);ids=None;linked=False
    match=re.search(r'contest[-_]standings[-_](\d+)',filename,re.I)
    registry=folder/'contest-snapshots.json'
    if match and registry.exists():
        try:
            ids=json.loads(registry.read_text(encoding='utf-8')).get(match[1]);linked=bool(ids)
        except (ValueError,OSError):pass
    if not date and not linked:return None,'Contest date unavailable and no saved contest-ID association; forecast comparison unavailable.'
    if len(observed)<(6 if kind=='showdown' else 9):return None,'Insufficient player-result coverage to match the saved slate.'
    candidates=[]
    for path in (folder/'snapshots').glob('*.json'):
        if ids is not None and path.stem not in ids:continue
        try:
            snap=load_snapshot(str(path));inputs=snap['inputs'];players=inputs['players']
            if inputs['recipe'].get('sport')!='NFL' or inputs['recipe'].get('contest_kind')!=kind:continue
            names=[_normalize_roster_token(p.get('Name')) for p in players]
            if len(set(names))!=len(names) or not observed.issubset(names):continue
            starts=[game_start(p.get('GameInfo')) for p in players]
            if not all(starts):continue
            dates={s.date().isoformat() for s in starts}
            if len(dates)!=1 or (date and dates!={date}):continue
            created=dt.datetime.fromisoformat(snap['created_at'].replace('Z','+00:00'))
            if created.tzinfo is None or created>=min(starts):continue
            candidates.append((created,snap,next(iter(dates))))
        except (OSError,ValueError,TypeError,KeyError):continue
    if not candidates:return None,'No unambiguous matching snapshot recorded before the earliest game start.'
    if len({x[2] for x in candidates})>1:return None,'Matching snapshots span different dates; no forecast comparison was inferred.'
    candidates.sort(key=lambda x:x[0],reverse=True)
    latest=[x for x in candidates if x[0]==candidates[0][0]]
    if len({x[1]['input_id'] for x in latest})>1:return None,'Conflicting latest snapshots; forecast comparison unavailable.'
    snap=candidates[0][1]
    return snap,('contest-ID association' if linked else 'filename date and full player-name coverage')


def compare_snapshot(conn,import_id,name,folder,date,kind,scores,ownership,username,*,ownership_source='Provided comparison data'):
    from learning_db import _normalize_roster_token,_dk_username,_field_roster_signature
    observed={k.removeprefix('@cpt:') for k in scores}
    snap,reason=match_snapshot(folder,date,kind,observed,name)
    payload=dict(version=3,name=name,username=_dk_username(username),status='unavailable',reason=reason,ownership_source=ownership_source,
                 input_id=None,players=[],lineups=[],personal_entries=0)
    personal=[]
    for entry,actual,raw in conn.execute('SELECT entry_name,actual_points,raw_json FROM historical_results WHERE import_id=?',(import_id,)):
        if username and _dk_username(entry)==_dk_username(username):
            try:signature=_field_roster_signature(json.loads(raw or '{}').get('lineup',''))
            except (ValueError,TypeError):signature=()
            personal.append((signature,actual))
    payload['personal_entries']=len(personal)
    if snap:
        payload.update(status='matched',input_id=snap['input_id'],recorded_at=snap['created_at'],date=game_start(snap['inputs']['players'][0].get('GameInfo')).date().isoformat())
        lookup={_normalize_roster_token(p.get('Name')):p for p in snap['inputs']['players']}
        base={k:v for k,v in scores.items() if not k.startswith('@cpt:')}
        forecasts={};player_rows=[]
        for key,p in lookup.items():
            forecast=number(p.get('FlexProjection')) if p.get('ProjectionSource')!='Missing forecast' else None
            forecasts[key]=forecast
            if kind=='showdown':forecasts['@cpt:'+key]=number(p.get('CptProjection')) if forecast is not None else None
            slots=[('FLEX','ProjFlexOwnPct',key),('Captain','ProjCptOwnPct','@cpt:'+key)] if kind=='showdown' else [('Regular','ProjOwnPct',key)]
            for slot,ownkey,token in slots:
                expected=number(p.get(ownkey)) if p.get('OwnershipUnits')=='percent_of_entries' else None
                if expected is not None and expected>100:expected=None
                actual_own=ownership.get(token)
                if actual_own is not None and not 0<=actual_own<=100:actual_own=None
                # Base player scoring appears once; Captain ownership remains separate.
                player_rows.append(dict(player=key,slot=slot,source=p.get('ProjectionSource','Unknown'),
                    forecast_group=forecast_group(p),
                    projected=forecast if slot!='Captain' else None,actual=base.get(key) if slot!='Captain' else None,
                    expected_ownership=expected,actual_ownership=actual_own))
        payload['players']=player_rows
        unique={}
        for signature,actual in personal:
            if len(signature)!=(6 if kind=='showdown' else 9) or actual is None:continue
            if sum(k.startswith('@cpt:') for k in signature)!=(1 if kind=='showdown' else 0):continue
            if len({k.removeprefix('@cpt:') for k in signature})!=len(signature):continue
            values=[forecasts.get(k) for k in signature]
            actual_values=[base.get(k.removeprefix('@cpt:')) for k in signature]
            if any(v is None for v in values+actual_values):continue
            computed=sum(v*(1.5 if k.startswith('@cpt:') else 1) for k,v in zip(signature,actual_values))
            if abs(computed-actual)>.15:continue
            count=unique.get(signature,{}).get('entry_count',0)+1
            unique[signature]=dict(roster=signature,projected=sum(values),actual=actual,entry_count=count)
        payload['lineups']=list(unique.values())
    conn.execute('CREATE TABLE IF NOT EXISTS result_snapshot_reviews(import_id TEXT PRIMARY KEY,payload TEXT)')
    conn.execute('INSERT OR REPLACE INTO result_snapshot_reviews VALUES (?,?)',(import_id,json.dumps(payload)))
    return payload


def snapshot_report(conn,username):
    from learning_db import _dk_username
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='result_snapshot_reviews'").fetchone():return []
    lines=['','Automatic results-to-snapshot comparison — no lineup export required',
           'Submitted lineups come from your exact username in the standings. Snapshots supply recorded forecasts only when format, player names, contest date/ID and pre-game time checks match. No export records or historical predictions are fabricated.',
           'These are calibration diagnostics, not automatic model updates. Entries share outcomes; multiple contests from the same game are not independent evidence. Payouts and touches cannot be recovered from scores/ownership alone.']
    for encoded, in conn.execute('SELECT payload FROM result_snapshot_reviews ORDER BY import_id'):
        p=json.loads(encoded)
        if p['username']!=_dk_username(username):continue
        lines.append(f"- {p['name']}: {p['personal_entries']} username-matched entries; {p['status']}. {p['reason']}")
        if p['status']!='matched':continue
        lines.append(f"  Input {p['input_id'][:12]}, recorded {p['recorded_at']}. Latest matching snapshot before the earliest game; this may differ from the inputs used for a manually edited lineup.")
        usable=[r for r in p['players'] if r['projected'] is not None and r['actual'] is not None]
        if usable:
            errors=[r['actual']-r['projected'] for r in usable]
            lines.append(f"  All covered players (includes backup/inactive/unknown roles): {len(errors)} observations; MAE {statistics.mean(map(abs,errors)):.2f}; actual-minus-forecast bias {statistics.mean(errors):+.2f} points.")
            lines.append('  Role groups use saved pre-game evidence, not actual scores. Other depth roles can be useful rotation players; unknown is not inactive. No projection or eligibility changes are applied.')
            for group in ('Recorded starters / eligible QBs','Other depth roles / rotation','Backup QBs','Recorded unavailable','Unverified / excluded QBs','Unknown recorded role'):
                rows=[r for r in usable if r.get('forecast_group','Unknown recorded role')==group]
                if not rows:continue
                errors=[r['actual']-r['projected'] for r in rows]
                lines.append(f"  {group}: {len(rows)} players; MAE {statistics.mean(map(abs,errors)):.2f}; bias {statistics.mean(errors):+.2f} points.")
                for r in sorted(rows,key=lambda r:-abs(r['actual']-r['projected']))[:3]:
                    lines.append(f"    {r['player']}: forecast {r['projected']:.2f}, actual {r['actual']:.2f}; {r['source']}.")
        for slot in sorted({r['slot'] for r in p['players']}):
            if p.get('version',0)<3:continue
            usable=[r for r in p['players'] if r['slot']==slot and r['expected_ownership'] is not None and r['actual_ownership'] is not None]
            if usable:
                errors=[r['actual_ownership']-r['expected_ownership'] for r in usable]
                lines.append(f"  {slot} ownership: {len(errors)} players; MAE {statistics.mean(map(abs,errors)):.2f} percentage points; actual-minus-estimate bias {statistics.mean(errors):+.2f} pp.")
        lines.append('  '+p.get('ownership_source','Older ownership comparison hidden; Analyze Saved Results refreshes it from observed rosters.'))
        entries=p['lineups']
        lines.append(f"  Reconciled unique submitted lineups with complete forecasts: {len(entries)} (not independent games).")
        if entries:lines.append(f"  Lineup forecast MAE: {statistics.mean(abs(r['actual']-r['projected']) for r in entries):.2f} points.")
    return lines
