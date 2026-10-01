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
