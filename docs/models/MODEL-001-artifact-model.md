# MODEL-001 – Artifact Model

## Purpose

The Artifact Model defines the minimum shared representation of an artifact after it has been loaded by `CORE-001 – Artifact Loader`.

It provides the contract used by downstream components to identify, inspect, index, and analyze loaded artifacts without requiring knowledge of how or where the artifact was originally acquired.

## Produced By

- `CORE-001 – Artifact Loader`

## Consumed By

- `CORE-002 – Artifact Index`
- `CORE-003 – Relationship Discovery Engine`
- Future analysis, validation, documentation, and rendering components

## Model Status

Draft

## Model Version

0.1

## Definition

An artifact is any discrete source, generated, or internal object that participates in the authoring, analysis, validation, visualization, documentation, maturation, publication, or maintenance of a FHIR Implementation Guide or related interoperability specification.

## Design Principles

- The model represents loaded artifacts, not artifact sources.
- The model preserves available metadata, identifiers, version information, provenance, and dates.
- The model should be sufficient for indexing and relationship discovery.
- The model should not require every artifact type to use the same identifier scheme.
- The model should support future artifact types without redesigning the core structure.
- Source artifacts and generated artifacts are represented as separate artifacts.

## Core Fields

| Field | Required | Description |
| --- | :---: | --- |
| `artifact_uid` | Yes | Stable toolkit identifier for the loaded artifact. Deterministic when sufficient identity information is available; otherwise generated as a fallback. |
| `uid_deterministic` | Yes | Indicates whether the artifact UID was deterministically derived. |
| `artifact_type` | Yes | Broad artifact type (e.g., `fhir-resource`, `fsh`, `cql`, `package`, `markdown`, `unknown`). |
| `source` | Yes | Provenance information describing where the artifact was loaded from. |
| `raw_content_ref` | Yes | Reference to the original loaded content. |
| `parsed_content_ref` | No | Reference to parsed or normalized content when available. |
| `metadata` | Yes | Normalized metadata extracted during loading. |
| `diagnostics` | No | Loading warnings, errors, or informational messages associated with the artifact. |
| `annotations` | No | Optional toolkit-generated annotations. |
| `extensions` | No | Optional structured extension data for artifact-specific information not represented in the core model. |

## Metadata Fields

| Field | Required | Description |
| --- | :---: | --- |
| `name` | No | Human-readable name when available. |
| `title` | No | Human-readable title when available. |
| `description` | No | Description or summary when available. |
| `identifier` | No | Artifact identifier when available. |
| `canonical` | No | Canonical URL when available. |
| `version` | No | Artifact version when available. |
| `status` | No | Publication or lifecycle status when available. |
| `kind` | No | More specific artifact kind (e.g., `StructureDefinition`, `Bundle`, `FSHInstance`). |
| `date` | No | Artifact-declared publication or effective date when available. |
| `last_updated` | No | Artifact-declared last updated date/time when available (e.g., FHIR `meta.lastUpdated`). |

## FHIR-Specific Metadata

FHIR artifacts may normalize the following metadata when available:

- `resourceType`
- `id`
- `url`
- `version`
- `name`
- `title`
- `status`
- `date`
- `meta.profile`
- `meta.lastUpdated`

Additional FHIR-specific metadata should remain within `extensions` until required by downstream components.

## Source Fields

| Field | Required | Description |
| --- | :---: | --- |
| `source_type` | Yes | Source category (e.g., `local-file`, `directory`, `generated-sushi`, `package`, `zip`, `git`, `fhir-server`). |
| `source_location` | Yes | Original source location. |
| `relative_path` | No | Relative path within the source when applicable. |
| `package_id` | No | Package identifier when applicable. |
| `package_version` | No | Package version when applicable. |
| `source_last_modified` | No | Last modified date/time reported by the source when available. |
| `retrieved_at` | No | Date/time the toolkit loaded or retrieved the artifact. |

## Artifact Relationships

The Artifact Model does not directly represent relationships between artifacts.

Relationships, including lineage between source and generated artifacts, are discovered by `CORE-003 – Relationship Discovery Engine`.

## Unsupported Artifacts

Unsupported or unrecognized files should still be represented as artifacts whenever practical.

These artifacts should:

- receive an appropriate artifact type (e.g., `unknown` or `unsupported`);
- preserve available provenance and metadata;
- include diagnostics describing why they could not be processed.

Representing unsupported artifacts allows anomaly detection, repository analytics, quality assurance, and future roadmap planning.

## Example

```json
{
  "artifact_uid": "artifact-000001",
  "uid_deterministic": true,
  "artifact_type": "fhir-resource",
  "source": {
    "source_type": "generated-sushi",
    "source_location": "fsh-generated/resources/Bundle-colonoscopy-gfe-packet.json",
    "relative_path": "Bundle-colonoscopy-gfe-packet.json"
  },
  "raw_content_ref": "fsh-generated/resources/Bundle-colonoscopy-gfe-packet.json",
  "parsed_content_ref": "memory://artifact/000001",
  "metadata": {
    "kind": "Bundle",
    "resourceType": "Bundle",
    "id": "colonoscopy-gfe-packet",
    "identifier": "Bundle/colonoscopy-gfe-packet"
  },
  "diagnostics": [],
  "annotations": {},
  "extensions": {}
}
```

## Open Questions

- Should the toolkit define a standard deterministic UID generation strategy?
- What format should `artifact_uid` use?
- Should source retrieval history be retained beyond the most recent retrieval?

## Notes

The Artifact Model intentionally represents loaded artifacts rather than artifact sources.

Version information should always be preserved when available, even when not immediately required by downstream components.

The Artifact Model is expected to evolve as additional artifact types are supported, while maintaining a stable contract for downstream components.