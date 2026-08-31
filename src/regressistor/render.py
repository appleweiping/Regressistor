"""Human-readable, Markdown, and JUnit report renderers."""

from __future__ import annotations

import html
import xml.etree.ElementTree as ET  # nosec B405
from pathlib import Path

from regressistor.bundle import case_label
from regressistor.errors import OutputError
from regressistor.model import Decision, Status
from regressistor.report import Report


def _display(value: float | None) -> str:
    return "—" if value is None else f"{value:.12g}"


def _safe_display_text(value: str) -> str:
    """Replace control characters before terminal, Markdown, or XML rendering."""
    return "".join(character if character.isprintable() else " " for character in value)


def console_summary(report: Report) -> str:
    outcome = "PASS" if report.passed else "FAIL"
    counts = ", ".join(f"{name}={count}" for name, count in report.counts.items() if count)
    return f"Regressistor {outcome}: {len(report.decisions)} decisions ({counts})"


def decision_text(decision: Decision) -> str:
    lines = [
        f"metric: {_safe_display_text(decision.metric)}",
        f"case: {_safe_display_text(case_label(decision.case))}",
        f"status: {decision.status.value}",
        f"severity: {decision.severity.value}",
        f"blocking: {str(decision.blocking).lower()}",
        f"baseline: {_display(decision.baseline)} {_safe_display_text(decision.unit)}",
        f"candidate: {_display(decision.candidate)} {_safe_display_text(decision.unit)}",
    ]
    if decision.contract_margin is not None:
        lines.append(
            f"contract margin: {_display(decision.contract_margin)} "
            f"{_safe_display_text(decision.unit)}"
        )
    if decision.regression_margin is not None:
        lines.append(
            f"regression margin: {_display(decision.regression_margin)} "
            f"{_safe_display_text(decision.unit)}"
        )
    lines.append(f"reason: {_safe_display_text(decision.message)}")
    return "\n".join(lines)


def markdown(report: Report) -> str:
    outcome = "PASS" if report.passed else "FAIL"
    lines = [
        f"# Regressistor report: {outcome}",
        "",
        "| Metric | Case | Status | Baseline | Candidate | Contract margin | Regression margin |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for decision in report.decisions:
        metric = _markdown_cell(_safe_display_text(decision.metric))
        case = _markdown_cell(_safe_display_text(case_label(decision.case)))
        status = decision.status.value.upper()
        lines.append(
            f"| {metric} | {case} | {status} | {_display(decision.baseline)} | "
            f"{_display(decision.candidate)} | {_display(decision.contract_margin)} | "
            f"{_display(decision.regression_margin)} |"
        )
    lines.extend(["", "## Diagnostics", ""])
    for decision in report.decisions:
        if decision.status is not Status.PASS:
            lines.append(
                f"- **{_markdown_cell(_safe_display_text(decision.metric))}** at "
                f"{_markdown_cell(_safe_display_text(case_label(decision.case)))}: "
                f"{_markdown_cell(_safe_display_text(decision.message))}"
            )
    if all(decision.status is Status.PASS for decision in report.decisions):
        lines.append("- No failures or warnings.")
    return "\n".join(lines) + "\n"


def _markdown_cell(value: str) -> str:
    flattened = value.replace("\r", " ").replace("\n", " ")
    escaped = html.escape(flattened, quote=True).replace("\\", "\\\\")
    for marker in ("|", "*", "_", "[", "]", "#"):
        escaped = escaped.replace(marker, f"\\{marker}")
    return escaped.replace("`", "&#96;")


def junit_xml(report: Report) -> str:
    suite = ET.Element(
        "testsuite",
        {
            "name": "regressistor",
            "tests": str(len(report.decisions)),
            "failures": str(sum(decision.blocking for decision in report.decisions)),
        },
    )
    for decision in report.decisions:
        case = case_label(decision.case)
        test = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": f"regressistor.{_safe_display_text(decision.metric)}",
                "name": _safe_display_text(case),
            },
        )
        if decision.blocking:
            failure = ET.SubElement(
                test,
                "failure",
                {
                    "type": decision.status.value,
                    "message": _safe_display_text(decision.message),
                },
            )
            failure.text = decision_text(decision)
        elif decision.status is not Status.PASS:
            output = ET.SubElement(test, "system-out")
            output.text = decision_text(decision)
    ET.indent(suite)
    return ET.tostring(suite, encoding="unicode", xml_declaration=True) + "\n"


def write_artifacts(report: Report, directory: str | Path) -> tuple[Path, Path, Path]:
    target = Path(directory)
    try:
        target.mkdir(parents=True, exist_ok=True)
        json_path = report.write_json(target / "report.json")
        markdown_path = target / "summary.md"
        junit_path = target / "junit.xml"
        markdown_path.write_text(markdown(report), encoding="utf-8")
        junit_path.write_text(junit_xml(report), encoding="utf-8")
    except OSError as error:
        raise OutputError(f"cannot write artifacts under {target}: {error}") from error
    return json_path, markdown_path, junit_path


def safe_html_text(text: str) -> str:
    """Escape report-originated text for downstream HTML integrations."""
    return html.escape(text, quote=True)
