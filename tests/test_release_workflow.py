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


def test_actual_wheel_smoke_imports_and_runs_all_clis_outside_the_checkout() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    blocks = re.findall(r"(?m)^          (.+) <<'PY'\n([\s\S]+?)^          PY$", source)
    smoke_blocks = [code for command, code in blocks if command == ".release-smoke/bin/python -I -"]
    assert len(smoke_blocks) == 1
    tree = ast.parse(textwrap.dedent(smoke_blocks[0]))
    contexts = [node for node in tree.body if isinstance(node, ast.With)]
    assert len(contexts) == 1
    external = contexts[0]
    # Exit order matters on Windows: chdir restores the original directory
    # before TemporaryDirectory tries to remove the external smoke directory.
    assert [ast.unparse(item.context_expr) for item in external.items] == [
        "tempfile.TemporaryDirectory()",
        "contextlib.chdir(directory)",
    ]
    assert ast.unparse(external.items[0].optional_vars) == "directory"
    descendants = set(ast.walk(external))
    assignments = {
        node.targets[0].id: node
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
    }
    for name, relative in (
        ("launcher", ".release-smoke/bin/regressistor"),
        ("fixture", "tests/fixtures/synthetic_nldm.lib"),
    ):
        assignment = assignments[name]
        value = assignment.value
        assert isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
        assert value.func.attr == "resolve"
        assert isinstance(value.func.value, ast.Call)
        assert ast.unparse(value.func.value.func) == "Path"
        assert ast.literal_eval(value.func.value.args[0]) == relative
        assert [(keyword.arg, ast.literal_eval(keyword.value)) for keyword in value.keywords] == [
            ("strict", True)
        ]
        assert assignment.lineno < external.lineno
    assert ast.unparse(assignments["installation"].value) == "launcher.parent.parent"

    project_imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        and any(alias.name == "regressistor" for alias in node.names)
    ]
    assert len(project_imports) == 1 and project_imports[0] in descendants
    guards = [
        node
        for node in external.body
        if isinstance(node, ast.If)
        and ast.unparse(node.test)
        == "not Path(regressistor.__file__).resolve().is_relative_to(installation)"
    ]
    assert len(guards) == 1 and any(isinstance(node, ast.Raise) for node in guards[0].body)
    cli_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "subprocess.run"
    ]
    assert len(cli_calls) == 4
    for call in cli_calls:
        assert call in descendants and call.lineno > guards[0].lineno
        assert not any(keyword.arg == "cwd" for keyword in call.keywords)
        assert isinstance(call.args[0], ast.List)
        assert ast.unparse(call.args[0].elts[0]) in {"str(launcher)", "*arguments"}
    argument_lists = [
        node
        for node in external.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "arguments" for target in node.targets
        )
    ]
    assert len(argument_lists) == 1
    assert isinstance(argument_lists[0].value, ast.List)
    assert ast.unparse(argument_lists[0].value.elts[0]) == "str(launcher)"


def test_source_allowlist_explicitly_contains_waveform_runtime_and_all_schemas() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    blocks = re.findall(r"(?m)^          (.+) <<'PY'\n([\s\S]+?)^          PY$", source)
    allowlists = [
        ast.literal_eval(node.value)
        for _command, code in blocks
        for node in ast.walk(ast.parse(textwrap.dedent(code)))
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "required_source"
            for target in node.targets
        )
    ]
    assert len(allowlists) == 1
    assert {
        "src/regressistor/waveform.py",
        "src/regressistor/waveform_json.py",
        "src/regressistor/schemas/waveform-v1.schema.json",
        "src/regressistor/schemas/waveform-policy-v1.schema.json",
        "src/regressistor/schemas/waveform-comparison-v1.schema.json",
    } <= allowlists[0]


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
    assert (
        source.count(
            "uv run --frozen pytest --cov=regressistor --cov-branch --cov-report=term-missing"
        )
        == 2
    )


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
