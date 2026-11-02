import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from designer_llm._reference_contract import (ContractError, canonical_bytes,
                                             decode_blueprint, incremental_frontier, parse_proposal, parse_snapshot)
from designer_llm.artifacts import export_outcome
from designer_llm.context import context_sha256, validate_context
from designer_llm.memory import DesignerMemory
from designer_llm.preview import preview_document, render_preview
from designer_llm.proposal_schema import from_wire, proposal_schema, to_wire
from designer_llm.providers import ReplayProvider
from designer_llm.request import make_request, validate_request
from designer_llm.workflow import AcceptedPlan, propose


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.context = validate_context(fixture("context.json"))
        self.raw = fixture("proposal.json")
        self.wire = to_wire(self.raw)
        self.request = make_request(self.context, "Expand iron production in the declared synthetic site.",
                                    demand=[{"item": "iron-plate", "metric": "rate_items_per_second",
                                             "amount": 4, "window_ticks": 3600}])

    def run_fixture(self, wire=None, **kwargs):
        return propose(self.request, self.context, ReplayProvider(self.wire if wire is None else wire), **kwargs)

    def test_pipeline_preserves_model_geometry_blueprint_hash_and_unbuilt_scope(self):
        outcome = self.run_fixture()
        self.assertEqual(outcome.status, "accepted_offline")
        doc = outcome.accepted.checked()
        self.assertEqual(doc["manifest"], self.raw)
        self.assertEqual(doc["provider_proposal"], self.wire)
        self.assertTrue(doc["provider"]["mock"])
        self.assertFalse(doc["native_flow_qualified"])
        self.assertEqual(doc["construction_status"], "proposed_unbuilt")
        entities = decode_blueprint(doc["blueprint"])["blueprint"]["entities"]
        self.assertEqual(doc["geometry_sha256"], hashlib.sha256(canonical_bytes({"entities": entities})).hexdigest())
        parsed = parse_proposal(doc["manifest"], parse_snapshot(self.context["geometry_snapshot"]))
        first = {e.id for e in parsed.entities if e.id in parsed.stages[0].entity_ids}
        self.assertEqual(incremental_frontier(parsed, first, set()), ("first-pair", ()))

    def test_same_tick_changed_context_and_reload_reject_pending_proposal(self):
        for edit in (lambda c: c["facts"]["inventories"]["value"].append({"new_observation": True}),
                     lambda c: c["identity"].update(session_epoch=4),
                     lambda c: c["identity"].update(life_id="fixture-life-2")):
            current = copy.deepcopy(self.context)
            edit(current)
            self.assertEqual(self.run_fixture(latest_context=lambda: current).status, "rejected")

    def test_physics_declarations_reject_collision_direction_recipe_bill_witness(self):
        mutations = [
            lambda p: p["entities"][1]["position"].update(x=p["entities"][0]["position"]["x"]),
            lambda p: p["entities"][0].update(direction=1),
            lambda p: p["entities"][1].update(recipe="steel-plate"),
            lambda p: p["stages"][0]["planned_bill"].clear(),
            lambda p: p["stages"][0]["witnesses"][-1].update(entity_id="furnace-2"),
        ]
        for mutation in mutations:
            value = copy.deepcopy(self.wire)
            mutation(value)
            self.assertEqual(self.run_fixture(value).status, "rejected")

    def test_schema_is_closed_and_duplicate_bills_and_extra_fields_reject(self):
        def check(node):
            if isinstance(node, dict):
                if node.get("type") == "object":
                    self.assertIs(node["additionalProperties"], False)
                    self.assertEqual(set(node["required"]), set(node["properties"]))
                for v in node.values():
                    check(v)
            elif isinstance(node, list):
                for v in node:
                    check(v)
        check(proposal_schema())
        dup = copy.deepcopy(self.wire)
        dup["stages"][0]["planned_bill"].append(dup["stages"][0]["planned_bill"][0])
        extra = copy.deepcopy(self.wire)
        extra["entities"][0]["python_to_execute"] = "print('unsupported')"
        for value in (dup, extra):
            self.assertEqual(self.run_fixture(value).status, "rejected")
        self.assertEqual(from_wire(self.wire), self.raw)

    def test_demand_units_freshness_and_bounded_input(self):
        request = copy.deepcopy(self.request)
        request["demand"][0]["window_ticks"] = None
        with self.assertRaises(ContractError):
            validate_request(request, self.context)
        request["demand"][0]["metric"] = "cumulative_produced"
        with self.assertRaises(ContractError):
            validate_request(request, self.context)
        stale = copy.deepcopy(self.context)
        stale["facts"]["terrain"]["observed_tick"] = 100
        with self.assertRaises(ContractError):
            make_request(stale, "Expand")
        with self.assertRaises(ContractError):
            self.run_fixture(max_input_bytes=31_999)

    def test_mutation_and_forged_blueprint_never_export(self):
        accepted = self.run_fixture().accepted
        accepted.document["manifest"]["entities"][0]["position"]["x"] += 1
        with self.assertRaises(ContractError):
            accepted.checked()
        accepted = self.run_fixture().accepted
        doc = copy.deepcopy(accepted.document)
        doc["blueprint"] = doc["blueprint"][:-3] + "BAD"
        forged = AcceptedPlan(doc, hashlib.sha256(canonical_bytes(doc)).hexdigest())
        with self.assertRaises(ContractError):
            forged.checked()

    def test_preview_is_exact_model_output_and_png_hashes_are_deterministic(self):
        accepted = self.run_fixture().accepted
        with self.assertRaises(ContractError):
            preview_document(accepted)
        with tempfile.TemporaryDirectory(prefix="designer-test-only-") as temp:
            p1, p2 = Path(temp) / "test1.png", Path(temp) / "test2.png"
            drawing = render_preview(accepted, p1, allow_mock_for_tests=True)
            render_preview(accepted, p2, allow_mock_for_tests=True)
            self.assertEqual(drawing["proposal"]["entities"], accepted.checked()["manifest"]["entities"])
            self.assertEqual(p1.read_bytes(), p2.read_bytes())
            with Image.open(p1) as img:
                self.assertEqual(img.size, (1500, 1000))
                self.assertEqual(img.info["geometry_sha256"], accepted.checked()["geometry_sha256"])
                self.assertEqual(img.info["mock"], "true")
            target = Path(temp) / "fake-deliverable"
            with self.assertRaises(ContractError):
                export_outcome(self.run_fixture(), target)
            self.assertFalse(target.exists())

    def test_persisted_revision_lineage_and_chat_not_build_authority(self):
        with tempfile.TemporaryDirectory() as temp:
            with DesignerMemory(Path(temp) / "history.sqlite3", "fixture-world", "fixture-force") as memory:
                memory.add_message("user", "I built twenty machines yesterday.", 12000)
                outcome = self.run_fixture(memory=memory)
                self.assertEqual(outcome.status, "accepted_offline")
                doc = outcome.accepted.checked()
                self.request = make_request(self.context, "Revise expansion", revision=2,
                                            parent_design_hash=doc["design_sha256"])
                moved = copy.deepcopy(self.wire)
                for entity in moved["entities"][:2]:
                    entity["position"]["y"] += 1
                self.assertEqual(self.run_fixture(moved, memory=memory).status, "accepted_offline")
                self.assertEqual(memory.get_plan("iron-expansion")["revision"], 2)
                summary = memory.context(self.context, max_bytes=16000)
                self.assertEqual(len(summary["events"]["records"]), 0)
                self.assertEqual(context_sha256(self.context), doc["context_sha256"])

    def test_rehashed_packages_still_require_original_request_raw_and_report_bindings(self):
        accepted = self.run_fixture().accepted
        for edit in (lambda d: d["request"]["constraints"].update(max_entities=1),
                     lambda d: d.update(parent_design_hash="c" * 64),
                     lambda d: d["provider"].update(mock="false"),
                     lambda d: d.update(raw_proposal_text='{}'),
                     lambda d: d["validation"].update(unqualified=[])):
            changed = copy.deepcopy(accepted.document)
            edit(changed)
            rehashed = AcceptedPlan(changed, hashlib.sha256(canonical_bytes(changed)).hexdigest())
            with self.assertRaises(ContractError):
                rehashed.checked()

    def test_nested_stale_observation_does_not_hide_under_fresh_group(self):
        stale = copy.deepcopy(self.context)
        stale["facts"]["built_machines"]["value"][0]["status"]["observed_tick"] = 100
        with self.assertRaises(ContractError):
            make_request(stale, "Expand")

    def test_overlay_identifiers_do_not_break_exact_proposed_geometry(self):
        context = copy.deepcopy(self.context)
        context["facts"]["built_machines"]["value"][0]["entity_id"] = "M" * 128
        request = make_request(context, "Expand")
        accepted = propose(request, context, ReplayProvider(self.wire)).accepted
        drawing = preview_document(accepted, allow_mock_for_tests=True)
        self.assertLessEqual(len(drawing["existing_entities"][0]["id"]), 64)
        self.assertEqual(drawing["proposal"]["entities"], self.raw["entities"])

    def test_renderer_accepts_full_contract_name_bounds(self):
        context, raw = copy.deepcopy(self.context), copy.deepcopy(self.raw)
        name, recipe = "P" * 128, "R" * 128
        context["geometry_snapshot"]["prototypes"][name] = context["geometry_snapshot"]["prototypes"]["stone-furnace"]
        context["geometry_snapshot"]["unlocked_recipes"].append(recipe)
        context["facts"]["recipes"]["value"].append({"name": recipe, "category": "smelting",
                                                      "ingredients": [{"item": "iron-ore", "amount": 1}]})
        context.pop("geometry_snapshot_sha256")
        context = validate_context(context)
        raw["snapshot_sha256"] = context["geometry_snapshot_sha256"]
        for entity in raw["entities"]:
            if entity["prototype"] == "stone-furnace":
                entity.update(prototype=name, recipe=recipe)
        outcome = propose(make_request(context, "Render permitted names"), context, ReplayProvider(to_wire(raw)))
        self.assertEqual(outcome.status, "accepted_offline")
        with tempfile.TemporaryDirectory() as temp:
            render_preview(outcome.accepted, Path(temp) / "test-only.png", allow_mock_for_tests=True)

    def test_host_history_failure_preserves_validated_provider_result(self):
        with tempfile.TemporaryDirectory() as temp:
            with DesignerMemory(Path(temp) / "history.sqlite3", "fixture-world", "fixture-force") as memory:
                with patch.object(memory, "record_plan", side_effect=OSError("private storage unavailable")):
                    outcome = self.run_fixture(memory=memory)
                self.assertEqual(outcome.status, "accepted_offline")
                self.assertEqual(outcome.evidence["memory_record_status"], "local_history_write_failed")
                self.assertEqual(outcome.accepted.checked()["manifest"], self.raw)


if __name__ == "__main__":
    unittest.main()
