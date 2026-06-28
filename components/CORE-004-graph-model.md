# CORE-004 – Graph Model
The toolkit’s canonical, technology-independent representation of artifacts, activities, actors, and their meaningful relationships.

## Purpose

The Common Graph Model provides the toolkit's canonical, technology-independent representation of artifacts, activities, actors, and their meaningful relationships.

It serves as the common interchange model between relationship discovery, analysis, documentation generation, validation, and rendering components.

## Definition / Key Concepts

A graph is composed of:

- Nodes, representing artifacts, activities, actors, systems, or other modeled entities.
- Edges, representing meaningful relationships between nodes.

The initial implementation is expected to focus on artifact nodes and artifact-to-artifact relationships. However, the graph model should be general enough to support activity-oriented nodes and relationships in future phases, including actions, interactions, workflows, actors, systems, and business processes.

The graph model is intended to be independent of any specific visualization technology or storage implementation.

## Phase

Phase 0 – Core Infrastructure

## Priority

High

## Maturity

Design

## Status

Planned

## Responsibilities

The Common Graph Model is responsible for:

- Providing a canonical representation of modeled entities and their relationships.
- Representing artifacts, activities, actors, systems, or other modeled entities as graph nodes.
- Representing meaningful relationships between modeled entities as graph edges.
- Preserving node and edge metadata required by downstream components.
- Remaining independent of any specific visualization, storage, workflow, or analysis technology.
- Supporting reuse by multiple downstream toolkit components.

## Non-Responsibilities

The Common Graph Model is not responsible for:

- Loading artifacts.
- Creating artifact indexes.
- Discovering relationships.
- Determining diagram layout.
- Defining renderer-specific presentation details.
- Performing analysis.
- Performing conformance validation.
- Rendering diagrams.

## Inputs

- Relationship data produced by `CORE-003 – Relationship Discovery Engine`

## Outputs

- Canonical graph representation
- Graph metadata suitable for downstream analysis and rendering

## Dependencies

- `CORE-003 – Relationship Discovery Engine`

## Consumers

- `CORE-005 – Renderer Framework`
- Future analysis components
- Future documentation generation components
- Future validation and QA components
- Future ecosystem analysis components

## Next Action

Define the minimum node, edge, and graph metadata required for the initial implementation.

## Open Questions

- What metadata should every graph node contain?
- What metadata should every graph edge contain?
- How should graph-level metadata be represented?
- How should unresolved or external relationships be represented?
- Should graph metadata distinguish between explicit and inferred relationships?
- How should multiple relationship types between the same pair of artifacts be represented?

## Notes

The Common Graph Model answers the question:

> "How are artifacts and their meaningful relationships represented independently of any particular consumer?"

The Common Graph Model is the canonical relationship representation used throughout the toolkit.

The graph model is intentionally independent of visualization technologies such as PlantUML, Mermaid, GraphViz, HTML, GraphML, or future rendering implementations. It is likewise independent of analysis and validation components, allowing all downstream capabilities to operate from a shared representation.

The graph model should represent semantic information rather than presentation details. Visualization-specific concepts such as node shape, color, layout, edge routing, or styling belong to renderer implementations rather than the graph itself.

### Design Direction

The Common Graph Model is the toolkit's canonical, technology-independent representation of artifacts and their meaningful relationships.

The graph model is not a visualization model. It should not encode renderer-specific concepts such as diagram layout, shapes, arrows, colors, or grouping rules except as optional presentation metadata supplied by downstream components.

The graph model represents both:

- artifacts, as nodes
- meaningful relationships between artifacts, as edges

The graph model is intended to support multiple downstream consumers, including:

- visualization renderers
- analysis components
- documentation generation
- validation and QA tooling
- future cross-IG or ecosystem-level analysis

Renderers and analysis tools should consume the common graph model rather than directly parsing source artifacts or rediscovering relationships.

Should be able to support:
Artifact → relationship → Artifact
Actor/System → performs → Activity
Activity → uses/produces → Artifact
Activity → precedes/follows → Activity
Artifact → constrains/supports/documents → Activity