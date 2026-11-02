"""Rich model-tool designs retain settings/wires across all acceptance checks."""

import copy
import hashlib
import tempfile
from pathlib import Path
import unittest

from designer_llm._reference_contract import ContractError, canonical_bytes
from designer_llm.editor import DraftGridController, replay_tool_trace
from designer_llm.preview import render_preview
from designer_llm.request import make_request
from designer_llm.rich_blueprint import decode_rich_blueprint
from designer_llm.workflow import AcceptedPlan, propose
from tests import test_editor, test_tool_provider
from tests.test_entity_configuration import arithmetic_configuration, operand


class RichWorkflowTests(unittest.TestCase):
    def setUp(self):
        fixture = test_editor.EditorTests()
        fixture.setUp()
        self.context = fixture.rich_context()
        self.request = make_request(self.context, "Synthetic configured workstation with arithmetic control.")
        self.editor = DraftGridController(self.request, self.context)
        self.wire = {"from_id": "assembler", "from_connector": 1, "to_id": "arithmetic",
                     "to_connector": 1, "color": "red"}
        self.stages = [{"id": "workstation", "depends_on": [], "entity_ids": ["assembler", "arithmetic"],
                       "manual_supplies": [{"entity_id": "assembler", "item": "iron-ore", "slot": "input"}],
                       "planned_bill": [{"item": "assembling-machine-2", "count": 1},
                                        {"item": "arithmetic-combinator", "count": 1}],
                       "witnesses": [{"kind": "product_counter_delta", "entity_id": "assembler",
                                      "item": "iron-plate", "min_delta": 1}]}]

    def place(self):
        for row in ({"id": "assembler", "prototype": "assembling-machine-2", "x": 10, "y": 10,
                     "direction": 4, "recipe": "iron-plate"},
                    {"id": "arithmetic", "prototype": "arithmetic-combinator", "x": 14, "y": 10,
                     "direction": 0, "recipe": None}):
            self.assertTrue(self.editor.call("place_entity", row)["ok"])

    def test_circuit_edits_revalidate_ports_distance_and_removal_atomically(self):
        self.place()
        self.assertTrue(self.editor.call("connect_entities", self.wire)["ok"])
        before = self.editor.state
        reverse = {"from_id": "arithmetic", "from_connector": 1, "to_id": "assembler",
                   "to_connector": 1, "color": "red"}
        self.assertFalse(self.editor.call("connect_entities", reverse)["ok"])
        self.assertEqual(self.editor.state, before)
        invalid = dict(self.wire, from_connector=2)
        self.assertFalse(self.editor.call("connect_entities", invalid)["ok"])
        self.assertEqual(self.editor.state, before)
        self.assertFalse(self.editor.call("move_entity", {"id": "arithmetic", "x": 30, "y": 10,
                                                         "direction": None})["ok"])
        self.assertEqual(self.editor.state, before)
        self.assertTrue(self.editor.call("disconnect_entities", reverse)["ok"])
        self.assertEqual(self.editor.state["circuit_wires"], [])
        self.assertTrue(self.editor.call("connect_entities", self.wire)["ok"])
        self.assertTrue(self.editor.call("remove_entity", {"id": "arithmetic"})["ok"])
        self.assertEqual(self.editor.state["circuit_wires"], [])

    def tool_outcome(self):
        config = {"quality": "legendary", "recipe_quality": "normal",
                  "modules": [{"item": "speed-module", "quality": "legendary", "count": 2}], "settings": None}
        rows = [
            ("place_entity", {"id": "assembler", "prototype": "assembling-machine-2", "x": 10, "y": 10,
                              "direction": 4, "recipe": "iron-plate"}),
            ("place_entity", {"id": "arithmetic", "prototype": "arithmetic-combinator", "x": 14, "y": 10,
                              "direction": 0, "recipe": None}),
            ("configure_entity", {"id": "assembler", "configuration": config}),
            ("configure_entity", {"id": "arithmetic", "configuration": arithmetic_configuration()}),
            ("connect_entities", self.wire),
            ("submit_plan", {"plan_id": "configured-workstation", "stages": self.stages}),
        ]
        fixture = test_tool_provider.ToolProviderTests()
        fixture.setUp()
        provider, transport = fixture.provider([test_tool_provider.response([
            test_tool_provider.tool_call(name, args, "rich_call_" + str(index))
            for index, (name, args) in enumerate(rows)])])
        outcome = propose(self.request, self.context, provider)
        self.assertEqual(outcome.status, "accepted_offline", outcome.evidence)
        self.assertEqual(len(transport.calls), 1)
        return outcome

    def test_rich_tool_geometry_settings_requirements_wires_and_preview_remain_bound(self):
        outcome = self.tool_outcome()
        document = outcome.accepted.checked()
        self.assertEqual(document["schema"], "designer-accepted-v2")
        manifest = document["manifest"]
        self.assertEqual(manifest["entity_configurations"]["assembler"]["quality"], "legendary")
        self.assertEqual(replay_tool_trace(self.request, self.context, document["model_tool_trace"]),
                         document["provider_proposal"])
        blueprint = decode_rich_blueprint(document["blueprint"])["blueprint"]
        self.assertEqual(blueprint["wires"], [[2, 1, 1, 1]])
        assembler = blueprint["entities"][0]
        self.assertEqual(assembler["quality"], "legendary")
        self.assertEqual(assembler["recipe_quality"], "normal")
        self.assertEqual(assembler["items"][0]["id"], {"name": "speed-module", "quality": "legendary"})
        self.assertEqual([p["stack"] for p in assembler["items"][0]["items"]["in_inventory"]], [0, 1])
        self.assertIn({"item": "speed-module", "quality": "legendary", "purpose": "module", "count": 2},
                      manifest["configured_item_requirements"]["workstation"])
        with tempfile.TemporaryDirectory() as directory:
            drawing = render_preview(outcome.accepted, Path(directory) / "test-only.png", allow_mock_for_tests=True)
            self.assertEqual(drawing["entity_configurations"], manifest["entity_configurations"])
            self.assertEqual(drawing["circuit_wires"], manifest["circuit_wires"])
        self.assertFalse(document["native_flow_qualified"])

    def test_rehashing_settings_or_wires_cannot_forge_model_actions(self):
        document = self.tool_outcome().accepted.checked()
        for target in ("entity_configurations", "circuit_wires"):
            changed = copy.deepcopy(document)
            if target == "entity_configurations":
                changed["provider_proposal"][target]["assembler"]["modules"][0]["count"] = 1
            else:
                changed["provider_proposal"][target] = []
            forged = AcceptedPlan(changed, hashlib.sha256(canonical_bytes(changed)).hexdigest())
            with self.assertRaises(ContractError):
                forged.checked()

    def test_inserter_filters_and_enable_condition_coexist_without_overwriting(self):
        from designer_llm.rich_blueprint import encode_rich_blueprint
        from designer_llm.rich_proposal import parse_design
        for row in ({"id": "drill", "prototype": "burner-mining-drill", "x": 10, "y": 10,
                     "direction": 4, "recipe": None},
                    {"id": "inserter", "prototype": "stack-inserter", "x": 14, "y": 10,
                     "direction": 0, "recipe": None},
                    {"id": "furnace", "prototype": "stone-furnace", "x": 12, "y": 10,
                     "direction": 0, "recipe": None}):
            self.assertTrue(self.editor.call("place_entity", row)["ok"])
        configuration = {"quality": "normal", "recipe_quality": None, "modules": [], "settings": {
            "kind": "bundle", "values": [
                {"kind": "inserter", "filter_mode": "whitelist", "filters": ["iron-plate"], "stack_size": 3},
                {"kind": "enable_condition", "first": operand(signal={"type": "item", "name": "iron-plate", "quality": "normal"}),
                 "second": operand(constant=10), "comparator": ">"}]}}
        self.assertTrue(self.editor.call("configure_entity", {"id": "inserter", "configuration": configuration})["ok"])
        before = self.editor.state
        invalid = copy.deepcopy(configuration)
        invalid["settings"]["values"].append(copy.deepcopy(invalid["settings"]["values"][0]))
        self.assertFalse(self.editor.call("configure_entity", {"id": "inserter", "configuration": invalid})["ok"])
        self.assertEqual(self.editor.state, before)
        stage = {"id": "extraction", "depends_on": [], "entity_ids": ["drill", "inserter", "furnace"],
                 "manual_supplies": [{"entity_id": eid, "item": "coal", "slot": "fuel"}
                                     for eid in ("drill", "furnace")],
                 "planned_bill": [{"item": "burner-mining-drill", "count": 1}, {"item": "stack-inserter", "count": 1},
                                  {"item": "stone-furnace", "count": 1}],
                 "witnesses": [{"kind": "resource_extraction_delta", "entity_id": "drill", "item": "iron-ore", "min_delta": 1}]}
        result = self.editor.call("submit_plan", {"plan_id": "combined-settings", "stages": [stage]})
        self.assertTrue(result["ok"], result.get("error"))
        geometry, manifest, catalog = parse_design(self.editor.submitted_wire, self.context)
        blueprint = decode_rich_blueprint(encode_rich_blueprint(
            geometry, manifest["entity_configurations"], manifest["circuit_wires"], catalog))["blueprint"]
        inserter = blueprint["entities"][1]
        self.assertEqual(inserter["filters"], [{"index": 1, "name": "iron-plate"}])
        self.assertEqual(inserter["override_stack_size"], 3)
        self.assertEqual(inserter["control_behavior"]["circuit_condition"]["constant"], 10)


if __name__ == "__main__":
    unittest.main()
