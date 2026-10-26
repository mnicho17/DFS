"""Bounded process-local reuse of complete deterministic opponent-field inputs."""
from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
import platform
import threading

_MAX_ROSTERS = 30000
_MAX_ENTRIES = 3
_fields = OrderedDict()
_lock = threading.Lock()


def identity(players, count, salary_cap, seed, model, profile):
    # Serialize every supplied field, in player order. Unsupported or ambiguous
    # inputs bypass reuse; they retain the existing sampler's behavior.
    from candidate_library import code_id
    from nfl_simulation import player_key
    keys = [player_key(p) for p in players]
    if not keys or any(not p.get('FlexID') for p in players) or len(set(keys)) != len(keys):
        return None
    if not 0 < count <= _MAX_ROSTERS:
        return None
    try:
        payload = json.dumps([players, count, salary_cap, seed, model, profile,
                              code_id(), platform.python_version()],
                             sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def get(key):
    with _lock:
        if key is None or key not in _fields:
            return None
        _fields.move_to_end(key)
        return deepcopy(_fields[key])


def put(key, field, requested):
    if key is None or len(field) != requested:
        return
    saved = deepcopy(field)
    with _lock:
        _fields[key] = saved
        _fields.move_to_end(key)
        while len(_fields) > _MAX_ENTRIES or sum(map(len, _fields.values())) > _MAX_ROSTERS:
            _fields.popitem(last=False)


def clear():
    with _lock:
        _fields.clear()
