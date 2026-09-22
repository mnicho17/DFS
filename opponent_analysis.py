"""Read-only, single-contest NFL standings analysis. No DFS storage imports."""
from collections import Counter, defaultdict
import csv
import hashlib
import itertools
import json
import math
from pathlib import Path
import re
import statistics


class AnalysisCancelled(Exception):
    pass


def username_key(value):
    return re.sub(r"\s+\(\d+\s*/\s*\d+\)\s*$", "", value.strip()).casefold()


def _display_username(value):
    return re.sub(r"\s+\(\d+\s*/\s*\d+\)\s*$", "", value.strip())


def _number(value, integer=False):
    try:
        result = float(str(value).replace(',', '').strip())
        if not math.isfinite(result) or (integer and (result < 1 or not result.is_integer())):
            return None
        return int(result) if integer else result
    except (ValueError, TypeError):
        return None


_MARKERS = re.compile(r"(?:^|\s)(CAPTAIN|CPT|FLEX|QB|RB|WR|TE|DST|D/ST)(?=\s)", re.I)


def _roster(text, contest_format):
    """Validate structure, not salary/eligibility; identity retains Captain role."""
    markers = list(_MARKERS.finditer(text))
    if not markers or text[:markers[0].start()].strip():
        return None
    roles, players, names = [], [], {}
    for index, match in enumerate(markers):
        role = match.group(1).upper().replace('CAPTAIN', 'CPT').replace('D/ST', 'DST')
        name = text[match.end():markers[index+1].start() if index+1 < len(markers) else len(text)].strip()
        if not name or name.casefold() in ('locked', 'hidden', '--', 'n/a'):
            return None
        # DK Captain and FLEX can have different IDs for the same athlete.
        # Standings names, not slot-specific IDs, identify shared athletes.
        identity_name = re.sub(r'\s*\(\d+\)\s*$', '', name).strip()
        if not identity_name or identity_name.isdigit():
            return None
        key = 'name:' + ' '.join(identity_name.casefold().split())
        names[key] = name
        roles.append(role)
        players.append(key)
    expected = Counter(CPT=1, FLEX=5) if contest_format == 'showdown' else Counter(QB=1, RB=2, WR=3, TE=1, FLEX=1, DST=1)
    if Counter(roles) != expected or len(set(players)) != len(players):
        return None
    signature = tuple(sorted(('CPT:' if role == 'CPT' else '') + player for role, player in zip(roles, players)))
    captain = players[roles.index('CPT')] if 'CPT' in roles else None
    return signature, tuple(sorted(players)), captain, names


def _band(count):
    for low, high, label in ((1, 1, '1'), (2, 5, '2–5'), (6, 20, '6–20'), (21, 150, '21–150')):
        if low <= count <= high:
            return label
    return '151+'


def _pct(numerator, denominator):
    return 100.0 * numerator / denominator if denominator else None


def _overlap(counts, size):
    return sum(n*(n-1)//2 for n in counts.values()) / (size*(size-1)//2) if size > 1 else None


def analyze_standings(path, contest_format='showdown', cancelled=lambda: False, progress=lambda text: None):
    """Analyze one CSV without changing history, settings, or source files.

    Entry IDs deduplicate rows; conflicting copies are excluded entirely.
    Field-wide metrics always describe supplied rows, never assumed completeness.
    """
    if contest_format not in ('showdown', 'classic'):
        raise ValueError('Choose NFL Showdown or NFL Classic.')

    def check():
        if cancelled():
            raise AnalysisCancelled()

    check()
    path = Path(path)
    before = path.stat()
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        while chunk := handle.read(1024 * 1024):
            check()
            digest.update(chunk)
    entries, conflicts, contest_ids, contest_names, field_sizes = {}, set(), set(), set(), set()
    names = {}
    audit = Counter({key: 0 for key in ('entry_rows', 'side_table_or_empty_rows', 'missing_id_or_username_rows',
        'identical_duplicate_rows', 'conflicting_duplicate_rows', 'conflicting_entry_ids_excluded', 'invalid_field_size_rows')})
    with path.open(newline='', encoding='utf-8-sig') as handle:
        sample = handle.read(8192)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=',;\t|')
        except csv.Error:
            # Some DK rows omit trailing side-table cells. Their field counts
            # differ, but the header still identifies the delimiter reliably.
            try:
                dialect = csv.Sniffer().sniff(sample.splitlines()[0], delimiters=',;\t|')
            except (csv.Error, IndexError):
                dialect = csv.excel
        reader = csv.DictReader(handle, dialect=dialect)
        headers = {}
        for header in reader.fieldnames or []:
            key = re.sub(r'[ _-]', '', str(header)).casefold()
            if key and key in headers:
                raise ValueError('Duplicate standings column: ' + str(header))
            headers[key] = header
        def column(*keys):
            return next((headers[key] for key in keys if key in headers), None)
        entry_col = column('entryid')
        user_col = column('entryname')
        lineup_col = column('lineup', 'roster')
        if not all((entry_col, user_col, lineup_col)):
            raise ValueError('Standings need EntryId, EntryName and Lineup columns; select one contest standings CSV.')
        rank_col = column('rank', 'place', 'finish')
        points_col = column('points', 'actualpoints', 'score')
        # FPTS alongside Player belongs to the independent player side table.
        if not points_col and 'player' not in headers:
            points_col = column('fpts', 'fantasypoints')
        size_col = column('fieldsize', 'contestentries', 'entries')
        contest_col = column('contestid')
        contest_name_col = column('contestname', 'contest')
        for row_index, row in enumerate(reader, 1):
            if row_index % 500 == 0:
                check()
                progress(f'Reading {row_index:,} standings rows…')
            def value(col):
                return str(row.get(col) or '').strip() if col else ''
            entry_id, user, lineup = value(entry_col), value(user_col), value(lineup_col)
            rank, points = _number(value(rank_col), True), _number(value(points_col))
            if not any((entry_id, user, lineup, value(rank_col), value(points_col))):
                audit['side_table_or_empty_rows'] += 1
                continue
            audit['entry_rows'] += 1
            if not entry_id or not username_key(user):
                audit['missing_id_or_username_rows'] += 1
                continue
            if value(contest_col):
                contest_ids.add(value(contest_col))
            if value(contest_name_col):
                contest_names.add(value(contest_name_col))
            if len(contest_ids) > 1 or len(contest_names) > 1:
                raise ValueError('Multiple contests detected. Analyze each contest separately.')
            if value(size_col):
                size = _number(value(size_col), True)
                if size is None:
                    audit['invalid_field_size_rows'] += 1
                else:
                    field_sizes.add(size)
            parsed = _roster(lineup, contest_format)
            if parsed:
                signature, players, captain, labels = parsed
                names.update(labels)
            else:
                signature, players, captain = None, (), None
            # Raw score/rank/lineup values are kept in the duplicate receipt so
            # contradictory malformed copies cannot masquerade as duplicates.
            receipt = (username_key(user), value(rank_col), value(points_col), lineup)
            if entry_id in entries:
                if entries[entry_id][0] == receipt:
                    audit['identical_duplicate_rows'] += 1
                else:
                    conflicts.add(entry_id)
                    audit['conflicting_duplicate_rows'] += 1
                continue
            entries[entry_id] = (receipt, _display_username(user), rank, points, signature, players, captain)
    check()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('The standings changed while reading. Retry with a stable copy.')
    audit['conflicting_entry_ids_excluded'] = len(conflicts)
    users = defaultdict(list)
    field_signatures, field_players, field_captains = Counter(), Counter(), Counter()
    for index, (entry_id, entry) in enumerate(entries.items()):
        if index % 500 == 0:
            check()
        if entry_id in conflicts:
            continue
        users[entry[0][0]].append(entry)
        if entry[4]:
            field_signatures[entry[4]] += 1
            field_players.update(entry[5])
            if entry[6]:
                field_captains[entry[6]] += 1
    accepted = sum(map(len, users.values()))
    if not accepted:
        raise ValueError('No identifiable entries remain after validation.')
    field_valid = sum(field_signatures.values())
    if not field_valid:
        raise ValueError('No readable rosters for the selected NFL format. Check the format and that lineups are visible.')
    audit.update(accepted_entries=accepted, readable_rosters=field_valid, unreadable_rosters=accepted-field_valid)
    supplied_size = next(iter(field_sizes)) if len(field_sizes) == 1 and not audit['invalid_field_size_rows'] else None
    coverage = ('Supplied field size matches accepted entry count; roster coverage is reported separately.'
                if supplied_size == accepted else 'Partial or inconsistent field size.' if field_sizes else
                'Field size is absent; completeness is unverified. Metrics describe supplied entries only.')
    if len(field_sizes) > 1:
        coverage = 'Conflicting field sizes; completeness is unverified.'
    def exposures(total, captains, denominator):
        result = []
        for key, count in total.most_common():
            cpt = captains[key] if contest_format == 'showdown' else None
            result.append(dict(player=names[key], player_key=key, entries=count, denominator=denominator,
                               total_pct=_pct(count, denominator), captain_entries=cpt,
                               captain_pct=_pct(cpt, denominator) if cpt is not None else None,
                               flex_entries=count-cpt if cpt is not None else None,
                               flex_pct=_pct(count-cpt, denominator) if cpt is not None else None))
        return result
    portfolios = []
    for index, (key, rows) in enumerate(sorted(users.items())):
        check()
        if index % 50 == 0:
            progress(f'Analyzing entrant {index+1:,} / {len(users):,}…')
        valid = [r for r in rows if r[4]]
        counts, role_counts, captains, pairs, signatures = Counter(), Counter(), Counter(), Counter(), Counter()
        for row_index, row in enumerate(valid):
            if row_index % 500 == 0:
                check()
            counts.update(row[5])
            role_counts.update(row[4])
            pairs.update(itertools.combinations(row[5], 2))
            signatures[row[4]] += 1
            if row[6]:
                captains[row[6]] += 1
        points = [r[3] for r in rows if r[3] is not None]
        ranks = [r[2] for r in rows if r[2] is not None]
        known_count = len(valid)
        portfolios.append(dict(username=rows[0][1], username_key=key, entries=len(rows), entry_band=_band(len(rows)),
            readable_rosters=known_count, roster_coverage_pct=_pct(known_count, len(rows)),
            unique_lineups=len(signatures) if known_count else None,
            repeated_entries=known_count-len(signatures) if known_count else None,
            repeated_pct=_pct(known_count-len(signatures), known_count),
            player_pool=len(counts) if known_count else None, captain_pool=len(captains) if known_count and contest_format == 'showdown' else None,
            mean_shared_players=_overlap(counts, known_count), mean_shared_role_slots=_overlap(role_counts, known_count),
            entries_shared_with_other_users=sum(n for sig, n in signatures.items() if field_signatures[sig] > n) if known_count else None,
            mean_field_copies=sum(n * field_signatures[sig] for sig, n in signatures.items()) / known_count if known_count else None,
            scored_entries=len(points), mean_points=statistics.mean(points) if points else None,
            median_points=statistics.median(points) if points else None, best_points=max(points) if points else None,
            ranked_entries=len(ranks), best_rank=min(ranks) if ranks else None,
            players=exposures(counts, captains, known_count),
            pairs=[dict(players=[names[p] for p in pair], player_keys=list(pair), entries=n,
                        denominator=known_count, pct=_pct(n, known_count)) for pair, n in pairs.most_common()],
            lineups=[dict(players=[names[p] for p in sig if not p.startswith('CPT:')],
                          captain=next((names[p[4:]] for p in sig if p.startswith('CPT:')), None),
                          entries=n, field_copies=field_signatures[sig]) for sig, n in signatures.most_common()]))
    def cohort_summary(members):
        cohort = dict(entrants=len(members), entries=sum(p['entries'] for p in members))
        for metric in ('repeated_pct', 'mean_shared_players', 'player_pool', 'mean_points'):
            values = [p[metric] for p in members if p[metric] is not None]
            cohort['median_'+metric] = statistics.median(values) if values else None
            cohort[metric+'_entrants'] = len(values)
        return cohort
    cohorts = [dict(entry_band=band, **cohort_summary(members)) for band in ('1', '2–5', '6–20', '21–150', '151+')
               if (members := [p for p in portfolios if p['entry_band'] == band])]
    exact_groups = defaultdict(list)
    for p in portfolios:
        exact_groups[p['entries']].append(p)
    exact_cohorts = [dict(entry_count=count, **cohort_summary(members)) for count, members in sorted(exact_groups.items())]
    check()
    return dict(schema_version=1, format=contest_format, source_name=path.name, source_sha256=digest.hexdigest(),
                coverage=coverage, supplied_field_size=supplied_size, observed_field_sizes=sorted(field_sizes),
                audit=dict(audit), entrants=len(portfolios), portfolios=portfolios, cohorts=cohorts, exact_count_cohorts=exact_cohorts,
                units=dict(points='DraftKings points', exposures='percent_of_readable_entries', overlap='shared_players_per_entry_pair'),
                field_players=exposures(field_players, field_captains, field_valid),
                limitations=[
                    'One supplied contest, not a strategy recommendation. Repeated entries share game outcomes.',
                    'Username matching ignores case and trailing entry counters only; punctuation stays distinct.',
                    'Roster identities retain Captain; Classic position swaps of the same athletes are equivalent.',
                    'Player identity uses exact case-insensitive names, ignoring appended numeric DK IDs (Captain/FLEX IDs can differ). No fuzzy merging; namesakes cannot be disambiguated. ID-only rosters are unreadable.',
                    'Readable roster means correct slot structure and distinct athlete names; salary/eligibility are not verified.',
                    'Exposure denominator is readable rosters; missing rosters are unknown, not zero exposure.',
                    'Overlap averages shared athletes across every pair of entries, including repeated lineups; one entry has no pair.',
                    'Field copies include the entrant’s own copies and cover supplied readable entries only.',
                    'Cohorts use observed entry counts; medians weight each entrant equally, including losing entrants.',
                    'No payouts, profitability, injury causes, backup roles or optimal lineups are inferred.'])


def share_payload(result, username='', include_lineups=False):
    """Default share scope: selected entrant plus field/cohort summaries."""
    payload = {k: v for k, v in result.items() if k != 'portfolios'}
    selected = username_key(username) if username else None
    excluded = {'lineups'} if selected and not include_lineups else set() if selected else {'lineups', 'players', 'pairs'}
    payload['portfolios'] = [{k: v for k, v in p.items() if k not in excluded}
                             for p in result['portfolios'] if selected is None or p['username_key'] == selected]
    payload['share_scope'] = 'selected username with exposure/pair detail' if selected else 'all usernames, aggregate summaries only'
    payload['detailed_lineups_included'] = bool(include_lineups and selected)
    return payload


def render_report(result, username='', include_lineups=False):
    payload = share_payload(result, username, include_lineups)
    def fmt(value, digits=1):
        return 'unknown' if value is None else f'{value:,.{digits}f}'
    lines = ['DFS Opponent Portfolios — descriptive analysis',
             f"Source: {result['source_name']}", f"SHA-256: {result['source_sha256']}",
             f"Format: NFL {result['format'].title()}; {result['entrants']:,} entrants; {result['audit']['accepted_entries']:,} accepted entries.",
             result['coverage'], 'Data quality: ' + json.dumps(result['audit'], sort_keys=True),
             'Scores are DK points; exposures are percent of readable entries. No monetary metrics.',
             '', 'Entry-count cohorts (entrant-weighted medians; all results included):']
    for cohort in result['cohorts']:
        lines.append(f"  {cohort['entry_band']} entries: {cohort['entrants']:,} entrants; repeated {fmt(cohort['median_repeated_pct'])}%; "
                     f"shared players {fmt(cohort['median_mean_shared_players'], 2)} (n={cohort['mean_shared_players_entrants']}); "
                     f"mean points {fmt(cohort['median_mean_points'], 2)} (n={cohort['mean_points_entrants']}).")
    field = {p['player_key']: p for p in result['field_players']}
    for p in payload['portfolios']:
        lines += ['', f"Username: {p['username']} | entries {p['entries']:,} | readable {p['readable_rosters']:,} | unique {fmt(p['unique_lineups'], 0)}",
                  f"  Repeated entries: {fmt(p['repeated_pct'])}%; athlete pool {fmt(p['player_pool'], 0)}; Captain pool {fmt(p['captain_pool'], 0)}.",
                  f"  Average shared players: {fmt(p['mean_shared_players'], 2)}; shared role slots: {fmt(p['mean_shared_role_slots'], 2)}.",
                  f"  Entries shared with other users: {fmt(p['entries_shared_with_other_users'], 0)}; mean field copies: {fmt(p['mean_field_copies'], 2)}.",
                  f"  Points: mean {fmt(p['mean_points'], 2)}, median {fmt(p['median_points'], 2)}, best {fmt(p['best_points'], 2)} (n={p['scored_entries']}); best rank {fmt(p['best_rank'], 0)} (n={p['ranked_entries']}).",
                  '  Player exposure (count / readable rosters; total / Captain / FLEX; field total):']
        peer = next(c for c in result['exact_count_cohorts'] if c['entry_count'] == p['entries'])
        lines.insert(len(lines)-1, f"  Exact entry-count peers: {peer['entrants']:,} entrants with {p['entries']} entries each, including this entrant; "
                     f"median player pool {fmt(peer['median_player_pool'], 0)} (n={peer['player_pool_entrants']}), "
                     f"shared players {fmt(peer['median_mean_shared_players'], 2)} (n={peer['mean_shared_players_entrants']}), "
                     f"mean points {fmt(peer['median_mean_points'], 2)} (n={peer['mean_points_entrants']}).")
        for player in p.get('players', []):
            lines.append(f"    {player['player']}: {player['entries']}/{player['denominator']}; "
                         f"{fmt(player['total_pct'])}% / {fmt(player['captain_pct'])}% / {fmt(player['flex_pct'])}%; "
                         f"field {fmt(field[player['player_key']]['total_pct'])}%.")
        lines.append('  Most shared player pairs (first 10; JSON includes all pairs):')
        for pair in p.get('pairs', [])[:10]:
            lines.append(f"    {' + '.join(pair['players'])}: {pair['entries']}/{pair['denominator']} ({fmt(pair['pct'])}%).")
        if payload['detailed_lineups_included']:
            lines.append('  Detailed lineups: ' + json.dumps(p['lineups'], ensure_ascii=False))
    lines += ['', 'Limits and definitions:'] + ['- ' + s for s in result['limitations']]
    return '\n'.join(lines)
