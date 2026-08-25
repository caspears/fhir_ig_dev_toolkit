import importlib.util
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
