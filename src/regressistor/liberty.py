"""Strict, bounded Liberty NLDM conversion to measurement bundles.

The parser intentionally implements data syntax, not Liberty expressions or
preprocessor execution.  Unknown groups are retained only long enough to find
the supported timing and internal-power records beneath cells and ports.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from regressistor._output import atomic_write_bytes
from regressistor._strict_data import MAX_DOCUMENT_BYTES, read_document
from regressistor.bundle import canonical_data, parse_bundle
from regressistor.errors import InputError, OutputError
from regressistor.model import Bundle, Scalar, case_identity
from regressistor.units import convert, parse_unit

_CONTRACT: Final = "regressistor.liberty-nldm/1"
_NUMBER = re.compile(r"[+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[Ee][+-]?\d+)?\Z")
_UNIT = re.compile(
    r"\s*([+]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[Ee][+-]?\d+)?)\s*"
    r"([A-Za-z%\u00b5\u03bc]+)\s*\Z"
)
_BUS_MEMBER_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.$]*\[[0-9]+(?::[0-9]+)?\]\Z")
_HEX = frozenset("0123456789abcdef")
_PUNCTUATION = frozenset("{}():;,")
_TIMING_TABLES = frozenset(
    {
        "cell_rise",
        "cell_fall",
        "rise_transition",
        "fall_transition",
        "rise_constraint",
        "fall_constraint",
    }
)
_POWER_TABLES = frozenset({"rise_power", "fall_power", "power"})
_DIRECT_TIME_SCALARS = frozenset(
    {
        "min_pulse_width_high",
        "min_pulse_width_low",
        "minimum_period",
        "min_period",
    }
)
_CONTEXT_ATTRIBUTES = (
    "related_pin",
    "related_output_pin",
    "related_bus_pins",
    "related_pg_pin",
    "timing_type",
    "timing_sense",
    "when",
    "sdf_cond",
    "equal_or_opposite_output",
    "power_level",
    "switching_interval",
    "switching_together_group",
    "rising_together_group",
    "falling_together_group",
)
_TIME_VARIABLES = frozenset(
    {
        "input_net_transition",
        "input_transition_time",
        "related_pin_transition",
        "constrained_pin_transition",
        "time",
    }
)
_CAPACITANCE_VARIABLES = frozenset(
    {
        "total_output_net_capacitance",
        "related_out_total_output_net_capacitance",
    }
)
_VOLTAGE_VARIABLES = frozenset({"input_voltage", "output_voltage"})
_DIMENSIONLESS_VARIABLES = frozenset({"normalized_voltage"})


@dataclass(frozen=True, slots=True)
class LibertyLimits:
    """Resource limits applied before and during parsing and materialization."""

    max_bytes: int = 4 * MAX_DOCUMENT_BYTES
    max_tokens: int = 200_000
    max_token_bytes: int = 4_096
    max_nesting: int = 64
    max_entries: int = 50_000
    max_tables: int = 2_048
    max_numeric_values: int = 100_000
    max_output_points: int = 320

    def __post_init__(self) -> None:
        values = (
            self.max_bytes,
            self.max_tokens,
            self.max_token_bytes,
            self.max_nesting,
            self.max_entries,
            self.max_tables,
            self.max_numeric_values,
            self.max_output_points,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in values
        ):
            raise ValueError("Liberty limits must be positive integers")
        hard_maxima = (
            4 * MAX_DOCUMENT_BYTES,
            200_000,
            4_096,
            64,
            50_000,
            2_048,
            100_000,
            320,
        )
        if any(value > maximum for value, maximum in zip(values, hard_maxima, strict=True)):
            raise ValueError("Liberty limits cannot exceed their hard safety maxima")


@dataclass(frozen=True, slots=True)
class SourceLocation:
    """One-based source coordinate and zero-based decoded-text offset."""

    line: int
    column: int
    offset: int

    @property
    def sample(self) -> str:
        """Return the bundle sample identifier used for this location."""

        return f"L{self.line}:C{self.column}"


@dataclass(frozen=True, slots=True)
class MetricSource:
    """Trace one emitted metric family to its Liberty declaration."""

    metric: str
    location: SourceLocation
    cell: str
    port: str


@dataclass(frozen=True, slots=True)
class LibertyConversion:
    """Validated bundle plus immutable conversion provenance."""

    bundle: Bundle
    source_sha256: str
    source_name: str
    metric_sources: tuple[MetricSource, ...]

    def canonical_data(self) -> dict[str, object]:
        """Return the standard, deterministic measurement-bundle document."""

        if self.bundle.source_hash != self.source_sha256:
            raise InputError("Liberty conversion source digest does not match its bundle")
        data = canonical_data(self.bundle)
        validated = parse_bundle(
            data,
            source_hash=self.source_sha256,
            source_path=self.source_name,
        )
        source = validated.run.get("source")
        expected_source = {"name": self.source_name, "sha256": self.source_sha256}
        if source != expected_source:
            raise InputError("Liberty conversion provenance does not match its source")
        return canonical_data(validated)


@dataclass(frozen=True, slots=True)
class _Token:
    kind: str
    value: str
    location: SourceLocation


@dataclass(frozen=True, slots=True)
class _Entry:
    name: str
    kind: str
    location: SourceLocation
    arguments: tuple[tuple[_Token, ...], ...] = ()
    value: tuple[_Token, ...] = ()
    children: tuple[_Entry, ...] = ()


@dataclass(frozen=True, slots=True)
class _DeclaredUnit:
    multiplier: float
    unit: str

    def metadata(self) -> dict[str, float | str]:
        return {"multiplier": self.multiplier, "unit": self.unit}


@dataclass(frozen=True, slots=True)
class _OperatingCondition:
    name: str
    process: float
    voltage: float
    temperature: float


@dataclass(frozen=True, slots=True)
class _Axis:
    variable: str
    values: tuple[float, ...]
    unit: _DeclaredUnit
    location: SourceLocation


@dataclass(frozen=True, slots=True)
class _Template:
    name: str
    axes: tuple[_Axis, ...]
    location: SourceLocation


class _Lexer:
    def __init__(self, text: str, limits: LibertyLimits) -> None:
        self._text = text
        self._limits = limits
        self._index = 0
        self._line = 1
        self._column = 1
        self._tokens: list[_Token] = []

    def tokenize(self) -> tuple[_Token, ...]:
        while self._index < len(self._text):
            character = self._text[self._index]
            if character in " \t\r\n":
                self._advance()
            elif character == "\\" and self._peek(1) in {"\r", "\n"}:
                self._line_continuation()
            elif character == "/" and self._peek(1) == "*":
                self._block_comment()
            elif character == "/" and self._peek(1) == "/":
                self._line_comment()
            elif character == '"':
                self._string()
            elif character in _PUNCTUATION:
                location = self._location()
                self._advance()
                self._append(_Token(character, character, location))
            else:
                self._word()
        self._append(_Token("EOF", "", self._location()))
        return tuple(self._tokens)

    def _peek(self, distance: int) -> str:
        index = self._index + distance
        return self._text[index] if index < len(self._text) else ""

    def _location(self) -> SourceLocation:
        return SourceLocation(self._line, self._column, self._index)

    def _advance(self) -> str:
        character = self._text[self._index]
        self._index += 1
        if character == "\n":
            self._line += 1
            self._column = 1
        else:
            self._column += 1
        return character

    def _append(self, token: _Token) -> None:
        if len(token.value.encode("utf-8")) > self._limits.max_token_bytes:
            raise _at(token.location, "token exceeds byte limit")
        self._tokens.append(token)
        if len(self._tokens) > self._limits.max_tokens:
            raise _at(token.location, "Liberty input exceeds token limit")

    def _line_continuation(self) -> None:
        self._advance()
        if self._peek(0) == "\r":
            self._advance()
        if self._peek(0) != "\n":
            raise _at(self._location(), "invalid line continuation")
        self._advance()

    def _block_comment(self) -> None:
        location = self._location()
        self._advance()
        self._advance()
        while self._index < len(self._text):
            if self._peek(0) == "*" and self._peek(1) == "/":
                self._advance()
                self._advance()
                return
            self._advance()
        raise _at(location, "unterminated block comment")

    def _line_comment(self) -> None:
        self._advance()
        self._advance()
        while self._index < len(self._text) and self._peek(0) not in "\r\n":
            self._advance()

    def _string(self) -> None:
        location = self._location()
        self._advance()
        result: list[str] = []
        while self._index < len(self._text):
            character = self._advance()
            if character == '"':
                self._append(_Token("STRING", "".join(result), location))
                return
            if character == "\\":
                escaped = self._peek(0)
                if escaped in {"\r", "\n"}:
                    if escaped == "\r":
                        self._advance()
                    if self._peek(0) != "\n":
                        raise _at(location, "invalid string line continuation")
                    self._advance()
                    continue
                if escaped not in {'"', "\\"}:
                    raise _at(self._location(), "unsupported string escape")
                result.append(self._advance())
                continue
            if ord(character) < 0x20 or ord(character) == 0x7F:
                raise _at(location, "string contains a control character")
            result.append(character)
        raise _at(location, "unterminated string")

    def _word(self) -> None:
        location = self._location()
        result: list[str] = []
        while self._index < len(self._text):
            character = self._peek(0)
            if character in " \t\r\n" or character in _PUNCTUATION or character == '"':
                break
            if character == "/" and self._peek(1) in {"/", "*"}:
                break
            if character == "\\" and self._peek(1) in {"\r", "\n"}:
                break
            if ord(character) < 0x20 or ord(character) == 0x7F:
                raise _at(location, "bare token contains a control character")
            result.append(self._advance())
        if not result:
            raise _at(location, f"unexpected character {self._peek(0)!r}")
        self._append(_Token("WORD", "".join(result), location))


class _Parser:
    def __init__(self, tokens: tuple[_Token, ...], limits: LibertyLimits) -> None:
        self._tokens = tokens
        self._limits = limits
        self._index = 0
        self._entries = 0

    def parse(self) -> tuple[_Entry, ...]:
        result = self._scope(0, top_level=True)
        self._expect("EOF")
        return result

    def _current(self) -> _Token:
        return self._tokens[self._index]

    def _take(self) -> _Token:
        token = self._current()
        self._index += 1
        return token

    def _expect(self, kind: str) -> _Token:
        token = self._current()
        if token.kind != kind:
            raise _at(token.location, f"expected {kind!r}, found {token.value or token.kind!r}")
        return self._take()

    def _scope(self, depth: int, *, top_level: bool = False) -> tuple[_Entry, ...]:
        if depth > self._limits.max_nesting:
            raise _at(self._current().location, "Liberty group nesting exceeds limit")
        entries: list[_Entry] = []
        while self._current().kind not in ({"EOF"} if top_level else {"}", "EOF"}):
            entries.append(self._entry(depth))
            self._entries += 1
            if self._entries > self._limits.max_entries:
                raise _at(entries[-1].location, "Liberty input exceeds entry limit")
        if not top_level and self._current().kind == "EOF":
            raise _at(self._current().location, "unterminated Liberty group")
        return tuple(entries)

    def _entry(self, depth: int) -> _Entry:
        name_token = self._expect("WORD")
        name = _semantic_text(name_token.value, "entry name", name_token.location)
        following = self._current()
        if following.kind == ":":
            self._take()
            value = self._until_semicolon()
            return _Entry(name, "attribute", name_token.location, value=value)
        arguments: tuple[tuple[_Token, ...], ...] = ()
        had_arguments = False
        if following.kind == "(":
            had_arguments = True
            arguments = self._arguments()
            following = self._current()
        if following.kind == "{":
            self._take()
            children = self._scope(depth + 1)
            self._expect("}")
            return _Entry(name, "group", name_token.location, arguments, children=children)
        if following.kind == ";" and had_arguments:
            self._take()
            return _Entry(name, "call", name_token.location, arguments)
        raise _at(following.location, f"entry {name!r} must be an attribute, call, or group")

    def _until_semicolon(self) -> tuple[_Token, ...]:
        result: list[_Token] = []
        parentheses = 0
        while True:
            token = self._current()
            if token.kind == "EOF" or (token.kind == "}" and parentheses == 0):
                raise _at(token.location, "unterminated Liberty attribute")
            if token.kind == ";" and parentheses == 0:
                self._take()
                if not result:
                    raise _at(token.location, "Liberty attribute value is empty")
                return tuple(result)
            if token.kind == "(":
                parentheses += 1
            elif token.kind == ")":
                if parentheses == 0:
                    raise _at(token.location, "unmatched ')' in attribute")
                parentheses -= 1
            result.append(self._take())

    def _arguments(self) -> tuple[tuple[_Token, ...], ...]:
        self._expect("(")
        if self._current().kind == ")":
            self._take()
            return ()
        arguments: list[tuple[_Token, ...]] = []
        current: list[_Token] = []
        depth = 0
        while True:
            token = self._current()
            if token.kind == "EOF":
                raise _at(token.location, "unterminated Liberty argument list")
            if token.kind == "(":
                depth += 1
                current.append(self._take())
            elif token.kind == ")":
                if depth:
                    depth -= 1
                    current.append(self._take())
                else:
                    self._take()
                    if not current:
                        raise _at(token.location, "empty Liberty argument")
                    arguments.append(tuple(current))
                    return tuple(arguments)
            elif token.kind == "," and depth == 0:
                self._take()
                if not current:
                    raise _at(token.location, "empty Liberty argument")
                arguments.append(tuple(current))
                current = []
            else:
                current.append(self._take())


class _Converter:
    def __init__(
        self,
        root: tuple[_Entry, ...],
        source_sha256: str,
        source_name: str,
        limits: LibertyLimits,
    ) -> None:
        self._root = root
        self._source_sha256 = source_sha256
        self._source_name = source_name
        self._limits = limits
        self._numeric_values = 0
        self._table_count = 0
        self._points: list[dict[str, object]] = []
        self._collisions: set[tuple[object, str]] = set()
        self._sources: list[MetricSource] = []

    def convert(self) -> LibertyConversion:
        library = _exactly_one(_groups(self._root, "library"), "library group")
        library_name = _one_argument_text(library, "library name")
        units = self._units(library)
        condition = self._condition(library, units.get("voltage"))
        templates = self._templates(library, units)
        cells = _groups(library.children, "cell")
        if not cells:
            raise _at(library.location, "library contains no cell groups")
        _reject_named_duplicates(cells, "cell")
        for cell in cells:
            self._cell(library_name, cell, condition, units, templates)
        if not self._points:
            raise _at(library.location, "library contains no supported NLDM measurements")
        run: dict[str, object] = {
            "producer": "Regressistor",
            "contract": _CONTRACT,
            "source": {"name": self._source_name, "sha256": self._source_sha256},
            "library": library_name,
            "operating_conditions": {
                "name": condition.name,
                "process": condition.process,
                "voltage_v": condition.voltage,
                "temperature_c": condition.temperature,
            },
            "units": {name: unit.metadata() for name, unit in sorted(units.items())},
            "sample_semantics": "L<line>:C<column> of the Liberty metric declaration",
        }
        data: dict[str, object] = {
            "schema_version": 1,
            "run": run,
            "points": self._points,
        }
        unsorted_bundle = parse_bundle(
            data,
            source_hash=self._source_sha256,
            source_path=self._source_name,
        )
        bundle = parse_bundle(
            canonical_data(unsorted_bundle),
            source_hash=self._source_sha256,
            source_path=self._source_name,
        )
        return LibertyConversion(
            bundle=bundle,
            source_sha256=self._source_sha256,
            source_name=self._source_name,
            metric_sources=tuple(self._sources),
        )

    def _units(self, library: _Entry) -> dict[str, _DeclaredUnit]:
        result: dict[str, _DeclaredUnit] = {}
        attributes = {
            "time": "time_unit",
            "voltage": "voltage_unit",
            "current": "current_unit",
            "power": "leakage_power_unit",
        }
        expected = {
            "time": (("time", 1),),
            "voltage": (("voltage", 1),),
            "current": (("current", 1),),
            "power": (("power", 1),),
            "capacitance": (("capacitance", 1),),
        }
        for key, attribute_name in attributes.items():
            attribute = _optional_one(_attributes(library.children, attribute_name), attribute_name)
            if attribute is not None:
                result[key] = _declared_unit(
                    _one_value_text(attribute, attribute_name), expected[key], attribute.location
                )
        capacitance = _optional_one(
            _calls(library.children, "capacitive_load_unit"), "capacitive_load_unit"
        )
        if capacitance is not None:
            if len(capacitance.arguments) != 2:
                raise _at(capacitance.location, "capacitive_load_unit requires multiplier and unit")
            multiplier = _number(
                _argument_text(capacitance.arguments[0], capacitance.location), capacitance.location
            )
            unit_text = _argument_text(capacitance.arguments[1], capacitance.location)
            result["capacitance"] = _declared_unit(
                f"{multiplier}{unit_text}", expected["capacitance"], capacitance.location
            )
        return result

    def _condition(
        self, library: _Entry, voltage_unit: _DeclaredUnit | None
    ) -> _OperatingCondition:
        conditions = _groups(library.children, "operating_conditions")
        _reject_named_duplicates(conditions, "operating_conditions")
        default = _optional_one(
            _attributes(library.children, "default_operating_conditions"),
            "default_operating_conditions",
        )
        if not conditions and default is None:
            if voltage_unit is None:
                raise _at(library.location, "voltage_unit is required for nominal PVT")
            process = self._required_number_attribute(library, "nom_process")
            raw_voltage = self._required_number_attribute(library, "nom_voltage")
            temperature = self._required_number_attribute(library, "nom_temperature")
            voltage = self._normalize_voltage(
                raw_voltage, voltage_unit, library.location, "nominal voltage"
            )
            return _OperatingCondition("nominal", process, voltage, temperature)
        if default is not None:
            selected_name = _one_value_text(default, "default_operating_conditions")
            selected = [
                group
                for group in conditions
                if _one_argument_text(group, "operating condition") == selected_name
            ]
            if len(selected) != 1:
                raise _at(
                    default.location, "default operating condition does not name exactly one group"
                )
            group = selected[0]
        elif len(conditions) == 1:
            group = conditions[0]
        else:
            raise _at(library.location, "operating condition is missing or ambiguous")
        if voltage_unit is None:
            raise _at(group.location, "voltage_unit is required for operating conditions")
        name = _one_argument_text(group, "operating condition")
        process = self._required_number_attribute(group, "process")
        raw_voltage = self._required_number_attribute(group, "voltage")
        temperature = self._required_number_attribute(group, "temperature")
        voltage = self._normalize_voltage(
            raw_voltage, voltage_unit, group.location, "operating-condition voltage"
        )
        return _OperatingCondition(name, process, voltage, temperature)

    @staticmethod
    def _normalize_voltage(
        raw_voltage: float,
        voltage_unit: _DeclaredUnit,
        location: SourceLocation,
        context: str,
    ) -> float:
        try:
            voltage = convert(
                _scaled(raw_voltage, voltage_unit, location, context),
                voltage_unit.unit,
                "V",
            )
        except InputError as error:
            raise _at(location, f"cannot normalize {context}: {error}") from error
        if voltage <= 0.0:
            raise _at(location, f"{context} must be positive")
        return voltage

    def _required_number_attribute(self, group: _Entry, name: str) -> float:
        attribute = _exactly_one(
            _attributes(group.children, name),
            f"{name} attribute",
            missing_location=group.location,
        )
        return self._counted_number(_one_value_text(attribute, name), attribute.location)

    def _templates(self, library: _Entry, units: dict[str, _DeclaredUnit]) -> dict[str, _Template]:
        groups = [
            entry
            for entry in library.children
            if entry.kind == "group" and entry.name in {"lu_table_template", "power_lut_template"}
        ]
        _reject_named_duplicates(groups, "lookup template")
        templates: dict[str, _Template] = {}
        for group in groups:
            name = _one_argument_text(group, "lookup template")
            axes: list[_Axis] = []
            for axis_number in (1, 2):
                variable_entry = _optional_one(
                    _attributes(group.children, f"variable_{axis_number}"),
                    f"variable_{axis_number}",
                )
                index_entry = _optional_one(
                    _calls(group.children, f"index_{axis_number}"),
                    f"index_{axis_number}",
                )
                if variable_entry is None and index_entry is None:
                    continue
                if variable_entry is None or index_entry is None:
                    raise _at(group.location, f"template axis {axis_number} is incomplete")
                if axis_number != len(axes) + 1:
                    raise _at(group.location, "lookup template axes must be contiguous")
                variable = _one_value_text(variable_entry, f"variable_{axis_number}")
                values = self._numeric_vector(index_entry)
                axes.append(
                    _Axis(
                        variable,
                        values,
                        self._axis_unit(variable, units, group.location),
                        index_entry.location,
                    )
                )
            if _calls(group.children, "index_3") or _attributes(group.children, "variable_3"):
                raise _at(group.location, "three-dimensional lookup tables are not supported")
            if not axes:
                raise _at(group.location, "lookup template has no axes")
            templates[_fold(name)] = _Template(name, tuple(axes), group.location)
        return templates

    def _cell(
        self,
        library_name: str,
        cell: _Entry,
        condition: _OperatingCondition,
        units: dict[str, _DeclaredUnit],
        templates: dict[str, _Template],
    ) -> None:
        cell_name = _one_argument_text(cell, "cell name")
        ports = [
            entry
            for entry in cell.children
            if entry.kind == "group" and entry.name in {"pin", "bus"}
        ]
        _reject_port_duplicates(ports, f"port in cell {cell_name}")
        for port in ports:
            self._port(
                library_name,
                cell_name,
                port,
                condition,
                units,
                templates,
                parent_bus=None,
            )

    def _port(
        self,
        library_name: str,
        cell_name: str,
        port: _Entry,
        condition: _OperatingCondition,
        units: dict[str, _DeclaredUnit],
        templates: dict[str, _Template],
        *,
        parent_bus: str | None,
    ) -> None:
        port_name = _one_port_name(port)
        base_case: dict[str, Scalar] = {
            "library": library_name,
            "cell": cell_name,
            "port": port_name,
            "port_kind": port.name,
            "operating_condition": condition.name,
            "process": condition.process,
            "vdd": condition.voltage,
            "temp_c": condition.temperature,
        }
        if parent_bus is not None:
            base_case["bus"] = parent_bus
        self._direct_scalars(port, base_case, cell_name, port_name, units)
        self._constraint_groups(port, base_case, cell_name, port_name, units)
        for timing in _groups(port.children, "timing"):
            _require_empty_arguments(timing, "pin timing group")
            context = self._context(timing, base_case, "timing")
            self._direct_scalars(timing, context, cell_name, port_name, units)
            for table in timing.children:
                if table.kind == "group" and table.name in _TIMING_TABLES:
                    time_unit = self._required_unit(units, "time", table.location)
                    self._table(
                        table,
                        context,
                        table.name,
                        time_unit,
                        cell_name,
                        port_name,
                        units,
                        templates,
                    )
        for power in _groups(port.children, "internal_power"):
            _require_empty_arguments(power, "internal_power group")
            context = self._context(power, base_case, "internal_power")
            for table in power.children:
                if table.kind == "group" and table.name in _POWER_TABLES:
                    energy_unit = self._internal_energy_unit(units, table.location)
                    self._table(
                        table,
                        context,
                        table.name,
                        energy_unit,
                        cell_name,
                        port_name,
                        units,
                        templates,
                    )

        nested_ports = [
            entry
            for entry in port.children
            if entry.kind == "group" and entry.name in {"pin", "bus"}
        ]
        if port.name != "bus" and nested_ports:
            raise _at(port.location, "nested ports are supported only inside a bus")
        if port.name == "bus":
            if any(entry.name != "pin" for entry in nested_ports):
                raise _at(port.location, "a bus may contain pin members but not another bus")
            _reject_port_duplicates(nested_ports, f"member pin in bus {port_name}")
            for member in nested_ports:
                self._port(
                    library_name,
                    cell_name,
                    member,
                    condition,
                    units,
                    templates,
                    parent_bus=port_name,
                )

    def _constraint_groups(
        self,
        port: _Entry,
        base_case: dict[str, Scalar],
        cell_name: str,
        port_name: str,
        units: dict[str, _DeclaredUnit],
    ) -> None:
        unit: _DeclaredUnit | None = None
        for group in _groups(port.children, "min_pulse_width"):
            _require_empty_arguments(group, "min_pulse_width group")
            context = self._context(group, base_case, "timing_constraint")
            emitted = False
            for attribute_name, metric in (
                ("constraint_high", "min_pulse_width_high"),
                ("constraint_low", "min_pulse_width_low"),
            ):
                attribute = _optional_one(
                    _attributes(group.children, attribute_name), attribute_name
                )
                if attribute is None:
                    continue
                unit = unit or self._required_unit(units, "time", attribute.location)
                value = self._counted_number(
                    _one_value_text(attribute, attribute_name), attribute.location
                )
                self._emit(
                    context,
                    metric,
                    _scaled(value, unit, attribute.location, metric),
                    unit.unit,
                    attribute.location,
                    cell_name,
                    port_name,
                )
                emitted = True
            if not emitted:
                raise _at(
                    group.location, "min_pulse_width has no constraint_high or constraint_low"
                )
        for group in _groups(port.children, "minimum_period"):
            _require_empty_arguments(group, "minimum_period group")
            context = self._context(group, base_case, "timing_constraint")
            attribute = _exactly_one(
                _attributes(group.children, "constraint"),
                "minimum_period constraint",
                missing_location=group.location,
            )
            unit = unit or self._required_unit(units, "time", attribute.location)
            value = self._counted_number(
                _one_value_text(attribute, "minimum_period constraint"), attribute.location
            )
            self._emit(
                context,
                "minimum_period",
                _scaled(value, unit, attribute.location, "minimum_period"),
                unit.unit,
                attribute.location,
                cell_name,
                port_name,
            )

    def _context(
        self, group: _Entry, base_case: dict[str, Scalar], analysis: str
    ) -> dict[str, Scalar]:
        result = dict(base_case)
        result["analysis"] = analysis
        for name in _CONTEXT_ATTRIBUTES:
            attribute = _optional_one(_attributes(group.children, name), name)
            if attribute is not None:
                result[name] = _one_value_text(attribute, name)
        mode = _optional_one(_calls(group.children, "mode"), "mode")
        if mode is not None:
            if len(mode.arguments) != 2:
                raise _at(mode.location, "mode requires exactly two arguments")
            result["mode_name"] = _argument_text(mode.arguments[0], mode.location)
            result["mode_value"] = _argument_text(mode.arguments[1], mode.location)
        return result

    def _direct_scalars(
        self,
        group: _Entry,
        base_case: dict[str, Scalar],
        cell_name: str,
        port_name: str,
        units: dict[str, _DeclaredUnit],
    ) -> None:
        for name in sorted(_DIRECT_TIME_SCALARS):
            attribute = _optional_one(_attributes(group.children, name), name)
            if attribute is None:
                continue
            unit = self._required_unit(units, "time", attribute.location)
            value = self._counted_number(_one_value_text(attribute, name), attribute.location)
            case = dict(base_case)
            case.setdefault("analysis", "timing_constraint")
            self._emit(
                case,
                name,
                _scaled(value, unit, attribute.location, name),
                unit.unit,
                attribute.location,
                cell_name,
                port_name,
            )

    def _table(
        self,
        table: _Entry,
        base_case: dict[str, Scalar],
        metric: str,
        metric_unit: _DeclaredUnit,
        cell_name: str,
        port_name: str,
        units: dict[str, _DeclaredUnit],
        templates: dict[str, _Template],
    ) -> None:
        self._table_count += 1
        if self._table_count > self._limits.max_tables:
            raise _at(table.location, "Liberty input exceeds table limit")
        if len(table.arguments) > 1:
            raise _at(table.location, f"{metric} accepts at most one template argument")
        template: _Template | None = None
        scalar = False
        if table.arguments:
            template_name = _argument_text(table.arguments[0], table.location)
            scalar = _fold(template_name) == "scalar"
            if not scalar:
                template = templates.get(_fold(template_name))
                if template is None:
                    raise _at(table.location, f"unknown lookup template {template_name!r}")
        axes: list[_Axis] = [] if template is None else list(template.axes)
        for axis_number in (1, 2):
            local_variable = _optional_one(
                _attributes(table.children, f"variable_{axis_number}"), f"variable_{axis_number}"
            )
            local_index = _optional_one(
                _calls(table.children, f"index_{axis_number}"), f"index_{axis_number}"
            )
            if local_variable is None and local_index is None:
                continue
            if local_index is None:
                raise _at(
                    table.location, f"local axis {axis_number} is missing index_{axis_number}"
                )
            if local_variable is not None:
                variable = _one_value_text(local_variable, f"variable_{axis_number}")
            elif axis_number <= len(axes):
                variable = axes[axis_number - 1].variable
            else:
                raise _at(
                    table.location, f"local axis {axis_number} is missing variable_{axis_number}"
                )
            axis = _Axis(
                variable,
                self._numeric_vector(local_index),
                self._axis_unit(variable, units, table.location),
                local_index.location,
            )
            if axis_number <= len(axes):
                axes[axis_number - 1] = axis
            elif axis_number == len(axes) + 1:
                axes.append(axis)
            else:
                raise _at(table.location, "lookup axes must be contiguous")
        if _calls(table.children, "index_3") or _attributes(table.children, "variable_3"):
            raise _at(table.location, "three-dimensional lookup tables are not supported")
        values_entry = _exactly_one(
            _calls(table.children, "values"),
            f"{metric} values",
            missing_location=table.location,
        )
        rows = self._numeric_rows(values_entry)
        if scalar:
            if axes or len(rows) != 1 or len(rows[0]) != 1:
                raise _at(
                    table.location, f"scalar {metric} must contain exactly one value and no axes"
                )
            self._emit(
                base_case,
                metric,
                _scaled(rows[0][0], metric_unit, table.location, metric),
                metric_unit.unit,
                table.location,
                cell_name,
                port_name,
            )
            return
        if len(axes) == 1:
            if len(rows) != 1 or len(rows[0]) != len(axes[0].values):
                raise _at(table.location, f"{metric} values do not match index_1")
            for first, value in zip(axes[0].values, rows[0], strict=True):
                case = self._axis_case(base_case, axes[0], 1, first)
                self._emit(
                    case,
                    metric,
                    _scaled(value, metric_unit, table.location, metric),
                    metric_unit.unit,
                    table.location,
                    cell_name,
                    port_name,
                )
            return
        if len(axes) == 2:
            if len(rows) != len(axes[0].values) or any(
                len(row) != len(axes[1].values) for row in rows
            ):
                raise _at(table.location, f"{metric} matrix dimensions do not match its indices")
            for first, row in zip(axes[0].values, rows, strict=True):
                for second, value in zip(axes[1].values, row, strict=True):
                    case = self._axis_case(base_case, axes[0], 1, first)
                    case = self._axis_case(case, axes[1], 2, second)
                    self._emit(
                        case,
                        metric,
                        _scaled(value, metric_unit, table.location, metric),
                        metric_unit.unit,
                        table.location,
                        cell_name,
                        port_name,
                    )
            return
        raise _at(
            table.location, f"{metric} requires scalar, one-dimensional, or two-dimensional data"
        )

    @staticmethod
    def _axis_case(
        base_case: dict[str, Scalar], axis: _Axis, number: int, value: float
    ) -> dict[str, Scalar]:
        result = dict(base_case)
        result[f"axis_{number}"] = axis.variable
        result[f"axis_{number}_value"] = _scaled(value, axis.unit, axis.location, f"axis_{number}")
        result[f"axis_{number}_unit"] = axis.unit.unit
        return result

    def _emit(
        self,
        case: dict[str, Scalar],
        metric: str,
        value: float,
        unit: str,
        location: SourceLocation,
        cell: str,
        port: str,
    ) -> None:
        if not math.isfinite(value):
            raise _at(location, f"{metric} conversion produced a non-finite value")
        normalized_case = tuple(sorted(case.items()))
        collision = (case_identity(normalized_case), _fold(metric))
        if collision in self._collisions:
            raise _at(location, f"duplicate or ambiguous measurement for metric {metric!r}")
        self._collisions.add(collision)
        self._points.append(
            {
                "case": dict(normalized_case),
                "sample": location.sample,
                "metrics": {metric: {"value": value, "unit": unit}},
            }
        )
        if len(self._points) > self._limits.max_output_points:
            raise _at(location, "conversion exceeds output point limit")
        source = MetricSource(metric, location, cell, port)
        if not self._sources or self._sources[-1] != source:
            self._sources.append(source)

    def _internal_energy_unit(
        self, units: dict[str, _DeclaredUnit], location: SourceLocation
    ) -> _DeclaredUnit:
        if "internal_energy" not in units:
            voltage = self._required_unit(units, "voltage", location)
            capacitance = self._required_unit(units, "capacitance", location)
            voltage_scale = convert(voltage.multiplier, voltage.unit, "V")
            capacitance_scale = convert(capacitance.multiplier, capacitance.unit, "F")
            energy_scale = voltage_scale * voltage_scale * capacitance_scale
            if not math.isfinite(energy_scale) or energy_scale <= 0:
                raise _at(location, "internal energy unit scale overflowed or underflowed")
            units["internal_energy"] = _DeclaredUnit(energy_scale, "J")
        return units["internal_energy"]

    def _axis_unit(
        self, variable: str, units: dict[str, _DeclaredUnit], location: SourceLocation
    ) -> _DeclaredUnit:
        if variable in _TIME_VARIABLES:
            return self._required_unit(units, "time", location)
        if variable in _CAPACITANCE_VARIABLES:
            return self._required_unit(units, "capacitance", location)
        if variable in _VOLTAGE_VARIABLES:
            return self._required_unit(units, "voltage", location)
        if variable in _DIMENSIONLESS_VARIABLES:
            return _DeclaredUnit(1.0, "1")
        raise _at(location, f"unsupported lookup variable {variable!r}; its unit is ambiguous")

    @staticmethod
    def _required_unit(
        units: dict[str, _DeclaredUnit], name: str, location: SourceLocation
    ) -> _DeclaredUnit:
        try:
            return units[name]
        except KeyError as error:
            raise _at(location, f"{name} unit is required by extracted data") from error

    def _numeric_vector(self, call: _Entry) -> tuple[float, ...]:
        rows = self._numeric_rows(call)
        if len(rows) != 1:
            raise _at(call.location, f"{call.name} must contain one numeric vector")
        if any(
            current <= previous for previous, current in zip(rows[0], rows[0][1:], strict=False)
        ):
            raise _at(call.location, f"{call.name} values must be strictly increasing")
        return rows[0]

    def _numeric_rows(self, call: _Entry) -> tuple[tuple[float, ...], ...]:
        if not call.arguments:
            raise _at(call.location, f"{call.name} must not be empty")
        rows: list[tuple[float, ...]] = []
        for argument in call.arguments:
            text = _argument_text(argument, call.location)
            pieces = [piece.strip() for piece in text.split(",")]
            if not pieces or any(not piece for piece in pieces):
                raise _at(call.location, f"{call.name} contains an empty numeric field")
            rows.append(tuple(self._counted_number(piece, call.location) for piece in pieces))
        return tuple(rows)

    def _counted_number(self, text: str, location: SourceLocation) -> float:
        self._numeric_values += 1
        if self._numeric_values > self._limits.max_numeric_values:
            raise _at(location, "Liberty input exceeds numeric value limit")
        return _number(text, location)


def _at(location: SourceLocation, message: str) -> InputError:
    return InputError(f"Liberty L{location.line}:C{location.column}: {message}")


def _fold(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _semantic_text(value: str, context: str, location: SourceLocation) -> str:
    if not value or value != value.strip() or not value.isprintable() or len(value) > 256:
        raise _at(
            location, f"{context} must be a printable non-empty string of at most 256 characters"
        )
    if unicodedata.normalize("NFC", value) != value:
        raise _at(location, f"{context} must use NFC-normalized Unicode")
    return value


def _argument_text(argument: tuple[_Token, ...], location: SourceLocation) -> str:
    if len(argument) != 1 or argument[0].kind not in {"WORD", "STRING"}:
        raise _at(location, "Liberty argument must be one bare or quoted value")
    return _semantic_text(argument[0].value, "argument", argument[0].location)


def _one_argument_text(entry: _Entry, context: str) -> str:
    if len(entry.arguments) != 1:
        raise _at(entry.location, f"{context} requires exactly one argument")
    return _argument_text(entry.arguments[0], entry.location)


def _one_port_name(entry: _Entry) -> str:
    if len(entry.arguments) != 1:
        raise _at(entry.location, "port name requires exactly one argument")
    argument = entry.arguments[0]
    if len(argument) == 1 and argument[0].kind in {"WORD", "STRING"}:
        return _argument_text(argument, entry.location)
    joined = "".join(token.value for token in argument)
    if _BUS_MEMBER_NAME.fullmatch(joined) is None:
        raise _at(entry.location, "port name must be one name or an unquoted bus member/range")
    return _semantic_text(joined, "port name", entry.location)


def _one_value_text(entry: _Entry, context: str) -> str:
    if len(entry.value) != 1 or entry.value[0].kind not in {"WORD", "STRING"}:
        raise _at(entry.location, f"{context} requires one bare or quoted value")
    return _semantic_text(entry.value[0].value, context, entry.value[0].location)


def _groups(entries: tuple[_Entry, ...], name: str) -> list[_Entry]:
    return [entry for entry in entries if entry.kind == "group" and entry.name == name]


def _attributes(entries: tuple[_Entry, ...], name: str) -> list[_Entry]:
    return [entry for entry in entries if entry.kind == "attribute" and entry.name == name]


def _calls(entries: tuple[_Entry, ...], name: str) -> list[_Entry]:
    return [entry for entry in entries if entry.kind == "call" and entry.name == name]


def _exactly_one(
    entries: list[_Entry], context: str, *, missing_location: SourceLocation | None = None
) -> _Entry:
    if len(entries) != 1:
        location = entries[1].location if len(entries) > 1 else missing_location
        location = location or SourceLocation(1, 1, 0)
        raise _at(location, f"{context} must appear exactly once")
    return entries[0]


def _optional_one(entries: list[_Entry], context: str) -> _Entry | None:
    if len(entries) > 1:
        raise _at(entries[1].location, f"duplicate {context}")
    return entries[0] if entries else None


def _reject_named_duplicates(groups: list[_Entry], context: str) -> None:
    names: set[str] = set()
    for group in groups:
        name = _one_argument_text(group, context)
        folded = _fold(name)
        if folded in names:
            raise _at(group.location, f"duplicate {context} name {name!r}")
        names.add(folded)


def _reject_port_duplicates(groups: list[_Entry], context: str) -> None:
    names: set[str] = set()
    for group in groups:
        name = _one_port_name(group)
        folded = _fold(name)
        if folded in names:
            raise _at(group.location, f"duplicate {context} name {name!r}")
        names.add(folded)


def _require_empty_arguments(group: _Entry, context: str) -> None:
    if group.arguments:
        raise _at(group.location, f"{context} does not accept arguments")


def _number(text: str, location: SourceLocation) -> float:
    if _NUMBER.fullmatch(text) is None:
        raise _at(location, f"invalid finite decimal number {text!r}")
    try:
        value = float(text)
    except (OverflowError, ValueError) as error:
        raise _at(location, f"invalid finite decimal number {text!r}") from error
    if not math.isfinite(value):
        raise _at(location, f"non-finite decimal number {text!r}")
    if value == 0.0 and any(character in "123456789" for character in text):
        raise _at(location, f"decimal number underflows binary floating point {text!r}")
    return value


def _declared_unit(
    text: str, expected_dimension: tuple[tuple[str, int], ...], location: SourceLocation
) -> _DeclaredUnit:
    match = _UNIT.fullmatch(text)
    if match is None:
        raise _at(location, f"invalid Liberty unit declaration {text!r}")
    multiplier = _number(match.group(1), location)
    if multiplier <= 0.0:
        raise _at(location, "Liberty unit multiplier must be positive")
    unit = _canonical_unit_spelling(match.group(2), expected_dimension)
    try:
        parsed = parse_unit(unit)
    except InputError as error:
        raise _at(location, f"unsupported Liberty unit {unit!r}") from error
    if parsed.dimension != expected_dimension:
        raise _at(location, f"Liberty unit {unit!r} has the wrong physical dimension")
    scaled = multiplier * parsed.scale
    if not math.isfinite(scaled) or scaled <= 0.0:
        raise _at(location, "Liberty unit scale is not finite and positive")
    return _DeclaredUnit(multiplier, unit)


def _canonical_unit_spelling(unit: str, expected_dimension: tuple[tuple[str, int], ...]) -> str:
    """Map Liberty's common lower-case base spelling to Regressistor units."""

    base_by_dimension: dict[tuple[tuple[str, int], ...], str] = {
        (("capacitance", 1),): "F",
        (("voltage", 1),): "V",
        (("current", 1),): "A",
        (("power", 1),): "W",
    }
    base = base_by_dimension.get(expected_dimension)
    if base is not None and unit[-1:].casefold() == base.casefold():
        return unit[:-1] + base
    return unit


def _scaled(value: float, declared: _DeclaredUnit, location: SourceLocation, context: str) -> float:
    scaled = value * declared.multiplier
    if not math.isfinite(scaled):
        raise _at(location, f"{context} unit scaling produced a non-finite value")
    if value != 0.0 and scaled == 0.0:
        raise _at(location, f"{context} unit scaling underflowed to zero")
    return scaled


def convert_liberty_nldm(
    payload: bytes,
    *,
    source_name: str = "<memory>",
    limits: LibertyLimits | None = None,
) -> LibertyConversion:
    """Convert bounded UTF-8 Liberty bytes into a validated bundle.

    ``source_name`` is descriptive only.  Integrity is bound to the exact byte
    sequence through ``source_sha256`` in both the result and bundle metadata.
    """

    selected_limits = limits or LibertyLimits()
    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    if not isinstance(source_name, str):
        raise TypeError("source_name must be a string")
    if len(payload) > selected_limits.max_bytes:
        raise InputError(f"Liberty input exceeds {selected_limits.max_bytes} byte limit")
    source_name = _semantic_text(source_name, "source name", SourceLocation(1, 1, 0))
    try:
        text = payload.decode("utf-8")
    except UnicodeError as error:
        raise InputError(f"invalid UTF-8 Liberty input: {error}") from error
    digest = hashlib.sha256(payload).hexdigest()
    if len(digest) != 64 or any(character not in _HEX for character in digest):  # pragma: no cover
        raise RuntimeError("SHA-256 implementation returned an invalid digest")
    tokens = _Lexer(text, selected_limits).tokenize()
    root = _Parser(tokens, selected_limits).parse()
    return _Converter(root, digest, source_name, selected_limits).convert()


def load_liberty_nldm(
    path: str | Path, *, limits: LibertyLimits | None = None
) -> LibertyConversion:
    """Read and convert one bounded Liberty file."""

    selected_limits = limits or LibertyLimits()
    source, payload = read_document(path, context="Liberty", max_bytes=selected_limits.max_bytes)
    return convert_liberty_nldm(payload, source_name=source.name, limits=selected_limits)


def write_liberty_bundle(
    conversion: LibertyConversion,
    destination: str | Path,
    *,
    force: bool = False,
    protected: Iterable[str | Path] = (),
) -> Path:
    """Write deterministic bundle JSON, refusing overwrite unless requested."""

    target = Path(destination)
    try:
        payload = (
            json.dumps(
                conversion.canonical_data(),
                indent=2,
                sort_keys=True,
                allow_nan=False,
                ensure_ascii=False,
            )
            + "\n"
        ).encode("utf-8")
        if len(payload) > MAX_DOCUMENT_BYTES:
            raise OutputError(
                f"Liberty bundle exceeds {MAX_DOCUMENT_BYTES} byte serialized output limit"
            )
        atomic_write_bytes(
            target,
            payload,
            context="bundle",
            force=force,
            protected=protected,
        )
    except OutputError:
        raise
    except OSError as error:
        raise OutputError(f"cannot write Liberty bundle {target}: {error}") from error
    except (InputError, TypeError, ValueError) as error:
        raise OutputError(f"cannot serialize Liberty bundle {target}: {error}") from error
    return target
