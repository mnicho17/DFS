"""Synthetic-only DST marginal specification. No production imports or random draws."""
import math
from statistics import NormalDist

VERSION = 'dst-marginal-specification-v1'
FLOOR = -4.0
SPREAD_FACTORS = (0.75, 1.0, 1.25)
FORECAST_GRID = (0., 2., 5., 10., 20.)
_NORMAL = NormalDist()


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError('Finite numeric input required.')
    return float(value)


def moments(location, spread):
    """Analytic moments of max(-4, location + spread * standard normal)."""
    location = _finite(location)
    spread = _finite(spread)
    if not -10000 <= location <= 10000 or not 0 < spread <= 200:
        raise ValueError('Synthetic location/spread budget exceeded.')
    d = location - FLOOR
    z = d / spread
    p = _NORMAL.cdf(z)
    density = math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)
    excess = d * p + spread * density
    second = (d * d + spread * spread) * p + d * spread * density
    return dict(mean=FLOOR + excess, sd=math.sqrt(max(0., second - excess * excess)),
                floor_probability=_NORMAL.cdf(-z))


def specification(forecast, factor=1., *, cancelled=lambda: False):
    """Solve latent location, preserving supplied mean and fixed legal lower bound.

    Factor changes latent spread, not claimed realized SD. These sensitivity
    factors are declared independently of historical outcomes. Never calibrates.
    """
    def check():
        if cancelled():
            raise InterruptedError('DST marginal specification cancelled; no partial result.')
    check()
    forecast = _finite(forecast)
    factor = _finite(factor)
    if not FLOOR <= forecast <= 100 or factor not in SPREAD_FACTORS:
        raise ValueError('Unsupported synthetic forecast or spread factor.')
    spread = factor * max(3., .8 * max(.1, forecast))
    if forecast == FLOOR:
        result = dict(forecast=forecast, factor=factor, latent_spread=0., location=FLOOR,
                      mean=FLOOR, sd=0., floor_probability=1., p10=FLOOR, p90=FLOOR,
                      probability_ge_8=0.)
    else:
        lo, hi = FLOOR - 40 * spread, forecast
        for _ in range(100):
            check()
            mid = (lo + hi) / 2
            if moments(mid, spread)['mean'] < forecast:
                lo = mid
            else:
                hi = mid
        location = (lo + hi) / 2
        result = dict(forecast=forecast, factor=factor, latent_spread=spread, location=location,
                      **moments(location, spread),
                      p10=max(FLOOR, location + spread * _NORMAL.inv_cdf(.1)),
                      p90=max(FLOOR, location + spread * _NORMAL.inv_cdf(.9)),
                      probability_ge_8=_NORMAL.cdf((location - 8.) / spread))
        if abs(result['mean'] - forecast) > 1e-9:
            raise ValueError('Analytic forecast mean did not reconcile.')
    check()
    return result


def synthetic_grid(*, cancelled=lambda: False):
    """Fixed 15-cell specification; no historical files or outcome arguments."""
    rows = [specification(mean, factor, cancelled=cancelled)
            for mean in FORECAST_GRID for factor in SPREAD_FACTORS]
    if cancelled():
        raise InterruptedError('DST marginal specification cancelled; no partial result.')
    return dict(version=VERSION, synthetic_only=True, floor=FLOOR, rows=rows)
