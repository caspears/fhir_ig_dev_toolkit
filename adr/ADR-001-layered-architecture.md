ADR-001 – Separate extraction from rendering
Explains the overall architectural layering


## Status
Completed

## Context
The initial scripts combine indexing, relationship discovery, and PlantUML rendering.

## Decision
The toolkit will separate artifact loading/indexing, relationship extraction, graph modeling, and rendering.

## Consequences
Renderers will consume a common graph model and will not parse FHIR resources directly.



## Proposed Layered Architecture
                FHIR Artifacts
                     │
                     ▼
          CORE-001 Artifact Loader
                     │
                     ▼
          CORE-002 Artifact Index
                     │
                     ▼
   CORE-003 Relationship Discovery Engine
                     │
                     ▼
          CORE-004 Graph Model
          (canonical representation)
                     │
         ┌───────────┼────────────┐
         ▼           ▼            ▼
PlantUML      Mermaid      GraphViz
Renderer      Renderer      Renderer


## Artifacts
An artifact is any discrete source, generated, or internal object that participates in the authoring, analysis, validation, visualization, documentation, maturation, publication, or maintenance of a FHIR Implementation Guide or related interoperability specification.

## Proposed artifact categories
### 1. FHIR artifacts

Includes:

* all FHIR resources
* all conformance resources
* all definitional resources
* * example resources
* potentially FHIR datatypes when used as analyzable structures

This keeps the model broad enough for StructureDefinition, ValueSet, ExampleScenario, ActorDefinition, Requirements, CapabilityStatement, etc.

### 2. FHIR-adjacent artifacts

Includes:

* CQL
* FSH
* SQL on FHIR ViewDefinition
* logical models
* mapping artifacts
* templates
* workflow definitions

These may not always be FHIR resources, but they still participate in IG authoring, validation, documentation, or implementation.

### 3. Package artifacts

Includes:

* npm packages
* package manifests
* dependency metadata
* canonical packages
* generated package content

This is important because package-level analysis will be central to dependency and cross-IG work.

### 4. Incubator or emerging artifacts

Includes:

* proposed FHIR resources
* IG-specific experimental artifacts
* implementation-specific extensions to tooling
* future HL7 incubator content

These should be supported through extensibility rather than hard-coding.

### 5. Generated/internal toolkit artifacts

Includes:

* relationship graph JSON
* PlantUML
* Mermaid
* GraphViz
* GraphML
* HTML reports
* documentation fragments
* indexes

These are artifacts too, but they are outputs or internal representations rather than source artifacts.