"""Functions for Catmull-Rom splines."""

from typing import Tuple

ALPHA = 0.5
"""
alpha: Parametric constant:

  - 0.0 for the uniform spline,
  - 0.5 for the centripetal spline,
  - 1.0 for the chordal spline.
"""


def _tj(pi, pj) -> float:
    chord_len = abs(pj - pi)
    assert chord_len > 0.0
    return chord_len**ALPHA


def parametric(
    p0: complex, p1: complex, p2: complex, p3: complex
) -> Tuple[float, float, float, float]:
    """Calculate the time parameters of a non-uniform catmull spline."""
    t0 = 0.0
    t1 = _tj(p0, p1) + t0
    t2 = _tj(p1, p2) + t1
    t3 = _tj(p2, p3) + t2

    return t0, t1, t2, t3


def parametric3(p0: complex, p1: complex, p2: complex) -> Tuple[float, float, float]:
    """Calculate the time parameters of a non-uniform catmull spline."""
    t0 = 0.0
    t1 = _tj(p0, p1) + t0
    t2 = _tj(p1, p2) + t1

    return t0, t1, t2


def ccatmull(t: float, p0: complex, p1: complex, p2: complex, p3: complex) -> complex:
    """
    Interpolate a point along a catmull-rom spline.

    t must be a float in the range 0..1
    p0, p1, p2, p3 are points as complex numbers
    returns interpolated point as complex number
    """
    assert 0 <= t <= 1

    t0, t1, t2, t3 = parametric(p0, p1, p2, p3)

    t = t1 + t * (t2 - t1)

    A1 = (t1 - t) / (t1 - t0) * p0 + (t - t0) / (t1 - t0) * p1
    A2 = (t2 - t) / (t2 - t1) * p1 + (t - t1) / (t2 - t1) * p2
    A3 = (t3 - t) / (t3 - t2) * p2 + (t - t2) / (t3 - t2) * p3

    B1 = (t2 - t) / (t2 - t0) * A1 + (t - t0) / (t2 - t0) * A2
    B2 = (t3 - t) / (t3 - t1) * A2 + (t - t1) / (t3 - t1) * A3

    C = (t2 - t) / (t2 - t1) * B1 + (t - t1) / (t2 - t1) * B2

    return C


def catmull_tangent(p0: complex, p1: complex, p2: complex) -> complex:
    """
    Calculate the tangent of a catmull spline at the control point p1.

    See: https://splines.readthedocs.io/en/latest/euclidean/catmull-rom-properties.html#Tangent-Vectors
    """
    t0, t1, t2 = parametric3(p0, p1, p2)

    v0 = (p1 - p0) / (t1 - t0)
    v1 = (p2 - p1) / (t2 - t1)

    return ((t2 - t1) * v0 + (t1 - t0) * v1) / (t2 - t0)


def ccatmull_diff(
    t: float, p0: complex, p1: complex, p2: complex, p3: complex
) -> complex:
    """
    Calculate the tangent of catmull spline at the arbitrary time t.

    This is the differential of a ccatmull.

    Derivation see: resources/catmull.ipynb
    """
    assert 0 <= t <= 1

    t0, t1, t2, t3 = parametric(p0, p1, p2, p3)

    t = t1 + t * (t2 - t1)

    delta0 = t - t0
    delta1 = t - t1
    delta2 = t - t2
    delta3 = t - t3

    return (
        (t0 - t1)
        * (t0 - t2)
        * (
            -delta1 * (t1 - t2) * (delta2 * p3 - delta3 * p2)
            + delta1
            * (
                (t1 - t2) * (delta1 * (p2 - p3) - delta2 * p3 + delta3 * p2)
                + (t2 - t3) * (delta1 * p2 - delta2 * p1 - delta3 * (p1 - p2))
            )
            + delta3 * (t2 - t3) * (delta1 * p2 - delta2 * p1)
        )
        + (t1 - t3)
        * (t2 - t3)
        * (
            delta0 * (t0 - t1) * (delta1 * p2 - delta2 * p1)
            - delta2 * (t1 - t2) * (delta0 * p1 - delta1 * p0)
            - delta2
            * (
                (t0 - t1) * (delta0 * (p1 - p2) - delta1 * p2 + delta2 * p1)
                + (t1 - t2) * (delta0 * p1 - delta1 * p0 - delta2 * (p0 - p1))
            )
        )
    ) / ((t0 - t1) * (t0 - t2) * (t1 - t2) ** 2 * (t1 - t3) * (t2 - t3))
