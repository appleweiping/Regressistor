from __future__ import annotations

import pytest

from regressistor.errors import InputError
from regressistor.model import Reducer
from regressistor.reducers import reduce_values


@pytest.mark.parametrize(
    ("reducer", "expected"),
    [
        (Reducer.MIN, 1.0),
        (Reducer.MAX, 8.0),
        (Reducer.MEAN, 4.0),
        (Reducer.MEDIAN, 3.0),
        (Reducer.P05, 1.2),
        (Reducer.P95, 7.5),
    ],
)
def test_reducers(reducer: Reducer, expected: float) -> None:
    assert reduce_values([1.0, 3.0, 8.0], reducer) == pytest.approx(expected)


def test_quantile_of_single_value() -> None:
    assert reduce_values([7.0], Reducer.P95) == 7.0


def test_rejects_empty_and_non_finite_values() -> None:
    with pytest.raises(InputError, match="empty"):
        reduce_values([], Reducer.MEAN)
    with pytest.raises(InputError, match="non-finite"):
        reduce_values([float("nan")], Reducer.MEAN)


def test_rejects_unknown_reducer() -> None:
    with pytest.raises(InputError, match="unsupported"):
        reduce_values([1.0], "mode")  # type: ignore[arg-type]


def test_rejects_reducer_overflow() -> None:
    with pytest.raises(InputError, match="overflowed"):
        reduce_values([1e308, 1e308], Reducer.MEAN)
