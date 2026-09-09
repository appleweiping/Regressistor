"""Independent boundary and physical-model oracles for waveform reports."""

import hashlib
import math
import random
import struct
from dataclasses import replace
from decimal import Context, Decimal, localcontext
from fractions import Fraction

import pytest

from regressistor.waveform import (
    Waveform,
    WaveformError,
    WaveformPolicy,
    compare_waveforms,
)
from regressistor.waveform_json import (
    dumps_waveform,
    loads_waveform,
    waveform_comparison_data,
    waveform_policy_data,
)


class _EqualsEveryMode:
    def __eq__(self, other: object) -> bool:
        return True


@pytest.mark.parametrize("field", ["grid", "domain"])
def test_api_policy_rejects_nontext_mode_with_permissive_equality(field: str) -> None:
    # Construction must fail at the typed boundary, not leak a later JSON
    # TypeError or let an object choose its own interpretation of the policy.
    with pytest.raises(WaveformError):
        waveform_policy_data(WaveformPolicy(**{field: _EqualsEveryMode()}))  # type: ignore[arg-type]


@pytest.mark.parametrize("side", ["left", "right"])
def test_report_rejects_positive_exclusions_from_both_traces_at_same_endpoint(side: str) -> None:
    # An intersection endpoint belongs to at least one input trace. Therefore
    # both left tails (or both right tails) cannot simultaneously be positive.
    baseline = Waveform((0, 3), (0, 3), "s", "V")
    candidate = Waveform((1, 4), (1, 4), "s", "V")
    result = compare_waveforms(
        baseline,
        candidate,
        WaveformPolicy(grid="linear", domain="intersection"),
    )
    forged = (
        replace(result, excluded_candidate=(Fraction(1), Fraction(1)))
        if side == "left"
        else replace(result, excluded_baseline=(Fraction(1), Fraction(1)))
    )
    with pytest.raises(WaveformError, match="intersection|exclusion|tail|domain"):
        waveform_comparison_data(forged)


def test_report_rejects_contradictory_observations_at_one_axis() -> None:
    baseline = Waveform((0, 1, 2), (0, 0, 0), "s", "V")
    candidate = Waveform((0, 1, 2), (1, 2, 3), "s", "V")
    result = compare_waveforms(baseline, candidate)
    # The report already includes candidate(0) == 1 in diagnostics. It cannot
    # also claim candidate(0) == 3 as its maximum-excess witness.
    impossible_maximum = replace(result.maximum_excess, axis=Fraction(0))
    forged = replace(result, maximum_excess=impossible_maximum)
    with pytest.raises(WaveformError, match="observation|witness|diagnostic|consistent"):
        waveform_comparison_data(forged)


def test_report_rejects_contradictory_maxima_pair_at_one_axis() -> None:
    baseline = Waveform((0, 1), (0, 0), "s", "V")
    candidate = Waveform((0, 1), (0, 0), "s", "V")
    result = compare_waveforms(baseline, candidate, WaveformPolicy(absolute=1))
    forged = replace(
        result,
        maximum_excess=replace(result.maximum_excess, baseline=Fraction(1), candidate=Fraction(1)),
    )
    with pytest.raises(WaveformError, match="observation|witness|consistent"):
        waveform_comparison_data(forged)


def test_report_cannot_invent_a_fourth_failing_witness_when_all_three_are_reported() -> None:
    baseline = Waveform((0, 1, 2), (0, 0, 0), "s", "V")
    candidate = Waveform((0, 1, 2), (1, 2, 3), "s", "V")
    result = compare_waveforms(baseline, candidate)
    extra_failure = replace(result.maximum_excess, axis=Fraction(1, 2), candidate=Fraction(10))
    forged = replace(result, maximum_excess=extra_failure, maximum_deviation=extra_failure)
    with pytest.raises(WaveformError, match="count|witness|diagnostic|consistent"):
        waveform_comparison_data(forged)


def test_binary64_wire_preserves_independent_ieee_bit_patterns_and_identity() -> None:
    rng = random.Random(7719201)
    raw_bits = [0, 1, 0x000FFFFFFFFFFFFF, 0x0010000000000000, 0x7FEFFFFFFFFFFFFF]
    while len(raw_bits) < 256:
        bits = rng.getrandbits(64)
        if (bits >> 52) & 0x7FF != 0x7FF:
            raw_bits.append(bits)
    raw_bits.extend(bits | (1 << 63) for bits in tuple(raw_bits))
    values = tuple(struct.unpack(">d", bits.to_bytes(8, "big"))[0] for bits in raw_bits)
    original = Waveform(tuple(range(len(values))), values, "ms", "µV", "wire oracle")
    restored = loads_waveform(dumps_waveform(original))

    # Reconstruct the framing independently from IEEE bytes; negative zero is
    # explicitly normalized by the input profile rather than preserved.
    frame = bytearray(b"org.regressistor.waveform\x00v1\x00")
    for text in ("wire oracle", "ms", "µV"):
        encoded = text.encode("utf-8")
        frame += struct.pack(">I", len(encoded)) + encoded
    frame += struct.pack(">I", len(values))
    for index, (value, restored_value) in enumerate(zip(values, restored.values, strict=True)):
        expected = 0.0 if value == 0 else value
        assert struct.pack(">d", restored_value) == struct.pack(">d", expected)
        frame += struct.pack(">dd", float(index), expected)
    assert restored.identity == hashlib.sha256(frame).hexdigest()


def _rc_step_at(tau_units: Fraction) -> float:
    """Solve the 1 V, zero-initial-charge RC step analytically at high precision."""
    with localcontext(Context(prec=80)):
        time = Decimal(tau_units.numerator) / Decimal(tau_units.denominator)
        return float(Decimal(1) - (-time).exp())


def test_original_rc_step_sampling_has_the_predicted_concavity_error() -> None:
    # R = 1 kOhm, C = 1 uF gives tau = 1 ms. The comparator checks the two
    # sampled piecewise-linear traces, not the underlying exponential itself.
    coarse_time = tuple(Fraction(index) for index in range(5))
    fine_time = tuple(Fraction(index, 2) for index in range(9))
    baseline = Waveform(
        tuple(map(float, coarse_time)), tuple(map(_rc_step_at, coarse_time)), "ms", "V"
    )
    candidate = Waveform(
        tuple(map(float, fine_time)), tuple(map(_rc_step_at, fine_time)), "ms", "V"
    )
    result = compare_waveforms(baseline, candidate, WaveformPolicy(grid="linear"))
    first_midpoint_error = Fraction(candidate.values[1]) - Fraction(baseline.values[1]) / 2
    assert result.maximum_deviation.axis == Fraction(1, 2)
    assert result.maximum_deviation.deviation == first_midpoint_error
    assert Fraction(7, 100) < first_midpoint_error < Fraction(8, 100)
    # |f''| <= 1 in normalized time gives the independent h^2 / 8 bound.
    assert first_midpoint_error < Fraction(1, 8)
    assert compare_waveforms(
        baseline, candidate, WaveformPolicy(absolute=0.08, grid="linear")
    ).passed
    assert not compare_waveforms(
        baseline, candidate, WaveformPolicy(absolute=0.07, grid="linear")
    ).passed


@pytest.mark.parametrize(
    ("component", "pass_budget", "fail_budget"),
    [("real", 0.06, 0.04), ("imaginary", 0.16, 0.14)],
)
def test_original_rc_ac_components_match_rational_transfer_function(
    component: str, pass_budget: float, fail_budget: float
) -> None:
    # H(jw) = 1 / (1 + j*w*R*C). Use the dimensionless coordinate w*R*C;
    # every analytic sample is rational before its one binary64 projection.
    def response(omega_tau: Fraction) -> float:
        numerator = Fraction(1) if component == "real" else -omega_tau
        return float(numerator / (1 + omega_tau**2))

    coarse = tuple(map(Fraction, (0, 1, 2, 4)))
    fine = (
        Fraction(0),
        Fraction(1, 2),
        Fraction(1),
        Fraction(3, 2),
        Fraction(2),
        Fraction(3),
        Fraction(4),
    )
    baseline = Waveform(tuple(map(float, coarse)), tuple(map(response, coarse)), "1", "1")
    candidate = Waveform(tuple(map(float, fine)), tuple(map(response, fine)), "1", "1")
    result = compare_waveforms(baseline, candidate, WaveformPolicy(grid="linear"))
    first_midpoint_error = abs(
        Fraction(candidate.values[1])
        - (Fraction(baseline.values[0]) + Fraction(baseline.values[1])) / 2
    )
    assert result.maximum_deviation.axis == Fraction(1, 2)
    assert result.maximum_deviation.deviation == first_midpoint_error
    assert math.isfinite(float(first_midpoint_error))
    assert compare_waveforms(
        baseline, candidate, WaveformPolicy(absolute=pass_budget, grid="linear")
    ).passed
    assert not compare_waveforms(
        baseline, candidate, WaveformPolicy(absolute=fail_budget, grid="linear")
    ).passed
