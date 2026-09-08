"""Portable, data-only build inputs. No executable objects or account settings."""
import copy
import datetime
import hashlib
import json
import os
import tempfile


def fingerprint(value):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def create_snapshot(players, recipe, rules, calibration=None, contest=None, freshness=None):
    inputs = copy.deepcopy(dict(players=players, recipe=recipe, rules=rules,
                                calibration=calibration or {}, contest=contest or {}))
    if not players:
        raise ValueError('Load players before saving a snapshot.')
    return dict(schema_version=1, created_at=datetime.datetime.now().astimezone().isoformat(),
                input_id=fingerprint(inputs), inputs=inputs, freshness=copy.deepcopy(freshness or {}))


def validate_snapshot(value):
    if not isinstance(value, dict) or value.get('schema_version') != 1:
        raise ValueError('Unsupported build snapshot format.')
    inputs = value.get('inputs')
    if not isinstance(inputs, dict) or set(inputs) != {'players', 'recipe', 'rules', 'calibration', 'contest'}:
        raise ValueError('Snapshot inputs are incomplete.')
    if not isinstance(inputs['players'], list) or not inputs['players'] or not all(isinstance(p, dict) for p in inputs['players']):
        raise ValueError('Snapshot player data is invalid.')
    if not all(isinstance(inputs[k], dict) for k in ('recipe', 'rules', 'calibration', 'contest')):
        raise ValueError('Snapshot settings are invalid.')
    if inputs['recipe'].get('sport') != 'NFL' or inputs['recipe'].get('contest_kind') not in ('classic', 'showdown'):
        raise ValueError('Snapshots currently support NFL Classic and Showdown.')
    if value.get('input_id') != fingerprint(inputs):
        raise ValueError('Snapshot content does not match its input ID; the file may have changed.')
    return copy.deepcopy(value)


def save_snapshot(path, value):
    validate_snapshot(value)
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=folder, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def load_snapshot(path):
    if os.path.getsize(path) > 25 * 1024 * 1024:
        raise ValueError('Snapshot exceeds the 25 MB size limit.')
    with open(path, encoding='utf-8') as handle:
        return validate_snapshot(json.load(handle))


def freshness_text(summary, replay=False):
    summary = summary or {}
    mode = 'Snapshot replay: automatic refresh paused' if replay else 'Live inputs'
    checked = summary.get('checked_at') or 'unknown'
    status = summary.get('sleeper_state') or 'unknown'
    odds = summary.get('odds_state') or 'unknown'
    usage = summary.get('usage_state') or 'unknown'
    return f'{mode}. Last recorded check: {checked}. Player source: {status}; usage: {usage} (season {summary.get("usage_season") or "unknown"}, matches {summary.get("usage", "unknown")}); odds: {odds}.'
