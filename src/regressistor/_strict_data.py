"""Bounded decoders for every untrusted Regressistor document."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Never

from regressistor.errors import InputError

MAX_DOCUMENT_BYTES = 1_048_576
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 10_000
MAX_NUMBER_CHARACTERS = 128
_MAX_INTEGER_ABS = 10**MAX_NUMBER_CHARACTERS


def _require_utf8(value: str, context: str) -> None:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise InputError(f"{context} contains an invalid Unicode scalar") from error


def _pairs(context: str) -> Callable[[list[tuple[str, Any]]], dict[str, Any]]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise InputError(f"duplicate JSON key {key!r} in {context}")
            result[key] = value
        return result

    return reject_duplicates


def _constant(context: str) -> Callable[[str], Never]:
    def reject(token: str) -> Never:
        raise InputError(f"non-finite JSON number {token!r} in {context}")

    return reject


def _integer(context: str) -> Callable[[str], int]:
    def parse(token: str) -> int:
        if len(token.removeprefix("-")) > MAX_NUMBER_CHARACTERS:
            raise InputError(f"oversized JSON number in {context}")
        return int(token)

    return parse


def _floating(context: str) -> Callable[[str], float]:
    def parse(token: str) -> float:
        if len(token) > MAX_NUMBER_CHARACTERS:
            raise InputError(f"oversized JSON number in {context}")
        value = float(token)
        if not math.isfinite(value):
            raise InputError(f"non-finite JSON number in {context}")
        return value

    return parse


def check_data_complexity(
    value: object,
    context: str,
    *,
    validate_scalars: bool = True,
    max_depth: int = MAX_JSON_DEPTH,
    max_nodes: int = MAX_JSON_NODES,
    max_text_bytes: int = MAX_DOCUMENT_BYTES,
) -> None:
    """Reject unsupported, excessively deep, or excessively large decoded trees."""

    pending: list[tuple[object, int]] = [(value, 0)]
    nodes = 0
    text_bytes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > max_depth or nodes > max_nodes:
            raise InputError(f"{context} exceeds JSON complexity limits")
        if item is None or isinstance(item, bool | str | float):
            if isinstance(item, str):
                _require_utf8(item, context)
                text_bytes += len(item.encode("utf-8"))
                if text_bytes > max_text_bytes:
                    raise InputError(f"{context} exceeds text-size limits")
            if validate_scalars and isinstance(item, float) and not math.isfinite(item):
                raise InputError(f"{context} contains a non-finite number")
            continue
        if isinstance(item, int):
            if validate_scalars and abs(item) >= _MAX_INTEGER_ABS:
                raise InputError(f"{context} contains an oversized number")
            continue
        if isinstance(item, Mapping):
            if not all(isinstance(key, str) for key in item):
                raise InputError(f"{context} object keys must be strings")
            for key in item:
                _require_utf8(key, context)
                text_bytes += len(key.encode("utf-8"))
                if text_bytes > max_text_bytes:
                    raise InputError(f"{context} exceeds text-size limits")
            pending.extend((child, depth + 1) for child in item.values())
            continue
        if isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
            continue
        if validate_scalars:
            raise InputError(f"{context} contains an unsupported value")


def _check_json_nesting(text: str, context: str, max_depth: int) -> None:
    """Reject excessive container nesting before the JSON decoder allocates it."""

    depth = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            # Decoded-tree depth starts at zero, so the root container is one opener.
            if depth > max_depth + 1:
                raise InputError(f"{context} exceeds JSON complexity limits")
        elif character in "]}":
            depth -= 1


def load_json_text(
    text: str,
    *,
    context: str,
    max_bytes: int = MAX_DOCUMENT_BYTES,
    max_depth: int = MAX_JSON_DEPTH,
    max_nodes: int = MAX_JSON_NODES,
    max_text_bytes: int = MAX_DOCUMENT_BYTES,
    fallback_text_on_syntax: bool = False,
) -> object:
    """Strictly decode a bounded JSON string, optionally treating bare text as a string."""

    try:
        if len(text.encode("utf-8")) > max_bytes:
            raise InputError(f"{context} exceeds {max_bytes} byte input limit")
        _check_json_nesting(text, context, max_depth)
        value = json.loads(
            text,
            object_pairs_hook=_pairs(context),
            parse_constant=_constant(context),
            parse_int=_integer(context),
            parse_float=_floating(context),
        )
    except json.JSONDecodeError as error:
        if fallback_text_on_syntax:
            return text
        raise InputError(f"invalid JSON {context}: {error.msg}") from error
    except InputError:
        raise
    except (RecursionError, OverflowError, UnicodeError, ValueError) as error:
        raise InputError(f"invalid JSON {context}: {error}") from error
    check_data_complexity(
        value,
        context,
        max_depth=max_depth,
        max_nodes=max_nodes,
        max_text_bytes=max_text_bytes,
    )
    return value


def read_document(
    path: str | Path, *, context: str, max_bytes: int = MAX_DOCUMENT_BYTES
) -> tuple[Path, bytes]:
    """Read at most one bounded document plus one overflow-detection byte."""

    source = Path(path)
    try:
        with source.open("rb") as stream:
            payload = stream.read(max_bytes + 1)
    except OSError as error:
        raise InputError(f"cannot read {context} {source}: {error}") from error
    if len(payload) > max_bytes:
        raise InputError(f"{context} exceeds {max_bytes} byte input limit")
    return source, payload


def load_json_path(
    path: str | Path,
    *,
    context: str,
    max_bytes: int = MAX_DOCUMENT_BYTES,
    max_depth: int = MAX_JSON_DEPTH,
    max_nodes: int = MAX_JSON_NODES,
    max_text_bytes: int = MAX_DOCUMENT_BYTES,
) -> tuple[Path, bytes, object]:
    """Read and strictly decode a bounded UTF-8 JSON document."""

    source, payload = read_document(path, context=context, max_bytes=max_bytes)
    try:
        text = payload.decode("utf-8")
    except UnicodeError as error:
        raise InputError(f"invalid JSON {context} {source}: {error}") from error
    return (
        source,
        payload,
        load_json_text(
            text,
            context=f"{context} {source}",
            max_bytes=max_bytes,
            max_depth=max_depth,
            max_nodes=max_nodes,
            max_text_bytes=max_text_bytes,
        ),
    )
