"""Offline draft editing and trace integrity; no provider or in-game actions."""

import copy
import json
from pathlib import Path
import unittest

from designer_llm._reference_contract import ContractError, parse_proposal, parse_snapshot
from designer_llm.editor import DraftGridController, replay_tool_trace, tool_schemas
from designer_llm.proposal_schema import to_wire
from designer_llm.request import make_request


BASE = Path(__file__).resolve().parents[1]


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.context = json.loads((BASE / "fixtures/context.json").read_text(encoding="utf-8"))
        self.request = make_request(self.context, "Edit a proposed iron pair.", max_entities=4, max_stages=2)
        self.editor = DraftGridController(self.request, self.context)

    def place(self, entity_id="drill", prototype="burner-mining-drill", x=10, y=10, **changes):
        arguments = {"id": entity_id, "prototype": prototype, "x": x, "y": y,
                     "direction": 4, "recipe": None}
        arguments.update(changes)
        return self.editor.call("place_entity", arguments)

    def pair(self):
        self.assertTrue(self.place()["ok"])
        self.assertTrue(self.place("furnace", "stone-furnace", 12, 10, direction=0, recipe="iron-plate")["ok"])

    def stages(self):
        return [{"id": "funded-pair", "depends_on": [], "entity_ids": ["drill", "furnace"],
                 "planned_bill": [{"item": "burner-mining-drill", "count": 1},
                                  {"item": "stone-furnace", "count": 1}],
                 "manual_supplies": [{"entity_id": eid, "item": "coal", "slot": "fuel"}
                                     for eid in ("drill", "furnace")],
                 "witnesses": [{"kind": "entity_built", "entity_id": "drill", "item": None, "min_delta": None},
                               {"kind": "resource_extraction_delta", "entity_id": "drill", "item": "iron-ore", "min_delta": 1},
                               {"kind": "product_counter_delta", "entity_id": "furnace", "item": "iron-plate", "min_delta": 1}]}]

    def submit(self, stages=None):
        return self.editor.call("submit_plan", {"plan_id": "tool-plan", "stages": self.stages() if stages is None else stages})

    def test_strict_tools_have_closed_required_arguments(self):
        rows = tool_schemas()
        self.assertEqual({r["name"] for r in rows}, {"read_grid", "get_entity_capabilities", "place_entity", "move_entity", "rotate_entity", "upgrade_entity", "remove_entity", "select_entity", "set_recipe", "configure_entity", "connect_entities", "disconnect_entities", "submit_plan"})
        def inspect(node):
            if isinstance(node, dict):
                if node.get("type") == "object":
                    self.assertFalse(node["additionalProperties"])
                    self.assertEqual(set(node["required"]), set(node["properties"]))
                for value in node.values(): inspect(value)
            elif isinstance(node, list):
                for value in node: inspect(value)
        for row in rows:
            self.assertTrue(row["strict"])
            inspect(row["parameters"])

    def test_empty_draft_copies_observations_without_claiming_construction(self):
        original = copy.deepcopy(self.context)
        state = self.editor.read_grid()
        self.assertEqual(state["entities"], [])
        self.assertFalse(state["native_flow_qualified"])
        self.assertEqual(state["construction_status"], "proposed_unbuilt")
        state["prototypes"].clear()
        self.assertEqual(len(self.editor.read_grid()["prototypes"]), 2)
        self.pair()
        self.assertEqual(self.context, original)
        self.assertEqual(self.editor.state["occupied_boxes"], [[1, 1, 3, 3]])

    def test_invalid_edits_are_atomic_and_safe(self):
        self.pair()
        before = self.editor.state
        edits = [
            ("place_entity", {"id": "drill", "prototype": "stone-furnace", "x": 18, "y": 10, "direction": 0, "recipe": None}),
            ("move_entity", {"id": "drill", "x": 12, "y": 10, "direction": None}),
            ("move_entity", {"id": "drill", "x": 2, "y": 2, "direction": None}),
            ("move_entity", {"id": "drill", "x": 0, "y": 10, "direction": None}),
            ("move_entity", {"id": "drill", "x": float("nan"), "y": 10, "direction": None}),
            ("move_entity", {"id": "drill", "x": 10, "y": 10, "direction": 1}),
            ("set_recipe", {"id": "drill", "recipe": "not-unlocked"}),
            ("remove_entity", {"id": "entity-old-furnace-1001"}),
            ("read_grid", {"python": "arbitrary-code-never-executed"}),
            ("select_entity", {"id": "sk-synthetic-forbidden-secret-12345"}),
        ]
        for name, args in edits:
            result = self.editor.call(name, args)
            self.assertFalse(result["ok"], name)
            self.assertEqual(self.editor.state, before)
            self.assertNotIn("arbitrary-code", json.dumps(result))
            self.assertNotIn("synthetic-forbidden-secret", json.dumps(result))

    def test_prototype_id_entity_limit_and_rotate_footprint(self):
        before = self.editor.state
        for changes in ({"prototype": "unsupported"}, {"id": "x" * 65}, {"x": True}):
            self.assertFalse(self.place(**changes)["ok"])
            self.assertEqual(self.editor.state, before)
        for i in range(4):
            self.assertTrue(self.place("drill" + str(i), x=10 + i * 3)["ok"])
        before = self.editor.state
        self.assertEqual(self.place("overflow", x=30)["error"], "entity_limit_exceeded")
        self.assertEqual(self.editor.state, before)
        shaped = copy.deepcopy(self.context)
        shaped["geometry_snapshot"]["prototypes"]["burner-mining-drill"]["width"] = 4
        shaped["geometry_snapshot"]["prototypes"]["burner-mining-drill"]["height"] = 2
        from designer_llm._reference_contract import snapshot_sha256
        shaped["geometry_snapshot_sha256"] = snapshot_sha256(shaped["geometry_snapshot"])
        shaped_request = make_request(shaped, "Check declared rotation.")
        rotated = DraftGridController(shaped_request, shaped)
        self.assertTrue(rotated.call("place_entity", {"id": "wide", "prototype": "burner-mining-drill", "x": 1, "y": 10, "direction": 4, "recipe": None})["ok"])
        prior = rotated.state
        self.assertEqual(rotated.call("move_entity", {"id": "wide", "x": 1, "y": 10, "direction": 0})["error"], "outside_survey_bounds")
        self.assertEqual(rotated.state, prior)

    def test_selection_move_clear_recipe_and_remove_callback(self):
        changes = []
        self.editor = DraftGridController(self.request, self.context, on_change=changes.append)
        self.pair()
        self.assertTrue(self.editor.call("select_entity", {"id": "furnace"})["ok"])
        self.assertEqual(self.editor.state["selected_entity_id"], "furnace")
        self.assertTrue(self.editor.call("move_entity", {"id": "furnace", "x": 14, "y": 11, "direction": None})["ok"])
        furnace = self.editor.state["entities"][1]
        self.assertEqual(furnace["direction"], 0)
        self.assertEqual(furnace["position"], {"x": 14, "y": 11})
        self.assertTrue(self.editor.call("set_recipe", {"id": "furnace", "recipe": None})["ok"])
        self.assertIsNone(self.editor.state["entities"][1]["recipe"])
        self.assertTrue(self.editor.call("remove_entity", {"id": "furnace"})["ok"])
        self.assertIsNone(self.editor.state["selected_entity_id"])
        self.assertEqual(len(changes), 6)
        changes[-1]["entities"].clear()
        self.assertEqual(len(self.editor.state["entities"]), 1)

    def test_rotation_upgrade_and_capabilities_disclose_pinned_limits(self):
        self.pair()
        result = self.editor.call("get_entity_capabilities", {"prototype": "stone-furnace"})
        self.assertTrue(result["ok"])
        capabilities = result["capabilities"]
        self.assertEqual(capabilities["supported_qualities"], ["normal"])
        self.assertFalse(capabilities["modules"])
        self.assertFalse(capabilities["parameter_settings"])
        self.assertFalse(capabilities["upgrade_relationships_qualified"])
        self.assertEqual(capabilities["recipe_validation"], "unlocked_name_only")
        self.assertFalse(capabilities["native_recipe_compatibility_qualified"])
        self.assertEqual(capabilities["width"], 2)
        self.assertTrue(self.editor.call("rotate_entity", {"id": "drill", "direction": 8})["ok"])
        self.assertEqual(self.editor.state["entities"][0]["direction"], 8)
        self.assertTrue(self.editor.call("upgrade_entity", {"id": "drill", "prototype": "stone-furnace"})["ok"])
        self.assertEqual(self.editor.state["entities"][0]["prototype"], "stone-furnace")
        before = self.editor.state
        self.assertEqual(self.editor.call("upgrade_entity", {"id": "drill", "prototype": "not-in-pinned-catalog"})["error"], "unsupported_prototype")
        self.assertEqual(self.editor.state, before)
        capabilities["supported_recipe_names"].clear()
        self.assertTrue(self.editor.call("get_entity_capabilities", {"prototype": "stone-furnace"})["capabilities"]["supported_recipe_names"])

    def test_rotated_upgrade_footprints_are_atomic(self):
        context = copy.deepcopy(self.context)
        context["geometry_snapshot"]["prototypes"]["stone-furnace"]["width"] = 4
        from designer_llm._reference_contract import snapshot_sha256
        context["geometry_snapshot_sha256"] = snapshot_sha256(context["geometry_snapshot"])
        request = make_request(context, "Check replacement footprint.")
        self.editor = DraftGridController(request, context)
        self.assertTrue(self.place(x=1, direction=0)["ok"])
        before = self.editor.state
        self.assertEqual(self.editor.call("upgrade_entity", {"id": "drill", "prototype": "stone-furnace"})["error"], "outside_survey_bounds")
        self.assertEqual(self.editor.state, before)
        self.assertTrue(self.editor.call("rotate_entity", {"id": "drill", "direction": 4})["ok"])
        self.assertTrue(self.editor.call("upgrade_entity", {"id": "drill", "prototype": "stone-furnace"})["ok"])
        before = self.editor.state
        self.assertEqual(self.editor.call("rotate_entity", {"id": "drill", "direction": 0})["error"], "outside_survey_bounds")
        self.assertEqual(self.editor.state, before)

    def test_rejected_submit_keeps_actual_geometry_then_corrects_and_freezes(self):
        self.pair()
        before = self.editor.state
        stages = self.stages()
        stages[0]["witnesses"] = stages[0]["witnesses"][:1]
        self.assertEqual(self.submit(stages)["error"], "stage_needs_flow_witness")
        self.assertEqual(self.editor.state, before)
        self.assertIsNone(self.editor.submitted_wire)
        accepted = self.submit()
        self.assertEqual(accepted["status"], "accepted_offline")
        wire = self.editor.submitted_wire
        self.assertEqual(wire["entities"], before["entities"])
        self.assertEqual(wire["stages"], self.stages())
        parsed = parse_proposal(self.editor.submitted_proposal.raw, parse_snapshot(self.context["geometry_snapshot"]))
        self.assertEqual(to_wire(parsed.raw), wire)
        frozen = self.editor.state
        self.assertFalse(frozen["editable"])
        self.assertEqual(self.editor.call("remove_entity", {"id": "drill"})["error"], "draft_frozen_after_submit")
        self.assertEqual(self.editor.state, frozen)
        self.assertTrue(self.editor.call("read_grid", {})["ok"])
        wire["entities"].clear()
        self.assertEqual(len(self.editor.submitted_geometry), 2)

    def test_submit_cross_stage_missing_bill_cycles_and_empty_draft_reject(self):
        self.assertEqual(self.submit()["error"], "empty_draft")
        self.pair()
        before = self.editor.state
        bad = self.stages()
        bad[0]["planned_bill"] = bad[0]["planned_bill"][:1]
        self.assertEqual(self.submit(bad)["error"], "placement_bill_incomplete")
        bad = self.stages()
        bad[0]["witnesses"][1]["entity_id"] = "absent"
        self.assertEqual(self.submit(bad)["error"], "witness_must_target_same_stage_entity")
        bad = self.stages()
        bad[0]["depends_on"] = [bad[0]["id"]]
        self.assertEqual(self.submit(bad)["error"], "invalid_stage_dependency")
        self.assertEqual(self.editor.state, before)

    def trace(self):
        trace = {"schema": "designer-model-tool-trace-v1", "calls": []}
        edits = [("read_grid", {}),
                 ("place_entity", {"id": "drill", "prototype": "burner-mining-drill", "x": 10, "y": 10, "direction": 4, "recipe": None}),
                 ("place_entity", {"id": "furnace", "prototype": "stone-furnace", "x": 12, "y": 10, "direction": 0, "recipe": "iron-plate"}),
                 ("move_entity", {"id": "furnace", "x": 10, "y": 10, "direction": None}),
                 ("submit_plan", {"plan_id": "tool-plan", "stages": self.stages()})]
        for i, (name, args) in enumerate(edits):
            trace["calls"].append({"call_id": "call_" + str(i), "name": name,
                                   "arguments": args, "raw_arguments": json.dumps(args),
                                   "result": self.editor.call(name, args)})
        return trace

    def test_trace_replay_binds_exact_model_actions_results_and_submission(self):
        trace = self.trace()
        self.assertEqual(replay_tool_trace(self.request, self.context, trace), self.editor.submitted_wire)
        altered = [
            lambda t: t["calls"][1]["arguments"].update(x=11),
            lambda t: t["calls"][1].update(raw_arguments='{"id":"drill","id":"drill"}'),
            lambda t: t["calls"][1]["result"]["state"]["entities"][0]["position"].update(x=11),
            lambda t: t["calls"][1].update(call_id="call_0"),
            lambda t: t["calls"][1].update(name="execute_python"),
            lambda t: t["calls"].pop(),
            lambda t: t["calls"].append(copy.deepcopy(t["calls"][0])),
            lambda t: t.update(unsupported="ignored-no-longer"),
        ]
        for edit in altered:
            value = copy.deepcopy(trace)
            edit(value)
            with self.assertRaisesRegex(ContractError, "Invalid model tool trace"):
                replay_tool_trace(self.request, self.context, value)

    def rich_context(self):
        from tests.test_entity_configuration import synthetic_catalog
        from designer_llm._reference_contract import snapshot_sha256
        context = copy.deepcopy(self.context)
        catalog = synthetic_catalog(context["identity"])
        geometry = context["geometry_snapshot"]
        for name in catalog["entities"]:
            if name not in geometry["prototypes"]:
                geometry["prototypes"][name] = {"placement_item": name, "width": 2, "height": 2}
                geometry["items"].append(name)
            spec = geometry["prototypes"][name]
            kind = catalog["entities"][name]["entity_type"]
            spec.setdefault("type", kind)
            spec.setdefault("energy_source", "electric" if kind in ("assembling-machine", "arithmetic-combinator", "inserter") else "none")
            if kind == "assembling-machine":
                # This synthetic configuration catalog intentionally crafts iron plates.
                spec["crafting_categories"] = ["crafting", "smelting"]
            if kind == "inserter":
                spec.update(pickup_vector=[0, -1], drop_vector=[0, 1])
        geometry["prototypes"]["test-powered-pole"] = {
            "placement_item": "test-powered-pole", "width": 1, "height": 1,
            "type": "electric-pole", "energy_source": "none", "supply_area_distance": 20,
            "max_wire_distance": 7.5}
        geometry["items"].append("test-powered-pole")
        context["facts"]["built_machines"]["value"].append({
            "prototype": "test-powered-pole", "position": {"x": 6, "y": 10}, "direction": 0,
            "powered": True})
        context["geometry_snapshot_sha256"] = snapshot_sha256(geometry)
        context["entity_capabilities"] = {
            "knowledge": "known", "value": catalog,
            "observed_tick": context["identity"]["observed_tick"],
            "revision": context["identity"]["revision"],
            "provenance": {"kind": "fixture", "source_id": "fixture:unit-test-capabilities"},
        }
        return context

    def test_configuration_without_catalog_is_explicitly_gated(self):
        self.pair()
        before = self.editor.state
        self.assertEqual(before["entity_configurations"]["drill"], {"quality": "normal", "recipe_quality": None, "modules": [], "settings": None})
        configuration = copy.deepcopy(before["entity_configurations"]["drill"])
        configuration["quality"] = "legendary"
        result = self.editor.call("configure_entity", {"id": "drill", "configuration": configuration})
        self.assertEqual(result["error"], "configuration_capabilities_required")
        self.assertEqual(self.editor.state, before)
        configuration["quality"] = "normal"
        self.assertTrue(self.editor.call("configure_entity", {"id": "drill", "configuration": configuration})["ok"])
        configuration["recipe_quality"] = "normal"
        self.assertEqual(self.editor.call("configure_entity", {"id": "drill", "configuration": configuration})["error"], "recipe_quality_requires_recipe")

    def test_declared_quality_modules_and_settings_preserved_in_v2_submit(self):
        from tests.test_entity_configuration import arithmetic_configuration
        context = self.rich_context()
        request = make_request(context, "Test declared rich intent without native export.", max_entities=4, max_stages=2)
        self.editor = DraftGridController(request, context)
        self.assertTrue(self.place("assembler", "assembling-machine-2", 10, 10, direction=0, recipe="iron-plate")["ok"])
        self.assertTrue(self.place("arithmetic", "arithmetic-combinator", 18, 10, direction=0)["ok"])
        configuration = arithmetic_configuration()
        result = self.editor.call("configure_entity", {"id": "arithmetic", "configuration": configuration})
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(self.editor.state["entity_configurations"]["arithmetic"], configuration)
        capability = self.editor.call("get_entity_capabilities", {"prototype": "arithmetic-combinator"})["capabilities"]
        self.assertTrue(capability["configuration_capabilities_known"])
        self.assertTrue(capability["parameter_settings"])
        self.assertIn("legendary", capability["supported_qualities"])
        capability["configuration_spec"]["qualities"].clear()
        capability["signal_capabilities"].clear()
        checked_capabilities = self.editor.call("get_entity_capabilities", {"prototype": "arithmetic-combinator"})["capabilities"]
        self.assertIn("legendary", checked_capabilities["configuration_spec"]["qualities"])
        self.assertTrue(checked_capabilities["signal_capabilities"])
        result = self.editor.call("submit_plan", {"plan_id": "rich-configured-design", "stages": [{
            "id": "stage-one", "depends_on": [], "entity_ids": ["assembler", "arithmetic"],
            "manual_supplies": [{"entity_id": "assembler", "item": "iron-ore", "slot": "input"}],
            "planned_bill": [{"item": "assembling-machine-2", "count": 1}, {"item": "arithmetic-combinator", "count": 1}],
            "witnesses": [{"kind": "product_counter_delta", "entity_id": "assembler", "item": "iron-plate", "min_delta": 1}]}]})
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(self.editor.submitted_wire["schema"], "designer-proposal-v2")
        self.assertEqual(self.editor.submitted_wire["entity_configurations"]["arithmetic"], configuration)
        self.assertFalse(self.editor.state["editable"])

    def test_upgrade_catalog_graph_and_configuration_revalidation_are_atomic(self):
        context = self.rich_context()
        request = make_request(context, "Check catalog upgrades and module slots.")
        self.editor = DraftGridController(request, context)
        self.assertTrue(self.place("assembler", "assembling-machine-2", 10, 10, direction=0, recipe="iron-plate")["ok"])
        configuration = {"quality": "legendary", "recipe_quality": "legendary", "modules": [{"item": "speed-module", "quality": "legendary", "count": 2}], "settings": None}
        self.assertTrue(self.editor.call("configure_entity", {"id": "assembler", "configuration": configuration})["ok"])
        before = self.editor.state
        self.assertEqual(self.editor.call("upgrade_entity", {"id": "assembler", "prototype": "stone-furnace"})["error"], "workstation_upgrade_unavailable")
        self.assertEqual(self.editor.state, before)
        self.assertTrue(self.editor.call("upgrade_entity", {"id": "assembler", "prototype": "assembling-machine-3"})["ok"])
        self.assertEqual(self.editor.state["entity_configurations"]["assembler"], configuration)
        self.assertTrue(self.editor.call("remove_entity", {"id": "assembler"})["ok"])
        self.assertEqual(self.editor.state["entity_configurations"], {})

    def test_legacy_submission_still_replays_with_explicit_default_config(self):
        trace = self.trace()
        self.assertEqual(replay_tool_trace(self.request, self.context, trace), self.editor.submitted_wire)
        before = self.editor.state
        self.assertEqual(set(before["entity_configurations"]), {"drill", "furnace"})
        self.assertFalse(before["configuration_capabilities_known"])

    def test_unknown_and_stale_catalogs_do_not_authorize_rich_edits(self):
        context = self.rich_context()
        wrapper = context["entity_capabilities"]
        wrapper["knowledge"], wrapper["value"] = "unknown", None
        request = make_request(context, "Unknown capabilities stay unavailable.")
        controller = DraftGridController(request, context)
        self.assertFalse(controller.state["configuration_capabilities_known"])
        context = self.rich_context()
        context["entity_capabilities"]["observed_tick"] -= 1
        request = make_request(context, "Stale catalog stays unavailable.")
        with self.assertRaisesRegex(ContractError, "Capability observations are stale"):
            DraftGridController(request, context)


if __name__ == "__main__":
    unittest.main()
