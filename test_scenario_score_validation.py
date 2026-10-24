"""Cached-score validation keeps exact values and rejects malformed full frames."""
from test_environment import install
install()
from array import array
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zlib
from scenario_cache import ScenarioReplay, _digest

class ScoreValidationTests(unittest.TestCase):
    def decode(self, values, count=None):
        replay = ScenarioReplay.__new__(ScenarioReplay)
        replay.keys = {'x'}
        replay.field_sizes = {len(values) if count is None else count}
        payload = zlib.compress(json.dumps([{'x': 0.0}, {'normal': 1}]).encode())
        return replay._decode(payload, zlib.compress(array('d', values).tobytes()))

    def test_sorted_finite_values_and_ties_keep_exact_bits(self):
        values = [-1e300, -2.5, -0.0, 0.0, 0.0, 1e-300, 1e300]
        outcomes, ranked, scripts = self.decode(values)
        self.assertEqual(ranked.tobytes(), array('d', values).tobytes())
        self.assertEqual(outcomes, {'x': 0.0})
        self.assertEqual(scripts, {'normal': 1})

    def test_nonfinite_values_at_every_position_reject(self):
        for value in (math.nan, math.inf, -math.inf):
            for position in range(5):
                values = [-2.0, -1.0, 0.0, 1.0, 2.0]
                values[position] = value
                with self.subTest(value=value, position=position), self.assertRaisesRegex(ValueError, 'Invalid cached opponent scores'):
                    self.decode(values)

    def test_early_middle_and_final_inversions_reject(self):
        for index in range(4):
            values = [0., 1., 2., 3., 4.]
            values[index], values[index+1] = values[index+1], values[index]
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, 'Invalid cached opponent scores'):
                self.decode(values)

    def test_wrong_coverage_and_oversized_scores_still_reject(self):
        for values, count in (([], 3), ([1.0, 2.0], 3), ([1.0]*4, 3)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.decode(values, count)

    def test_compression_trailer_and_truncation_still_reject(self):
        replay = ScenarioReplay.__new__(ScenarioReplay)
        replay.keys = {'x'}; replay.field_sizes = {3}
        payload = zlib.compress(json.dumps([{'x': 0}, {}]).encode())
        scores = zlib.compress(array('d', [1., 2., 3.]).tobytes())
        for invalid in (scores+b'extra', scores[:-2]):
            with self.assertRaises(ValueError): replay._decode(payload, invalid)

    def test_invalid_frame_with_valid_digest_recomputes_before_any_hit(self):
        # Recalculate the digest so semantic validation, not merely the checksum,
        # must reject even a final-frame inversion or nonfinite score.
        for invalid in ([2., 1., 3.], [1., 2., math.nan], [1., 2., math.inf]):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as folder:
                options = dict(kind='showdown', seed=13, count=2, enabled=True, folder=folder)
                players = [dict(Name='Synthetic', FlexID='x')]
                fields = [['a', 'b', 'c']]
                cold = ScenarioReplay(players, fields, **options)
                for index in range(2): cold.frame(index, lambda: ({'x': 0.0}, [1., 2., 3.]), {})
                self.assertEqual(cold.finish(2)['status'], 'saved')
                path = next(Path(folder).glob('*.sqlite'))
                with sqlite3.connect(path) as connection:
                    connection.execute('UPDATE frames SET scores=? WHERE id=1', (zlib.compress(array('d', invalid).tobytes()),))
                    digest = hashlib.sha256()
                    for _, payload, scores in connection.execute('SELECT id,payload,scores FROM frames ORDER BY id'):
                        _digest(digest, payload, scores)
                    connection.execute("UPDATE meta SET value=? WHERE key='digest'", (digest.hexdigest(),))
                connection.close()
                warm = ScenarioReplay(players, fields, **options)
                try:
                    self.assertEqual(warm.hits, 0)
                    self.assertIsNone(warm.reader)
                    self.assertIn('failed integrity', warm.reason)
                    calls = []
                    def compute(): calls.append(1); return {'x': 0.0}, [1., 2., 3.]
                    self.assertEqual(warm.frame(0, compute, {}), ({'x': 0.0}, [1., 2., 3.]))
                    self.assertEqual(calls, [1])
                finally: warm.finish(0)

if __name__ == '__main__': unittest.main()
