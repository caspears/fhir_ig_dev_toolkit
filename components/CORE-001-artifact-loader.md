# CORE-001 – Artifact Loader
What artifacts exist? - What artifacts are available?
The artifacts, with content, metadata, and provenance.

## Purpose

Load artifacts from configured artifact sources and produce normalized artifact content, metadata, and provenance for use by downstream toolkit components.

## Definition / Key Concepts

An artifact is any discrete source, generated, or internal object that participates in the authoring, analysis, validation, visualization, documentation, maturation, publication, or maintenance of a FHIR Implementation Guide or related interoperability specification.

An artifact source is a location or mechanism from which artifacts are loaded, such as a local folder, generated SUSHI output, package folder, downloaded IG package, ZIP archive, Git repository, package registry, or FHIR server.

## Phase

Phase 0 – Core Infrastructure

## Priority

High

## Maturity

Design

## Status

Planned

## Responsibilities

The Artifact Loader is responsible for:

- Discovering artifacts from configured artifact sources.
- Loading supported artifact content.
- Extracting and normalizing basic artifact metadata.
- Identifying artifact type.
- Recording artifact provenance.
- Performing basic load-level validation.
- Reporting loading errors and warnings.


## Non-Responsibilities

The Artifact Loader is not responsible for:

- Building the artifact index.
- Resolving references or canonicals.
- Discovering relationships.
- Building graphs.
- Rendering diagrams.
- Performing conformance validation.
- Interpreting business meaning.
- Modifying source artifacts.

## Inputs

Initial expected sources:

- Local FHIR resource folders
- `input/fsh`
- `fsh-generated/resources`
- npm package folders
- downloaded IG packages

Future possible sources:

- ZIP archives
- Git repositories
- FHIR servers
- package registries
- external workflow or template repositories

## Outputs

A collection of loaded artifacts with basic metadata and provenance sufficient for indexing by `CORE-002 – Artifact Index`.

## Dependencies

None

## Consumers

- `CORE-002 – Artifact Index`

## Next Action

Define the initial supported artifact sources and the minimum loaded artifact metadata.

## Open Questions

- What minimum metadata must every loaded artifact include?
- Should unsupported files be ignored, warned, or recorded as unsupported artifacts?
- Should generated artifacts and source artifacts be loaded through the same interface?

## Notes