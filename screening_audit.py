"""Bounded observational Showdown banks; never candidate-library or SIM reuse."""
import copy
import datetime as dt
import gzip
import json
import math
import os
from pathlib import Path
import re
import tempfile
import uuid
from collections import Counter
from build_snapshots import fingerprint
from showdown_simulation import showdown_signature

MAX_CANDIDATES = 100000
MAX_BYTES = 64 * 1024**2
MAX_COMPRESSED_BYTES = 16 * 1024**2


class ScreeningAudit:
    def __init__(self, generated, eligible, retained, players, *, input_id, context, cancelled=lambda: False):
        self.reason = ''; self.cancelled = cancelled; self.rows = []; self.lookup = {}
        self.payload = None
        try:
            if cancelled(): raise ValueError('Cancelled build; screening audit not captured.')
            if not re.fullmatch('[0-9a-f]{64}', input_id or ''):
                raise ValueError('Build input identity unavailable; screening audit not captured.')
            if len(generated) + len(retained) > MAX_CANDIDATES:
                raise ValueError('Candidate count exceeds audit limit; no truncated bank saved.')
            for group in (generated, retained):
                for lu in group:
                    if cancelled(): raise ValueError('Cancelled build; screening audit not captured.')
                    signature = showdown_signature(lu)
                    if signature not in self.lookup:
                        self.lookup[signature] = len(self.rows)
                        self.rows.append(dict(signature=list(signature), coarse_metrics=None))
            indices = lambda rows: [self.lookup[showdown_signature(lu)] for lu in rows]
            self.payload = dict(schema=1, input_id=input_id,
                frozen_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                players=copy.deepcopy(players), context=copy.deepcopy(context), candidates=self.rows,
                generated_order=indices(generated), salary_eligible_order=indices(eligible),
                retained_order=indices(retained), screening_input_order=indices(retained + eligible),
                coarse_order=[], shortlisted=[], captain_reserved=[], feasible_reserved=[], selected=[],
                screening_status='not run', screening_report={},
                limitation='Observed candidate bank and scalar coarse scores only; not an optimizer-ready library or exact solver replay. Partial scoring is explicit. No actual outcomes or return claims.')
        except Exception as exc:
            self.reason = str(exc); self.payload = None

    def screened(self, coarse, short, captain_reserved, feasible_reserved):
        if self.payload is None: return
        try:
            indices = lambda rows: [self.lookup[showdown_signature(lu)] for lu in rows]
            for lu in coarse['lineups']:
                if self.cancelled(): raise ValueError('Cancelled build; screening audit not captured.')
                metrics = dict(getattr(lu, 'sim_metrics', {}) or {})
                if any(isinstance(v, float) and not math.isfinite(v) for v in metrics.values()):
                    raise ValueError('Non-finite coarse metrics; screening audit not captured.')
                self.rows[self.lookup[showdown_signature(lu)]]['coarse_metrics'] = {
                    k: v for k, v in metrics.items() if v is None or isinstance(v, (str, bool, int, float))}
            report = coarse['report']; completed = int(report.get('scenarios', 0))
            expected = self.payload['context']['screening']['scenarios']
            complete = completed == expected and Counter(indices(coarse['lineups'])) == Counter(self.payload['screening_input_order'])
            self.payload.update(coarse_order=indices(coarse['lineups']), shortlisted=indices(short),
                captain_reserved=sorted(self.lookup[signature] for signature in captain_reserved),
                feasible_reserved=indices(feasible_reserved),
                screening_status='complete' if complete else 'partial',
                screening_report=dict(scenarios=completed, requested_scenarios=expected,
                    field_lineups=int(report.get('field_lineups', 0)),
                    screening_reused=int(report.get('screening_reused', 0)),
                    field_diagnostic=copy.deepcopy(report.get('field_diagnostic') or {})))
        except Exception as exc:
            self.reason = str(exc); self.payload = None

    def save(self, selected, *, build_cancelled=False, folder=None):
        temporary = None
        try:
            if self.payload is None:
                return dict(status='not saved', reason=self.reason)
            if build_cancelled or self.cancelled():
                return dict(status='not saved', reason='Cancelled build; screening audit not saved.')
            payload = self.payload
            payload['selected'] = [self.lookup[showdown_signature(lu)] for lu in selected]
            payload['saved_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
            from repeatability import model_version
            payload['model_version'] = model_version()
            envelope = dict(audit_id=fingerprint(payload), payload=payload)
            raw = json.dumps(envelope, separators=(',', ':'), allow_nan=False).encode('utf-8')
            if len(raw) > MAX_BYTES:
                raise ValueError('Audit byte limit exceeded; no truncated bank saved.')
            if folder is None:
                from build_diagnostics import build_history_path
                folder = Path(build_history_path()).parent / 'screening-audits'
            folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
            filename = payload['input_id'] + '-' + uuid.uuid4().hex + '.dfsscreen.json.gz'
            fd, temporary = tempfile.mkstemp(dir=folder, suffix='.tmp')
            with os.fdopen(fd, 'wb') as handle:
                with gzip.GzipFile(fileobj=handle, mode='wb', compresslevel=1, mtime=0) as compressed:
                    compressed.write(raw)
            size = Path(temporary).stat().st_size
            if size > MAX_COMPRESSED_BYTES:
                raise ValueError('Compressed audit limit exceeded; no truncated bank saved.')
            if self.cancelled():
                return dict(status='not saved', reason='Cancelled build; screening audit not saved.')
            os.replace(temporary, folder / filename); temporary = None
            return dict(status='saved', audit_id=envelope['audit_id'], filename=filename, bytes=size,
                candidates=len(self.rows), generated=len(payload['generated_order']),
                salary_eligible=len(payload['salary_eligible_order']), retained=len(payload['retained_order']),
                shortlisted=len(payload['shortlisted']), screening_status=payload['screening_status'])
        except Exception as exc:
            return dict(status='not saved', reason='Screening audit unavailable: ' + str(exc))
        finally:
            if temporary is not None:
                try: os.unlink(temporary)
                except OSError: pass


def load_audit(path):
    """Data-only bounded read; integrity does not certify pregame timing or scores."""
    path = Path(path)
    if path.stat().st_size > MAX_COMPRESSED_BYTES: raise ValueError('Compressed audit limit exceeded.')
    with gzip.open(path, 'rb') as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES: raise ValueError('Audit decompression limit exceeded.')
    envelope = json.loads(raw); payload = envelope['payload']
    if payload.get('schema') != 1 or envelope.get('audit_id') != fingerprint(payload):
        raise ValueError('Screening audit integrity mismatch.')
    rows = payload['candidates']
    if not 0 <= len(rows) <= MAX_CANDIDATES: raise ValueError('Audit candidate limit exceeded.')
    for field in ('generated_order', 'salary_eligible_order', 'retained_order', 'screening_input_order',
                  'coarse_order', 'shortlisted', 'captain_reserved', 'feasible_reserved', 'selected'):
        if any(type(i) is not int or not 0 <= i < len(rows) for i in payload[field]):
            raise ValueError('Invalid candidate stage index.')
    if payload['screening_input_order'] != payload['retained_order'] + payload['salary_eligible_order']:
        raise ValueError('Screening input order does not match the captured stages.')
    if not set(payload['salary_eligible_order']) <= set(payload['generated_order']):
        raise ValueError('Salary eligibility is outside the generated bank.')
    if not set(payload['shortlisted']) <= set(payload['coarse_order']):
        raise ValueError('Shortlist is outside the scored bank.')
    from nfl_simulation import player_key
    lookup = {player_key(p): p for p in payload['players']}
    if len(lookup) != len(payload['players']): raise ValueError('Ambiguous frozen player identities.')
    signatures = set()
    from showdown_simulation import validate_showdown_lineup
    from optimizers import ShowdownLineup
    for row in rows:
        signature = tuple(row['signature'])
        if len(signature) != 6 or not signature[0].startswith('CPT:'):
            raise ValueError('Invalid audit roster slots.')
        keys = [signature[0][4:]] + list(signature[1:])
        if len(set(keys)) != 6 or any(k not in lookup for k in keys) or signature in signatures:
            raise ValueError('Invalid or duplicate audit roster identity.')
        signatures.add(signature)
        validate_showdown_lineup(ShowdownLineup(lookup[keys[0]], [lookup[k] for k in keys[1:]]),
                                payload['players'], payload['context']['salary_cap'])
    return envelope
