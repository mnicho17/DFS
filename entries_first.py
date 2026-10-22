"""Captured entries-first setup. No generation, export or historical qualification."""
from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import tempfile

from dk_entries import read_entries_template
from data_io import read_players_csv

MAX_BYTES = 32 * 1024 * 1024
LIMITS = {'Single Entry': 1, '3-Max': 3, '20-Max': 20, '150-Max': 150}
SLOTS = {'showdown': ['CPT'] + ['FLEX'] * 5,
         'classic': ['QB', 'RB', 'RB', 'WR', 'WR', 'WR', 'TE', 'FLEX', 'DST']}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def signature(path):
    s = Path(path).stat()
    return [s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_dev, s.st_ino]


def check(cancelled):
    if cancelled():
        raise InterruptedError('Entries setup cancelled.')


@contextmanager
def capture(path, cancelled=lambda: False):
    """Parse a bounded private copy, retaining the exact source digest."""
    check(cancelled)
    before = signature(path)
    if before[0] > MAX_BYTES:
        raise ValueError('Entries setup supports CSV files up to 32 MiB.')
    with tempfile.TemporaryDirectory(prefix='dfs-entries-') as folder:
        target = Path(folder) / 'captured.csv'
        h = hashlib.sha256()
        total = 0
        with open(path, 'rb') as source, target.open('wb') as output:
            while True:
                check(cancelled)
                block = source.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > MAX_BYTES:
                    raise ValueError('Entries setup supports CSV files up to 32 MiB.')
                h.update(block)
                output.write(block)
        if signature(path) != before:
            raise ValueError('CSV changed while being read; reopen entries setup.')
        check(cancelled)
        yield str(target), h.hexdigest(), before
        check(cancelled)


def money(value):
    try:
        result = Decimal(str(value).strip().replace('$', '').replace(',', ''))
    except InvalidOperation:
        raise ValueError('Each contest needs a known entry fee, including an explicit zero.') from None
    try:
        valid = result.is_finite() and result >= 0 and result == result.quantize(Decimal('.01'))
    except InvalidOperation:
        valid = False
    if not valid:
        raise ValueError('Entry fees must be nonnegative amounts in cents.')
    return str(result.quantize(Decimal('.01')))


def contests(template):
    groups = {}
    for index in template.entry_row_indexes:
        row = template.rows[index]
        key, name, fee = row[2].strip(), row[1].strip(), money(row[3])
        if not name:
            raise ValueError('An entry has no contest name.')
        item = groups.setdefault(key, dict(id=key, name=name, fee=fee, count=0))
        if (item['name'], item['fee']) != (name, fee):
            raise ValueError('Conflicting metadata for the same Contest ID; download fresh entries.')
        item['count'] += 1
    return list(groups.values())


def kind_for(template):
    for kind, slots in SLOTS.items():
        if template.roster_headers == slots:
            return kind
    raise ValueError('Entries-first setup currently supports NFL Classic and six-player Showdown. Use Load CSV for other formats.')


def pool_identity(players):
    return fingerprint(sorted((str(p.get('FlexID') or ''), str(p.get('CptID') or ''),
        str(p.get('Name') or ''), str(p.get('Team') or ''), str(p.get('Position') or ''),
        str(p.get('GameInfo') or ''), float(p.get('FlexSalary') or 0), float(p.get('CptSalary') or 0))
        for p in players))


def salary_players(path, cancelled=lambda: False):
    # The shared reader keys by name. Reject ambiguous source rows before it can
    # collapse two explicit identities or choose the cheaper duplicate revision.
    from data_io import _salary_rows, _canon_field, _extract_name
    names, identities, athlete_ids, roles = {}, {}, {}, {}
    with open(path, newline='', encoding='utf-8-sig') as source:
        for raw in _salary_rows(source):
            check(cancelled)
            row = {_canon_field(k): v for k, v in raw.items()}
            name = _extract_name(row)
            if not name:
                if str(row.get('ID') or '').strip():
                    raise ValueError('A salary ID has no player name.')
                continue
            pid = str(row.get('ID') or '').strip()
            if not re.fullmatch(r'[0-9]+', pid):
                raise ValueError('The salary pool needs explicit numeric player IDs.')
            role = 'CPT' if str(row.get('Roster Position') or '').strip().upper() == 'CPT' else 'FLEX'
            facts = (name, str(row.get('Team') or ''), str(row.get('Position') or ''),
                     str(row.get('Game Info') or ''), money(row.get('Salary')))
            if ((role, name) in names and names[role, name] != pid or
                (role, pid) in identities and identities[role, pid] != facts or
                pid in athlete_ids and athlete_ids[pid] != name):
                raise ValueError('Conflicting explicit player identities or salary revisions in the source table.')
            names[role, name] = pid;identities[role, pid] = facts;athlete_ids[pid] = name
            roles.setdefault(name, {})[role] = facts
    for role_facts in roles.values():
        if 'CPT' in role_facts and ('FLEX' not in role_facts or role_facts['CPT'][:4] != role_facts['FLEX'][:4]):
            raise ValueError('Captain and FLEX source identities do not match.')
    return read_players_csv(path)


def embedded_players(path, cancelled=lambda: False):
    try:
        return salary_players(path, cancelled)
    except ValueError as exc:
        if str(exc).startswith('No player salary table found.'):
            return None
        raise


def inspect_entries(path, cancelled=lambda: False):
    with capture(path, cancelled) as (frozen, digest, _):
        template = read_entries_template(frozen)
        kind = kind_for(template)
        groups = contests(template)
        embedded = embedded_players(frozen, cancelled)
        check(cancelled)
        return dict(entries_path=str(Path(path).resolve()), entries_digest=digest,
                    kind=kind, contests=groups,
                    embedded_identity=pool_identity(embedded) if embedded else '')


def metadata_key(plan, contest):
    return fingerprint(dict(kind=plan['kind'], id=contest['id'], name=contest['name'], fee=contest['fee']))


def profile_key(profile):
    return fingerprint({k: v for k, v in profile.items() if k != 'user_entries'}) if profile else ''


def choose(plan, contest_id, limit, strategy, profiles, salary_path, confirmed=False):
    contest = next((c for c in plan['contests'] if c['id'] == contest_id), None)
    if not contest or limit not in LIMITS or not strategy:
        raise ValueError('Select a contest, its entry-limit preset, and a strategy.')
    if contest['count'] > LIMITS[limit]:
        raise ValueError('The entries count exceeds the selected entry-limit preset.')
    if not salary_path:
        raise ValueError('Choose the matching salary slate.')
    if not plan['embedded_identity'] and not confirmed:
        raise ValueError('Confirm the selected salary slate; this entries file has no embedded player pool.')
    profile = None
    if strategy != 'default':
        from contest_strategy import execution_profile
        raw = profiles.get(strategy.removeprefix('profile:')) if strategy.startswith('profile:') else None
        if not raw:
            raise ValueError('The selected saved contest profile is unavailable.')
        profile = execution_profile(dict(raw, user_entries=contest['count']))
        if money(profile['entry_fee']) != contest['fee']:
            raise ValueError('The saved profile entry fee does not match this contest.')
    return dict(schema=1, entries_path=plan['entries_path'], entries_digest=plan['entries_digest'],
                contest=deepcopy(contest), kind=plan['kind'], entry_limit=limit, strategy=strategy,
                profile=profile, metadata_key=metadata_key(plan, contest),
                salary_path=str(Path(salary_path).resolve()), salary_signature=signature(salary_path),
                salary_confirmed=bool(confirmed))


def validate_players(template, contest_id, players):
    from csv_loading import detect_sport, detect_mode
    kind = kind_for(template)
    if detect_sport(players) != 'NFL' or detect_mode(players) != kind:
        raise ValueError('Salary sport or roster format does not match the entries.')
    lookup = {}
    for p in players:
        canonical = str(p.get('FlexID') or '')
        if not canonical:
            raise ValueError('The salary pool needs explicit player IDs.')
        for role, field in (('CPT', 'CptID'), ('FLEX', 'FlexID')):
            key = str(p.get(field) or '')
            if key:
                if (role, key) in lookup:
                    raise ValueError('Ambiguous explicit player IDs in the salary pool.')
                lookup[role, key] = canonical
    observed = 0
    for index in template.selected_rows(contest_id):
        row = template.rows[index]
        seen = set()
        for slot, raw in zip(template.roster_headers, row[template.roster_start:]):
            value = raw.strip()
            if not value:
                continue
            match = re.fullmatch(r'.+\s\(([0-9]+)\)', value)
            explicit = value if re.fullmatch(r'[0-9]+', value) else match[1] if match else ''
            if not explicit:
                raise ValueError('An existing roster cell has no explicit player ID; download fresh entries.')
            key = lookup.get(('CPT' if slot == 'CPT' else 'FLEX', explicit))
            if not key:
                raise ValueError('An existing entry player ID or Captain role does not match this salary pool.')
            if key in seen:
                raise ValueError('An existing entry repeats the same player across roster slots.')
            seen.add(key)
            observed += 1
    return observed


def load_salary(path, setup, cancelled=lambda: False):
    """Called on the CSV worker before enrichment; never mutate the selection."""
    if signature(path) != setup['salary_signature']:
        raise ValueError('Selected salary CSV changed; reopen entries setup.')
    with capture(path, cancelled) as (frozen, salary_digest, _):
        players = salary_players(frozen, cancelled)
    with capture(setup['entries_path'], cancelled) as (frozen, digest, _):
        if digest != setup['entries_digest']:
            raise ValueError('Entries CSV changed; reopen entries setup.')
        template = read_entries_template(frozen)
        current = next((c for c in contests(template) if c['id'] == setup['contest']['id']), None)
        if current != setup['contest'] or kind_for(template) != setup['kind']:
            raise ValueError('Selected contest changed; reopen entries setup.')
        embedded = embedded_players(frozen, cancelled)
        if embedded and pool_identity(embedded) != pool_identity(players):
            raise ValueError('Selected salary pool differs from the embedded entries salary pool.')
        if not embedded and not setup['salary_confirmed']:
            raise ValueError('Confirm the matching salary slate first.')
        observed = validate_players(template, setup['contest']['id'], players)
    result = deepcopy(setup)
    result.update(salary_digest=salary_digest, pool_identity=pool_identity(players), observed_slots=observed)
    return players, result


def verify_sources(setup, cancelled=lambda: False):
    for path, expected in ((setup['entries_path'], setup['entries_digest']),
                           (setup['salary_path'], setup['salary_digest'])):
        with capture(path, cancelled) as (_, digest, _):
            if digest != expected:
                raise ValueError('Entries or salary source changed during loading; reopen entries setup.')
