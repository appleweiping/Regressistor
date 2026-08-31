"""Command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from regressistor._strict_data import load_json_text
from regressistor._version import __version__
from regressistor.bundle import freeze_bundle, load_bundle
from regressistor.errors import InputError, OutputError, RegressistorError
from regressistor.gate import compare
from regressistor.inspection import inspect_bundle, inspection_text
from regressistor.matching import index_bundle
from regressistor.model import Scalar, scalar_identity
from regressistor.policy import load_policy
from regressistor.render import console_summary, decision_text, write_artifacts
from regressistor.report import load_report


def _emit(text: object, *, error: bool = False) -> None:
    """Write without leaking UnicodeEncodeError on narrow host encodings."""
    stream = sys.stderr if error else sys.stdout
    value = str(text)
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        safe = value.encode(encoding, errors="backslashreplace").decode(encoding)
    except LookupError:
        safe = value.encode("ascii", errors="backslashreplace").decode("ascii")
    print(safe, file=stream)


def _validate(args: argparse.Namespace) -> int:
    policy = load_policy(args.policy)
    for bundle_path in args.bundle:
        bundle = load_bundle(bundle_path)
        index_bundle(bundle, policy)
    bundle_text = f" and {len(args.bundle)} bundle(s)" if args.bundle else ""
    _emit(f"Validated policy{bundle_text}: {args.policy}")
    return 0


def _check(args: argparse.Namespace) -> int:
    report = compare(
        load_policy(args.policy),
        load_bundle(args.baseline),
        load_bundle(args.candidate),
    )
    paths = write_artifacts(report, args.out)
    _emit(console_summary(report))
    _emit("Artifacts: " + ", ".join(str(path) for path in paths))
    return 0 if report.passed else 1


def _freeze(args: argparse.Namespace) -> int:
    bundle = load_bundle(args.candidate)
    if args.policy:
        index_bundle(bundle, load_policy(args.policy))
    target = freeze_bundle(bundle, args.out, force=args.force)
    _emit(f"Frozen canonical baseline: {target}")
    return 0


def _inspect(args: argparse.Namespace) -> int:
    inspection = inspect_bundle(load_policy(args.policy), load_bundle(args.bundle))
    if args.format == "json":
        _emit(json.dumps(inspection.as_dict(), indent=2, sort_keys=True))
    else:
        _emit(inspection_text(inspection))
    return 0


def _parse_filter(raw: str) -> tuple[str, Scalar]:
    key, separator, value_text = raw.partition("=")
    if not separator or not key:
        raise InputError(f"case filter must have KEY=VALUE form: {raw!r}")
    value = load_json_text(
        value_text,
        context="case filter",
        max_bytes=4_096,
        fallback_text_on_syntax=True,
    )
    if not isinstance(value, bool | str | int | float):
        raise InputError(f"case filter value must be scalar: {raw!r}")
    return key, value


def _explain(args: argparse.Namespace) -> int:
    report = load_report(args.report)
    parsed_filters = [_parse_filter(raw) for raw in args.case]
    folded_keys = [key.casefold() for key, _ in parsed_filters]
    if len(folded_keys) != len(set(folded_keys)):
        raise InputError("case filters contain duplicate keys ignoring case")
    filters = dict(parsed_filters)
    matches = []
    for decision in report.decisions:
        case = dict(decision.case)
        if args.metric and decision.metric != args.metric:
            continue
        if any(
            key not in case or scalar_identity(case[key]) != scalar_identity(value)
            for key, value in filters.items()
        ):
            continue
        matches.append(decision)
    if not matches:
        raise InputError("no report decision matched the supplied filters")
    _emit("\n\n".join(decision_text(decision) for decision in matches))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="regressistor",
        description="Gate analog measurements against specifications and a baseline.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate a policy and optional bundles")
    validate.add_argument("--policy", required=True, type=Path)
    validate.add_argument("--bundle", action="append", default=[], type=Path)
    validate.set_defaults(handler=_validate)

    check = subparsers.add_parser("check", help="run contract and regression gates")
    check.add_argument("--policy", required=True, type=Path)
    check.add_argument("--baseline", required=True, type=Path)
    check.add_argument("--candidate", required=True, type=Path)
    check.add_argument("--out", required=True, type=Path)
    check.set_defaults(handler=_check)

    freeze = subparsers.add_parser("freeze", help="write a canonical baseline bundle")
    freeze.add_argument("--candidate", required=True, type=Path)
    freeze.add_argument("--out", required=True, type=Path)
    freeze.add_argument("--policy", type=Path)
    freeze.add_argument("--force", action="store_true")
    freeze.set_defaults(handler=_freeze)

    inspect = subparsers.add_parser("inspect", help="audit bundle coverage under a policy")
    inspect.add_argument("--policy", required=True, type=Path)
    inspect.add_argument("--bundle", required=True, type=Path)
    inspect.add_argument("--format", choices=("text", "json"), default="text")
    inspect.set_defaults(handler=_inspect)

    explain = subparsers.add_parser("explain", help="explain matching report decisions")
    explain.add_argument("--report", required=True, type=Path)
    explain.add_argument("--metric")
    explain.add_argument("--case", action="append", default=[], metavar="KEY=VALUE")
    explain.set_defaults(handler=_explain)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except OutputError as error:
        _emit(f"regressistor: output error: {error}", error=True)
        return 3
    except InputError as error:
        _emit(f"regressistor: input error: {error}", error=True)
        return 2
    except RegressistorError as error:
        _emit(f"regressistor: error: {error}", error=True)
        return 3
    except OSError as error:
        _emit(f"regressistor: I/O error: {error}", error=True)
        return 3
