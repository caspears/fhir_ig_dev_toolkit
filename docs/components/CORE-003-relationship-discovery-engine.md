# CORE-003 – Relationship Discovery Engine
What relationships exist among indexed artifacts?

## Purpose

The Relationship Discovery Engine discovers explicit and inferred relationships among loaded and indexed artifacts and prepares those relationships for representation in the common graph model.

## Definition / Key Concepts

A relationship is a meaningful connection between two or more artifacts.

Relationships may be explicit, such as a FHIR `Reference.reference`, canonical URL, package dependency, or terminology binding.

Relationships may also be inferred, such as Bundle membership, scenario membership, profile usage, profile inheritance, workflow participation, or other relationships derived through analysis.

## Phase

Phase 0 – Core Infrastructure

## Priority

High

## Maturity

Design

## Status

Planned

## Responsibilities

The Relationship Discovery Engine is responsible for:

- Discovering explicit relationships among loaded and indexed artifacts.
- Discovering inferred relationships among loaded and indexed artifacts when supported by discovery logic.
- Classifying discovered relationships by type.
- Preserving relationship metadata, including source artifact, target artifact, relationship path, relationship kind, and discovery method when available.
- Reporting unresolved, ambiguous, duplicate, or conflicting relationships.
- Producing relationship data suitable for the common graph model.

## Non-Responsibilities

The Relationship Discovery Engine is not responsible for:

- Acquiring artifacts from artifact sources.
- Creating artifact indexes.
- Rendering diagrams.
- Generating documentation.
- Performing full conformance validation.
- Modifying source artifacts.
- Deciding how relationships should be visually displayed.

## Inputs

- Loaded artifacts from `CORE-001 – Artifact Loader`
- Indexed artifact catalog from `CORE-002 – Artifact Index`
- Supported relationship discovery logic

## Outputs

- Discovered relationships
- Relationship discovery warnings and diagnostics
- Relationship data suitable for `CORE-004 – Common Graph Model`

## Dependencies

- `CORE-001 – Artifact Loader`
- `CORE-002 – Artifact Index`

## Consumers

- `CORE-004 – Common Graph Model`
- Future analysis components
- Future validation and QA components
- Future documentation generation components

## Next Action

Define the initial relationship types supported by the first implementation.

## Open Questions

- What relationship types are required for the first implementation?
- How should explicit and inferred relationships be distinguished?
- How should unresolved references be represented?
- How much source path detail should be preserved for each relationship?
- Should relationship discovery operate across one IG at a time initially, or allow multiple indexed sources?

## Notes

The Relationship Discovery Engine answers the question: "What meaningful connections exist among the artifacts?"

Initial relationship discovery is expected to focus on example resources, including FHIR references, Bundle membership, canonical references, and missing or unresolved references.

The component uses loaded artifact content and the artifact index, but it does not acquire artifacts from source locations or create indexes itself.

The component should be designed so additional relationship types can be added later without changing the overall architecture.