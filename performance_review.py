"""Cached descriptive construction review and source-labelled player histories."""
import csv
import datetime as dt
import hashlib
import json
import math
import os
import re
import statistics
from pathlib import Path
from collections import Counter,defaultdict
from results_audit import _number, _player_results


def ensure_tables(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS construction_reviews(import_id TEXT PRIMARY KEY, file_hash TEXT, payload TEXT, checked_at TEXT);
    CREATE TABLE IF NOT EXISTS contest_player_scores(import_id TEXT, player TEXT, points REAL, result_date TEXT, date_source TEXT, PRIMARY KEY(import_id,player));
    CREATE TABLE IF NOT EXISTS seasonal_player_stats(season INTEGER, week INTEGER, player_id TEXT, player TEXT, team TEXT, position TEXT, ppr REAL, raw_json TEXT, fetched_at TEXT, PRIMARY KEY(season,week,player_id));
    CREATE TABLE IF NOT EXISTS seasonal_stats_status(season INTEGER PRIMARY KEY, checked_at TEXT, status TEXT);
    """)
    columns={row[1] for row in conn.execute('PRAGMA table_info(seasonal_stats_status)')}
    if 'source_url' not in columns:conn.execute('ALTER TABLE seasonal_stats_status ADD COLUMN source_url TEXT')


def scoped_metadata(conn,import_id):
    """Only exports actually matched to this contest; no newest-slate salary guesses."""
    from learning_db import _normalize_roster_token
    rows=conn.execute("""SELECT DISTINCT p.name,p.team,p.opponent,p.position,p.salary,p.slot
        FROM lineup_players p JOIN lineups l ON l.lineup_id=p.lineup_id JOIN exports e ON e.export_id=l.export_id
        WHERE e.sport='NFL' AND l.export_id IN (SELECT matched_export_id FROM historical_results WHERE import_id=? AND matched_export_id IS NOT NULL)""",(import_id,)).fetchall()
    options=defaultdict(list)
    for name,team,opp,pos,salary,slot in rows:
        key=('@cpt:' if slot=='CPT' else '')+_normalize_roster_token(name)
        options[key].append(dict(team=team,opponent=opp,position=pos,salary=salary))
    # Ambiguous metadata is not silently resolved by export order.
    return {key:values[0] for key,values in options.items() if all(v==values[0] for v in values)}


def snapshot_metadata(folder,date,observed):
    """Date-matched input snapshots supply stable metadata, never retrospective forecasts."""
    if not date or not observed or not Path(folder).is_dir():return {}
    from build_snapshots import load_snapshot
    from learning_db import _normalize_roster_token
    options=defaultdict(list)
    for path in sorted(Path(folder).glob('*.json')):
        try:
            snapshot=load_snapshot(str(path));players=snapshot['inputs']['players']
            if snapshot['inputs']['recipe'].get('sport')!='NFL':continue
            dates={result_date(str(p.get('GameInfo') or '')) for p in players}
            names={_normalize_roster_token(p.get('Name')) for p in players}
            if dates!={date} or not observed.issubset(names):continue
            for p in players:
                name=_normalize_roster_token(p.get('Name'))
                if not name:continue
                base=dict(team=p.get('Team'),opponent=p.get('Opponent'),position=p.get('Position'),salary=p.get('FlexSalary'))
                options[name].append(base)
                if p.get('CptSalary'):options['@cpt:'+name].append(dict(base,salary=p['CptSalary']))
        except (ValueError,OSError,KeyError,TypeError):continue
    return {k:v[0] for k,v in options.items() if all(row==v[0] for row in v)}


def construction(signature,metadata):
    rows=[metadata.get(k) for k in signature]
    if not all(rows) or not all(p.get('team') and p.get('position') in {'QB','RB','WR','TE','K','DST'} for p in rows):return None
    counts=Counter(p['team'] for p in rows)
    positions=Counter(p['position'] for p in rows)
    features=[]
    if len(signature)==6 and any(k.startswith('@cpt:') for k in signature):
        captain=rows[next(i for i,k in enumerate(signature) if k.startswith('@cpt:'))]
        features += ['Team split '+ '-'.join(map(str,sorted(counts.values(),reverse=True))),
                     'Captain '+captain['position'],str(positions['QB'])+' quarterbacks',
                     str(positions['K']+positions['DST'])+' kickers/defenses']
        if captain['position'] in {'WR','TE'}:
            features.append('Receiver Captain with QB' if any(p['position']=='QB' and p['team']==captain['team'] for p in rows) else 'Receiver Captain without QB')
        if captain['position']=='QB':
            n=sum(p['position'] in {'WR','TE'} and p['team']==captain['team'] for p in rows)
            features.append('QB Captain + '+str(n)+' receivers')
    elif len(signature)==9:
        qbs=[p for p in rows if p['position']=='QB']
        if len(qbs)!=1:return None
        qb=qbs[0]
        n=sum(p['position'] in {'WR','TE'} and p['team']==qb['team'] for p in rows)
        features.append('QB + '+str(n)+' receivers')
        if qb.get('opponent'):
            features.append('With opposing skill player' if any(p['team']==qb['opponent'] and p['position'] in {'RB','WR','TE'} for p in rows) else 'No opposing skill player')
        for pos,base in [('RB',2),('WR',3),('TE',1)]:
            if positions[pos]>base:features.append('FLEX '+pos)
        if all(p.get('opponent') for p in rows):
            secondary=any(a['team']==b['opponent'] and a['team'] not in {qb['team'],qb.get('opponent')} and a['position'] in {'RB','WR','TE'} and b['position'] in {'RB','WR','TE'} for a in rows for b in rows)
            features.append('Secondary opposing pair' if secondary else 'No secondary opposing pair')
    else:return None
    return features


def bucket():return dict(n=0,mapped=0,features=Counter(),salary_n=0,salary_sum=0,own_n=0,own_sum=0,duplicates=0)


def add(b,signature,meta,ownership):
    b['n']+=1
    features=construction(signature,meta)
    if features is not None:b['mapped']+=1;b['features'].update(features)
    salaries=[_number((meta.get(k) or {}).get('salary')) for k in signature]
    if all(v is not None and v>0 for v in salaries):b['salary_n']+=1;b['salary_sum']+=sum(salaries)
    if all(k in ownership for k in signature):b['own_n']+=1;b['own_sum']+=statistics.mean(ownership[k] for k in signature)


def result_date(name):
    m=re.search(r'(?<!\d)(20\d{2})[-_/](\d{2})[-_/](\d{2})(?!\d)',name)
    if m:parts=tuple(map(int,m.groups()))
    else:
        m=re.search(r'(?<!\d)(\d{2})[-_/](\d{2})[-_/](20\d{2})(?!\d)',name)
        if not m:return ''
        mo,day,year=map(int,m.groups());parts=(year,mo,day)
    try:return dt.date(*parts).isoformat()
    except ValueError:return ''


def analyze_saved_results(*,db_path=None,username='',cancelled=lambda:False,progress=lambda text:None):
    from learning_db import _connect,init_historical_import_tables,_field_roster_signature,_normalize_roster_token,_dk_username
    conn=_connect(db_path)
    messages=[];completed=0
    try:
        init_historical_import_tables(conn);ensure_tables(conn)
        imports=conn.execute('SELECT import_id,source_path,file_name FROM historical_imports').fetchall()
        for import_id,path,name in imports:
            if cancelled():break
            field=conn.execute('SELECT MAX(field_size),MAX(roster_size),MAX(sport) FROM contest_field_summaries WHERE import_id=?',(import_id,)).fetchone()
            if not field or not field[0] or field[1] not in (6,9) or field[2] not in ('NFL','UNKNOWN'):
                messages.append(name+': complete NFL field unavailable');continue
            progress('Analyzing '+name)
            if not path or not os.path.isfile(path):messages.append(name+': original file unavailable; previous cached review retained');continue
            inferred_date=result_date(name) or result_date(Path(path).name)
            meta=scoped_metadata(conn,import_id)
            scores,own,problem=_player_results(path,_normalize_roster_token)
            from learning_db import history_db_path
            snap_meta=snapshot_metadata(Path(db_path or history_db_path()).parent/'snapshots',inferred_date,{k for k in scores if not k.startswith('@cpt:')})
            for key,value in snap_meta.items():meta.setdefault(key,value)
            if field[2]=='UNKNOWN' and not meta and 'NFL' not in name.upper():
                messages.append(name+': sport unverified; matching NFL metadata needed');continue
            groups={key:bucket() for key in ('field','top5','top1','winners','yours')}
            duplicates=Counter();subsets={key:Counter() for key in groups if key!='field'}
            digest=hashlib.sha256()
            with open(path,'rb') as handle:
                for block in iter(lambda:handle.read(1024*1024),b''):digest.update(block)
            meta_key=hashlib.sha256(json.dumps(dict(metadata=meta,date=inferred_date),sort_keys=True).encode()).hexdigest()
            cached=conn.execute('SELECT file_hash,payload FROM construction_reviews WHERE import_id=?',(import_id,)).fetchone()
            if cached and cached[0]==digest.hexdigest():
                previous=json.loads(cached[1])
                if previous.get('metadata_key')==meta_key and previous.get('username')==_dk_username(username):
                    continue
            with open(path,newline='',encoding='utf-8-sig') as handle:
                reader=csv.DictReader(handle)
                for index,row in enumerate(reader):
                    if index%2500==0:
                        if cancelled():return dict(completed=completed,cancelled=True,message='Analysis cancelled; completed contests remain saved.')
                        progress(f'Analyzing {name}: {index:,} entries')
                    rank=_number(row.get('Rank'))
                    if rank is None or rank<1:continue
                    signature=_field_roster_signature(str(row.get('Lineup') or ''))
                    if len(signature)!=field[1]:continue
                    key=hashlib.blake2b(json.dumps(signature).encode(),digest_size=16).digest()
                    duplicates[key]+=1
                    targets=['field']
                    if rank<=math.ceil(field[0]*.05):targets.append('top5')
                    if rank<=math.ceil(field[0]*.01):targets.append('top1')
                    if rank==1:targets.append('winners')
                    if username and _dk_username(row.get('EntryName'))==_dk_username(username):targets.append('yours')
                    for target in targets:
                        add(groups[target],signature,meta,own)
                        if target!='field':subsets[target][key]+=1
            groups['field']['duplicates']=sum(n for n in duplicates.values() if n>1)
            for target,counts in subsets.items():groups[target]['duplicates']=sum(n for k,n in counts.items() if duplicates[k]>1)
            payload=dict(name=name,groups=groups,username=_dk_username(username),format='Showdown' if field[1]==6 else 'Classic',
                         date=inferred_date,score_gap=problem,metadata_key=meta_key,metadata_source='matched exports and date-matched snapshot consensus')
            # FLEX scores are the base observations. Captain is never counted as another game.
            base={k:v for k,v in scores.items() if not k.startswith('@cpt:')}
            with conn:
                conn.execute('INSERT OR REPLACE INTO construction_reviews VALUES (?,?,?,?)',(import_id,digest.hexdigest(),json.dumps(payload),dt.datetime.now(dt.timezone.utc).isoformat()))
                if base:
                    conn.execute('DELETE FROM contest_player_scores WHERE import_id=?',(import_id,))
                    conn.executemany('INSERT INTO contest_player_scores VALUES (?,?,?,?,?)',[(import_id,k,v,payload['date'],'filename-unverified' if payload['date'] else 'unknown') for k,v in base.items()])
            completed+=1
        return dict(completed=completed,cancelled=cancelled(),message=f'Analyzed {completed} contests. '+ '; '.join(messages))
    finally:conn.close()


def refresh_season_stats(season,*,db_path=None,cancelled=lambda:False,progress=lambda text:None,fetcher=None):
    from learning_db import _connect,init_historical_import_tables,_normalize_roster_token
    from nfl_auto_data import _fetch_nflverse_season_rows, NFLVERSE_PLAYER_STATS_URL
    fetcher=fetcher or _fetch_nflverse_season_rows
    if not 2000<=int(season)<=dt.datetime.now().year:raise ValueError('Choose a season from 2000 through the current year.')
    conn=_connect(db_path);counts={}
    try:
        init_historical_import_tables(conn);ensure_tables(conn)
        for year in (int(season),int(season)-1):
            if cancelled():break
            progress(f'Downloading nflverse weekly statistics: {year}')
            rows=fetcher(year)
            stamp=dt.datetime.now(dt.timezone.utc).isoformat();valid={}
            for row in rows:
                if str(row.get('season_type') or 'REG').upper()!='REG':continue
                try:week=int(row.get('week') or 0);row_year=int(row.get('season') or 0)
                except (TypeError,ValueError):continue
                pid=str(row.get('player_id') or '');name=_normalize_roster_token(row.get('player_display_name') or row.get('player_name'))
                if not pid or not name or row_year!=year or not 1<=week<=22:continue
                valid[(week,pid)]=(year,week,pid,name,str(row.get('recent_team') or row.get('team') or ''),str(row.get('position') or ''),_number(row.get('fantasy_points_ppr')),json.dumps(row),stamp)
            if cancelled():break
            with conn:
                if valid:
                    # Upsert corrections without deleting previously available games on a partial download.
                    conn.executemany('INSERT OR REPLACE INTO seasonal_player_stats VALUES (?,?,?,?,?,?,?,?,?)',valid.values())
                conn.execute('INSERT OR REPLACE INTO seasonal_stats_status(season,checked_at,status,source_url) VALUES (?,?,?,?)',(year,stamp,'ok' if valid else 'unavailable; existing cache retained',NFLVERSE_PLAYER_STATS_URL.format(season=year)))
            counts[year]=len(valid)
        return dict(completed=sum(counts.values()),cancelled=cancelled(),message='Weekly player rows refreshed: '+', '.join(f'{y}: {n:,}' for y,n in counts.items())+'. Missing seasons retain their previous cache.')
    finally:conn.close()


def review_report(conn,username=''):
    from learning_db import _dk_username
    ensure_tables(conn)
    lines=['','Construction & Scenario Review — descriptive, no automatic tuning']
    reviews=conn.execute('SELECT payload,checked_at FROM construction_reviews ORDER BY checked_at').fetchall()
    if not reviews:lines.append('- Click Analyze Saved Results to compare field constructions and store player scores.')
    for encoded,checked in reviews:
        p=json.loads(encoded);g=p['groups'];field=g['field'];lines.append(f"- {p['name']} | {p['format']} | analyzed {checked[:10]}")
        for label,key in [('Field','field'),('Top 5%','top5'),('Top 1%','top1'),('Winners','winners'),('Yours','yours')]:
            if key=='yours' and _dk_username(username)!=p.get('username'):continue
            b=g[key]
            if not b['n']:continue
            salary=f"${b['salary_sum']/b['salary_n']:,.0f} ({b['salary_n']} covered)" if b['salary_n'] else 'unknown'
            own=f"{b['own_sum']/b['own_n']:.1f}% ({b['own_n']} covered)" if b['own_n'] else 'unknown'
            lines.append(f"  {label}: {b['n']:,} entries; construction metadata {b['mapped']:,}/{b['n']:,}; duplicated {100*b['duplicates']/b['n']:.1f}%; average salary {salary}; average slot ownership {own}.")
        keys=set(field['features'])|set(g['top1']['features'])
        ranked=sorted(keys,key=lambda k:abs(g['top1']['features'].get(k,0)/max(1,g['top1']['mapped'])-field['features'].get(k,0)/max(1,field['mapped'])),reverse=True)
        for key in ranked[:14]:
            parts=[]
            for label,group in [('field',field),('top 5%',g['top5']),('top 1%',g['top1']),('winners',g['winners']),('yours',g['yours'])]:
                if label=='yours' and _dk_username(username)!=p.get('username'):continue
                if group['mapped']:parts.append(f"{label} {100*group['features'].get(key,0)/group['mapped']:.1f}% ({group['features'].get(key,0)}/{group['mapped']})")
            lines.append('    '+key+': '+' | '.join(parts))
        if not field['mapped']:lines.append('  Team/position constructions need matching export history or date-matched snapshots; no current-slate salary guesses were used.')
    lines += ['- Rank cutoffs include ties. Construction percentages use fully mapped lineups; ownership uses covered lineups. Cohorts overlap.',
              '- Top-finisher frequency alone is not evidence of an edge. Compare its field frequency and independent games.',
              '- Scores cannot identify blowouts or passing/rushing game scripts by themselves. No game-script probabilities are fitted here.']
    sim_rows=conn.execute("""SELECT DISTINCT h.import_id,l.lineup_id,l.sim_top_one_pct,l.sim_top_five_pct,
        l.sim_ceiling,h.actual_points,h.rank_text,h.field_size,h.entry_name
        FROM historical_results h JOIN lineups l ON l.lineup_id=h.matched_lineup_id
        JOIN exports e ON e.export_id=l.export_id
        WHERE e.sport='NFL' AND l.sim_scenarios>0 AND h.actual_points IS NOT NULL""").fetchall()
    comparisons=defaultdict(dict)
    for import_id,lid,top1,top5,ceiling,actual,rank,size,entry in sim_rows:
        if username and _dk_username(entry)!=_dk_username(username):continue
        if not size or _number(rank) is None:continue
        comparisons[import_id][lid]=(top1,top5,ceiling,actual,_number(rank),size)
    if comparisons:
        lines.append('- Saved SIM expectations versus actual finishes (unique matched lineups per contest, not independent games):')
        for import_id,rows in comparisons.items():
            name=conn.execute('SELECT file_name FROM historical_imports WHERE import_id=?',(import_id,)).fetchone()[0]
            values=list(rows.values());parts=[]
            for index,label,fraction in [(0,'top 1%',.01),(1,'top 5%',.05)]:
                usable=[v for v in values if v[index] is not None]
                if usable:parts.append(f"{label} expected {statistics.mean(v[index] for v in usable):.2f}% / observed {100*sum(v[4]<=math.ceil(v[5]*fraction) for v in usable)/len(usable):.2f}% ({len(usable)} lineups)")
            ceilings=[v for v in values if v[2] is not None]
            if ceilings:parts.append(f"above saved SIM ceiling {sum(v[3]>v[2] for v in ceilings)}/{len(ceilings)}")
            lines.append('  '+name+': '+'; '.join(parts))
        lines.append('- These selected-lineup comparisons share game outcomes and do not justify fitting probabilities from a single slate.')
    from score_reconciliation import reconciliation_report
    lines += reconciliation_report(conn)
    lines += player_history_report(conn)
    return lines


def nfl_season(date):
    value=dt.date.fromisoformat(date)
    return value.year-1 if value.month<=2 else value.year


def player_history_report(conn):
    lines=['','Player performance history']
    observed=defaultdict(list);undated=0;conflicts=0
    # Repeated contests on a filename date yield at most one observation per athlete.
    values=defaultdict(set)
    for name,points,date in conn.execute('SELECT player,points,result_date FROM contest_player_scores'):
        if not date:undated+=1;continue
        values[(name,date)].add(round(points,4))
    for (name,date),points in values.items():
        if len(points)!=1:conflicts+=1;continue
        observed[name].append((date,next(iter(points))))
    lines.append(f'- Contest DK scores: {sum(map(len,observed.values()))} deduplicated player/date observations; {undated} undated rows excluded from chronological averages; {conflicts} conflicting player/date groups excluded.')
    lines.append('- Contest dates come from filenames and are unverified; January/February belong to the previous NFL season. Imported DK results do not separate playoffs. Same-day entries and Captain appearances are not extra games; missing appearances are not zero scores.')
    for name,rows in sorted(observed.items(),key=lambda pair:(-len(pair[1]),pair[0]))[:15]:
        rows.sort();season=nfl_season(rows[-1][0]);season_rows=[v for d,v in rows if nfl_season(d)==season];recent=season_rows[-3:];older=season_rows[:-3]
        old=f'{statistics.mean(older):.2f} ({len(older)})' if older else 'not enough earlier observations'
        lines.append(f'  {name}: {season} imported DK average {statistics.mean(season_rows):.2f} ({len(season_rows)} dates); last {len(recent)} {statistics.mean(recent):.2f}; earlier {old}.')
    for season,checked,status in conn.execute('SELECT season,checked_at,status FROM seasonal_stats_status ORDER BY season DESC'):
        lines.append(f'- nflverse {season}: {status}; checked {checked[:19]} UTC. Regular-season PPR points, not DraftKings scoring.')
    focus={row[0] for row in conn.execute('SELECT DISTINCT player FROM contest_player_scores')}
    weekly=defaultdict(list)
    for season,week,pid,name,ppr,raw in conn.execute("SELECT season,week,player_id,player,ppr,raw_json FROM seasonal_player_stats WHERE position IN ('QB','RB','WR','TE') ORDER BY season DESC,week"):
        if ppr is not None and (not focus or name in focus):weekly[(season,pid,name)].append((week,ppr,json.loads(raw)))
    displayed=sorted(weekly.items(),key=lambda x:(x[0][2],-x[0][0]))
    if not focus:displayed=displayed[:30]
    for (season,pid,name),rows in displayed:
        recent=rows[-3:];old=rows[:-3]
        prior=f'{statistics.mean(v[1] for v in old):.2f} ({len(old)} games)' if old else 'not available'
        carries=[_number(v[2].get('carries')) for v in recent];targets=[_number(v[2].get('targets')) for v in recent]
        volume=[]
        for label,vals in [('carries',carries),('targets',targets)]:
            if vals and all(v is not None for v in vals):volume.append(f'{label} {statistics.mean(vals):.1f}')
        lines.append(f'  {name} {season}: season PPR {statistics.mean(v[1] for v in rows):.2f} ({len(rows)} observed games); last {len(recent)} {statistics.mean(v[1] for v in recent):.2f}; earlier {prior}; recent '+', '.join(volume)+'.')
    if weekly:lines.append('- Free PPR summaries cover QB/RB/WR/TE; kicker/defense performance uses imported DK scores. Source statistics may change after corrections.')
    if weekly and not focus:lines.append('- Showing the first 30 player-season records alphabetically; import contest results to focus on those players.')
    if not weekly:lines.append('- Use Refresh Free NFL Stats to cache the selected season and previous season. No subscription or API key is needed.')
    errors=defaultdict(list)
    forecast_seen=set()
    from learning_db import _normalize_roster_token
    forecasts=conn.execute("""SELECT DISTINCT p.name,p.slot,p.projection,c.result_date,e.created_at
        FROM historical_results h JOIN lineups l ON l.lineup_id=h.matched_lineup_id
        JOIN exports e ON e.export_id=l.export_id JOIN lineup_players p ON p.lineup_id=l.lineup_id
        JOIN (SELECT DISTINCT import_id,result_date FROM contest_player_scores) c ON c.import_id=h.import_id
        WHERE e.sport='NFL' ORDER BY e.created_at,l.lineup_index""").fetchall()
    for name,slot,projection,date,created in forecasts:
        normalized=_normalize_roster_token(name)
        # Join names in Python because stored normalization is deliberately shared with imports.
        if not date or projection is None or (normalized,date) not in values or len(values[(normalized,date)])!=1:continue
        if (normalized,date) in forecast_seen:continue
        actual=next(iter(values[(normalized,date)]))
        forecast_seen.add((normalized,date))
        errors[normalized].append((date,actual-float(projection)/(1.5 if slot=='CPT' else 1)))
    if errors:
        lines.append('- Saved forecast bias by player (actual minus forecast, Captain normalized; earliest matched export per date; pre-lock timing unverified):')
        for name,rows in sorted(errors.items(),key=lambda x:abs(statistics.mean(v[1] for v in x[1])),reverse=True)[:15]:
            rows.sort();year=nfl_season(rows[-1][0]);rows=[v for v in rows if nfl_season(v[0])==year];recent=rows[-3:];older=rows[:-3]
            earlier=f"{statistics.mean(v[1] for v in older):+.2f} ({len(older)} dates)" if older else 'insufficient earlier data'
            lines.append(f"  {name} {year}: average bias {statistics.mean(v[1] for v in rows):+.2f}; last {len(recent)} {statistics.mean(v[1] for v in recent):+.2f}; earlier {earlier}.")
    lines.append('- PPR and DK averages are never pooled. These are observed-game averages, not full-season totals or predictive validation. Projection mismatches use the original forecasts in Results audit.')
    return lines
