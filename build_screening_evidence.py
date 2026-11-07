"""Bind original observational audit bytes to a generated build archive."""
from collections import Counter
import hashlib
from pathlib import Path
import re


def capture(folder, receipt, snapshot, rows, *, kind, cancelled=False):
    """Return a conservative receipt and optional immutable compressed member.

    Failure withholds screening evidence only. It never repairs/reuses a bank or
    changes generated lineups. Input identity alone never selects an audit.
    """
    unavailable = dict(status='unavailable', reason='original_screening_evidence_not_bound')
    if cancelled or kind != 'showdown' or not snapshot or not isinstance(receipt, dict):
        return unavailable, None
    if receipt.get('status') != 'saved':
        return dict(status='not_recorded'), None
    try:
        from build_snapshots import validate_snapshot
        snapshot = validate_snapshot(snapshot)
        from screening_audit import MAX_COMPRESSED_BYTES, load_audit_bytes
        from showdown_simulation import showdown_signature
        name = receipt.get('filename')
        if not isinstance(name, str) or not re.fullmatch(r'[0-9a-f]{64}-[0-9a-f]{32}\.dfsscreen\.json\.gz', name):
            raise ValueError('invalid_audit_filename')
        path = Path(folder) / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_COMPRESSED_BYTES:
            raise ValueError('unsupported_audit_file')
        with path.open('rb') as handle:
            captured = handle.read(MAX_COMPRESSED_BYTES + 1)
        envelope = load_audit_bytes(captured)
        payload = envelope['payload']
        if envelope['audit_id'] != receipt.get('audit_id') or len(captured) != receipt.get('bytes'):
            raise ValueError('audit_receipt_differs')
        inputs = snapshot['inputs']
        if payload['input_id'] != snapshot['input_id'] or payload['players'] != inputs['players']:
            raise ValueError('audit_frozen_inputs_differ')
        if payload['context']['rules'] != inputs['rules']:
            raise ValueError('audit_frozen_rules_differ')
        recipe = inputs['recipe']
        fields = dict(salary_cap='salary_cap', salary_strategy='salary_strategy',
                      build_style='build_style', own_mode='ownership_mode',
                      own_weight='ownership_weight', requested_count='requested_lineups')
        if any(key not in payload['context'] or target not in recipe or
               payload['context'][key] != recipe[target] for key, target in fields.items()):
            raise ValueError('audit_frozen_context_differs')
        from nfl_simulation import player_key
        frozen = {player_key(player): player for player in inputs['players']}
        if len(frozen) != len(inputs['players']):
            raise ValueError('ambiguous_frozen_players')
        for row in rows:
            for player in [row['Captain']] + list(row['Flex']):
                if frozen.get(player_key(player)) != player:
                    raise ValueError('selected_player_identity_differs')
        candidates = payload['candidates']
        selected = Counter(tuple(candidates[i]['signature']) for i in payload['selected'])
        if selected != Counter(showdown_signature(row) for row in rows):
            raise ValueError('audit_selected_occurrences_differ')
        return dict(status='bound', audit_id=envelope['audit_id'], filename=name,
                    sha256=hashlib.sha256(captured).hexdigest(), bytes=len(captured),
                    member='screening-audit.json.gz', scope='recorded_membership_only'), captured
    except Exception:
        # Optional observational evidence must never prevent the output archive.
        return unavailable, None
