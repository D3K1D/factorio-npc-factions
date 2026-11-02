"""Steam and research feasibility with pinned vanilla 2.0.77 port geometry.

fluid-prototypes.json transcribes base/prototypes/entity/entities.lua. Footprints
are tile sizes (offshore-pump explicitly has tile_width/tile_height = 1). Boiler
water uses input-output per the designer contract: both water ports allow flow.
"""

import copy
import json
from pathlib import Path
import time
import unittest

from designer_llm._reference_contract import ContractError, Entity, parse_snapshot
from designer_llm.context import validate_context
from designer_llm.diagnostics import rejection_validation
from designer_llm.editor import DraftGridController, tool_schemas
from designer_llm.functional_validation import FunctionalValidationError, fluid_components, rotate_vector
from designer_llm.proposal_schema import proposal_schema, to_wire, validate_wire
from designer_llm.request import make_request
from designer_llm.rich_proposal import parse_design


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class FluidValidationTests(unittest.TestCase):
    def setUp(self):
        self.context = json.loads((FIXTURES / "context.json").read_text())
        self.raw = json.loads((FIXTURES / "proposal.json").read_text())
        self.prototypes = json.loads((FIXTURES / "fluid-prototypes.json").read_text())
        snapshot = self.context["geometry_snapshot"]
        snapshot["prototypes"].update(copy.deepcopy(self.prototypes))
        snapshot["items"].extend([*self.prototypes, "automation-science-pack"])
        self.context["facts"]["terrain"]["value"]["water"] = [[14, 15, 15, 16]]
        placements = [
            ("pump", "offshore-pump", 14.5, 16.5),
            ("pipe-1", "pipe", 14.5, 17.5), ("pipe-2", "pipe", 15.5, 17.5),
            ("pipe-3", "pipe", 16.5, 17.5), ("boiler", "boiler", 18.5, 17),
            ("engine", "steam-engine", 18.5, 13.5),
            ("pole", "small-electric-pole", 21.5, 14.5), ("lab", "lab", 23.5, 17.5),
        ]
        self.raw["entities"] = [{"id": eid, "prototype": name, "position": {"x": x, "y": y}, "direction": 0}
                                for eid, name, x, y in placements]
        self.raw["stages"] = [{
            "id": "research", "depends_on": [], "entity_ids": [p[0] for p in placements],
            "planned_bill": {"offshore-pump": 1, "pipe": 3, "boiler": 1, "steam-engine": 1,
                             "small-electric-pole": 1, "lab": 1},
            "manual_supplies": [{"entity_id": "boiler", "item": "coal", "slot": "fuel"},
                                {"entity_id": "lab", "item": "automation-science-pack", "slot": "input"}],
            "witnesses": [{"kind": "research_progress_delta", "entity_id": "lab", "min_delta": 1}],
        }]

    def synchronize(self):
        self.context.pop("geometry_snapshot_sha256", None)
        self.context = validate_context(self.context)
        self.raw["snapshot_sha256"] = self.context["geometry_snapshot_sha256"]

    def accept(self):
        self.synchronize()
        return parse_design(to_wire(self.raw), self.context)

    def codes(self):
        self.synchronize()
        wire = to_wire(self.raw)
        with self.assertRaises(FunctionalValidationError) as caught:
            parse_design(wire, self.context)
        report = rejection_validation(wire, caught.exception)
        return {issue["code"] for issue in report["issues"]}

    def remove(self, eid):
        entity = next(e for e in self.raw["entities"] if e["id"] == eid)
        self.raw["entities"].remove(entity)
        stage = next(s for s in self.raw["stages"] if eid in s["entity_ids"])
        stage["entity_ids"].remove(eid)
        name = entity["prototype"]
        stage["planned_bill"][name] -= 1
        if not stage["planned_bill"][name]:
            del stage["planned_bill"][name]
        stage["manual_supplies"] = [s for s in stage["manual_supplies"] if s["entity_id"] != eid]

    def test_connected_steam_power_and_research_accepted(self):
        geometry, manifest, _ = self.accept()
        self.assertEqual(len(geometry.entities), 8)
        self.assertEqual(manifest["stages"][0]["witnesses"][0]["kind"], "research_progress_delta")

    def test_missing_water_pipe_disconnects_boiler_and_disables_engine_power(self):
        self.remove("pipe-2")
        self.assertEqual(self.codes(), {"fluid_input_unconnected", "electric_consumer_unpowered"})

    def test_engine_fed_by_water_rejected(self):
        engine = next(e for e in self.raw["entities"] if e["id"] == "engine")
        engine.update(position={"x": 23.5, "y": 17.5}, direction=4)
        self.remove("lab")
        # Engine's west port meets a pipe adjacent to the boiler's east water port.
        self.raw["entities"].append({"id": "water-pipe", "prototype": "pipe",
                                    "position": {"x": 20.5, "y": 17.5}, "direction": 0})
        stage = self.raw["stages"][0]
        stage["entity_ids"].append("water-pipe")
        stage["planned_bill"]["pipe"] += 1
        stage["witnesses"] = [{"kind": "power_connected", "entity_id": "engine"}]
        self.assertTrue({"fluid_input_unconnected", "fluid_mixed"} <= self.codes())

    def test_pump_without_water_rejected(self):
        self.context["facts"]["terrain"]["value"]["water"] = []
        self.assertIn("pump_not_on_water", self.codes())

    def test_unknown_water_rejected(self):
        for mode in ("missing", "unknown-water", "unknown-terrain"):
            with self.subTest(mode=mode):
                original = copy.deepcopy(self.context)
                if mode == "missing":
                    del self.context["facts"]["terrain"]["value"]["water"]
                elif mode == "unknown-water":
                    wrapper = copy.deepcopy(self.context["facts"]["terrain"])
                    wrapper.update(knowledge="unknown", value=None)
                    self.context["facts"]["terrain"]["value"]["water"] = wrapper
                else:
                    self.context["facts"]["terrain"].update(knowledge="unknown", value=None)
                self.assertIn("pump_not_on_water", self.codes())
                self.context = original

    def test_pump_footprint_water_accepted(self):
        self.context["facts"]["terrain"]["value"]["water"] = [[14, 16, 15, 17]]
        self.accept()

    def test_all_four_layout_rotations_accepted(self):
        original_raw, original_context = copy.deepcopy(self.raw), copy.deepcopy(self.context)
        for direction in (0, 4, 8, 12):
            with self.subTest(direction=direction):
                self.raw, self.context = copy.deepcopy(original_raw), copy.deepcopy(original_context)
                for entity in self.raw["entities"]:
                    p = entity["position"]
                    x, y = rotate_vector((p["x"] - 20, p["y"] - 15), direction)
                    entity.update(position={"x": 20 + x, "y": 15 + y}, direction=direction)
                corners = [rotate_vector((x - 20, y - 15), direction)
                           for x in (14, 15) for y in (15, 16)]
                self.context["facts"]["terrain"]["value"]["water"] = [[
                    20 + min(p[0] for p in corners), 15 + min(p[1] for p in corners),
                    20 + max(p[0] for p in corners), 15 + max(p[1] for p in corners)]]
                self.accept()

    def test_lab_without_science_supply_rejected(self):
        self.raw["stages"][0]["manual_supplies"].pop()
        self.assertEqual(self.codes(), {"crafting_input_unplanned"})

    def test_lab_wrong_manual_supply_rejected(self):
        supply = self.raw["stages"][0]["manual_supplies"][-1]
        for item, slot in (("coal", "input"), ("automation-science-pack", "fuel")):
            with self.subTest(item=item, slot=slot):
                supply.update(item=item, slot=slot)
                self.assertEqual(self.codes(), {"crafting_input_unplanned"})

    def test_lab_unpowered_rejected(self):
        self.remove("pole")
        self.assertEqual(self.codes(), {"electric_consumer_unpowered"})

    def test_lab_science_inserter_requires_drop_into_lab_and_power(self):
        self.raw["stages"][0]["manual_supplies"].pop()
        snapshot = self.context["geometry_snapshot"]
        snapshot["items"].append("test-inserter")
        snapshot["prototypes"]["test-inserter"] = {
            "placement_item": "test-inserter", "width": 1, "height": 1,
            "type": "inserter", "energy_source": "electric", "pickup_vector": [0, -1], "drop_vector": [0, 1]}
        self.raw["entities"].append({"id": "science-inserter", "prototype": "test-inserter",
                                    "position": {"x": 23.5, "y": 15.5}, "direction": 0})
        stage = self.raw["stages"][0]
        stage["entity_ids"].append("science-inserter")
        stage["planned_bill"]["test-inserter"] = 1
        self.accept()
        self.raw["entities"][-1]["direction"] = 8
        self.assertEqual(self.codes(), {"crafting_input_unplanned"})
        self.raw["entities"][-1]["direction"] = 0
        self.remove("pole")
        self.assertEqual(self.codes(), {"electric_consumer_unpowered"})

    def test_boiler_still_requires_fuel(self):
        self.raw["stages"][0]["manual_supplies"].pop(0)
        self.assertEqual(self.codes(), {"burner_fuel_unplanned"})

    def test_research_witness_is_sufficient_flow(self):
        geometry, _, _ = self.accept()
        self.assertEqual(len(geometry.stages[0].witnesses), 1)

    def test_research_witness_requires_lab_no_item_and_positive_delta(self):
        original = copy.deepcopy(self.raw["stages"][0]["witnesses"][0])
        for changes in ({"entity_id": "engine"}, {"item": "coal"}, {"min_delta": 0}, {"min_delta": True}):
            with self.subTest(changes=changes):
                self.raw["stages"][0]["witnesses"][0] = {**original, **changes}
                with self.assertRaises(ContractError):
                    self.accept()

    def test_power_only_stage_accepts_engine_or_pole_connection_witness(self):
        self.remove("lab")
        for eid in ("engine", "pole"):
            with self.subTest(entity_id=eid):
                self.raw["stages"][0]["witnesses"] = [{"kind": "power_connected", "entity_id": eid}]
                self.accept()

    def test_lab_stage_still_requires_research_flow(self):
        self.raw["stages"][0]["witnesses"] = [{"kind": "power_connected", "entity_id": "pole"}]
        with self.assertRaisesRegex(ContractError, "lacks a planned native flow"):
            self.accept()

    def test_power_only_witness_requires_power_target(self):
        self.remove("lab")
        self.raw["stages"][0]["witnesses"] = [{"kind": "power_connected", "entity_id": "boiler"}]
        with self.assertRaisesRegex(ContractError, "lacks a planned native flow"):
            self.accept()

    def test_steam_engine_still_requires_power_production_flag(self):
        spec = self.context["geometry_snapshot"]["prototypes"]["steam-engine"]
        spec["produces_power"] = False
        self.assertIn("electric_consumer_unpowered", self.codes())
        del self.context["geometry_snapshot"]["prototypes"]["steam-engine"]["produces_power"]
        self.assertIn("prototype_facts_missing:steam-engine", self.codes())

    def test_missing_fluid_facts_rejected(self):
        del self.context["geometry_snapshot"]["prototypes"]["boiler"]["fluid_boxes"]
        self.assertEqual(self.codes(), {"prototype_facts_missing:boiler"})

    def split_stages(self):
        stage = self.raw["stages"][0]
        stage["id"] = "power"
        stage["entity_ids"].remove("lab")
        del stage["planned_bill"]["lab"]
        stage["manual_supplies"].pop()
        stage["witnesses"] = [{"kind": "power_connected", "entity_id": "engine"}]
        self.raw["stages"].append({"id": "research", "depends_on": ["power"], "entity_ids": ["lab"],
            "planned_bill": {"lab": 1}, "manual_supplies": [{"entity_id": "lab", "item": "automation-science-pack", "slot": "input"}],
            "witnesses": [{"kind": "research_progress_delta", "entity_id": "lab", "min_delta": 1}]})

    def test_dependency_stage_can_supply_fluids_and_power(self):
        self.split_stages()
        self.accept()

    def test_independent_or_later_stage_cannot_supply_power(self):
        self.split_stages()
        self.raw["stages"][1]["depends_on"] = []
        self.assertIn("electric_consumer_unpowered", self.codes())
        self.raw["stages"].reverse()
        self.assertIn("electric_consumer_unpowered", self.codes())

    def test_existing_machines_can_supply_fluids_and_power(self):
        existing = self.context["facts"]["built_machines"]["value"]
        for eid in ("pump", "pipe-1", "pipe-2", "pipe-3", "boiler", "engine", "pole"):
            entity = next(e for e in self.raw["entities"] if e["id"] == eid)
            existing.append({k: copy.deepcopy(entity[k]) for k in ("prototype", "position", "direction")})
            if eid == "engine":
                existing[-1]["powered"] = True
            self.remove(eid)
        self.accept()
        existing = self.context["facts"]["built_machines"]["value"]
        next(e for e in existing if e["prototype"] == "pipe" and e["position"]["x"] == 16.5)["position"]["y"] += 1
        self.assertIn("electric_consumer_unpowered", self.codes())

    def test_existing_pump_requires_known_water(self):
        entity = self.raw["entities"][0]
        self.context["facts"]["built_machines"]["value"].append(
            {k: copy.deepcopy(entity[k]) for k in ("prototype", "position", "direction")})
        self.remove("pump")
        self.context["facts"]["terrain"]["value"]["water"] = []
        self.assertIn("fluid_input_unconnected", self.codes())

    def test_existing_steam_engine_requires_known_powered_observation(self):
        entity = next(e for e in self.raw["entities"] if e["id"] == "engine")
        self.context["facts"]["built_machines"]["value"].append(
            {k: copy.deepcopy(entity[k]) for k in ("prototype", "position", "direction")})
        self.remove("engine")
        self.assertEqual(self.codes(), {"electric_consumer_unpowered"})
        self.context["facts"]["built_machines"]["value"][-1]["powered"] = True
        self.accept()

    def test_underground_water_layout_accepted_within_range(self):
        self.raw["entities"][0]["position"]["x"] = 7.5  # Pump.
        self.raw["entities"][1]["position"]["x"] = 7.5  # First surface pipe.
        self.context["facts"]["terrain"]["value"]["water"] = [[7, 15, 8, 16]]
        for eid, x, direction in (("pipe-2", 8.5, 12), ("pipe-3", 16.5, 4)):
            entity = next(e for e in self.raw["entities"] if e["id"] == eid)
            entity.update(prototype="pipe-to-ground", position={"x": x, "y": 17.5}, direction=direction)
        self.raw["stages"][0]["planned_bill"].update({"pipe": 1, "pipe-to-ground": 2})
        self.accept()
        self.context["geometry_snapshot"]["prototypes"]["pipe-to-ground"]["fluid_boxes"][0]["connections"][1]["max_underground_distance"] = 7
        self.assertIn("fluid_input_unconnected", self.codes())

    def test_tool_submit_schema_and_editor_accept_research(self):
        self.synchronize()
        wire = to_wire(self.raw)
        validate_wire(wire, proposal_schema())
        args = {"plan_id": wire["plan_id"], "stages": wire["stages"]}
        schema = next(s for s in tool_schemas() if s["name"] == "submit_plan")
        validate_wire(args, schema["parameters"])
        controller = DraftGridController(make_request(self.context, "Steam power and research"), self.context)
        for entity in wire["entities"]:
            args_entity = {"id": entity["id"], "prototype": entity["prototype"],
                           **entity["position"], "direction": entity["direction"], "recipe": None}
            self.assertTrue(controller.call("place_entity", args_entity)["ok"])
        self.assertTrue(controller.call("submit_plan", args)["ok"])

    def test_fluid_facts_types_and_hashing(self):
        snapshot = self.context["geometry_snapshot"]
        original_hash = parse_snapshot(snapshot).sha256
        for field, value in (("requires_water", 1), ("fluid_boxes", {})):
            changed = copy.deepcopy(snapshot)
            changed["prototypes"]["offshore-pump"][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                parse_snapshot(changed)
        box_bad = ({"index": 0}, {"index": True}, {"production": "both"}, {"filter": 3},
                   {"connections": {}}, {"connections": [None]})
        connection_bad = ({"position": [0]}, {"position": [False, 0]}, {"direction": 1},
                          {"direction": False}, {"type": "surface"}, {"max_underground_distance": -1},
                          {"max_underground_distance": True}, {"max_underground_distance": float("inf")})
        for changes, connection in [(c, False) for c in box_bad] + [(c, True) for c in connection_bad]:
            changed = copy.deepcopy(snapshot)
            box = changed["prototypes"]["offshore-pump"]["fluid_boxes"][0]
            target = box["connections"][0] if connection else box
            target.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ContractError):
                parse_snapshot(changed)
        changed = copy.deepcopy(snapshot)
        boxes = changed["prototypes"]["offshore-pump"]["fluid_boxes"]
        boxes.append(copy.deepcopy(boxes[0]))
        with self.assertRaisesRegex(ContractError, "Duplicate fluid box"):
            parse_snapshot(changed)
        for mutate in (lambda p: p.update(requires_water=False),
                       lambda p: p["fluid_boxes"][0].update(filter=None)):
            changed = copy.deepcopy(snapshot)
            mutate(changed["prototypes"]["offshore-pump"])
            self.assertNotEqual(original_hash, parse_snapshot(changed).sha256)

    def test_underground_links_nearest_opposite_same_prototype_within_range(self):
        def components(placements):
            entities = {eid: Entity(eid, name, x, y, direction, None)
                        for eid, name, x, y, direction in placements}
            return fluid_components(entities, {eid: self.prototypes[e.prototype] for eid, e in entities.items()})[1]
        base = [("a", "pipe-to-ground", 0, 0, 0), ("b", "pipe-to-ground", 0, 10, 8)]
        self.assertIn(("b", 1), components(base)[("a", 1)])
        for second in (("b", "pipe-to-ground", 0, 11, 8), ("b", "pipe-to-ground", 1, 5, 8),
                       ("b", "pipe-to-ground", 0, 5, 0), ("b", "pipe", 0, 5, 8)):
            with self.subTest(second=second):
                self.assertNotIn(("b", 1), components([base[0], second])[("a", 1)])
        result = components([*base, ("near", "pipe-to-ground", 0, 5, 8)])
        self.assertIn(("near", 1), result[("a", 1)])
        self.assertNotIn(("b", 1), result[("a", 1)])

    def test_normal_links_require_mutual_targets_and_distinct_entities(self):
        specs = {"a": copy.deepcopy(self.prototypes["pipe"]), "b": copy.deepcopy(self.prototypes["pipe"])}
        entities = {"a": Entity("a", "pipe", 0, 0, 0, None), "b": Entity("b", "pipe", 1.009, 0, 0, None)}
        self.assertIn(("b", 1), fluid_components(entities, specs)[1][("a", 1)])
        specs["b"]["fluid_boxes"][0]["connections"] = [specs["b"]["fluid_boxes"][0]["connections"][1]]
        self.assertNotIn(("b", 1), fluid_components(entities, specs)[1][("a", 1)])
        specs["a"]["fluid_boxes"].append({**copy.deepcopy(specs["a"]["fluid_boxes"][0]), "index": 2})
        self.assertNotIn(("a", 2), fluid_components({"a": entities["a"]}, specs)[1][("a", 1)])

    def test_large_existing_pipe_line_is_connected_in_under_one_second(self):
        self.synchronize()
        existing = self.context["facts"]["built_machines"]["value"]
        line = {}
        for index in range(3000):
            position = {"x": 100.5 + index, "y": 100.5}
            existing.append({"prototype": "pipe", "position": position, "direction": 0})
            eid = "existing:" + str(index)
            line[eid] = Entity(eid, "pipe", position["x"], position["y"], 0, None)
        specs = {eid: self.prototypes["pipe"] for eid in line}
        started = time.perf_counter()
        nodes, components = fluid_components(line, specs)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 1.0, f"3000 pipes took {elapsed:.3f}s")
        component = components[("existing:0", 1)]
        self.assertEqual(len(component), 3000)
        self.assertEqual(component, set(nodes))
        self.assertTrue(all(value is component for value in components.values()))
        # Exercise functional validation alongside the planned layout without
        # serializing 3000 observations through the context's separate byte cap.
        parse_design(to_wire(self.raw), self.context)

    def test_normal_index_preserves_tolerance_across_bucket_boundaries(self):
        specs = {eid: self.prototypes["pipe"] for eid in ("a", "b")}
        for sign in (-1, 1):
            for offset in (0.009, 0.011):
                with self.subTest(sign=sign, offset=offset):
                    entities = {
                        "a": Entity("a", "pipe", sign * 0.015, sign * 0.015, 0, None),
                        "b": Entity("b", "pipe", sign * (1.015 + offset), sign * (0.015 + offset), 0, None),
                    }
                    component = fluid_components(entities, specs)[1][("a", 1)]
                    self.assertEqual(("b", 1) in component, offset < 0.01)

    def test_underground_index_preserves_line_tolerance_and_nearest_ties(self):
        # Equal-distance candidates retain the original lowest-port-index tie
        # break, including when searching in the negative axis direction.
        for direction in (0, 4, 8, 12):
            with self.subTest(direction=direction):
                positions = [(0.015, 0.015), (0.024, 5.015), (0.024, 5.015), (0.026, 4.015)]
                entities = {}
                for index, position in enumerate(positions):
                    x, y = rotate_vector(position, direction)
                    eid = str(index)
                    facing = direction if index == 0 else (direction + 8) % 16
                    entities[eid] = Entity(eid, "pipe-to-ground", x, y, facing, None)
                specs = {eid: self.prototypes["pipe-to-ground"] for eid in entities}
                component = fluid_components(entities, specs)[1][("0", 1)]
                self.assertIn(("1", 1), component)
                self.assertNotIn(("2", 1), component)
                self.assertNotIn(("3", 1), component)


if __name__ == "__main__":
    unittest.main()
