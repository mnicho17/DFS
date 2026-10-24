"""Report-only, revision-bound source-row salary/construction aggregates.

Rows are deliberately not deduplicated: this is not portfolio or full-field
validation. Reconciliation owns cancellation, source verification and commit.
"""
from collections import Counter, OrderedDict
import math
import re


VERSION = 3


def positive_integer(value):
    try:
        number = float(str(value).replace(',',''))
        return int(number) if math.isfinite(number) and 0 < number <= 2**53-1 and number.is_integer() else None
    except (TypeError,ValueError):
        return None


class RankRows:
    """Bounded rank-cutoff receipt; never establishes field completeness."""
    def __init__(self):
        self.rows = 0
        self.sizes = set()
        self.invalid = False

    def add(self, row):
        if not (row.get('EntryId') or row.get('EntryName')):
            return
        self.rows += 1
        for key in ('FieldSize','ContestEntries','Entries'):
            value = row.get(key)
            if value is not None and str(value).strip():
                size = positive_integer(value)
                if size is None:
                    self.invalid = True
                elif len(self.sizes) < 2:
                    self.sizes.add(size)

    def receipt(self):
        if self.invalid or len(self.sizes) > 1:
            return None, 'conflicting/invalid supplied field size; percentile cutoffs unavailable'
        if self.sizes:
            return next(iter(self.sizes)), 'supplied field size; completeness unverified'
        return self.rows or None, 'observed source-row rank cutoff; completeness unverified'


class FieldRows:
    def __init__(self, salary, *, usernames=(), rank_size=None, rank_basis='unavailable'):
        from opponent_construction import salary_lookup
        self.lookup = salary_lookup(salary['players'])
        self.kind = salary['format']
        self.counts = Counter()
        self.features = Counter()
        self.cache = OrderedDict()
        self.users = set(usernames) if len(usernames) <= 256 else set()
        self.user_limit = len(usernames) > 256
        self.rank_size = rank_size
        self.rank_basis = rank_basis
        self.cohorts = {}

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
                    'Kicker/defense slots: ' + str(sum(p['position'] in ('K', 'DST') for _, p in players)),
                    'Kickers: ' + str(sum(p['position']=='K' for _, p in players)),
                    'Defenses: ' + str(sum(p['position']=='DST' for _, p in players))]
        captain_defense = None
        if self.kind == 'showdown':
            captain = next(p for role, p in players if role == 'CPT')
            opposing = any(role!='CPT' and p['position']=='DST' and p['team']!=captain['team'] for role,p in players)
            features += ['Captain position: ' + captain['position'],
                         f"Captain team split: {teams[captain['team']]}–{6-teams[captain['team']]}",
                         'Captain with opposing FLEX defense: ' + ('yes' if opposing else 'no')]
            captain_defense = captain['position']+(' with' if opposing else ' without')
        else:
            qb = next(p for role, p in players if role == 'QB')
            features += ['QB same-team WR/TE: ' + str(sum(p['team']==qb['team'] and p['position'] in ('WR','TE') for _, p in players)),
                         'FLEX position: ' + next(p['position'] for role,p in players if role=='FLEX')]
        return 'supported_rows', (salary, features, captain_defense)

    def add(self, text, *, rank=None, username=''):
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
        targets = ['field']
        if rank is not None:
            if rank == 1:
                targets.append('rank1')
            if self.rank_size and rank <= self.rank_size:
                if rank <= (self.rank_size+99)//100: targets.append('top1')
                if rank <= (self.rank_size+19)//20: targets.append('top5')
        if username in self.users:
            targets.append('user:'+username)
        for target in targets:
            group = self.cohorts.setdefault(target, dict(counts=Counter(),features=Counter()))
            group['counts']['entry_rows'] += 1
            group['counts'][status] += 1
            group['counts']['ranked_rows' if rank is not None else 'missing_rank_rows'] += 1
            if resolved:
                group['counts']['salary_sum'] += resolved[0]
                group['features'].update(resolved[1])
                if resolved[2]:
                    group.setdefault('captain_defense',Counter())[resolved[2]] += 1
        if resolved:
            salary, features, _ = resolved
            self.counts[self.kind + '_supported_rows'] += 1
            self.counts['salary_sum'] += salary
            self.features.update(features)

    def payload(self, scan_complete):
        return dict(version=VERSION, scan_complete=scan_complete,
                    counts=dict(self.counts) if scan_complete else {},
                    features=dict(self.features) if scan_complete else {},
                    rank_size=self.rank_size, rank_basis=self.rank_basis,
                    username_limit_exceeded=self.user_limit,
                    cohorts={k:dict(counts=dict(v['counts']),features=dict(v['features']),captain_defense=dict(v.get('captain_defense',{}))) for k,v in self.cohorts.items()} if scan_complete else {},
                    basis='source rows including duplicate EntryIds; exact salary roles/IDs; no export fallback; not unique entries or complete-field evidence')


def summarize(saved, pairings, username='', *, observations=None):
    """Only current verified pairings may expose cached source-row metrics."""
    from historical_coverage import aggregate
    pairs = {p['hash']: p for p in pairings['results']}
    counts, features = Counter(), Counter()
    sources, excluded = set(), 0
    scopes = {}
    from opponent_analysis import username_key
    from learning_db import _infer_field_preset
    selected = username_key(username)
    from historical_contest_comparison import contest_record
    observations = observations or dict(complete=True,imports={})
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
                not payload.get('scan_complete') or payload.get('version') not in (1, 2, VERSION)):
            excluded += 1
            continue
        if source in sources:
            continue
        sources.add(source)
        counts.update(payload['counts'])
        features.update(payload['features'])
        kind = salary.get('format')
        if kind not in ('showdown','classic'):
            continue
        preset = payload.get('entry_preset') or _infer_field_preset(pair.get('name',''))
        scope = scopes.setdefault((kind,preset),dict(format=kind,preset=preset,sources=0,counts=Counter(),features=Counter(),cohorts={},cohort_sources=Counter(),rank_bases=Counter(),old_sources=0,user_limits=0,contests=[]))
        record=contest_record(data,pair,payload,username,observations)
        from historical_concentration import attach
        attach(record,data,observations.get('concentration',{}))
        from controlled_construction import attach as attach_controls
        attach_controls(record,data,observations.get('controls',{}))
        scope['contests'].append(record)
        scope['sources'] += 1
        scope['counts'].update(payload['counts'])
        scope['features'].update(payload['features'])
        scope['old_sources'] += payload.get('version') == 1
        scope['user_limits'] += bool(payload.get('username_limit_exceeded'))
        scope['rank_bases'][payload.get('rank_basis','unavailable')] += 1
        for key,group in payload.get('cohorts',{}).items():
            if key.startswith('user:'):
                if not selected or key != 'user:'+selected:
                    continue
                key = 'yours'
            dest = scope['cohorts'].setdefault(key,dict(counts=Counter(),features=Counter()))
            scope['cohort_sources'][key] += 1
            dest['counts'].update(group['counts'])
            dest['features'].update(group['features'])
    n = counts['supported_rows']
    methods = Counter(c.data.get('method_version',0) for c in saved['contests'])
    return dict(coverage=aggregate(saved['contests']), scopes=list(scopes.values()), methods=dict(methods), counts=dict(counts), features=dict(features),
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
        for scope in sorted(summary['scopes'],key=lambda s:(s['format'],s['preset'])):
            lines += ['',f"{scope['format'].title()} / {scope['preset']} — {scope['sources']} sources (preset inferred from recorded names)"]
            from historical_concentration import report_lines as concentration_report_lines
            lines.extend(concentration_report_lines(scope))
            from controlled_construction import report_lines as controlled_report_lines
            lines.extend(controlled_report_lines(scope))
            supported = scope['counts']['supported_rows']
            if supported:
                lines.append(f"- Supported source rows: {supported:,}/{scope['counts']['entry_rows']:,}; average salary ${scope['counts']['salary_sum']/supported:,.0f}.")
            for key,value in sorted(scope['features'].items()):
                if supported:
                    lines.append(f'- {key}: {value:,}/{supported:,} ({100*value/supported:.1f}%).')
            if scope['old_sources']:
                lines.append(f"- {scope['old_sources']} older summaries lack cohort comparisons; reconcile once to populate them. Existing salary/construction metrics remain available.")
            if scope['user_limits']:
                lines.append('- Username cohort budget exceeded for some sources; no arbitrary username subset was retained.')
            if not scope['cohorts']:
                continue
            lines.append('- Rank-cutoff bases: ' + ', '.join(f'{k}: {v} sources' for k,v in sorted(scope['rank_bases'].items())))
            lines.append('- Top 1%/5% columns are rank cutoffs, include ties and overlap. Observed-row cutoffs do not establish complete-field percentiles. Rank-1 rows do not establish profitability.')
            cohorts = scope['cohorts']
            for key,label in [('field','Field'),('top5','Rank cutoff 5%'),('top1','Rank cutoff 1%'),('rank1','Rank 1'),('yours','Your submitted rows')]:
                group = cohorts.get(key,dict(counts=Counter()))
                c = group['counts']
                lines.append(f"- {label}: {c.get('supported_rows',0):,}/{c.get('entry_rows',0):,} supported; {scope['cohort_sources'][key]} contributing sources; missing ranks {c.get('missing_rank_rows',0):,}.")
            keys = sorted({k for group in cohorts.values() for k in group['features']})
            lines.append('| Construction | Field | Rank cutoff 5% | Rank cutoff 1% | Rank 1 | Your submitted rows |')
            lines.append('|---|---:|---:|---:|---:|---:|')
            for feature in keys:
                cells = []
                for cohort in ('field','top5','top1','rank1','yours'):
                    group = cohorts.get(cohort,{})
                    denominator = group.get('counts',{}).get('supported_rows',0)
                    value = group.get('features',{}).get(feature,0)
                    cells.append(f'{100*value/denominator:.1f}% ({value:,}/{denominator:,})' if denominator else 'unavailable')
                lines.append('| '+feature.replace('|','/')+' | '+' | '.join(cells)+' |')
            from historical_contest_comparison import report_lines as contest_report_lines
            lines.extend(contest_report_lines(scope))
        lines.append('- These are source-row frequencies, not independent games or evidence of a construction edge. Cohort contributing-source counts can differ when ranks or personal observations are missing; old summaries remain outside comparison denominators. No generation behavior or model weights are changed.')
    return lines
