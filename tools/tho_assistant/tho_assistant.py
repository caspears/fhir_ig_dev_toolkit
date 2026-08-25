#!/usr/bin/env python3
"""Small command-line entry point for the THO Proposal Assistant MVP.

python tools/tho_assistant/tho_assistant.py analyze tools\\tho_assistant\\tests\\fixtures\\CodeSystem-example.json --output-dir build/tho-analysis
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


FHIR_NS = "http://hl7.org/fhir"
METADATA_FIELDS = (
    "id",
    "url",
    "identifier",
    "version",
    "name",
    "title",
    "status",
    "experimental",
    "date",
    "publisher",
    "description",
    "purpose",
    "copyright",
    "caseSensitive",
    "valueSet",
    "hierarchyMeaning",
    "compositional",
    "versionNeeded",
    "content",
    "count",
)


class AnalysisError(ValueError):
    """Raised when an input cannot be analyzed as a FHIR CodeSystem."""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_value(element: ElementTree.Element) -> Any:
    """Convert the subset of FHIR XML needed by the MVP to JSON-like data."""
    value = element.get("value")
    children = list(element)
    if value is not None and not children:
        name = _local_name(element.tag)
        if name in {"caseSensitive", "compositional", "experimental", "versionNeeded"}:
            return value == "true"
        if name == "count":
            return int(value)
        return value

    result: dict[str, Any] = {}
    for child in children:
        name = _local_name(child.tag)
        child_value = _xml_value(child)
        if name in result:
            if not isinstance(result[name], list):
                result[name] = [result[name]]
            result[name].append(child_value)
        else:
            result[name] = child_value
    return result


def load_resource(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            resource = json.loads(path.read_text(encoding="utf-8-sig"))
        elif suffix == ".xml":
            root = ElementTree.parse(path).getroot()
            resource = _xml_value(root)
            resource["resourceType"] = _local_name(root.tag)
        else:
            raise AnalysisError("Input must have a .json or .xml extension")
    except (OSError, json.JSONDecodeError, ElementTree.ParseError) as error:
        raise AnalysisError(f"Unable to read {path}: {error}") from error

    if resource.get("resourceType") != "CodeSystem":
        actual = resource.get("resourceType", "unknown")
        raise AnalysisError(f"Expected CodeSystem, found {actual}")
    return resource


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _flatten_concepts(
    concepts: list[dict[str, Any]], parent: str | None = None
) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for concept in concepts:
        entry = {
            "code": concept.get("code"),
            "display": concept.get("display"),
            "definition": concept.get("definition"),
            "parent": parent,
            "designation": _as_list(concept.get("designation")),
            "property": _as_list(concept.get("property")),
        }
        flattened.append(entry)
        children = [
            item
            for item in _as_list(concept.get("concept"))
            if isinstance(item, dict)
        ]
        flattened.extend(_flatten_concepts(children, concept.get("code")))
    return flattened


def _review_flags(resource: dict[str, Any], concepts: list[dict[str, Any]]) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []
    required_metadata = ("url", "version", "name", "title", "status", "description", "content")
    for field in required_metadata:
        if resource.get(field) in (None, ""):
            flags.append(
                {
                    "severity": "warning",
                    "code": f"missing-{field}",
                    "message": f"CodeSystem.{field} is missing.",
                }
            )

    if resource.get("caseSensitive") is not True:
        flags.append(
            {
                "severity": "information",
                "code": "review-case-sensitive",
                "message": (
                    "THO content is case-sensitive; review whether "
                    "caseSensitive should be true."
                ),
            }
        )

    for concept in concepts:
        code = concept.get("code") or "(missing code)"
        for field in ("code", "display", "definition"):
            if concept.get(field) in (None, ""):
                flags.append(
                    {
                        "severity": "warning",
                        "code": f"concept-missing-{field}",
                        "message": f"Concept {code} has no {field}.",
                    }
                )

    duplicate_codes: set[str] = set()
    seen_codes: set[str] = set()
    for concept in concepts:
        code = concept.get("code")
        if code and code in seen_codes:
            duplicate_codes.add(code)
        if code:
            seen_codes.add(code)
    for code in sorted(duplicate_codes):
        flags.append(
            {
                "severity": "error",
                "code": "duplicate-code",
                "message": f"Concept code {code} occurs more than once.",
            }
        )
    return flags


def analyze(resource: dict[str, Any], source: Path) -> dict[str, Any]:
    concepts = _flatten_concepts(
        [item for item in _as_list(resource.get("concept")) if isinstance(item, dict)]
    )
    metadata = {field: resource[field] for field in METADATA_FIELDS if field in resource}
    return {
        "schema_version": "0.1.0",
        "source": str(source),
        "resource_type": "CodeSystem",
        "metadata": metadata,
        "properties": _as_list(resource.get("property")),
        "concept_count": len(concepts),
        "concepts": concepts,
        "review_flags": _review_flags(resource, concepts),
    }


def _escape_table(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(analysis: dict[str, Any]) -> str:
    metadata = analysis["metadata"]
    lines = [
        "# THO candidate CodeSystem analysis",
        "",
        f"- Title: {_escape_table(metadata.get('title') or metadata.get('name'))}",
        f"- Canonical: {_escape_table(metadata.get('url'))}",
        f"- Version: {_escape_table(metadata.get('version'))}",
        f"- Concepts: {analysis['concept_count']}",
        "",
        "## Review flags",
        "",
    ]
    flags = analysis["review_flags"]
    if flags:
        lines.extend(f"- **{flag['severity'].upper()}**: {flag['message']}" for flag in flags)
    else:
        lines.append("No initial structural review flags were found.")

    lines.extend([
        "",
        "## Concepts",
        "",
        "| Code | Display | Definition | Parent |",
        "|---|---|---|---|",
    ])
    for concept in analysis["concepts"]:
        lines.append(
            "| " + " | ".join(
                _escape_table(concept.get(field))
                for field in ("code", "display", "definition", "parent")
            ) + " |"
        )
    lines.append("")
    return "\n".join(lines)


def command_analyze(args: argparse.Namespace) -> int:
    source = args.input.resolve()
    output_dir = args.output_dir.resolve()
    resource = load_resource(source)
    result = analyze(resource, source)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "analysis.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output_dir / "concept-inventory.md").write_text(
        render_markdown(result), encoding="utf-8"
    )
    print(f"Analyzed {result['concept_count']} concepts from {source}")
    print(f"Wrote {output_dir / 'analysis.json'}")
    print(f"Wrote {output_dir / 'concept-inventory.md'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare IG terminology for a THO proposal"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    analyze_parser = subparsers.add_parser("analyze", help="Analyze a candidate CodeSystem")
    analyze_parser.add_argument("input", type=Path, help="FHIR CodeSystem JSON or XML")
    analyze_parser.add_argument("--output-dir", type=Path, required=True, help="Directory for generated analysis")
    analyze_parser.set_defaults(handler=command_analyze)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.handler(args)
    except AnalysisError as error:
        parser.error(str(error))
        return 2


if __name__ == "__main__":
    sys.exit(main())
