"""Historical construction metadata from a saved, freshly verified association."""
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path
import re
import sqlite3

import analysis_imports as ai


def saved_contests(db_path):
    path = Path(db_path)
    if not path.is_file():
        return []
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        conn.execute('PRAGMA query_only=ON')
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'analysis_sources', 'analysis_salary_pairs'} <= tables:
            return []
        return [dict(result_hash=h, name=n) for h, n in conn.execute(
            'SELECT r.hash,r.original_name FROM analysis_salary_pairs p '
            'JOIN analysis_sources r ON r.hash=p.result_hash ORDER BY r.original_name')]


def analyze_saved_contest(db_path, result_hash, cancelled=lambda: False, progress=lambda text: None):
    from opponent_analysis import analyze_standings
    from data_paths import history_source_paths
    path = Path(db_path or history_source_paths()[0]).resolve()
    ai._check(cancelled)
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as conn:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        sources = {s['hash']: s for s in ai._sources(conn)}
        pair = conn.execute('SELECT salary_hash FROM analysis_salary_pairs WHERE result_hash=?', (result_hash,)).fetchone()
        if not pair or result_hash not in sources or pair[0] not in sources:
            raise ValueError('The saved salary association is missing; review it in Results & Learning.')
        result, salary = sources[result_hash], sources[pair[0]]
    for source in (result, salary):
        ai._verify(source, cancelled)
    qualification = ai.qualify_pair(result, salary)
    if not qualification['compatible']:
        raise ValueError('The saved salary association no longer qualifies: ' + qualification['reason'])
    # Parse original historical salaries again, never use live player metadata.
    manifest = ai._salary_manifest(salary['snapshot'], cancelled)
    report = analyze_standings(result['snapshot'], manifest['format'], cancelled, progress,
                               salary_players=manifest['players'])
    for source in (result, salary):
        ai._verify(source, cancelled)
    report['source_name'] = result['name']
    report['salary_evidence'] = dict(name=salary['name'], sha256=salary['hash'],
                                   association='saved, freshly verified', dates=manifest['dates'])
    return report


def construction_summary(lineups, salary_players, kind='showdown', checkpoint=lambda: None):
    """Each metric has its own denominator; ambiguous identities stay unknown."""
    lookup = defaultdict(list)
    for player in salary_players or []:
        for role in player['role'].split('/'):
            lookup[(ai._name(player['name']), role)].append(player)
    categories = defaultdict(Counter)
    denominators = Counter()
    unknown = 0
    salary_values = []
    for index, (lineup, copies) in enumerate(lineups):
        if index % 500 == 0:
            checkpoint()
        metadata = []
        for label, role in lineup:
            matches = lookup[(ai._name(label), role)]
            explicit = re.search(r'\((\d+)\)\s*$', label)
            if explicit:
                matches = [p for p in matches if p['id'] == explicit[1]]
            if len(matches) != 1:
                metadata = []
                break
            metadata.append(matches[0])
        if not metadata:
            unknown += copies
            continue
        teams = Counter(p['team'] for p in metadata)
        if kind == 'showdown' and len(teams) != 2:
            unknown += copies
            continue
        def add(category, label):
            categories[category][label] += copies
            denominators[category] += copies
        add('Team split', '–'.join(map(str, sorted(teams.values(), reverse=True))))
        captain = metadata[0]
        if kind == 'showdown':
            add('Captain team split', f"{teams[captain['team']]}–{6-teams[captain['team']]} (Captain team first)")
            add('Captain position', captain['position'])
            same_qb = any(p['position'] == 'QB' and p['team'] == captain['team'] for p in metadata[1:])
            add('Captain with same-team FLEX QB', 'yes' if same_qb else 'no')
        else:
            qbs = [p for p in metadata if p['position']=='QB']
            if len(qbs)==1:
                qb=qbs[0]
                add('QB same-team WR/TE', str(sum(p['team']==qb['team'] and p['position'] in ('WR','TE') for p in metadata)))
                add('QB opposing RB/WR/TE', str(sum(p['team']==qb['opponent'] and p['position'] in ('RB','WR','TE') for p in metadata)))
        add('Quarterbacks', str(sum(p['position'] == 'QB' for p in metadata)))
        add('Kicker/defense slots', str(sum(p['position'] in ('K', 'DST') for p in metadata)))
        add('Kickers', str(sum(p['position'] == 'K' for p in metadata)))
        add('Defenses', str(sum(p['position'] == 'DST' for p in metadata)))
        salary = sum(p['salary'] for p in metadata)
        salary_values.append((salary, copies))
        unused = 50000 - salary
        add('Salary left', 'over cap' if unused < 0 else '$0–200' if unused <= 200 else
            '$201–700' if unused <= 700 else '$701–1,200' if unused <= 1200 else 'over $1,200')
    known = sum(n for _, n in salary_values)
    return dict(known_entries=known, unknown_entries=unknown,
                mean_salary=sum(s*n for s,n in salary_values)/known if known else None,
                tables={category: [dict(label=label, entries=n, denominator=denominators[category],
                                        pct=100*n/denominators[category])
                                   for label,n in sorted(counts.items())]
                        for category, counts in sorted(categories.items())})


def apply_user_context(report, username):
    from opponent_analysis import username_key
    for portfolio in report['portfolios']:
        if username and portfolio['username_key']==username_key(username) and portfolio['captain_pool']==1:
            portfolio['build_context']=dict(basis='user-instructed assumption, not a build receipt',
                captain_locked=True, typical_sim_workflow=False,
                complete_roster_coverage=portfolio['readable_rosters']==portfolio['entries'],
                note='Single Captain among readable entries; treat as Captain-locked without the typical SIM workflow. Do not attribute Captain concentration to normal SIM selection.')
    return report
