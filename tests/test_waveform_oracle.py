"""Independent interval arithmetic oracle; no production interpolation helpers."""

import random
from fractions import Fraction

from regressistor.waveform import Waveform, WaveformPolicy, compare_waveforms


def interpolate(xs, ys, x):
    for index in range(len(xs) - 1):
        if xs[index] <= x <= xs[index + 1]:
            # Barycentric formula deliberately differs from the production cursor.
            width = xs[index + 1] - xs[index]
            return ((xs[index + 1] - x) * ys[index] + (x - xs[index]) * ys[index + 1]) / width
    raise AssertionError("oracle cannot extrapolate")


def test_two_hundred_nonuniform_traces_match_independent_rational_interval_oracle() -> None:
    rng = random.Random(871122)
    for _ in range(200):
        bx = tuple(map(Fraction, (0, *sorted(rng.sample(range(1, 8), rng.randrange(7))), 8)))
        cx = tuple(map(Fraction, (0, *sorted(rng.sample(range(1, 8), rng.randrange(7))), 8)))
        by = tuple(Fraction(rng.randrange(-32, 33), 4) for _ in bx)
        cy = tuple(Fraction(rng.randrange(-32, 33), 4) for _ in cx)
        absolute = rng.choice((Fraction(0), Fraction(1, 4), Fraction(2)))
        relative = rng.choice((Fraction(0), Fraction(1, 8), Fraction(1, 2)))
        floor = rng.choice((Fraction(0), Fraction(1, 4), Fraction(1), Fraction(2)))
        knots = set(bx) | set(cx)
        if relative:
            for index in range(len(bx) - 1):
                if by[index] != by[index + 1]:
                    for boundary in (floor, -floor):
                        fraction = (boundary - by[index]) / (by[index + 1] - by[index])
                        if 0 < fraction < 1:
                            knots.add((1 - fraction) * bx[index] + fraction * bx[index + 1])
        evaluations = []
        for x in sorted(knots):
            baseline = interpolate(bx, by, x)
            candidate = interpolate(cx, cy, x)
            error = abs(baseline - candidate)
            excess = error - absolute - relative * max(abs(baseline), floor)
            evaluations.append((x, error, excess))
        expected_error = max(row[1] for row in evaluations)
        expected_excess = max(row[2] for row in evaluations)
        actual = compare_waveforms(
            Waveform(tuple(map(float, bx)), tuple(map(float, by)), "s", "V"),
            Waveform(tuple(map(float, cx)), tuple(map(float, cy)), "s", "V"),
            WaveformPolicy(float(absolute), float(relative), float(floor), grid="linear"),
        )
        assert actual.maximum_deviation.deviation == expected_error
        assert actual.maximum_excess.excess == expected_excess
        assert actual.maximum_excess.axis == next(
            x for x, _, e in evaluations if e == expected_excess
        )
        assert actual.evaluated_breakpoints == len(knots)
        assert actual.failing_breakpoints == sum(excess > 0 for _, _, excess in evaluations)
        assert actual.passed is (expected_excess <= 0)

        # A second oracle samples interior rational points, verifying that the
        # breakpoint proof does not miss a greater continuous excess.
        ordered = sorted(knots)
        for left, right in zip(ordered, ordered[1:], strict=False):
            for fraction in (Fraction(1, 7), Fraction(1, 2), Fraction(6, 7)):
                x = (1 - fraction) * left + fraction * right
                b = interpolate(bx, by, x)
                c = interpolate(cx, cy, x)
                assert abs(b - c) - absolute - relative * max(abs(b), floor) <= expected_excess


def test_collinear_refinement_preserves_continuous_result() -> None:
    policy = WaveformPolicy(relative=0.25, relative_floor=0.5, grid="linear")
    baseline = Waveform((0, 4, 8), (-2, 0, 2), "s", "V")
    candidate = Waveform((0, 8), (-1, 3), "s", "V")
    first = compare_waveforms(baseline, candidate, policy)
    refined = compare_waveforms(
        Waveform((0, 2, 4, 6, 8), (-2, -1, 0, 1, 2), "s", "V"),
        Waveform((0, 2, 4, 6, 8), (-1, 0, 1, 2, 3), "s", "V"),
        policy,
    )
    assert refined.status == first.status
    assert refined.maximum_deviation.deviation == first.maximum_deviation.deviation
    assert refined.maximum_excess == first.maximum_excess
