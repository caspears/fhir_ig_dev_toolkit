#!/usr/bin/env python3
"""Small command-line entry point for the THO Proposal Assistant MVP.

python tools/tho_assistant/tho_assistant.py analyze tools\\tho_assistant\\tests\\fixtures\\CodeSystem-example.json --output-dir build/tho-analysis
"""

from __future__ import annotations

import argparse
import copy
import difflib
import getpass
import importlib.util
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib import error, parse, request
from xml.etree import ElementTree


FHIR_NS = "http://hl7.org/fhir"
_draft_spec = importlib.util.spec_from_file_location("tho_draft_builds", Path(__file__).with_name("draft_builds.py"))
draft_builds = importlib.util.module_from_spec(_draft_spec)
_draft_spec.loader.exec_module(draft_builds)
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


def _artifact_paths(ig_dir: Path, resource_type: str) -> list[Path]:
    pattern_json = f"{resource_type}-*.json"
    pattern_xml = f"{resource_type}-*.xml"
    generated_resources = ig_dir / "fsh-generated" / "resources"
    output_dir = ig_dir / "output"
    if generated_resources.is_dir():
        scan_dir = generated_resources
        return sorted(set(scan_dir.glob(pattern_json)) | set(scan_dir.glob(pattern_xml)))
    elif output_dir.is_dir():
        scan_dir = output_dir
        return sorted(set(scan_dir.glob(pattern_json)) | set(scan_dir.glob(pattern_xml)))
    direct_paths = set(ig_dir.glob(pattern_json)) | set(ig_dir.glob(pattern_xml))
    return sorted(
        direct_paths
        or (set(ig_dir.rglob(pattern_json)) | set(ig_dir.rglob(pattern_xml)))
    )


def find_valueset_usage(candidate_url: str | None, ig_dir: Path) -> list[dict[str, Any]]:
    """Find local ValueSets that directly include the candidate CodeSystem."""
    if not candidate_url:
        return []
    paths = _artifact_paths(ig_dir, "ValueSet")
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


def find_structuredefinition_bindings(
    valueset_usage: list[dict[str, Any]], ig_dir: Path
) -> list[dict[str, Any]]:
    """Find generated profiles whose elements bind to discovered local ValueSets."""
    valueset_urls = {
        usage["url"] for usage in valueset_usage if isinstance(usage.get("url"), str)
    }
    if not valueset_urls:
        return []
    profiles: dict[str, dict[str, Any]] = {}
    for path in _artifact_paths(ig_dir, "StructureDefinition"):
        try:
            resource = load_fhir_resource(path)
        except AnalysisError:
            continue
        if resource.get("resourceType") != "StructureDefinition":
            continue
        found: dict[tuple[str, str, str | None], set[str]] = {}
        for section_name in ("differential", "snapshot"):
            section = resource.get(section_name) or {}
            for element in _as_list(section.get("element")):
                if not isinstance(element, dict):
                    continue
                binding = element.get("binding") or {}
                value_set = binding.get("valueSet")
                if not isinstance(value_set, str):
                    continue
                unversioned = value_set.split("|", 1)[0]
                if unversioned not in valueset_urls:
                    continue
                key = (element.get("path") or element.get("id"), value_set, binding.get("strength"))
                found.setdefault(key, set()).add(section_name)
        if not found:
            continue
        identity = resource.get("url") or resource.get("id") or str(path.resolve())
        profile = profiles.get(identity)
        if profile is None:
            profile = {
                "sources": [],
                "id": resource.get("id"),
                "url": resource.get("url"),
                "name": resource.get("name"),
                "title": resource.get("title"),
                "type": resource.get("type"),
                "kind": resource.get("kind"),
                "base_definition": resource.get("baseDefinition"),
                "bindings": [],
            }
            profiles[identity] = profile
        profile["sources"].append(str(path.resolve()))
        existing = {
            (item["path"], item["value_set"], item.get("strength")): item
            for item in profile["bindings"]
        }
        for (element_path, value_set, strength), sections in found.items():
            key = (element_path, value_set, strength)
            item = existing.get(key)
            if item is None:
                item = {
                    "path": element_path,
                    "value_set": value_set,
                    "strength": strength,
                    "sections": [],
                }
                profile["bindings"].append(item)
                existing[key] = item
            item["sections"] = sorted(set(item["sections"]) | sections)
        profile["bindings"].sort(key=lambda item: (item["path"], item["value_set"]))
    return sorted(profiles.values(), key=lambda item: item.get("url") or item.get("id") or "")


def _canonical_url(value: Any) -> str | None:
    """Return an unversioned canonical URL when value is a canonical string."""
    if not isinstance(value, str) or not value:
        return None
    return value.split("|", 1)[0]


def _package_resource_paths(package_dir: Path, resource_type: str) -> list[Path]:
    """Find JSON resources in an extracted FHIR package or its package folder."""
    roots = [package_dir / "package", package_dir]
    patterns = (f"{resource_type}-*.json", f"{resource_type.lower()}-*.json")
    for root in roots:
        if not root.is_dir():
            continue
        paths: set[Path] = set()
        for pattern in patterns:
            paths.update(root.glob(pattern))
        if paths:
            return sorted(paths)
    return []


def _package_declared_version(package_dir: Path) -> str | None:
    for metadata_path in (
        package_dir / "package" / "package.json",
        package_dir / "package.json",
    ):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        version = metadata.get("version") if isinstance(metadata, dict) else None
        if isinstance(version, str) and version:
            return version
    if "#" in package_dir.name:
        return package_dir.name.rsplit("#", 1)[1]
    return None


def _version_sort_key(version: str) -> tuple[Any, ...]:
    """Create a practical semantic-version key without adding a dependency."""
    main, separator, suffix = version.partition("-")
    numeric = tuple(int(part) for part in re.findall(r"\d+", main))
    return numeric, not bool(separator), suffix.casefold()


def resolve_latest_package_dir(package_dir: Path) -> tuple[Path, str | None]:
    """Resolve an unversioned package-family path to its latest installed version."""
    expanded = package_dir.expanduser()
    if expanded.is_dir():
        resolved = expanded.resolve()
        return resolved, _package_declared_version(resolved)
    if "#" in expanded.name:
        return expanded.resolve(), None
    candidates: list[tuple[tuple[Any, ...], Path, str]] = []
    parent = expanded.parent
    if parent.is_dir():
        for candidate in parent.glob(f"{expanded.name}#*"):
            if not candidate.is_dir():
                continue
            version = _package_declared_version(candidate)
            if version:
                candidates.append((_version_sort_key(version), candidate.resolve(), version))
    if not candidates:
        return expanded.resolve(), None
    _, selected, version = max(candidates, key=lambda item: item[0])
    return selected, version


def _index_package_resources(
    package_dir: Path, resource_type: str
) -> dict[str, dict[str, Any]]:
    resources: dict[str, dict[str, Any]] = {}
    for path in _package_resource_paths(package_dir, resource_type):
        try:
            resource = load_fhir_resource(path)
        except AnalysisError:
            continue
        if resource.get("resourceType") != resource_type:
            continue
        resource["_source"] = str(path.resolve())
        for identity in (resource.get("url"), resource.get("id")):
            if isinstance(identity, str) and identity:
                resources[identity] = resource
    return resources


def _find_element(resource: dict[str, Any], path: str) -> dict[str, Any] | None:
    for section_name in ("snapshot", "differential"):
        elements = _as_list((resource.get(section_name) or {}).get("element"))
        for element in elements:
            if isinstance(element, dict) and element.get("path") == path:
                return element
    return None


def _valueset_code_systems(value_set: dict[str, Any]) -> list[str]:
    systems = {
        include.get("system")
        for include in _as_list((value_set.get("compose") or {}).get("include"))
        if isinstance(include, dict) and include.get("system")
    }

    def collect_contains(items: list[Any]) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("system"):
                systems.add(item["system"])
            collect_contains(_as_list(item.get("contains")))

    collect_contains(_as_list((value_set.get("expansion") or {}).get("contains")))
    return sorted(systems)


def _text_comparison(candidate: Any, target: Any) -> str:
    if target in (None, ""):
        return "target-missing"
    if candidate in (None, ""):
        return "candidate-missing"
    if candidate == target:
        return "exact"
    if str(candidate).casefold() == str(target).casefold():
        return "case-insensitive"
    return "different"


def compare_tho_target_artifacts(
    proposal_matches: list[dict[str, Any]],
    concepts: list[dict[str, Any]],
    package_dir: Path,
) -> list[dict[str, Any]]:
    """Compare proposal target artifacts with an installed THO package."""
    if not package_dir.is_dir():
        raise AnalysisError(f"THO package directory does not exist: {package_dir}")
    code_systems = _index_package_resources(package_dir, "CodeSystem")
    value_sets = _index_package_resources(package_dir, "ValueSet")
    if not code_systems and not value_sets:
        raise AnalysisError(
            "No CodeSystem or ValueSet JSON files were found in the THO package "
            f"directory or its package subdirectory: {package_dir}"
        )
    compared = copy.deepcopy(proposal_matches)
    candidate_by_code = {
        concept["code"]: concept
        for concept in concepts
        if isinstance(concept.get("code"), str)
    }
    for proposal in compared:
        artifacts: list[dict[str, Any]] = []
        for canonical in proposal.get("target_canonicals", []):
            artifact_type = None
            index: dict[str, dict[str, Any]] = {}
            if "/CodeSystem/" in canonical:
                artifact_type = "CodeSystem"
                index = code_systems
            elif "/ValueSet/" in canonical:
                artifact_type = "ValueSet"
                index = value_sets
            if artifact_type is None:
                continue
            artifact = index.get(canonical) or index.get(canonical.rsplit("/", 1)[-1])
            if artifact is None:
                artifacts.append(
                    {
                        "canonical": canonical,
                        "resource_type": artifact_type,
                        "status": "not-found-in-package",
                    }
                )
                continue
            result: dict[str, Any] = {
                "canonical": canonical,
                "resource_type": artifact_type,
                "status": "found",
                "id": artifact.get("id"),
                "url": artifact.get("url"),
                "version": artifact.get("version"),
                "name": artifact.get("name"),
                "title": artifact.get("title"),
                "source": artifact.get("_source"),
            }
            if artifact_type == "CodeSystem":
                target_concepts = {
                    concept["code"]: concept
                    for concept in _flatten_concepts(
                        [
                            item
                            for item in _as_list(artifact.get("concept"))
                            if isinstance(item, dict)
                        ]
                    )
                    if isinstance(concept.get("code"), str)
                }
                concept_results: list[dict[str, Any]] = []
                result["style_review"] = style_inventory(list(target_concepts.values()))
                result["style_review"]["scope"] = "installed-target-CodeSystem"
                result["style_review"]["note"] = "Installed target inventory; existing codes are preserved. Addition style assessments require human review."
                for code, candidate in candidate_by_code.items():
                    proposed = next((row for row in proposal.get("proposed_concepts", [])
                                     if row["code"] == code), {})
                    if proposed.get("draft_canonical") and proposed["draft_canonical"] != canonical:
                        proposed = {}
                    target_code = proposed.get("proposed_code", code)
                    target = target_concepts.get(target_code)
                    style_assessment = target_style_assessment(target_code, target, result["style_review"])
                    concept_results.append(
                        {
                            "code": code,
                            "target_code": target_code,
                            "style_assessment": style_assessment,
                            "mapping_status": proposed.get("mapping_status", "unresolved"),
                            "status": "existing-code" if target else "absent-code",
                            "candidate_display": candidate.get("display"),
                            "target_display": target.get("display") if target else None,
                            "display_comparison": _text_comparison(
                                candidate.get("display"),
                                target.get("display") if target else None,
                            ),
                            "candidate_definition": candidate.get("definition"),
                            "target_definition": target.get("definition") if target else None,
                            "definition_comparison": _text_comparison(
                                candidate.get("definition"),
                                target.get("definition") if target else None,
                            ),
                        }
                    )
                result["concept_comparison"] = concept_results
            else:
                result["included_code_systems"] = _valueset_code_systems(artifact)
            artifacts.append(result)
        proposal["tho_target_artifacts"] = artifacts
        proposed_by_code = {row["code"]: row for row in proposal.get("proposed_concepts", [])}
        for artifact in artifacts:
            for row in artifact.get("concept_comparison", []):
                proposed = proposed_by_code.get(row["code"], {})
                if proposed.get("draft_canonical") and proposed["draft_canonical"] != artifact["canonical"]:
                    proposed = {}
                row["proposed_source"] = proposed.get("proposed_source", "jira-text")
                row["draft_provenance"] = proposed.get("draft_provenance")
                row["proposal_status"] = proposed.get("status", "not-extracted")
                row["proposed_display"] = proposed.get("display")
                row["proposed_definition"] = proposed.get("definition")
                row["inferred_change"] = "manual-review"
                if proposed.get("status") in {"extracted", "alternate-code-candidate"}:
                    row["candidate_proposed_definition_comparison"] = _text_comparison(
                        row.get("candidate_definition"), proposed["definition"])
                    if row["status"] == "absent-code":
                        row["inferred_change"] = "add-code-relative-to-package"
                    else:
                        changes = [field for field in ("display", "definition")
                                   if row.get("target_" + field) != proposed[field]]
                        row["inferred_change"] = "change-" + "-and-".join(changes) if changes else "no-text-change"
    return compared


def compare_base_fhir_bindings(
    binding_context: list[dict[str, Any]], package_dir: Path
) -> list[dict[str, Any]]:
    """Resolve local profile bindings against an extracted base FHIR package."""
    if not package_dir.is_dir():
        raise AnalysisError(
            f"FHIR package directory does not exist: {package_dir}"
        )
    structures = _index_package_resources(package_dir, "StructureDefinition")
    if not structures:
        raise AnalysisError(
            "No StructureDefinition JSON files were found in the FHIR package "
            f"directory or its package subdirectory: {package_dir}"
        )
    value_sets = _index_package_resources(package_dir, "ValueSet")
    compared = copy.deepcopy(binding_context)
    for profile in compared:
        starting_base = _canonical_url(profile.get("base_definition"))
        for local_binding in profile.get("bindings", []):
            comparison: dict[str, Any] = {
                "status": "base-structuredefinition-not-found",
                "starting_base_definition": starting_base,
            }
            current_url = starting_base
            visited: set[str] = set()
            while current_url and current_url not in visited:
                visited.add(current_url)
                base = structures.get(current_url) or structures.get(
                    current_url.rsplit("/", 1)[-1]
                )
                if base is None:
                    comparison["missing_structure_definition"] = current_url
                    break
                comparison["status"] = "base-element-not-found"
                comparison["base_structure_definition"] = base.get("url")
                comparison["base_structure_definition_source"] = base.get("_source")
                element = _find_element(base, local_binding.get("path"))
                if element is not None:
                    comparison.update(
                        {
                            "status": "base-element-unbound",
                            "base_element_path": element.get("path"),
                            "base_element_short": element.get("short"),
                            "base_element_definition": element.get("definition"),
                            "base_element_types": sorted(
                                {
                                    item["code"]
                                    for item in _as_list(element.get("type"))
                                    if isinstance(item, dict) and item.get("code")
                                }
                            ),
                        }
                    )
                    binding = element.get("binding") or {}
                    base_value_set = binding.get("valueSet")
                    if binding.get("strength") or base_value_set:
                        comparison.update(
                            {
                                "status": "resolved",
                                "base_binding_strength": binding.get("strength"),
                                "base_value_set": base_value_set,
                            }
                        )
                        value_set_url = _canonical_url(base_value_set)
                        value_set = None
                        if value_set_url:
                            value_set = value_sets.get(value_set_url) or value_sets.get(
                                value_set_url.rsplit("/", 1)[-1]
                            )
                        if value_set:
                            comparison["base_value_set_details"] = {
                                "url": value_set.get("url"),
                                "name": value_set.get("name"),
                                "title": value_set.get("title"),
                                "code_systems": _valueset_code_systems(value_set),
                                "source": value_set.get("_source"),
                            }
                        elif value_set_url:
                            comparison["base_value_set_status"] = "not-found-in-package"
                    break
                current_url = _canonical_url(base.get("baseDefinition"))
            local_binding["base_fhir_comparison"] = comparison
    return compared


def _jql_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def discover_context_targets(usages: list[dict[str, Any]], bindings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    targets: dict[str, list[dict[str, Any]]] = {}
    for usage in usages:
        for system in usage.get("tho_code_systems", []):
            targets.setdefault(system, []).append({"source": "ValueSet co-inclusion", "value_set": usage.get("url")})
    for profile in bindings:
        for binding in profile.get("bindings", []):
            base = binding.get("base_fhir_comparison") or {}
            for system in (base.get("base_value_set_details") or {}).get("code_systems", []):
                if system.startswith("http://terminology.hl7.org/CodeSystem/"):
                    targets.setdefault(system, []).append({"source": "base FHIR binding", "profile": profile.get("url"),
                        "element": binding.get("path"), "value_set": base.get("base_value_set")})
    return [{"canonical": canonical, "evidence": evidence} for canonical, evidence in sorted(targets.items())]


def build_proposal_jql(resource: dict[str, Any], usages: list[dict[str, Any]],
                       bindings: list[dict[str, Any]] | None = None) -> str:
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
    terms.extend(target["canonical"] for target in discover_context_targets(usages, bindings or []))
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
    cookie = normalize_jira_cookie(cookie)
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
        if exc.code == 401:
            raise AnalysisError(
                "HL7 Jira rejected the browser session (HTTP 401). "
                "The cookie may have expired; please verify and/or update "
                "HL7_JIRA_COOKIE."
            ) from exc
        if exc.code == 400:
            body = exc.read(16384).decode("utf-8", errors="replace")
            try:
                problem = json.loads(body)
            except json.JSONDecodeError:
                problem = None
            if isinstance(problem, dict) and ("errorMessages" in problem or "errors" in problem):
                reason = "Jira reported a query or request-validation error. Check the JQL and project access."
            elif re.search(r"(?:invalid|malformed)[^\n]{0,100}cookie|cookie[^\n]{0,100}(?:invalid|malformed)", body, re.I):
                reason = "The server reported malformed cookie syntax. Verify the copied Cookie header or use only the JSESSIONID value."
            else:
                reason = "The server rejected the request; the response does not establish a cookie or expiration problem."
            raise AnalysisError(
                "HL7 Jira request failed (HTTP 400). " + reason
            ) from exc
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
            rf"(?<![A-Za-z0-9_-]){_discovery_pattern(term)}(?![A-Za-z0-9_-])",
            text,
            flags=re.IGNORECASE,
        )
    ]


def _discovery_pattern(term: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", term):
        return r"[-_\s]*".join(re.escape(c) for c in re.sub(r"[-_]", "", term))
    return re.escape(term)


def normalized_identifier(value: str) -> str:
    return re.sub(r"[-_\s]+", "", value).casefold()


def discover_concept_candidates(source: dict[str, Any], targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matches = []
    for target in targets:
        code = source.get("code") or ""
        target_code = target.get("code") or ""
        score = 1.0
        if code and code == target_code:
            method, rank = "exact-code", 0
        elif code and normalized_identifier(code) == normalized_identifier(target_code):
            method, rank = "normalized-code", 1
        elif code and _mentioned_terms([code], target.get("display") or ""):
            method, rank = "normalized-display-mention", 2
        else:
            display = source.get("display") or ""
            left = normalized_identifier(display)
            right = normalized_identifier(target.get("display") or "")
            if min(len(left), len(right)) < 4:
                continue
            score = difflib.SequenceMatcher(None, left, right, autojunk=False).ratio()
            if score < .85:
                continue
            method, rank = "fuzzy-display", 3
        matches.append({"concept": target, "method": method, "score": round(score, 3), "rank": rank})
    if not matches:
        return []
    best_rank = min(m["rank"] for m in matches)
    return [m for m in matches if m["rank"] == best_rank]


CONTEXT_STOP_WORDS = {
    "a", "an", "and", "code", "codes", "codesystem", "for", "of", "the",
    "to", "type", "types", "value", "values", "valueset", "vs", "cs",
}


def _context_tokens(value: Any, excluded: set[str] | None = None) -> set[str]:
    if not isinstance(value, str):
        return set()
    expanded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    tokens = {
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9]*", expanded)
    }
    return tokens - CONTEXT_STOP_WORDS - (excluded or set())


def _proposal_context_alignment(
    target_canonicals: list[str],
    resource: dict[str, Any],
    concepts: list[dict[str, Any]],
    valueset_usage: list[dict[str, Any]],
    binding_context: list[dict[str, Any]],
) -> dict[str, Any]:
    excluded = {
        str(concept.get("code", "")).lower()
        for concept in concepts
        if concept.get("code")
    }
    excluded.update(
        token
        for concept in concepts
        for token in _context_tokens(concept.get("display"))
    )
    target_tokens = set().union(
        *(
            _context_tokens(canonical.rsplit("/", 1)[-1], excluded)
            for canonical in target_canonicals
        )
    ) if target_canonicals else set()
    sources: list[dict[str, Any]] = []
    for usage in valueset_usage:
        label = " ".join(
            str(usage.get(field) or "") for field in ("name", "title", "description")
        )
        sources.append(
            {
                "source": "local ValueSet",
                "tokens": _context_tokens(label, excluded),
                "weight": 4,
                "details": {
                    "url": usage.get("url"),
                    "name": usage.get("name"),
                    "title": usage.get("title"),
                    "description": usage.get("description"),
                },
            }
        )
    for profile in binding_context:
        for binding in profile.get("bindings", []):
            sources.append(
                {
                    "source": "IG ValueSet binding",
                    "tokens": _context_tokens(binding.get("path"), excluded),
                    "weight": 3,
                    "details": {
                        "profile": profile.get("url") or profile.get("id"),
                        "element": binding.get("path"),
                        "value_set": binding.get("value_set"),
                        "strength": binding.get("strength"),
                    },
                }
            )
            base = binding.get("base_fhir_comparison") or {}
            base_text = " ".join(
                str(base.get(field) or "")
                for field in ("base_element_short", "base_element_definition")
            )
            sources.append(
                {
                    "source": "base FHIR element",
                    "tokens": _context_tokens(base_text, excluded),
                    "weight": 2,
                    "details": {
                        "structure_definition": base.get("base_structure_definition"),
                        "element": base.get("base_element_path"),
                        "binding_status": base.get("status"),
                        "definition": base.get("base_element_definition"),
                    },
                }
            )
    sources.append(
        {
            "source": "candidate CodeSystem",
            "tokens": _context_tokens(
                " ".join(str(resource.get(field) or "") for field in ("name", "title", "description")),
                excluded,
            ),
            "weight": 1,
            "details": {
                "url": resource.get("url"),
                "name": resource.get("name"),
                "title": resource.get("title"),
            },
        }
    )
    evidence: list[dict[str, Any]] = []
    basis: list[dict[str, Any]] = []
    score = 0
    for source in sources:
        matched = sorted(target_tokens & source["tokens"])
        contribution = source["weight"] * len(matched)
        basis.append(
            {
                "source": source["source"],
                "matched_terms": matched,
                "weight": source["weight"],
                "contribution": contribution,
                "details": source["details"],
            }
        )
        if matched:
            score += contribution
            evidence.append(basis[-1])
    if not target_canonicals:
        alignment = "unknown"
    elif score >= 4:
        alignment = "high"
    elif score:
        alignment = "moderate"
    else:
        alignment = "low"
    return {
        "alignment": alignment,
        "score": score,
        "target_terms": sorted(target_tokens),
        "evidence": evidence,
        "basis": basis,
    }


def extract_proposed_concepts(fields: dict[str, Any], codes: list[str] | None) -> list[dict[str, Any]]:
    """Extract only explicit indented code/display/definition blocks.

    Comments and user metadata are deliberately excluded. Multiple different
    rows for a code remain ambiguous rather than selecting one silently.
    """
    if codes is None:
        codes = sorted({line for field in ("description", "customfield_10426")
                        for line in str(fields.get(field) or "").splitlines()
                        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", line)})
        return [row for row in extract_proposed_concepts(fields, codes)
                if row["status"] != "not-extracted"]
    results = []
    for code in codes:
        rows = []
        for field, value in fields.items():
            if field != "description" and field != "customfield_10426":
                continue
            if not isinstance(value, str):
                continue
            lines = value.splitlines()
            for index, line in enumerate(lines[:-2]):
                if line.strip() != code or line != line.lstrip():
                    continue
                display, definition = lines[index + 1:index + 3]
                if not display.startswith("\t") or display.startswith("\t\t"):
                    continue
                if not definition.startswith("\t\t"):
                    continue
                if not display.strip() or not definition.strip():
                    continue
                rows.append({"display": display.strip(), "definition": definition.strip(),
                             "source_field": field, "source_line": index + 1,
                             "source_excerpt": "\n".join(lines[index:index + 3])})
        distinct = {(row["display"], row["definition"]) for row in rows}
        result = {"code": code, "status": "extracted" if len(distinct) == 1 else
                  "ambiguous" if distinct else "not-extracted", "evidence": rows}
        if len(distinct) == 1:
            result.update(display=rows[0]["display"], definition=rows[0]["definition"],
                          proposed_code=code, mapping_status="exact-code")
        results.append(result)
    return results


def map_proposed_concepts(fields: dict[str, Any], codes: list[str], concepts: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    all_rows = extract_proposed_concepts(fields, None)
    results = extract_proposed_concepts(fields, codes)
    for result in results:
        if result["status"] != "not-extracted":
            continue
        # A display mention is a discovery signal, not proof of equivalence.
        source = next((c for c in (concepts or []) if c.get("code") == result["code"]), {"code": result["code"]})
        matches = discover_concept_candidates(source, [r for r in all_rows if r["status"] == "extracted"])
        candidates = [m["concept"] for m in matches]
        result["match_evidence"] = [{"target_code": m["concept"]["code"], "method": m["method"], "score": m["score"]} for m in matches]
        result["alternate_candidates"] = candidates
        if len(candidates) == 1:
            row = candidates[0]
            result.update(status="alternate-code-candidate", proposed_code=row["code"],
                          display=row["display"], definition=row["definition"],
                          evidence=row["evidence"], mapping_status="requires-review")
        elif candidates:
            result.update(status="ambiguous", mapping_status="requires-review")
    return results


def match_proposals(
    resource: dict[str, Any],
    concepts: list[dict[str, Any]],
    proposal_payloads: list[dict[str, Any]],
    valueset_usage: list[dict[str, Any]] | None = None,
    binding_context: list[dict[str, Any]] | None = None,
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
    context_canonicals = {target["canonical"] for target in
                         discover_context_targets(valueset_usage or [], binding_context or [])}
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
            matched_context_canonicals = sorted(context_canonicals.intersection(canonicals))
            if not mentioned_codes and not matched_terms and not matched_local_canonicals and not matched_context_canonicals:
                continue
            if candidate_codes and len(mentioned_codes) == len(candidate_codes):
                coverage = "full"
                code_coverage = "full"
            elif mentioned_codes:
                coverage = "partial"
                code_coverage = "partial"
            elif matched_local_canonicals:
                coverage = "artifact"
                code_coverage = "none"
            else:
                coverage = "contextual"
                code_coverage = "none"
            context = _proposal_context_alignment(
                canonicals,
                resource,
                concepts,
                valueset_usage or [],
                binding_context or [],
            )
            if code_coverage == "full" and context["alignment"] == "high":
                assessment = "strong-existing-proposal-match"
            elif code_coverage in {"full", "partial"} and context["alignment"] == "low":
                assessment = "code-overlap-different-context"
            else:
                assessment = "manual-review"
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
                    "proposed_concepts": map_proposed_concepts(fields, candidate_codes, concepts),
                    "all_proposed_concepts": extract_proposed_concepts(fields, None),
                    "code_mention_coverage": code_coverage,
                    "code_coverage": code_coverage,
                    "context_alignment": context["alignment"],
                    "context_score": context["score"],
                    "context_target_terms": context["target_terms"],
                    "context_evidence": context["evidence"],
                    "context_basis": context["basis"],
                    "assessment": assessment,
                    "matched_codes": mentioned_codes,
                    "matched_terms": matched_terms,
                    "matched_local_canonicals": matched_local_canonicals,
                    "target_canonicals": canonicals,
                    "matched_context_canonicals": matched_context_canonicals,
                }
            )
    alignment_rank = {"high": 0, "moderate": 1, "unknown": 2, "low": 3}
    coverage_rank = {"full": 0, "partial": 1, "none": 2}
    return sorted(
        matches,
        key=lambda item: (
            alignment_rank[item["context_alignment"]],
            coverage_rank[item["code_coverage"]],
            item.get("key") or "",
        ),
    )


def code_style(code: str) -> str:
    if re.fullmatch(r"[0-9]+", code):
        return "numeric"
    for pattern, label in ((r"[a-z][a-z0-9]*(?:-[a-z0-9]+)+", "kebab-case"),
                           (r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+", "snake_case"),
                           (r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+", "UPPER_SNAKE_CASE")):
        if re.fullmatch(pattern, code):
            return label
    if re.fullmatch(r"[A-Z][A-Z0-9]*", code):
        return "UPPERCASE"
    if re.fullmatch(r"[a-z][a-z0-9]*", code):
        return "lowercase-single-token"
    if re.fullmatch(r"[a-z][A-Za-z0-9]*", code) and re.search(r"[A-Z]", code):
        return "camelCase"
    if re.fullmatch(r"[A-Z][A-Za-z0-9]*", code):
        return "PascalCase-or-capitalized-token"
    return "other"


def style_inventory(concepts: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for concept in concepts:
        display = concept.get("display") or ""
        definition = (concept.get("definition") or "").strip()
        letters = [c for c in display if c.isalpha()]
        capitalization = ("missing" if not letters else "uppercase" if all(c.isupper() for c in letters)
                          else "lowercase" if all(c.islower() for c in letters) else
                          "initial-uppercase" if letters[0].isupper() else "initial-lowercase")
        rows.append({"code": concept["code"], "code_style": code_style(concept["code"]),
                     "display_capitalization": capitalization,
                     "definition_ending": "missing" if not definition else
                     "terminal-punctuation" if definition[-1] in ".!?" else "no-terminal-punctuation"})
    summaries = {}
    for field in ("code_style", "display_capitalization", "definition_ending"):
        counts = {}
        for row in rows:
            counts[row[field]] = counts.get(row[field], 0) + 1
        eligible = {k: v for k, v in counts.items() if k != "missing"}
        total = sum(eligible.values())
        predominant = max(eligible, key=eligible.get) if eligible else None
        pattern = ("insufficient-evidence" if total < 3 else "consistent" if len(eligible) == 1
                   else "predominant" if eligible[predominant] / total >= .8 else "mixed")
        summaries[field] = {"counts": counts, "pattern": pattern,
                            "predominant": predominant if pattern in {"consistent", "predominant"} else None,
                            "examples": {k: [r["code"] for r in rows if r[field] == k][:3] for k in counts}}
    return {"scope": "source-CodeSystem", "summaries": summaries, "concepts": rows,
            "note": "Descriptive inventory only. Single-token casing is ambiguous; capitalization is not a grammatical title/sentence-case assessment. Existing identifiers remain unchanged. Spelling checks are not yet implemented."}


def target_style_assessment(code: str, existing: dict[str, Any] | None, inventory: dict[str, Any]) -> dict[str, Any]:
    if existing is not None:
        return {"status": "preserve-existing-code", "identifier": existing["code"],
                "reason": "Reuse preserves the existing target identifier, regardless of source naming style."}
    summary = inventory["summaries"]["code_style"]
    observed = code_style(code)
    if summary["pattern"] == "insufficient-evidence":
        status = "insufficient-evidence"
    elif observed not in summary["counts"]:
        status = "introduces-new-style"
    elif summary["pattern"] == "mixed":
        status = "established-style-review-group"
    elif observed != summary["predominant"]:
        status = "established-minority-style"
    else:
        status = "matches-established-style"
    return {"status": status, "identifier": code, "observed_style": observed,
            "reason": "Potential addition only; review target examples and the applicable concept group. This finding does not establish that a new code is needed."}


def is_tho_canonical(value: str) -> bool:
    return bool(re.fullmatch(r"https?://terminology\.hl7\.org/CodeSystem/[^\s/?#]+", value))


def tho_catalog(package_dir: Path) -> list[dict[str, Any]]:
    indexed = _index_package_resources(package_dir, "CodeSystem")
    catalog = {r["url"]: {k: r.get(k) for k in ("url", "name", "title", "version", "description")}
               for r in indexed.values() if isinstance(r.get("url"), str) and is_tho_canonical(r["url"])}
    return [catalog[url] for url in sorted(catalog)]


def analyze(
    resource: dict[str, Any],
    source: Path,
    proposal_payloads: list[dict[str, Any]] | None = None,
    valueset_usage: list[dict[str, Any]] | None = None,
    binding_context: list[dict[str, Any]] | None = None,
    tho_package_dir: Path | None = None,
    draft_records: dict[str, list[dict[str, Any]]] | None = None,
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
        "style_review": style_inventory(concepts),
    }
    if proposal_payloads:
        result["proposal_matches"] = match_proposals(
            resource, concepts, proposal_payloads, valueset_usage, binding_context
        )
        for proposal in result["proposal_matches"]:
            records = (draft_records or {}).get(proposal["key"], [])
            proposal["draft_artifacts"] = records
            proposal["jira_proposed_concepts"] = copy.deepcopy(proposal["proposed_concepts"])
            valid = [row for row in records if row.get("resource", {}).get("resourceType") == "CodeSystem"
                     and row["resource"].get("url") in proposal["target_canonicals"]]
            for candidate in proposal["proposed_concepts"]:
                pool = []
                for draft in valid:
                    for concept in _flatten_concepts(_as_list(draft["resource"].get("concept"))):
                        pool.append({**concept, "_draft": draft})
                source_concept = next(c for c in concepts if c["code"] == candidate["code"])
                matches = discover_concept_candidates(source_concept, pool)
                options = [(m["concept"]["_draft"], m["concept"]) for m in matches]
                if matches:
                    candidate["match_evidence"] = [{"target_code": m["concept"]["code"], "method": m["method"], "score": m["score"]} for m in matches]
                exact = [option for option in options if option[1]["code"] == candidate["code"]]
                options = exact or options
                if len(options) == 1:
                    draft, concept = options[0]
                    candidate.update(status="extracted" if exact else "alternate-code-candidate",
                        proposed_code=concept["code"], display=concept.get("display"), definition=concept.get("definition"),
                        mapping_status="exact-code" if exact else "requires-review",
                        proposed_source="draft-build", draft_canonical=draft["resource"]["url"],
                        draft_provenance={key: draft.get(key) for key in ("url", "status", "retrieved_at", "sha256")})
                elif len(options) > 1:
                    candidate.update(status="ambiguous", mapping_status="requires-review",
                                     alternate_candidates=[{k: c.get(k) for k in ("code", "display", "definition")} for _, c in options])
        if tho_package_dir is not None:
            result["proposal_matches"] = compare_tho_target_artifacts(
                result["proposal_matches"], concepts, tho_package_dir
            )
    if valueset_usage is not None:
        result["valueset_usage"] = valueset_usage
    if binding_context is not None:
        result["binding_context"] = binding_context
    if tho_package_dir is not None:
        targets = discover_context_targets(valueset_usage or [], binding_context or [])
        result["tho_code_system_catalog"] = tho_catalog(tho_package_dir)
        inspected = compare_tho_target_artifacts(
            [{"target_canonicals": [target["canonical"]]} for target in targets], concepts, tho_package_dir)
        result["context_target_artifacts"] = [
            {**record["tho_target_artifacts"][0], "discovery_evidence": target["evidence"]}
            for target, record in zip(targets, inspected)]
    return result


def _escape_table(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def build_review_template(analysis: dict[str, Any]) -> dict[str, Any]:
    decisions = []
    for proposal in analysis.get("proposal_matches", []):
        for artifact in proposal.get("tho_target_artifacts", []):
            for row in artifact.get("concept_comparison", []):
                if not row.get("proposed_definition"):
                    continue
                decisions.append({
                    "proposal": proposal["key"], "source_code": row["code"],
                    "target_system": artifact["canonical"], "target_code": row["target_code"],
                    "decision": "pending", "note": "",
                    "reviewed_evidence": {key: row.get(key) for key in (
                        "candidate_display", "candidate_definition", "proposed_display", "proposed_definition")},
                })
                if row.get("proposed_source") == "draft-build":
                    decisions[-1]["reviewed_evidence"]["proposed_source"] = "draft-build"
                    decisions[-1]["reviewed_evidence"]["draft_url"] = (row.get("draft_provenance") or {}).get("url")
    for artifact in analysis.get("context_target_artifacts", []):
        if artifact.get("status") != "found":
            continue
        for row in artifact.get("concept_comparison", []):
            decisions.append({"proposal": None, "decision_kind": "target-system-suitability",
                "source_code": row["code"], "target_system": artifact["canonical"],
                "target_code": row["code"], "decision": "pending", "note": "Confirm system suitability only; this does not confirm code equivalence or authorize a new code.",
                "reviewed_evidence": {**{key: row.get(key) for key in
                    ("candidate_display", "candidate_definition", "target_display", "target_definition", "status")},
                    "discovery_evidence": artifact.get("discovery_evidence")}})
    return {"schema_version": "1.0", "source_system": analysis["metadata"].get("url"),
            "decisions": decisions}


def apply_review_decisions(analysis: dict[str, Any], review: dict[str, Any]) -> None:
    if review.get("schema_version") != "1.0" or review.get("source_system") != analysis["metadata"].get("url"):
        raise AnalysisError("Review file schema or source CodeSystem does not match this analysis. Use a separate output directory or the correct --review-file; existing decisions are preserved.")
    if not isinstance(review.get("decisions"), list):
        raise AnalysisError("Review file decisions must be an array.")
    current = build_review_template(analysis)["decisions"]
    results = []
    seen = set()
    confirmed_sources = set()
    for decision in review["decisions"]:
        if not isinstance(decision, dict) or decision.get("decision") not in {"pending", "confirmed", "rejected"}:
            raise AnalysisError("Each review decision must be pending, confirmed, or rejected.")
        identity = tuple(decision.get(key) for key in ("proposal", "source_code", "target_system", "target_code"))
        valid_proposal = isinstance(identity[0], str) and bool(identity[0]) or (
            identity[0] is None and decision.get("decision_kind") == "target-system-suitability")
        if not valid_proposal or not all(isinstance(value, str) and value for value in identity[1:]) or identity in seen:
            raise AnalysisError("Review decisions require unique proposal/source/target identities.")
        seen.add(identity)
        if decision["decision"] == "confirmed":
            if decision["source_code"] in confirmed_sources:
                raise AnalysisError("Only one mapping or system candidate may be confirmed per source code. Choose one candidate in the review page.")
            confirmed_sources.add(decision["source_code"])
        matching = [row for row in current if tuple(row[key] for key in
                    ("proposal", "source_code", "target_system", "target_code")) == identity]
        status = decision["decision"]
        if not matching:
            status = "evidence-unavailable"
        elif (decision.get("reviewed_evidence") != matching[0]["reviewed_evidence"] or
              decision.get("decision_kind") != matching[0].get("decision_kind")):
            status = "requires-re-review"
        if status == "confirmed":
            proposal = next((item for item in analysis.get("proposal_matches", []) if item.get("key") == decision.get("proposal")), {})
            artifact = next((item for item in proposal.get("tho_target_artifacts", []) if item.get("canonical") == decision["target_system"]), {})
            comparison = next((item for item in artifact.get("concept_comparison", []) if item.get("code") == decision["source_code"]), {})
            if (comparison.get("draft_provenance") or {}).get("status") == "cached-live-unavailable":
                status = "requires-re-review"
        results.append({**decision, "effective_status": status})
    analysis["review_decisions"] = results
    requests = review.get("change_requests", [])
    if not isinstance(requests, list):
        raise AnalysisError("change_requests must be an array.")
    seen_requests = set()
    for item in requests:
        if not isinstance(item, dict) or not isinstance(item.get("source_code"), str) or item["source_code"] in seen_requests:
            raise AnalysisError("Change requests require unique source codes.")
        seen_requests.add(item["source_code"])
        for field, values in {"target_kind": {"undecided", "existing", "new"}, "relationship": {"undecided", "equivalent", "needs-modification", "different-concept"}, "action": {"investigate", "reuse", "modify", "add", "create-system"}}.items():
            if item.get(field) not in values:
                raise AnalysisError(f"Invalid change request {field}.")
        for field in ("target_system", "target_code", "display", "definition", "rationale", "system_scope"):
            if not isinstance(item.get(field, ""), str):
                raise AnalysisError(f"Change request {field} must be text.")
    analysis["change_requests"] = copy.deepcopy(requests)
    if not isinstance(review.get("proposal_rationale", ""), str):
        raise AnalysisError("Shared proposal rationale must be text.")
    analysis["proposal_rationale"] = review.get("proposal_rationale", "")


def maintain_review_file(analysis: dict[str, Any], path: Path) -> tuple[bool, int]:
    """Preserve human decisions and their baseline; append only new identities."""
    exists = path.exists()
    original = path.read_text(encoding="utf-8-sig") if exists else None
    try:
        review = json.loads(original) if exists else build_review_template(analysis)
    except json.JSONDecodeError as exc:
        raise AnalysisError("Review file is invalid JSON; it has not been overwritten.") from exc
    if not isinstance(review, dict):
        raise AnalysisError("Review file must contain a JSON object.")
    # Validate before modifying any existing file.
    apply_review_decisions(analysis, review)
    keys = ("proposal", "source_code", "target_system", "target_code")
    identities = {tuple(row[key] for key in keys) for row in review["decisions"]}
    additions = [row for row in build_review_template(analysis)["decisions"]
                 if tuple(row[key] for key in keys) not in identities] if exists else []
    review["decisions"].extend(additions)
    if not exists or additions:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(review, indent=2, ensure_ascii=False) + "\n"
        if not exists:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(content)
        else:
            # Preserve a pre-update copy and avoid partially written decision files.
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=path.name + ".backup-", delete=False) as backup:
                backup.write(original)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=path.name + ".tmp-", delete=False) as handle:
                handle.write(content)
                temporary = Path(handle.name)
            if path.read_text(encoding="utf-8-sig") != original:
                temporary.unlink()
                raise AnalysisError("Review file changed during analysis; rerun to preserve your edits.")
            os.replace(temporary, path)
    apply_review_decisions(analysis, review)
    return not exists, len(additions)


def build_recommendations(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """Recommend next review actions without inferring publication or equivalence."""
    results = []
    decisions = analysis.get("review_decisions", [])
    for concept in analysis.get("concepts", []):
        code = concept.get("code")
        reviews = [row for row in decisions if row.get("source_code") == code]
        confirmed = [row for row in reviews if row.get("effective_status") == "confirmed"]
        targets = {(row["target_system"], row["target_code"]) for row in confirmed}
        if len(targets) > 1:
            action = "resolve-conflicting-decisions"
            reason = "More than one target mapping is confirmed; choose the applicable target before proceeding."
        elif confirmed:
            if any(row.get("proposal") for row in confirmed):
                action = "coordinate-with-existing-proposal"
                reason = "A current human review confirms this mapping. Coordinate with the associated proposal before preparing a duplicate."
            else:
                action = "review-concepts-in-selected-system"
                reason = "Target system suitability is confirmed. Review existing concepts and related proposals before deciding on reuse or additions. No code mapping is confirmed by this decision."
        elif any(row.get("effective_status") in {"requires-re-review", "evidence-unavailable"} for row in reviews):
            action = "re-review-evidence"
            reason = "Saved decisions have changed or unavailable evidence and cannot support a current recommendation."
        else:
            action = "review-candidates"
            reason = "No current confirmed mapping is available. Review candidates or broaden terminology discovery; absence of a match does not establish a need for a new code."
        refs = []
        for decision in confirmed:
            if decision.get("decision_kind") == "target-system-suitability":
                continue
            proposal = next((row for row in analysis.get("proposal_matches", [])
                             if row.get("key") == decision["proposal"]), {})
            artifact = next((row for row in proposal.get("tho_target_artifacts", [])
                             if row.get("canonical") == decision["target_system"]), {})
            comparison = next((row for row in artifact.get("concept_comparison", [])
                               if row.get("code") == code and row.get("target_code") == decision["target_code"]), {})
            refs.append({"proposal": decision["proposal"], "proposal_status": proposal.get("status"),
                         "proposal_resolution": proposal.get("resolution"),
                         "target_system": decision["target_system"], "target_code": decision["target_code"],
                         "installed_code_status": comparison.get("status"),
                         "proposed_change": comparison.get("inferred_change")})
        warnings = []
        for decision in confirmed:
            proposal = next((p for p in analysis.get("proposal_matches", []) if p.get("key") == decision.get("proposal")), {})
            if proposal.get("context_alignment") == "low":
                warnings.append(f"{decision['proposal']}: confirmed mapping has low context alignment. Review the binding and target purpose and record your rationale; the saved decision remains confirmed.")
        results.append({"source_code": code, "action": action, "reason": reason,
                        "context_warnings": warnings,
                        "confirmed_evidence": refs,
                        "publication_readiness": "not-established",
                        "publication_check": "Verify the required target display and definition in the intended published THO version before updating the IG. Jira status and confirmed mappings alone do not establish publication readiness."})
    return results


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

    if "recommendations" in analysis:
        lines.extend(["", "## Recommended next actions", "",
                      "| Candidate code | Action | Reason |", "|---|---|---|"])
        for recommendation in analysis["recommendations"]:
            lines.append("| " + " | ".join(_escape_table(recommendation[key]) for key in
                         ("source_code", "action", "reason")) + " |")
        for recommendation in analysis["recommendations"]:
            for warning in recommendation.get("context_warnings", []):
                lines.extend(["", f"- Context review for `{recommendation['source_code']}`: {_escape_table(warning)}"])
            for evidence in recommendation["confirmed_evidence"]:
                lines.extend(["", f"- `{recommendation['source_code']}` → `{evidence['target_system']}#{evidence['target_code']}`: "
                              f"{evidence['proposal']} ({evidence.get('proposal_status')}); "
                              f"installed target: {evidence.get('installed_code_status')}; "
                              f"proposed change: {evidence.get('proposed_change')}."])
        lines.extend(["", "Publication readiness is not established. Verify the required target display and definition in the intended published THO version before updating the IG. Jira status and mapping confirmation alone are insufficient."])

    if "style_review" in analysis:
        style = analysis["style_review"]
        lines.extend(["", "## Source style inventory", "", style["note"], "",
                      "Patterns require at least three observations; predominant means at least 80% of observations.", "",
                      "| Component | Pattern | Counts |", "|---|---|---|"])
        for field, summary in style["summaries"].items():
            lines.append("| " + " | ".join(_escape_table(value) for value in (field, summary["pattern"], json.dumps(summary["counts"]))) + " |")
        lines.extend(["", "| Code | Identifier pattern | Display capitalization | Definition ending |", "|---|---|---|---|"])
        for row in style["concepts"]:
            lines.append("| " + " | ".join(_escape_table(row[k]) for k in ("code", "code_style", "display_capitalization", "definition_ending")) + " |")

    targets = list(analysis.get("context_target_artifacts", [])) + [a for p in analysis.get("proposal_matches", []) for a in p.get("tho_target_artifacts", [])]
    styled = {a["canonical"]: a for a in targets if a.get("style_review")}
    if styled:
        lines.extend(["", "## Installed target style review", "", "Descriptive style checks use the installed target package, including all nested concepts. Existing target identifiers remain unchanged. Potential additions require human review; no identifiers or text are rewritten."])
        for canonical, artifact in styled.items():
            lines.extend(["", f"### {canonical}", "", f"Artifact version: {artifact.get('version')}; source: {artifact.get('source')}", "", "| Component | Pattern | Counts and examples |", "|---|---|---|"])
            for field, summary in artifact["style_review"]["summaries"].items():
                lines.append("| " + " | ".join(_escape_table(x) for x in (field, summary["pattern"], json.dumps({"counts": summary["counts"], "examples": summary["examples"]}))) + " |")
            for row in artifact.get("concept_comparison", []):
                assessment = row.get("style_assessment", {})
                lines.append(f"- `{row['code']}` → `{row['target_code']}`: {assessment.get('status')}. {assessment.get('reason', '')}")

    if "context_target_artifacts" in analysis:
        lines.extend(["", "## THO targets discovered from IG context", "",
                      "These systems were inspected independently of Jira. Confirming system suitability does not confirm a code mapping or justify adding a code."])
        for artifact in analysis["context_target_artifacts"]:
            lines.extend(["", f"### {artifact['canonical']}", "",
                          f"Package lookup: {artifact['status']}; artifact version: {artifact.get('version') or 'unknown'}."])
            for evidence in artifact["discovery_evidence"]:
                lines.append("- " + _escape_table("; ".join(f"{key}: {value}" for key, value in evidence.items())))
            lines.extend(["", "| Candidate code | Exact-code lookup | Installed display | Installed definition |",
                          "|---|---|---|---|"])
            for row in artifact.get("concept_comparison", []):
                lines.append("| " + " | ".join(_escape_table(row.get(key)) for key in
                    ("code", "status", "target_display", "target_definition")) + " |")
            lines.extend(["", "An absent exact code does not rule out an equivalent concept under another code."])

    if "review_decisions" in analysis:
        lines.extend(["", "## Saved review decisions", "",
                      "Human decisions are separate from automated findings. Confirmed mappings do not imply THO publication.", "",
                      "Target-system-suitability decisions select a system for investigation only. Their target_code is the candidate identifier used for exact lookup, not an approved mapping.", "",
                      "| Decision kind | Proposal | Source code | Target system | Target code | Saved decision | Effective status | Note |",
                      "|---|---|---|---|---|---|---|---|"])
        for decision in analysis["review_decisions"]:
            lines.append("| " + _escape_table(decision.get("decision_kind", "proposal-mapping")) + " | " + " | ".join(_escape_table(decision.get(key)) for key in
                         ("proposal", "source_code", "target_system", "target_code", "decision", "effective_status", "note")) + " |")

    if any(proposal.get("draft_artifacts") for proposal in analysis.get("proposal_matches", [])):
        lines.extend(["", "## Draft build evidence", "",
                      "Draft content is development evidence. Cached snapshots may be older than the current proposal; verify before confirming decisions."])
        for proposal in analysis["proposal_matches"]:
            for draft in proposal.get("draft_artifacts", []):
                lines.extend(["", f"- {proposal['key']}: {draft['action']} `{draft['source_path']}`; status: {draft['status']}; "
                              f"URL: {draft.get('url', 'not fetched')}; retrieved: {draft.get('retrieved_at', 'unavailable')}; "
                              f"SHA-256: {draft.get('sha256', 'unavailable')}."])
            for candidate in proposal.get("proposed_concepts", []):
                if candidate.get("proposed_source") == "draft-build":
                    jira = next((row for row in proposal["jira_proposed_concepts"] if row["code"] == candidate["code"]), {})
                    lines.extend(["", f"- `{candidate['code']}` → `{candidate['proposed_code']}`: proposed values taken from draft JSON.",
                                  f"  - Jira display: {_escape_table(jira.get('display'))}",
                                  f"  - Jira definition: {_escape_table(jira.get('definition'))}",
                                  f"  - Draft display: {_escape_table(candidate.get('display'))}",
                                  f"  - Draft definition: {_escape_table(candidate.get('definition'))}"])

    if "proposal_matches" in analysis:
        lines.extend(["", "## Related THO proposals", ""])
        proposals = analysis["proposal_matches"]
        if proposals:
            lines.extend(
                [
                    "| Proposal | Status | Code-mention coverage | Context | Assessment | Context evidence | Target artifacts |",
                    "|---|---|---|---|---|---|---|",
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
                            _escape_table(proposal.get("code_coverage")),
                            _escape_table(proposal.get("context_alignment")),
                            _escape_table(proposal.get("assessment")),
                            _escape_table("; ".join(
                                f"{item['source']}: {', '.join(item['matched_terms'])}"
                                for item in proposal.get("context_evidence", [])
                            )),
                            _escape_table(", ".join(proposal.get("target_canonicals", []))),
                        ]
                    )
                    + " |"
                )
            lines.extend([
                "",
                "### Context-alignment evidence",
                "",
                "The score compares meaningful terms from each proposal's target "
                "artifact canonicals with the sources below. Candidate codes and "
                "generic terminology words such as `code`, `ValueSet`, and `type` "
                "are excluded. Binding strength is reported as evidence but is not "
                "currently assigned additional weight.",
            ])
            for proposal in proposals:
                lines.extend([
                    "",
                    f"#### {proposal.get('key')}",
                    "",
                    f"- Target context terms: {_escape_table(', '.join(proposal.get('context_target_terms', [])) or '(none)')}",
                    f"- Alignment: {_escape_table(proposal.get('context_alignment'))}",
                    f"- Score: {_escape_table(proposal.get('context_score'))}",
                    f"- Assessment: {_escape_table(proposal.get('assessment'))}",
                    "- Evidence sources:",
                ])
                for item in proposal.get("context_basis", []):
                    details = item.get("details") or {}
                    detail_text = "; ".join(
                        f"{key}={value}"
                        for key, value in details.items()
                        if value not in (None, "", [])
                    )
                    matched = ", ".join(item.get("matched_terms", [])) or "none"
                    lines.append(
                        "  - "
                        f"{item.get('source')}: matched={matched}; "
                        f"weight={item.get('weight')}; "
                        f"contribution={item.get('contribution')}; "
                        f"{_escape_table(detail_text)}"
                    )
        else:
            lines.append("No related proposal was found in the supplied Jira data.")

        target_comparisons = [
            proposal for proposal in proposals if "tho_target_artifacts" in proposal
        ]
        if target_comparisons:
            lines.extend(["", "## Installed THO target-artifact comparison", ""])
            lines.append(
                "This section describes the installed THO package. An open proposal "
                "may contain changes that are not yet present in that package."
            )
            lines.extend(["", "Changes below are inferred relative to the installed package, not explicit Jira actions. "
                          "Extracted rows require human confirmation of target and proposal intent; textual differences do not establish semantic differences.", ""])
            for proposal in target_comparisons:
                lines.extend(["", f"### {proposal.get('key')}", ""])
                for proposed in proposal.get("proposed_concepts", []):
                    lines.append(f"- Candidate `{proposed['code']}` → proposed "
                                 f"`{proposed.get('proposed_code', 'unresolved')}`: "
                                 f"{proposed.get('mapping_status', proposed['status'])}.")
                    for evidence in proposed.get("evidence", []):
                        lines.append(f"- Extraction evidence for `{proposed['code']}`: "
                                     f"{evidence['source_field']}, line {evidence['source_line']}.")
                    for evidence in proposed.get("match_evidence", []):
                        lines.append(f"- Discovery for `{proposed['code']}` → `{evidence['target_code']}`: {evidence['method']}; score={evidence['score']} (similarity, not semantic confidence).")
                for artifact in proposal.get("tho_target_artifacts", []):
                    lines.append(
                        f"- `{_escape_table(artifact.get('canonical'))}` "
                        f"({artifact.get('resource_type')}): {artifact.get('status')}"
                    )
                    if artifact.get("resource_type") == "CodeSystem" and artifact.get("status") == "found":
                        for concept in artifact.get("concept_comparison", []):
                            lines.extend(["", f"#### Concept `{concept.get('code')}`", "",
                                f"Installed THO comparison code: `{concept.get('target_code', concept.get('code'))}`; mapping: {concept.get('mapping_status')}.", "",
                                "| Source | Display | Definition |", "|---|---|---|",
                                "| IG | " + _escape_table(concept.get("candidate_display")) + " | " + _escape_table(concept.get("candidate_definition")) + " |",
                                "| Installed THO | " + _escape_table(concept.get("target_display")) + " | " + _escape_table(concept.get("target_definition")) + " |",
                                ("| Draft build | " if concept.get("proposed_source") == "draft-build" else "| Jira proposal | ") + _escape_table(concept.get("proposed_display")) + " | " + _escape_table(concept.get("proposed_definition")) + " |", "",
                                f"Extraction: {concept.get('proposal_status')}; inferred change: {concept.get('inferred_change')}.", ""])
                            lines.append(
                                "  - "
                                f"Candidate `{_escape_table(concept.get('code'))}` → target `{_escape_table(concept.get('target_code'))}`: "
                                f"{concept.get('status')}; "
                                f"display={concept.get('display_comparison')}; "
                                f"definition={concept.get('definition_comparison')}"
                            )
                            if concept.get("status") == "existing-code":
                                lines.extend([
                                    f"    - Candidate display: {_escape_table(concept.get('candidate_display'))}",
                                    f"    - THO display: {_escape_table(concept.get('target_display'))}",
                                    f"    - Candidate definition: {_escape_table(concept.get('candidate_definition'))}",
                                    f"    - THO definition: {_escape_table(concept.get('target_definition'))}",
                                ])
                    elif artifact.get("resource_type") == "ValueSet" and artifact.get("status") == "found":
                        systems = ", ".join(artifact.get("included_code_systems", [])) or "none identified"
                        lines.append(f"  - Included CodeSystems: {_escape_table(systems)}")

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

    if "binding_context" in analysis:
        lines.extend(["", "## StructureDefinition binding context", ""])
        profiles = analysis["binding_context"]
        if profiles:
            lines.extend([
                "| Profile | Type | Element | Strength | ValueSet | Source section | Base definition |",
                "|---|---|---|---|---|---|---|",
            ])
            for profile in profiles:
                label = profile.get("title") or profile.get("name") or profile.get("id")
                for binding in profile["bindings"]:
                    base = binding.get("base_fhir_comparison") or {}
                    base_details = base.get("base_value_set_details") or {}
                    lines.append("| " + " | ".join([
                        _escape_table(label),
                        _escape_table(profile.get("type")),
                        _escape_table(binding.get("path")),
                        _escape_table(binding.get("strength")),
                        _escape_table(binding.get("value_set")),
                        _escape_table(", ".join(binding.get("sections", []))),
                        _escape_table(profile.get("base_definition")),
                    ]) + " |")
                    if base:
                        lines.extend([
                            "",
                            f"Base FHIR comparison for `{_escape_table(binding.get('path'))}`:",
                            "",
                            f"- Status: {_escape_table(base.get('status'))}",
                            f"- Base element description: {_escape_table(base.get('base_element_definition'))}",
                            f"- Base element type: {_escape_table(', '.join(base.get('base_element_types', [])))}",
                            f"- Base binding strength: {_escape_table(base.get('base_binding_strength'))}",
                            f"- Base ValueSet: {_escape_table(base.get('base_value_set'))}",
                            f"- Base ValueSet CodeSystems: {_escape_table(', '.join(base_details.get('code_systems', [])))}",
                        ])
        else:
            lines.append("No generated StructureDefinition binds to the discovered local ValueSets.")

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


def normalize_jira_cookie(cookie: str | None) -> str | None:
    """Accept either a bare JSESSIONID or a complete browser Cookie value."""
    if cookie is None:
        return None
    # Reject hidden/control characters before stripping or constructing headers.
    if any(ord(char) < 32 or ord(char) > 126 for char in cookie):
        raise AnalysisError("The Jira cookie contains control or non-ASCII characters. Copy a single plain-text cookie value.")
    cookie = cookie.strip()
    if len(cookie) >= 2 and cookie[0] == cookie[-1] and cookie[0] in "\"'":
        cookie = cookie[1:-1].strip()
    if cookie.lower().startswith("cookie:"):
        cookie = cookie.split(":", 1)[1].strip()
    if not cookie:
        return None
    if "=" not in cookie:
        cookie = f"JSESSIONID={cookie}"
    parts = []
    for part in cookie.split(";"):
        if not part.strip():
            continue
        name, separator, value = part.partition("=")
        name, value = name.strip(), value.strip()
        if not separator or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name):
            raise AnalysisError("Invalid Cookie header syntax. Enter a bare JSESSIONID value or name=value cookie pairs.")
        if name == "JSESSION":
            raise AnalysisError("The Jira session cookie name is JSESSIONID, not JSESSION.")
        if name == "JSESSIONID" and not value:
            raise AnalysisError("The JSESSIONID value is empty.")
        if not re.fullmatch(r'[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]*', value):
            raise AnalysisError("Invalid cookie value characters. Remove pasted quotes or embedded whitespace.")
        parts.append(f"{name}={value}")
    return "; ".join(parts)


def get_jira_credentials() -> tuple[str | None, str | None]:
    cookie = normalize_jira_cookie(os.environ.get("HL7_JIRA_COOKIE"))
    token = os.environ.get("HL7_JIRA_PAT")
    if not cookie and not token:
        if not sys.stdin.isatty():
            raise AnalysisError(
                "HL7_JIRA_COOKIE or HL7_JIRA_PAT is not set and a secure "
                "prompt is unavailable"
            )
        cookie = normalize_jira_cookie(
            getpass.getpass("HL7 Jira JSESSIONID value (or complete Cookie header): ")
        )
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
    binding_context = (
        find_structuredefinition_bindings(valueset_usage or [], args.ig_dir.resolve())
        if args.ig_dir else None
    )
    if args.fhir_package_dir and binding_context is not None:
        binding_context = compare_base_fhir_bindings(
            binding_context, args.fhir_package_dir.expanduser().resolve()
        )
    if args.search_proposals:
        jql = build_proposal_jql(resource, valueset_usage or [], binding_context or [])
        proposal_payloads.append(
            search_jira_proposals(
                args.jira_url, jql, token=token, cookie=cookie
            )
        )
    draft_records = {}
    if args.fetch_drafts or args.draft_file:
        relevant_keys = {item["key"] for item in match_proposals(resource,
            _flatten_concepts(_as_list(resource.get("concept"))), proposal_payloads, valueset_usage, binding_context)}
        local_files: dict[str, list[Path]] = {}
        for value in args.draft_file:
            key, separator, path = value.partition("=")
            if not separator or not re.fullmatch(r"UP-\d+", key):
                raise AnalysisError("--draft-file requires UP-number=path/to/Resource.json")
            local_files.setdefault(key, []).append(Path(path).expanduser().resolve())
        for payload in proposal_payloads:
            for issue in _jira_issues(payload):
                if (args.fetch_drafts and issue.get("key") in relevant_keys) or issue.get("key") in local_files:
                    draft_records[issue["key"]] = draft_builds.retrieve_drafts(
                        issue, output_dir / "draft-snapshots", local_files.get(issue["key"], []), allow_fetch=args.fetch_drafts)
    tho_package_dir: Path | None = None
    if args.tho_package_dir:
        tho_package_dir, tho_package_version = resolve_latest_package_dir(
            args.tho_package_dir
        )
        if tho_package_dir != args.tho_package_dir.expanduser().resolve():
            print(
                "Resolved THO package "
                f"{args.tho_package_dir} to {tho_package_dir} "
                f"(version {tho_package_version})."
            )
    result = analyze(
        resource,
        source,
        proposal_payloads,
        valueset_usage,
        binding_context,
        tho_package_dir,
        draft_records,
    )
    if args.search_proposals:
        result["proposal_search"] = {"jql": jql, "limit": 50, "total": proposal_payloads[-1].get("total"),
                                     "returned": len(proposal_payloads[-1].get("issues", [])), "pagination": "not-implemented"}
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.review_file and args.write_review_template:
        raise AnalysisError("Use --review-file alone; --write-review-template is deprecated.")
    review_path = (args.review_file or args.write_review_template or
                   (output_dir / "review-decisions.json")).expanduser().resolve()
    if review_path in {output_dir / "analysis.json", output_dir / "concept-inventory.md", output_dir / "proposal-draft.md", output_dir / "proposal-submission.md", output_dir / "proposal-changes.json", output_dir / "review.html", source}:
        raise AnalysisError("The review file must be separate from the input and generated reports.")
    created, added = maintain_review_file(result, review_path)
    result["recommendations"] = build_recommendations(result)
    (output_dir / "analysis.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output_dir / "concept-inventory.md").write_text(
        render_markdown(result), encoding="utf-8"
    )
    write_proposal_outputs(result, output_dir)
    print(f"Analyzed {result['concept_count']} concepts from {source}")
    print(f"Wrote {output_dir / 'analysis.json'}")
    print(f"Wrote {output_dir / 'concept-inventory.md'}")
    print(f"Wrote {output_dir / 'proposal-draft.md'} (generated working draft; keep manual edits in a separate copy)")
    print(f"Review file {'created' if created else 'reused'}: {review_path}")
    if added:
        print(f"Added {added} pending mappings; existing decisions and reviewed evidence preserved.")
    counts: dict[str, int] = {}
    for decision in result["review_decisions"]:
        status = decision["effective_status"]
        counts[status] = counts.get(status, 0) + 1
    print("Review status: " + (", ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "no extracted mappings available"))
    for instruction in review_next_steps(counts):
        print(instruction)
    print("Keep the review file between runs. Use a separate output directory for each CodeSystem.")
    if args.write_review_template:
        print("--write-review-template is deprecated; use --review-file for this custom path on subsequent runs.")
    command_review(argparse.Namespace(output_dir=output_dir, review_file=review_path))
    return 0


def review_next_steps(counts: dict[str, int]) -> list[str]:
    instructions = []
    if counts.get("pending"):
        instructions.append("Next: Review concept-inventory.md. Set applicable pending decisions to confirmed or rejected; optionally add a note.")
    if counts.get("requires-re-review"):
        instructions.append("Next: Compare changed evidence in concept-inventory.md with the saved baseline before updating reviewed_evidence and reconfirming or rejecting.")
    if instructions:
        instructions.append("Preserve mapping identities. Leave reviewed_evidence unchanged for pending decisions. Save the review file and rerun the same command.")
    elif counts:
        instructions.append("Review complete: no pending decisions or changed evidence require review. Next: Follow the recommended actions in concept-inventory.md; verify publication readiness before updating the IG.")
    else:
        instructions.append("Next: Review concept-inventory.md for context and discovery findings. No mapping decisions are available; broaden terminology discovery as needed.")
    return instructions


def render_proposal_draft(analysis: dict[str, Any], prepared: list[dict[str, Any]] | None = None) -> str:
    recommendations = build_recommendations(analysis)
    lines = ["# THO proposal preparation draft", "",
             "Working draft for human review. No Jira submission or THO approval is implied.", "",
             f"Source CodeSystem: {analysis['metadata'].get('url')}",
             f"Source version: {analysis['metadata'].get('version') or 'unspecified'}", "",
             "## Reviewed scope", "",
             "Confirmed proposal mappings support coordination with existing tickets. System suitability selects a target for investigation only; it does not approve additions or equivalence."]
    for recommendation in recommendations:
        code = recommendation["source_code"]
        lines.extend(["", f"## Candidate `{code}`", "", f"Next action: {recommendation['action']}", recommendation["reason"]])
        source = next(c for c in analysis["concepts"] if c["code"] == code)
        lines.extend(["", "### Source wording", "", f"Display: {_escape_table(source.get('display'))}", f"Definition: {_escape_table(source.get('definition'))}"])
        decisions = [d for d in analysis.get("review_decisions", []) if d.get("source_code") == code]
        if recommendation["action"] in {"resolve-conflicting-decisions", "re-review-evidence", "review-candidates"}:
            lines.extend(["", "**Not ready for proposal wording.** Resolve the review action above before selecting a target or change."])
        else:
            for decision in decisions:
                if decision.get("effective_status") != "confirmed":
                    continue
                lines.extend(["", f"### Reviewed target: {decision['target_system']}", "", f"Reviewer note: {_escape_table(decision.get('note')) or '[Add rationale]'}"])
                if decision.get("decision_kind") == "target-system-suitability":
                    lines.extend(["", "Candidate change: **unresolved**. Check existing concepts and related proposals before choosing reuse, modification, or addition.",
                                  "Proposed target identifier: **unresolved** (the source identifier above is not an approved target code).",
                                  "Proposed display and definition: **unresolved**. Use the source wording as input and broaden it as appropriate for the target system."])
                    continue
                proposal = next((p for p in analysis.get("proposal_matches", []) if p.get("key") == decision.get("proposal")), {})
                artifact = next((a for a in proposal.get("tho_target_artifacts", []) if a.get("canonical") == decision["target_system"]), {})
                row = next((r for r in artifact.get("concept_comparison", []) if r.get("code") == code and r.get("target_code") == decision["target_code"]), {})
                evidence = decision.get("reviewed_evidence") or {}
                lines.extend(["", f"Related ticket: {decision['proposal']} — {proposal.get('status') or 'status unavailable'}",
                              "Preparation mode: coordinate with this ticket; do not prepare a duplicate submission without reviewing its scope.",
                              f"Reviewed target code: `{decision['target_code']}`",
                              f"Installed target version: {artifact.get('version') or 'unavailable'}",
                              f"Change inferred relative to installed package: {row.get('inferred_change') or 'unresolved'}", "",
                              "### Proposed wording from reviewed evidence", "",
                              f"Display: {_escape_table(evidence.get('proposed_display')) or '[Not available]'}",
                              f"Definition: {_escape_table(evidence.get('proposed_definition')) or '[Not available]'}",
                              f"Evidence source: {evidence.get('proposed_source') or 'Jira extraction'}"])
                if evidence.get("draft_url"):
                    lines.append(f"Draft URL: {evidence['draft_url']}")
                for warning in recommendation.get("context_warnings", []):
                    lines.append(f"Context review: {_escape_table(warning)}")
        lines.extend(["", "### Decisions included", ""])
        lines.extend(f"- {d.get('proposal') or 'Target suitability'}: {d.get('effective_status')}; target `{d['target_system']}#{d['target_code']}`." for d in decisions)
    lines.extend(["", "## Explicit requested changes", "", "These are reviewer-authored intentions, separate from candidate mapping decisions. Alternative targets have not automatically been searched or validated."])
    grouped = {}
    for item in analysis.get("change_requests", []):
        source = next((c for c in analysis["concepts"] if c["code"] == item["source_code"]), None)
        issues = []
        if item.get("confirmed_mapping"):
            mapping = item["confirmed_mapping"]
            matches = [d for d in analysis.get("review_decisions", []) if d.get("source_code") == item["source_code"] and d.get("effective_status") == "confirmed" and d.get("decision_kind") != "target-system-suitability" and all(d.get(k) == mapping.get(k) for k in ("proposal", "target_system", "target_code"))]
            if not matches:
                issues.append("Automatic reuse request no longer has a current confirmed mapping; review it again.")
        if item.get("confirmed_suitability") and not any(d.get("source_code") == item["source_code"] and d.get("decision_kind") == "target-system-suitability" and d.get("effective_status") == "confirmed" and d.get("target_system") == item["confirmed_suitability"].get("target_system") for d in analysis.get("review_decisions", [])):
            issues.append("Addition request no longer has current confirmed system suitability; review it again.")
        if source is None or item.get("source_evidence") != {k: source.get(k) for k in ("code", "display", "definition")}:
            issues.append("Source evidence changed or is unavailable; review this request again.")
        if item["target_kind"] == "undecided" or item["action"] == "investigate":
            issues.append("Target or action remains undecided.")
        if not item.get("target_system"):
            issues.append("Provide the target canonical.")
        if item.get("target_system") and not is_tho_canonical(item["target_system"]):
            issues.append("Target must be a terminology.hl7.org CodeSystem canonical URL.")
        if item["target_kind"] == "existing" and item.get("target_system") not in {s["url"] for s in analysis.get("tho_code_system_catalog", [])}:
            issues.append("Existing CodeSystem was not verified in the supplied THO package; refresh lookup evidence.")
        if item["action"] in {"add", "modify", "create-system"} and not all(item.get(k) for k in ("target_code", "display", "definition")):
            issues.append("Provide target code, display, and definition.")
        if item["action"] == "reuse" and (item["relationship"] != "equivalent" or not item.get("target_code")):
            issues.append("Reuse requires an equivalent relationship and a target code.")
        if item["action"] == "modify" and item["relationship"] != "needs-modification":
            issues.append("Modification requires the needs-modification relationship.")
        if item["action"] == "add" and item["relationship"] != "different-concept":
            issues.append("Addition requires the different-concept relationship after reviewing existing concepts.")
        if item["target_kind"] == "new" and (item["action"] != "create-system" or not item.get("system_scope")):
            issues.append("A new system requires create-system and a scope statement.")
        if item["action"] == "create-system" and item["target_kind"] != "new":
            issues.append("Create-system requires a new target system.")
        lines.extend(["", f"### Request for `{item['source_code']}`", "", "Status: " + ("needs review" if issues else "working proposal for steward review")])
        for field in ("target_kind", "target_system", "relationship", "action", "target_code", "display", "definition", "rationale", "system_scope"):
            lines.append(f"- {field}: {_escape_table(item.get(field)) or '[Unspecified]'}")
        lines.extend(f"- Unresolved: {issue}" for issue in issues)
        if not item.get("rationale") and not analysis.get("proposal_rationale"):
            lines.append("- Rationale not provided (optional).")
        if not issues:
            grouped.setdefault((item["target_system"], item["action"] == "reuse"), []).append(item)
        if prepared is not None:
            prepared.append({"request": copy.deepcopy(item), "issues": issues})
    lines.extend(["", "## Proposal wording grouped by target", "", "Reviewer-authored working content. Reuse mappings are listed separately from requested terminology changes. Entries with unresolved issues above are excluded."])
    if analysis.get("proposal_rationale"):
        lines.extend(["", "Shared rationale: " + _escape_table(analysis["proposal_rationale"])])
    for (canonical, reuse), items in grouped.items():
        lines.extend(["", f"### {canonical}", "", "Reuse mappings — no terminology change requested." if reuse else "Requested terminology changes — review with the CodeSystem steward.", "",
                      "| Action | Code | Display | Definition | Source code |", "|---|---|---|---|---|"])
        for item in items:
            lines.append("| " + " | ".join(_escape_table(item.get(k)) for k in ("action", "target_code", "display", "definition", "source_code")) + " |")
        for rationale in dict.fromkeys(item.get("rationale", "") for item in items):
            if rationale:
                lines.append("- Rationale: " + _escape_table(rationale))
        tickets = sorted({d["proposal"] for d in analysis.get("review_decisions", []) if d.get("proposal") and d.get("effective_status") == "confirmed" and d.get("target_system") == canonical and d.get("source_code") in {item["source_code"] for item in items}})
        if tickets:
            lines.append("- Coordinate with existing proposal(s): " + ", ".join(tickets) + ".")
    lines.extend(["", "## IG usage evidence", ""])
    for profile in analysis.get("binding_context", []):
        for binding in profile.get("bindings", []):
            lines.append(f"- Profile: {profile.get('url') or profile.get('id')}; element: {binding.get('path')}; ValueSet: {binding.get('value_set')}; strength: {binding.get('strength')}.")
    lines.extend(["", "## Complete before submission or IG changes", "",
                  "- Confirm the requested change with the target CodeSystem steward; resolve required wording and target questions. Rationale is optional context.",
                  "- Verify existing terminology and related proposals; absent exact codes do not establish a need for additions.",
                  "- Review display, definition, spelling, identifier style, and ValueSet scope. Automated quality review is incomplete.",
                  "- Verify published target content in the intended THO release before changing the IG.", ""])
    return "\n".join(lines)


def write_proposal_outputs(analysis: dict[str, Any], directory: Path) -> None:
    prepared = []
    dossier = render_proposal_draft(analysis, prepared)
    groups = {}
    for row in prepared:
        item = row["request"]
        if not row["issues"] and item["action"] != "reuse":
            groups.setdefault(item["target_system"], []).append(item)
    submission = ["# Proposed THO terminology changes", "", "Prepared for human submission review. No ticket has been created."]
    if analysis.get("proposal_rationale"):
        submission.extend(["", "## Rationale", "", _escape_table(analysis["proposal_rationale"])])
    if not groups:
        submission.extend(["", "No complete terminology change requests are available. Reuse mappings do not request THO changes. See proposal-draft.md for review findings."])
    for canonical, items in groups.items():
        submission.extend(["", f"## {canonical}", "", "Requested changes:", "", "| Action | Code | Display | Definition |", "|---|---|---|---|"])
        for item in items:
            submission.append("| " + " | ".join(_escape_table(item.get(k)) for k in ("action", "target_code", "display", "definition")) + " |")
        for scope in dict.fromkeys(item.get("system_scope", "") for item in items):
            if scope:
                submission.append("\nNew CodeSystem scope: " + _escape_table(scope))
        for rationale in dict.fromkeys(item.get("rationale", "") for item in items):
            if rationale:
                submission.append("\nRationale: " + _escape_table(rationale))
        related = [p for p in analysis.get("proposal_matches", []) if canonical in p.get("target_canonicals", [])]
        if related:
            submission.extend(["", "Related proposals to check before creating a ticket:"])
            submission.extend(f"- {p['key']} ({p.get('status', 'unknown')}): https://jira.hl7.org/browse/{p['key']}" for p in related)
    submission.extend(["", "## Source and usage", "", f"Source: {analysis['metadata'].get('url')}"])
    for profile in analysis.get("binding_context", []):
        for binding in profile.get("bindings", []):
            submission.append(f"- {profile.get('url')}: {binding.get('path')}; ValueSet {binding.get('value_set')}; {binding.get('strength')} binding.")
    excluded = [r["request"]["source_code"] for r in prepared if r["issues"]]
    if excluded:
        submission.extend(["", "Incomplete requests excluded: " + ", ".join(excluded) + ". See proposal-draft.md."])
    submission.extend(["", "ValueSet changes have not been inferred. Confirm ValueSet scope, target wording, and existing-code coverage before submission. These files do not modify THO source artifacts.", ""])
    manifest = {"schema_version": "1.0", "source_system": analysis["metadata"].get("url"),
                "proposal_rationale": analysis.get("proposal_rationale", ""),
                "targets": [{"canonical": canonical, "changes": items} for canonical, items in groups.items()],
                "reuse_mappings": [r["request"] for r in prepared if not r["issues"] and r["request"]["action"] == "reuse"],
                "excluded": [r for r in prepared if r["issues"]], "submitted": False}
    (directory / "proposal-draft.md").write_text(dossier, encoding="utf-8")
    (directory / "proposal-submission.md").write_text("\n".join(submission), encoding="utf-8")
    (directory / "proposal-changes.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def command_prepare_proposal(args: argparse.Namespace) -> int:
    directory = args.output_dir.expanduser().resolve()
    review_path = (args.review_file or directory / "review-decisions.json").expanduser().resolve()
    try:
        analysis = json.loads((directory / "analysis.json").read_text(encoding="utf-8"))
        review = json.loads(review_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise AnalysisError(f"Cannot load analysis and review files: {error}") from error
    apply_review_decisions(analysis, review)
    destination = directory / "proposal-draft.md"
    if review_path in {destination, directory / "proposal-submission.md", directory / "proposal-changes.json"}:
        raise AnalysisError("Proposal draft and review JSON must use separate paths.")
    write_proposal_outputs(analysis, directory)
    print(f"Wrote {destination}")
    print("Working draft based on saved analysis evidence. Refresh analysis before relying on current Jira or draft-build status. Resolve placeholders and coordinate with existing tickets before submission.")
    return 0


def command_review(args: argparse.Namespace) -> int:
    directory = args.output_dir.expanduser().resolve()
    review_path = (args.review_file or directory / "review-decisions.json").expanduser().resolve()
    try:
        analysis = json.loads((directory / "analysis.json").read_text(encoding="utf-8"))
        review = json.loads(review_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise AnalysisError(f"Cannot load analysis and review files: {error}") from error
    apply_review_decisions(analysis, review)
    current = build_review_template(analysis)["decisions"]
    identity = lambda d: tuple(d.get(k) for k in ("proposal", "source_code", "target_system", "target_code"))
    catalog = analysis.get("tho_code_system_catalog", [])
    if getattr(args, "tho_package_dir", None):
        package_dir, _ = resolve_latest_package_dir(args.tho_package_dir)
        catalog = tho_catalog(package_dir)
    suggestions = {}
    for concept in analysis["concepts"]:
        suggested = {}
        for artifact in analysis.get("context_target_artifacts", []):
            if is_tho_canonical(artifact.get("canonical", "")):
                suggested.setdefault(artifact["canonical"], []).extend(e.get("source", "IG context") for e in artifact.get("discovery_evidence", []))
        for proposal in analysis.get("proposal_matches", []):
            for canonical in proposal.get("target_canonicals", []):
                if is_tho_canonical(canonical):
                    suggested.setdefault(canonical, []).append(f"{proposal['key']} · context {proposal.get('context_alignment', 'unknown')}")
        suggestions[concept["code"]] = suggested
    payload = {"review": review, "path": str(review_path), "concepts": analysis["concepts"],
               "catalog": catalog, "suggestions": suggestions,
               "target_styles": {a["canonical"]: a["style_review"]["summaries"] for a in list(analysis.get("context_target_artifacts", [])) + [a for p in analysis.get("proposal_matches", []) for a in p.get("tho_target_artifacts", [])] if a.get("style_review")},
               "contexts": [next(({k: p.get(k) for k in ("context_alignment", "context_score", "context_evidence", "assessment")} for p in analysis.get("proposal_matches", []) if p.get("key") == d.get("proposal")), None) for d in review["decisions"]],
               "kinds": [next((c.get("decision_kind") for c in current if identity(c) == identity(d)), None) for d in review["decisions"]],
               "statuses": [d["effective_status"] for d in analysis["review_decisions"]],
               "current": [next((c["reviewed_evidence"] for c in current if identity(c) == identity(d)), None) for d in review["decisions"]]}
    # Prevent embedded evidence from closing the inert JSON script element.
    data = json.dumps(payload, ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    template = Path(__file__).with_name("review.html").read_text(encoding="utf-8")
    destination = directory / "review.html"
    if destination == review_path:
        raise AnalysisError("Review JSON and browser page must use separate paths.")
    destination.write_text(template.replace("__REVIEW_DATA__", data), encoding="utf-8")
    print(f"Open in your browser: {destination}")
    print(f"Download edited decisions and replace {review_path}; then rerun analysis.")
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
    analyze_parser.add_argument("--fetch-drafts", action="store_true", help="Fetch manifest-listed UTG draft artifacts and cache snapshots")
    analyze_parser.add_argument("--draft-file", action="append", default=[], help="Local draft override UP-number=path/to/Resource.json; repeatable")
    analyze_parser.add_argument("--review-file", type=Path, help="Override the automatic output-dir/review-decisions.json path")
    analyze_parser.add_argument("--write-review-template", type=Path, help="Deprecated alias for a custom review-file path")
    analyze_parser.add_argument("input", type=Path, help="FHIR CodeSystem JSON or XML")
    analyze_parser.add_argument("--output-dir", type=Path, required=True, help="Directory for generated analysis")
    analyze_parser.add_argument(
        "--ig-dir",
        type=Path,
        help="IG directory to scan for ValueSet usage and profile bindings",
    )
    analyze_parser.add_argument(
        "--fhir-package-dir",
        type=Path,
        help=(
            "Extracted base FHIR package directory used to compare profile "
            "bindings with their base elements"
        ),
    )
    analyze_parser.add_argument(
        "--tho-package-dir",
        type=Path,
        help=(
            "Installed hl7.terminology.r4 package directory, or unversioned "
            "package-family path resolved to the latest installed version"
        ),
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
    review_parser = subparsers.add_parser("review", help="Create an offline browser review page")
    review_parser.add_argument("--output-dir", required=True, type=Path)
    review_parser.add_argument("--review-file", type=Path)
    review_parser.add_argument("--tho-package-dir", type=Path, help="Refresh target lookup catalog from an installed THO package")
    review_parser.set_defaults(handler=command_review)
    draft_parser = subparsers.add_parser("prepare-proposal", help="Prepare a reviewed working draft without submitting to Jira")
    draft_parser.add_argument("--output-dir", required=True, type=Path)
    draft_parser.add_argument("--review-file", type=Path)
    draft_parser.set_defaults(handler=command_prepare_proposal)
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
