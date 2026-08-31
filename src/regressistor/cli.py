"""Command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from regressistor import __version__
from regressistor.bundle import freeze_bundle, load_bundle
from regressistor.errors import InputError, OutputError, RegressistorError
from regressistor.gate import compare
from regressistor.inspection import inspect_bundle, inspection_text
from regressistor.matching import index_bundle
from regressistor.model import Scalar, scalar_identity
from regressistor.policy import load_policy
from regressistor.render import console_summary, decision_text, write_artifacts
from regressistor.report import load_report


def _validate(args: argparse.Namespace) -> int:
    policy = load_policy(args.policy)
    for bundle_path in args.bundle:
        bundle = load_bundle(bundle_path)
        index_bundle(bundle, policy)
    bundle_text = f" and {len(args.bundle)} bundle(s)" if args.bundle else ""
    print(f"Validated policy{bundle_text}: {args.policy}")
    return 0


def _check(args: argparse.Namespace) -> int:
    report = compare(
        load_policy(args.policy),
        load_bundle(args.baseline),
        load_bundle(args.candidate),
    )
    paths = write_artifacts(report, args.out)
    print(console_summary(report))
    print("Artifacts: " + ", ".join(str(path) for path in paths))
    return 0 if report.passed else 1


def _freeze(args: argparse.Namespace) -> int:
    bundle = load_bundle(args.candidate)
    if args.policy:
        index_bundle(bundle, load_policy(args.policy))
    target = freeze_bundle(bundle, args.out, force=args.force)
    print(f"Frozen canonical baseline: {target}")
    return 0


def _inspect(args: argparse.Namespace) -> int:
    inspection = inspect_bundle(load_policy(args.policy), load_bundle(args.bundle))
    if args.format == "json":
        print(json.dumps(inspection.as_dict(), indent=2, sort_keys=True))
    else:
        print(inspection_text(inspection))
    return 0


def _parse_filter(raw: str) -> tuple[str, Scalar]:
    key, separator, value_text = raw.partition("=")
    if not separator or not key:
        raise InputError(f"case filter must have KEY=VALUE form: {raw!r}")
    try:
        value = json.loads(value_text)
    except json.JSONDecodeError:
        value = value_text
    if isinstance(value, dict | list) or value is None:
        raise InputError(f"case filter value must be scalar: {raw!r}")
    return key, value


def _explain(args: argparse.Namespace) -> int:
    report = load_report(args.report)
    filters = dict(_parse_filter(raw) for raw in args.case)
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
    print("\n\n".join(decision_text(decision) for decision in matches))
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
        print(f"regressistor: output error: {error}", file=sys.stderr)
        return 3
    except InputError as error:
        print(f"regressistor: input error: {error}", file=sys.stderr)
        return 2
    except RegressistorError as error:
        print(f"regressistor: error: {error}", file=sys.stderr)
        return 3
    except OSError as error:
        print(f"regressistor: I/O error: {error}", file=sys.stderr)
        return 3
