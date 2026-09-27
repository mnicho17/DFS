"""Read-only RL-06 source adapters. RL-05A remains the historical authority."""
from collections import Counter, defaultdict
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3

import review_build_evidence as be
from portfolio_risk import RiskCapture, check, finite, recorded_player, EXPORT_UNAVAILABLE


def fingerprint(selection, targets=(), alternatives=()):
    return be.digest([selection,list(targets),sorted(set(alternatives))])


@contextmanager
def read_database(db_path):
    path = Path(db_path).absolute()
    if not path.is_file() or path.is_symlink():
        raise ValueError('History database unavailable; use Historical Coverage to inspect sources')
    conn = sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        yield conn
    finally:
        conn.close()


def columns(conn, table):
    return {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}


def select_columns(conn, table, allowed):
    return [c for c in allowed if c in columns(conn,table)]


def source_catalog(db_path):
    """Cheap cached suggestions only; no source scans or qualification authority."""
    from historical_coverage import load_saved
    result = dict(contests=[],exports=[],issues=[])
    if not Path(db_path).is_file():
        return result
    saved = load_saved(db_path)
    for contest in saved['contests'][:be.MAX_DIRECTORY_ENTRIES]:
        d = contest.data
        result['contests'].append(dict(identity_id=d['identity_id'], identity=d['identity'],
            state=d['state'], archives=d.get('build_evidence',[])))
    if len(saved['contests'])>be.MAX_DIRECTORY_ENTRIES:
        result['issues'].append('contest_list_limit')
    with read_database(db_path) as conn:
        fields = select_columns(conn,'exports',('export_id','created_at','sport','contest_type','lineup_count'))
        if 'export_id' in fields:
            rows = list(conn.execute('SELECT '+','.join(fields)+' FROM exports ORDER BY export_id LIMIT ?',
                                     (be.MAX_DIRECTORY_ENTRIES+1,)))
            result['exports'] = [dict(r) for r in rows[:be.MAX_DIRECTORY_ENTRIES]]
            if len(rows)>be.MAX_DIRECTORY_ENTRIES:
                result['issues'].append('export_list_limit')
    return result


class CaptureReader(be.Reader):
    """Collect validated data during the authority's one inventory pass."""
    def __init__(self, cancelled):
        super().__init__(cancelled)
        self.snapshots, self.archives, self.directories = {}, defaultdict(list), {}

    @staticmethod
    def stamps(paths):
        return [(str(p),p.stat().st_size,p.stat().st_mtime_ns) for p in paths]

    def paths(self, folder, suffix):
        for parent in (folder,folder.parent):
            if parent.is_symlink() or getattr(parent,'is_junction',lambda:False)():
                raise ValueError('Linked history directories are unsupported')
        paths = super().paths(folder,suffix)
        self.directories[(folder,suffix)] = self.stamps(paths)
        return paths

    def data(self, path):
        value = super().data(path)
        snap = be.snapshot(value)
        self.snapshots[be.digest(snap['raw'])] = snap
        return value

    def archive(self, path):
        detail = super().archive_details(path)
        snap,meta,sigs,_,rows = detail
        self.snapshots[be.digest(snap['raw'])] = snap
        self.archives[be.archive_identity(meta,sigs)].append(detail)
        return detail[:4]

    def revalidate(self):
        # Source files are outside the SQL transaction. Reread their receipts
        # within the SAME shared byte budget; reject a mixed capture.
        for (folder,suffix),stamps in self.directories.items():
            if self.stamps(super().paths(folder,suffix)) != stamps:
                raise ValueError('Evidence inventory changed during capture; retry')
        for name,expected in self.receipts.items():
            self.check()
            path = Path(name)
            if path.is_symlink() or not path.is_file():
                raise ValueError('Evidence source changed during capture')
            self.consume(path.stat().st_size)
            with path.open('rb') as handle:
                raw = handle.read(be.MAX_FILE_BYTES+1)
            if len(raw)>be.MAX_FILE_BYTES or hashlib.sha256(raw).hexdigest()!=expected:
                raise ValueError('Evidence bytes changed during capture; retry')


def _archive_rosters(rows, players, kind, cancelled):
    """Exact scoped IDs establish identity; every present redundant field agrees."""
    by_flex = {str(p.get('FlexID')):p for p in players if p.get('FlexID') is not None}
    by_cpt = {str(p.get('CptID')):p for p in players if p.get('CptID') is not None}
    if len(by_flex)!=len(players) or kind=='showdown' and len(by_cpt)!=len(players):
        raise ValueError('Frozen player IDs are missing or ambiguous')
    result = []
    for row in rows:
        check(cancelled)
        slots = []
        for slot in row['slots']:
            raw,role = slot['player'],slot['slot']
            role = 'CPT' if role in ('CAPTAIN','CPT') else role.replace('D/ST','DST')
            flex = by_flex.get(str(raw.get('FlexID'))) if raw.get('FlexID') is not None else None
            captain = by_cpt.get(str(raw.get('CptID'))) if raw.get('CptID') is not None else None
            player = flex or captain
            if not player:
                raise ValueError('Archive slot lacks an unambiguous frozen player ID')
            for field in ('FlexID','CptID','Name','Team','Position','GameInfo','GameKey'):
                if field in raw and raw[field] not in (None,'') and str(raw[field])!=str(player.get(field)):
                    raise ValueError('Archive player identity/context contradicts frozen input: '+field)
            for field in ('FlexSalary','CptSalary'):
                if field in raw and raw[field] is not None and (finite(raw[field]) is None or finite(raw[field])!=finite(player.get(field))):
                    raise ValueError('Archive salary contradicts frozen input')
            position = str(player.get('Position') or '').replace('D/ST','DST')
            if kind=='classic' and not (position in ('RB','WR','TE') if role=='FLEX' else position==role):
                raise ValueError('Archive slot conflicts with frozen position')
            slots.append(dict(key=str(player['FlexID']),role=role))
        if len({s['key'] for s in slots})!=len(slots):
            raise ValueError('Duplicate athlete in archive roster')
        result.append(sorted(slots,key=lambda s:s['role']!='CPT') if kind=='showdown' else slots)
    return result


def capture_archive(conn, root, selection, cancelled, progress):
    from historical_identity import derive_contests
    from analysis_imports import _sources, _verify
    reader = CaptureReader(cancelled)
    contests = derive_contests(conn,root,cancelled=cancelled,progress=progress,evidence_reader=reader)
    data = next((c.data for c in contests if c.data['identity_id']==selection.get('contest_id')),None)
    if not data or not (data.get('salary_evidence') or {}).get('qualified') or not data.get('snapshot_evidence'):
        issues = ', '.join((data or {}).get('source_issues',{}))
        raise ValueError('Qualified salary and pregame snapshot required; use Historical Coverage'+
                         (' (source limitations: '+issues+')' if issues else ''))
    archive = next((a for a in data['build_evidence'] if a['archive_id']==selection.get('archive_id')),None)
    if not archive:
        raise ValueError('This archive is not freshly qualified build evidence; use Historical Coverage')
    matches = reader.archives.get(archive['archive_id'],[])
    if not matches or len({be.digest([d[0]['raw'],d[1],d[4]]) for d in matches})!=1:
        raise ValueError('Archive identity has missing or conflicting occurrence payloads')
    _,meta,_,_,rows = matches[0]
    evidence = data['snapshot_evidence']
    resolution = data.get('snapshot_resolution') or {}
    chosen = [s for digest,s in reader.snapshots.items() if s['raw']['input_id']==evidence['input_id']
              and s['raw']['created_at']==evidence['recorded_at']
              and (not resolution or digest==resolution['snapshot_digest'])]
    if len(chosen)!=1:
        raise ValueError('Exact qualified snapshot revision unavailable')
    snap = chosen[0]
    archive_time = be.timestamp(meta.get('created_at'))
    if not archive_time or not snap['created']<=archive_time<snap['earliest']:
        raise ValueError('Archive timing does not follow the exact qualified pregame snapshot')
    progress('Validating archived occurrence identities')
    rosters = _archive_rosters(rows,snap['players'],snap['kind'],cancelled)
    pool = {str(p['FlexID']):recorded_player(str(p['FlexID']),p,qualified=True,
            captured_at=snap['raw']['created_at'],kickoff=snap['earliest'].isoformat(),game=p.get('GameInfo'))
            for p in snap['players']}
    progress('Revalidating frozen source receipts')
    selected_hashes = {data['salary_evidence']['revision_hash'],data['results_evidence']['source_hash']}
    for source in _sources(conn):
        if source['hash'] in selected_hashes:
            _verify(source,cancelled)
    reader.revalidate()
    check(cancelled)
    return dict(version=1,source_kind='archive',label='Generated archive - submission not established',
        format=snap['kind'],sport='NFL',slate_date=snap['day'],source_count=len(rows),rosters=rosters,pool=pool,
        forecast_qualified=True,rejected={},limits=dict(data.get('source_issues',{})),
        provenance=dict(contest_id=data['identity_id'],archive_id=archive['archive_id'],
            archive_time=meta['created_at'],input_id=snap['raw']['input_id'],
            snapshot_digest=be.digest(snap['raw']),snapshot_time=snap['raw']['created_at'],
            earliest_kickoff=snap['earliest'].isoformat(),method='RL-05A fresh qualification / RL-06 v1',
            byte_receipts=dict(reader.receipts),read_bytes=reader.bytes,
            snapshot_basis='exact qualified revision, including saved resolution; embedded archive time cannot replace it'))


def capture_export(conn, selection, cancelled, progress):
    fields = select_columns(conn,'exports',('export_id','created_at','sport','contest_type','lineup_count'))
    if 'export_id' not in fields:
        raise ValueError('Saved export catalog unavailable')
    raw = conn.execute('SELECT '+','.join(fields)+' FROM exports WHERE export_id=?',(selection.get('export_id'),)).fetchone()
    if not raw:
        raise ValueError('Selected saved export is unavailable')
    export = dict(raw)
    kind = str(export.get('contest_type') or '').lower()
    if kind not in ('classic','showdown') or str(export.get('sport') or '').upper()!='NFL':
        raise ValueError('Recorded export format/sport is unknown or unsupported')
    if not {'lineup_id','export_id'}<=columns(conn,'lineups') or not {'lineup_id','player_key','slot','name'}<=columns(conn,'lineup_players'):
        raise ValueError('Saved export identity columns unavailable; no data was backfilled')
    count = conn.execute('SELECT COUNT(*) FROM lineups WHERE export_id=?',(export['export_id'],)).fetchone()[0]
    ids = [r[0] for r in conn.execute('SELECT lineup_id FROM lineups WHERE export_id=? ORDER BY lineup_id LIMIT ?',
                                     (export['export_id'],be.MAX_LINEUPS))]
    allowed = select_columns(conn,'lineup_players',('player_key','player_id','slot','name','team','opponent','position','projection','injury_status'))
    observed, candidate_rows, rejected = defaultdict(list), [], Counter()
    if count>len(ids):
        rejected['occurrence_read_limit'] = count-len(ids)
    for index,ident in enumerate(ids):
        check(cancelled)
        if index%100==0:
            progress(f'Reading saved export occurrences {index}/{len(ids)}')
        players = [dict(r) for r in conn.execute('SELECT '+','.join(allowed)+' FROM lineup_players WHERE lineup_id=? LIMIT 11',(ident,))]
        expected = 6 if kind=='showdown' else 9
        if len(players)!=expected or any(not p['player_key'] or not p['name'] for p in players):
            rejected['malformed_or_missing_identity'] += 1
            continue
        if len({p['player_key'] for p in players})!=expected:
            rejected['duplicate_athlete'] += 1
            continue
        roles = [str(p['slot'] or '').upper() for p in players]
        valid_roles = (set(roles)=={'CPT',*[f'FLEX{i}' for i in range(1,6)]} if kind=='showdown'
                       else set(roles)=={f'SLOT{i}' for i in range(1,10)})
        if not valid_roles:
            rejected['ambiguous_slot_identity'] += 1
            continue
        slots = []
        for p,role in zip(players,roles):
            p['slot'] = role
            key = str(p['player_key'])
            observed[key].append(p)
            slots.append(dict(key=key,role='CPT' if p['slot']=='CPT' else 'FLEX' if kind=='showdown' else str(p['slot'])))
        candidate_rows.append(sorted(slots,key=lambda s:s['role']!='CPT') if kind=='showdown' else slots)
    pool, ambiguous = {},set()
    for key,records in observed.items():
        for field in ('name','team','position'):
            if len({str(p.get(field)) for p in records if p.get(field) not in (None,'')})>1:
                ambiguous.add(key)
        for captain in (True,False):
            if len({str(p.get('player_id')) for p in records if (p['slot']=='CPT')==captain and p.get('player_id')})>1:
                ambiguous.add(key)
        first = records[0]
        # Incomplete recorded context stays incomplete; no cross-row enrichment.
        context = {field:first.get(field) if all(p.get(field)==first.get(field) for p in records) else None
                   for field in ('name','team','position','projection','injury_status')}
        pool[key] = recorded_player(key,dict(Name=context['name'],Team=context['team'],Position=context['position'],
            RecordedProjection=finite(context['projection']),RecordedStatus=context['injury_status']),qualified=False)
        # Preserve differing legacy Captain/regular values as unqualified metadata,
        # not a merged/defaulted forecast and never as scenario input.
        pool[key]['legacy_metadata']['ByRole'] = {
            role:sorted({json.dumps(dict(projection=finite(p.get('projection')),status=p.get('injury_status')),
                sort_keys=True) for p in records if ('Captain' if p['slot']=='CPT' else 'Regular')==role})
            for role in ('Captain','Regular')}
        pool[key]['legacy_metadata']['ByRole'] = {role:[json.loads(value) for value in values]
            for role,values in pool[key]['legacy_metadata']['ByRole'].items() if values}
    # Same recorded role ID assigned to different base keys cannot certify athletes.
    role_ids = defaultdict(set)
    for key,records in observed.items():
        for p in records:
            if p.get('player_id'):
                role_ids[str(p['player_id'])].add(key)
    for keys in role_ids.values():
        if len(keys)>1:
            ambiguous.update(keys)
    rosters = []
    for row in candidate_rows:
        if any(s['key'] in ambiguous for s in row):
            rejected['conflicting_recorded_identity'] += 1
        else:
            rosters.append(row)
    for key in ambiguous:
        pool.pop(key,None)
    check(cancelled)
    return dict(version=1,source_kind='saved_export',label='Saved export - submission and original pregame input not established',
        format=kind,sport='NFL',slate_date=None,source_count=count,rosters=rosters,pool=pool,
        forecast_qualified=False,rejected=dict(rejected),limits={'occurrences':be.MAX_LINEUPS} if count>be.MAX_LINEUPS else {},
        provenance=dict(export_id=export['export_id'],recorded_export_time=export.get('created_at'),
            method='Original saved export rows / descriptive only / RL-06 v1',
            slate_context='Unknown or mixed; export time does not establish slate/game identity',
            upload_slots='Classic SLOTn records do not establish exact DraftKings upload slots',
            forecast_gate=EXPORT_UNAVAILABLE))


def capture(db_path, selection, *, history_root=None, cancelled=lambda:False, progress=lambda text:None):
    check(cancelled)
    path = Path(db_path).absolute()
    root = Path(history_root).absolute() if history_root else path.parent
    if root!=path.parent:
        raise ValueError('History root must match the pinned database')
    with read_database(path) as conn:
        if selection.get('kind')=='archive':
            data = capture_archive(conn,root,selection,cancelled,progress)
        elif selection.get('kind')=='saved_export':
            data = capture_export(conn,selection,cancelled,progress)
        else:
            raise ValueError('Choose one explicit portfolio source')
    check(cancelled)
    return RiskCapture.freeze(data)
