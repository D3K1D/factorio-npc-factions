"""Offline rejection reports preserve model output and expose fixed text only."""

import copy
import unittest
from unittest.mock import patch

from designer_llm._reference_contract import ContractError
from designer_llm.diagnostics import rejection_validation, validation_message
from tests import test_workflow


class DiagnosticTests(unittest.TestCase):
    def test_split_construction_and_flow_reports_all_stage_defects_without_editing(self):
        wire = {"stages": [
            {"id": "place-pair", "entity_ids": ["drill", "furnace"],
             "planned_bill": [{"item": "stone-furnace", "count": 1}],
             "witnesses": [{"kind": "entity_built", "entity_id": "drill"}]},
            {"id": "x" * 76, "entity_ids": [], "planned_bill": [],
             "witnesses": [{"kind": "product_counter_delta", "entity_id": "furnace"}]},
        ]}
        original = copy.deepcopy(wire)
        report = rejection_validation(wire, ContractError("Functional stage lacks a planned native flow witness"))
        self.assertEqual(wire, original)
        self.assertEqual({row["code"] for row in report["issues"]}, {
            "planned_flow_witness_missing", "stage_id_invalid", "stage_entities_empty",
            "planned_bill_empty", "witness_outside_stage"})
        self.assertIn("planned product", validation_message(report))

    def test_unknown_exception_or_report_text_never_reaches_public_diagnostics(self):
        private_value = "synthetic_private_exception_value"
        report = rejection_validation({}, RuntimeError(private_value))
        self.assertNotIn(private_value, str(report))
        self.assertNotIn(private_value, validation_message({"issues": [
            {"code": "unknown", "message": private_value}]}))
        self.assertEqual(report["issues"][0]["code"], "local_structural_validation_failed")

    def test_malformed_stage_members_and_large_lists_have_bounded_safe_reports(self):
        stages = [{"id": [], "entity_ids": [["not-a-string"]], "planned_bill": [],
                   "witnesses": [{"kind": [], "entity_id": []}]}] * 1000
        report = rejection_validation({"stages": stages}, TypeError("private"))
        self.assertLessEqual(len(report["issues"]), 16)
        self.assertNotIn("private", str(report))

    def test_workflow_attaches_fixed_rejection_details_and_does_not_render(self):
        fixture = test_workflow.WorkflowTests()
        fixture.setUp()
        wire = copy.deepcopy(fixture.wire)
        wire["stages"][0]["witnesses"] = [
            {"kind": "entity_built", "entity_id": wire["stages"][0]["entity_ids"][0],
             "item": None, "min_delta": None}]
        with patch("designer_llm.workflow.encode_blueprint", side_effect=AssertionError("must not compile rejected plan")):
            outcome = fixture.run_fixture(wire)
        self.assertEqual(outcome.status, "rejected")
        self.assertIsNone(outcome.accepted)
        self.assertEqual(outcome.evidence["provider_proposal"], wire)
        self.assertEqual(outcome.evidence["validation"]["issues"][0]["code"], "planned_flow_witness_missing")


if __name__ == "__main__":
    unittest.main()
