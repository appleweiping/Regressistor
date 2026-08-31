from __future__ import annotations

from importlib.metadata import version

import pytest

from regressistor import __version__
from regressistor.cli import main


def test_runtime_distribution_and_cli_versions_agree(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert __version__ == version("regressistor") == "0.2.0"
    with pytest.raises(SystemExit) as raised:
        main(["--version"])
    assert raised.value.code == 0
    assert capsys.readouterr().out == f"regressistor {__version__}\n"
