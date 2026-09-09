from __future__ import annotations

import re
from pathlib import Path


def test_every_github_action_is_pinned_to_a_full_commit() -> None:
    references: list[tuple[Path, int, str]] = []
    for workflow in sorted(Path(".github/workflows").glob("*.yml")):
        for line_number, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("uses:"):
                references.append((workflow, line_number, stripped.partition("uses:")[2].strip()))
    assert references
    for workflow, line_number, reference in references:
        assert re.match(r"[^\s@]+@[0-9a-f]{40}(?:\s+#.*)?\Z", reference), (
            workflow,
            line_number,
            reference,
        )


def test_source_distribution_contains_its_frozen_self_test_contract() -> None:
    manifest = Path("MANIFEST.in").read_text(encoding="utf-8")
    assert "include uv.lock" in manifest
    assert "recursive-include .github/workflows *.yml" in manifest
    assert "recursive-include tests *.py" in manifest


def test_dco_runs_only_the_trusted_base_verifier_and_binds_every_pr_identity() -> None:
    dco = Path(".github/workflows/dco.yml").read_text(encoding="utf-8")
    ci = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "pull_request_target:" in dco
    assert "types: [opened, reopened, synchronize, edited]" in dco
    assert "cancel-in-progress: true" in dco
    assert "statuses: write" in dco
    assert "state=pending" in dco
    assert "ref: ${{ github.event.pull_request.base.sha }}" in dco
    assert "repository: ${{ github.event.pull_request.base.repo.full_name }}" in dco
    assert "BASE_SHA: ${{ github.event.pull_request.base.sha }}" in dco
    assert "BASE_REF: ${{ github.event.pull_request.base.ref }}" in dco
    assert "BASE_REPOSITORY: ${{ github.event.pull_request.base.repo.full_name }}" in dco
    assert dco.count(".base.sha, .base.ref, .base.repo.full_name") == 3
    assert "pull-commits.json" in dco
    assert "python -I src/regressistor/dco.py" in dco
    assert "PYTHONPATH:" not in dco
    assert "ref: ${{ github.event.pull_request.head.sha }}" not in dco
    assert "# Transitional check" in ci


def test_release_accepts_only_successful_main_push_ci_for_the_exact_tag_commit() -> None:
    release = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "head_sha=$GITHUB_SHA&per_page=100" in release
    assert (
        r".conclusion == \"success\" and .event == \"push\" and .head_branch == \"main\" "
        r"and .head_sha == \"$GITHUB_SHA\""
    ) in release
    assert "refs/tags/v*) ;;" in release
    assert "if: needs.verify-ci.result == 'success'" in release
    assert "github.event_name == 'workflow_dispatch' ||" not in release
    assert 'git verify-tag "$GITHUB_REF_NAME"' in release
    assert 'git rev-parse "refs/tags/$GITHUB_REF_NAME^{}"' in release
    assert 'if [ "$resolved" != "$GITHUB_SHA" ]' in release
    assert 'git merge-base --is-ancestor "$GITHUB_SHA" origin/main' in release
    assert ".commit.verification.verified" in release
