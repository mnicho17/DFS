"""Versioned, rebuildable historical evidence. Never reconstruct live forecasts.

The normalized read model is detached from its sources. Reconciliation replaces
only derived rows, atomically. Saved salary resolutions belong to analysis_imports
and are never written here. A generated archive is not submission evidence.
"""
from collections import Counter, defaultdict
from contextlib import closing
import csv
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import zipfile

from analysis_imports import ImportCancelled, _check, _name, _sources, _verify, _pairing_state, qualify_pair
from contest_objectives import recorded_objective
from compute_ledger import instrument_reconciliation, phase
from results_audit import _number

SCHEMA_VERSION = 1
METHOD_VERSION = 2
STATES = ('UNRESOLVED', 'CANDIDATE', 'SALARY_QUALIFIED', 'SNAPSHOT_QUALIFIED',
          'BUILD_QUALIFIED', 'OUTCOME_QUALIFIED')
MAX_RESULTS = 200_000
MAX_SCORE_ROWS = 5000


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


@dataclass(frozen=True)
class QualifiedHistoricalContest:
    """Immutable serialization; callers receive a fresh, mutable copy on read."""
    evidence: str

    @property
    def data(self):
        return json.loads(self.evidence)


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _groups(conn, sources, cancelled):
    tables = _tables(conn)
    groups = defaultdict(list)
    imports = set()
    if {'import_id','notes'} <= {r[1] for r in conn.execute('PRAGMA table_info(historical_imports)')}:
        imports.update(r[0] for r in conn.execute(
            "SELECT import_id FROM historical_imports WHERE notes IN ('ok','field_only')"))
    result_columns = {r[1] for r in conn.execute('PRAGMA table_info(historical_results)')}
    if {'import_id','sport','slate_date','contest_name','raw_json','result_id'} <= result_columns:
        cursor = conn.execute('SELECT import_id,sport,slate_date,contest_name,raw_json '
                              'FROM historical_results ORDER BY import_id,result_id')
        for index, (ident, sport, day, label, encoded) in enumerate(cursor):
            _check(cancelled)
            if index >= MAX_RESULTS:
                raise ValueError('Historical identity result scan limit exceeded; no derived state saved.')
            try:
                raw = json.loads(encoded or '{}')
                raw = raw if isinstance(raw, dict) else {}
            except (ValueError, TypeError):
                raw = {}
            normalized = {str(k).lower().replace(' ', '').replace('_', ''): v for k, v in raw.items()}
            key = str(normalized.get('contestid') or label or '')
            groups[ident].append(dict(key=key, sport=sport, date=day, raw=normalized))
            imports.add(ident)
    imports.update(s['import_id'] for s in sources if s['kind'] == 'results' and s['import_id'])
    for ident in sorted(imports):
        rows = groups[ident]
        keys = sorted({r['key'] for r in rows})
        # The single-contest key stays stable when later sources supply its ID.
        if len(keys) <= 1:
            yield ident, 'import', rows
        else:
            for key in keys:
                yield ident, _digest(key), [r for r in rows if r['key'] == key]


@phase('historical_evidence_inventory', output_size=lambda result: len(result[0]) + len(result[1]))
def _inventory(root, cancelled):
    from review_build_evidence import Reader, snapshot, timestamp
    # Use one cancellation exception throughout the reconciliation API, including
    # directory enumeration before individual files are opened.
    reader = Reader(lambda: _check(cancelled))
    snapshots, archives, issues = {}, {}, Counter()
    for folder, suffix in (('snapshots', '.json'), ('build-archives', '.zip')):
        for path in reader.paths(Path(root) / folder, suffix):
            _check(cancelled)
            try:
                if suffix == '.json':
                    snap = snapshot(reader.data(path))
                else:
                    snap, meta, rosters, _ = reader.archive(path)
                    stamp = timestamp(meta.get('created_at'))
                    pregame = bool(snap['pregame'] and stamp and snap['created'] <= stamp < snap['earliest'])
                    archive = dict(archive_id=_digest([meta, sorted(rosters)]), input_id=snap['raw']['input_id'],
                        generated_rosters=[list(r) for r in sorted(rosters)], output_count=meta['output_count'],
                        recorded_at=meta.get('created_at'), completed=meta.get('build_status') == 'completed',
                        pregame=pregame, contest_objective=recorded_objective(meta.get('contest_objective')),
                        association_method='immutable_input_id', submission='not_established')
                    archives[archive['archive_id']] = archive
                snapshots[_digest(snap['raw'])] = snap
            except OverflowError:
                issues['evidence_read_limit'] += 1
                break
            except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError,
                    UnicodeError, zipfile.BadZipFile, NotImplementedError, RuntimeError):
                issues['invalid_snapshot_or_archive'] += 1
    issues.update(reader.issues)
    return list(snapshots.values()), sorted(archives.values(), key=lambda a: a['archive_id']), dict(issues)


def _snapshot_compatible(snap, salary):
    """Verify the exact salary pool and roles in addition to the existing matcher.

    A subset sharing a date/names cannot certify the historical eligible pool.
    Projection/depth/availability are read only from the frozen snapshot.
    """
    if snap['kind'] != salary['format'] or [snap['day']] != salary['dates']:
        return False
    lookup = {_name(p.get('Name')): p for p in snap['players']}
    if len(lookup) != len(snap['players']) or set(lookup) != {_name(p['name']) for p in salary['players']}:
        return False
    for row in salary['players']:
        p = lookup[_name(row['name'])]
        prefix = 'Cpt' if row['role'] == 'CPT' else 'Flex'
        if (str(p.get(prefix+'ID') or '') != row['id'] or
                _number(p.get(prefix+'Salary')) != row['salary'] or
                str(p.get('Team') or '').upper() != row['team'] or
                str(p.get('Position') or '').upper().replace('D/ST', 'DST') != row['position']):
            return False
        # The same date and salary pool with different games is conflicting evidence.
        game = re.search(r'\b[A-Z]{2,3}@[A-Z]{2,3}\b', str(p.get('GameInfo', '')).upper())
        if not game or game.group(0) not in salary['games']:
            return False
    return True


def _stored_role_coverage(rows, manifest, salary, cancelled):
    """Check additional stored entry observations through the same salary matcher."""
    from opponent_analysis import _MARKERS, _roster
    observed = set()
    texts = {text for r in rows if isinstance(text := r['raw'].get('lineup') or r['raw'].get('roster'), str)}
    for text in sorted(texts):
        _check(cancelled)
        if not isinstance(text, str) or not _roster(text, salary['format']):
            continue  # Unreadable rows have their own explicit coverage blocker.
        markers = list(_MARKERS.finditer(text))
        for index, marker in enumerate(markers):
            label = text[marker.end():markers[index+1].start() if index+1<len(markers) else len(text)].strip()
            role = marker.group(1).upper().replace('CAPTAIN','CPT').replace('D/ST','DST')
            numeric = re.search(r'\((\d+)\)\s*$',label)
            observed.add((role,_name(label),numeric.group(1) if numeric else ''))
    if not observed:
        return dict(compatible=True, matched=0, observed=0)
    return qualify_pair({'manifest':dict(manifest,observed=sorted(observed))},{'manifest':salary})


def _outcomes(source, salary, cancelled):
    """Observe the immutable result side table, including duplicate conflicts.

    Base scores apply once per athlete; Captain scores are explicitly 1.5x base.
    An observed Captain value is also checked. Missing base values stay unknown.
    """
    from review_build_evidence import roster_key, name_key
    observed, conflicts, limitations = defaultdict(set), set(), []
    result_rosters = set()
    try:
        with open(source['snapshot'], newline='', encoding='utf-8-sig') as handle:
            reader = csv.DictReader(handle)
            if not {'Player', 'FPTS'} <= set(reader.fieldnames or []):
                limitations.append('missing_player_score_table')
            else:
                for index, row in enumerate(reader):
                    _check(cancelled)
                    if index >= MAX_RESULTS or len(observed) > MAX_SCORE_ROWS:
                        limitations.append('player_score_read_limit')
                        break
                    if row.get('EntryId') or row.get('EntryName'):
                        parsed = roster_key(row.get('Lineup') or row.get('Roster'))
                        total = _number(row.get('Points') or row.get('ActualPoints'))
                        if parsed and total is not None:
                            result_rosters.add((parsed[1],total))
                    name = _name(row.get('Player') or '')
                    if not name:
                        continue
                    role = str(row.get('Roster Position') or '').strip().upper()
                    key = ('@cpt:' if role in ('CPT', 'CAPTAIN') else '') + name
                    value = _number(row.get('FPTS'))
                    if value is not None:
                        observed[key].add(value)
        _verify(source, cancelled)
    except (OSError, UnicodeError, ValueError, csv.Error):
        limitations.append('result_source_unavailable_or_changed')
        observed.clear()
    for key, values in observed.items():
        if max(values) - min(values) > .02:
            conflicts.add(key)
    rows = []
    for player in salary['players']:
        name = _name(player['name'])
        key = ('@cpt:' if player['role'] == 'CPT' else '') + name
        values = observed.get(name, set())
        value = min(values) if values and name not in conflicts else None
        captain = player['role'] == 'CPT'
        score = value * (1.5 if captain else 1) if value is not None else None
        if captain and score is not None and any(abs(v-score) > .02 for v in observed.get(key, ())):
            conflicts.add(key)
        if key in conflicts or name in conflicts:
            score = None
        rows.append(dict(player_id=player['id'], name=player['name'], role=player['role'], actual=score,
                         basis='base_score_x1.5' if captain else 'observed_base_score'))
    unknown = sum(r['actual'] is None for r in rows)
    by_role = {('@cpt:' if r['role']=='CPT' else '')+name_key(r['name']):r['actual'] for r in rows}
    checked = mismatches = 0
    for roster, total in result_rosters:
        values = [by_role.get(k) for k in roster]
        if all(v is not None for v in values):
            checked += 1
            mismatches += abs(sum(values)-total) > .15
    if mismatches:
        limitations.append('roster_actual_total_conflict')
    return dict(eligible_player_roles=len(rows), known_scores=len(rows)-unknown, unknown_scores=unknown,
                conflicting_scores=sorted(conflicts), scores=rows, limitations=limitations,
                checked_roster_totals=checked, conflicting_roster_totals=mismatches,
                complete=bool(rows) and not unknown and not conflicts and not limitations,
                captain_handling='separate salary IDs; base scores counted once; Captain = 1.5x base')


@phase('historical_contest_qualification', output_size=lambda result: 1)
def _contest(ident, key, rows, source, pair, basis, by_hash, snapshots, archives, issues, root, cancelled, resolution=None):
    from learning_db import _normalize_roster_token
    from results_snapshot_learning import match_snapshot
    from review_build_evidence import roster_key
    result = dict(identity_id=_digest(['historical-contest', ident, key]), import_id=ident, contest_key=key,
        schema_version=SCHEMA_VERSION, method_version=METHOD_VERSION, state='UNRESOLVED',
        identity=dict(sport=None, format=None, slate_date=None, contest_objective=None),
        results_evidence=dict(observed_rows=len(rows), source_hash=source['hash'] if source else None),
        salary_evidence=None, snapshot_evidence=None, snapshot_candidates=[], build_evidence=[],
        build_candidates=[], player_evidence=[],
        actual_score_evidence=None, limitations=[], conflicts=[], blockers=[], source_issues=issues,
        snapshot_resolution=resolution)
    limitations, conflicts, blockers = result['limitations'], result['conflicts'], result['blockers']
    explicit_days = sorted({r['date'] for r in rows if r['date']})
    explicit_sports = sorted({r['sport'] for r in rows if r['sport'] and r['sport'] != 'UNKNOWN'})
    explicit_formats = sorted({str(r['raw'].get('contesttype') or '').lower() for r in rows
                               if r['raw'].get('contesttype')})
    result['results_evidence'].update(observed_dates=explicit_days, observed_sports=explicit_sports,
                                      observed_formats=explicit_formats)
    result['identity'].update(slate_date=explicit_days[0] if len(explicit_days)==1 else None,
                             sport=explicit_sports[0] if len(explicit_sports)==1 else None,
                             format=explicit_formats[0] if len(explicit_formats)==1 else None)
    if not source or not pair:
        blockers.append('missing_cataloged_result_and_salary_association')
        limitations.append('legacy_matches_do_not_establish_identity')
        return result
    manifest = source['manifest']
    result['results_evidence'].update(contest_ids=manifest.get('contest_ids', []),
        readable_rosters=pair['readable'], unreadable_rosters=pair['unreadable'], entries=pair['entries'])
    candidates = [c for c in pair['candidates'] if c['compatible']]
    result['salary_candidates'] = sorted(c['hash'] for c in candidates)
    selected = None
    if pair['status'] == 'PAIRED':
        selected = pair['salary_hash']
        method = basis.get(source['hash'], 'saved_association')
    elif pair['status'] == 'INVALID_SAVED_PAIR':
        result['state'] = 'CANDIDATE'
        result['salary_evidence'] = dict(revision_hash=pair['salary_hash'], qualified=False,
                                         association_method=basis.get(source['hash'], 'saved_association'))
        conflicts.append('invalid_saved_salary_revision')
        blockers.append('saved_salary_association_requires_review')
    elif candidates:
        result['state'] = 'CANDIDATE'
        if len(candidates) == 1 and candidates[0]['automatic']:
            selected = candidates[0]['hash']
            method = 'unique_compatible_revision_automatic_qualification_not_saved_pair'
        else:
            blockers.append('ambiguous_salary_revisions' if len(candidates)>1 else 'salary_date_confirmation_required')
    else:
        blockers.append('no_compatible_salary_revision')
    if not selected:
        return result
    salary = by_hash[selected]['manifest']
    if explicit_days and explicit_days != salary['dates']:
        conflicts.append('explicit_result_salary_date_conflict')
    if explicit_sports and explicit_sports != [salary['sport']]:
        conflicts.append('explicit_result_salary_sport_conflict')
    if explicit_formats and explicit_formats != [salary['format']]:
        conflicts.append('explicit_result_salary_format_conflict')
    if key != 'import':
        conflicts.append('multiple_result_contests_require_separate_sources')
    result['salary_evidence'] = dict(revision_hash=selected, result_hash=source['hash'],
        association_method=method, qualified=not conflicts, dates=salary['dates'], games=salary['games'],
        sport=salary['sport'], format=salary['format'])
    if conflicts:
        result['state']='CANDIDATE'
        blockers.append('conflicting_result_salary_identity')
        return result
    result['state'] = 'SALARY_QUALIFIED'
    result['identity'].update(sport=salary['sport'], format=salary['format'], slate_date=salary['dates'][0])
    result['player_evidence'] = [{k:p[k] for k in ('name','id','role','position','team','opponent','salary')}
                                 for p in salary['players']]
    stored_coverage = _stored_role_coverage(rows, manifest, salary, cancelled)
    result['results_evidence']['stored_player_role_coverage'] = dict(
        matched=stored_coverage['matched'], observed=stored_coverage['observed'])
    if not stored_coverage['compatible']:
        result['state'] = 'CANDIDATE'
        result['salary_evidence']['qualified'] = False
        conflicts.append('stored_result_player_role_conflict')
        blockers.append('incomplete_stored_player_role_coverage')
        return result
    outcome = _outcomes(source, salary, cancelled)
    result['actual_score_evidence'] = outcome
    try:
        _verify(by_hash[selected], cancelled)
        if 'result_source_unavailable_or_changed' in outcome['limitations']:
            raise ValueError('source changed')
    except (OSError, ValueError):
        result['state'] = 'CANDIDATE'
        result['salary_evidence']['qualified'] = False
        conflicts.append('source_revision_changed_during_reconciliation')
        blockers.append('restore_immutable_source_revision')
        return result
    if pair['unreadable'] or not pair['readable']:
        blockers.append('unreadable_result_rosters')
    if outcome['unknown_scores']:
        blockers.append('unknown_eligible_actual_scores')
    if outcome['conflicting_scores']:
        conflicts.append('conflicting_actual_scores')
    blockers.extend(outcome['limitations'])
    # Stored personal result rows must also have readable compatible roster identity.
    invalid_rows = sum(not (parsed := roster_key(r['raw'].get('lineup') or r['raw'].get('roster'))) or
                       parsed[0] != salary['format'] for r in rows)
    result['results_evidence']['unreadable_stored_rosters'] = invalid_rows
    if invalid_rows:
        blockers.append('unreadable_stored_result_rosters')
    compatible = [s for s in snapshots if _snapshot_compatible(s, salary)]
    candidate_ids = {s['raw']['input_id'] for s in compatible if s['pregame']}
    result['build_candidates'] = [a for a in archives if a['input_id'] in candidate_ids and a['pregame'] and a['completed']]
    if any(not s['pregame'] for s in compatible):
        limitations.append('postgame_or_unknown_time_snapshots_rejected')
    if any(s['kind']==salary['format'] and s['day'] in salary['dates'] and s not in compatible for s in snapshots):
        limitations.append('snapshot_salary_pool_or_role_conflict')
    truncated = any('limit' in code for code in issues)
    def match(candidates):
        return match_snapshot(root, salary['dates'][0], salary['format'],
            {_normalize_roster_token(p['name']) for p in salary['players']}, '',
            snapshots=candidates if not truncated else [],
            contest_ids=manifest.get('contest_ids', []), check=lambda: _check(cancelled))
    snapshot, reason = match([s['raw'] for s in compatible])
    # The same matcher, including contest-ID precedence, qualifies every manual
    # choice. A confirmation identifies exact content AND timestamp, not just input_id.
    eligible = [s for s in compatible if match([s['raw']])[0] is not None]
    result['snapshot_candidates'] = sorted([dict(input_id=s['raw']['input_id'],
        snapshot_digest=_digest(s['raw']), recorded_at=s['raw']['created_at'],
        earliest_game=s['earliest'].isoformat(), format=s['kind'], player_count=len(s['players']),
        pregame=True, build_count=sum(a['input_id']==s['raw']['input_id'] for a in result['build_candidates']))
        for s in eligible], key=lambda s:(s['recorded_at'],s['input_id'],s['snapshot_digest']))
    if resolution:
        chosen = [s for s in eligible if _digest(s['raw']) == resolution['snapshot_digest']
                  and s['raw']['input_id'] == resolution['input_id']]
        if resolution['salary_hash'] != selected or not chosen:
            result['state'] = 'CANDIDATE'
            conflicts.append('invalid_saved_snapshot_resolution')
            blockers.append('restore_confirmed_snapshot_evidence')
            return result
        snapshot, reason = chosen[0]['raw'], 'user_confirmed'
    if not snapshot:
        blockers.append('snapshot_evidence_scan_incomplete' if truncated else 'no_unambiguous_pregame_snapshot')
        if reason.startswith('Conflicting latest'):
            conflicts.append('conflicting_latest_snapshots')
            result['state'] = 'CANDIDATE'
        return result
    input_id = snapshot['input_id']
    if not resolution and len({_digest(s['raw']) for s in compatible if s['raw']['input_id']==input_id}) > 1:
        conflicts.append('snapshot_timestamp_conflict')
        result['state'] = 'CANDIDATE'
        blockers.append('no_unambiguous_pregame_snapshot')
        return result
    inputs = snapshot['inputs']
    objective = recorded_objective(inputs['recipe'].get('contest_objective',
        inputs['contest'].get('objective', inputs['contest'].get('contest_objective'))))
    result['identity']['contest_objective'] = objective
    result['snapshot_evidence'] = dict(input_id=input_id, recorded_at=snapshot['created_at'],
        association_method=('user_confirmed' if resolution else 'contest_id' if reason=='contest-ID association'
                            else 'qualified_salary_pool_and_latest_pregame'),
        earliest_game=next(s['earliest'].isoformat() for s in eligible if _digest(s['raw'])==_digest(snapshot)),
        contest_objective=objective, players=inputs['players'], rules=inputs['rules'], recipe=inputs['recipe'],
        original_build='not_established')
    result['state'] = 'SNAPSHOT_QUALIFIED'
    result['build_evidence'] = [a for a in archives if a['input_id']==input_id and a['pregame'] and a['completed']]
    if result['build_evidence']:
        result['state'] = 'BUILD_QUALIFIED'
        limitations.append('generated_archives_do_not_prove_original_or_submitted_build')
        if len(result['build_evidence']) > 1:
            limitations.append('multiple_qualified_builds_preserved')
    else:
        blockers.append('missing_qualified_build_archive')
    # Outcome completeness is independent of build identity; the top state requires
    # the entire ladder. Consumers can inspect outcome.complete at earlier states.
    if result['build_evidence'] and outcome['complete'] and not blockers and not conflicts:
        result['state'] = 'OUTCOME_QUALIFIED'
    return result


@phase("historical_identity_derivation")
def derive_contests(conn, root, *, cancelled=lambda: False, progress=lambda text: None):
    """Read a consistent caller-owned DB snapshot; perform no SQL/file writes."""
    _check(cancelled)
    sources = _sources(conn) if 'analysis_sources' in _tables(conn) else []
    # The caller already holds a read/write transaction. Reuse the authoritative
    # evaluator without its connection/path/savepoint wrapper (report reads allow
    # SELECT/table_info only and must not create settings or history).
    progress('Verifying saved results and salary revisions')
    state = _pairing_state(conn, None, cancelled)
    by_hash = {s['hash']:s for s in sources}
    pairs = {r['hash']:r for r in state['results']}
    basis = dict(conn.execute('SELECT result_hash,basis FROM analysis_salary_pairs')) if 'analysis_salary_pairs' in _tables(conn) else {}
    by_import = defaultdict(list)
    for s in sources:
        if s['kind']=='results':
            by_import[s['import_id']].append(s)
    progress('Reading pregame snapshots and build archives')
    snapshots, archives, issues = _inventory(root, cancelled)
    resolutions = {}
    if 'historical_evidence_resolutions' in _tables(conn):
        for ident, digest, input_id, salary_hash, method, version, stamp in conn.execute(
                'SELECT * FROM historical_evidence_resolutions'):
            resolutions[ident] = dict(snapshot_digest=digest, input_id=input_id, salary_hash=salary_hash,
                method=method, evidence_version=version, confirmed_at=stamp)
    output = []
    for ident, key, rows in _groups(conn, sources, cancelled):
        _check(cancelled)
        progress(f'Qualifying historical contest {len(output)+1}')
        candidates = by_import[ident]
        source = candidates[0] if len(candidates)==1 else None
        data = _contest(ident, key, rows, source, pairs.get(source['hash']) if source else None,
                        basis, by_hash, snapshots, archives, issues, root, cancelled,
                        resolutions.get(_digest(['historical-contest', ident, key])))
        if data['snapshot_resolution'] and not data['snapshot_evidence']:
            data['state'] = 'CANDIDATE'
            data['conflicts'].append('invalid_saved_snapshot_resolution')
        for field in ('limitations','conflicts','blockers'):
            data[field] = sorted(set(data[field]))
        output.append(data)
    # Same qualified immutable salary revision is stronger than a shared date.
    # Multiple contests are not independent confirmations of differing scores.
    scores = defaultdict(set)
    for data in output:
        salary = data['salary_evidence'] or {}
        if not salary.get('qualified'):
            continue
        for score in (data['actual_score_evidence'] or {}).get('scores', []):
            if score['actual'] is not None:
                scores[(salary['revision_hash'],score['player_id'],score['role'])].add(score['actual'])
    inconsistent = {key for key, values in scores.items() if max(values)-min(values) > .02}
    for data in output:
        _check(cancelled)
        salary = data['salary_evidence'] or {}
        outcome = data['actual_score_evidence']
        if salary.get('qualified') and outcome:
            affected = [dict(player_id=r['player_id'],role=r['role']) for r in outcome['scores']
                        if (salary['revision_hash'],r['player_id'],r['role']) in inconsistent]
            outcome['cross_contest_conflicts'] = affected
            if affected:
                outcome['complete'] = False
                data['conflicts'] = sorted(set(data['conflicts']+['cross_contest_actual_score_conflict']))
                if data['state']=='OUTCOME_QUALIFIED':
                    data['state']='BUILD_QUALIFIED'
    _check(cancelled)
    return tuple(QualifiedHistoricalContest(_json(d)) for d in output)


def qualified_contests(db_path=None, *, cancelled=lambda: False):
    """Fresh read model, including invalidation; cached derived rows are not proof."""
    from data_paths import history_source_paths
    path = Path(db_path or history_source_paths()[0]).resolve()
    if not path.exists():
        return ()
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as conn:
        conn.execute('BEGIN')
        return derive_contests(conn, path.parent, cancelled=cancelled)


@instrument_reconciliation
@phase("historical_identity_reconciliation")
def reconcile(db_path=None, *, cancelled=lambda: False, progress=lambda text: None, snapshot_choices=None):
    """Rebuild derived state all-or-nothing. Source observations remain untouched."""
    from learning_db import _connect, history_db_path, init_historical_import_tables
    path = Path(db_path or history_db_path())
    _check(cancelled)
    with closing(_connect(str(path))) as conn:
        init_historical_import_tables(conn)
        conn.execute('BEGIN IMMEDIATE')
        try:
            contests = derive_contests(conn, path.parent, cancelled=cancelled, progress=progress)
            if snapshot_choices:
                indexed = {c.data['identity_id']: c.data for c in contests}
                for ident, digest in snapshot_choices.items():
                    _check(cancelled)
                    data = indexed.get(ident, {})
                    existing = data.get('snapshot_resolution')
                    if existing:
                        if existing['snapshot_digest'] != digest:
                            raise ValueError('A snapshot is already confirmed. The saved choice was preserved.')
                        continue
                    if not set(data.get('conflicts', [])) & {'conflicting_latest_snapshots','snapshot_timestamp_conflict'}:
                        raise ValueError('Snapshot evidence is no longer ambiguous. Refresh coverage before choosing.')
                    candidate = next((c for c in data['snapshot_candidates'] if c['snapshot_digest']==digest), None)
                    if not candidate:
                        raise ValueError('The chosen snapshot no longer qualifies. No changes were saved.')
                    conn.execute('''INSERT INTO historical_evidence_resolutions
                        (identity_id,snapshot_digest,input_id,salary_hash,method,evidence_version)
                        VALUES (?,?,?,?,?,?)''', (ident,digest,candidate['input_id'],
                        data['salary_evidence']['revision_hash'],'user_confirmed',METHOD_VERSION))
                contests = derive_contests(conn, path.parent, cancelled=cancelled, progress=progress)
                for contest in contests:
                    data = contest.data
                    if data['identity_id'] in snapshot_choices and not data['snapshot_evidence']:
                        raise ValueError('Confirmed evidence changed during reconciliation. No changes were saved.')
            progress('Saving all derived evidence in one transaction')
            for contest in contests:
                _check(cancelled)
                d = contest.data
                conn.execute('''INSERT INTO historical_contest_identities
                    (identity_id,import_id,contest_key,schema_version,state,evidence_hash,payload)
                    VALUES (?,?,?,?,?,?,?) ON CONFLICT(identity_id) DO UPDATE SET
                    schema_version=excluded.schema_version,state=excluded.state,
                    evidence_hash=excluded.evidence_hash,payload=excluded.payload,updated_at=CURRENT_TIMESTAMP
                    WHERE historical_contest_identities.evidence_hash != excluded.evidence_hash''',
                    (d['identity_id'],d['import_id'],d['contest_key'],SCHEMA_VERSION,d['state'],
                     hashlib.sha256(contest.evidence.encode()).hexdigest(),contest.evidence))
            ids = {c.data['identity_id'] for c in contests}
            for ident, in conn.execute('SELECT identity_id FROM historical_contest_identities').fetchall():
                _check(cancelled)
                if ident not in ids:
                    conn.execute('DELETE FROM historical_contest_identities WHERE identity_id=?',(ident,))
            _check(cancelled)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return contests


def coverage(contests):
    """Allowlisted aggregates only: no names, native IDs, paths or raw reasons."""
    counts = Counter({s:0 for s in STATES})
    blockers = Counter()
    conflicts = Counter()
    limitations = Counter()
    source_issues = Counter()
    for contest in contests:
        d = contest.data
        counts[d['state']] += 1
        blockers.update(d['blockers'])
        conflicts.update(d['conflicts'])
        limitations.update(d['limitations'])
        source_issues.update({key:1 for key in d['source_issues']})
    return dict(schema_version=SCHEMA_VERSION, total_historical_contests=sum(counts.values()),
        states=dict(counts), blockers=dict(sorted(blockers.items())), conflicts=dict(sorted(conflicts.items())),
        limitations=dict(sorted(limitations.items())), source_issues_contests=dict(sorted(source_issues.items())),
        basis='result import/contest groups; states are exclusive; generated builds do not establish submission')
