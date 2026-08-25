import importlib.util
import tempfile
import unittest
from pathlib import Path


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
