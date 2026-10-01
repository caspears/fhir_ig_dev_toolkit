import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "tho_assistant.py"
SPEC = importlib.util.spec_from_file_location("tho_assistant", MODULE_PATH)
assert SPEC and SPEC.loader
tho_assistant = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tho_assistant)


class AnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Path(__file__).parent / "fixtures" / "CodeSystem-example.json"
        self.formulary_fixture = (
            Path(__file__).parent
            / "fixtures"
            / "formulary"
            / "CodeSystem-usdf-BenefitCostTypeCS-TEMPORARY-TRIAL-USE.json"
        )
        self.proposal_fixture = (
            Path(__file__).parent / "fixtures" / "proposals" / "UP-814.json"
        )

    def test_analyzes_nested_concepts(self):
        resource = tho_assistant.load_resource(self.fixture)
        analysis = tho_assistant.analyze(resource, self.fixture)

        self.assertEqual(analysis["concept_count"], 3)
        self.assertEqual(analysis["concepts"][2]["code"], "verified")
        self.assertEqual(analysis["concepts"][2]["parent"], "completed")
        self.assertEqual(analysis["review_flags"], [])

    def test_matches_up_814_to_all_formulary_codes(self):
        resource = tho_assistant.load_resource(self.formulary_fixture)
        proposal = tho_assistant._load_json_or_fenced_json(self.proposal_fixture)
        analysis = tho_assistant.analyze(
            resource, self.formulary_fixture, [proposal]
        )

        self.assertEqual(len(analysis["proposal_matches"]), 1)
        match = analysis["proposal_matches"][0]
        self.assertEqual(match["key"], "UP-814")
        self.assertEqual(match["status"], "Consensus Review")
        self.assertEqual(match["coverage"], "full")
        self.assertEqual(match["matched_codes"], ["copay", "coinsurance"])
        self.assertIn(
            "http://terminology.hl7.org/CodeSystem/benefit-type",
            match["target_canonicals"],
        )

    def test_finds_valueset_usage(self):
        resource = tho_assistant.load_resource(self.formulary_fixture)
        usages = tho_assistant.find_valueset_usage(
            resource["url"], self.formulary_fixture.parent
        )

        self.assertEqual(len(usages), 1)
        self.assertEqual(usages[0]["id"], "BenefitCostTypeVS")
        self.assertEqual(usages[0]["inclusion"], "all-codes")
        self.assertEqual(usages[0]["other_code_systems"], [])
        self.assertEqual(len(usages[0]["sources"]), 1)

    def test_deduplicates_valueset_representations(self):
        resource = tho_assistant.load_resource(self.formulary_fixture)
        source = self.formulary_fixture.parent / "ValueSet-BenefitCostTypeVS.json"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "a"
            second = root / "b"
            first.mkdir()
            second.mkdir()
            text = source.read_text(encoding="utf-8")
            (first / source.name).write_text(text, encoding="utf-8")
            (second / source.name).write_text(text, encoding="utf-8")
            usages = tho_assistant.find_valueset_usage(resource["url"], root)

        self.assertEqual(len(usages), 1)
        self.assertEqual(len(usages[0]["sources"]), 2)

    def test_finds_profile_binding_to_discovered_valueset(self):
        resource = tho_assistant.load_resource(self.formulary_fixture)
        valueset_source = self.formulary_fixture.parent / "ValueSet-BenefitCostTypeVS.json"
        profile = {
            "resourceType": "StructureDefinition",
            "id": "TestInsurancePlan",
            "url": "http://example.org/StructureDefinition/TestInsurancePlan",
            "name": "TestInsurancePlan",
            "title": "Test InsurancePlan",
            "type": "InsurancePlan",
            "kind": "resource",
            "baseDefinition": "http://hl7.org/fhir/StructureDefinition/InsurancePlan",
            "differential": {
                "element": [
                    {
                        "id": "InsurancePlan.plan.specificCost.benefit.cost.type",
                        "path": "InsurancePlan.plan.specificCost.benefit.cost.type",
                        "binding": {
                            "strength": "extensible",
                            "valueSet": "http://hl7.org/fhir/us/davinci-drug-formulary/ValueSet/BenefitCostTypeVS|3.0.0-ballot",
                        },
                    }
                ]
            },
            "snapshot": {
                "element": [
                    {
                        "id": "InsurancePlan.plan.specificCost.benefit.cost.type",
                        "path": "InsurancePlan.plan.specificCost.benefit.cost.type",
                        "binding": {
                            "strength": "extensible",
                            "valueSet": "http://hl7.org/fhir/us/davinci-drug-formulary/ValueSet/BenefitCostTypeVS|3.0.0-ballot",
                        },
                    }
                ]
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / valueset_source.name).write_text(
                valueset_source.read_text(encoding="utf-8"), encoding="utf-8"
            )
            (root / "StructureDefinition-TestInsurancePlan.json").write_text(
                json.dumps(profile), encoding="utf-8"
            )
            usages = tho_assistant.find_valueset_usage(resource["url"], root)
            bindings = tho_assistant.find_structuredefinition_bindings(usages, root)

        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0]["type"], "InsurancePlan")
        self.assertEqual(
            bindings[0]["bindings"][0]["path"],
            "InsurancePlan.plan.specificCost.benefit.cost.type",
        )
        self.assertEqual(bindings[0]["bindings"][0]["strength"], "extensible")
        self.assertEqual(
            bindings[0]["bindings"][0]["sections"],
            ["differential", "snapshot"],
        )

    def test_compares_profile_binding_with_base_fhir_package(self):
        binding_context = [
            {
                "id": "TestInsurancePlan",
                "base_definition": "http://hl7.org/fhir/StructureDefinition/InsurancePlan",
                "bindings": [
                    {
                        "path": "InsurancePlan.plan.specificCost.benefit.cost.type",
                        "value_set": "http://example.org/ValueSet/local-benefit-type",
                        "strength": "extensible",
                        "sections": ["differential"],
                    }
                ],
            }
        ]
        base_structure = {
            "resourceType": "StructureDefinition",
            "id": "InsurancePlan",
            "url": "http://hl7.org/fhir/StructureDefinition/InsurancePlan",
            "snapshot": {
                "element": [
                    {
                        "path": "InsurancePlan.plan.specificCost.benefit.cost.type",
                        "binding": {
                            "strength": "example",
                            "valueSet": "http://hl7.org/fhir/ValueSet/benefit-type|4.0.1",
                        },
                    }
                ]
            },
        }
        base_valueset = {
            "resourceType": "ValueSet",
            "id": "benefit-type",
            "url": "http://hl7.org/fhir/ValueSet/benefit-type",
            "name": "BenefitTypeCodes",
            "compose": {
                "include": [
                    {"system": "http://terminology.hl7.org/CodeSystem/benefit-type"}
                ]
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "package"
            package.mkdir()
            (package / "StructureDefinition-InsurancePlan.json").write_text(
                json.dumps(base_structure), encoding="utf-8"
            )
            (package / "ValueSet-benefit-type.json").write_text(
                json.dumps(base_valueset), encoding="utf-8"
            )
            compared = tho_assistant.compare_base_fhir_bindings(
                binding_context, Path(directory)
            )

        base = compared[0]["bindings"][0]["base_fhir_comparison"]
        self.assertEqual(base["status"], "resolved")
        self.assertEqual(base["base_binding_strength"], "example")
        self.assertEqual(
            base["base_value_set"],
            "http://hl7.org/fhir/ValueSet/benefit-type|4.0.1",
        )
        self.assertEqual(
            base["base_value_set_details"]["code_systems"],
            ["http://terminology.hl7.org/CodeSystem/benefit-type"],
        )
        self.assertNotIn("base_fhir_comparison", binding_context[0]["bindings"][0])

    def test_base_fhir_comparison_reports_missing_structuredefinition(self):
        binding_context = [
            {
                "base_definition": "http://hl7.org/fhir/StructureDefinition/Missing",
                "bindings": [{"path": "Missing.code"}],
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            unrelated = {
                "resourceType": "StructureDefinition",
                "id": "Unrelated",
                "url": "http://hl7.org/fhir/StructureDefinition/Unrelated",
                "snapshot": {"element": [{"path": "Unrelated"}]},
            }
            (Path(directory) / "StructureDefinition-Unrelated.json").write_text(
                json.dumps(unrelated), encoding="utf-8"
            )
            compared = tho_assistant.compare_base_fhir_bindings(
                binding_context, Path(directory)
            )

        result = compared[0]["bindings"][0]["base_fhir_comparison"]
        self.assertEqual(result["status"], "base-structuredefinition-not-found")
        self.assertEqual(
            result["missing_structure_definition"],
            "http://hl7.org/fhir/StructureDefinition/Missing",
        )

    def test_base_fhir_comparison_rejects_empty_package_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(tho_assistant.AnalysisError) as caught:
                tho_assistant.compare_base_fhir_bindings([], Path(directory))

        self.assertIn("No StructureDefinition JSON files", str(caught.exception))

    def test_base_fhir_comparison_preserves_unbound_element_context(self):
        binding_context = [
            {
                "base_definition": "http://hl7.org/fhir/StructureDefinition/InsurancePlan",
                "bindings": [
                    {"path": "InsurancePlan.plan.specificCost.benefit.cost.type"}
                ],
            }
        ]
        base_structure = {
            "resourceType": "StructureDefinition",
            "id": "InsurancePlan",
            "url": "http://hl7.org/fhir/StructureDefinition/InsurancePlan",
            "baseDefinition": "http://hl7.org/fhir/StructureDefinition/DomainResource",
            "snapshot": {
                "element": [
                    {
                        "path": "InsurancePlan.plan.specificCost.benefit.cost.type",
                        "short": "Type of cost",
                        "definition": "Type of cost (copay; coinsurance; deductible).",
                        "type": [{"code": "CodeableConcept"}],
                    }
                ]
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "StructureDefinition-InsurancePlan.json").write_text(
                json.dumps(base_structure), encoding="utf-8"
            )
            compared = tho_assistant.compare_base_fhir_bindings(
                binding_context, Path(directory)
            )

        result = compared[0]["bindings"][0]["base_fhir_comparison"]
        self.assertEqual(result["status"], "base-element-unbound")
        self.assertEqual(result["base_element_short"], "Type of cost")
        self.assertEqual(result["base_element_types"], ["CodeableConcept"])
        self.assertEqual(
            result["base_structure_definition"],
            "http://hl7.org/fhir/StructureDefinition/InsurancePlan",
        )

    def test_filters_unrelated_jira_results_and_keeps_context(self):
        resource = tho_assistant.load_resource(self.formulary_fixture)
        payload = {
            "issues": [
                {
                    "key": "UP-UNRELATED",
                    "fields": {
                        "summary": "Device terminology",
                        "description": "http://terminology.hl7.org/CodeSystem/device-kind",
                        "status": {"name": "Draft"},
                    },
                },
                {
                    "key": "UP-CONTEXT",
                    "fields": {
                        "summary": "Update Coverage Copay Type Codes",
                        "description": "http://terminology.hl7.org/CodeSystem/coverage-copay-type",
                        "status": {"name": "Draft"},
                    },
                },
            ]
        }
        concepts = tho_assistant._flatten_concepts(resource["concept"])
        matches = tho_assistant.match_proposals(resource, concepts, [payload])

        self.assertEqual([match["key"] for match in matches], ["UP-CONTEXT"])
        self.assertEqual(matches[0]["coverage"], "contextual")
        self.assertEqual(matches[0]["matched_terms"], ["Copay"])

    def test_builds_contextual_jira_query(self):
        resource = tho_assistant.load_resource(self.formulary_fixture)
        usages = tho_assistant.find_valueset_usage(
            resource["url"], self.formulary_fixture.parent
        )
        jql = tho_assistant.build_proposal_jql(resource, usages)

        self.assertIn("project = UP", jql)
        self.assertIn('text ~ "copay"', jql)
        self.assertIn('text ~ "Benefit type of cost"', jql)

    def test_live_search_uses_bearer_token_without_returning_it(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"issues": []}'

        with mock.patch.object(
            tho_assistant.request, "urlopen", return_value=Response()
        ) as urlopen:
            result = tho_assistant.search_jira_proposals(
                "https://jira.example.test",
                "project = UP",
                token="secret-test-token",
            )

        sent_request = urlopen.call_args.args[0]
        query = tho_assistant.parse.parse_qs(
            tho_assistant.parse.urlsplit(sent_request.full_url).query
        )
        self.assertTrue(sent_request.full_url.startswith(
            "https://jira.example.test/rest/api/2/search?"
        ))
        self.assertEqual(sent_request.get_method(), "GET")
        self.assertEqual(
            sent_request.headers["Authorization"], "Bearer secret-test-token"
        )
        self.assertEqual(query["jql"], ["project = UP"])
        self.assertEqual(result, {"issues": []})
        self.assertNotIn("secret-test-token", json.dumps(result))

    def test_live_search_uses_browser_cookie_instead_of_pat(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"issues": []}'

        with mock.patch.object(
            tho_assistant.request, "urlopen", return_value=Response()
        ) as urlopen:
            result = tho_assistant.search_jira_proposals(
                "https://jira.hl7.org",
                "project = UP",
                token="unused-pat",
                cookie="JSESSIONID=secret-session",
            )

        sent_request = urlopen.call_args.args[0]
        self.assertEqual(
            sent_request.headers["Cookie"], "JSESSIONID=secret-session"
        )
        self.assertNotIn("Authorization", sent_request.headers)
        self.assertEqual(result, {"issues": []})
        self.assertNotIn("secret-session", json.dumps(result))

    def test_jira_preflight_uses_minimal_project_query(self):
        expected = {"total": 1, "issues": [{"key": "UP-814"}]}
        with mock.patch.object(
            tho_assistant, "search_jira_proposals", return_value=expected
        ) as search:
            result = tho_assistant.test_jira_access(
                "https://jira.hl7.org", None, "JSESSIONID=session"
            )

        search.assert_called_once_with(
            "https://jira.hl7.org",
            "project=UP",
            token=None,
            cookie="JSESSIONID=session",
        )
        self.assertEqual(result, expected)

    def test_unauthorized_jira_response_hides_html_and_explains_cookie(self):
        html = b"<html><head><title>Unauthorized (401)</title></head></html>"
        unauthorized = tho_assistant.error.HTTPError(
            "https://jira.hl7.org/rest/api/2/search",
            401,
            "Unauthorized",
            {},
            io.BytesIO(html),
        )
        with mock.patch.object(
            tho_assistant.request, "urlopen", side_effect=unauthorized
        ):
            with self.assertRaises(tho_assistant.AnalysisError) as caught:
                tho_assistant.search_jira_proposals(
                    "https://jira.hl7.org",
                    "project=UP",
                    cookie="JSESSIONID=expired",
                )

        message = str(caught.exception)
        self.assertIn("cookie may have expired", message)
        self.assertIn("HL7_JIRA_COOKIE", message)
        self.assertNotIn("<html>", message)

    def test_normalizes_bare_jira_session_id(self):
        self.assertEqual(
            tho_assistant.normalize_jira_cookie("secret-session-value"),
            "JSESSIONID=secret-session-value",
        )
        self.assertEqual(
            tho_assistant.normalize_jira_cookie("JSESSIONID=secret-session-value"),
            "JSESSIONID=secret-session-value",
        )
        self.assertEqual(
            tho_assistant.normalize_jira_cookie(
                "Cookie: JSESSIONID=secret-session-value; another=value"
            ),
            "JSESSIONID=secret-session-value; another=value",
        )

    def test_bad_request_hides_html_and_explains_cookie_format(self):
        html = b"<html><head><title>Bad Request (400)</title></head></html>"
        bad_request = tho_assistant.error.HTTPError(
            "https://jira.hl7.org/rest/api/2/search",
            400,
            "Bad Request",
            {},
            io.BytesIO(html),
        )
        with mock.patch.object(
            tho_assistant.request, "urlopen", side_effect=bad_request
        ):
            with self.assertRaises(tho_assistant.AnalysisError) as caught:
                tho_assistant.search_jira_proposals(
                    "https://jira.hl7.org",
                    "project=UP",
                    cookie="JSESSIONID=invalid",
                )

        message = str(caught.exception)
        self.assertIn("HTTP 400", message)
        self.assertIn("JSESSIONID", message)
        self.assertNotIn("<html>", message)

    def test_xml_input(self):
        xml = """<CodeSystem xmlns=\"http://hl7.org/fhir\">
          <id value=\"xml-example\"/>
          <url value=\"http://example.org/CodeSystem/xml-example\"/>
          <version value=\"0.1.0\"/>
          <name value=\"XmlExample\"/>
          <title value=\"XML Example\"/>
          <status value=\"active\"/>
          <description value=\"An XML test.\"/>
          <caseSensitive value=\"true\"/>
          <content value=\"complete\"/>
          <concept><code value=\"one\"/><display value=\"One\"/><definition value=\"The first concept.\"/></concept>
        </CodeSystem>"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "CodeSystem-example.xml"
            path.write_text(xml, encoding="utf-8")
            resource = tho_assistant.load_resource(path)
            analysis = tho_assistant.analyze(resource, path)

        self.assertEqual(resource["resourceType"], "CodeSystem")
        self.assertIs(resource["caseSensitive"], True)
        self.assertEqual(analysis["concept_count"], 1)
        self.assertEqual(analysis["review_flags"], [])


if __name__ == "__main__":
    unittest.main()
