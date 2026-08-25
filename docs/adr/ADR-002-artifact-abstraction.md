ADR-002 – Artifact Abstraction
Artifact is broader than FHIR Resource


## Status
Completed

## Context
The FHIR IG Development Toolkit began by analyzing FHIR example resources and generating relationship diagrams. As the toolkit vision expanded, it became clear that many capabilities extend beyond the analysis of individual FHIR resources.

Implementation Guide development involves numerous artifacts in addition to exchange resources, including conformance resources (such as StructureDefinition, ValueSet, and CodeSystem), authoring artifacts (such as FSH and CQL), implementation assets (such as npm packages and SQL on FHIR ViewDefinitions), workflow and requirements resources (such as ExampleScenario, ActorDefinition, and Requirements), and toolkit-generated artifacts (such as relationship graphs, documentation, and visualization files).

Future capabilities are also expected to support emerging standards artifacts, incubator resources, templates, and additional specification assets as they evolve.

Designing the toolkit around the concept of a "FHIR resource" would unnecessarily constrain future capabilities and require architectural changes as additional artifact types are introduced.

A more general abstraction is needed that allows all supported objects to participate consistently in loading, indexing, relationship discovery, analysis, validation, documentation, visualization, and generation.


## Decision
The toolkit shall use Artifact as the primary abstraction representing any discrete object that participates in the authoring, analysis, validation, visualization, documentation, publication, maturation, or maintenance of a FHIR Implementation Guide or related interoperability specification.
This includes:

* FHIR resources
* FHIR datatypes, when represented/analyzed structurally
* FSH
* CQL
* npm packages
* package metadata
* logical models
* SQL on FHIR ViewDefinition
* templates
* workflow definitions
* incubator or emerging artifacts
* generated/internal artifacts such as graph JSON, PlantUML, Mermaid, reports, and indexes

## Consequences


### CORE-001 implication

CORE-001 – Artifact Loader should not be limited to FHIR resources. Its responsibility is to load artifacts from one or more artifact sources and normalize basic metadata.

Initial artifact types are expected to include:

* local resource folders
* input/fsh
* fsh-generated/resources
* npm package folders
* downloaded IG packages

Future artifact sources:

* ZIP archives
* Git repositories
* FHIR servers
* package registries
* external workflow/template repositories
* Stable terminology


Use these terms consistently:

| Term            | Meaning                                                  |
| --------------- | -------------------------------------------------------- |
| Artifact        | Any analyzable object in the toolkit                     |
| FHIR Resource   | A FHIR resource artifact                                 |
| Artifact Source | A location artifacts are loaded from                     |
| Artifact Index  | Catalog of known artifacts                               |
| Relationship    | A discovered connection between artifacts                |
| Graph           | Normalized representation of artifacts and relationships |
| Renderer        | Output generator that consumes graph/model data          |




### 1. Artifact Extensibility (I would add this)

One of the reasons we're making this decision is so we don't have to revisit it every time a new FHIR artifact or related specification appears.

Something like:

The toolkit architecture shall support the addition of new artifact types without requiring changes to the overall architectural model. New artifact types should be incorporated by extending artifact loading, indexing, and relationship discovery capabilities rather than by introducing new architectural abstractions.

That captures a principle we've talked about several times.

### 2. Artifact Identity (I would add this)

I think this will become important later.

An artifact should have an identity regardless of where it came from.

For example:

StructureDefinition/us-core-patient

input/fsh/PatientExample.fsh

package hl7.fhir.us.core#8.0.0

ExampleScenario/example-pa-workflow

Requirements/prior-auth-business-rules

The loader knows where it came from.

The rest of the toolkit should primarily care what it is.

We don't need to define the identity model today, but I'd like to capture the principle.

Perhaps:

The toolkit shall distinguish an artifact's identity from its physical source. Artifact loading is responsible for recording provenance while downstream components operate on normalized artifact identities.

That will become very important when we start looking at packages, multiple IGs, and version comparisons.

### 3. Keep the Initial Artifact List Informative, Not Restrictive

One small wording suggestion.

Instead of saying:

Initial supported artifacts...

