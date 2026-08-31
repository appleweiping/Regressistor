"""Deterministic aggregation for repeated measurements."""

from __future__ import annotations

import math
from collections.abc import Iterable

from regressistor.errors import InputError
from regressistor.model import Reducer


def _quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = probability * (len(ordered) - 1)
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    fraction = position - lower_index
    return ordered[lower_index] * (1.0 - fraction) + ordered[upper_index] * fraction


def reduce_values(values: Iterable[float], reducer: Reducer) -> float:
    """Reduce finite values using an explicitly selected operation."""
    materialized = list(values)
    if not materialized:
        raise InputError("cannot reduce an empty measurement set")
    if any(not math.isfinite(value) for value in materialized):
        raise InputError("cannot reduce non-finite measurements")
    try:
        if reducer is Reducer.MIN:
            result = min(materialized)
        elif reducer is Reducer.MAX:
            result = max(materialized)
        elif reducer is Reducer.MEAN:
            result = math.fsum(materialized) / len(materialized)
        elif reducer is Reducer.MEDIAN:
            result = _quantile(materialized, 0.5)
        elif reducer is Reducer.P05:
            result = _quantile(materialized, 0.05)
        elif reducer is Reducer.P95:
            result = _quantile(materialized, 0.95)
        else:
            raise InputError(f"unsupported reducer: {reducer}")
    except ArithmeticError as error:
        raise InputError("measurement reduction overflowed") from error
    if not math.isfinite(result):
        raise InputError("measurement reduction produced a non-finite result")
    return result
