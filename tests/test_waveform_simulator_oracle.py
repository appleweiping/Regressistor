"""Offline safety and analytic checks for the opt-in real simulator harness."""

from __future__ import annotations

import hashlib
import math
import sys
import zipfile
from decimal import Context, Decimal, localcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks import waveform_simulator_oracle as oracle


@pytest.mark.parametrize(
    "time_value", [0.0, 0.001, 0.0010000000001, 0.001005, 0.00101, 0.002, 0.006]
)
@pytest.mark.parametrize("tau", [0.001, 0.002])
def test_finite_rise_rc_formula_matches_independent_high_precision_convolution(
    time_value: float, tau: float
) -> None:
    with localcontext(Context(prec=80)):
        # Match the exact binary input/coefficient arithmetic. The integral of
        # the first-order impulse response against the input ramp gives this
        # independent difference-of-two-integrals representation.
        elapsed = Decimal.from_float(time_value - 0.001)
        rise, time_constant = Decimal.from_float(0.00001), Decimal.from_float(tau)
        if elapsed <= 0:
            expected = Decimal(0)
        else:
            upper = min(elapsed, rise)
            expected = (
                upper
                - time_constant
                * ((-(elapsed - upper) / time_constant).exp() - (-elapsed / time_constant).exp())
            ) / rise
    actual = oracle.rc_transient(time_value, tau)
    assert math.isclose(actual, float(expected), rel_tol=2e-11, abs_tol=2e-15)
    if 0 < expected < Decimal("1e-12"):
        assert math.isclose(actual, float(expected), rel_tol=2e-10, abs_tol=0.0)


@pytest.mark.parametrize("time_value,tau", [(float("nan"), 1), (0, 0), (0, -1), (0, float("inf"))])
def test_analytic_model_rejects_invalid_parameters(time_value: float, tau: float) -> None:
    with pytest.raises(oracle.OracleError, match="analytic"):
        oracle.rc_transient(time_value, tau)


def _plot(analysis: str = "ac") -> SimpleNamespace:
    complex_encoding = analysis == "ac"
    return SimpleNamespace(
        analysis="AC Analysis" if complex_encoding else "Transient Analysis",
        encoding="complex" if complex_encoding else "real",
        variables=(
            SimpleNamespace(
                name="frequency" if complex_encoding else "time",
                quantity="frequency" if complex_encoding else "time",
            ),
            SimpleNamespace(name="v(out)", quantity="voltage"),
        ),
        columns=((0j, 10 + 0j), (1 + 0j, 0.5 - 0.5j))
        if complex_encoding
        else ((0.0, 0.001), (0.0, 0.5)),
        dimensions=(2,),
        points=2,
    )


def test_ac_projection_uses_explicit_real_and_imaginary_components_and_units() -> None:
    plot = _plot()
    real = oracle._trace(plot, "ac", "real")
    imaginary = oracle._trace(plot, "ac", "imaginary")
    assert real.values == (1.0, 0.5)
    assert imaginary.values == (0.0, -0.5)
    assert real.axis == imaginary.axis == (0.0, 10.0)
    assert real.axis_unit == "Hz" and real.value_unit == "V"
    assert oracle._trace(_plot("transient"), "transient", "real").axis_unit == "s"


@pytest.mark.parametrize(
    "change",
    [
        "complex_axis",
        "wrong_analysis",
        "wrong_encoding",
        "wrong_vector",
        "wrong_quantity",
        "batch_shape",
    ],
)
def test_projection_rejects_unsupported_or_ambiguous_raw_semantics(change: str) -> None:
    plot = _plot()
    if change == "complex_axis":
        plot.columns = ((1j, 10 + 0j), plot.columns[1])
    elif change == "wrong_analysis":
        plot.analysis = "Transient Analysis"
    elif change == "wrong_encoding":
        plot.encoding = "real"
    elif change == "wrong_vector":
        plot.variables[1].name = "v(other)"
    elif change == "wrong_quantity":
        plot.variables[1].quantity = "current"
    else:
        plot.dimensions = (1, 2)
    with pytest.raises(oracle.OracleError):
        oracle._trace(plot, "ac", "real")


@pytest.mark.parametrize(
    "analysis,component", [("dc", "real"), ("ac", "magnitude"), ("transient", "imaginary")]
)
def test_projection_never_guesses_component_or_analysis(analysis: str, component: str) -> None:
    with pytest.raises(oracle.OracleError):
        oracle._trace(_plot(analysis), analysis, component)


def test_only_three_fixed_original_decks_are_executable() -> None:
    assert set(oracle.CASES) == {"nominal-coarse", "nominal-fine", "changed-resistance"}
    for name in oracle.CASES:
        text = oracle.deck(name)
        assert "PULSE(0 1 1m 10u 10u 10m 20m)" in text
        assert "write transient.raw v(out)" in text and "write ac.raw v(out)" in text
        assert all(token not in text for token in (".include", "source ", "shell "))
    with pytest.raises(oracle.OracleError, match="fixed"):
        oracle.deck("../../another-user-deck.cir")


def test_bounded_reader_and_package_identity_reject_oversized_inputs(tmp_path: Path) -> None:
    path = tmp_path / "sample.py"
    path.write_bytes(b"abc")
    with pytest.raises(oracle.OracleError, match="ceiling"):
        oracle._bytes(path, maximum=2)
    first = oracle._package_identity(tmp_path)
    path.write_bytes(b"abd")
    assert oracle._package_identity(tmp_path) != first
    with pytest.raises(oracle.OracleError, match="regular"):
        oracle._bytes(tmp_path)


def _fake_installed_spice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    package = tmp_path / "site-packages" / "spicetrellis"
    package.mkdir(parents=True)
    package.joinpath("__init__.py").write_bytes(b"# original test fixture\n")
    package.joinpath("py.typed").write_bytes(b"")
    wheel = tmp_path / "fixture.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("spicetrellis/__init__.py", package.joinpath("__init__.py").read_bytes())
        archive.writestr("spicetrellis/py.typed", b"")
    monkeypatch.setattr(
        oracle, "SPICE_WHEEL_SHA256", hashlib.sha256(wheel.read_bytes()).hexdigest()
    )
    distribution = SimpleNamespace(version="0.6.0", locate_file=lambda name: package.parent / name)
    monkeypatch.setattr(oracle.importlib.metadata, "distribution", lambda _name: distribution)
    monkeypatch.setitem(
        sys.modules, "spicetrellis", SimpleNamespace(__file__=str(package / "__init__.py"))
    )
    return wheel, package


def test_wheel_binding_compares_actual_imported_package_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel, package = _fake_installed_spice(tmp_path, monkeypatch)
    identity = oracle._spice_identity(wheel)
    assert identity["version"] == "0.6.0"
    package.joinpath("__init__.py").write_bytes(b"# changed after installation\n")
    with pytest.raises(oracle.OracleError, match="differs"):
        oracle._spice_identity(wheel)


def test_wheel_binding_rejects_additional_importable_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel, package = _fake_installed_spice(tmp_path, monkeypatch)
    package.joinpath("additional.py").write_bytes(b"# not in wheel\n")
    with pytest.raises(oracle.OracleError, match="additional"):
        oracle._spice_identity(wheel)


def test_wheel_binding_rejects_a_different_public_asset_before_import(tmp_path: Path) -> None:
    wheel = tmp_path / "wrong.whl"
    wheel.write_bytes(b"not the public wheel")
    with pytest.raises(oracle.OracleError, match="pinned public"):
        oracle._spice_identity(wheel)


def test_process_guard_records_actual_exit_and_log_hashes(tmp_path: Path) -> None:
    result = oracle._run([sys.executable, "-c", "print('bounded child')"], tmp_path, ())
    assert result["returncode"] == 0
    assert (
        result["files"]["stdout.txt"]["sha256"]
        == hashlib.sha256(tmp_path.joinpath("stdout.txt").read_bytes()).hexdigest()
    )


def test_process_guard_reaps_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oracle, "TIMEOUT_SECONDS", 0.1)
    with pytest.raises(oracle.OracleError, match="wall-time"):
        oracle._run([sys.executable, "-c", "import time; time.sleep(10)"], tmp_path, ())


def test_process_guard_rejects_output_exhaustion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(oracle, "MAX_FILE_BYTES", 1024)
    with pytest.raises(oracle.OracleError, match="byte ceiling"):
        oracle._run([sys.executable, "-c", "print('x' * 4096)"], tmp_path, ())


def test_process_guard_rejects_nonzero_exit(tmp_path: Path) -> None:
    with pytest.raises(oracle.OracleError, match="status 7"):
        oracle._run([sys.executable, "-c", "raise SystemExit(7)"], tmp_path, ())
