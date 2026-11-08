"""Explicit research-only exact-score reader; no standings completeness claims."""
import hashlib
import io
import math
import tempfile
import time
from decimal import Decimal
from pathlib import Path
import analysis_imports as ai
import hindsight_evidence as he

LIMITS = dict(file_bytes=128 * 1024 * 1024, rows=1_000_000, seconds=60)
CHUNK = 64 * 1024


class _Budget:
    def __init__(self, limits, cancelled):
        self.limits = limits
        self.cancelled = cancelled
        self.started = time.monotonic()
        self.bytes = 0

    def check(self):
        ai._check(self.cancelled)
        if time.monotonic() - self.started >= self.limits['seconds']:
            raise ValueError('Research outcome elapsed-time budget exceeded; no partial evaluation.')

    def consume(self, amount):
        self.check()
        self.bytes += amount
        # Capture, parse (including dialect sample reread), final verification.
        if self.bytes > 3 * self.limits['file_bytes'] + CHUNK:
            raise ValueError('Research outcome total-byte budget exceeded; no partial evaluation.')


def _capture(source, budget, target=None):
    digest = hashlib.sha256()
    count = 0
    budget.check()
    with Path(source['snapshot']).open('rb') as handle:
        while True:
            budget.check()
            block = handle.read(min(CHUNK, budget.limits['file_bytes'] - count + 1))
            budget.consume(len(block))
            if not block:
                break
            count += len(block)
            if count > budget.limits['file_bytes']:
                raise ValueError('Research outcome file-byte budget exceeded; no partial evaluation.')
            digest.update(block)
            if target is not None:
                target.write(block)
    budget.check()
    if digest.hexdigest() != source['hash']:
        raise ValueError('Research outcome original source changed; evaluation withheld.')


class _CheckedRaw(io.RawIOBase):
    """Check cancellation/time/bytes during text decoding, including CSV records."""
    def __init__(self, handle, budget):
        self.handle, self.budget = handle, budget

    def readable(self):return True
    def seekable(self):return True
    def tell(self):return self.handle.tell()
    def seek(self, offset, whence=0):
        self.budget.check()
        return self.handle.seek(offset, whence)
    def readinto(self, buffer):
        self.budget.check()
        block = self.handle.read(min(len(buffer), CHUNK))
        self.budget.consume(len(block))
        buffer[:len(block)] = block
        return len(block)


class ResearchScoreReader:
    def __init__(self, *, file_bytes=LIMITS['file_bytes'], rows=LIMITS['rows'], seconds=LIMITS['seconds']):
        if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in (file_bytes, rows)):
            raise ValueError('Positive integer research byte/row budgets required.')
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds <= 0:
            raise ValueError('Positive finite research time budget required.')
        self.limits = dict(file_bytes=file_bytes, rows=rows, seconds=seconds)

    def verify(self, source, cancelled=lambda:False):
        _capture(source, _Budget(self.limits, cancelled))

    def scores(self, source, manifest, cancelled=lambda:False):
        budget = _Budget(self.limits, cancelled)
        universe = he._universe(manifest)
        with tempfile.TemporaryFile(mode='w+b') as capture:
            _capture(source, budget, capture)
            capture.seek(0)
            with io.TextIOWrapper(io.BufferedReader(_CheckedRaw(capture, budget)), encoding='utf-8-sig', newline='') as handle:
                reader = ai._reader(handle)
                header = ai._header(next(reader, []))
                def rows():
                    for index, cells in enumerate(reader):
                        budget.check()
                        if index >= self.limits['rows']:
                            raise ValueError('Research outcome CSV row budget exceeded; no partial evaluation.')
                        yield index + 2, dict(zip(header, cells))
                    budget.check()
                values, _, errors, _ = he._score_observations(rows(), universe, 'showdown', budget.check)
            if errors:
                raise ValueError('Exact actual-score evidence invalid: ' + ', '.join(sorted(errors)))
            # Same exact role-equivalence and selected-source coverage as the legacy reader.
            base, observations = {}, {}
            for player in universe:
                budget.check()
                b = values.get((player['key'], 'BASE'), set())
                c = values.get((player['key'], 'CPT'), set())
                if len(b) > 1 or len(c) > 1 or any(cv != bv * Decimal('1.5') for cv in c for bv in b):
                    raise ValueError('Conflicting exact player/role actual scores.')
                key = (ai._name(player['name']), player['team'], player['position'], player['game'])
                if len(b) == 1:
                    base[key] = observations[key] = next(iter(b))
                elif len(c) == 1:
                    observations[key] = next(iter(c)) / Decimal('1.5')
            _capture(source, budget)
            budget.check()
            return universe, base, observations
