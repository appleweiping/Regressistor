from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

import pytest

from regressistor import __version__
from regressistor.cli import main


def test_runtime_distribution_and_cli_versions_agree(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert __version__ == version("regressistor") == "0.4.1"
    with pytest.raises(SystemExit) as raised:
        main(["--version"])
    assert raised.value.code == 0
    assert capsys.readouterr().out == f"regressistor {__version__}\n"


def test_citation_version_and_date_match_the_current_changelog() -> None:
    fields = dict(
        line.split(": ", 1)
        for line in Path("CITATION.cff").read_text(encoding="utf-8").splitlines()
        if line.startswith(("version: ", "date-released: "))
    )
    assert fields["version"] == __version__
    heading = f"## {__version__} - {fields['date-released']}"
    assert heading in Path("CHANGELOG.md").read_text(encoding="utf-8").splitlines()
