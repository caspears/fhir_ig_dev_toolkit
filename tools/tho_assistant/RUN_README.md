## Running with Jira Integration (Checks for existing UP tickets)
To run working with Jira, Authentication will have to be established, 

Open browser Developer Tools
Open the Network tab.
Visit https://jira.hl7.org/rest/api/2/myself (If the session is expired, may need a refresh. If the token doesn't change, may need to run in a new incognito tab)
Select the myself request.
Under Request Headers, copy the complete JSESSIONID Cookie value (including "JSESSION="). If that doesn't work, try without JSESSIONID=
Set it in PowerShell:
    `$env:HL7_JIRA_COOKIE = "JSESSIONID={value}"`

Then run tool
Use a different output directory (or clear out the current one) if you want to start fresh.
```shell
python tools/tho_assistant/tho_assistant.py analyze `                                                                                    
   tools/tho_assistant/tests/fixtures/formulary/CodeSystem-usdf-BenefitCostTypeCS-TEMPORARY-TRIAL-USE.json `
   --ig-dir C:/dev/fhir/ig/davinci/davinci-pdex-formulary/output `
   --search-proposals `
   --output-dir build/tho-analysis/BenefitCostTypeCS
```


only test the Jira Connection
```shell
python tools/tho_assistant/tho_assistant.py test-jira
```



To enable FHIR base comparison
Functionality:
* Accept an extracted FHIR core package using --fhir-package-dir.
* Follow a profile’s baseDefinition chain.
* Find the corresponding base element.
* Report the base binding strength and ValueSet.
* Inspect the base ValueSet and list its CodeSystems.
* Report explicit statuses when the StructureDefinition, element, binding, or ValueSet cannot be resolved.
* Preserve the original binding context without mutating it.

```shell
python tools/tho_assistant/tho_assistant.py analyze `
  tools/tho_assistant/tests/fixtures/formulary/CodeSystem-usdf-BenefitCostTypeCS-TEMPORARY-TRIAL-USE.json `
  --ig-dir C:/dev/fhir/ig/davinci/davinci-pdex-formulary/output `
  --fhir-package-dir ~/.fhir/packages/hl7.fhir.r4.core#4.0.1 `
  --search-proposals `
  --output-dir build/tho-analysis/BenefitCostTypeCS
```



Using a THO package
```shell
python tools/tho_assistant/tho_assistant.py analyze `
  tools/tho_assistant/tests/fixtures/formulary/CodeSystem-usdf-BenefitCostTypeCS-TEMPORARY-TRIAL-USE.json `
  --ig-dir C:/dev/fhir/ig/davinci/davinci-pdex-formulary/output `
  --fhir-package-dir ~/.fhir/packages/hl7.fhir.r4.core#4.0.1 `
  --tho-package-dir ~/.fhir/packages/hl7.terminology.r4 `
  --search-proposals `
  --output-dir build/tho-analysis/BenefitCostTypeCS
```


With added review template 


```shell
python tools/tho_assistant/tho_assistant.py analyze `
  tools/tho_assistant/tests/fixtures/formulary/CodeSystem-usdf-BenefitCostTypeCS-TEMPORARY-TRIAL-USE.json `
  --ig-dir C:/dev/fhir/ig/davinci/davinci-pdex-formulary/output `
  --fhir-package-dir ~/.fhir/packages/hl7.fhir.r4.core#4.0.1 `
  --tho-package-dir ~/.fhir/packages/hl7.terminology.r4 `
  --search-proposals `
  --fetch-drafts `
  --output-dir build/tho-analysis/BenefitCostTypeCS
```
After updating the review file, run again the same way.
