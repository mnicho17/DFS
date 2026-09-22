"""Validated last-successful weekly downloads; cache dates never imply a fresh fetch."""
import datetime as dt
import json
from pathlib import Path
from build_snapshots import fingerprint


class UsageRows(list):
    def __init__(self,rows=(),*,state='unavailable',fetched_at='',error=''):
        super().__init__(rows)
        self.state=state;self.fetched_at=fetched_at;self.error=error


def cache_path(season):
    from build_diagnostics import build_history_path
    return Path(build_history_path()).parent/'usage-cache'/f'{int(season)}.json'


def fetch_cached(season,url,download):
    path=cache_path(season);error=''
    for attempt in range(2):
        try:
            rows=download()
            if not rows:return UsageRows(state='empty')
            stamp=dt.datetime.now(dt.timezone.utc).isoformat()
            payload=dict(season=int(season),url=url,fetched_at=stamp,rows=rows)
            try:
                from repeatability import atomic_json
                atomic_json(path,dict(payload=payload,digest=fingerprint(payload)))
            except OSError:
                pass  # A usable download must not fail because the cache disk is full.
            return UsageRows(rows,state='fresh',fetched_at=stamp)
        except Exception as exc:
            error=str(exc)
            response=getattr(exc,'response',None)
            status=getattr(response,'status_code',None)
            transient=(isinstance(status,int) and (status>=500 or status==429)) or type(exc).__name__ in ('Timeout','ReadTimeout','ConnectTimeout','ConnectionError')
            if attempt or not transient:break
    try:
        if path.stat().st_size>128*1024*1024:raise ValueError('Cache too large')
        record=json.loads(path.read_text(encoding='utf-8'));p=record['payload']
        if record['digest']!=fingerprint(p) or p['season']!=int(season) or p['url']!=url:raise ValueError('Cache identity mismatch')
        stamp=dt.datetime.fromisoformat(p['fetched_at'])
        if stamp.tzinfo is None or stamp>dt.datetime.now(dt.timezone.utc):raise ValueError('Invalid cache date')
        if not isinstance(p['rows'],list) or not p['rows']:raise ValueError('Empty cache')
        for row in p['rows']:
            if int(row['season'])!=int(season) or not 1<=int(row['week'])<=22:raise ValueError('Invalid cached season/week')
        return UsageRows(p['rows'],state='cached',fetched_at=p['fetched_at'],error=error)
    except (OSError,ValueError,TypeError,KeyError):
        return UsageRows(error=error)
