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

HL7's AWS front end currently rejects PAT-only REST requests, while REST calls
made with an authenticated browser session work. For proposal discovery, copy
the `Cookie` request-header value from a signed-in Jira REST request into the
`HL7_JIRA_COOKIE` environment variable and add `--search-proposals`. The cookie
is held only in memory and is not written to reports. Do not commit it or pass
it as a command-line argument.

For example, in the browser developer tools, reload
`https://jira.hl7.org/rest/api/2/myself`, select the request in the Network tab,
and copy only its `Cookie` request-header value. Then, in PowerShell:

```powershell
$env:HL7_JIRA_COOKIE = "cookie-name=cookie-value; another-name=another-value"
```

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
used. Browser sessions expire, so the cookie may need to be refreshed.

## Current limitations

- Only CodeSystem resources are accepted.
- THO artifact matching is not yet implemented.
- Jira results are ranked as full-code, partial-code, artifact, or contextual
  matches. Unrelated results returned by Jira's text search are omitted.
- Semantic comparison of code definitions is not yet implemented.
- Questionnaire, CQL, and StructureMap artifacts will be added after the
  normalized analysis is tested against a real candidate.
- The generated review flags are prompts for human review, not governance
  conclusions.
