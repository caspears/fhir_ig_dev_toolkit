# FHIR IG Analysis Toolkit

## Vision

A suite of tools that assist FHIR Implementation Guide authors and standards developers in understanding, authoring, validating, documenting, visualizing, maturing, and maintaining FHIR Implementation Guides throughout their lifecycle.

PurposeA suite of tools that assist FHIR Implementation Guide authors and standards developers in understanding, authoring, validating, documenting, visualizing, maturing, and maintaining FHIR Implementation Guides throughout their lifecycle.

## Guiding Principles
### Model-Driven Architecture

The toolkit is built around a series of shared semantic models. Components communicate through well-defined models rather than through implementation-specific representations. This promotes reuse, extensibility, and separation of concerns while enabling multiple downstream capabilities—such as visualization, analysis, documentation, validation, and future tooling—to operate on common representations.

## Scope

The toolkit is intended to support the development and maintenance of FHIR Implementation Guides and related standards artifacts.

This includes tools that analyze, generate, validate, visualize, and document:

- ImplementationGuide resources
- StructureDefinitions (profiles, extensions, logical models)
- ValueSets and CodeSystems
- ConceptMaps
- NamingSystems
- CapabilityStatements
- OperationDefinitions
- SearchParameters
- ExampleScenario resources
- ActorDefinition resources
- Requirements resources
- TestPlan and TestScript resources
- SubscriptionTopic resources
- GraphDefinition resources
- StructureMaps
- Questionnaire and Library resources
- Example resources used within implementation guides

The toolkit may also support artifacts external to FHIR resources-including FSH, Markdown documentation, PlantUML, Mermaid, GraphViz, spreadsheets, business process models, and other implementation assets-when they contribute to the specification, documentation, testing, or lifecycle management of an implementation guide.

## Roadmap

### Phase 0 - Core Infrastructure - Build the Platform

This contains the plumbing every capability will reuse.

Examples:

- Package loader
- IG loader
- FSH loader
- Resource indexing
- Canonical resolution
- StructureDefinition cache
- Terminology cache
- Relationship Discovery Engine (Formerly Reference resolver then Relationship extractor)
- Common utilities
- Configuration management
- Plugin framework (eventually)

Without these, every later phase ends up duplicating code.


### Phase 0.5 – Core Models

The deliverables would be:

- MODEL-001 – Artifact Model
- MODEL-002 – Artifact Index Model
- MODEL-003 – Relationship Model
- MODEL-004 – Common Graph Model

### Phase 1 - Relationship Engine (Foundation) - Discover Relationships

This becomes the common engine that everything else uses.

| **Status** | **Use Case**                     | **Notes**                                          |
| ---------- | -------------------------------- | -------------------------------------------------- |
| ☐          | Resource relationship extraction | Parse references between example resources         |
| ☐          | Scenario detection/grouping      | Explicit scenarios, Bundles, folders, or manifests |
| ☐          | Relationship graph model         | Internal graph representation                      |
| ☐          | PlantUML output                  | Initial visualization format                       |
| ☐          | Graph export                     | JSON/GraphML for future tools                      |

### Phase 2 - Example Visualization - Visualize Examples

| **Status** | **Use Case**                     |
| ---------- | -------------------------------- |
| ☐          | Scenario relationship diagrams   |
| ☐          | Bundle composition diagrams      |
| ☐          | Timeline view of scenarios       |
| ☐          | Scenario completeness validation |
| ☐          | Cross-scenario comparison        |

### Phase 3 - Profile Analysis - Understand Profiles

| **Status** | **Use Case**                           |
| ---------- | -------------------------------------- |
| ☐          | Profile reference diagrams             |
| ☐          | Profile inheritance diagrams           |
| ☐          | Differential vs Snapshot visualization |
| ☐          | Extension usage diagrams               |
| ☐          | Cardinality visualization              |

### Phase 4 - Terminology Analysis - Understanding Terminology

| **Status** | **Use Case**                   |
| ---------- | ------------------------------ |
| ☐          | ValueSet dependency diagrams   |
| ☐          | CodeSystem usage               |
| ☐          | Binding strength visualization |
| ☐          | Unused terminology detection   |

### Phase 5 - IG Dependency Analysis - Understanding Dependencies

| **Status** | **Use Case**                  |
| ---------- | ----------------------------- |
| ☐          | Impact analysis               |
| ☐          | Dependency graphs             |
| ☐          | Orphan profile detection      |
| ☐          | Circular dependency detection |
| ☐          | External dependency reports   |

### Phase 6 - Documentation Generation - Generate Documentation

| **Status** | **Use Case**             |
| ---------- | ------------------------ |
| ☐          | PlantUML diagrams        |
| ☐          | Mermaid diagrams         |
| ☐          | Cross-reference matrices |
| ☐          | Markdown documentation   |
| ☐          | HTML reports             |

### Phase 7 - Authoring Assistance - Assist the Author

| **Status** | **Use Case**                         |
| ---------- | ------------------------------------ |
| ☐          | Missing example detection            |
| ☐          | Missing profile references           |
| ☐          | Example/profile consistency checking |
| ☐          | IG QA enhancements                   |
| ☐          | Suggested example generation         |

### Future Capability Domains

**Potential future expansions once the core toolkit is mature:**

- **Cross-IG Ecosystem Analysis**
- **Workflow Architecture Modeling**
- **Business Concept Mapping**
- **Cross-IG Dependency Analysis**
- **Multi-IG Validation**
- **Implementation Landscape Visualization**

Possible capabilities:

- Cross-IG dependency analysis
- Cross-IG workflow visualization
- Business concept mapping
- Resource lifecycle mapping
- Canonical/profile overlap detection
- Shared terminology analysis
- Touch-point identification
- Ecosystem architecture diagrams
- Version compatibility analysis
- IG overlap and gap analysis

## What we've already discussed

From our previous conversations, I'd actually mark several items as already explored or partially complete:

| **Status** | **Capability**                                   |
| ---------- | ------------------------------------------------ |
| ✔          | Validation profile generation and optimization   |
| ✔          | Scenario creation for PCT examples               |
| ✔          | Subscription workflow visualization (manual)     |
| ✔          | CodeSystem generation utilities                  |
| ✔          | Example conformance improvement techniques       |
| ◐          | Relationship visualization (currently designing) |

So we're not starting from scratch-we've already laid some of the groundwork.

**I also think we should maintain a "parking lot"**

As we work, we'll undoubtedly think of additional ideas. Rather than interrupting the current effort, we can capture them in a backlog.

Examples that came to mind while thinking about this include:

- Sequence diagrams for FHIR interactions.
- REST operation diagrams showing client/server exchanges.
- Automatic IG overview diagrams.
- CapabilityStatement relationship diagrams.
- SearchParameter usage analysis.
- FSH dependency visualization.
- Package dependency graphs.
- Validation performance analysis (building on the HAPI validator optimization discussion we've already had).
- "What changed?" visualizations between IG versions.
- Release readiness dashboards (e.g., missing examples, unresolved TODOs, incomplete narratives).

**I think this could become something much larger**

Based on the kinds of questions you've been asking over the past couple of weeks, I no longer think of this as "a tool to generate PlantUML."

I think it has the potential to become an **IG Development Toolkit** that helps throughout the entire authoring lifecycle:

- **Author** - create and maintain profiles, examples, and terminology.
- **Understand** - visualize relationships and dependencies.
- **Validate** - ensure consistency, completeness, and conformance.
- **Document** - generate diagrams, tables, and narrative documentation.
- **Maintain** - analyze impacts, identify dead references, and compare versions.

One thing I would add to our roadmap is to assign each capability a priority (High/Medium/Low) and a maturity (Idea, Design, Prototype, Implemented). That gives us a lightweight project board we can revisit in future conversations.

I also think we should keep a running design document where we capture decisions-things like _"How do we define a scenario?"_ or _"Which profile references should appear in a diagram?"_ Those decisions will become the architecture for the toolkit, and they'll be just as valuable as the code itself as the project grows.