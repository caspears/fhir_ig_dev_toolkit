# THO Proposal Assistant

## Context-derived THO discovery

With `--tho-package-dir`, THO CodeSystems discovered via ValueSet co-inclusion
or resolved base bindings are inspected even without Jira matches. Discovery
evidence, artifact version, and exact-code comparisons appear in JSON and Markdown.
These canonical URLs also enter the Jira search, and canonical-only related
results are retained. Inspection currently uses exact-code lookup; absence does
not exclude synonyms or equivalent concepts under different identifiers.

Automatic review files now add `target-system-suitability` decisions with a null
proposal. Confirming one selects a system for investigation, not a code mapping.
The target_code is the identifier used for lookup. Recommendations request concept
review in the selected system; they do not infer a need for a new code. Existing
proposal-mapping decisions remain compatible. New entries append to review files.

## Planned spelling review

Implement spelling checks in the backend so CLI, JSON/Markdown, and a future UI
share the same findings. Check code, display, and definition. Tokenize camelCase,
PascalCase, kebab-case, snake_case, and acronym boundaries in identifiers; retain
the original code and token locations. Support domain dictionaries and reviewed
exceptions for medical terms, abbreviations, and terminology identifiers. The UI
will show suggestions and allow accept/dismiss decisions. Never automatically
rename codes, especially published identifiers. Spelling review is planned,
not implemented in this increment.

## Recommendations and next milestones

Every CLI analysis now includes recommended next actions in JSON and Markdown.
Current confirmed review decisions support coordination with their existing
proposals. Changed or unavailable evidence requires re-review; competing confirmed
targets require resolution. Unreviewed or rejected mappings do not support a
reuse recommendation. No-match results never automatically recommend a new code.
Publication readiness remains a separate check against the intended THO release.

Next milestones: pilot a second CodeSystem, then add a small local browser UI
for selecting inputs, reviewing evidence, confirming/rejecting mappings, and
viewing recommendations. JSON remains the persistence format, not the intended
end-user editing experience. Reviewed proposal/questionnaire generation follows.

## Saved review decisions

Every analysis automatically creates or reuses `review-decisions.json` inside
`--output-dir`. Read the report, change applicable `pending` decisions to
`confirmed` or `rejected`, optionally add a note, save, and rerun the same command.
Keep identities and `reviewed_evidence` unchanged during initial confirmation.
New mappings are appended as pending; old decisions, notes, and reviewed baselines
are retained. A backup is created before appending to an existing file. If no new
mappings are present, the review file is not rewritten.

Changed candidate/proposed text triggers `requires-re-review`; missing evidence
produces `evidence-unavailable`. Compare changed evidence with the saved baseline
before deliberately updating that baseline and reconfirming. Jira status alone
does not invalidate mappings. Use a separate output directory per CodeSystem.
Do not clear the review file between runs; back it up if build output is cleaned.

`--review-file` remains an optional location override. To migrate an existing
file stored elsewhere, retain the override or move the file into the output
directory as `review-decisions.json`. The tool does not search other directories.
`--write-review-template` is deprecated and acts as a custom review-path alias.
Review entries currently require extracted proposal text and THO comparisons;
an empty file means no reviewable mappings were extracted yet.

Cookie input accepts a bare session value, `JSESSIONID=value`, or a complete
Cookie header. Matching outer straight quotes and spaces around `=` are
normalized. Control/non-ASCII characters and invalid cookie syntax are rejected
locally without echoing credentials. HTTP 400 diagnostics distinguish structured
Jira validation errors, explicit malformed-cookie responses, and unknown request
failures; they do not assume expiration or print server response bodies.

The extractor retains all supported proposal rows. When a candidate code has no
exact row, a whole-term mention in another row's display identifies an alternate
code candidate. Unique alternatives are compared with that target code in THO
and marked `requires-review`; multiple alternatives remain ambiguous. No mapping
is automatically confirmed. `code_mention_coverage` explicitly describes ticket
mentions; the older `code_coverage` field remains for compatibility.

Three-way comparison now includes explicit proposed concept rows from Jira
`description` or HL7's `customfield_10426` proposal field. This initial extractor
supports unindented code lines followed by tab-indented displays and double-tab
indented definitions, as seen in the supplied UP-814 export. Other formats are
reported as `not-extracted`; conflicting rows are `ambiguous`. Source field,
line, and excerpt are retained in JSON, with field and line references in Markdown.
Changes are inferred against the installed package, not asserted as Jira actions.
This does not establish semantic equivalence or authorize a new proposal.

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

Proposal matching identifies exact code mentions and target HL7
CodeSystem/ValueSet canonicals. Code coverage is reported separately from
context alignment. Context alignment uses explainable token overlap between the
proposal targets and the discovered local ValueSet, profile element path, base
element definition, and candidate metadata. It does not mean that definitions
are semantically equivalent. The JSON and Markdown outputs show every context
source, the exact IG binding, matched terms, weight, score contribution, and
non-matching evidence. Binding strength is displayed but does not currently
change the score.

To inspect the actual CodeSystems and ValueSets targeted by matching proposals,
provide an installed THO package:

```bash
python tools/tho_assistant/tho_assistant.py analyze path/to/CodeSystem.json \
  --ig-dir path/to/ig \
  --tho-package-dir ~/.fhir/packages/hl7.terminology.r4#6.5.0 \
  --search-proposals \
  --output-dir build/tho-analysis
```

The report identifies whether each target artifact exists in that package,
whether candidate codes already exist, display and definition differences, and
the CodeSystems included by target ValueSets. The package is a snapshot; an
open proposal may describe changes that are not present in the installed
version.

An unversioned package-family path automatically selects the highest installed
semantic version and prints the resolved version and directory:

```bash
--tho-package-dir ~/.fhir/packages/hl7.terminology.r4
```

For example, this may resolve to
`~/.fhir/packages/hl7.terminology.r4#7.4.0`. Supplying an explicit versioned
directory continues to use that exact package.

To scan an IG directory for ValueSets that directly include the candidate:

```bash
python tools/tho_assistant/tho_assistant.py analyze path/to/CodeSystem.json \
  --ig-dir path/to/ig \
  --output-dir build/tho-analysis
```

The scanner prefers `fsh-generated/resources`, then top-level `output`
artifacts, and uses a recursive fallback only when neither standard location is
available. It reads only `ValueSet-*` JSON/XML files and consolidates multiple
representations by canonical URL.

It also scans generated `StructureDefinition-*` resources for bindings to the
discovered local ValueSets. The report identifies the profile, resource type,
element path, binding strength, differential/snapshot provenance, and base
definition.

To compare those bindings with the corresponding elements in an extracted base
FHIR package, add `--fhir-package-dir`. The path may identify either the package
root or its inner `package` directory:

```bash
python tools/tho_assistant/tho_assistant.py analyze path/to/CodeSystem.json \
  --ig-dir path/to/ig \
  --fhir-package-dir path/to/hl7.fhir.r4.core \
  --output-dir build/tho-analysis
```

The comparison follows the base-definition chain, reports the inherited base
binding and strength, and summarizes the CodeSystems included by the base
ValueSet when that ValueSet is present in the supplied package. It does not
download packages automatically. Direct `.fsh` source parsing is not yet
included.

When the corresponding base element exists without a binding, the result is
`base-element-unbound`. Its short description, definition, and datatype are
retained as terminology-matching context rather than continuing to an unrelated
ancestor element.

User-home paths such as `~/.fhir/packages/hl7.fhir.r4.core#4.0.1` are expanded
by the tool. An invalid or empty package path fails before analysis with a
specific package-directory message.

HL7's AWS front end currently rejects PAT-only REST requests, while REST calls
made with an authenticated browser session work. For proposal discovery, copy
the `Cookie` request-header value from a signed-in Jira REST request into the
`HL7_JIRA_COOKIE` environment variable and add `--search-proposals`. The cookie
is held only in memory and is not written to reports. Do not commit it or pass
it as a command-line argument.

For example, in the browser developer tools, reload
`https://jira.hl7.org/rest/api/2/myself`, select the request in the Network tab,
and copy the `JSESSIONID` cookie value. Then, in PowerShell, either the bare
value or the complete cookie form may be used:

```powershell
$env:HL7_JIRA_COOKIE = "JSESSIONID=your-session-value"
```

If the environment variable is not set, the secure prompt likewise accepts
either the JSESSIONID value alone or `JSESSIONID=your-session-value`.

Test Jira access before running the local IG scan:

```powershell
python tools/tho_assistant/tho_assistant.py test-jira
```

This performs only the same minimal `project=UP` Jira search that can be tested
in the browser. The `analyze --search-proposals` command also runs this
preflight before scanning `--ig-dir`, so authentication or project-access
failures return immediately.

```bash
python tools/tho_assistant/tho_assistant.py analyze path/to/CodeSystem.json \
  --ig-dir path/to/ig \
  --search-proposals \
  --output-dir build/tho-analysis
```

`HL7_JIRA_PAT` remains supported for Jira deployments whose front end permits
Bearer authentication. If both variables are present, the browser cookie is
used. Browser sessions expire, so the cookie may need to be refreshed. An HTTP
401 produces a concise instruction to verify or update `HL7_JIRA_COOKIE`; the
Jira HTML login response is not printed.

## Current limitations

- Only CodeSystem resources are accepted.
- THO artifact matching is not yet implemented.
- Jira results are ranked as full-code, partial-code, artifact, or contextual
  matches. Unrelated results returned by Jira's text search are omitted.
- Semantic comparison of code definitions is not yet implemented.
- Base FHIR comparison requires an explicitly supplied extracted package and
  does not yet validate that its FHIR version matches the IG.
- Questionnaire, CQL, and StructureMap artifacts will be added after the
  normalized analysis is tested against a real candidate.
- The generated review flags are prompts for human review, not governance
  conclusions.
