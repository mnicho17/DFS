"""RL-05B presentation of RL-05A evidence, never an alternate qualifier.

The fast view reads the last committed derived state. Only explicit background
reconciliation revalidates files. Cached evidence cannot authorize analysis.
"""
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from historical_identity import STATES, SCHEMA_VERSION, coverage


REASONS = {
    'missing_cataloged_result_and_salary_association': 'Result source or salary association is not cataloged. Import Results & Salaries.',
    'legacy_matches_do_not_establish_identity': 'Legacy name/export matches do not establish salary or slate identity.',
    'invalid_saved_salary_revision': 'The saved salary revision is missing, changed or incompatible. Restore that exact revision; it was not replaced.',
    'saved_salary_association_requires_review': 'Review the saved salary association in Review Salary Matches.',
    'ambiguous_salary_revisions': 'Multiple compatible salary revisions need an explicit selection in Review Salary Matches.',
    'salary_date_confirmation_required': 'Result date is missing. Confirm the salary slate date in Review Salary Matches.',
    'no_compatible_salary_revision': 'No salary revision covers the observed players/roles and slate. Review Salary Matches shows each mismatch.',
    'explicit_result_salary_date_conflict': 'The recorded result date conflicts with the salary slate date.',
    'explicit_result_salary_sport_conflict': 'The recorded result sport conflicts with the salary sport.',
    'explicit_result_salary_format_conflict': 'The recorded result format conflicts with the salary format (Classic/Showdown).',
    'multiple_result_contests_require_separate_sources': 'This source contains multiple contests; separate result sources are required.',
    'conflicting_result_salary_identity': 'Explicit result and salary identity disagree.',
    'stored_result_player_role_conflict': 'Stored result players or Captain/FLEX roles conflict with the salary revision.',
    'incomplete_stored_player_role_coverage': 'Some stored result players/roles are not covered by the salary revision.',
    'source_revision_changed_during_reconciliation': 'A source revision changed during reconciliation.',
    'restore_immutable_source_revision': 'Restore the original saved source revision and reconcile again.',
    'unreadable_result_rosters': 'Some result rosters are unreadable and excluded from qualification.',
    'unknown_eligible_actual_scores': 'Some eligible player-role scores are unknown. They are not treated as zero.',
    'conflicting_actual_scores': 'The source contains conflicting actual scores.',
    'unreadable_stored_result_rosters': 'Some stored result rosters are unreadable.',
    'postgame_or_unknown_time_snapshots_rejected': 'A compatible snapshot was recorded after game start or has unknown timing.',
    'snapshot_salary_pool_or_role_conflict': 'Other snapshots do not match the complete salary pool, roles or game identity.',
    'snapshot_evidence_scan_incomplete': 'Evidence scan limits were reached. Snapshot selection is withheld.',
    'no_unambiguous_pregame_snapshot': 'No unambiguous compatible pregame snapshot is available.',
    'conflicting_latest_snapshots': 'Multiple latest compatible pregame snapshots need an explicit choice.',
    'snapshot_timestamp_conflict': 'The same input has conflicting snapshot timestamps; review the exact revisions.',
    'invalid_saved_snapshot_resolution': 'The confirmed snapshot no longer qualifies. The saved selection is preserved; no replacement was chosen.',
    'restore_confirmed_snapshot_evidence': 'Restore the confirmed snapshot and its salary revision, then reconcile again.',
    'missing_qualified_build_archive': 'No completed pregame build archive shares this exact snapshot input ID.',
    'generated_archives_do_not_prove_original_or_submitted_build': 'Generated archives do not establish the original or submitted build.',
    'multiple_qualified_builds_preserved': 'Multiple qualified builds are retained; none is assumed to be the submitted portfolio.',
    'cross_contest_actual_score_conflict': 'Contests using the same salary revision disagree on actual scores.',
    'missing_player_score_table': 'No eligible-player score table was supplied.',
    'player_score_read_limit': 'The score scan limit was reached; completeness is unknown.',
    'result_source_unavailable_or_changed': 'The immutable result source is missing or has changed.',
    'roster_actual_total_conflict': 'Known player scores disagree with an observed roster total.',
}


def reason_text(code):
    return REASONS.get(code, 'Additional evidence issue: ' + code.replace('_', ' '))


def evidence_levels(data):
    """Independent stage facts; do not infer every stage from the exclusive state."""
    return dict(results=True,
        salary=bool((data.get('salary_evidence') or {}).get('qualified')),
        snapshot=bool(data.get('snapshot_evidence')),
        build=bool(data.get('build_evidence')),
        outcome=bool((data.get('actual_score_evidence') or {}).get('complete')))


def categories(data):
    ready = data['state'] == 'OUTCOME_QUALIFIED'
    conflict = bool(data.get('conflicts'))
    review = data['state'] == 'CANDIDATE' or conflict
    return dict(ready=ready, needs_review=review, missing_evidence=not ready and not review, conflict=conflict)


def capabilities(data):
    levels = evidence_levels(data)
    build = levels['salary'] and levels['snapshot'] and levels['build']
    return dict(results=True, portfolio_risk=build,
        hindsight=data['state'] == 'OUTCOME_QUALIFIED',
        compute_input_output=build, compute_timing=False)


def aggregate(contests):
    result = coverage(contests)
    levels, groups, supported = Counter(), Counter(), Counter()
    for contest in contests:
        data = contest.data
        levels.update({k:int(v) for k,v in evidence_levels(data).items()})
        groups.update({k:int(v) for k,v in categories(data).items()})
        supported.update({k:int(v) for k,v in capabilities(data).items()})
    result.update(coverage_version=1, evidence_levels=dict(levels), categories=dict(groups),
        capability_evidence=dict(supported),
        capability_basis='prerequisites only; Portfolio Risk revalidates its selected archive; RL-07 is not implemented; generated input/output evidence does not establish phase timings or submission')
    return result


def load_saved(db_path):
    """Read cached JSON and catalog counts, never open/hash/reparse source files."""
    path = Path(db_path).resolve()
    result = dict(contests=(), updated_at=None, pending_imports=0, invalid_rows=0, labels={})
    if not path.exists():
        return result
    from historical_identity import QualifiedHistoricalContest
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as conn:
        conn.execute('BEGIN')
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        saved, imports, dates = [], set(), []
        if 'historical_contest_identities' in tables:
            for encoded, digest, stamp in conn.execute('SELECT payload,evidence_hash,updated_at FROM historical_contest_identities ORDER BY identity_id'):
                try:
                    data = json.loads(encoded)
                    if (hashlib.sha256(encoded.encode()).hexdigest()!=digest or
                            data['schema_version']!=SCHEMA_VERSION or data['state'] not in STATES):
                        raise ValueError('Invalid derived row')
                    imports.add(data['import_id'])
                    saved.append(QualifiedHistoricalContest(encoded))
                    dates.append(stamp)
                except (ValueError, TypeError, KeyError):
                    result['invalid_rows'] += 1
        catalog = set()
        if 'historical_imports' in tables:
            catalog.update(r[0] for r in conn.execute("SELECT import_id FROM historical_imports WHERE notes IN ('ok','field_only')"))
            result['labels'].update(conn.execute('SELECT import_id,file_name FROM historical_imports'))
        if 'analysis_sources' in tables:
            catalog.update(r[0] for r in conn.execute("SELECT import_id FROM analysis_sources WHERE kind='results'"))
        result.update(contests=tuple(saved), updated_at=max(dates) if dates else None,
                      pending_imports=len(catalog-imports))
    return result


def changes(before, after):
    old = {c.data['identity_id']:c.data for c in before}
    counts = Counter({k:0 for k in ('salary','snapshot','build','outcome')})
    downgraded = 0
    for contest in after:
        data = contest.data
        previous = old.get(data['identity_id'])
        prior = evidence_levels(previous) if previous else {}
        current = evidence_levels(data)
        # The completion summary says "newly outcome-qualified", which requires
        # the full ladder. Independent complete-score coverage remains in overview.
        prior['outcome'] = bool(previous and previous['state']=='OUTCOME_QUALIFIED')
        current['outcome'] = data['state']=='OUTCOME_QUALIFIED'
        counts.update({k:int(current[k] and not prior.get(k)) for k in counts})
        downgraded += bool(previous and any(prior.get(k) and not current[k] for k in counts))
    return dict(newly_qualified=dict(counts), downgraded=downgraded,
                removed=len(set(old)-{c.data['identity_id'] for c in after}), coverage=aggregate(after))


def reconciliation_text(result):
    counts = result['newly_qualified']
    cats = result['coverage']['categories']
    return ('Reconciliation committed. Newly qualified: ' + ', '.join(f'{k} {v}' for k,v in counts.items()) +
            f". Downgraded: {result['downgraded']}; removed: {result['removed']}. " +
            f"Needs review: {cats.get('needs_review',0)}; missing evidence: {cats.get('missing_evidence',0)}; conflicts: {cats.get('conflict',0)}.")


def detail_text(data):
    ident = data['identity']
    levels = evidence_levels(data)
    lines = [f"{ident.get('sport') or 'Unknown sport'} / {ident.get('format') or 'Unknown format'} / {ident.get('slate_date') or 'Date unavailable'}",
             f"Evidence state: {data['state']}", 'Evidence chain (last committed reconciliation):']
    for label, key in (('Results','results'),('Salary / slate','salary'),('Pregame snapshot','snapshot'),
                       ('Build archive','build'),('Outcome coverage','outcome')):
        missing = ('Conflict / needs review' if data.get('conflicts') else
                   'Ambiguous / needs review' if data['state']=='CANDIDATE' else
                   'Unresolved' if data['state']=='UNRESOLVED' else 'Unavailable')
        lines.append(f"  {label}: {'Qualified' if levels[key] else missing}")
    r = data['results_evidence']
    lines += [f"Stored personal/legacy result rows: {r.get('observed_rows',0)}",
              f"Readable source rosters: {r.get('readable_rosters','unknown')} / {r.get('entries','unknown')}; unreadable: {r.get('unreadable_rosters','unknown')}"]
    salary = data.get('salary_evidence') or {}
    if salary:
        lines += [f"Salary revision: {salary.get('revision_hash','')[:12]}",
                  'Association: ' + salary.get('association_method','unknown').replace('_',' ')]
    roles = r.get('stored_player_role_coverage') or {}
    if roles:
        lines.append(f"Stored player/role coverage: {roles['matched']} / {roles['observed']}")
    snap = data.get('snapshot_evidence') or {}
    if snap:
        lines += [f"Input: {snap['input_id'][:12]} — {snap['association_method'].replace('_',' ')}",
                  f"Recorded: {snap['recorded_at']}; earliest game: {snap.get('earliest_game','not recorded')}"]
    choice = data.get('snapshot_resolution')
    if choice:
        lines.append(f"User confirmed: {choice['confirmed_at']} (evidence version {choice['evidence_version']}); exact revision {choice['snapshot_digest'][:12]}")
    seen_archives = set()
    for heading, builds in (
        ('Archives linked to the current qualified snapshot:', data.get('build_evidence', [])),
        ('Other generated archive candidates (not qualified build evidence):', data.get('build_candidates', [])),
    ):
        lines += ['', heading]
        shown = 0
        for build in builds:
            if build['archive_id'] in seen_archives:
                continue
            seen_archives.add(build['archive_id'])
            shown += 1
            lines += [f"  Generated archive {build['archive_id']}",
                      f"  Input {build['input_id']}",
                      f"  Recorded: {build['recorded_at']}; {build['output_count']} lineups"]
        if not shown:
            lines.append('  None recorded.')
    lines.append('Other candidates do not qualify the current build stage or enable analysis.')
    lines.append('Exact original/submitted build: not established. Multiple generated builds remain separate.')
    outcome = data.get('actual_score_evidence') or {}
    if outcome:
        lines.append(f"Actual scores: {outcome['known_scores']} / {outcome['eligible_player_roles']} player-role scores known; {outcome['unknown_scores']} unknown. Zero and negative scores count as known.")
    lines += ['', 'Analysis prerequisites (saved evidence; revalidation required):']
    for label, key in (('Ordinary historical results','results'),('Portfolio Risk / RL-06','portfolio_risk'),
                       ('Hindsight / RL-07','hindsight'),('Compute input/output comparison','compute_input_output')):
        lines.append(f"  {label}: {'evidence available' if capabilities(data)[key] else 'unavailable — evidence incomplete'}")
    lines += ['Portfolio Risk revalidates one explicitly selected archive. RL-07 is not implemented. Compute phase timings and submitted-build identity are not established by these archives.']
    for field in ('conflicts','blockers','limitations'):
        codes = data.get(field, [])
        if codes:
            lines += ['',field.title()+':',*[reason_text(c) for c in codes]]
    if data.get('source_issues'):
        lines += ['', 'Source scan issues: ' + ', '.join(k.replace('_',' ') for k in data['source_issues'])]
    return '\n'.join(lines)
