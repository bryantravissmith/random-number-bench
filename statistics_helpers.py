"""Small, dependency-free interval and distance helpers for public reports."""

import math
from statistics import NormalDist

CONFIDENCE = 0.95


def wilson_interval(successes, n, confidence=CONFIDENCE):
    """Pointwise Wilson score interval for a binomial proportion.

    Returns None when there are no trials. Endpoints are clamped to [0, 1]
    and are exact at the point masses 0/n and n/n.
    """
    if type(successes) is not int or type(n) is not int:
        raise TypeError("Wilson interval counts must be integers")
    if n < 0 or not 0 <= successes <= max(n, 0):
        raise ValueError("Wilson interval requires 0 <= successes <= n")
    if not 0 < confidence < 1:
        raise ValueError("Confidence must be between 0 and 1")
    if n == 0:
        return None
    z = NormalDist().inv_cdf(0.5 + confidence / 2)
    p = successes / n
    scale = 1 + z * z / n
    center = (p + z * z / (2 * n)) / scale
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / scale
    low = 0.0 if successes == 0 else max(0.0, center - half)
    high = 1.0 if successes == n else min(1.0, center + half)
    return [round(low, 12), round(high, 12)]


def tv_to_uniform(counts):
    """Total variation distance from the uniform distribution over the bins."""
    n = sum(counts)
    if not n:
        return None
    return round(sum(abs(c / n - 1 / len(counts)) for c in counts) / 2, 12)
