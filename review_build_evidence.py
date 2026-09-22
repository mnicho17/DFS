"""Bounded readers for existing build archives/snapshots. Never import DFS writers.

Roster equivalence is evidence of shared output, not proof of submission or of
which of several identical builds was used. Checksums detect inconsistency,
not authenticity. No original inputs are reconstructed or upgraded.
"""
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import itertools
import json
from pathlib import Path
import re
import zipfile
from zoneinfo import ZoneInfo

from review_report import Cancelled, accepted, enum, now, number, safe_text

MAX_FILES = 100
MAX_DIRECTORY_ENTRIES = 1000
MAX_FILE_BYTES = 32_000_000
MAX_TOTAL_BYTES = 64_000_000  # Expanded bytes, shared by both source directories.
MAX_PLAYERS = 2000
MAX_LINEUPS = 1000
MAX_PLAYER_DETAILS = 1000
HASH = re.compile(r'[0-9a-f]{64}')
MEMBERS = {'manifest.json', 'input-snapshot.json', 'lineups.json',
           'audit-lineups.csv', 'build-report.txt'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def name_key(value):
    if not isinstance(value, str) or not value or len(value) > 200:
        return ''
    value = re.sub(r'\s*\(\s*\d{4,12}\s*\)\s*$', '', value)
    parts = re.sub('[^a-z0-9]+', ' ', value.lower()).split()
    if parts and parts[-1] in {'jr', 'sr', 'ii', 'iii', 'iv', 'v'}:
        parts.pop()
    return ' '.join(parts)


def roster_key(text):
    """Names only; reject malformed, ambiguous or ID-only rosters, never guess."""
    if not isinstance(text, str) or len(text) > 4000:
        return None
    matches = list(re.finditer(r'\b(CPT|CAPTAIN|FLEX|QB|RB|WR|TE|DST|D/ST)\s+', text, re.I))
    if not matches or text[:matches[0].start()].strip():
        return None
    rows = [(m.group(1).upper(), name_key(text[m.end():matches[n+1].start()
              if n+1 < len(matches) else len(text)].strip(' ,;|/')))
            for n, m in enumerate(matches)]
    names = [n for _, n in rows]
    if any(not n or n.isdigit() for n in names) or len(set(names)) != len(names):
        return None
    slots = Counter(s for s, _ in rows)
    if len(rows) == 6 and slots.get('CPT', 0) + slots.get('CAPTAIN', 0) == 1 and slots['FLEX'] == 5:
        kind = 'showdown'
    elif len(rows) == 9 and slots['QB'] == 1 and slots['RB'] == 2 and slots['WR'] == 3 and slots['TE'] == 1 and slots['FLEX'] == 1 and slots.get('DST', 0) + slots.get('D/ST', 0) == 1:
        kind = 'classic'
    else:
        return None
    return kind, tuple(sorted(('@cpt:' if s in {'CPT', 'CAPTAIN'} else '') + n for s, n in rows))


def timestamp(value):
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return dt if dt.tzinfo is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def snapshot(value):
    if not isinstance(value, dict) or value.get('schema_version') != 1:
        raise ValueError('snapshot schema')
    inputs = value.get('inputs')
    if not isinstance(inputs, dict) or set(inputs) != {'players', 'recipe', 'rules', 'calibration', 'contest'}:
        raise ValueError('snapshot inputs')
    players = inputs['players']
    if not isinstance(players, list) or not 0 < len(players) <= MAX_PLAYERS or any(not isinstance(p, dict) for p in players):
        raise ValueError('players')
    if any(not isinstance(inputs[k], dict) for k in ('recipe', 'rules', 'calibration', 'contest')):
        raise ValueError('settings')
    if not HASH.fullmatch(str(value.get('input_id', ''))) or digest(inputs) != value['input_id']:
        raise ValueError('input checksum')
    recipe = inputs['recipe']
    if recipe.get('sport') != 'NFL' or recipe.get('contest_kind') not in ('classic', 'showdown'):
        raise ValueError('unsupported format')
    names = [name_key(p.get('Name')) for p in players]
    if any(not n or n.isdigit() for n in names) or len(set(names)) != len(names):
        raise ValueError('ambiguous names')
    starts = []
    for p in players:
        match = re.search(r'(\d{2}/\d{2}/20\d{2})\s+(\d{1,2}:\d{2}\s*[AP]M)\s+ET\b', str(p.get('GameInfo', '')), re.I)
        if not match:
            starts = []
            break
        try:
            starts.append(datetime.strptime(match[1]+' '+match[2].replace(' ', '').upper(),
                           '%m/%d/%Y %I:%M%p').replace(tzinfo=ZoneInfo('America/New_York')))
        except (ValueError, KeyError):
            starts = []
            break
    created = timestamp(value.get('created_at'))
    dates = {d.date().isoformat() for d in starts}
    day = next(iter(dates)) if len(dates) == 1 else None
    pregame = bool(created and starts and created < min(starts) and day)
    return dict(raw=value, players=players, names=set(names), recipe=recipe,
                kind=recipe['contest_kind'], day=day, created=created,
                earliest=min(starts) if starts else None, pregame=pregame)


def flag(player, key):
    v = player.get(key)
    return v if isinstance(v, bool) else None


def pct(player, key):
    v = number(player.get(key))
    return v if v is not None and 0 <= v <= 100 else None


def decisions(snap, detail_budget):
    """Describe stored decisions. Missing flags do not become today's decisions."""
    counts = Counter()
    details = []
    for p in snap['players']:
        qb = flag(p, 'NFLQBEligible')
        state = 'not_qb' if p.get('Position') != 'QB' else 'eligible' if qb is True else 'excluded' if qb is False else 'unknown'
        counts['qb_' + state] += 1
        for key in ('LockCpt', 'LockFlex', 'FadeCpt', 'FadeFlex'):
            counts[key + ('_true' if flag(p, key) is True else '_false' if flag(p, key) is False else '_unknown')] += 1
        counts['positive_projection'] += (number(p.get('FlexProjection')) or 0) > 0
        if state == 'excluded' and (number(p.get('FlexProjection')) or 0) > 0:
            counts['excluded_qb_positive_projection'] += 1
        counts['explicit_captain_max'] += pct(p, 'MaxCptPct') is not None
        if len(details) < detail_budget:
            details.append({'player': safe_text(p.get('Name')), 'recorded_qb_eligibility': state,
                'recorded_depth': number(p.get('NFLDepthOrder'), nonnegative=True),
                'recorded_availability': enum(p.get('NFLAvailability'), ('STARTER','OUT','IR','INACTIVE','BACKUP 2','BACKUP 3','BACKUP 4')),
                'flags': {k: flag(p, k) for k in ('LockCpt','LockFlex','FadeCpt','FadeFlex')},
                'limits_pct': {k: pct(p, k) for k in ('MinPct','MaxPct','MinCptPct','MaxCptPct')},
                'recorded_flex_projection': number(p.get('FlexProjection')), 'projection_unit': 'DK_points'})
    return dict(player_count=len(snap['players']), counts=dict(counts)), details


def explanation(snap, details_left):
    summary, detail = decisions(snap, details_left)
    recipe = snap['recipe']
    deep = recipe.get('deep_compute') if isinstance(recipe.get('deep_compute'), dict) else {}
    return {'input_id': snap['raw']['input_id'], 'input_recorded_at': snap['raw'].get('created_at') if snap['created'] else None,
            'sport': 'NFL', 'format': snap['kind'], 'slate_date': snap['day'],
            'pregame_input': snap['pregame'], 'checksum_state': 'consistent_not_authenticated',
            'recorded_settings': {'selection_mode': enum(deep.get('selection_mode'), ('Individual ranking','Portfolio selection')),
                'balance_ownership': flag(recipe, 'balance_ownership'), 'requested_lineups': number(recipe.get('requested_lineups'), nonnegative=True),
                'minimum_unique': number(recipe.get('min_unique'), nonnegative=True)},
            'player_decisions': summary, **({'player_details': detail} if details_left else {})}


class Reader:
    def __init__(self, cancelled):
        self.cancelled = cancelled
        self.bytes = 0
        self.issues = Counter()

    def check(self):
        if self.cancelled():
            raise Cancelled()

    def consume(self, amount):
        self.check()
        if amount > MAX_FILE_BYTES or self.bytes + amount > MAX_TOTAL_BYTES:
            raise OverflowError('read budget')
        self.bytes += amount

    def paths(self, folder, suffix):
        self.check()
        try:
            if folder.is_symlink() or getattr(folder, 'is_junction', lambda: False)():
                self.issues['unsupported_directory'] += 1
                return []
            entries = list(itertools.islice(folder.iterdir(), MAX_DIRECTORY_ENTRIES + 1))
            if len(entries) > MAX_DIRECTORY_ENTRIES:
                self.issues['directory_scan_limit'] += 1
            paths = sorted((p for p in entries[:MAX_DIRECTORY_ENTRIES] if p.suffix == suffix), reverse=True)
            if len(paths) > MAX_FILES:
                self.issues['file_count_limit'] += len(paths) - MAX_FILES
            return paths[:MAX_FILES]
        except FileNotFoundError:
            return []
        except OSError:
            self.issues['directory_inaccessible'] += 1
            return []

    def data(self, path):
        self.check()
        if path.is_symlink() or not path.is_file():
            raise ValueError('unsupported file')
        declared = path.stat().st_size
        self.consume(declared)
        with path.open('rb') as f:
            raw = f.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise OverflowError('file size changed')
        if len(raw) > declared:
            self.consume(len(raw) - declared)
        return json.loads(raw)

    def archive(self, path):
        self.check()
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError('unsupported archive')
        with zipfile.ZipFile(path) as z:
            entries = z.infolist()
            names = [x.filename for x in entries]
            if len(names) != len(set(names)) or not set(names) <= MEMBERS or not {'manifest.json', 'lineups.json', 'input-snapshot.json'} <= set(names):
                raise ValueError('archive members')
            self.consume(sum(x.file_size for x in entries))
            data = {}
            for entry in entries:
                self.check()
                with z.open(entry) as f:
                    raw = f.read(min(entry.file_size, MAX_FILE_BYTES) + 1)
                if len(raw) != entry.file_size:
                    raise ValueError('member size')
                data[entry.filename] = raw
        manifest = json.loads(data['manifest.json'])
        if not isinstance(manifest, dict) or not isinstance(manifest.get('metadata'), dict) or not isinstance(manifest.get('sha256'), dict):
            raise ValueError('manifest')
        if set(manifest['sha256']) != set(data) - {'manifest.json'} or any(hashlib.sha256(data[k]).hexdigest() != v for k, v in manifest['sha256'].items()):
            raise ValueError('archive checksum')
        payload = json.loads(data['lineups.json']); meta = manifest['metadata']
        if not isinstance(payload, dict) or payload.get('metadata') != meta or meta.get('schema_version') != 1 or meta.get('record_type') != 'generated_outputs':
            raise ValueError('archive metadata')
        snap = snapshot(json.loads(data['input-snapshot.json']))
        if meta.get('sport') != 'NFL' or meta.get('kind') != snap['kind'] or meta.get('input_id') != snap['raw']['input_id']:
            raise ValueError('archive scope')
        rows = payload.get('lineups')
        if not isinstance(rows, list) or not 0 < len(rows) <= MAX_LINEUPS or meta.get('output_count') != len(rows):
            raise ValueError('output count')
        sigs, cpts = set(), Counter()
        for row in rows:
            self.check()
            if not isinstance(row, dict) or not isinstance(row.get('slots'), list):
                raise ValueError('output slots')
            slots = row['slots']
            if any(not isinstance(s, dict) or not isinstance(s.get('player'), dict) for s in slots):
                raise ValueError('player slots')
            parsed = roster_key(' '.join(str(s.get('slot'))+' '+str(s['player'].get('Name', '')) for s in slots))
            if not parsed or parsed[0] != snap['kind'] or any(k.removeprefix('@cpt:') not in snap['names'] for k in parsed[1]):
                raise ValueError('output roster')
            sigs.add(parsed[1])
            for k in parsed[1]:
                if k.startswith('@cpt:'):
                    cpts[k[5:]] += 1
        return snap, meta, sigs, cpts


def capture_build_evidence(root, options, results, cancelled, progress):
    started = now()
    reader = Reader(cancelled)
    records, archives, inputs = [], [], []
    exclusions = Counter()
    details_left = MAX_PLAYER_DETAILS if options.details else 0
    for folder, suffix in (('build-archives', '.zip'), ('snapshots', '.json')):
        for path in reader.paths(Path(root) / folder, suffix):
            reader.check()
            progress('Reading saved build evidence')
            try:
                if suffix == '.zip':
                    snap, meta, sigs, cpts = reader.archive(path)
                else:
                    snap = snapshot(reader.data(path)); meta = None
                if not accepted(options, 'NFL', snap['kind'], snap['day'], exclusions):
                    continue
                record = explanation(snap, details_left)
                details_left -= len(record.get('player_details', []))
                if options.details:
                    record['player_details_truncated'] = len(record.get('player_details', [])) < len(snap['players'])
                record['build_ref'] = f'build-{len(records)+1:03d}'
                record['source'] = 'generated_output_archive' if meta is not None else 'input_snapshot_only'
                record['matched_result_occurrences'] = 0
                if meta is not None:
                    created = timestamp(meta.get('created_at'))
                    status = enum(meta.get('build_status'), ('completed', 'cancelled', 'error'))
                    record.update(recorded_code_fingerprint=meta.get('app_code_id') if HASH.fullmatch(str(meta.get('app_code_id',''))) else None,
                        source_revision=None, revision_state='not_recorded', computation=status,
                        application='not_established', submission='not_established', output_count=meta['output_count'],
                        pregame_output=bool(created and snap['earliest'] and created >= snap['created'] and created < snap['earliest']) if snap['created'] else False)
                    locks = {name_key(p.get('Name')) for p in snap['players'] if p.get('LockCpt') is True}
                    record['captain_concentration'] = {
                        'largest_count': max(cpts.values()) if cpts else None, 'output_denominator': meta['output_count'],
                        'locked_captain_output_count': sum(v for k,v in cpts.items() if k in locks),
                        'basis': 'generated_output_occurrences_not_submitted_entries'}
                    if options.details:
                        record['captain_concentration']['players'] = [{'player':safe_text(k),'count':v,'recorded_captain_lock':k in locks} for k,v in sorted(cpts.items())]
                    if status == 'completed' and record['pregame_output'] and snap['pregame']:
                        archives.append((snap['kind'], snap['day'], sigs, record))
                else:
                    inputs.append((snap, record))
                records.append(record)
            except OverflowError:
                reader.issues['byte_limit'] += 1
                break
            except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError, UnicodeError, zipfile.BadZipFile, NotImplementedError, RuntimeError):
                reader.issues['invalid_or_unavailable_file'] += 1
    by_roster = defaultdict(list)
    for kind, day, sigs, record in archives:
        for sig in sigs:
            by_roster[(kind, day, sig)].append(record)
    linked = Counter()
    for n, (sport, kind, day, parsed) in enumerate(results):
        if n % 100 == 0:
            reader.check()
        if sport != 'NFL' or day is None or parsed is None or (kind != 'unknown' and kind != parsed[0]):
            linked['unqualified_result_identity'] += 1
            continue
        result_kind, signature = parsed
        matches = by_roster.get((result_kind, day, signature), [])
        if matches:
            linked['unique_archive_roster_match' if len(matches) == 1 else 'multiple_archive_roster_matches'] += 1
            for record in matches:
                record['matched_result_occurrences'] += 1
            continue
        names = {p.removeprefix('@cpt:') for p in signature}
        candidates = [(s,r) for s,r in inputs if s['pregame'] and s['kind'] == result_kind and s['day'] == day and names <= s['names']]
        if candidates:
            latest = max(s['created'] for s,r in candidates)
            candidates = [(s,r) for s,r in candidates if s['created'] == latest]
            if len({s['raw']['input_id'] for s,r in candidates}) == 1:
                linked['snapshot_compatible_only'] += 1
                candidates[0][1]['matched_result_occurrences'] += 1
            else:
                linked['ambiguous_latest_snapshot'] += 1
        else:
            linked['no_supported_evidence'] += 1
    return {'state': 'partial' if reader.issues else 'available' if records else 'missing_or_filtered',
        'schema_version': 1, 'capture_started_at':started, 'capture_completed_at':now(),
        'records': records, 'issues':dict(reader.issues), 'filter_exclusions':dict(exclusions),
        'read_bytes':reader.bytes, 'limits':{'files_per_directory':MAX_FILES,'directory_entries':MAX_DIRECTORY_ENTRIES,
            'expanded_bytes':MAX_TOTAL_BYTES,'player_details':MAX_PLAYER_DETAILS},
        'result_linkage':{'target_count':len(results),'counts':dict(linked),
            'cohort':'selected_imported_result_occurrences',
            'basis':'within scanned valid files: NFL name rosters, Captain-aware; same recorded slate date; pregame completed output or compatible pregame input',
            'certified_original_build_count':0},
        'limitations':[
            'A matching archived roster does not establish platform submission, GUI application, or which identical build was used.',
            'Snapshot compatibility uses the latest qualifying input among scanned files containing the roster names; it is not original-build attribution.',
            'Archive code fingerprints are not Git revisions. Missing revisions and eligibility flags remain unknown.',
            'Checksums verify internal consistency, not authenticity. Name matching rejects within-snapshot collisions but cannot certify player IDs.',
            'Recorded locks explain constraints, not whether the user intended them. Positive forecasts do not establish eligibility.',
            'No current eligibility rules are applied to old inputs. No forecast, ownership, or strategy adjustment is performed.',
            'Build files are independently captured, bounded history. Counts per build can overlap; do not add them to result totals.']}
