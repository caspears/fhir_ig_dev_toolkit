# FHIR IG Development Toolkit Glossary

This glossary defines the common vocabulary used throughout the FHIR IG Development Toolkit.

The glossary is intended to provide consistent terminology across the roadmap, architecture documents, Architectural Decision Records (ADRs), component specifications, model specifications, and implementation.

---

## Annotation

Additional information produced by the toolkit that augments an existing model without modifying its semantic meaning.

Annotations are typically generated during analysis and may be used by downstream components.

---

## Artifact

A discrete source, generated, or internal object that participates in the authoring, analysis, validation, visualization, documentation, maturation, publication, or maintenance of a FHIR Implementation Guide or related interoperability specification.

Examples include:

- FHIR resources
- FSH definitions
- CQL libraries
- ValueSets
- CodeSystems
- Implementation Guides
- npm packages
- Markdown documentation
- SQL on FHIR ViewDefinitions
- Templates
- Workflow definitions
- Future interoperability artifacts

---

## Artifact Index

A collection of searchable indexes that organizes loaded artifacts using available identifiers, metadata, version information, provenance, and other searchable characteristics.

The Artifact Index is produced by `CORE-002 – Artifact Index`.

---

## Artifact Model

The shared semantic model representing a loaded artifact after processing by the Artifact Loader.

Defined by `MODEL-001 – Artifact Model`.

---

## Artifact Source

A location or mechanism from which artifacts are acquired.

Examples include:

- Local directories
- Generated SUSHI output
- npm packages
- ZIP archives
- Git repositories
- FHIR servers

An artifact source is not itself an artifact.

---

## Canonical Representation

The toolkit's technology-independent representation of information that can be reused by multiple downstream components.

---

## Common Graph Model

The toolkit's canonical representation of modeled entities and the meaningful relationships between them.

The Common Graph Model is independent of visualization technologies and serves as the primary interchange model for downstream components.

Defined by `MODEL-004 – Common Graph Model`.

---

## Component

A major architectural building block of the toolkit with defined responsibilities, inputs, outputs, dependencies, and consumers.

Examples include:

- Artifact Loader
- Artifact Index
- Relationship Discovery Engine
- Renderer Framework

---

## Diagnostic

Information describing warnings, errors, informational messages, or other observations generated during toolkit processing.

Diagnostics are intended to support quality assurance, troubleshooting, and analysis.

---

## Edge

A graph element representing a meaningful relationship between two nodes.

---

## Extension

Structured information that extends a model without changing the core model definition.

Extensions allow the toolkit to support additional artifact types and future capabilities while maintaining stable model contracts.

---

## Metadata

Structured descriptive information about an artifact or other modeled entity.

Examples include:

- identifier
- canonical URL
- version
- publication status
- provenance
- dates

Metadata describes an entity but is not the entity itself.

---

## Modeled Entity

Any object represented within the Common Graph Model.

Examples include:

- Artifacts
- Activities
- Actors
- Systems
- Future entity types

Modeled entities are represented as graph nodes.

---

## Model

A shared semantic contract exchanged between toolkit components.

Models define what the toolkit knows rather than how it is implemented.

---

## Node

A graph element representing a modeled entity.

Nodes are connected through meaningful relationships represented by edges.

---

## Presentation Metadata

Information used by renderers to control presentation without altering semantic meaning.

Examples include:

- Colors
- Shapes
- Layout hints
- Tooltips
- Icons
- Grouping

Presentation metadata is distinct from semantic metadata.

---

## Prepared Toolkit Model

A semantic model that has been prepared for consumption by downstream toolkit components.

Examples include:

- Common Graph Model
- Future documentation models
- Future analysis models

Prepared toolkit models are intended to be independent of implementation technologies.

---

## Provenance

Information describing where an artifact originated and how it was acquired.

Examples include:

- Source location
- Package information
- Retrieval date
- Generation lineage

---

## Relationship

A meaningful semantic connection between two modeled entities.

Relationships may be:

- Explicit
- Inferred

Examples include:

- FHIR references
- Canonical references
- Bundle membership
- Profile inheritance
- Workflow participation
- Generation lineage

---

## Relationship Discovery

The process of identifying explicit and inferred relationships among loaded artifacts.

Performed by `CORE-003 – Relationship Discovery Engine`.

---

## Renderer

A component that transforms a prepared toolkit model into a specific output representation.

Examples include renderers for:

- PlantUML
- Mermaid
- HTML
- Markdown
- JSON
- GraphML

Renderers consume semantic models but do not rediscover relationships.

---

## Semantic Information

Information whose meaning is independent of any particular implementation, visualization, or storage technology.

Semantic information is preserved across all downstream toolkit capabilities.

---

## Strategy

A replaceable implementation approach used by a toolkit component to perform a specific function.

Examples include:

- UID generation
- Relationship discovery logic
- Rendering implementation

Strategies describe *how* work is performed rather than *what* information is represented.

---

## Version Information

Information describing the version of an artifact, package, model, or other versioned entity.

Version information should be preserved whenever available to support future comparison, dependency analysis, and ecosystem analysis.