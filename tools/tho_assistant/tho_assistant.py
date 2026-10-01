#!/usr/bin/env python3
"""Small command-line entry point for the THO Proposal Assistant MVP.

python tools/tho_assistant/tho_assistant.py analyze tools\\tho_assistant\\tests\\fixtures\\CodeSystem-example.json --output-dir build/tho-analysis
"""

from __future__ import annotations

import argparse
import copy
import getpass
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
                for code, candidate in candidate_by_code.items():
                    proposed = next((row for row in proposal.get("proposed_concepts", [])
                                     if row["code"] == code), {})
                    target_code = proposed.get("proposed_code", code)
                    target = target_concepts.get(target_code)
                    concept_results.append(
                        {
                            "code": code,
                            "target_code": target_code,
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
            rf"(?<![A-Za-z0-9_-]){re.escape(term)}(?![A-Za-z0-9_-])",
            text,
            flags=re.IGNORECASE,
        )
    ]


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


def map_proposed_concepts(fields: dict[str, Any], codes: list[str]) -> list[dict[str, Any]]:
    all_rows = extract_proposed_concepts(fields, None)
    results = extract_proposed_concepts(fields, codes)
    for result in results:
        if result["status"] != "not-extracted":
            continue
        # A display mention is a discovery signal, not proof of equivalence.
        candidates = [row for row in all_rows if row["status"] == "extracted"
                      and _mentioned_terms([result["code"]], row["display"])]
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
                    "proposed_concepts": map_proposed_concepts(fields, candidate_codes),
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


def analyze(
    resource: dict[str, Any],
    source: Path,
    proposal_payloads: list[dict[str, Any]] | None = None,
    valueset_usage: list[dict[str, Any]] | None = None,
    binding_context: list[dict[str, Any]] | None = None,
    tho_package_dir: Path | None = None,
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
            resource, concepts, proposal_payloads, valueset_usage, binding_context
        )
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
    for decision in review["decisions"]:
        if not isinstance(decision, dict) or decision.get("decision") not in {"pending", "confirmed", "rejected"}:
            raise AnalysisError("Each review decision must be pending, confirmed, or rejected.")
        identity = tuple(decision.get(key) for key in ("proposal", "source_code", "target_system", "target_code"))
        valid_proposal = isinstance(identity[0], str) and bool(identity[0]) or (
            identity[0] is None and decision.get("decision_kind") == "target-system-suitability")
        if not valid_proposal or not all(isinstance(value, str) and value for value in identity[1:]) or identity in seen:
            raise AnalysisError("Review decisions require unique proposal/source/target identities.")
        seen.add(identity)
        matching = [row for row in current if tuple(row[key] for key in
                    ("proposal", "source_code", "target_system", "target_code")) == identity]
        status = decision["decision"]
        if not matching:
            status = "evidence-unavailable"
        elif (decision.get("reviewed_evidence") != matching[0]["reviewed_evidence"] or
              decision.get("decision_kind") != matching[0].get("decision_kind")):
            status = "requires-re-review"
        results.append({**decision, "effective_status": status})
    analysis["review_decisions"] = results


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
        results.append({"source_code": code, "action": action, "reason": reason,
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
            for evidence in recommendation["confirmed_evidence"]:
                lines.extend(["", f"- `{recommendation['source_code']}` → `{evidence['target_system']}#{evidence['target_code']}`: "
                              f"{evidence['proposal']} ({evidence.get('proposal_status')}); "
                              f"installed target: {evidence.get('installed_code_status')}; "
                              f"proposed change: {evidence.get('proposed_change')}."])
        lines.extend(["", "Publication readiness is not established. Verify the required target display and definition in the intended published THO version before updating the IG. Jira status and mapping confirmation alone are insufficient."])

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
                                "| Jira proposal | " + _escape_table(concept.get("proposed_display")) + " | " + _escape_table(concept.get("proposed_definition")) + " |", "",
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
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.review_file and args.write_review_template:
        raise AnalysisError("Use --review-file alone; --write-review-template is deprecated.")
    review_path = (args.review_file or args.write_review_template or
                   (output_dir / "review-decisions.json")).expanduser().resolve()
    if review_path in {output_dir / "analysis.json", output_dir / "concept-inventory.md", source}:
        raise AnalysisError("The review file must be separate from the input and generated reports.")
    created, added = maintain_review_file(result, review_path)
    result["recommendations"] = build_recommendations(result)
    (output_dir / "analysis.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output_dir / "concept-inventory.md").write_text(
        render_markdown(result), encoding="utf-8"
    )
    print(f"Analyzed {result['concept_count']} concepts from {source}")
    print(f"Wrote {output_dir / 'analysis.json'}")
    print(f"Wrote {output_dir / 'concept-inventory.md'}")
    print(f"Review file {'created' if created else 'reused'}: {review_path}")
    if added:
        print(f"Added {added} pending mappings; existing decisions and reviewed evidence preserved.")
    counts: dict[str, int] = {}
    for decision in result["review_decisions"]:
        status = decision["effective_status"]
        counts[status] = counts.get(status, 0) + 1
    print("Review status: " + (", ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "no extracted mappings available"))
    print("Next: Review concept-inventory.md. Set applicable pending decisions to confirmed or rejected; optionally add a note.")
    print("Preserve mapping identities and reviewed_evidence. Save the review file and rerun the same command.")
    if counts.get("requires-re-review"):
        print("Changed evidence: compare current findings with the saved baseline before updating reviewed_evidence and reconfirming.")
    print("Keep the review file between runs. Use a separate output directory for each CodeSystem.")
    if args.write_review_template:
        print("--write-review-template is deprecated; use --review-file for this custom path on subsequent runs.")
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
