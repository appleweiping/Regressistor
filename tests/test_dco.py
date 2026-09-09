from __future__ import annotations

import json
from pathlib import Path

import pytest

from regressistor.dco import DCOError, main, verify_commit_file, verify_commit_pages


def _commit(
    *,
    name: str = "Ada Lovelace",
    email: str = "ada@example.com",
    message: str | None = None,
    sha: str = "a" * 40,
) -> dict[str, object]:
    message = message or f"Change the engine\n\nSigned-off-by: {name} <{email}>"
    return {"sha": sha, "commit": {"author": {"name": name, "email": email}, "message": message}}


def test_every_commit_requires_a_final_author_matching_trailer() -> None:
    pages = [[_commit(), _commit(name="Grace Hopper", sha="b" * 40)]]
    assert verify_commit_pages(pages, expected_count=2, expected_head="b" * 40) == 2
    for message in (
        "Signed-off-by: Ada Lovelace <ada@example.com>\n\nLater body",
        "Change\n\nordinary body\nSigned-off-by: Ada Lovelace <ada@example.com>",
        "Change\n\nSigned-off-by: Somebody Else <ada@example.com>",
    ):
        with pytest.raises(DCOError, match="DCO sign-off"):
            verify_commit_pages(
                [[_commit(message=message)]], expected_count=1, expected_head="a" * 40
            )


def test_signoff_normalizes_names_and_email_case() -> None:
    commit = _commit(
        name="Jose\u0301",
        email="AUTHOR@EXAMPLE.COM",
        message="Change\n\nSigned-off-by: Jos\u00e9 <author@example.com>",
    )
    assert verify_commit_pages([[commit]], expected_count=1, expected_head="a" * 40) == 1


def test_pages_are_bound_to_count_unique_order_and_final_head() -> None:
    first = _commit(sha="a" * 40)
    last = _commit(sha="b" * 40)
    pages = [[first], [last]]
    assert verify_commit_pages(pages, expected_count=2, expected_head="b" * 40) == 2
    with pytest.raises(DCOError, match="final pull-request head"):
        verify_commit_pages(pages, expected_count=2, expected_head="c" * 40)
    with pytest.raises(DCOError, match="declares 3"):
        verify_commit_pages(pages, expected_count=3, expected_head="b" * 40)
    with pytest.raises(DCOError, match="duplicate"):
        verify_commit_pages([[first, first]], expected_count=2, expected_head="a" * 40)


@pytest.mark.parametrize(
    ("pages", "message"),
    [
        ([], "invalid.*pages"),
        ([{}], "invalid.*pages"),
        ([[{}]], "malformed"),
        ([[_commit(sha="bad")]], "invalid commit SHA"),
        ([[_commit() for _ in range(251)]], "oversized commit page"),
    ],
)
def test_malformed_or_excessive_api_responses_fail_closed(pages: object, message: str) -> None:
    with pytest.raises(DCOError, match=message):
        verify_commit_pages(pages, expected_count=1, expected_head="a" * 40)


def test_bounded_strict_file_and_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "commits.json"
    path.write_text(json.dumps([[_commit()]]) + "\n", encoding="utf-8")
    assert verify_commit_file(path, expected_count=1, expected_head="a" * 40) == 1
    assert main([str(path), "1", "a" * 40]) == 0
    assert "1 commit" in capsys.readouterr().out
    path.write_text('[[{"sha":"x","sha":"y"}]]', encoding="utf-8")
    assert main([str(path), "1", "a" * 40]) == 1
    assert "duplicate JSON" in capsys.readouterr().err
    path.write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    with pytest.raises(DCOError, match="size"):
        verify_commit_file(path, expected_count=1, expected_head="a" * 40)
    assert main([]) == 2


def test_commit_file_rejects_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text(json.dumps([[_commit()]]), encoding="utf-8")
    link = tmp_path / "link.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(DCOError, match="regular file"):
        verify_commit_file(link, expected_count=1, expected_head="a" * 40)
