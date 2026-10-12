"""Report-only, revision-bound source-row salary/construction aggregates.

Rows are deliberately not deduplicated: this is not portfolio or full-field
validation. Reconciliation owns cancellation, source verification and commit.
"""
from collections import Counter, OrderedDict
import re


VERSION = 1


class FieldRows:
    def __init__(self, salary):
        from opponent_construction import salary_lookup
        self.lookup = salary_lookup(salary['players'])
        self.kind = salary['format']
        self.counts = Counter()
        self.features = Counter()
        self.cache = OrderedDict()

    def _resolve(self, text):
        from analysis_imports import _name
        from opponent_analysis import _MARKERS, _roster
        if not text.strip():
            return 'blank_roster_rows', None
        if not _roster(text, self.kind):
            return 'malformed_roster_rows', None
        markers = list(_MARKERS.finditer(text))
        players = []
        for i, marker in enumerate(markers):
            role = marker.group(1).upper().replace('CAPTAIN', 'CPT').replace('D/ST', 'DST')
            label = text[marker.end():markers[i+1].start() if i+1 < len(markers) else len(text)].strip()
            matches = self.lookup.get((_name(label), role), ())
            explicit = re.search(r'\((\d+)\)\s*$', label)
            if explicit:
                matches = [p for p in matches if p['id'] == explicit[1]]
            if len(matches) != 1:
                return 'unknown_identity_rows', None
            players.append((role, matches[0]))
        teams = Counter(p['team'] for _, p in players)
        if self.kind == 'showdown' and len(teams) != 2:
            return 'invalid_team_rows', None
        salary = sum(p['salary'] for _, p in players)
        if salary > 50000:
            return 'over_cap_rows', None
        features = ['Team split: ' + '–'.join(map(str, sorted(teams.values(), reverse=True))),
                    'Kicker/defense slots: ' + str(sum(p['position'] in ('K', 'DST') for _, p in players))]
        if self.kind == 'showdown':
            captain = next(p for role, p in players if role == 'CPT')
            features += ['Captain position: ' + captain['position'],
                         f"Captain team split: {teams[captain['team']]}–{6-teams[captain['team']]}" ]
        else:
            qb = next(p for role, p in players if role == 'QB')
            features += ['QB same-team WR/TE: ' + str(sum(p['team']==qb['team'] and p['position'] in ('WR','TE') for _, p in players)),
                         'FLEX position: ' + next(p['position'] for role,p in players if role=='FLEX')]
        return 'supported_rows', (salary, features)

    def add(self, text):
        self.counts['entry_rows'] += 1
        if text in self.cache:
            result = self.cache.pop(text)
        else:
            result = self._resolve(text)
        # Bound both item count and retained text size. Large labels are not cached.
        if len(text) <= 4096:
            self.cache[text] = result
            if len(self.cache) > 4096:
                self.cache.popitem(last=False)
        status, resolved = result
        self.counts[status] += 1
        if resolved:
            salary, features = resolved
            self.counts[self.kind + '_supported_rows'] += 1
            self.counts['salary_sum'] += salary
            self.features.update(features)

    def payload(self, scan_complete):
        return dict(version=VERSION, scan_complete=scan_complete,
                    counts=dict(self.counts) if scan_complete else {},
                    features=dict(self.features) if scan_complete else {},
                    basis='source rows including duplicate EntryIds; exact salary roles/IDs; no export fallback; not unique entries or complete-field evidence')


def summarize(saved, pairings):
    """Only current verified pairings may expose cached source-row metrics."""
    from historical_coverage import aggregate
    pairs = {p['hash']: p for p in pairings['results']}
    counts, features = Counter(), Counter()
    sources, excluded = set(), 0
    for contest in saved['contests']:
        data = contest.data
        salary = data.get('salary_evidence') or {}
        result = data.get('results_evidence') or {}
        source = result.get('source_hash')
        pair = pairs.get(source, {})
        candidates = [c for c in pair.get('candidates', ()) if c['compatible']]
        current = (pair.get('salary_hash') if pair.get('status') == 'PAIRED' else
                   candidates[0]['hash'] if not pair.get('salary_hash') and len(candidates)==1 and candidates[0]['automatic'] else None)
        payload = result.get('field_row_summary') or {}
        if (not salary.get('qualified') or current != salary.get('revision_hash') or
                not payload.get('scan_complete') or payload.get('version') != VERSION):
            excluded += 1
            continue
        if source in sources:
            continue
        sources.add(source)
        counts.update(payload['counts'])
        features.update(payload['features'])
    n = counts['supported_rows']
    methods = Counter(c.data.get('method_version',0) for c in saved['contests'])
    return dict(coverage=aggregate(saved['contests']), methods=dict(methods), counts=dict(counts), features=dict(features),
                sources=len(sources), excluded=excluded,
                mean_salary=counts['salary_sum']/n if n else None,
                metadata_coverage_pct=100*n/counts['entry_rows'] if counts['entry_rows'] else None)


def report_lines(saved, summary):
    coverage = summary['coverage']
    rows = coverage['roster_row_coverage']
    counts = summary['counts']
    lines = ['', 'Historical database evidence — report-only',
             f"- Cached reconciliation: {saved['updated_at'] or 'not available'}; {len(saved['contests'])} contest records; pending imports {saved['pending_imports']}; invalid cached records {saved['invalid_rows']}.",
             '- Cached states: ' + ', '.join(f'{k}: {v}' for k,v in coverage['states'].items()),
             '- Cached reconciliation methods: ' + (', '.join(f'v{k}: {v}' for k,v in sorted(summary['methods'].items())) or 'none'),
             f"- Cached score-supported source rows: {rows.get('score_supported_roster_rows',0):,}; usable sources {rows.get('usable_sources',0)}. Cached counts need reconciliation after source changes.",
             f"- Current results/salary-bound summaries: {summary['sources']}; excluded or awaiting reconciliation: {summary['excluded']}.",
             '- Source-row counts include duplicate EntryIds. Complete-field, unique-entry and submission coverage are not established by these counts.',
             '- Reconcile Historical Coverage to refresh these aggregates; older cached records do not supply salary/construction metrics.']
    if summary['sources']:
        n = counts.get('supported_rows',0)
        lines.append(f"- Salary/construction-supported source rows: {n:,}/{counts.get('entry_rows',0):,}.")
        if summary['mean_salary'] is not None:
            lines.append(f"- Average source-row salary used: ${summary['mean_salary']:,.0f} ({n:,} supported rows; exact contest salary revision, Captain salary retained).")
        lines.append('- Excluded rows: ' + ', '.join(f"{label}: {counts.get(key,0):,}" for key,label in
            [('blank_roster_rows','blank'),('malformed_roster_rows','malformed'),('unknown_identity_rows','unknown/ambiguous identity'),('invalid_team_rows','invalid team split'),('over_cap_rows','over $50,000')]))
        for key,value in sorted(summary['features'].items()):
            denominator = (counts.get('showdown_supported_rows',0) if key.startswith('Captain ') else
                           counts.get('classic_supported_rows',0) if key.startswith(('QB ', 'FLEX ')) else n)
            if denominator:
                lines.append(f'- {key}: {value:,}/{denominator:,} ({100*value/denominator:.1f}%).')
    return lines
