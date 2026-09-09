import ast
import re
import textwrap
from pathlib import Path

WORKFLOW = Path(".github/workflows/release.yml")


def test_project_importing_release_heredocs_use_the_installed_environment() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    blocks = re.findall(r"(?m)^          (.+) <<'PY'\n([\s\S]+?)^          PY$", source)
    project_blocks = []
    for command, code in blocks:
        tree = ast.parse(textwrap.dedent(code))
        imports = [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        ]
        imports.extend(
            node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        )
        if any(name == "regressistor" or name.startswith("regressistor.") for name in imports):
            project_blocks.append(command)
            assert command.startswith(
                ("uv run --frozen python -I -", ".release-smoke/bin/python -I -")
            ), f"project import uses an interpreter without the installed package: {command}"
    assert len(project_blocks) == 2


def test_release_workflow_binds_and_revalidates_exact_installed_wheel_evidence() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    required = (
        "SYFT_FILE_METADATA_SELECTION: all",
        "path: .release-smoke",
        "output-file: dist/SBOM.spdx.json",
        "release_artifacts.py bind-installed-wheel",
        '--expected-external-path "bin/regressistor"',
        "release_artifacts.py validate-sbom",
        "release_artifacts.py write-checksums",
        "release_artifacts.py verify-release",
        "subject-checksums: dist/SHA256SUMS",
    )
    assert all(token in source for token in required)
    assert source.count("src/regressistor/release_artifacts.py verify-release") == 3
    assert source.count("ref: ${{ github.sha }}") == 3
    assert "release_evidence" not in source
    assert "regressistor.spdx.json" not in source
    assert "subject-path: dist/*" not in source
    assert ".sbom-root" not in source
    assert "python -m regressistor.release_artifacts" not in source
    assert 'archive.extractall(destination, filter="data")' in source
    assert 'cd "$source_root/regressistor-$RELEASE_PROJECT_VERSION"' in source
    assert source.count("uv sync --frozen --extra dev") == 2
    assert source.count("uv run --frozen pytest") == 2


def test_release_workflow_uploads_and_publishes_only_the_exact_four_assets() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    expected_paths = (
        "dist/regressistor-${{ env.RELEASE_PROJECT_VERSION }}-py3-none-any.whl",
        "dist/regressistor-${{ env.RELEASE_PROJECT_VERSION }}.tar.gz",
        "dist/SBOM.spdx.json",
        "dist/SHA256SUMS",
    )
    assert all(path in source for path in expected_paths)
    assert "gh release create" in source
    assert "path: dist/*" not in source
    assert 'gh release create "$RELEASE_TAG" dist/*' not in source
    assert "dist/*.whl" in source  # Build-time wheel installation, never an upload/publish glob.
