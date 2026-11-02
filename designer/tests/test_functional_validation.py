"""Declared functional acceptance, using only local synthetic observations."""

import copy
import json
from pathlib import Path
import unittest

from designer_llm._reference_contract import ContractError, Entity, _entity_box, parse_proposal, parse_snapshot
from designer_llm.context import validate_context
from designer_llm.diagnostics import rejection_validation, validation_message
from designer_llm.editor import DraftGridController, tool_schemas
from designer_llm.functional_validation import (
    FunctionalValidationError, crafting_input_accepts, port_point, rotate_vector,
)
from designer_llm.proposal_schema import from_wire, proposal_schema, to_wire, validate_wire
from designer_llm.providers import ReplayProvider
from designer_llm.request import make_request
from designer_llm.rich_proposal import parse_design
from designer_llm.workflow import AcceptedPlan, propose, sha256

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class FunctionalValidationTests(unittest.TestCase):
    def setUp(self):
        self.context = json.loads((FIXTURES / "context.json").read_text())
        self.raw = json.loads((FIXTURES / "proposal.json").read_text())
        self.raw["entities"] = self.raw["entities"][:2]
        self.raw["stages"] = self.raw["stages"][:1]

    def synchronize(self):
        self.context.pop("geometry_snapshot_sha256", None)
        self.context = validate_context(self.context)
        self.raw["snapshot_sha256"] = self.context["geometry_snapshot_sha256"]

    def outcome(self):
        self.synchronize()
        return propose(make_request(self.context, "Test declared functional feasibility"), self.context,
                       ReplayProvider(to_wire(self.raw)))

    def codes(self):
        outcome = self.outcome()
        self.assertEqual(outcome.status, "rejected", outcome.evidence)
        self.assertIsNone(outcome.accepted)
        return {issue["code"] for issue in outcome.evidence["validation"]["issues"]}

    def add_prototype(self, name, **facts):
        snapshot = self.context["geometry_snapshot"]
        snapshot["items"].append(name)
        snapshot["prototypes"][name] = {"placement_item": name, "width": 1, "height": 1, **facts}

    def add_entity(self, entity_id, prototype, x, y, direction=0, recipe=None):
        entity = {"id": entity_id, "prototype": prototype, "position": {"x": x, "y": y},
                  "direction": direction}
        if recipe is not None:
            entity["recipe"] = recipe
        self.raw["entities"].append(entity)
        stage = self.raw["stages"][0]
        stage["entity_ids"].append(entity_id)
        stage["planned_bill"][prototype] = stage["planned_bill"].get(prototype, 0) + 1

    def assembler(self):
        self.add_prototype("assembling-machine-2", type="assembling-machine", energy_source="electric",
                           width=3, height=3, crafting_categories=["crafting"])
        snapshot = self.context["geometry_snapshot"]
        snapshot["unlocked_recipes"].append("iron-gear-wheel")
        self.context["facts"]["recipes"]["value"].append({"name": "iron-gear-wheel", "category": "crafting",
                                                           "ingredients": [{"item": "iron-plate", "amount": 2}]})
        self.raw["entities"] = [{"id": "assembler", "prototype": "assembling-machine-2",
                                 "position": {"x": 20.5, "y": 10.5}, "direction": 0, "recipe": "iron-gear-wheel"}]
        self.raw["stages"] = [{"id": "gears", "depends_on": [], "entity_ids": ["assembler"],
                               "planned_bill": {"assembling-machine-2": 1},
                               "witnesses": [{"kind": "product_counter_delta", "entity_id": "assembler",
                                              "item": "iron-gear-wheel", "min_delta": 1}]}]

    def power_chain(self):
        self.add_prototype("test-pole", type="electric-pole", energy_source="none",
                           supply_area_distance=2.5, max_wire_distance=5.5)
        self.add_prototype("test-solar", type="solar-panel", energy_source="electric", produces_power=True,
                           width=3, height=3)
        self.add_entity("solar", "test-solar", 6.5, 10.5)
        for i, x in enumerate((8.5, 13.5, 18.5)):
            self.add_entity("pole-" + str(i), "test-pole", x, 10.5)

    def supply_input(self):
        self.raw["stages"][0]["manual_supplies"] = [{"entity_id": "assembler", "item": "iron-plate", "slot": "input"}]

    def test_fixed_fixture_is_accepted_with_hashed_manual_supplies(self):
        self.raw = json.loads((FIXTURES / "proposal.json").read_text())
        outcome = self.outcome()
        self.assertEqual(outcome.status, "accepted_offline", outcome.evidence)
        document = outcome.accepted.checked()
        self.assertEqual(document["manifest"], self.raw)
        self.assertIn("declared_functional_feasibility", document["validation"]["checked"])
        self.assertFalse(document["native_flow_qualified"])

    def test_original_gap_is_rejected(self):
        self.raw = json.loads((FIXTURES / "proposal-gap.json").read_text())
        self.assertEqual(self.codes(), {"drill_output_unconnected", "crafting_input_unplanned"})

    def test_each_burner_requires_planned_fuel(self):
        for eid in ("first-drill", "first-furnace"):
            with self.subTest(entity=eid):
                original = copy.deepcopy(self.raw["stages"][0]["manual_supplies"])
                self.raw["stages"][0]["manual_supplies"] = [s for s in original if s["entity_id"] != eid]
                self.assertIn("burner_fuel_unplanned", self.codes())
                self.raw["stages"][0]["manual_supplies"] = original

    def test_input_supply_does_not_count_as_fuel(self):
        self.raw["stages"][0]["manual_supplies"][0]["slot"] = "input"
        self.assertIn("burner_fuel_unplanned", self.codes())

    def test_drill_off_resource_and_unknown_patches_are_rejected(self):
        for mode in ("off-patch", "unknown-patches", "unknown-terrain", "empty-patches", "incompatible-category"):
            with self.subTest(mode=mode):
                original = copy.deepcopy(self.context)
                if mode == "off-patch":
                    self.context["facts"]["terrain"]["value"]["resource_patches"]["value"][0]["bounds"] = [40, 20, 45, 25]
                elif mode == "unknown-patches":
                    self.context["facts"]["terrain"]["value"]["resource_patches"].update(knowledge="unknown", value=None)
                elif mode == "unknown-terrain":
                    self.context["facts"]["terrain"].update(knowledge="unknown", value=None)
                elif mode == "empty-patches":
                    self.context["facts"]["terrain"]["value"]["resource_patches"]["value"] = []
                else:
                    self.context["geometry_snapshot"]["prototypes"]["burner-mining-drill"]["resource_categories"] = ["basic-fluid"]
                if mode == "unknown-terrain":
                    self.synchronize()
                    with self.assertRaisesRegex(FunctionalValidationError, "drill_not_on_resource"):
                        parse_design(to_wire(self.raw), self.context)
                else:
                    self.assertIn("drill_not_on_resource", self.codes())
                self.context = original

    def test_extraction_witness_must_match_patch(self):
        self.raw["stages"][0]["witnesses"][2]["item"] = "coal"
        self.assertIn("drill_not_on_resource", self.codes())

    def test_drill_drop_requires_compatible_furnace_recipe(self):
        self.raw["entities"][1]["recipe"] = "stone-furnace"
        self.assertIn("drill_output_unconnected", self.codes())

    def test_automatic_furnace_recipe_accepts_ore(self):
        self.raw["entities"][1].pop("recipe")
        self.assertEqual(self.outcome().status, "accepted_offline")

    def test_container_and_belt_accept_drill_output(self):
        for kind in ("container", "transport-belt"):
            with self.subTest(kind=kind):
                original = copy.deepcopy(self.context)
                self.context["geometry_snapshot"]["prototypes"]["stone-furnace"].update(type=kind, energy_source="none")
                self.raw["stages"][0]["witnesses"] = self.raw["stages"][0]["witnesses"][:3]
                self.assertEqual(self.outcome().status, "accepted_offline")
                self.context = original

    def test_coal_drop_accepts_a_burner(self):
        self.context["facts"]["terrain"]["value"]["resource_patches"]["value"][0]["resource"] = "coal"
        self.raw["stages"][0]["witnesses"] = self.raw["stages"][0]["witnesses"][:3]
        self.raw["stages"][0]["witnesses"][2]["item"] = "coal"
        self.assertEqual(self.outcome().status, "accepted_offline")

    def test_coal_drill_into_iron_furnace_does_not_supply_crafting_input(self):
        self.context["facts"]["terrain"]["value"]["resource_patches"]["value"][0]["resource"] = "coal"
        self.raw["stages"][0]["witnesses"][2]["item"] = "coal"
        self.assertEqual(self.codes(), {"crafting_input_unplanned"})

    def test_furnace_without_recipe_manual_input_requires_smelting_ingredient(self):
        self.raw["entities"] = self.raw["entities"][1:]
        self.raw["entities"][0].pop("recipe")
        stage = self.raw["stages"][0]
        stage["entity_ids"] = ["first-furnace"]
        stage["planned_bill"].pop("burner-mining-drill")
        stage["witnesses"] = [w for w in stage["witnesses"] if w["entity_id"] == "first-furnace"]
        stage["manual_supplies"] = [s for s in stage["manual_supplies"] if s["entity_id"] == "first-furnace"]
        stage["manual_supplies"].append({"entity_id": "first-furnace", "item": "iron-ore", "slot": "input"})
        self.assertEqual(self.outcome().status, "accepted_offline")
        stage["manual_supplies"][-1]["item"] = "coal"
        self.assertEqual(self.codes(), {"crafting_input_unplanned"})

    def test_crafting_input_accepts_known_recipe_ingredients_without_fuel_shortcut(self):
        for kind in ("furnace", "assembling-machine", "lab"):
            for ingredients in ([{"item": "coal", "amount": 1}], [{"name": "coal", "amount": 1}], {"coal": 1}):
                with self.subTest(kind=kind, ingredients=ingredients):
                    target = Entity("machine", "test-machine", 10, 10, 0, "test-recipe")
                    recipes = {"test-recipe": {"category": "smelting", "ingredients": ingredients}}
                    self.assertTrue(crafting_input_accepts(target, {"type": kind}, "coal", recipes))
                    self.assertFalse(crafting_input_accepts(target, {"type": kind}, "iron-ore", recipes))
        furnace = Entity("furnace", "stone-furnace", 10, 10, 0, None)
        spec = {"type": "furnace"}
        recipes = {"test-recipe": {"category": "smelting", "ingredients": [{"item": "coal", "amount": 1}]}}
        self.assertTrue(crafting_input_accepts(furnace, spec, "coal", recipes))
        recipes["test-recipe"]["category"] = "crafting"
        self.assertFalse(crafting_input_accepts(furnace, spec, "coal", recipes))
        for ore in ("iron-ore", "copper-ore", "stone"):
            self.assertTrue(crafting_input_accepts(furnace, spec, ore, {}))
        # Coal is crafting input in a selected furnace only when the recipe actually needs it.
        furnace = Entity("furnace", "stone-furnace", 10, 10, 0, "iron-plate")
        self.assertFalse(crafting_input_accepts(furnace, spec, "coal", {}))
        recipes = {"iron-plate": {"ingredients": [{"item": "iron-ore"}, {"item": "coal"}]}}
        self.assertTrue(crafting_input_accepts(furnace, spec, "coal", recipes))

    def test_each_needed_prototype_fact_missing_rejects_without_guessing(self):
        for name, field in (("burner-mining-drill", "type"), ("burner-mining-drill", "energy_source"),
                            ("burner-mining-drill", "fuel_categories"), ("burner-mining-drill", "drop_vector"),
                            ("burner-mining-drill", "mining_area_radius"), ("burner-mining-drill", "resource_categories"),
                            ("stone-furnace", "crafting_categories")):
            with self.subTest(prototype=name, field=field):
                original = copy.deepcopy(self.context)
                self.context["geometry_snapshot"]["prototypes"][name].pop(field)
                self.assertIn("prototype_facts_missing:" + name, self.codes())
                self.context = original

    def test_legacy_geometry_still_loads_but_missing_functional_facts_reject_acceptance(self):
        for spec in self.context["geometry_snapshot"]["prototypes"].values():
            for key in list(spec):
                if key not in ("placement_item", "width", "height"):
                    del spec[key]
        self.synchronize()
        parse_proposal(self.raw, parse_snapshot(self.context["geometry_snapshot"]))
        self.assertIn("prototype_facts_missing:burner-mining-drill", self.codes())

    def test_all_four_port_rotations_and_rotated_boxes(self):
        vectors = {0: (-0.5, -1.3), 4: (1.3, -0.5), 8: (0.5, 1.3), 12: (-1.3, 0.5)}
        for direction, vector in vectors.items():
            with self.subTest(direction=direction):
                entity = Entity("drill", "burner-mining-drill", 10, 10, direction, None)
                self.assertEqual(rotate_vector([-0.5, -1.3], direction), vector)
                self.assertEqual(port_point(entity, [-0.5, -1.3]), (10 + vector[0], 10 + vector[1]))
                box = _entity_box(entity, {"width": 1, "height": 3})
                self.assertEqual(box[2] - box[0], 3 if direction in (4, 12) else 1)

    def test_rotated_drills_drop_into_adjacent_furnaces(self):
        for direction, position in ((0, (10, 8)), (4, (12, 10)), (8, (10, 12)), (12, (8, 10))):
            with self.subTest(direction=direction):
                self.raw["entities"][0]["direction"] = direction
                self.raw["entities"][1]["position"] = dict(zip(("x", "y"), position))
                self.assertEqual(self.outcome().status, "accepted_offline")

    def test_unpowered_unfed_assembler_returns_both_codes(self):
        self.assembler()
        self.assertEqual(self.codes(), {"electric_consumer_unpowered", "crafting_input_unplanned"})

    def test_powered_assembler_still_requires_input(self):
        self.assembler()
        self.power_chain()
        self.assertEqual(self.codes(), {"crafting_input_unplanned"})
        self.supply_input()
        self.assertEqual(self.outcome().status, "accepted_offline")

    def test_powered_assembler_manual_coal_is_not_a_recipe_ingredient(self):
        self.assembler()
        self.power_chain()
        self.supply_input()
        self.raw["stages"][0]["manual_supplies"][0]["item"] = "coal"
        self.assertEqual(self.codes(), {"crafting_input_unplanned"})
        self.raw["stages"][0]["manual_supplies"][0]["item"] = "iron-plate"
        self.assertEqual(self.outcome().status, "accepted_offline")

    def test_manual_input_remains_allowed_with_unknown_recipe_ingredients(self):
        self.assembler()
        self.power_chain()
        self.supply_input()
        self.raw["stages"][0]["manual_supplies"][0]["item"] = "coal"
        original = copy.deepcopy(self.context)
        for mode in ("missing", "unknown"):
            with self.subTest(mode=mode):
                self.context = copy.deepcopy(original)
                recipe = self.context["facts"]["recipes"]["value"][-1]
                if mode == "missing":
                    recipe.pop("ingredients")
                else:
                    observation = copy.deepcopy(self.context["facts"]["terrain"]["value"]["resource_patches"])
                    observation.update(knowledge="unknown", value=None)
                    recipe["ingredients"] = observation
                self.assertEqual(self.outcome().status, "accepted_offline")

    def test_wire_chain_must_fit_both_poles_wire_distances(self):
        self.assembler()
        self.power_chain()
        self.supply_input()
        self.context["geometry_snapshot"]["prototypes"]["test-pole"]["max_wire_distance"] = 4.9
        self.assertEqual(self.codes(), {"electric_consumer_unpowered"})

    def test_supply_uses_machine_footprint_and_rejects_outside_area(self):
        self.assembler()
        self.power_chain()
        self.supply_input()
        self.raw["entities"][0]["position"]["x"] = 21.5  # center outside, footprint overlaps
        self.assertEqual(self.outcome().status, "accepted_offline")
        self.raw["entities"][0]["position"]["x"] = 22.5  # touching edge gives no positive overlap
        self.assertEqual(self.codes(), {"electric_consumer_unpowered"})

    def test_existing_power_requires_explicit_known_powered_flag(self):
        self.assembler()
        self.supply_input()
        self.add_prototype("test-pole", type="electric-pole", energy_source="none",
                           supply_area_distance=2.5, max_wire_distance=7.5)
        row = {"prototype": "test-pole", "position": {"x": 18.5, "y": 10.5}, "direction": 0}
        self.context["facts"]["built_machines"]["value"].append(row)
        self.assertEqual(self.codes(), {"electric_consumer_unpowered"})
        row["powered"] = True
        # synchronize() owns a copy, so modify the current observed row.
        self.context["facts"]["built_machines"]["value"][-1]["powered"] = True
        self.assertEqual(self.outcome().status, "accepted_offline")

    def test_existing_unpowered_producer_is_not_a_plan_built_producer(self):
        self.assembler()
        self.power_chain()
        self.supply_input()
        self.raw["entities"] = [e for e in self.raw["entities"] if e["id"] != "solar"]
        stage = self.raw["stages"][0]
        stage["entity_ids"].remove("solar")
        stage["planned_bill"].pop("test-solar")
        self.context["facts"]["built_machines"]["value"].append({"prototype": "test-solar",
            "position": {"x": 6.5, "y": 10.5}, "direction": 0})
        self.assertEqual(self.codes(), {"electric_consumer_unpowered"})

    def test_later_or_independent_producer_cannot_power_first_stage(self):
        self.assembler()
        self.power_chain()
        self.supply_input()
        first = self.raw["stages"][0]
        first["entity_ids"].remove("solar")
        first["planned_bill"].pop("test-solar")
        power = {"id": "power", "depends_on": [], "entity_ids": ["solar"], "planned_bill": {"test-solar": 1},
                 "witnesses": [{"kind": "energy_generated_delta", "entity_id": "solar", "min_delta": 1}]}
        self.raw["stages"].append(power)
        self.assertEqual(self.codes(), {"electric_consumer_unpowered"})
        self.raw["stages"] = [power, first]
        self.assertEqual(self.codes(), {"electric_consumer_unpowered"})
        first["depends_on"] = ["power"]
        self.assertEqual(self.outcome().status, "accepted_offline")

    def test_recipe_category_from_list_or_keyed_facts_is_checked(self):
        self.assembler()
        self.power_chain()
        self.supply_input()
        self.context["geometry_snapshot"]["prototypes"]["assembling-machine-2"]["crafting_categories"] = ["smelting"]
        self.assertEqual(self.codes(), {"recipe_category_mismatch"})
        recipes = self.context["facts"]["recipes"]["value"]
        self.context["facts"]["recipes"]["value"] = {r["name"]: r for r in recipes}
        self.assertEqual(self.codes(), {"recipe_category_mismatch"})

    def test_inserter_from_known_fuel_inventory_can_supply_burner(self):
        self.raw["stages"][0]["manual_supplies"] = [s for s in self.raw["stages"][0]["manual_supplies"]
                                                  if s["entity_id"] != "first-furnace"]
        self.add_prototype("test-inserter", type="inserter", energy_source="none", pickup_vector=[0, -1], drop_vector=[0, 1])
        self.add_prototype("test-chest", type="container", energy_source="none")
        self.add_entity("feeder", "test-inserter", 12.5, 8.5)
        self.context["facts"]["built_machines"]["value"].append({"prototype": "test-chest", "position": {"x": 12.5, "y": 7.5},
            "direction": 0, "contents": {"coal": 10}})
        self.assertEqual(self.outcome().status, "accepted_offline")
        self.context["facts"]["built_machines"]["value"][-1]["contents"] = {}
        self.assertEqual(self.codes(), {"burner_fuel_unplanned"})

    def test_inserter_requires_a_source_at_its_pickup_and_input_never_grants_power(self):
        self.assembler()
        self.power_chain()
        self.add_prototype("test-inserter", type="inserter", energy_source="electric", pickup_vector=[0, -1], drop_vector=[0, 1])
        self.add_prototype("test-chest", type="container", energy_source="none")
        self.add_entity("input-inserter", "test-inserter", 20.5, 8.5)
        self.assertIn("crafting_input_unplanned", self.codes())
        self.add_entity("input-chest", "test-chest", 20.5, 7.5)
        self.assertEqual(self.outcome().status, "accepted_offline")

    def test_supplies_schema_is_strict_nullable_and_shared_with_submit_tool(self):
        stage_schema = proposal_schema()["properties"]["stages"]["items"]
        self.assertIn("manual_supplies", stage_schema["required"])
        supplies = stage_schema["properties"]["manual_supplies"]
        validate_wire(None, supplies)
        validate_wire([], supplies)
        self.assertEqual(supplies["items"]["required"], ["entity_id", "item", "slot"])
        submit = next(t for t in tool_schemas() if t["name"] == "submit_plan")
        self.assertEqual(submit["parameters"]["properties"]["stages"]["items"], stage_schema)
        for bad in ([{"entity_id": "first-drill", "item": "coal", "slot": "output"}],
                    [{"entity_id": "first-drill", "item": "coal", "slot": "fuel", "count": 1}]):
            with self.assertRaises(ContractError):
                validate_wire(bad, supplies)

    def test_old_wire_without_supplies_loads_and_null_roundtrips(self):
        self.raw["stages"][0].pop("manual_supplies")
        wire = to_wire(self.raw)
        self.assertIsNone(wire["stages"][0]["manual_supplies"])
        self.assertEqual(from_wire(wire), self.raw)
        wire["stages"][0].pop("manual_supplies")
        self.assertEqual(from_wire(wire), self.raw)
        self.assertIn("burner_fuel_unplanned", self.codes())

    def test_structural_supply_references_names_slots_and_duplicates_are_checked(self):
        self.synchronize()
        snapshot = parse_snapshot(self.context["geometry_snapshot"])
        for supply in ({"entity_id": "missing", "item": "coal", "slot": "fuel"},
                       {"entity_id": "first-drill", "item": "missing-item", "slot": "fuel"},
                       {"entity_id": "first-drill", "item": "coal", "slot": "output"}):
            changed = copy.deepcopy(self.raw)
            changed["stages"][0]["manual_supplies"] = [supply]
            with self.assertRaises(ContractError):
                parse_proposal(changed, snapshot)
        changed = copy.deepcopy(self.raw)
        changed["stages"][0]["manual_supplies"].append(changed["stages"][0]["manual_supplies"][0])
        with self.assertRaisesRegex(ContractError, "Duplicate manual supply"):
            parse_proposal(changed, snapshot)

    def test_tool_submission_returns_functional_diagnostics_and_remains_editable(self):
        self.raw["entities"][1]["position"]["x"] = 13
        self.synchronize()
        editor = DraftGridController(make_request(self.context, "Repair gap"), self.context)
        wire = to_wire(self.raw)
        for e in wire["entities"]:
            self.assertTrue(editor.call("place_entity", {"id": e["id"], "prototype": e["prototype"],
                "x": e["position"]["x"], "y": e["position"]["y"], "direction": e["direction"], "recipe": e["recipe"]})["ok"])
        args = {"plan_id": wire["plan_id"], "stages": wire["stages"]}
        before = editor.state
        result = editor.call("submit_plan", args)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "drill_output_unconnected")
        self.assertIn("drill_output_unconnected", {i["code"] for i in result["validation"]["issues"]})
        self.assertEqual(editor.state, before)
        self.assertTrue(editor.call("move_entity", {"id": "first-furnace", "x": 12, "y": 10, "direction": None})["ok"])
        self.assertTrue(editor.call("submit_plan", args)["ok"])

    def test_prototype_pole_producer_and_inserter_facts_cannot_be_omitted(self):
        self.assembler()
        self.power_chain()
        self.supply_input()
        self.add_prototype("test-inserter", type="inserter", energy_source="none",
                           pickup_vector=[0, -1], drop_vector=[0, 1])
        self.add_entity("inserter", "test-inserter", 30.5, 10.5)
        for prototype, field in (("test-pole", "supply_area_distance"), ("test-pole", "max_wire_distance"),
                                 ("test-solar", "produces_power"), ("test-inserter", "pickup_vector")):
            with self.subTest(prototype=prototype, field=field):
                original = copy.deepcopy(self.context)
                self.context["geometry_snapshot"]["prototypes"][prototype].pop(field)
                self.assertIn("prototype_facts_missing:" + prototype, self.codes())
                self.context = original

    def test_existing_pole_missing_facts_is_reported(self):
        self.assembler()
        self.supply_input()
        self.add_prototype("test-pole", type="electric-pole", energy_source="none", max_wire_distance=7.5)
        self.context["facts"]["built_machines"]["value"].append({"prototype": "test-pole",
            "position": {"x": 18.5, "y": 10.5}, "direction": 0, "powered": True})
        self.assertIn("prototype_facts_missing:test-pole", self.codes())

    def test_manual_supplies_may_reference_previous_entities_but_not_future_entities(self):
        self.raw = json.loads((FIXTURES / "proposal.json").read_text())
        second = self.raw["stages"][1]
        second["manual_supplies"].append({"entity_id": "first-drill", "item": "coal", "slot": "fuel"})
        self.assertEqual(self.outcome().status, "accepted_offline")
        self.raw["stages"][0]["manual_supplies"].append({"entity_id": "second-drill", "item": "coal", "slot": "fuel"})
        self.synchronize()
        with self.assertRaisesRegex(ContractError, "Manual supply entity is not in this or an earlier stage"):
            parse_design(to_wire(self.raw), self.context)

    def test_existing_matching_furnace_accepts_drill_drop(self):
        self.raw["entities"] = self.raw["entities"][:1]
        stage = self.raw["stages"][0]
        stage["entity_ids"] = ["first-drill"]
        stage["planned_bill"].pop("stone-furnace")
        stage["witnesses"] = [w for w in stage["witnesses"] if w["entity_id"] == "first-drill"]
        stage["manual_supplies"] = stage["manual_supplies"][:1]
        self.context["facts"]["built_machines"]["value"].append({"prototype": "stone-furnace",
            "position": {"x": 12, "y": 10}, "direction": 0, "recipe": "iron-plate"})
        self.assertEqual(self.outcome().status, "accepted_offline")

    def test_rich_design_acceptance_runs_the_same_functional_validator(self):
        from tests.test_editor import EditorTests
        fixture = EditorTests()
        fixture.setUp()
        context = fixture.rich_context()
        self.raw["stages"][0]["manual_supplies"] = []
        self.context = context
        self.synchronize()
        wire = to_wire(self.raw)
        wire.update(schema="designer-proposal-v2", circuit_wires=[], entity_configurations={
            e["id"]: {"quality": "normal", "recipe_quality": None, "modules": [], "settings": None}
            for e in wire["entities"]})
        with self.assertRaisesRegex(FunctionalValidationError, "burner_fuel_unplanned"):
            parse_design(wire, self.context)

    def test_supply_mutation_and_rehash_cannot_bypass_persisted_revalidation(self):
        document = self.outcome().accepted.checked()
        old_hash = document["design_sha256"]
        document["provider_proposal"]["stages"][0]["manual_supplies"] = []
        document["manifest"]["stages"][0]["manual_supplies"] = []
        document["design_sha256"] = sha256(document["manifest"])
        self.assertNotEqual(old_hash, document["design_sha256"])
        forged = AcceptedPlan(document, sha256(document))
        with self.assertRaisesRegex(ContractError, "burner_fuel_unplanned"):
            forged.checked()

    def test_optional_prototype_fact_types_are_validated_and_hashed(self):
        self.synchronize()
        snapshot = self.context["geometry_snapshot"]
        for key, bad in (("type", 5), ("energy_source", "magic"), ("produces_power", 1),
                         ("fuel_categories", "chemical"), ("drop_vector", [0]), ("pickup_vector", [0, True]),
                         ("supply_area_distance", -1), ("max_wire_distance", float("inf")),
                         ("mining_area_radius", True)):
            changed = copy.deepcopy(snapshot)
            changed["prototypes"]["burner-mining-drill"][key] = bad
            with self.subTest(field=key), self.assertRaises(ContractError):
                parse_snapshot(changed)
        original = parse_snapshot(snapshot).sha256
        changed = copy.deepcopy(snapshot)
        changed["prototypes"]["stone-furnace"]["produces_power"] = False
        self.assertNotEqual(parse_snapshot(changed).sha256, original)

    def test_missing_fact_diagnostics_are_safe_and_use_fixed_text(self):
        spec = self.context["geometry_snapshot"]["prototypes"]["burner-mining-drill"]
        spec.pop("type")
        self.synchronize()
        with self.assertRaises(FunctionalValidationError) as caught:
            parse_design(to_wire(self.raw), self.context)
        report = rejection_validation(to_wire(self.raw), caught.exception)
        self.assertEqual(report["issues"][0]["code"], "prototype_facts_missing:burner-mining-drill")
        self.assertIn("prototype facts", validation_message(report))
        self.assertNotIn("secret exception text", json.dumps(rejection_validation({}, ValueError("secret exception text"))))


if __name__ == "__main__":
    unittest.main()
