"""Read-only RL-07A capture. RL-05A alone selects salary/snapshot associations."""
from collections import Counter, defaultdict
import csv
import hashlib
import io
from pathlib import Path
import re

import analysis_imports as ai
import historical_identity as hi
import review_build_evidence as be
from portfolio_risk_evidence import read_database, CaptureReader
from hindsight_contract import (Frozen, check, decimal, score, integer, SCALE, MAX_ATHLETES,
    RULES, restrictions, validate_roster, points)
from hindsight_rules import classify


def fingerprint(selection,seconds=30,tie_limit=20):
    return be.digest([selection,seconds,tie_limit])


def source_catalog(db_path):
    """Cached prerequisites, not solver readiness. Never scans sources or solves."""
    from historical_coverage import load_saved
    saved=load_saved(db_path);result=[]
    for contest in saved['contests'][:be.MAX_DIRECTORY_ENTRIES]:
        d=contest.data;s=d.get('snapshot_evidence') or {}
        candidates=[c for c in d.get('snapshot_candidates',[]) if c['input_id']==s.get('input_id')
                    and c['recorded_at']==s.get('recorded_at')]
        resolution=d.get('snapshot_resolution') or {}
        if resolution:candidates=[c for c in candidates if c['snapshot_digest']==resolution['snapshot_digest']]
        result.append(dict(contest_id=d['identity_id'],identity=d['identity'],
            snapshot=candidates[0] if len(candidates)==1 else None,archives=d.get('build_evidence',[])))
    return result


def safe_path(path,root):
    path=Path(path).absolute()
    if not path.is_relative_to(root):raise ValueError('source_outside_pinned_history_root')
    for item in (path,*path.parents):
        if item.is_symlink() or getattr(item,'is_junction',lambda:False)():
            raise ValueError('linked_history_source')
        if item==root:break
    return path


def source_bytes(source,root,reader):
    path=safe_path(source['snapshot'],root)
    if path.stat().st_size>be.MAX_FILE_BYTES:raise OverflowError('CSV file limit')
    chunks=[];digest=hashlib.sha256();size=0
    with path.open('rb') as handle:
        while block:=handle.read(1024*1024):
            reader.consume(len(block));size+=len(block)
            if size>be.MAX_FILE_BYTES:raise OverflowError('CSV file limit')
            digest.update(block);chunks.append(block)
    if digest.hexdigest()!=source['hash']:raise ValueError('source_revision_changed')
    reader.receipts[str(path)]=source['hash']
    return b''.join(chunks)


def _universe(manifest):
    kind=manifest['format'];rows=manifest['players'];by_name=defaultdict(list);ids=set();coarse={}
    if not rows or len(rows)>2*MAX_ATHLETES or manifest['sport']!='NFL' or kind not in ('classic','showdown') or len(manifest['dates'])!=1:
        raise ValueError('unsupported_salary_universe')
    for r in rows:
        name=ai._name(r['name']);key=be.name_key(r['name'])
        if not name or not key or key in coarse and coarse[key]!=name:raise ValueError('whole_pool_name_collision')
        coarse[key]=name
        if not r['id'] or r['id'] in ids:raise ValueError('salary_role_id_collision')
        ids.add(r['id']);by_name[name].append(r)
    pool=[]
    for name,group in sorted(by_name.items()):
        if kind=='showdown':
            if len(group)!=2 or {r['role'] for r in group}!={'CPT','FLEX'}:raise ValueError('missing_or_ambiguous_role_salary')
            base=next(r for r in group if r['role']=='FLEX');captain=next(r for r in group if r['role']=='CPT')
        else:
            if len(group)!=1:raise ValueError('whole_pool_name_collision')
            base=group[0];captain=None
        raw=base['raw'];game=re.search(r'\b[A-Z]{2,3}@[A-Z]{2,3}\b',raw['gameinfo'].upper())
        if not game or base['team'] not in game[0].split('@'):raise ValueError('salary_game_membership')
        p=dict(key=base['id'],name=base['name'],position=base['position'],team=base['team'],
               game=game[0],roles={},original_rows=group)
        for row in group:
            if any(row[field]!=base[field] for field in ('name','position','team','opponent')) or row['raw']['gameinfo']!=raw['gameinfo']:
                raise ValueError('captain_flex_identity_context_conflict')
            cost=integer(row['raw']['salary'].replace(',',''))
            if cost!=row['salary']:raise ValueError('lossy_salary_value')
            embedded=re.search(r'\(([^()]+)\)\s*$',row['raw'].get('name+id',row['raw'].get('nameid','')))
            if embedded and embedded[1]!=row['id']:raise ValueError('salary_explicit_id_conflict')
            roles=[be.normalize_role(role) for role in row['role'].split('/')]
            if not roles or None in roles or len(set(roles))!=len(roles):raise ValueError('unsupported_roster_eligibility')
            for role in roles:
                if kind=='classic' and not (p['position'] in ('RB','WR','TE') if role=='FLEX' else role==p['position']):
                    raise ValueError('salary_position_eligibility_conflict')
                p['roles'][role]=dict(id=row['id'],salary=cost,score_units=None)
        if captain and 2*p['roles']['CPT']['salary']!=3*p['roles']['FLEX']['salary']:
            raise ValueError('captain_salary_ratio_conflict')
        pool.append(p)
    if len(pool)>MAX_ATHLETES:raise ValueError('salary_athlete_limit')
    if kind=='showdown' and len({p['game'] for p in pool})!=1:raise ValueError('mixed_showdown_games')
    return sorted(pool,key=lambda p:p['key'])


def _rows(raw,cancelled):
    reader=ai._reader(io.StringIO(raw.decode('utf-8-sig'),newline=''))
    header=ai._header(next(reader,[]))
    for n,cells in enumerate(reader):
        check(cancelled)
        if n>=hi.MAX_RESULTS:raise OverflowError('CSV row limit')
        yield n+2,dict(zip(header,cells))


def _score_observations(rows,pool,kind,cancelled):
    by_name={ai._name(p['name']):p for p in pool};by_id={r['id']:p for p in pool for r in p['roles'].values()}
    values=defaultdict(set);lexemes=[];errors=set();outside=0;missing=0
    for line,row in rows:
        check(cancelled)
        label=row.get('player','').strip()
        if not label:continue
        if len(lexemes)>=hi.MAX_SCORE_ROWS:raise OverflowError('Exact score observation limit')
        p=by_name.get(ai._name(label));rawrole=row.get('rosterposition','').strip()
        role=be.normalize_role(rawrole) if rawrole else 'FLEX' if kind=='showdown' else None
        role='CPT' if role=='CPT' else role
        explicit=[row.get(k,'').strip() for k in ('playerid','id','dkid','draftkingsid') if row.get(k,'').strip()]
        suffix=re.search(r'\((\d+)\)\s*$',label)
        if suffix:explicit.append(suffix[1])
        if not p:
            if any(i in by_id for i in explicit):errors.add('actual_explicit_id_name_conflict')
            outside+=1;continue
        target='CPT' if role=='CPT' else 'BASE'
        eligible_ids={r['id'] for label,r in p['roles'].items() if (label=='CPT')==(target=='CPT')}
        if explicit and (len(set(explicit))!=1 or explicit[0] not in eligible_ids):errors.add('actual_explicit_id_conflict')
        if rawrole and (role is None or kind=='showdown' and role not in ('CPT','FLEX') or kind=='classic' and role not in p['roles']):
            errors.add('actual_role_conflict')
        for field,expected in (('teamabbrev',p['team']),('team',p['team']),('position',p['position'])):
            if row.get(field) and row[field].strip().upper().replace('D/ST','DST')!=expected:errors.add('actual_context_conflict')
        if row.get('gameinfo') and p['game'] not in row['gameinfo'].upper():errors.add('actual_game_conflict')
        rawscore=row.get('fpts','');record=dict(line=line,key=p['key'],role=target,lexeme=rawscore,
            explicit_ids=explicit,identity_basis='exact salary ID and name' if explicit else 'exact salary-qualified name; whole-pool ambiguity checked')
        lexemes.append(record)
        if not rawscore.strip():missing+=1;continue
        try:values[p['key'],target].add(score(rawscore,target=='CPT'))
        except ValueError:errors.add('invalid_or_unsupported_actual_score')
    return values,lexemes,errors,dict(outside_salary_observations=outside,blank_observations=missing)


def _roster(text,pool,kind):
    from opponent_analysis import _roster as structural, _MARKERS
    if not isinstance(text,str) or len(text)>4000 or not structural(text,kind):raise ValueError('unreadable_roster')
    names={ai._name(p['name']):p for p in pool};markers=list(_MARKERS.finditer(text));slots=[]
    for n,m in enumerate(markers):
        label=text[m.end():markers[n+1].start() if n+1<len(markers) else len(text)].strip()
        p=names.get(ai._name(label));role=be.normalize_role(m[1]);ident=re.search(r'\((\d+)\)\s*$',label)
        if not p or role not in p['roles']:raise ValueError('roster_identity_or_eligibility')
        if ident and ident[1]!=p['roles'][role]['id']:raise ValueError('roster_explicit_id_conflict')
        slots.append(dict(key=p['key'],role=role))
    return validate_roster(slots,pool,kind,require_scores=False)


def observed_entries(rows,pool,kind,cancelled):
    from opponent_analysis import username_key, _roster as structural
    entries={};conflicts=set();counts=Counter();sizes=set();invalid_size=False
    for line,row in rows:
        check(cancelled)
        if not (row.get('entryid') or row.get('entryname')):continue
        counts['supplied_entry_rows']+=1;ident=row.get('entryid','').strip()
        if not ident:counts['unidentified_rows']+=1;continue
        raw=row.get('points',row.get('actualpoints',''));text=row.get('lineup',row.get('roster',''))
        try:value=decimal(raw)
        except ValueError:value=None
        parsed=structural(text,kind) if kind in ('classic','showdown') else None
        receipt=(username_key(row.get('entryname','')),row.get('rank',''),value if value is not None else raw,
                 parsed[0] if parsed else text)
        if ident in entries:
            if entries[ident]['receipt']==receipt:counts['identical_duplicate_rows']+=1
            else:conflicts.add(ident)
            continue
        for key in ('fieldsize','contestentries','entries'):
            if row.get(key):
                try:
                    size=integer(row[key],10_000_000)
                    if not size:raise ValueError()
                    sizes.add(size)
                except ValueError:invalid_size=True
        witness=None;issue=None
        try:witness=_roster(text,pool,kind)
        except ValueError as exc:issue=str(exc)
        entries[ident]=dict(receipt=receipt,value=value,witness=witness,issue=issue,raw_score=raw)
    accepted=[v for k,v in entries.items() if k not in conflicts]
    known=[v['value'] for v in accepted if v['value'] is not None];highest=max(known) if known else None
    best=[v for v in accepted if highest is not None and v['value']==highest]
    valid=[v for v in accepted if v['witness'] is not None]
    best_valid=[v for v in best if v['witness'] is not None]
    exact=[v for v in best_valid if v['witness']['score_units'] is not None and decimal(v['witness']['points'])==highest]
    size=next(iter(sizes)) if len(sizes)==1 and not invalid_size else None
    counts.update(accepted_entries=len(accepted),conflicting_entry_ids=len(conflicts),known_reported_scores=len(known),
        valid_roster_witnesses=len(valid),unavailable_roster_witnesses=len(accepted)-len(valid),
        reconstructed_total_discrepancies=sum(v['value'] is not None and v['witness']['points'] is not None and
            v['value']!=decimal(v['witness']['points']) for v in valid))
    return dict(coverage=dict(counts),highest=str(highest) if highest is not None else None,
        highest_raw_lexemes=sorted({v['raw_score'] for v in best}),highest_tied_entries=len(best),
        highest_valid_roster_entries=len(best_valid),highest_tied_unique_valid_rosters=len({tuple(v['witness']['signature']) for v in best_valid}),
        highest_exact_validated=bool(exact),highest_units=exact[0]['witness']['score_units'] if exact else None,
        highest_witnesses=[v['witness'] for v in best_valid[:20]],
        field_size=size,field_completeness='reported_count_matches' if size==len(accepted) else 'partial_or_unverified',
        label='Highest reported score in supplied entries; not necessarily the contest winner')


def _periods(rows):
    for _,row in rows:
        for key in ('period','scoringperiod','scoringperiodid'):
            if row.get(key) and row[key].strip().lower() not in ('full','full game','fullgame','full slate','fullslate'):
                return ['unsupported_scoring_period']
        if row.get('sport') and row['sport'].strip().upper()!='NFL':return ['unsupported_sport']
        label=row.get('contestname','').lower()
        if re.search(r'\b(1st|2nd|first|second) half\b|\bquarter\b|\b[1-4]q\b',label):return ['unsupported_contest_variant']
    return []


def capture(db_path,selection,*,history_root=None,cancelled=lambda:False,progress=lambda text:None):
    check(cancelled);path=Path(db_path).absolute();root=Path(history_root).absolute() if history_root else path.parent
    if root!=path.parent:raise ValueError('history_root_mismatch')
    safe_path(path,root)
    with read_database(path) as conn:
        if 'analysis_sources' not in hi._tables(conn):raise ValueError('cataloged_historical_source_required')
        sources=ai._sources(conn);by_hash={s['hash']:s for s in sources}
        if len(sources)>be.MAX_FILES:raise OverflowError('Historical CSV source inventory limit')
        # Bound the authority's existing CSV scans before calling it. Additional
        # lossless reads and rechecks below consume the SAME capture byte budget.
        sizes=[]
        for source in sources:
            p=safe_path(source['snapshot'],root)
            if p.is_file():sizes.append(p.stat().st_size)
        if any(n>be.MAX_FILE_BYTES for n in sizes) or sum(sizes)>be.MAX_TOTAL_BYTES:raise OverflowError('Historical CSV inventory byte limit')
        reader=CaptureReader(cancelled)
        contests=[c.data for c in hi.derive_contests(conn,root,cancelled=cancelled,progress=progress,evidence_reader=reader)]
        data=next((d for d in contests if d['identity_id']==selection.get('contest_id')),None)
        if not data:raise ValueError('selected_historical_contest_unavailable')
        source=by_hash.get(data['results_evidence']['source_hash'])
        if not source:raise ValueError('exact_result_revision_unavailable')
        progress('Reading exact actual-score observations and supplied standings')
        rows=list(_rows(source_bytes(source,root,reader),cancelled))
        blockers=_periods(rows);salary=data.get('salary_evidence') or {};pool=[];kind=data['identity'].get('format')
        if not salary.get('qualified'):blockers+=['qualified_exact_salary_revision_required']
        else:
            s=by_hash[salary['revision_hash']];source_bytes(s,root,reader)
            reader.consume(Path(s['snapshot']).stat().st_size)  # Explicit fresh full-table pass.
            fresh=ai._salary_manifest(s['snapshot'],cancelled)
            if fresh!=s['manifest']:blockers.append('salary_manifest_original_source_conflict')
            kind=fresh['format']
            try:pool=_universe(fresh)
            except ValueError as exc:blockers.append(str(exc))
        actual=dict(athletes=len(pool),known=0,unknown=len(pool),conflicting=0,role_rows=0,observations=[])
        if pool:
            values,lexemes,errors,extra=_score_observations(rows,pool,kind,cancelled)
            blockers.extend(errors);actual.update(extra,observations=lexemes)
            for p in pool:
                base=values.get((p['key'],'BASE'),set());cpt=values.get((p['key'],'CPT'),set())
                conflict=len(base)>1 or len(cpt)>1 or bool(len(base)==1 and any(v!=next(iter(base))*decimal('1.5') for v in cpt))
                if conflict:actual['conflicting']+=1;blockers.append('exact_actual_score_conflict')
                elif len(base)==1:
                    units=int(next(iter(base))*SCALE);actual['known']+=1
                    for role,r in p['roles'].items():r['score_units']=units*3//2 if role=='CPT' else units
                actual['role_rows']+=len(p['original_rows'])
            actual['unknown']=len(pool)-actual['known']
            if actual['unknown']:blockers.append('unknown_eligible_actual_scores')
            # Compare exact observations only; never fill a missing selected score.
            relevant={d['results_evidence']['source_hash'] for d in contests
                if (d.get('salary_evidence') or {}).get('revision_hash')==salary['revision_hash']}
            compared=[]
            for source_hash in sorted(relevant-{source['hash']}):
                other=by_hash[source_hash];other_rows=list(_rows(source_bytes(other,root,reader),cancelled))
                observed,_,other_errors,_=_score_observations(other_rows,pool,kind,cancelled)
                compared.append(source_hash)
                if other_errors:blockers.append('cross_contest_score_evidence_invalid')
                for p in pool:
                    # Compare role-equivalent evidence even if the selected
                    # source has no redundant Captain observation. Never use
                    # this union to fill selected-source score coverage.
                    base=observed.get((p['key'],'BASE'),set())|values.get((p['key'],'BASE'),set())
                    cpt=observed.get((p['key'],'CPT'),set())|values.get((p['key'],'CPT'),set())
                    if len(base)>1 or len(cpt)>1 or any(c!=b*decimal('1.5') for b in base for c in cpt):
                        blockers.append('exact_cross_contest_score_conflict')
            actual['conflict_source_hashes']=compared
            if (data.get('actual_score_evidence') or {}).get('cross_contest_conflicts'):blockers.append('cross_contest_actual_score_conflict')
        observed=observed_entries(rows,pool,kind,cancelled)
        restricted=dict(rules=restrictions(),provenance=None,label='Snapshot-local restricted optimum')
        requested=bool(selection.get('snapshot_digest') or selection.get('archive_id'))
        gate=dict(status='not_requested' if not requested else 'unavailable_evidence',blockers=[])
        if requested:
            evidence=data.get('snapshot_evidence') or {};chosen=reader.snapshots.get(selection.get('snapshot_digest'))
            if not chosen or chosen['raw']['input_id']!=evidence.get('input_id') or chosen['raw']['created_at']!=evidence.get('recorded_at'):
                gate['blockers']=['exact_qualified_snapshot_revision_required']
            elif data.get('snapshot_resolution') and data['snapshot_resolution']['snapshot_digest']!=selection.get('snapshot_digest'):
                gate['blockers']=['saved_snapshot_resolution_preserved']
            else:
                archive=None
                if selection.get('archive_id'):
                    archive=next((a for a in data['build_evidence'] if a['archive_id']==selection['archive_id']),None)
                    matches=reader.archives.get(selection['archive_id'],[])
                    if not archive or not matches or len({be.digest([a[0]['raw'],a[1],a[4]]) for a in matches})!=1:
                        gate['blockers']=['exact_qualified_archive_required']
                    elif not chosen['created']<=be.timestamp(archive['recorded_at'])<chosen['earliest']:
                        gate['blockers']=['archive_snapshot_timing_conflict']
                restricted['rules']=classify(chosen['raw'],pool,kind)
                restricted['provenance']=dict(input_id=evidence['input_id'],snapshot_digest=selection['snapshot_digest'],
                    recorded_at=evidence['recorded_at'],salary_hash=salary.get('revision_hash'),
                    association_method=evidence['association_method'],saved_resolution=data.get('snapshot_resolution'),
                    archive=archive,original_or_submitted_build='not_established')
                if archive:restricted['label']='Selected generated-build/snapshot comparison; submission not established'
                if not gate['blockers']:
                    gate=dict(status='unsupported_rules' if restricted['rules']['blockers'] else 'ready',blockers=restricted['rules']['blockers'])
        blockers=sorted(set(blockers))
        if blockers and requested:gate=dict(status='unavailable_evidence',blockers=blockers+gate['blockers'])
        progress('Revalidating immutable capture receipts and inventories')
        reader.revalidate();check(cancelled)
        return Frozen.freeze(dict(version=1,format=kind,pool=pool,rules=RULES,rules_digest=be.digest(RULES),scale=SCALE,
            universe_digest=be.digest(pool),gates=dict(supplied=dict(status='unavailable_evidence' if blockers else 'ready',blockers=blockers),snapshot=gate),
            restricted=restricted,observed=observed,actual_coverage=actual,
            salary_coverage=dict(source_hash=salary.get('revision_hash'),rows_read=actual['role_rows'],
                rows_expected=len(by_hash.get(salary.get('revision_hash'),{}).get('manifest',{}).get('players',[])),athletes=len(pool)),
            contest_pool_completeness=dict(status='unverified',reason='No independent archived original-platform membership evidence exists'),
            provenance=dict(contest_id=data['identity_id'],result_hash=source['hash'],salary_evidence=salary,
                authority='RL-05A fresh qualification; exact score supplement RL-07A v1',
                source_receipts=dict(reader.receipts),read_bytes=reader.bytes,source_issues=data['source_issues']),
            limits=dict(csv_rows=hi.MAX_RESULTS,score_observations=hi.MAX_SCORE_ROWS,source_files=be.MAX_FILES,
                file_bytes=be.MAX_FILE_BYTES,capture_read_bytes=be.MAX_TOTAL_BYTES,
                csv_budget_basis='Additional lossless CSV reads/rechecks share capture budget with expanded archives; original authority scans have separate preflight file/count bounds')))
