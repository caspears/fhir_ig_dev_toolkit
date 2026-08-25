## Running with Jira Integration (Checks for existing UP tickets)
To run working with Jira, Authentication will have to be established, 

Open browser Developer Tools
Open the Network tab.
Visit https://jira.hl7.org/rest/api/2/myself
Select the myself request.
Under Request Headers, copy the complete JSESSIONID Cookie value.
Set it in PowerShell:
    `$env:HL7_JIRA_COOKIE = "JSESSIONID={value}"`

THen run tool
```powershell
python tools/tho_assistant/tho_assistant.py analyze `                                                                                    
>>   tools/tho_assistant/tests/fixtures/formulary/CodeSystem-usdf-BenefitCostTypeCS-TEMPORARY-TRIAL-USE.json `
>>   --ig-dir C:/dev/fhir/ig/davinci/davinci-pdex-formulary/output `
>>   --search-proposals `
>>   --output-dir build/tho-analysis
```