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
    def test_review_guidance_tracks_effective_status(self):
        complete = " ".join(tho_assistant.review_next_steps({"confirmed": 2, "rejected": 2}))
        self.assertIn("Review complete", complete)
        self.assertNotIn("rerun", complete)
        self.assertIn("publication readiness", complete)
        pending = " ".join(tho_assistant.review_next_steps({"pending": 1}))
        self.assertIn("confirmed or rejected", pending)
        changed = " ".join(tho_assistant.review_next_steps({"requires-re-review": 1}))
        self.assertIn("saved baseline", changed)
        empty = " ".join(tho_assistant.review_next_steps({}))
        self.assertIn("No mapping decisions", empty)

    def test_manifest_draft_precedence_and_expired_snapshot(self):
        issue = {"key": "UP-814", "fields": {
            "customfield_13305": "| _New:_ | |\n| _Deleted:_ | input/sourceOfTruth/fhir/valueSets/ValueSet-old.xml |\n| _Modified:_ | [input/sourceOfTruth/fhir/codeSystems/CodeSystem-benefit-type.xml|https://example.test/input/sourceOfTruth/fhir/codeSystems/CodeSystem-benefit-type.xml]\n[input/utg.xml|https://example.test/input/utg.xml] |",
            "customfield_10426": "http://terminology.hl7.org/CodeSystem/benefit-type\ncoinsurance\n\tOld display\n\t\tOld definition."}}
        manifest = tho_assistant.draft_builds.parse_manifest(issue["fields"]["customfield_13305"])
        self.assertEqual(len(manifest), 2)
        self.assertEqual(manifest[0]["action"], "deleted")
        draft = {"resourceType": "CodeSystem", "id": "benefit-type", "url": "http://terminology.hl7.org/CodeSystem/benefit-type",
                 "concept": [{"code": "copay-percent", "display": "Percent / Coinsurance", "definition": "New definition."}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "CodeSystem-benefit-type.json"
            path.write_text(json.dumps(draft), encoding="utf-8")
            cache = Path(directory) / "cache"
            records = tho_assistant.draft_builds.retrieve_drafts(issue, cache, [path], allow_fetch=False)
            self.assertEqual(records[1]["status"], "local-file")
            self.assertEqual(records[0]["status"], "declared-deleted")
            result = tho_assistant.analyze({"url": "urn:local", "concept": [{"code": "coinsurance"}]}, Path("input.json"),
                                          [issue], draft_records={"UP-814": records})
            proposed = result["proposal_matches"][0]["proposed_concepts"][0]
            self.assertEqual(proposed["proposed_code"], "copay-percent")
            self.assertEqual(proposed["definition"], "New definition.")
            self.assertEqual(result["proposal_matches"][0]["jira_proposed_concepts"][0]["definition"], "Old definition.")
            with mock.patch.object(tho_assistant.draft_builds.request, "urlopen", side_effect=OSError("unavailable")):
                cached = tho_assistant.draft_builds.retrieve_drafts(issue, cache)
            self.assertEqual(cached[1]["status"], "cached-live-unavailable")

    def test_context_discovery_without_jira_supports_system_review(self):
        canonical = "http://terminology.hl7.org/CodeSystem/contactentity-type"
        resource = {"resourceType": "CodeSystem", "url": "urn:local", "concept": [
            {"code": "MARKETING", "display": "Marketing", "definition": "Plan marketing contact."}]}
        usages = [{"url": "urn:vs", "tho_code_systems": [canonical]}]
        bindings = [{"url": "urn:profile", "bindings": [{"path": "InsurancePlan.contact.purpose",
            "base_fhir_comparison": {"base_value_set": "urn:base-vs", "base_value_set_details": {"code_systems": [canonical]}}}]}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "CodeSystem-contactentity-type.json").write_text(json.dumps({
                "resourceType": "CodeSystem", "url": canonical, "concept": [{"code": "ADMIN"}]
            }), encoding="utf-8")
            result = tho_assistant.analyze(resource, Path("input.json"), [], usages, bindings, path)
            artifact = result["context_target_artifacts"][0]
            self.assertEqual(len(artifact["discovery_evidence"]), 2)
            self.assertEqual(artifact["concept_comparison"][0]["status"], "absent-code")
            review = tho_assistant.build_review_template(result)
            self.assertEqual(review["decisions"][0]["proposal"], None)
            review["decisions"][0]["decision"] = "confirmed"
            tho_assistant.apply_review_decisions(result, review)
            self.assertEqual(tho_assistant.build_recommendations(result)[0]["action"], "review-concepts-in-selected-system")
            self.assertIn("THO targets discovered from IG context", tho_assistant.render_markdown(result))
        self.assertIn(canonical, tho_assistant.build_proposal_jql(resource, usages, bindings))
        matches = tho_assistant.match_proposals(resource, resource["concept"], [{"key": "UP-context", "fields": {
            "summary": "Contact terminology", "description": canonical}}], usages, bindings)
        self.assertEqual(matches[0]["matched_context_canonicals"], [canonical])

    def test_automatic_review_lifecycle(self):
        analysis = {"metadata": {"url": "urn:source"}, "proposal_matches": [{
            "key": "UP-test", "tho_target_artifacts": [{"canonical": "urn:target",
            "concept_comparison": [{"code": "one", "target_code": "one", "proposed_definition": "Original"}]}]}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "review.json"
            self.assertEqual(tho_assistant.maintain_review_file(analysis, path), (True, 0))
            review = json.loads(path.read_text())
            review["decisions"][0].update(decision="confirmed", note="Reviewed")
            path.write_text(json.dumps(review))
            original = path.read_bytes()
            self.assertEqual(tho_assistant.maintain_review_file(analysis, path), (False, 0))
            self.assertEqual(path.read_bytes(), original)
            rows = analysis["proposal_matches"][0]["tho_target_artifacts"][0]["concept_comparison"]
            rows[0]["proposed_definition"] = "Changed"
            rows.append({"code": "two", "target_code": "two", "proposed_definition": "New"})
            self.assertEqual(tho_assistant.maintain_review_file(analysis, path), (False, 1))
            updated = json.loads(path.read_text())
            self.assertEqual(updated["decisions"][0], review["decisions"][0])
            self.assertEqual(updated["decisions"][1]["decision"], "pending")
            self.assertEqual(analysis["review_decisions"][0]["effective_status"], "requires-re-review")
            before = path.read_bytes()
            analysis["metadata"]["url"] = "urn:other"
            with self.assertRaises(tho_assistant.AnalysisError):
                tho_assistant.maintain_review_file(analysis, path)
            self.assertEqual(path.read_bytes(), before)

    def test_recommendations_require_current_confirmation(self):
        analysis = {"concepts": [{"code": "coinsurance"}], "review_decisions": []}
        self.assertEqual(tho_assistant.build_recommendations(analysis)[0]["action"], "review-candidates")
        decision = {"source_code": "coinsurance", "target_code": "copay-percent",
                    "target_system": "urn:benefit", "proposal": "UP-test", "effective_status": "confirmed"}
        analysis["review_decisions"] = [decision]
        result = tho_assistant.build_recommendations(analysis)[0]
        self.assertEqual(result["action"], "coordinate-with-existing-proposal")
        self.assertEqual(result["publication_readiness"], "not-established")
        decision["effective_status"] = "requires-re-review"
        self.assertEqual(tho_assistant.build_recommendations(analysis)[0]["action"], "re-review-evidence")
        decision["effective_status"] = "rejected"
        self.assertEqual(tho_assistant.build_recommendations(analysis)[0]["action"], "review-candidates")
        decision["effective_status"] = "confirmed"
        analysis["review_decisions"].append({**decision, "target_system": "urn:other"})
        self.assertEqual(tho_assistant.build_recommendations(analysis)[0]["action"], "resolve-conflicting-decisions")

    def test_review_decisions_expire_on_changed_evidence(self):
        analysis = {"metadata": {"url": "urn:source"}, "proposal_matches": [{
            "key": "UP-test", "tho_target_artifacts": [{"canonical": "urn:target",
            "concept_comparison": [{"code": "source", "target_code": "target",
                                    "proposed_definition": "Meaning"}]}]}]}
        review = tho_assistant.build_review_template(analysis)
        self.assertEqual(review["decisions"][0]["decision"], "pending")
        review["decisions"][0]["decision"] = "confirmed"
        tho_assistant.apply_review_decisions(analysis, review)
        self.assertEqual(analysis["review_decisions"][0]["effective_status"], "confirmed")
        analysis["proposal_matches"][0]["tho_target_artifacts"][0]["concept_comparison"][0]["proposed_definition"] = "Changed"
        tho_assistant.apply_review_decisions(analysis, review)
        self.assertEqual(analysis["review_decisions"][0]["effective_status"], "requires-re-review")
        analysis["proposal_matches"] = []
        tho_assistant.apply_review_decisions(analysis, review)
        self.assertEqual(analysis["review_decisions"][0]["effective_status"], "evidence-unavailable")

    def test_alternate_code_mapping_requires_review(self):
        fields = {"customfield_10426": "copay-percent\n\tCopayment Percent / Coinsurance\n\t\tPercentage cost sharing, also referred to as coinsurance."}
        row = tho_assistant.map_proposed_concepts(fields, ["coinsurance"])[0]
        self.assertEqual(row["proposed_code"], "copay-percent")
        self.assertEqual(row["mapping_status"], "requires-review")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "CodeSystem-test.json").write_text(json.dumps({
                "resourceType": "CodeSystem", "url": "http://example.org/CodeSystem/test",
                "concept": [{"code": "copay-percent", "display": "Percent", "definition": "A percentage."}]
            }), encoding="utf-8")
            result = tho_assistant.compare_tho_target_artifacts(
                [{"target_canonicals": ["http://example.org/CodeSystem/test"], "proposed_concepts": [row]}],
                [{"code": "coinsurance", "display": "Coinsurance"}], path)
        comparison = result[0]["tho_target_artifacts"][0]["concept_comparison"][0]
        self.assertEqual(comparison["status"], "existing-code")
        self.assertEqual(comparison["target_code"], "copay-percent")
        fields["description"] = "another\n\tCoinsurance\n\t\tAnother meaning."
        self.assertEqual(tho_assistant.map_proposed_concepts(fields, ["coinsurance"])[0]["status"], "ambiguous")

    def test_extracts_explicit_proposal_blocks_only(self):
        fields = {"customfield_10426": "copay\n\tCopayment\n\t\tA fixed amount.\n\ncoinsurance mentioned only"}
        rows = tho_assistant.extract_proposed_concepts(fields, ["copay", "coinsurance"])
        self.assertEqual(rows[0]["definition"], "A fixed amount.")
        self.assertEqual(rows[0]["evidence"][0]["source_field"], "customfield_10426")
        self.assertEqual(rows[1]["status"], "not-extracted")
        fields["description"] = "copay\n\tDifferent\n\t\tAnother definition."
        self.assertEqual(tho_assistant.extract_proposed_concepts(fields, ["copay"])[0]["status"], "ambiguous")

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
        self.assertEqual(match["code_coverage"], "full")
        self.assertEqual(match["matched_codes"], ["copay", "coinsurance"])
        self.assertIn(
            "http://terminology.hl7.org/CodeSystem/benefit-type",
            match["target_canonicals"],
        )

    def test_ranks_benefit_proposal_above_adjudication_code_overlap(self):
        resource = tho_assistant.load_resource(self.formulary_fixture)
        up_814 = tho_assistant._load_json_or_fenced_json(self.proposal_fixture)
        up_819 = {
            "key": "UP-819",
            "fields": {
                "summary": "Add copay and coinsurance to adjudication codes",
                "description": (
                    "Targets http://terminology.hl7.org/CodeSystem/adjudication "
                    "and http://terminology.hl7.org/ValueSet/adjudication."
                ),
                "status": {"name": "Proposal Draft"},
            },
        }
        usages = tho_assistant.find_valueset_usage(
            resource["url"], self.formulary_fixture.parent
        )
        bindings = [
            {
                "bindings": [
                    {
                        "path": "InsurancePlan.plan.specificCost.benefit.cost.type",
                        "base_fhir_comparison": {
                            "base_element_short": "Type of cost",
                            "base_element_definition": (
                                "Type of cost (copay; coinsurance; deductible)."
                            ),
                        },
                    }
                ]
            }
        ]
        concepts = tho_assistant._flatten_concepts(resource["concept"])
        matches = tho_assistant.match_proposals(
            resource, concepts, [up_814, up_819], usages, bindings
        )

        self.assertEqual([match["key"] for match in matches], ["UP-814", "UP-819"])
        self.assertEqual(matches[0]["code_coverage"], "full")
        self.assertEqual(matches[0]["context_alignment"], "high")
        self.assertEqual(matches[0]["assessment"], "strong-existing-proposal-match")
        ig_binding = next(
            item
            for item in matches[0]["context_basis"]
            if item["source"] == "IG ValueSet binding"
        )
        self.assertEqual(
            ig_binding["details"]["element"],
            "InsurancePlan.plan.specificCost.benefit.cost.type",
        )
        self.assertEqual(ig_binding["details"]["strength"], None)
        self.assertEqual(ig_binding["matched_terms"], ["benefit"])
        self.assertEqual(ig_binding["contribution"], 3)
        self.assertEqual(matches[1]["code_coverage"], "full")
        self.assertEqual(matches[1]["context_alignment"], "low")
        self.assertEqual(matches[1]["assessment"], "code-overlap-different-context")

        analysis = tho_assistant.analyze(
            resource, self.formulary_fixture, [up_814, up_819], usages, bindings
        )
        markdown = tho_assistant.render_markdown(analysis)
        self.assertIn("### Context-alignment evidence", markdown)
        self.assertIn("IG ValueSet binding: matched=benefit", markdown)
        self.assertIn(
            "element=InsurancePlan.plan.specificCost.benefit.cost.type", markdown
        )

    def test_compares_candidate_concepts_with_installed_tho_targets(self):
        resource = tho_assistant.load_resource(self.formulary_fixture)
        concepts = tho_assistant._flatten_concepts(resource["concept"])
        proposals = [
            {
                "key": "UP-814",
                "target_canonicals": [
                    "http://terminology.hl7.org/CodeSystem/benefit-type",
                    "http://terminology.hl7.org/ValueSet/benefit-type",
                ],
            }
        ]
        code_system = {
            "resourceType": "CodeSystem",
            "id": "benefit-type",
            "url": "http://terminology.hl7.org/CodeSystem/benefit-type",
            "version": "test",
            "concept": [
                {
                    "code": "copay",
                    "display": "Copayment",
                    "definition": "A fixed member cost.",
                }
            ],
        }
        value_set = {
            "resourceType": "ValueSet",
            "id": "benefit-type",
            "url": "http://terminology.hl7.org/ValueSet/benefit-type",
            "compose": {
                "include": [
                    {"system": "http://terminology.hl7.org/CodeSystem/benefit-type"}
                ]
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "package"
            package.mkdir()
            (package / "CodeSystem-benefit-type.json").write_text(
                json.dumps(code_system), encoding="utf-8"
            )
            (package / "ValueSet-benefit-type.json").write_text(
                json.dumps(value_set), encoding="utf-8"
            )
            compared = tho_assistant.compare_tho_target_artifacts(
                proposals, concepts, Path(directory)
            )

        artifacts = compared[0]["tho_target_artifacts"]
        code_result = next(
            item for item in artifacts if item["resource_type"] == "CodeSystem"
        )
        value_set_result = next(
            item for item in artifacts if item["resource_type"] == "ValueSet"
        )
        by_code = {
            item["code"]: item for item in code_result["concept_comparison"]
        }
        self.assertEqual(by_code["copay"]["status"], "existing-code")
        self.assertEqual(by_code["copay"]["display_comparison"], "different")
        self.assertEqual(by_code["coinsurance"]["status"], "absent-code")
        self.assertEqual(
            value_set_result["included_code_systems"],
            ["http://terminology.hl7.org/CodeSystem/benefit-type"],
        )

    def test_resolves_latest_installed_unversioned_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for version in ("7.3.0", "7.4.0-ballot", "7.4.0"):
                package = root / f"hl7.terminology.r4#{version}" / "package"
                package.mkdir(parents=True)
                (package / "package.json").write_text(
                    json.dumps(
                        {
                            "name": "hl7.terminology.r4",
                            "version": version,
                        }
                    ),
                    encoding="utf-8",
                )
            selected, version = tho_assistant.resolve_latest_package_dir(
                root / "hl7.terminology.r4"
            )

        self.assertEqual(selected.name, "hl7.terminology.r4#7.4.0")
        self.assertEqual(version, "7.4.0")

    def test_explicit_versioned_package_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            requested = root / "hl7.terminology.r4#7.3.0"
            requested.mkdir()
            (root / "hl7.terminology.r4#7.4.0").mkdir()
            selected, version = tho_assistant.resolve_latest_package_dir(requested)

        self.assertEqual(selected.name, "hl7.terminology.r4#7.3.0")
        self.assertEqual(version, "7.3.0")

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
        self.assertIn("does not establish", message)
        self.assertNotIn("<html>", message)

    def test_cookie_input_normalization_and_rejection(self):
        for value in ("example", "JSESSIONID=example", '"JSESSIONID=example"',
                      "Cookie: JSESSIONID = example"):
            self.assertEqual(tho_assistant.normalize_jira_cookie(value), "JSESSIONID=example")
        for value in ("JSESSIONID=", "JSESSION=example", "JSESSIONID=secret\u200b",
                      "JSESSIONID=secret\n", "JSESSIONID=sec ret"):
            with self.assertRaises(tho_assistant.AnalysisError) as caught:
                tho_assistant.normalize_jira_cookie(value)
            self.assertNotIn("secret", str(caught.exception))

    def test_json_400_does_not_blame_cookie_or_echo_server_data(self):
        failure = tho_assistant.error.HTTPError("https://jira.hl7.org", 400, "Bad Request", {},
            io.BytesIO(b'{"errorMessages":["secret-session query error"]}'))
        with mock.patch.object(tho_assistant.request, "urlopen", side_effect=failure):
            with self.assertRaises(tho_assistant.AnalysisError) as caught:
                tho_assistant.search_jira_proposals("https://jira.hl7.org", "project=UP", cookie="example")
        self.assertIn("query or request-validation", str(caught.exception))
        self.assertNotIn("secret-session", str(caught.exception))

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
