#!/usr/bin/env python3
"""Small command-line entry point for the THO Proposal Assistant MVP.

python tools/tho_assistant/tho_assistant.py analyze tools\\tho_assistant\\tests\\fixtures\\CodeSystem-example.json --output-dir build/tho-analysis
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib import error, parse, request
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
    resource = load_fhir_resource(path)
    if resource.get("resourceType") != "CodeSystem":
        actual = resource.get("resourceType", "unknown")
        raise AnalysisError(f"Expected CodeSystem, found {actual}")
    return resource


def load_fhir_resource(path: Path) -> dict[str, Any]:
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

    if not isinstance(resource, dict):
        raise AnalysisError(f"Expected a FHIR JSON object in {path}")
    return resource


def _load_json_or_fenced_json(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8-sig").strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        value = json.loads(text)
    except (OSError, json.JSONDecodeError) as error:
        raise AnalysisError(f"Unable to read Jira JSON from {path}: {error}") from error
    if not isinstance(value, dict):
        raise AnalysisError(f"Expected a JSON object in Jira input {path}")
    return value


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


def _iter_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _iter_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_strings(child)


def _jira_issues(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if payload.get("key") and isinstance(payload.get("fields"), dict):
        return [payload]
    issues = payload.get("issues")
    if isinstance(issues, list):
        return [issue for issue in issues if isinstance(issue, dict)]
    raise AnalysisError("Jira JSON must contain an issue or an issues array")


def find_valueset_usage(candidate_url: str | None, ig_dir: Path) -> list[dict[str, Any]]:
    """Find local ValueSets that directly include the candidate CodeSystem."""
    if not candidate_url:
        return []
    generated_resources = ig_dir / "fsh-generated" / "resources"
    output_dir = ig_dir / "output"
    if generated_resources.is_dir():
        scan_dir = generated_resources
        paths = sorted(set(scan_dir.glob("ValueSet-*.json")) | set(scan_dir.glob("ValueSet-*.xml")))
    elif output_dir.is_dir():
        scan_dir = output_dir
        paths = sorted(set(scan_dir.glob("ValueSet-*.json")) | set(scan_dir.glob("ValueSet-*.xml")))
    else:
        direct_paths = set(ig_dir.glob("ValueSet-*.json")) | set(ig_dir.glob("ValueSet-*.xml"))
        paths = sorted(direct_paths or (set(ig_dir.rglob("ValueSet-*.json")) | set(ig_dir.rglob("ValueSet-*.xml"))))
    usages_by_identity: dict[str, dict[str, Any]] = {}
    for path in paths:
        try:
            resource = load_fhir_resource(path)
        except AnalysisError:
            continue
        if resource.get("resourceType") != "ValueSet":
            continue
        includes = [
            item
            for item in _as_list((resource.get("compose") or {}).get("include"))
            if isinstance(item, dict)
        ]
        matching = [item for item in includes if item.get("system") == candidate_url]
        if not matching:
            continue
        other_systems = sorted(
            {
                item["system"]
                for item in includes
                if item.get("system") != candidate_url and item.get("system")
            }
        )
        selected_codes = sorted(
            {
                concept["code"]
                for item in matching
                for concept in _as_list(item.get("concept"))
                if isinstance(concept, dict) and concept.get("code")
            }
        )
        identity = resource.get("url") or resource.get("id") or str(path.resolve())
        usage = usages_by_identity.get(identity)
        if usage is None:
            usage = {
                "sources": [],
                "id": resource.get("id"),
                "url": resource.get("url"),
                "name": resource.get("name"),
                "title": resource.get("title"),
                "description": resource.get("description"),
                "inclusion": "selected-codes" if selected_codes else "all-codes",
                "selected_codes": selected_codes,
                "other_code_systems": other_systems,
                "tho_code_systems": [
                    system
                    for system in other_systems
                    if system.startswith("http://terminology.hl7.org/CodeSystem/")
                    or system.startswith("https://terminology.hl7.org/CodeSystem/")
                ],
            }
            usages_by_identity[identity] = usage
        usage["sources"].append(str(path.resolve()))
        usage["selected_codes"] = sorted(set(usage["selected_codes"]) | set(selected_codes))
        usage["other_code_systems"] = sorted(set(usage["other_code_systems"]) | set(other_systems))
        usage["tho_code_systems"] = sorted(
            set(usage["tho_code_systems"])
            | {
                system for system in other_systems
                if system.startswith("http://terminology.hl7.org/CodeSystem/")
                or system.startswith("https://terminology.hl7.org/CodeSystem/")
            }
        )
    return list(usages_by_identity.values())


def _jql_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def build_proposal_jql(resource: dict[str, Any], usages: list[dict[str, Any]]) -> str:
    terms: list[str] = []
    for value in (resource.get("url"), resource.get("name"), resource.get("title")):
        if isinstance(value, str) and value.strip():
            terms.append(value.strip())
    concepts = _flatten_concepts(
        [item for item in _as_list(resource.get("concept")) if isinstance(item, dict)]
    )
    for concept in concepts:
        for field in ("code", "display"):
            value = concept.get(field)
            if isinstance(value, str) and value.strip():
                terms.append(value.strip())
    for usage in usages:
        for field in ("url", "name", "title"):
            value = usage.get(field)
            if isinstance(value, str) and value.strip():
                terms.append(value.strip())
    clauses = [
        f'text ~ "{_jql_text(term)}"' for term in dict.fromkeys(terms)
    ]
    return "project = UP AND (" + " OR ".join(clauses) + ") ORDER BY updated DESC"


def search_jira_proposals(
    jira_url: str,
    jql: str,
    token: str | None = None,
    cookie: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    if not token and not cookie:
        raise AnalysisError("Jira search requires a PAT or browser-session cookie")
    query = parse.urlencode(
        {"jql": jql, "maxResults": 50, "fields": "*all"}
    )
    endpoint = jira_url.rstrip("/") + "/rest/api/2/search?" + query
    headers = {
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/151.0.0.0 Safari/537.36"
        ),
    }
    if cookie:
        headers["Cookie"] = cookie
    else:
        headers["Authorization"] = f"Bearer {token}"
    api_request = request.Request(
        endpoint,
        method="GET",
        headers=headers,
    )
    try:
        with request.urlopen(api_request, timeout=timeout) as response:
            payload = json.load(response)
    except error.HTTPError as exc:
        detail = exc.read(500).decode("utf-8", errors="replace")
        if exc.code == 403 and "awselb" in str(exc.headers).lower():
            raise AnalysisError(
                "HL7's AWS front end rejected Jira REST authentication. "
                "HL7 currently requires an authenticated browser-session cookie; "
                "set HL7_JIRA_COOKIE from a signed-in browser request."
            ) from exc
        raise AnalysisError(
            f"HL7 Jira search failed with HTTP {exc.code}: {detail}"
        ) from exc
    except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise AnalysisError(f"Unable to search HL7 Jira: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("issues"), list):
        raise AnalysisError("HL7 Jira returned an unexpected search response")
    return payload


def _mentioned_terms(terms: list[str], text: str) -> list[str]:
    return [
        term for term in terms
        if re.search(
            rf"(?<![A-Za-z0-9_-]){re.escape(term)}(?![A-Za-z0-9_-])",
            text,
            flags=re.IGNORECASE,
        )
    ]


def match_proposals(
    resource: dict[str, Any],
    concepts: list[dict[str, Any]],
    proposal_payloads: list[dict[str, Any]],
    valueset_usage: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    candidate_codes = [
        concept["code"] for concept in concepts if isinstance(concept.get("code"), str)
    ]
    contextual_terms = [
        value
        for value in (
            resource.get("name"),
            resource.get("title"),
            *(concept.get("display") for concept in concepts),
            *(usage.get(field) for usage in (valueset_usage or []) for field in ("name", "title")),
        )
        if isinstance(value, str) and value.strip()
    ]
    contextual_terms = list(dict.fromkeys(contextual_terms))
    local_canonicals = {
        value
        for value in (resource.get("url"), *(usage.get("url") for usage in (valueset_usage or [])))
        if isinstance(value, str) and value
    }
    matches: list[dict[str, Any]] = []
    for payload in proposal_payloads:
        for issue in _jira_issues(payload):
            fields = issue.get("fields") or {}
            searchable_text = "\n".join(_iter_strings(fields))
            mentioned_codes = [
                code
                for code in candidate_codes
                if re.search(rf"(?<![A-Za-z0-9_-]){re.escape(code)}(?![A-Za-z0-9_-])", searchable_text)
            ]
            matched_terms = _mentioned_terms(contextual_terms, searchable_text)
            canonicals = sorted(
                set(
                    canonical.rstrip(".")
                    for canonical in re.findall(
                        r"https?://(?:terminology\.hl7\.org|hl7\.org/fhir)/"
                        r"(?:CodeSystem|ValueSet)/[A-Za-z0-9._-]+",
                        searchable_text,
                    )
                )
            )
            matched_local_canonicals = sorted(
                canonical for canonical in local_canonicals if canonical in searchable_text
            )
            if not mentioned_codes and not matched_terms and not matched_local_canonicals:
                continue
            if candidate_codes and len(mentioned_codes) == len(candidate_codes):
                coverage = "full"
            elif mentioned_codes:
                coverage = "partial"
            elif matched_local_canonicals:
                coverage = "artifact"
            else:
                coverage = "contextual"
            status = fields.get("status") or {}
            resolution = fields.get("resolution")
            matches.append(
                {
                    "key": issue.get("key"),
                    "url": f"https://jira.hl7.org/browse/{issue.get('key')}",
                    "summary": fields.get("summary"),
                    "status": status.get("name") if isinstance(status, dict) else status,
                    "resolution": (
                        resolution.get("name")
                        if isinstance(resolution, dict)
                        else resolution
                    ),
                    "coverage": coverage,
                    "matched_codes": mentioned_codes,
                    "matched_terms": matched_terms,
                    "matched_local_canonicals": matched_local_canonicals,
                    "target_canonicals": canonicals,
                }
            )
    rank = {"full": 0, "partial": 1, "artifact": 2, "contextual": 3}
    return sorted(matches, key=lambda item: (rank[item["coverage"]], item.get("key") or ""))


def analyze(
    resource: dict[str, Any],
    source: Path,
    proposal_payloads: list[dict[str, Any]] | None = None,
    valueset_usage: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    concepts = _flatten_concepts(
        [item for item in _as_list(resource.get("concept")) if isinstance(item, dict)]
    )
    metadata = {field: resource[field] for field in METADATA_FIELDS if field in resource}
    result = {
        "schema_version": "0.1.0",
        "source": str(source),
        "resource_type": "CodeSystem",
        "metadata": metadata,
        "properties": _as_list(resource.get("property")),
        "concept_count": len(concepts),
        "concepts": concepts,
        "review_flags": _review_flags(resource, concepts),
    }
    if proposal_payloads:
        result["proposal_matches"] = match_proposals(
            resource, concepts, proposal_payloads, valueset_usage
        )
    if valueset_usage is not None:
        result["valueset_usage"] = valueset_usage
    return result


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
        lines.append(
            "No initial source-structure flags were found. THO artifact matching "
            "and governance review are still required."
        )

    if "proposal_matches" in analysis:
        lines.extend(["", "## Related THO proposals", ""])
        proposals = analysis["proposal_matches"]
        if proposals:
            lines.extend(
                [
                    "| Proposal | Status | Match | Evidence | Target artifacts |",
                    "|---|---|---|---|---|",
                ]
            )
            for proposal in proposals:
                proposal_link = f"[{proposal['key']}]({proposal['url']})"
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            proposal_link,
                            _escape_table(proposal.get("status")),
                            _escape_table(proposal.get("coverage")),
                            _escape_table(", ".join(
                                proposal.get("matched_codes", [])
                                or proposal.get("matched_terms", [])
                                or proposal.get("matched_local_canonicals", [])
                            )),
                            _escape_table(", ".join(proposal.get("target_canonicals", []))),
                        ]
                    )
                    + " |"
                )
        else:
            lines.append("No related proposal was found in the supplied Jira data.")

    if "valueset_usage" in analysis:
        lines.extend(["", "## Local ValueSet usage", ""])
        usages = analysis["valueset_usage"]
        if usages:
            lines.extend([
                "| ValueSet | Inclusion | Other CodeSystems | THO co-inclusions |",
                "|---|---|---|---|",
            ])
            for usage in usages:
                label = usage.get("title") or usage.get("name") or usage.get("id")
                lines.append(
                    "| " + " | ".join([
                        _escape_table(label),
                        _escape_table(usage.get("inclusion")),
                        _escape_table(", ".join(usage.get("other_code_systems", []))),
                        _escape_table(", ".join(usage.get("tho_code_systems", []))),
                    ]) + " |"
                )
        else:
            lines.append("No local ValueSet directly includes the candidate CodeSystem.")

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


def get_jira_credentials() -> tuple[str | None, str | None]:
    cookie = os.environ.get("HL7_JIRA_COOKIE")
    token = os.environ.get("HL7_JIRA_PAT")
    if not cookie and not token:
        if not sys.stdin.isatty():
            raise AnalysisError(
                "HL7_JIRA_COOKIE or HL7_JIRA_PAT is not set and a secure "
                "prompt is unavailable"
            )
        cookie = getpass.getpass("HL7 Jira browser Cookie header: ")
    if not cookie and not token:
        raise AnalysisError("HL7 Jira authentication is required")
    return token, cookie


def test_jira_access(
    jira_url: str, token: str | None, cookie: str | None
) -> dict[str, Any]:
    """Run a minimal UP-project search before any potentially slow IG scan."""
    return search_jira_proposals(
        jira_url, "project=UP", token=token, cookie=cookie
    )


def command_test_jira(args: argparse.Namespace) -> int:
    token, cookie = get_jira_credentials()
    payload = test_jira_access(args.jira_url, token, cookie)
    total = payload.get("total", len(payload.get("issues", [])))
    auth_type = "browser session" if cookie else "personal access token"
    print(f"HL7 Jira connection succeeded using {auth_type}.")
    print(f"Project UP is visible ({total} matching issues reported).")
    return 0


def command_analyze(args: argparse.Namespace) -> int:
    source = args.input.resolve()
    output_dir = args.output_dir.resolve()
    resource = load_resource(source)
    proposal_payloads = [
        _load_json_or_fenced_json(path.resolve()) for path in args.proposal_file
    ]
    token: str | None = None
    cookie: str | None = None
    if args.search_proposals:
        token, cookie = get_jira_credentials()
        test_jira_access(args.jira_url, token, cookie)
        print("HL7 Jira preflight succeeded; scanning local IG content.")
    valueset_usage = (
        find_valueset_usage(resource.get("url"), args.ig_dir.resolve())
        if args.ig_dir else None
    )
    if args.search_proposals:
        jql = build_proposal_jql(resource, valueset_usage or [])
        proposal_payloads.append(
            search_jira_proposals(
                args.jira_url, jql, token=token, cookie=cookie
            )
        )
    result = analyze(resource, source, proposal_payloads, valueset_usage)
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
    test_parser = subparsers.add_parser(
        "test-jira", help="Test Jira authentication and access to project UP"
    )
    test_parser.add_argument(
        "--jira-url",
        default="https://jira.hl7.org",
        help="Jira base URL (default: https://jira.hl7.org)",
    )
    test_parser.set_defaults(handler=command_test_jira)
    analyze_parser = subparsers.add_parser("analyze", help="Analyze a candidate CodeSystem")
    analyze_parser.add_argument("input", type=Path, help="FHIR CodeSystem JSON or XML")
    analyze_parser.add_argument("--output-dir", type=Path, required=True, help="Directory for generated analysis")
    analyze_parser.add_argument(
        "--ig-dir",
        type=Path,
        help="IG directory to scan recursively for ValueSets using the candidate",
    )
    analyze_parser.add_argument(
        "--proposal-file",
        action="append",
        default=[],
        type=Path,
        help="Jira issue or search-response JSON; may be repeated",
    )
    analyze_parser.add_argument(
        "--search-proposals",
        action="store_true",
        help="Search Jira using HL7_JIRA_COOKIE or HL7_JIRA_PAT",
    )
    analyze_parser.add_argument(
        "--jira-url",
        default="https://jira.hl7.org",
        help="Jira base URL (default: https://jira.hl7.org)",
    )
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
