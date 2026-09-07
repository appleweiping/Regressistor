"""Run-to-run scatter, and what it does to a regression verdict.

A gate that reduces repeated measurements to one number and compares it against
a fixed budget cannot tell a real shift from noise. The same one-percent budget
is too tight for a metric that scatters three percent between identical runs and
far too loose for one that repeats to a part in ten thousand: the first fails
constantly and gets switched off, the second passes a twenty-sigma regression in
silence. Neither failure is visible in the reduced value, because reduction is
where the scatter was discarded.

This module keeps the scatter. It describes each side of a comparison, combines
the two into a standard error of their difference, and expresses the adverse
change in those units, so a policy can require a change to be both materially
large and larger than the noise before it blocks.

The combination is Welch's, which does not assume the two sides scatter equally.
A baseline measured on one machine, simulator version or seed set has no reason
to share a variance with a candidate measured on another, and pooling them would
report a precision neither side has.

Nothing here is a hypothesis test. The standardized change is reported as a
descriptive ratio and compared against a threshold the policy states outright,
rather than converted into a p-value that would invite reading a gate decision
as evidence about a population.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from regressistor.errors import InputError

#: Repeats per side below which a standard deviation is not an estimate of
#: anything. Two points always fit a spread exactly, so their deviation carries
#: one degree of freedom and swings by a factor of several between identical
#: runs. Three is the smallest count at which the number starts to mean
#: something, and it is still weak; a policy that depends on the noise gate
#: should ask for more.
DEFAULT_MIN_NOISE_SAMPLES = 3

#: Lowest `noise_min_samples` a policy may state. One sample has no spread at
#: all, so a noise gate built on it would divide by an undefined quantity.
MIN_NOISE_SAMPLES = 2


@dataclass(frozen=True, slots=True)
class Dispersion:
    """How far repeated measurements of one metric fell from each other."""

    count: int
    mean: float
    deviation: float
    span: float

    @property
    def standard_error(self) -> float:
        """Uncertainty of the mean, which shrinks as repeats are added."""

        return self.deviation / math.sqrt(self.count)

    @property
    def relative_deviation(self) -> float:
        """Deviation as a fraction of the mean, or infinity around zero.

        A metric centred on zero has no meaningful relative scatter, and
        reporting a small absolute deviation as a small relative one there
        would be the more misleading answer.
        """

        if self.mean == 0.0:
            return math.inf if self.deviation > 0.0 else 0.0
        return self.deviation / abs(self.mean)

    def as_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "mean": self.mean,
            "deviation": self.deviation,
            "span": self.span,
            "standard_error": self.standard_error,
        }


@dataclass(frozen=True, slots=True)
class NoiseAssessment:
    """The adverse change expressed in standard errors, when it can be."""

    baseline: Dispersion | None
    candidate: Dispersion
    standard_error: float | None
    standardized_change: float | None
    degrees_of_freedom: float | None
    applicable: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        """Serialize, writing an unbounded standardized change as null.

        A measurement that repeats exactly and then moves is beyond noise by an
        unbounded ratio, and JSON has no way to say so. The verdict survives in
        `reason`, and a reader can recover the case unambiguously from an
        applicable assessment whose standard error is zero.
        """

        standardized = self.standardized_change
        return {
            "baseline": self.baseline.as_dict() if self.baseline else None,
            "candidate": self.candidate.as_dict(),
            "standard_error": self.standard_error,
            "standardized_change": (
                standardized if standardized is not None and math.isfinite(standardized) else None
            ),
            "degrees_of_freedom": self.degrees_of_freedom,
            "applicable": self.applicable,
            "reason": self.reason,
        }


def describe(values: Iterable[float]) -> Dispersion:
    """Summarize repeated measurements without discarding their spread.

    The deviation is Bessel-corrected, so a single measurement reports zero
    scatter rather than a fabricated one. Zero here means "not measured", which
    the sample count is kept alongside to disambiguate.
    """

    materialized = [float(value) for value in values]
    if not materialized:
        raise InputError("cannot describe an empty measurement set")
    if any(not math.isfinite(value) for value in materialized):
        raise InputError("cannot describe non-finite measurements")
    count = len(materialized)
    mean = math.fsum(materialized) / count
    if count == 1:
        return Dispersion(count=1, mean=mean, deviation=0.0, span=0.0)
    variance = math.fsum((value - mean) ** 2 for value in materialized) / (count - 1)
    deviation = math.sqrt(variance)
    if not math.isfinite(deviation):
        raise InputError("measurement dispersion overflowed")
    return Dispersion(
        count=count,
        mean=mean,
        deviation=deviation,
        span=max(materialized) - min(materialized),
    )


def difference_standard_error(baseline: Dispersion, candidate: Dispersion) -> float:
    """Combine two dispersions into the uncertainty of their difference.

    This is Welch's form, which adds the two squared standard errors rather
    than pooling the variances. Pooling assumes both sides scatter equally, and
    a baseline frozen from an older simulator, machine or seed set has no
    reason to.
    """

    combined = baseline.standard_error**2 + candidate.standard_error**2
    result = math.sqrt(combined)
    if not math.isfinite(result):
        raise InputError("difference standard error overflowed")
    return result


def welch_degrees_of_freedom(baseline: Dispersion, candidate: Dispersion) -> float:
    """Effective degrees of freedom behind the combined standard error.

    Reported so a reader can see how thin the estimate is. Ten repeats a side
    give roughly eighteen; three a side give roughly four, and a standard error
    resting on four degrees of freedom is worth treating as an order of
    magnitude rather than a number.

    Two sides with no scatter at all have nothing to estimate, and the
    Welch-Satterthwaite ratio is zero over zero there, so it is reported as
    zero rather than as a division failure.
    """

    numerator = (baseline.standard_error**2 + candidate.standard_error**2) ** 2
    denominator = math.fsum(
        side.standard_error**4 / (side.count - 1)
        for side in (baseline, candidate)
        if side.count > 1
    )
    if denominator <= 0.0:
        return 0.0
    result = numerator / denominator
    return result if math.isfinite(result) else 0.0


def assess(
    baseline_values: Sequence[float] | None,
    candidate_values: Sequence[float],
    adverse_change: float | None,
    *,
    min_samples: int = DEFAULT_MIN_NOISE_SAMPLES,
) -> NoiseAssessment:
    """Express an adverse change in standard errors, or say why it cannot be.

    An assessment that is not applicable still reports every dispersion it
    could compute, because the numbers a policy needs in order to choose a
    noise budget are exactly the ones it has before that budget exists.

    A measurement that repeats exactly has a standard error of zero, which is
    the ordinary case for a deterministic simulator run several times. Any
    change in it is then infinitely many standard errors, and that reading is
    correct rather than a degenerate one: a quantity that reproduces to the
    last bit and then moves did not move by chance.
    """

    if min_samples < MIN_NOISE_SAMPLES:
        raise InputError(f"noise assessment needs at least {MIN_NOISE_SAMPLES} samples per side")
    candidate = describe(candidate_values)
    baseline = describe(baseline_values) if baseline_values else None

    if baseline is None:
        return NoiseAssessment(
            baseline=None,
            candidate=candidate,
            standard_error=None,
            standardized_change=None,
            degrees_of_freedom=None,
            applicable=False,
            reason="no baseline to compare against",
        )
    if adverse_change is None:
        return NoiseAssessment(
            baseline=baseline,
            candidate=candidate,
            standard_error=None,
            standardized_change=None,
            degrees_of_freedom=None,
            applicable=False,
            reason="no regression budget to assess",
        )

    standard_error = difference_standard_error(baseline, candidate)
    degrees_of_freedom = welch_degrees_of_freedom(baseline, candidate)
    thin = min(baseline.count, candidate.count)
    if thin < min_samples:
        return NoiseAssessment(
            baseline=baseline,
            candidate=candidate,
            standard_error=standard_error,
            standardized_change=None,
            degrees_of_freedom=degrees_of_freedom,
            applicable=False,
            reason=(
                f"{thin} repeat(s) on the thinner side is below the {min_samples} "
                "required to estimate run-to-run scatter"
            ),
        )
    reason = (
        "repeated measurements did not vary, so any change is beyond their scatter"
        if standard_error == 0.0
        else "assessed against run-to-run scatter"
    )
    return NoiseAssessment(
        baseline=baseline,
        candidate=candidate,
        standard_error=standard_error,
        standardized_change=standardize(adverse_change, standard_error),
        degrees_of_freedom=degrees_of_freedom,
        applicable=True,
        reason=reason,
    )


def standardize(adverse_change: float, standard_error: float) -> float:
    """Divide an adverse change by the noise, treating zero noise exactly.

    A change of zero against no measurable noise is zero standard errors, not
    an indeterminate form: nothing moved, so nothing needs explaining. A change
    away from zero against no measurable noise is unbounded, which is what an
    exactly reproducible measurement that shifted actually means.
    """

    if standard_error > 0.0:
        return adverse_change / standard_error
    if adverse_change == 0.0:
        return 0.0
    return math.inf if adverse_change > 0.0 else -math.inf
