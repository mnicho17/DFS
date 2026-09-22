"""Outcome checks for recorded simulations, deduplicated by player and scheduled game."""
from collections import Counter,defaultdict
import datetime as dt
import json
import math
from pathlib import Path
import statistics
from scoring_distributions import load_distribution


def compare_distributions(conn,import_id,input_id,kind,scores,folder):
    rows=[];skipped=0;candidates=[]
    if input_id:
        for path in (Path(folder)/'scoring-distributions').glob(input_id+'-*.json'):
            try:
                value=load_distribution(path);p=value['payload']
                if p['input_id']!=input_id or p['kind']!=kind:continue
                finished=dt.datetime.fromisoformat(p['finished_at']);started=dt.datetime.fromisoformat(p['started_at'])
                if not finished.tzinfo or not started.tzinfo or started>finished:continue
                candidates.append((finished,value))
            except (OSError,ValueError,TypeError,KeyError):continue
    seen=set()
    for finished,value in sorted(candidates,key=lambda item:(item[0],item[1]['capture_id']),reverse=True):
        p=value['payload'];identities=Counter((r.get('game'),r.get('name')) for r in p['players'])
        for row in p['players']:
            key=(row.get('game'),row.get('name'))
            if not key[0] or identities[key]!=1:skipped+=1;continue
            try:kickoff=dt.datetime.fromisoformat(key[0].split('|')[-1])
            except (ValueError,TypeError):skipped+=1;continue
            if not kickoff.tzinfo or finished>=kickoff:skipped+=1;continue
            actual=scores.get(row['name'])
            if actual is None or not math.isfinite(actual) or key in seen:continue
            seen.add(key)
            rows.append(dict(row,actual=actual,kind=kind,model=p['model_version'],capture_id=value['capture_id'],input_id=input_id,
                captured_at=p['finished_at'],scenarios=p['scenarios']))
    payload=dict(version=1,rows=rows,skipped=skipped,status='matched' if rows else 'unavailable',
        reason='Exact snapshot ID and completed pre-kickoff capture required; older inputs do not recreate historical simulation ranges.')
    conn.execute('CREATE TABLE IF NOT EXISTS distribution_validations(import_id TEXT PRIMARY KEY,payload TEXT)')
    conn.execute('INSERT OR REPLACE INTO distribution_validations VALUES (?,?)',(import_id,json.dumps(payload,allow_nan=False)))
    return payload


def validation_report(conn):
    lines=['','Recorded scoring-distribution validation',
        '- Uses actual base-player DK scores against ranges recorded by completed pre-game simulations. Captain is the same outcome at 1.5x, not an extra observation. No simulations are rerun or model weights updated.']
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='distribution_validations'").fetchone():
        return lines+['- No recorded comparisons yet. Complete a new NFL SIM build before kickoff, then import results. Deep Classic/Showdown and Fast Classic SIM save automatically.']
    all_rows=[]
    for name,encoded in conn.execute('SELECT h.file_name,d.payload FROM distribution_validations d JOIN historical_imports h ON h.import_id=d.import_id'):
        p=json.loads(encoded);all_rows.extend(p['rows'])
        lines.append(f"- {name}: {len(p['rows'])} player comparisons; {p['status']}; {p['skipped']} capture rows excluded for missing/ambiguous game identity or timing.")
    if not all_rows:return lines+['- No qualifying ranges saved for these results. Historical snapshots alone cannot supply them. New pre-game builds capture them automatically; no extra long run is needed.']
    outcomes=defaultdict(list)
    for r in all_rows:outcomes[(r['game'],r['name'])].append(r['actual'])
    conflicts={k for k,v in outcomes.items() if max(v)-min(v)>.02}
    retained={}
    for r in sorted(all_rows,key=lambda r:(dt.datetime.fromisoformat(r['captured_at']),r['capture_id']),reverse=True):
        if (r['game'],r['name']) in conflicts:continue
        retained.setdefault((r['kind'],r['game'],r['name']),r)
    rows=list(retained.values());unique={(r['game'],r['name']) for r in rows};games={r['game'] for r in rows}
    lines.append(f"- Evidence: {len(games)} scheduled games; {len(unique)} unique player-game outcomes; {len(all_rows)-len(rows)} repeated or conflicting comparison rows excluded; {len(conflicts)} conflicting player-game outcomes excluded.")
    lines.append('- Latest qualifying capture retained per format/player/game. Classic and Showdown are shown separately and can share outcomes; do not add their samples together. Different model versions are not pooled. Players from the same game remain correlated.')
    grouped=defaultdict(list)
    for r in rows:grouped[(r['kind'],r['model'])].append(r)
    for (kind,model),values in sorted(grouped.items()):
        lines.append(f"- {kind.title()} | model {model[:12]} | {len(values)} player-game observations; scenarios per capture {min(r['scenarios'] for r in values)}–{max(r['scenarios'] for r in values)}.")
        groups=[('All covered roles',values)]
        for label,field in [('Position','position'),('Recorded role','role')]:
            for v in sorted({r[field] for r in values}):groups.append((label+': '+v,[r for r in values if r[field]==v]))
        for label,group in groups:
            below=statistics.mean(r['actual']<r['p10'] for r in group)*100
            above=statistics.mean(r['actual']>r['p90'] for r in group)*100
            expected_lo=statistics.mean(r['expected_below_p10'] for r in group)*100
            expected_hi=statistics.mean(r['expected_above_p90'] for r in group)*100
            bias=statistics.mean(r['actual']-r['mean'] for r in group)
            mae=statistics.mean(abs(r['actual']-r['mean']) for r in group)
            lines.append(f"  {label}: n={len(group)}; inside p10–p90 {100-below-above:.1f}%; below {below:.1f}% (SIM {expected_lo:.1f}%); above {above:.1f}% (SIM {expected_hi:.1f}%); mean-score MAE {mae:.2f}; actual-minus-SIM-mean bias {bias:+.2f}.")
        for r in sorted(values,key=lambda r:abs(r['actual']-r['mean']),reverse=True)[:5]:
            lines.append(f"  Largest mean miss: {r['name']} [{r['position']}]; actual {r['actual']:.2f}; SIM mean {r['mean']:.2f}; p10/p50/p90 {r['p10']:.2f}/{r['p50']:.2f}/{r['p90']:.2f}; capture {r['capture_id'][:12]}.")
    lines.append('- p10/p90 are simulated percentiles, not hard floors/ceilings. Boundaries count inside the range; SIM tail rates account for discrete ties and zero masses. These small, correlated samples diagnose model behavior, not proven predictive accuracy or an automatic tuning recommendation.')
    return lines
