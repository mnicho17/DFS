"""Read-only qualification for an explicit pregame field-model experiment.

Existing reconciliation, saved choices, and archive certification are untouched.
The same snapshot integrity/timing and exact historical salary rules apply.
"""
from collections import Counter
from contextlib import closing
import copy
import hashlib
import itertools
import json
import math
from pathlib import Path
import re
import analysis_imports as ai
from historical_identity import _snapshot_compatible, _digest
from review_build_evidence import Reader, snapshot, digest, MAX_DIRECTORY_ENTRIES, MAX_FILES, MAX_TOTAL_BYTES
from results_snapshot_learning import match_snapshot, game_start
from learning_db import _normalize_roster_token
from opponent_history import _connect

# Explicit multi-game catalog, separate from the existing reconciliation reader.
# Per-file limits remain unchanged; each dated slate retains its 64 MB budget.
MAX_CATALOG_BYTES=256_000_000


def _paths(folder):
    if not folder.is_dir() or folder.is_symlink() or getattr(folder,'is_junction',lambda:False)():
        raise ValueError('Choose an accessible original history folder with snapshots.')
    paths = list(itertools.islice(folder.iterdir(), MAX_DIRECTORY_ENTRIES+1))
    if len(paths)>MAX_DIRECTORY_ENTRIES:
        raise ValueError('Snapshot directory enumeration limit exceeded; no partial selection.')
    return sorted(p for p in paths if p.suffix=='.json')


def _resolutions(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='historical_evidence_resolutions'").fetchone():return {}
    return {ident:dict(snapshot_digest=d,input_id=i,salary_hash=s,method=m,evidence_version=v,confirmed_at=t)
            for ident,d,i,s,m,v,t in conn.execute('SELECT * FROM historical_evidence_resolutions')}


class PregameEvidence:
    def __init__(self,db_path,root=None,cancelled=lambda:False,progress=lambda text:None):
        self.db_path=str(db_path);self.root=Path(root or Path(db_path).resolve().parent).resolve()
        self.cancelled=cancelled;self.progress=progress
        self.reader=Reader(lambda:ai._check(cancelled))
        self.paths=_paths(self.root/'snapshots');self.snapshots=[];self.issues=[];groups=Counter();group_bytes=Counter();total_bytes=0
        for i,path in enumerate(self.paths):
            ai._check(cancelled);progress(f'Reading frozen snapshot {i+1}/{len(self.paths)}')
            try:
                reader=Reader(lambda:ai._check(cancelled))
                raw=reader.data(path);total_bytes+=reader.bytes
                if total_bytes>MAX_CATALOG_BYTES:raise OverflowError('multi-game catalog read budget')
                self.reader.receipts.update(reader.receipts)
                item=snapshot(raw)
                group=(item['kind'],item['day']);groups[group]+=1;group_bytes[group]+=reader.bytes
                if group_bytes[group]>MAX_TOTAL_BYTES:raise OverflowError('dated slate read budget')
                item['path']=str(path);item['sha256']=self.reader.receipts[str(path)]
                if item['kind']=='showdown':self.snapshots.append(item)
            except OverflowError:
                raise ValueError('Snapshot file, slate or multi-game catalog byte budget exceeded; incomplete inventory cannot select a snapshot.')
            except ai.ImportCancelled:raise
            except Exception as exc:self.issues.append(dict(name=path.name,reason=str(exc)))
        if any(n>MAX_FILES for n in groups.values()):
            raise ValueError('Per-format/date snapshot count limit exceeded; no partial selection.')
        if self.issues:
            raise ValueError('Snapshot inventory contains unreadable or invalid files; latest-input inference withheld: '+', '.join(r['name'] for r in self.issues[:5]))
        self.snapshots=list({digest(s['raw']):s for s in reversed(self.snapshots)}.values())
        # Retain only Showdown bodies; Classic integrity is still checked and its
        # bytes count against this explicitly bounded multi-game catalog.
        registry=self.root/'contest-snapshots.json'
        self.registry_exists=registry.exists()
        if self.registry_exists:
            value=self.reader.data(registry)
            if not isinstance(value,dict) or any(not isinstance(v,list) or any(not isinstance(x,str) or not re.fullmatch('[0-9a-f]{64}',x) for x in v) for v in value.values()):
                raise ValueError('Saved contest/snapshot associations need review.')
        with closing(_connect(db_path,True)) as conn:self.resolutions=_resolutions(conn)

    def qualify(self,source,salary,salary_hash,conn):
        ai._check(self.cancelled)
        compatible=[s for s in self.snapshots if _snapshot_compatible(s,salary)]
        candidates=[]
        for item in compatible:
            selected,_=match_snapshot(self.root,salary['dates'][0],'showdown',
                {_normalize_roster_token(p['name']) for p in salary['players']},'',snapshots=[item['raw']],
                contest_ids=source['manifest'].get('contest_ids',[]),check=lambda:ai._check(self.cancelled))
            if selected is not None and item['pregame']:candidates.append(item)
        ident=_digest(['historical-contest',source['import_id'],'import'])
        identifiers={ident}
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='historical_contest_identities'").fetchone():
            identifiers.update(row[0] for row in conn.execute('SELECT identity_id FROM historical_contest_identities WHERE import_id=?',(source['import_id'],)))
        choices=[self.resolutions[k] for k in identifiers if k in self.resolutions]
        if len(choices)>1:raise ValueError('Multiple saved snapshot resolutions for this import require identity review.')
        resolution=choices[0] if choices else None
        if resolution:
            wanted=[s for s in candidates if digest(s['raw'])==resolution['snapshot_digest'] and s['raw']['input_id']==resolution['input_id']]
            if resolution['salary_hash']!=salary_hash or len(wanted)!=1:
                raise ValueError('Saved snapshot resolution is unavailable or conflicts; the saved choice is preserved.')
            chosen=wanted[0];method='saved exact snapshot resolution'
        else:
            selected,method=match_snapshot(self.root,salary['dates'][0],'showdown',
                {_normalize_roster_token(p['name']) for p in salary['players']},'',snapshots=[s['raw'] for s in candidates],
                contest_ids=source['manifest'].get('contest_ids',[]),check=lambda:ai._check(self.cancelled))
            if selected is None:raise ValueError(method)
            wanted=[s for s in candidates if s['raw']['input_id']==selected['input_id']]
            if len({digest(s['raw']) for s in compatible if s['raw']['input_id']==selected['input_id']})!=1:
                raise ValueError('Snapshot timestamp revisions conflict; an exact saved choice is required.')
            chosen=wanted[0]
        starts=[game_start(p['raw'].get('gameinfo')) for p in salary['players']]
        if not all(starts) or len(set(starts))!=1 or chosen['earliest']!=starts[0]:
            raise ValueError('Original salary kickoff and recorded snapshot kickoff do not agree.')
        raw=copy.deepcopy(chosen['raw'])
        recipe=raw['inputs']['recipe']
        if float(recipe.get('salary_cap',50000))!=50000:
            raise ValueError('This recorded-input experiment requires the historical $50,000 cap.')
        players=raw['inputs']['players']
        if any(p.get('Position')=='QB' and not isinstance(p.get('NFLQBEligible'),bool) for p in players):
            raise ValueError('Frozen quarterback eligibility is unknown; no current depth information is inferred.')
        from showdown_simulation import active_showdown_players
        pool=active_showdown_players([dict(p,LockCpt=False,LockFlex=False) for p in players])
        if len(pool)<6:raise ValueError('Fewer than six frozen eligible forecast athletes.')
        for p in pool:
            for key in ('FlexProjection','CptProjection','ProjCptOwnPct','ProjFlexOwnPct'):
                value=p.get(key)
                if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0 or (key.endswith('OwnPct') and value>100):
                    raise ValueError('Missing or invalid frozen projection/slot ownership; no fallback estimates are inferred.')
            if p.get('OwnershipUnits')!='percent_of_entries':
                raise ValueError('Frozen slot ownership units are unverified.')
        for key,total in (('ProjCptOwnPct',100),('ProjFlexOwnPct',500)):
            if abs(sum(p[key] for p in pool)-total)>max(2,len(pool)*.051):
                raise ValueError('Frozen active-pool ownership totals do not match roster slots; no renormalization is inferred.')
        receipt=dict(path=chosen['path'],sha256=chosen['sha256'],snapshot_digest=digest(chosen['raw']),input_id=raw['input_id'],
            recorded_at=raw['created_at'],earliest_game=chosen['earliest'].isoformat(),association=method,
            supplied_players=len(players),active_players=len(pool),saved_resolution=resolution,
            eligibility_basis='frozen snapshot flags and existing active-pool rules; personal Captain/FLEX locks cleared for opponents')
        return pool,receipt

    def revalidate(self):
        ai._check(self.cancelled)
        if _paths(self.root/'snapshots')!=self.paths or (self.root/'contest-snapshots.json').exists()!=self.registry_exists:
            raise ValueError('Snapshot inventory or saved associations changed during comparison; retry.')
        with closing(_connect(self.db_path,True)) as conn:
            if _resolutions(conn)!=self.resolutions:raise ValueError('Saved snapshot resolutions changed during comparison; retry.')
        for name,expected in self.reader.receipts.items():
            path=Path(name)
            if path.is_symlink() or not path.is_file():raise ValueError('Frozen evidence disappeared or became a link.')
            sha=hashlib.sha256()
            with path.open('rb') as handle:
                for chunk in iter(lambda:handle.read(1024*1024),b''):
                    ai._check(self.cancelled);sha.update(chunk)
            if sha.hexdigest()!=expected:raise ValueError('Frozen evidence changed during comparison; retry.')
