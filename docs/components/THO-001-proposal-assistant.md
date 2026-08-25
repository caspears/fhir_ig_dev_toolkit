# THO-001 - THO Proposal Assistant

## Purpose

Reduce the manual effort required to assess IG-owned terminology and prepare a
draft HL7 Terminology (THO) change proposal.

## Scope

The MVP supports a local, author-driven workflow for one CodeSystem and its
related ValueSet content. It is designed for personal use and possibly a small
number of close collaborators, rather than as a hosted or enterprise platform.

## Phase

Phase 4 - Terminology Analysis

## Priority

High

## Maturity

Prototype

## Status

In progress

## MVP workflow

1. Load a candidate CodeSystem from FHIR JSON or XML.
2. Extract metadata, concepts, hierarchy, designations, and properties.
3. Produce machine-readable analysis and a Markdown concept inventory.
4. Compare the candidate with potentially related THO artifacts.
5. Capture proposal decisions in a QuestionnaireResponse.
6. Evaluate focused readiness and requirement rules.
7. Generate draft CodeSystem and ValueSet resources with StructureMap.
8. Generate draft Jira proposal text and an author checklist.

## Design constraints

- Prefer standard-library or already-available dependencies.
- Keep the command-line workflow usable without hosted infrastructure.
- Do not automate semantic or governance decisions that require human review.
- Do not commit, push, or submit proposed changes without an explicit action.
- Keep CQL and StructureMap use proportional to the small MVP.

## Dependencies

- CORE-001 Artifact Loader concepts, without requiring its full implementation.
- A FHIR validator or publisher for later output validation.
- A StructureMap execution environment for the generation stage.

## Next action

Run the initial analyzer against a representative IG-owned CodeSystem and refine
the normalized analysis model from that concrete example.
