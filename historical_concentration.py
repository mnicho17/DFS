"""Small revision-bound report receipts; never used for model qualification."""
from collections import defaultdict
import json

VERSION=1
CACHE_KEY='concentration_v1'


def _count(value):
    return type(value) is int and value>=0


def _audit(a):
    required=('entry_rows','accepted_entries','readable_rosters','identical_duplicate_rows','conflicting_entry_ids_excluded')
    if not isinstance(a,dict) or not all(_count(a.get(k)) for k in required):
        raise ValueError('Invalid cached entry-validation receipt.')
    if not a['readable_rosters']<=a['accepted_entries']<=a['entry_rows']:
        raise ValueError('Inconsistent cached entry-validation totals.')


def validate(receipt,audit):
    _audit(audit)
    if not isinstance(receipt,dict) or type(receipt.get('version')) is not int or receipt['version']!=VERSION:
        raise ValueError('Concentration receipt unavailable; normal field-history indexing refreshes it once from cached entrant statistics.')
    if receipt.get('reason'):raise ValueError(receipt['reason'])
    keys=('users','entries','readable','metadata','lineup_groups','repeated','shared')
    if not all(_count(receipt.get(k)) for k in keys):raise ValueError('Invalid cached concentration totals.')
    n,r,u=receipt['entries'],receipt['readable'],receipt['lineup_groups']
    if (n!=audit['accepted_entries'] or r!=audit['readable_rosters'] or
            not receipt['users']<=n or not receipt['metadata']<=r or not u<=r or
            receipt['repeated']!=r-u or receipt['shared']>r or (r>0 and u==0)):
        raise ValueError('Indexed concentration totals do not match the validated entry receipt.')
    return dict(receipt,reason=None,audit={k:v for k,v in audit.items() if k!=CACHE_KEY})


def build_receipt(audit,members,*,limit=200000,cancelled=lambda:False):
    """Used during normal indexing, not when opening the learning report."""
    from analysis_imports import _check
    _check(cancelled)
    g=dict(version=VERSION,users=0,entries=0,readable=0,metadata=0,lineup_groups=0,repeated=0,shared=0)
    try:
        _audit(audit)
        for index,stats in enumerate(members):
            if index%250==0:_check(cancelled)
            if index>=limit:raise ValueError('Entrant-statistics read limit reached; no partial concentration totals shown.')
            if not isinstance(stats,dict):raise ValueError('Invalid cached entrant statistics.')
            n,r=stats.get('entries'),stats.get('readable_rosters')
            if not all(_count(v) for v in (n,r)) or not 0<n or r>n:raise ValueError('Invalid cached entrant counts.')
            u,p,shared=(stats.get(k) for k in ('unique_lineups','repeated_entries','entries_shared_with_other_users'))
            if r:
                if not all(_count(v) for v in (u,p,shared)) or not 0<u<=r or p!=r-u or shared>r:
                    raise ValueError('Invalid cached lineup-concentration counts.')
            else:
                if any(v is not None for v in (u,p,shared)):raise ValueError('Unreadable entries cannot supply lineup concentration.')
                u=p=shared=0
            construction=stats.get('constructions')
            if not isinstance(construction,dict):raise ValueError('Cached salary metadata coverage is unavailable.')
            known,unknown=(construction.get(k) for k in ('known_entries','unknown_entries'))
            if not all(_count(v) for v in (known,unknown)) or known>r or known+unknown!=n:
                raise ValueError('Inconsistent cached salary metadata coverage.')
            for k,v in dict(users=1,entries=n,readable=r,metadata=known,lineup_groups=u,repeated=p,shared=shared).items():g[k]+=v
        _check(cancelled);validate(g,audit)
        return g
    except (ValueError,TypeError) as error:return dict(version=VERSION,reason=str(error))


def cached_receipt(conn,key,audit,*,cancelled=lambda:False,limit=200000):
    """One-time backfill from a single contest's already saved statistics."""
    def members():
        for n,r,encoded in conn.execute('SELECT entries,readable,stats_json FROM opponent_user_contest_stats WHERE contest_key=? ORDER BY user_key LIMIT ?',(key,limit+1)):
            if not isinstance(encoded,str) or len(encoded)>65536:raise ValueError('Invalid cached entrant statistics.')
            s=json.loads(encoded)
            if not isinstance(s,dict) or type(s.get('entries')) is not int or s['entries']!=n or type(s.get('readable_rosters')) is not int or s['readable_rosters']!=r:
                raise ValueError('Inconsistent cached entrant counts.')
            yield s
    return build_receipt(audit,members(),limit=limit,cancelled=cancelled)


def observations(conn,*,limit=4096,cancelled=lambda:False):
    from analysis_imports import _check
    from opponent_history import VERSION as INDEX_VERSION
    _check(cancelled)
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='opponent_contests'").fetchone():
        return dict(complete=True,receipts={},reason='No indexed entrant statistics; existing history indexing supplies them.')
    receipts=defaultdict(list)
    for index,(source,salary,kind,version,encoded) in enumerate(conn.execute('SELECT result_hash,salary_hash,format,version,audit_json FROM opponent_contests ORDER BY contest_key LIMIT ?',(limit+1,))):
        if index%250==0:_check(cancelled)
        if index>=limit:return dict(complete=False,receipts={},reason='Indexed contest-receipt read limit reached; no partial concentration totals shown.')
        try:
            if version!=INDEX_VERSION:raise ValueError('Older indexed statistics; normal history indexing must refresh them.')
            if not isinstance(encoded,str) or len(encoded)>65536:raise ValueError('Invalid cached entry-validation receipt.')
            a=json.loads(encoded);_audit(a)
            g=validate(a.get(CACHE_KEY),a)
        except (ValueError,TypeError) as error:g=dict(reason=str(error))
        receipts[(source,salary)].append(dict(g,format=kind))
    _check(cancelled)
    return dict(complete=True,reason=None,receipts={k:(v[0] if len(v)==1 else dict(reason='Conflicting indexed receipts for this source and salary revision.')) for k,v in receipts.items()})


def attach(contest, data, observation):
    """Caller admits only currently verified salary-bound historical summaries."""
    source=data['results_evidence']['source_hash'];salary=data['salary_evidence']['revision_hash']
    receipt=observation.get('receipts',{}).get((source,salary)) if observation.get('complete') else None
    reason=observation.get('reason') or 'No current indexed receipt for this source and salary revision.'
    if receipt and not receipt.get('reason') and receipt.get('format')!=data['salary_evidence']['format']:
        receipt=dict(reason='Indexed roster format differs from the verified salary association.')
    contest['concentration']=receipt or dict(reason=reason)


def report_lines(scope):
    from historical_contest_comparison import slate_blocks
    contests=scope.get('contests',[])
    lines=['', 'Evidence coverage and lineup concentration',
        '- Source-row counts and validated unique EntryIds have separate denominators. Cached entrant statistics are used only for the same current result/salary revision; unavailable values are not zero.',
        '| Source | Mapped / source rows | Accepted unique EntryIds | Readable / accepted | Salary matched / accepted | Entrant/lineup groups | Repeats within entrants | Entries shared across entrants | Validation / availability |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---|']
    for c in sorted(contests,key=lambda c:(c['name'],c['source'])):
        name=c['name'].replace('|','/').replace('\n',' ').replace('\r',' ')
        counts=c.get('cohorts',{}).get('field',{}).get('counts',{})
        rows=f"{counts.get('supported_rows',0):,}/{counts.get('entry_rows',0):,}" if counts else 'unavailable'
        r=c.get('concentration',{})
        if not r or r.get('reason'):
            reason=r.get('reason') or 'No indexed entrant statistics available.'
            lines.append(f"| {name} | {rows} | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | {reason} |")
            continue
        a=r['audit'];n=r['entries'];readable=r['readable']
        validation=f"{a['identical_duplicate_rows']:,} identical duplicate rows removed; {a['conflicting_entry_ids_excluded']:,} conflicting EntryIds excluded"
        if r['metadata']<n:validation+='; incomplete salary metadata coverage'
        repeat=f"{r['repeated']:,}/{readable:,}" if readable else 'unavailable'
        shared=f"{r['shared']:,}/{readable:,}" if readable else 'unavailable'
        lines.append(f"| {name} | {rows} | {n:,} | {readable:,}/{n:,} | {r['metadata']:,}/{n:,} | {r['lineup_groups']:,} | {repeat} | {shared} | {validation} |")
    blocks,excluded=slate_blocks(contests)
    lines.append(f'- Recorded game/date overlap blocks: {len(blocks)}; sources missing game/date identity: {excluded}. These may differ from the matched blocks in each construction comparison.')
    if len(blocks)<2:
        lines.append('- Fewer than two recorded game/date blocks: there is insufficient slate coverage for a cross-slate conclusion.')
    lines += ['- Entrant/lineup groups count a lineup separately for each entrant; they are not globally unique lineups. Repeats are additional accepted entries within an entrant using the same readable lineup. Shared entries include each entrant’s copies of lineups also used by another entrant. These measures overlap and must not be added.',
        '- Legitimate repeated entries remain valid. Repeated lineups share outcomes; entry counts are not independent games. Formats and presets can share games. Salary metadata coverage can be lower than readable-roster coverage.',
        '- These are whole-source concentration statistics, not top-finisher or Captain-specific concentration. The construction tables retain source-row semantics and are not deduplicated or reweighted by this panel. A frequency gap does not establish finish probability, payouts, a causal edge or a reason to change defaults.']
    return lines
