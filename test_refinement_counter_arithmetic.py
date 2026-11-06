"""Exercise the actual nested swap kernel against its original arithmetic."""
from test_environment import install
install()
import ast
from collections import Counter
import math
from pathlib import Path
import random
import unittest
import portfolio_rules


class RefinementCounterArithmeticTests(unittest.TestCase):
    def test_swap_kernel_preserves_exact_float_results_and_count_clamps(self):
        tree = ast.parse(Path(portfolio_rules.__file__).read_text(encoding='utf-8'))
        node = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef) and node.name == 'scenario_swap_delta')
        assignments = [statement for statement in ast.walk(tree)
                       if isinstance(statement, ast.Assign) and any(
                           isinstance(target, ast.Name) and target.id in
                           ('scenario_count_logs', 'scenario_count_deltas')
                           for target in statement.targets)]
        self.assertEqual(len(assignments), 2)
        scope = {'Counter': Counter, 'math': math, 'selected': list(range(150)),
                 'refinement_passes': 8}
        exec(compile(ast.Module(body=assignments + [node], type_ignores=[]),
                     '<actual swap kernel and tables>', 'exec'), scope)
        actual = scope['scenario_swap_delta']
        rng = random.Random(90210)
        for trial in range(200):
            counts = Counter({n: rng.randrange(-2, 151) for n in range(30)})
            outgoing = {n for n in range(40) if rng.random() < .5} if trial else set()
            incoming = {n for n in range(40) if rng.random() < .5} if trial else set()
            original = counts.copy()
            for weight in (0., 10., 18., -2.):
                touched = outgoing.union(incoming)
                delta = 0.
                for scenario in touched:
                    before = max(0, counts[scenario])
                    after = max(0, before - int(scenario in outgoing) + int(scenario in incoming))
                    delta += math.log1p(after) - math.log1p(before)
                expected = weight * (100. / max(1, len(counts))) * delta if touched else 0.
                with self.subTest(trial=trial, weight=weight):
                    self.assertEqual(actual(counts, outgoing, incoming, weight).hex(), expected.hex())
            self.assertEqual(counts, original)


if __name__ == '__main__':
    unittest.main()
