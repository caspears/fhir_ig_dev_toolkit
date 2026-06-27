ADR-003 – Common Graph Model



## Status
Completed

## Context
The toolkit will perform many different kinds of analysis and visualization. Initially these capabilities focus on relationships among example resources within a single Implementation Guide, but future phases include profile analysis, terminology analysis, dependency analysis, documentation generation, authoring assistance, and potentially cross-Implementation Guide ecosystem analysis.

Although these capabilities operate on different kinds of artifacts and relationships, they all share a common need to represent entities and the relationships between them.

Allowing each capability or renderer to define its own relationship model would lead to duplicated logic, inconsistent behavior, and increased maintenance. A common representation is needed to enable reusable analysis, visualization, and documentation across the toolkit.

## Decision
The toolkit shall use a common graph model as the canonical representation of discovered relationships.

The graph model shall:

represent artifacts as nodes
represent discovered relationships as edges
support multiple node and edge types
preserve sufficient metadata to support analysis and rendering
remain independent of any specific visualization or storage technology

All relationship discovery components shall produce this common graph model.

Analysis components and renderers shall consume the common graph model rather than directly parsing source artifacts.

## Consequences
Benefits

One relationship discovery implementation can support many downstream capabilities.
New renderers can be added without modifying discovery logic.
Multiple analyses can operate on the same graph.
Future phases can extend the graph with additional node and edge types without redesigning the architecture.

Trade-offs

The graph model must be designed carefully to remain generic while supporting future capabilities.
Some specialized analyses may require additional metadata beyond simple node-edge relationships.