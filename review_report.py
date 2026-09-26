"""RL-01: bounded, read-only review snapshots and manually published archives.

No dependency on learning_db: its path, connection, report and matching helpers
can write. All shared fields are assembled explicitly, never copied wholesale.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import csv
import io
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import tempfile
from typing import Callable
import uuid
import zipfile

MAX_ROWS = 100_000  # Each source table; totals describe this explicit scanned scope.
MAX_DETAILS = 1_000
MAX_GROUPS = 500
MAX_DIAGNOSTIC_BYTES = 8_000_000
MAX_DIAGNOSTICS = 100
SPORTS = ('NFL', 'NBA', 'WNBA', 'MLB', 'NHL')
FORMATS = ('classic', 'showdown')
CSV_FIELDS = ('result_ref', 'export_ref', 'sport', 'format', 'date', 'association',
              'slot', 'player', 'player_id', 'score', 'fee', 'winnings', 'currency',
              'net', 'roi_pct', 'cash_state')
# Stored observations, not certified forecasts. No single Edge/SIM validity gate.
RECORDED_METRICS = {
    'projection': 'DK_points', 'base_projection': 'DK_points',
    'avg_ownership': 'legacy_percent_unknown_basis',
    'sim_edge': 'index', 'sim_top_one_pct': 'percent', 'sim_cash_rate': 'percent',
    'sim_return_index': 'index', 'sim_scenarios': 'count',
    'sim_expected_payout': 'currency_unverified', 'sim_expected_profit': 'currency_unverified',
    'sim_expected_roi_pct': 'percent',
}


class Cancelled(Exception):
    pass


class SourceDestination(ValueError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def source_paths() -> tuple[Path, Path]:
    """Resolve the accepted profile without creating directories or settings."""
    from data_paths import history_source_paths
    return history_source_paths()


@dataclass(frozen=True)
class Options:
    start: str = ''
    end: str = ''
    sport: str = 'all'
    kind: str = 'all'
    details: bool = False
    observation: str = ''

    def __post_init__(self):
        if self.sport not in ('all', *SPORTS) or self.kind not in ('all', *FORMATS):
            raise ValueError('Unsupported report filter.')
        if bool(self.start) != bool(self.end):
            raise ValueError('Choose both range dates.')
        if self.start:
            a, b = date.fromisoformat(self.start), date.fromisoformat(self.end)
            if a > b:
                raise ValueError('Start date must not follow end date.')
        if len(self.observation) > 2000:
            raise ValueError('Observation must be at most 2,000 characters.')


def safe_text(value, limit=120) -> str:
    """Disclosure minimization, not a guarantee of anonymity. Preview is required."""
    if not isinstance(value, str):
        return ''
    text = ''.join(c if c.isprintable() else ' ' for c in value[:4000])
    text = re.sub(r'(?i)(?:[a-z]:[\\/]|\\\\|/)[^\s<>"\']+', '[path removed]', text)
    text = re.sub(r'(?i)\b(?:password|secret|token|api[ _-]?key|authorization)\s*[:=]\s*\S+', '[credential removed]', text)
    text = re.sub(r'(?i)\b(?:sk-|gh[pousr]_|github_pat_|FAKE_SECRET_)[a-z0-9_-]+', '[credential removed]', text)
    text = re.sub(r'\b[A-Za-z0-9_+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b', '[email removed]', text)
    text = re.sub(r'(?i)\b(?:bearer\s+\S+|[a-z0-9_-]{40,})', '[token removed]', text)
    # Markdown/HTML remains literal text, including user observations.
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    text = re.sub(r'([\\`*_\[\]#!|])', r'\\\1', text)
    return text[:limit]


def number(value, *, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    if isinstance(value, str):
        value = value.strip()
        if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', value):
            return None
    try:
        result = float(value)
    except (ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and (not nonnegative or result >= 0) else None


def money(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, 'missing'
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None, 'invalid'
    text = str(value).strip()
    # Currency comes from explicit source evidence, never from this symbol.
    text = text.removeprefix('$')
    if not re.fullmatch(r'(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?', text):
        return None, 'invalid'
    try:
        amount = Decimal(text.replace(',', ''))
        if not amount.is_finite() or number(amount, nonnegative=True) is None or amount > Decimal('1e12'):
            return None, 'invalid'
        return amount, 'valid'
    except InvalidOperation:
        return None, 'invalid'


def object_json(value):
    if not isinstance(value, str) or len(value) > 16_384:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, RecursionError):
        return {}


def cash(raw):
    """Known generic import fields; no interpretation of legacy normalized roi."""
    fee, fs = money(raw.get('entry_fee'))
    winnings, ws = money(raw.get('winnings'))
    currency = raw.get('currency')
    fc = raw.get('entry fee currency', currency)
    wc = raw.get('winnings currency', currency)
    currency = fc if fc == wc and fc in ('USD', 'CAD', 'GBP', 'EUR') else None
    state = ('fee_' + fs if fs != 'valid' else 'winnings_' + ws if ws != 'valid'
             else 'currency_unverified' if currency is None else 'qualified')
    if raw.get('prize type', 'cash') != 'cash':
        state = 'unsupported_prize'
    # A fee may be a known subtotal even if winnings are absent, with known units.
    net = winnings - fee if state == 'qualified' else None
    roi = 100 * net / fee if net is not None and fee > 0 else None
    if roi is not None and number(roi) is None:
        state, net, roi = 'arithmetic_invalid', None, None
    return {'state': state, 'fee': fee, 'winnings': winnings, 'currency': currency,
            'net': net, 'roi_pct': roi}


def recorded_date(value):
    if not isinstance(value, str):
        return None
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            return date.fromisoformat(value).isoformat()
        # Supported timestamp dates use the explicitly recorded offset's calendar
        # date; naive ISO timestamps are labelled recorded-local, not guessed UTC.
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?', value):
            return datetime.fromisoformat(value.replace('Z', '+00:00')).date().isoformat()
    except ValueError:
        pass
    return None


def accepted(options, sport, kind, day, excluded):
    if options.sport != 'all' and sport != options.sport:
        excluded['sport_unknown' if sport == 'unknown' else 'sport_filter'] += 1
        return False
    if options.kind != 'all' and kind != options.kind:
        excluded['format_unknown' if kind == 'unknown' else 'format_filter'] += 1
        return False
    if options.start and (day is None or not options.start <= day <= options.end):
        excluded['date_unknown' if day is None else 'date_filter'] += 1
        return False
    return True


def enum(value, allowed):
    return value if isinstance(value, str) and value in allowed else 'unknown'


def metric(value, unit, basis, n, target, reason='missing_or_invalid', cohort='selected_results'):
    return {'state': 'available' if value is not None else 'unavailable', 'value': value,
            'unit': unit, 'basis': basis, 'cohort_id': cohort, 'qualified_count': n,
            'target_count': target, 'exclusions': {reason: target - n} if target > n else {}}


class Snapshot:
    def __init__(self, path, cancelled, progress):
        self.path, self.cancelled, self.progress = Path(path), cancelled, progress
        self.connection = None
        self.tables = {}
        self.sources = {}

    def check(self):
        if self.cancelled():
            raise Cancelled()

    def __enter__(self):
        self.check()
        try:
            self.connection = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)
            self.connection.row_factory = sqlite3.Row
            self.connection.set_progress_handler(lambda: 1 if self.cancelled() else 0, 1000)
            self.connection.execute('BEGIN')
            for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                self.tables[row[0]] = True
        except BaseException:
            if self.connection is not None:
                self.connection.close()
            raise
        return self

    def rows(self, table, fields, required=()):
        self.check()
        if table not in self.tables:
            self.sources[table] = {'state': 'missing_table'}
            return
        columns = {r[1] for r in self.connection.execute(f'PRAGMA table_info("{table}")')}
        if not set(required) <= columns:
            self.sources[table] = {'state': 'unsupported_schema'}
            return
        count = self.connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        state = self.sources[table] = {'state': 'available', 'total_rows': count, 'scanned_rows': 0,
                                      'limit': MAX_ROWS, 'truncated': count > MAX_ROWS,
                                      'missing_columns': [f for f in fields if f not in columns]}
        selected = ','.join(f'CASE WHEN typeof("{f}") IN (\'text\',\'blob\') THEN '
                            f'substr("{f}",1,16385) ELSE "{f}" END AS "{f}"'
                            if f in columns else f'NULL AS "{f}"' for f in fields)
        # All table/field identifiers originate in fixed calls below.
        for row in self.connection.execute(f'SELECT {selected} FROM "{table}" ORDER BY rowid LIMIT ?', (MAX_ROWS,)):
            state['scanned_rows'] += 1
            if state['scanned_rows'] % 250 == 1:
                self.check()
                self.progress(f'Reading {table.replace("_", " ")}: {state["scanned_rows"]:,}')
            yield dict(row)

    def __exit__(self, *args):
        if self.connection is not None:
            self.connection.close()


def _database(path, options, cancelled, progress):
    from review_build_evidence import roster_key
    excluded, coverage, methods = Counter(), Counter(), Counter()
    build_link_rows = []
    groups, contests, exports = {}, {}, {}
    details, detail_ids = [], defaultdict(list)
    settings, settings_excluded, fields = Counter(), Counter(), []
    selected_exports = set()
    recorded_counts = {k: Counter() for k in RECORDED_METRICS}
    imports, import_excluded = Counter(), Counter()
    with Snapshot(path, cancelled, progress) as db:
        for row in db.rows('historical_results', ('result_id', 'sport', 'slate_date', 'contest_name', 'raw_json',
                            'matched_lineup_id', 'match_method'), ('result_id', 'raw_json')):
            raw = object_json(row['raw_json'])
            sport = enum(row['sport'], SPORTS)
            # Only explicit retained source type is supported; never inherited
            # from the newest matched export or reconstructed from player names.
            raw_kind = raw.get('contest type')
            kind = enum(raw_kind.casefold() if isinstance(raw_kind, str) else None, FORMATS)
            day = recorded_date(row['slate_date'])
            if not accepted(options, sport, kind, day, excluded):
                continue
            coverage['selected_results'] += 1
            build_link_rows.append((sport, kind, day, roster_key(raw.get('lineup'))))
            coverage['unknown_date'] += day is None
            coverage['unknown_format'] += kind == 'unknown'
            matched = bool(row['matched_lineup_id'])
            coverage['recorded_matches'] += matched
            coverage['unmatched_results'] += not matched
            methods[enum(row['match_method'], ('player_ids', 'player_names'))] += matched
            cash_row = cash(raw)
            coverage['cash_' + cash_row['state']] += 1
            recorded_score = number(raw.get('actual_points'))
            score = recorded_score if sport != 'unknown' and kind != 'unknown' else None
            coverage['recorded_score_values'] += recorded_score is not None
            coverage['score_comparability_unknown'] += recorded_score is not None and score is None
            rank = number(raw.get('rank'), nonnegative=True)
            field_size = number(raw.get('field_size'), nonnegative=True)
            rank = rank if rank is not None and rank >= 1 and rank.is_integer() else None
            percentile = (100 * (1 - (rank - 1) / field_size) if rank is not None and
                          field_size and field_size.is_integer() and rank <= field_size else None)
            places = number(raw.get('places_paid'), nonnegative=True)
            paid_rank = rank <= places if rank is not None and places is not None and places.is_integer() else None
            positive_cash = cash_row['winnings'] > 0 if cash_row['state'] == 'qualified' else None
            conflict = paid_rank is not None and positive_cash is not None and paid_rank != positive_cash
            coverage['rank_cash_conflicts'] += conflict
            # Counts of distinct recorded labels do not certify distinct contests.
            ckey = (sport, day, str(row['contest_name'] or '')[:512])
            cref = None
            if ckey[2]:
                if ckey in contests or len(contests) < MAX_GROUPS:
                    cref = contests.setdefault(ckey, f'contest-{len(contests)+1:03d}')
                else:
                    coverage['contest_label_limit_rows'] += 1
            key = (sport, kind, day, cref, cash_row['currency'])
            if key not in groups and len(groups) >= MAX_GROUPS:
                coverage['group_limit_rows'] += 1
            else:
                group = groups.setdefault(key, {'n': 0, 'scores': [], 'percentiles': [], 'cash': [],
                                               'known_fees': [], 'paid': [], 'free': []})
                group['n'] += 1
                if score is not None:
                    group['scores'].append(score)
                if percentile is not None:
                    group['percentiles'].append(percentile)
                if cash_row['currency'] and cash_row['fee'] is not None:
                    group['known_fees'].append(cash_row['fee'])
                if cash_row['state'] == 'qualified':
                    group['cash'].append(cash_row)
                    group['paid' if cash_row['fee'] > 0 else 'free'].append(cash_row)
            if options.details:
                if len(details) >= MAX_DETAILS:
                    coverage['detail_limit_rows'] += 1
                else:
                    record = {'result_ref': f'result-{coverage["selected_results"]:03d}',
                              'sport': sport, 'format': kind, 'date': day,
                              'association': 'legacy_unverified' if matched else 'unmatched',
                              'score': recorded_score, 'cash_state': cash_row['state'],
                              'recorded_rank': rank, 'recorded_field_size': field_size,
                              'rank_cash_conflict': conflict,
                              **{k: float(cash_row[k]) if isinstance(cash_row[k], Decimal) else cash_row[k]
                                 for k in ('fee', 'winnings', 'currency', 'net', 'roi_pct')},
                              'recorded_export_slots': []}
                    details.append(record)
                    if matched:
                        detail_ids[str(row['matched_lineup_id'])].append(record)

        for row in db.rows('exports', ('export_id', 'sport', 'contest_type', 'created_at', 'app_version',
                                      'build_style', 'salary_strategy', 'own_mode', 'field_preset'), ('export_id',)):
            sp, ki, dy = enum(row['sport'], SPORTS), enum(row['contest_type'], FORMATS), recorded_date(row['created_at'])
            if not accepted(options, sp, ki, dy, settings_excluded):
                continue
            selected_exports.add(row['export_id'])
            version = row['app_version']
            version = version if isinstance(version, str) and re.fullmatch(r'v?\d+\.\d+\.\d+', version) else None
            key = (sp, ki, version,
                   enum(row['build_style'], ('Strategic', 'Balanced', 'Randomized', 'Contrarian', 'Chalk')),
                   enum(row['salary_strategy'], ('Near Cap', 'Maximize Salary', 'Balanced Spend', 'Salary Leverage')),
                   enum(row['own_mode'], ('Leverage', 'Balanced', 'Chalk')),
                   enum(row['field_preset'], ('Single Entry', '3-Max', '20-Max', '150-Max')))
            if key in settings or len(settings) < MAX_GROUPS:
                settings[key] += 1
            else:
                coverage['settings_group_limit_rows'] += 1
            coverage['recorded_exports'] += 1

        for row in db.rows('historical_imports', ('created_at', 'sport'), ('created_at',)):
            # Imports are a separate timestamp-based inventory, never entries.
            if accepted(options, enum(row['sport'], SPORTS), 'unknown', recorded_date(row['created_at']), import_excluded):
                imports['selected_import_records'] += 1

        for row in db.rows('lineups', ('lineup_id', 'export_id', *RECORDED_METRICS), ('lineup_id', 'export_id')):
            if row['export_id'] not in selected_exports:
                coverage['rosters_outside_selected_exports'] += 1
                continue
            coverage['recorded_rosters'] += 1
            observed = {}
            for name, unit in RECORDED_METRICS.items():
                value = number(row[name])
                state = 'missing' if row[name] is None else 'invalid' if value is None else 'recorded_unverified'
                recorded_counts[name][state] += 1
                recorded_counts[name]['zero_count'] += value == 0
                observed[name] = {'value': value, 'state': state, 'unit': unit,
                                  'basis': 'recorded_export_value_not_certified_forecast'}
            for target in detail_ids.get(str(row['lineup_id']), []):
                target['recorded_export_metrics'] = observed
        if options.details:
            # No values or native IDs from this lookup are exposed by default.
            for row in db.rows('lineup_players', ('lineup_id', 'slot', 'name', 'player_id'), ('lineup_id', 'slot')):
                targets = detail_ids.get(str(row['lineup_id']))
                if not targets:
                    continue
                ref = exports.setdefault(str(row['lineup_id']), f'recorded-roster-{len(exports)+1:03d}')
                slot = row['slot']
                slot = slot if isinstance(slot, str) and re.fullmatch(r'(?:CPT|FLEX|QB|RB|WR|TE|DST|P|C|1B|2B|3B|SS|OF|PG|SG|SF|PF|G|F|UTIL)\d?', slot) else 'unknown'
                pid = str(row['player_id'] or '')
                pid = pid if re.fullmatch(r'\d{1,16}', pid) else ''
                for target in targets:
                    target['export_ref'] = ref
                    if len(target['recorded_export_slots']) < 12:
                        target['recorded_export_slots'].append({'slot': slot, 'player': safe_text(row['name'], 80), 'player_id': pid})
                    else:
                        coverage['slot_limit_rows'] += 1

        for row in db.rows('contest_field_summaries', ('sport', 'roster_size', 'entry_count', 'field_size',
                                                     'metadata_coverage_pct'), ('entry_count',)):
            # No source contest date/format guarantees in this summary schema.
            if options.start or options.kind != 'all' or (options.sport != 'all' and row['sport'] != options.sport):
                coverage['field_filter_unavailable_rows'] += 1
                continue
            if len(fields) < MAX_GROUPS:
                fields.append({'field_ref': f'field-{len(fields)+1:03d}', 'sport': enum(row['sport'], SPORTS),
                               **{k: number(row[k], nonnegative=True) for k in ('entry_count', 'field_size', 'roster_size', 'metadata_coverage_pct')}})
            else:
                coverage['field_detail_limit_rows'] += 1
        sources = db.sources

    measures = []
    for (sp, ki, dy, cr, currency), g in groups.items():
        n, paid, zero = g['n'], g['paid'], g['free']
        fees = sum((r['fee'] for r in paid), Decimal(0))
        wins = sum((r['winnings'] for r in paid), Decimal(0))
        net = wins - fees
        cid = f'group-{len(measures)+1:03d}'
        def m(value, unit, basis, count):
            return metric(float(value) if value is not None and number(value) is not None else None,
                          unit, basis, count, n, cohort=cid)
        def avg(values):
            # Divide before summing to avoid overflow for finite extreme scores.
            return math.fsum(v / len(values) for v in values) if values else None
        full = len(g['cash']) == n
        measures.append({'cohort_id': cid, 'sport': sp, 'format': ki, 'date': dy, 'contest_ref': cr,
                         'contest_basis': 'distinct_recorded_label_not_certified_identity', 'target_count': n,
                         'currency': currency, 'values': {
            'mean_score': m(avg(g['scores']), 'DK_points', 'original_result_score', len(g['scores'])),
            'mean_finish_percentile': m(avg(g['percentiles']), 'percent', '100*(1-(rank-1)/recorded_field_size)', len(g['percentiles'])),
            'paid_fees': m(fees if paid else None, currency or 'unknown', 'complete_positive_fee_cash_subset', len(paid)),
            'paid_winnings': m(wins if paid else None, currency or 'unknown', 'same_paid_subset', len(paid)),
            'paid_net': m(net if paid else None, currency or 'unknown', 'same_paid_subset', len(paid)),
            'paid_roi_pct': m(100*net/fees if fees else None, 'percent', '100*subset_net/subset_fees', len(paid)),
            'known_fees_subtotal': m(sum(g['known_fees']) if g['known_fees'] else None, currency or 'unknown', 'known_fee_rows_only', len(g['known_fees'])),
            'zero_fee_prizes': m(sum(r['winnings'] for r in zero) if zero else None, currency or 'unknown', 'zero_fee_cash_separate', len(zero)),
            'whole_cohort_net': m(sum(r['net'] for r in g['cash']) if full else None, currency or 'unknown', 'whole_group_cash', len(g['cash'])),
            'whole_cohort_roi_pct': m(100*net/fees if len(paid) == n and fees else None, 'percent', 'whole_group_positive_fee_cash', len(paid)),
        }})
    settings_rows = [dict(zip(('sport', 'format', 'recorded_app_version', 'build_style', 'salary_strategy', 'ownership_mode', 'field_preset'), k),
                          count=v, basis='recorded_export_not_certified_original_build') for k, v in settings.items()]
    availability = {name: {**metric(None, unit, 'legacy_origin_completion_and_association_unverified',
                                    0, coverage['recorded_rosters'], 'lineage_unverified', 'selected_export_rosters'),
                           'observed_counts': dict(recorded_counts[name])}
                    for name, unit in RECORDED_METRICS.items()}
    return {'_build_link_rows': build_link_rows, 'sources': sources, 'coverage': dict(coverage), 'filter_exclusions': dict(excluded),
            'match_methods': dict(methods), 'distinct_recorded_contest_labels': len(contests),
            'groups': measures, 'settings': settings_rows, 'settings_filter_exclusions': dict(settings_excluded),
            'imports': dict(imports), 'import_filter_exclusions': dict(import_excluded),
            'recorded_metric_availability': availability,
            'fields': fields, **({'details': details} if options.details else {})}


def diagnostics(path, options, cancelled):
    result = {'captured_at': now(), 'state': 'missing', 'scope': 'independent_recent_records',
              'complete_history': False, 'records': [], 'filter_exclusions': {}}
    try:
        with Path(path).open('rb') as handle:
            content = handle.read(MAX_DIAGNOSTIC_BYTES + 1)
        if len(content) > MAX_DIAGNOSTIC_BYTES:
            return dict(result, state='size_limit')
        payload = json.loads(content)
        rows = payload.get('records') if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return dict(result, state='invalid')
        excluded = Counter()
        result.update(state='available', available_records=len(rows), truncated=len(rows) > MAX_DIAGNOSTICS)
        for row in rows[:MAX_DIAGNOSTICS]:
            if cancelled():
                raise Cancelled()
            if not isinstance(row, dict):
                excluded['invalid_record'] += 1
                continue
            sp, ki, dy = enum(row.get('sport'), SPORTS), enum(row.get('contest_type'), FORMATS), recorded_date(row.get('created_at'))
            if not accepted(options, sp, ki, dy, excluded):
                continue
            application = row.get('application') if isinstance(row.get('application'), dict) else {}
            timing = row.get('timing') if isinstance(row.get('timing'), dict) else {}
            settings = row.get('settings') if isinstance(row.get('settings'), dict) else {}
            sim = row.get('sim') if isinstance(row.get('sim'), dict) else {}
            result['records'].append({'sport': sp, 'format': ki, 'recorded_date': dy,
                'computation': enum(row.get('status'), ('completed', 'cancelled', 'error')),
                'application': enum(application.get('status'), ('applied', 'not_applied')),
                'reason': enum(application.get('reason'), ('applied', 'cancelled', 'stale_job', 'scope_mismatch', 'source_changed', 'incomplete', 'preparation_failed', 'retained_mismatch', 'validation_failed', 'failed', 'context_changed', 'closing')),
                'total_seconds': number(timing.get('total_seconds'), nonnegative=True),
                'phase_seconds': {k: number(timing.get(k), nonnegative=True) for k in ('generation_seconds', 'simulation_seconds', 'selection_seconds')},
                'recorded_settings': {
                    'build_style': enum(settings.get('build_style'), ('Strategic', 'Balanced', 'Randomized', 'Contrarian', 'Chalk')),
                    'salary_strategy': enum(settings.get('salary_strategy'), ('Near Cap', 'Maximize Salary', 'Balanced Spend', 'Salary Leverage')),
                    'compute_mode': enum(settings.get('compute_mode'), ('Fast', 'Deep')),
                },
                'recorded_sampling': {k: number(sim.get(k), nonnegative=True) for k in ('scenario_count', 'screening_scenarios', 'validation_scenarios')},
                'sampling_basis': 'stored_counts_do_not_certify_completion_or_outcome_link'})
        result['filter_exclusions'] = dict(excluded)
    except FileNotFoundError:
        pass
    except (ValueError, RecursionError, UnicodeError):
        result['state'] = 'invalid'
    except OSError:
        result['state'] = 'inaccessible'
    return result


@dataclass(frozen=True)
class Report:
    """Immutable serialized evidence is the sole source of all output files."""
    evidence: bytes
    _source_files: tuple[Path, ...] = field(default=(), repr=False)
    _source_roots: tuple[Path, ...] = field(default=(), repr=False)

    @property
    def data(self):
        return json.loads(self.evidence)

    def summary(self):
        d = self.data
        db = d['database']
        filters = d['filters']
        date_scope = f'{filters["start"]} through {filters["end"]} (inclusive)' if filters['start'] else 'all available dates'
        lines = ['# DFS Review Report', '', f'Report: {d["report_id"]}', f'Captured: {d["generated_at"]}',
                 'Schema: 2; running application version/revision: unavailable (not embedded).',
                 '', '## Scope and data quality',
                 f'Dates: {date_scope}; sport: {filters["sport"]}; format: {filters["format"]}.',
                 'Results use recorded slate dates. Settings/imports/diagnostics use their own recorded timestamp dates.',
                 'Database state: ' + db['state'],
                 'Imported rows are recorded occurrences, not a verified personal bankroll or submitted-entry ledger.']
        counts = db.get('coverage', {})
        lines += [f'- Selected imported rows: {counts.get("selected_results", 0)}; recorded legacy matches: {counts.get("recorded_matches", 0)}; unmatched: {counts.get("unmatched_results", 0)}.',
                  f'- Unknown result dates: {counts.get("unknown_date", 0)}; unknown formats: {counts.get("unknown_format", 0)}.',
                  f'- Recorded exports: {counts.get("recorded_exports", 0)}; recorded rosters: {counts.get("recorded_rosters", 0)}.',
                  '- Distinct recorded contest labels: ' + str(db.get('distinct_recorded_contest_labels', 0)) + '.',
                  'Result filter exclusions: ' + (', '.join(f'{k.replace("_", " ")}: {v}' for k, v in db.get('filter_exclusions', {}).items()) or 'none'),
                  'Source tables and scan coverage:']
        for name, source in db.get('sources', {}).items():
            lines.append(f'- {name}: {source["state"]}; {source.get("scanned_rows", 0)}/{source.get("total_rows", "unknown")} scanned; truncated: {source.get("truncated", False)}.')
        lines += ['', '## Performance']
        for g in db.get('groups', []):
            lines.append(f'### {g["cohort_id"]}: {g["sport"]} / {g["format"]} / {g["date"] or "date unknown"} / {g["contest_ref"] or "contest unknown"}')
            for name, m in g['values'].items():
                value = f'{m["value"]:.4g} {m["unit"]}' if m['value'] is not None else 'Unavailable'
                lines.append(f'- {name.replace("_", " ")}: {value}; {m["qualified_count"]}/{m["target_count"]} rows; {m["basis"]}.')
        if not db.get('groups'):
            lines.append('No selected performance groups are available.')
        lines += ['', '## Recorded build settings', 'Export-time settings; original build association is unverified.']
        for row in db.get('settings', []):
            lines.append(f'- {row["count"]} exports: {row["sport"]} {row["format"]}; app {row["recorded_app_version"] or "version unknown"}; {row["build_style"]}; {row["salary_strategy"]}; ownership {row["ownership_mode"]}; field {row["field_preset"]}.')
        if not db.get('settings'):
            lines.append('No selected export settings are available.')
        builds = d.get('build_evidence', {})
        linkage = builds.get('result_linkage', {})
        lines += ['', '## Original build evidence',
                  'State: ' + builds.get('state', 'unavailable'),
                  'Result linkage: ' + json.dumps(linkage.get('counts', {})) + f'; denominator: {linkage.get("target_count", 0)} selected imported occurrences.',
                  'Exact Captain-aware name-roster matches and compatible snapshots are separate evidence. Neither certifies the original build or submission.']
        for record in builds.get('records', []):
            counts = record['player_decisions']['counts']
            settings = record['recorded_settings']
            concentration = record.get('captain_concentration', {})
            lines.append(f'- {record["build_ref"]}: {record["source"]}; {record["slate_date"] or "date unknown"} {record["format"]}; '
                         f'input {record["input_id"][:12]}; selection {settings["selection_mode"]}; '
                         f'recorded Captain locks {counts.get("LockCpt_true", 0)}, unknown lock flags {counts.get("LockCpt_unknown", 0)}; '
                         f'excluded QBs with positive forecasts {counts.get("excluded_qb_positive_projection", 0)}; '
                         f'unknown QB eligibility {counts.get("qb_unknown", 0)}; related result occurrences {record["matched_result_occurrences"]}.')
            if concentration:
                lines.append(f'  Largest Captain exposure: {concentration["largest_count"] if concentration["largest_count"] is not None else "not applicable"}/{concentration["output_denominator"]} generated outputs; '
                             f'outputs with a recorded Captain lock: {concentration["locked_captain_output_count"]}. '
                             f'Code fingerprint: {record.get("recorded_code_fingerprint") or "unknown"}; Git revision: not recorded.')
        lines.extend('- ' + item for item in builds.get('limitations', []))
        lines += ['', '## Predictions and ownership', *d['qualification_limits']]
        for name, m in db.get('recorded_metric_availability', {}).items():
            lines.append(f'- {name}: qualified comparison unavailable (0/{m["target_count"]}); stored observations: {json.dumps(m["observed_counts"])}; units: {m["unit"]}.')
        lines += ['', '## Available diagnostics',
                  'State: ' + d['diagnostics']['state'],
                  f'Available records: {d["diagnostics"].get("available_records", "unknown")}; selected: {len(d["diagnostics"]["records"])}; truncated: {d["diagnostics"].get("truncated", False)}.',
                  'Independent recent observations; not linked to contest outcomes.']
        for row in d['diagnostics']['records']:
            lines.append(f'- {row["recorded_date"] or "date unknown"} {row["sport"]} {row["format"]}: computation {row["computation"]}; application {row["application"]} ({row["reason"]}); total seconds {row["total_seconds"] if row["total_seconds"] is not None else "unknown"}.')
        lines += ['', '## Opponent field observations',
                  f'{len(db.get("fields", []))} selected stored field summaries; see evidence.json for recorded entry, field-size and metadata-coverage values. These are excluded from cash totals.',
                  '', '## Findings to investigate', *('- ' + s for s in d['findings']),
                  '', '## User observation (unverified)', d['observation'] or 'None included.',
                  '', '## Limits and sharing', *('- ' + s for s in d['limitations']),
                  'Files: summary.md, evidence.json' + (', lineups.csv' if d['options']['details'] else ''),
                  'No automatic transmission. Review optional detail and observation text before sharing.']
        return '\n'.join(lines) + '\n'

    def csv(self):
        stream = io.StringIO(newline='')
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        for row in self.data['database'].get('details', []):
            for slot in row.get('recorded_export_slots') or [{}]:
                merged = {**row, **slot}
                values = {k: '' if merged.get(k) is None else merged.get(k, '') for k in CSV_FIELDS}
                for k, v in values.items():
                    if isinstance(v, str) and v.lstrip().startswith(('=', '+', '-', '@')):
                        values[k] = "'" + v
                writer.writerow(values)
        return stream.getvalue()


def capture(options=Options(), *, db_path=None, diagnostic_path=None,
            cancelled: Callable[[], bool] = lambda: False, progress: Callable[[str], None] = lambda _: None):
    default_db, default_diag = source_paths()
    if cancelled():
        raise Cancelled()
    db = {'state': 'missing', 'captured_at': now()}
    try:
        path = Path(db_path) if db_path is not None else default_db
        if stat.S_ISREG(path.stat().st_mode):
            db.update(_database(path, options, cancelled, progress), state='available')
        else:
            db['state'] = 'unsupported_source'
    except FileNotFoundError:
        pass
    except sqlite3.Error:
        if cancelled():
            raise Cancelled()
        db = {'state': 'unavailable_or_invalid', 'captured_at': db['captured_at']}
    except OSError:
        db = {'state': 'inaccessible', 'captured_at': db['captured_at']}
    diag = diagnostics(diagnostic_path if diagnostic_path is not None else default_diag, options, cancelled)
    from review_build_evidence import capture_build_evidence
    builds = capture_build_evidence(path.parent, options, db.pop('_build_link_rows', []), cancelled, progress)
    coverage = db.get('coverage', {})
    selected = coverage.get('selected_results', 0)
    findings = [f'{coverage.get("recorded_matches", 0)} of {selected} selected imported rows have recorded legacy matches; original forecast/build identity is unverified.']
    if coverage.get('rank_cash_conflicts'):
        findings.append(f'{coverage["rank_cash_conflicts"]} of {selected} selected rows have contradictory paid-rank and cash-return evidence; neither source is used to overwrite the other.')
    for key, count in coverage.items():
        if key.startswith('cash_') and key != 'cash_qualified' and count:
            findings.append(f'{count} of {selected} selected rows excluded from qualified cash: {key[5:]}.')
    limits = ['Imported occurrences and exports are not proof of platform submission or personal bankroll.',
              'Cash requires original fee/winnings and explicit compatible currency. Missing currency stays unverified.',
              'Scores are grouped by recorded sport and explicit source format; unknown formats are labelled.',
              'Contest references count recorded labels, not certified unique contests. Entries within a contest are correlated.',
              f'At most {MAX_ROWS:,} rows per source table, {MAX_GROUPS} breakdown groups and {MAX_DETAILS:,} detail rows; see source/coverage truncation.',
              'Fields are independent opponent observations; excluded from personal cash totals.',
              'Diagnostic JSON has a separate capture time and incomplete history.',
              'The existing Results & Learning refresh may update matches; this new capture/save does not.',
              'Automatic text redaction cannot identify every personal detail. Review before sharing.']
    data = {'schema_version': 2, 'report_id': str(uuid.uuid4()), 'generated_at': now(),
            'generator': {'application_version': None, 'source_revision': None, 'identity_state': 'not_embedded'},
            'filters': {'start': options.start or None, 'end': options.end or None, 'sport': options.sport,
                        'format': options.kind, 'result_date_basis': 'recorded_slate_date',
                        'other_date_basis': 'recorded_timestamp_calendar_date', 'range_unknowns': 'excluded_and_counted'},
            'options': {'details': options.details}, 'database': db, 'diagnostics': diag, 'build_evidence': builds,
            'observation': safe_text(options.observation, 2000), 'findings': findings, 'limitations': limits,
            'qualification_limits': [
                'Forecast comparison unavailable: legacy storage does not certify original run/stage/timing or role-aware outcome association.',
                'Legacy ownership basis and coverage are unverified; no low-ownership buckets or Captain/FLEX substitution.',
                'Edge/index/cash-proxy values are not validated forecasts, cash payouts or ROI. No simulation or historical reconstruction ran.']}
    if cancelled():
        raise Cancelled()
    return Report(json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2).encode('utf-8'),
                  (path.resolve(), Path(str(path) + '-wal').resolve(), Path(str(path) + '-shm').resolve(),
                   Path(diagnostic_path if diagnostic_path is not None else default_diag).resolve()),
                  (path.parent.resolve(),))


def publish(report: Report, destination, *, cancelled=lambda: False):
    """Caller obtains normal overwrite consent. An error preserves old bytes."""
    target = Path(destination)
    if cancelled():
        raise Cancelled()
    if any(target.resolve().is_relative_to(root) for root in report._source_roots):
        raise SourceDestination()
    for source in report._source_files:
        if target.resolve() == source or (target.exists() and source.exists() and target.samefile(source)):
            raise SourceDestination()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.dfs-review-', suffix='.tmp', dir=target.parent, delete=False) as handle:
            temporary = Path(handle.name)
            with zipfile.ZipFile(handle, 'w', compression=zipfile.ZIP_DEFLATED) as z:
                entries = [('summary.md', report.summary().encode('utf-8')), ('evidence.json', report.evidence)]
                if report.data['options']['details']:
                    entries.append(('lineups.csv', report.csv().encode('utf-8')))
                for name, content in entries:
                    if cancelled():
                        raise Cancelled()
                    # Fixed names and metadata do not disclose host paths.
                    info = zipfile.ZipInfo(name)
                    info.compress_type = zipfile.ZIP_DEFLATED
                    z.writestr(info, content)
            handle.flush()
            os.fsync(handle.fileno())
        with zipfile.ZipFile(temporary) as z:
            if z.testzip() is not None:
                raise OSError('Report archive validation failed.')
        if cancelled():
            raise Cancelled()
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
