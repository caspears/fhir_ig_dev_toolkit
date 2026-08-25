# THO Proposal Assistant

This directory contains a small local MVP for preparing IG-owned terminology
for an HL7 Terminology (THO) proposal.

The first implemented command analyzes a FHIR CodeSystem in JSON or XML and
writes:

- `analysis.json` - normalized metadata and concept information.
- `concept-inventory.md` - a human-readable concept table and review summary.

## Run

```bash
python tools/tho_assistant/tho_assistant.py analyze path/to/CodeSystem.json \
  --output-dir build/tho-analysis
```

XML input is also supported. The command uses only the Python standard library.

To compare the candidate codes with previously exported Jira proposals, provide
one or more Jira issue or search-response JSON files:

```bash
python tools/tho_assistant/tho_assistant.py analyze path/to/CodeSystem.json \
  --proposal-file path/to/UP-814.json \
  --output-dir build/tho-analysis
```

Proposal matching currently identifies exact code mentions and target HL7
CodeSystem/ValueSet canonicals. A full match means all candidate codes were
mentioned in the proposal; it does not mean their definitions are identical.

## Current limitations

- Only CodeSystem resources are accepted.
- THO artifact matching is not yet implemented.
- Jira proposal data must currently be supplied as exported JSON; live Jira
  search is not yet implemented.
- Questionnaire, CQL, and StructureMap artifacts will be added after the
  normalized analysis is tested against a real candidate.
- The generated review flags are prompts for human review, not governance
  conclusions.
