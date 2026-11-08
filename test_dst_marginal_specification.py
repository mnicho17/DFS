"""Synthetic mathematical checks only; no history access or production changes."""
from test_environment import install
install()
import math
import random
import unittest
from scripts.dst_marginal_specification import specification, synthetic_grid, moments, FLOOR


class DstMarginalSpecificationTests(unittest.TestCase):
    def test_fixed_grid_means_bounds_and_reproducibility(self):
        result = synthetic_grid()
        self.assertEqual(result, synthetic_grid())
        self.assertEqual(len(result['rows']), 15)
        for row in result['rows']:
            self.assertAlmostEqual(row['mean'], row['forecast'], places=9)
            self.assertGreaterEqual(row['p10'], FLOOR)
            self.assertGreaterEqual(row['p90'], row['p10'])
            self.assertTrue(0 <= row['floor_probability'] <= 1)
            self.assertTrue(0 <= row['probability_ge_8'] <= 1)

    def test_half_normal_closed_form_oracle(self):
        result = moments(-4., 3.)
        self.assertAlmostEqual(result['mean'], -4 + 3 / math.sqrt(2 * math.pi))
        self.assertAlmostEqual(result['sd'] ** 2, 9 * (.5 - 1 / (2 * math.pi)))
        self.assertEqual(result['floor_probability'], .5)

    def test_independent_density_integration_oracle(self):
        # Midpoint integration in latent z, independent of analytic moment code.
        row = specification(2., 1.25)
        count = 40000
        step = 16 / count
        total = second = 0.
        for i in range(count):
            z = -8 + (i + .5) * step
            value = max(-4., row['location'] + row['latent_spread'] * z)
            weight = math.exp(-z * z / 2) / math.sqrt(2 * math.pi) * step
            total += value * weight
            second += value * value * weight
        self.assertAlmostEqual(total, 2., places=6)
        self.assertAlmostEqual(math.sqrt(second - total * total), row['sd'], places=6)

    def test_floor_forecast_is_degenerate(self):
        for factor in (.75, 1., 1.25):
            row = specification(-4., factor)
            self.assertEqual((row['mean'], row['sd'], row['p90']), (-4., 0., -4.))
            self.assertEqual(row['floor_probability'], 1.)

    def test_near_floor_and_high_supported_forecast(self):
        for mean in (-4 + 1e-7, 100.):
            self.assertAlmostEqual(specification(mean)['mean'], mean, places=9)

    def test_no_global_rng_consumption(self):
        state = random.getstate()
        synthetic_grid()
        self.assertEqual(random.getstate(), state)

    def test_invalid_input_and_budget(self):
        for value in (True, None, '5', float('nan'), float('inf'), -4.01, 100.01):
            with self.assertRaises(ValueError):
                specification(value)
        for factor in (True, 0., 2., float('nan')):
            with self.assertRaises(ValueError):
                specification(5., factor)
        for location, spread in ((10001, 1), (0, 0), (0, 201), (False, 1)):
            with self.assertRaises(ValueError):
                moments(location, spread)

    def test_cancel_before_during_and_final(self):
        for stop in (1, 20, 102):
            count = 0
            def cancelled():
                nonlocal count
                count += 1
                return count >= stop
            with self.assertRaises(InterruptedError):
                specification(5., cancelled=cancelled)

    def test_cancel_grid_withholds_result(self):
        count = 0
        def cancelled():
            nonlocal count
            count += 1
            return count >= 1531
        # 15 * 102 checks plus final grid check.
        with self.assertRaises(InterruptedError):
            synthetic_grid(cancelled=cancelled)

    def test_clipping_requires_location_correction(self):
        for mean in (0., 2., 5.):
            row = specification(mean)
            self.assertGreater(moments(mean, row['latent_spread'])['mean'], mean)
            self.assertLess(row['location'], mean)
