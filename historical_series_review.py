"""Bounded chronological review batches, with fresh final source/index checks."""
from contextlib import closing
from datetime import date
import hashlib,json
import analysis_imports as ai
from opponent_history import _connect,VERSION
from winner_portfolio_review import review_winners,ReviewLimitError

MAX_BATCHES=8
MAX_SERIES_ENTRIES=1200000
MAX_SERIES_PROFILES=10000


def combine_followups(reports):
    if not reports or len(reports)>MAX_BATCHES:raise ReviewLimitError('Choose between 1 and 8 bounded evaluation windows.')
    ordered=sorted(reports,key=lambda r:r['followup_from']);first=ordered[0];contests={};evidence={};excluded={};windows=[]
    for report in ordered:
        if report.get('review_mode')!='prior_qualified' or report['since']!=first['since'] or report['format']!=first['format']:raise ValueError('Batch history scopes or review modes disagree.')
        start=date.fromisoformat(report['followup_from']).isoformat();end=date.fromisoformat(report['through']).isoformat()
        if start>end or (windows and start<=windows[-1][1]):raise ValueError('Evaluation windows overlap or are invalid.')
        windows.append([start,end])
        for c in report['contests']:
            if not start<=c['date']<=end or c['contest_key'] in contests:raise ValueError('Duplicate contest or out-of-window evaluation receipt.')
            contests[c['contest_key']]=c
        for e in report['evidence']:
            previous=evidence.get(e['contest_key'])
            if previous is not None and previous!=e:raise ValueError('Historical evidence changed between batches; rerun.')
            evidence[e['contest_key']]=e
        for e in report['excluded']:excluded[e['name'],e['reason']]=e
    output=dict(first,through=windows[-1][1],followup_from=windows[0][0],evaluation_windows=windows,contests=sorted(contests.values(),key=lambda c:(c['date'],c['contest_key'])),evidence=sorted(evidence.values(),key=lambda e:(e['date'],e['contest_key'])),excluded=list(excluded.values()))
    profiles=[p for c in output['contests'] for p in c['winners']]
    if sum(p['entries'] for p in profiles)>MAX_SERIES_ENTRIES or len(profiles)>MAX_SERIES_PROFILES:raise ReviewLimitError('Series limit reached; narrow the evaluation range. No partial series published.')
    return output


def _inventory(db_path):
    with closing(_connect(db_path,True)) as conn:
        conn.execute('BEGIN')
        tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'opponent_contests','analysis_sources','analysis_salary_pairs'}<=tables:raise ValueError('Index mapped results in Data and Learning first.')
        data=[]
        for table,order in (('opponent_contests','contest_key'),('analysis_sources','hash'),('analysis_salary_pairs','result_hash')):
            data.append(conn.execute('SELECT * FROM '+table+' ORDER BY '+order).fetchall())
        return hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()


def review_series(db_path,through,cancelled=lambda:False,progress=lambda text:None,*,contest_format='showdown',since='',followup_from='',evaluation_windows=None):
    ai._check(cancelled)
    through=date.fromisoformat(through).isoformat();since=date.fromisoformat(since).isoformat() if since else '0001-01-01';start=date.fromisoformat(followup_from).isoformat()
    if not since<start<=through or contest_format not in ('','showdown','classic'):raise ValueError('Choose valid history and evaluation dates and format.')
    initial=_inventory(db_path)
    if evaluation_windows is None:
        with closing(_connect(db_path,True)) as conn:
            dates=[r[0] for r in conn.execute('SELECT DISTINCT end_date FROM opponent_contests WHERE version=? AND end_date BETWEEN ? AND ? AND (?=\'\' OR format=?) ORDER BY end_date',(VERSION,start,through,contest_format,contest_format))]
        windows=[(dates[i],dates[min(i+1,len(dates)-1)]) for i in range(0,len(dates),2)] or [(start,through)]
    else:windows=[(date.fromisoformat(a).isoformat(),date.fromisoformat(b).isoformat()) for a,b in evaluation_windows]
    if not windows or len(windows)>MAX_BATCHES:raise ReviewLimitError('Use up to 8 bounded windows (at most two evaluation dates per automatic batch).')
    for i,(a,b) in enumerate(windows):
        if not start<=a<=b<=through or (i and a<=windows[i-1][1]):raise ValueError('Evaluation windows must be ordered, nonoverlapping and inside the selected range.')
    reports=[];entries=profiles=0
    for i,(a,b) in enumerate(windows,1):
        ai._check(cancelled);progress(f'Historical batch {i}/{len(windows)}: {a} through {b}. Each batch retains the existing review limits.')
        r=review_winners(db_path,b,cancelled,lambda t:progress(f'Batch {i}/{len(windows)} — '+t),contest_format=contest_format,since=since,review_mode='prior_qualified',followup_from=a)
        reports.append(r);entries+=sum(p['entries'] for c in r['contests'] for p in c['winners']);profiles+=sum(len(c['winners']) for c in r['contests'])
        if entries>MAX_SERIES_ENTRIES or profiles>MAX_SERIES_PROFILES:raise ReviewLimitError('Series limit reached; narrow the evaluation range. No partial series published.')
        if _inventory(db_path)!=initial:raise ValueError('History index or salary mappings changed during the series; wait for indexing to finish and rerun.')
    result=combine_followups(reports)
    with closing(_connect(db_path,True)) as conn:sources={s['hash']:s for s in ai._sources(conn)}
    required={h for e in result['evidence'] for h in (e['source'],e['salary_source'])}
    for i,digest in enumerate(sorted(required),1):
        ai._check(cancelled);progress(f'Final series source verification {i}/{len(required)}.')
        if digest not in sources:raise ValueError('Series source is no longer indexed; rerun.')
        ai._verify(sources[digest],cancelled)
    if _inventory(db_path)!=initial:raise ValueError('Historical evidence changed before publication; rerun the complete series.')
    ai._check(cancelled)
    return result
