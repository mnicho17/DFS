"""Disposable, exact-input scenario replay. No predictions or lineup selections are cached."""
from array import array
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import struct
import sys
import tempfile
import zlib

from build_snapshots import fingerprint

FILE_LIMIT = 512 * 1024 * 1024
STORE_LIMIT = 2 * 1024 * 1024 * 1024


def cache_folder():
    override = os.environ.get('DFS_OPTIMIZER_DATA_DIR')
    base = Path(override) if override else Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'DFS Optimizer'
    return base / 'scenario-cache'


def _digest(digest, payload, scores):
    digest.update(struct.pack('<QQ', len(payload), len(scores)))
    digest.update(payload); digest.update(scores)


class ScenarioReplay:
    def __init__(self, players, fields, *, kind, seed, count, enabled=False, folder=None, cancelled=None, context=None):
        self.connection = None; self.temporary = None; self.reader = None
        self.count = count; self.next_index = 0; self.hits = 0; self.written = 0
        self.status = 'disabled'; self.reason = ''; self.keys = set()
        self.cancelled = cancelled or (lambda: False)
        if not enabled:return
        try:
            from candidate_library import code_id
            self.key = fingerprint(dict(schema=1, players=players, fields=fields, kind=kind,
                seed=seed, count=count, model=code_id(), byteorder=sys.byteorder, python=sys.version, context=context))
            from nfl_simulation import player_key
            self.keys = {player_key(p) for p in players}
            self.field_sizes = {len(field) for field in fields}
            self.root = (Path(folder) if folder else cache_folder()).resolve()
            self.root.mkdir(parents=True, exist_ok=True)
            self.target = self.root / (self.key + '.sqlite')
            if self.target.exists():
                try:
                    if self.target.stat().st_size > FILE_LIMIT:raise ValueError('Cache exceeds size limit')
                    con = sqlite3.connect(self.target.as_uri() + '?mode=ro', uri=True)
                    self.connection = con
                    con.execute('BEGIN')
                    meta = dict(con.execute('SELECT key,value FROM meta'))
                    if meta.get('key') != self.key or int(meta.get('count', -1)) != count:
                        raise ValueError('Cache identity mismatch')
                    digest = hashlib.sha256(); seen = 0
                    for index, payload, scores in con.execute('SELECT id,payload,scores FROM frames ORDER BY id'):
                        if self.cancelled():raise ValueError('Cancelled during cache verification')
                        if index != seen or seen >= count:raise ValueError('Cache frame sequence mismatch')
                        self._decode(payload, scores)
                        _digest(digest, payload, scores); seen += 1
                    if seen != count or digest.hexdigest() != meta.get('digest'):
                        raise ValueError('Cache content verification failed')
                    self.reader = iter(con.execute('SELECT id,payload,scores FROM frames ORDER BY id'))
                    self.status = 'reused'
                    return
                except Exception as exc:
                    self.close(); self.reason = str(exc)
            if self.cancelled():return
            self.available = STORE_LIMIT - sum(p.stat().st_size for p in self.root.iterdir() if p.is_file())
            if self.available < 8*1024*1024:
                self.status = 'not saved'; self.reason = 'Cache storage limit reached'; return
            fd, name = tempfile.mkstemp(dir=self.root, suffix='.partial'); os.close(fd)
            self.temporary = Path(name)
            self.connection = sqlite3.connect(name)
            self.connection.execute('CREATE TABLE frames(id INTEGER PRIMARY KEY,payload BLOB,scores BLOB)')
            self.connection.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)')
            self.digest = hashlib.sha256(); self.status = 'recording'
        except Exception as exc:
            self.status = 'unavailable'; self.reason = str(exc); self.close()

    def _decode(self, payload, scores):
        # Bounded decompression prevents corrupted local data from allocating arbitrary memory.
        decoder = zlib.decompressobj()
        raw = decoder.decompress(payload, 4*1024*1024)
        if not decoder.eof or decoder.unused_data:raise ValueError('Invalid cached scenario payload')
        outcomes, scripts = json.loads(raw)
        ranked = array('d'); ranked.frombytes(scores)
        if set(outcomes) != self.keys or len(ranked) not in self.field_sizes:
            raise ValueError('Cached player or field coverage mismatch')
        if any(not isinstance(v, (int,float)) or not math.isfinite(v) for v in outcomes.values()):
            raise ValueError('Invalid cached player score')
        if any(not math.isfinite(v) for v in ranked) or any(a>b for a,b in zip(ranked,ranked[1:])):
            raise ValueError('Invalid cached opponent scores')
        if not isinstance(scripts,dict) or any(not isinstance(v,int) or v<0 for v in scripts.values()):
            raise ValueError('Invalid cached script counts')
        return outcomes, ranked, scripts

    def frame(self, index, compute, scripts):
        if self.reader is not None:
            saved_index, payload, scores = next(self.reader)
            if saved_index != index:raise ValueError('Scenario replay order changed')
            outcomes, ranked, counts = self._decode(payload,scores)
            scripts.clear(); scripts.update(counts); self.hits += 1
            return outcomes, ranked
        outcomes, ranked = compute()
        if self.connection is not None:
            try:
                payload = zlib.compress(json.dumps([outcomes,dict(scripts)],allow_nan=False,separators=(',',':')).encode())
                scores = array('d',ranked).tobytes()
                self.connection.execute('INSERT INTO frames VALUES (?,?,?)',(index,payload,scores))
                _digest(self.digest,payload,scores); self.written += 1
                if index % 100 == 0:
                    self.connection.commit()
                    if self.temporary.stat().st_size > min(FILE_LIMIT,self.available):
                        raise ValueError('Cache storage limit reached')
            except Exception as exc:
                self.status = 'not saved'; self.reason = str(exc); self.close()
        return outcomes, ranked

    def finish(self, completed):
        try:
            if self.temporary is not None and completed == self.count and self.written == self.count:
                self.connection.executemany('INSERT INTO meta VALUES (?,?)',
                    [('key',self.key),('count',str(self.count)),('digest',self.digest.hexdigest())])
                self.connection.commit(); self.connection.close(); self.connection = None
                if self.temporary.stat().st_size > min(FILE_LIMIT,self.available):raise ValueError('Cache storage limit reached')
                os.replace(self.temporary,self.target); self.temporary = None; self.status = 'saved'
            elif self.temporary is not None:
                self.status = 'not saved'; self.reason = 'Incomplete simulation'
        except Exception as exc:
            self.status = 'not saved'; self.reason = str(exc)
        finally:self.close()
        return dict(status=self.status,reused_scenarios=self.hits,requested_scenarios=self.count,reason=self.reason)

    def close(self):
        if self.connection is not None:
            try:self.connection.close()
            except sqlite3.Error:pass
            self.connection = None; self.reader = None
        if self.temporary is not None:
            try:self.temporary.unlink(missing_ok=True)
            except OSError:pass
            self.temporary = None

    def __del__(self):
        try:self.close()
        except Exception:pass
