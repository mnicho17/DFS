"""Unqualified research input captures and isolated DST sampling; no history I/O.

A digest detects changes, not authenticity or capture time. External immutable
registration and existing historical qualification remain separate requirements.
"""
import datetime as dt
import hashlib
import json
import math
import random
from zoneinfo import ZoneInfo
from scripts.dst_marginal_specification import specification, SPREAD_FACTORS

PLAN = 'dst-marginal-prospective-plan-v1'
SEEDS = (90210, 481516, 730001)
DRAWS = 2000


def _check(cancelled):
    if cancelled():
        raise InterruptedError('DST research capture/sampling cancelled; no partial result.')


def _text(value):
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 256:
        raise ValueError('Exact bounded canonical text required.')
    return value


def _hash(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('Lowercase SHA-256 required.')
    return value


def _time(value):
    if not isinstance(value, str):
        raise ValueError('Aware ISO timestamp required.')
    try:
        stamp = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('Aware ISO timestamp required.') from None
    if stamp.tzinfo is None:
        raise ValueError('Aware ISO timestamp required.')
    return stamp


def _encoded(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def capture_inputs(*, game_date, game_key, kickoff, captured_at, defenses,
                   source_hashes, model_hash, pregame_evidence_hash, cancelled=lambda: False):
    """Return a detached, deterministic unqualified capture, never proof of timing.

    Caller supplies canonical keys and an externally captured timestamp. No
    result/actual fields are accepted; neither wall-clock nor files are read.
    """
    _check(cancelled)
    if not isinstance(game_date, str) or not '2026-11-08' <= game_date <= '2026-12-07':
        raise ValueError('Game outside declared window.')
    try:
        if dt.date.fromisoformat(game_date).isoformat() != game_date:
            raise ValueError('Canonical ISO date required.')
    except ValueError:
        raise ValueError('Canonical ISO date required.') from None
    start, captured = _time(kickoff), _time(captured_at)
    if start.astimezone(ZoneInfo('America/New_York')).date().isoformat() != game_date:
        raise ValueError('Kickoff does not match local game date.')
    if not _time('2026-11-08T03:24:35Z') <= captured < start:
        raise ValueError('Capture must follow declaration and precede kickoff.')
    if not isinstance(defenses, (list, tuple)) or len(defenses) != 2:
        raise ValueError('Exactly two opposing DST identities required.')
    rows = []
    for row in defenses:
        _check(cancelled)
        if not isinstance(row, dict) or set(row) != {'key', 'team', 'opponent', 'forecast'}:
            raise ValueError('Exact DST input fields required; no outcomes.')
        mean = row['forecast']
        if isinstance(mean, bool) or not isinstance(mean, (float, int)) or not math.isfinite(mean):
            raise ValueError('Finite forecast required.')
        specification(mean, cancelled=cancelled)
        rows.append(dict(key=_text(row['key']), team=_text(row['team']),
                         opponent=_text(row['opponent']), forecast=float(mean)))
    a, b = rows
    if a['key'] == b['key'] or a['team'] == b['team'] or a['opponent'] != b['team'] or b['opponent'] != a['team']:
        raise ValueError('Conflicting/duplicate DST identity or opposing-team mapping.')
    if not isinstance(source_hashes, dict) or set(source_hashes) != {'salary', 'frozen_inputs'}:
        raise ValueError('Salary and frozen-input SHA-256 receipts required.')
    payload = dict(plan=PLAN, status='unqualified_capture', game_date=game_date,
                   game_key=_text(game_key), kickoff=kickoff, captured_at=captured_at,
                   defenses=sorted(rows, key=lambda row: row['key']),
                   source_hashes={k: _hash(v) for k, v in source_hashes.items()},
                   model_hash=_hash(model_hash), pregame_evidence_hash=_hash(pregame_evidence_hash))
    result = dict(payload=payload, sha256=hashlib.sha256(_encoded(payload)).hexdigest())
    _check(cancelled)
    return result


def verify_capture(receipt, *, cancelled=lambda: False):
    """Verify schema/digest only; does not certify identity, provenance or timing."""
    _check(cancelled)
    if not isinstance(receipt, dict) or set(receipt) != {'payload', 'sha256'}:
        raise ValueError('Exact capture envelope required.')
    p = receipt['payload']
    fields = {'plan','status','game_date','game_key','kickoff','captured_at','defenses',
              'source_hashes','model_hash','pregame_evidence_hash'}
    if not isinstance(p, dict) or set(p) != fields or p['plan'] != PLAN or p['status'] != 'unqualified_capture':
        raise ValueError('Unsupported capture schema or qualification claim.')
    expected = capture_inputs(**{k: v for k, v in p.items() if k not in {'plan','status'}}, cancelled=cancelled)
    if expected != receipt:
        raise ValueError('Capture digest/content changed or noncanonical.')
    _check(cancelled)
    return expected


def sample_capture(receipt, *, cancelled=lambda: False):
    """Unqualified candidate-only sidecar; never touches offense or parent RNG.

    Fixed seeds and 6,000 draws per defense/factor. Same normals across factors,
    distinct defense streams. No source read, qualification, or actual scoring.
    """
    verified = verify_capture(receipt, cancelled=cancelled)
    p = verified['payload']
    output = []
    for row in p['defenses']:
        specs = {str(f): specification(row['forecast'], f, cancelled=cancelled) for f in SPREAD_FACTORS}
        values = {str(f): [] for f in SPREAD_FACTORS}
        for seed in SEEDS:
            # Compact array encoding matches the frozen plan; no key sorting applies.
            material = [PLAN, p['game_date'], p['game_key'], row['key'], seed]
            rng = random.Random(int.from_bytes(hashlib.sha256(_encoded(material)).digest(), 'big'))
            for _ in range(DRAWS):
                _check(cancelled)
                z = rng.gauss(0., 1.)
                for factor, spec in specs.items():
                    values[factor].append(max(-4., spec['location'] + spec['latent_spread'] * z))
        output.append(dict(key=row['key'], specifications=specs, samples=values))
    _check(cancelled)
    return dict(plan=PLAN, status='unqualified_samples', capture_sha256=verified['sha256'],
                seeds=list(SEEDS), draws_per_seed=DRAWS, defenses=output)
