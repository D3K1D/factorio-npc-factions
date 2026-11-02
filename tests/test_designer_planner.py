"""Offline tests: game site export -> designer context -> validated plan (no network)."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from companion import layout_planner
from companion.designer_planner import CONTEXT_BUDGET, LLMPlanner, build_context, to_plan
from designer_llm.context import canonical_bytes
from designer_llm.proposal_schema import to_wire
from designer_llm.providers import ReplayProvider
from designer_llm.request import make_request
from designer_llm.workflow import propose

DRILL = {"type": "mining-drill", "width": 2, "height": 2, "placement_item": "burner-mining-drill",
         "energy_source": "burner", "fuel_categories": ["chemical"], "resource_categories": ["basic-solid"],
         "mining_area_radius": 0.99, "drop_vector": [-0.5, -1.3]}
FURNACE = {"type": "furnace", "width": 2, "height": 2, "placement_item": "stone-furnace",
           "energy_source": "burner", "fuel_categories": ["chemical"], "crafting_categories": ["smelting"]}


def site():
    # Shaped like the Lua export, including {} for empty Lua tables.
    return {
        "schema": "npc-site-v1", "request_id": "1:1", "npc_id": 1, "npc_name": "Ada",
        "world_id": "npc-world-123456789", "force": "npc-faction-1", "surface": "nauvis", "tick": 600,
        "game_version": "2.0.77", "mod_version": "0.7.0", "origin": {"x": -32, "y": -32},
        "bounds": [0, 0, 64, 64], "home": {"x": 32, "y": 32}, "actor_position": {"x": 31, "y": 31},
        "goal": "Start automatic iron plate production.",
        "inventory": {"iron-plate": 8, "wood": 1, "burner-mining-drill": 1, "stone-furnace": 1},
        "recipes": [
            {"name": "iron-plate", "category": "smelting", "handcraft": False,
             "ingredients": [{"name": "iron-ore", "amount": 1}], "products": [{"name": "iron-plate", "amount": 1}]},
            {"name": "stone-furnace", "category": "crafting", "handcraft": True,
             "ingredients": [{"name": "stone", "amount": 5}], "products": [{"name": "stone-furnace", "amount": 1}]},
        ],
        "researched": {},
        "prototypes": {"burner-mining-drill": DRILL, "stone-furnace": FURNACE},
        "resources": [{"resource": "iron-ore", "amount": 72000, "tile_count": 36,
                       "rows": [[y, 42, 47] for y in range(29, 35)]},
                      {"resource": "coal", "amount": 18000, "tile_count": 9, "rows": [[y, 25, 27] for y in range(36, 39)]}],
        "obstacles": [{"name": "tree-01", "type": "tree", "box": [35.6, 23.6, 36.4, 24.4], "mineable": True}],
        "water_rows": {}, "built": {},
    }


def wire(context, plan):
    stages = []
    for stage in plan["stages"]:
        bill = {}
        for entity in plan["entities"]:
            if entity["id"] in stage["entity_ids"]:
                bill[entity["prototype"]] = bill.get(entity["prototype"], 0) + 1
        stages.append({**{k: stage[k] for k in ("id", "depends_on", "entity_ids", "witnesses", "manual_supplies")},
                       "planned_bill": bill})
    snap = context["geometry_snapshot"]
    return {"schema": "designer-proposal-v1", "snapshot_sha256": context["geometry_snapshot_sha256"],
            "game_version": snap["game_version"], "source_hash": snap["source_hash"],
            "world_id": snap["world_id"], "force_id": snap["force_id"], "observed_tick": snap["observed_tick"],
            "quality": "normal", "plan_id": plan["plan_id"],
            "entities": [{"id": e["id"], "prototype": e["prototype"], "position": e["position"],
                          "direction": e["direction"]} for e in plan["entities"]],
            "stages": stages}


class DesignerPlannerTests(unittest.TestCase):
    def test_export_becomes_a_valid_native_context(self):
        context = build_context(site())
        self.assertEqual(context["evidence_mode"], "native_export")
        patches = context["facts"]["terrain"]["value"]["resource_patches"]["value"]
        self.assertEqual(len([p for p in patches if p["resource"] == "iron-ore"]), 6)
        self.assertEqual(context["geometry_snapshot"]["researched_technologies"], [])

    def test_in_the_team_world_other_plots_and_players_builds_are_occupied_ground(self):
        export = site()
        export["team"] = {"plot": [0, 0, 64, 40], "area": [-500, -500, 500, 500], "no_build": [[50, 0, 64, 10]]}
        boxes = build_context(export)["geometry_snapshot"]["occupied_boxes"]
        self.assertIn([-500.0, 40.0, 500.0, 500.0], boxes)       # below its plot: someone else's
        self.assertIn([50.0, 0.0, 64.0, 10.0], boxes)            # by a player's build
        self.assertEqual(boxes.index([50.0, 0.0, 64.0, 10.0]), 4)   # (first, ahead of trees and water)

    def accept(self, plan):
        context = build_context(site())
        request = make_request(context, "Start iron", request_id="npc1-r1", max_entities=12, max_stages=3)
        return propose(request, context, ReplayProvider(to_wire(wire(context, plan))), max_input_bytes=120_000)

    def test_a_working_layout_passes_designer_validation_and_round_trips(self):
        plan = layout_planner.plan(site())
        outcome = self.accept(plan)
        self.assertTrue(outcome.accepted, outcome.evidence.get("validation"))
        game_plan = to_plan(outcome.accepted.document)
        self.assertEqual([e["id"] for e in game_plan["entities"]], [e["id"] for e in plan["entities"]])
        self.assertEqual(game_plan["stages"][0]["manual_supplies"], plan["stages"][0]["manual_supplies"])

    def test_on_factorio_2_1_a_working_layout_still_passes(self):
        # (2026-10-23: every design failed on 2.1, "Invalid reference geometry snapshot")
        export = dict(site(), game_version="2.1.23")
        context = build_context(export)
        plan = layout_planner.plan(export)
        request = make_request(context, "Start iron", request_id="npc1-r1", max_entities=12, max_stages=3)
        outcome = propose(request, context, ReplayProvider(to_wire(wire(context, plan))), max_input_bytes=120_000)
        self.assertTrue(outcome.accepted, outcome.evidence.get("validation"))
        self.assertEqual(context["geometry_snapshot"]["game_version"], "2.1.23")

    def test_a_furnace_one_tile_off_is_rejected(self):
        plan = copy.deepcopy(layout_planner.plan(site()))
        furnace = next(e for e in plan["entities"] if e["id"] == "furnace-1")
        furnace["position"]["x"] -= 1
        outcome = self.accept(plan)
        self.assertFalse(outcome.accepted)
        self.assertIn("drill_output_unconnected", str(outcome.evidence.get("validation")))

    def test_fluid_facts_without_null_fields_still_validate(self):
        export = site()
        export["prototypes"]["boiler"] = {
            "type": "boiler", "width": 3, "height": 2, "placement_item": "boiler", "energy_source": "burner",
            "fuel_categories": ["chemical"],
            "fluid_boxes": [  # as Lua writes them: no "filter" / "max_underground_distance" when nil
                {"index": 1, "production": "input-output", "filter": "water",
                 "connections": [{"position": [-1, 0.5], "direction": 12, "type": "normal"},
                                 {"position": [1, 0.5], "direction": 4, "type": "normal"}]},
                {"index": 2, "production": "output",
                 "connections": [{"position": [0, -0.5], "direction": 0, "type": "normal"},
                                 {"position": [0, 0], "direction": 0, "type": "linked"}]}]}
        export["inventory"]["boiler"] = 1
        context = build_context(export)
        boxes = context["geometry_snapshot"]["prototypes"]["boiler"]["fluid_boxes"]
        self.assertIsNone(boxes[1]["filter"])
        self.assertEqual(len(boxes[1]["connections"]), 1)
        self.assertIsNone(boxes[0]["connections"][0]["max_underground_distance"])

    def test_a_modded_games_hundreds_of_recipes_are_trimmed_to_fit_and_a_layout_still_passes(self):
        # (the Biter World rehearsal: 861 unlocked recipes, 724 of them an incinerator mod's, 224 KB)
        export = site()

        def recipe(name, category, ingredients, product=None):
            return {"name": name, "category": category, "handcraft": category == "crafting",
                    "ingredients": [{"name": i, "amount": 1} for i in ingredients],
                    "products": [{"name": product or name, "amount": 1}]}
        export["recipes"] += [recipe("iron-gear-wheel", "crafting", ["iron-plate"]),
                              recipe("automation-science-pack", "crafting", ["iron-gear-wheel", "copper-plate"])]
        export["recipes"] += [recipe(f"incinerate-item-number-{n:04d}", "incineration", [f"modded-item-{n:04d}"],
                                     "incinerated-ash") for n in range(1500)]
        context = build_context(export)
        kept = {r["name"] for r in context["facts"]["recipes"]["value"]}
        self.assertLessEqual(len(canonical_bytes(context)), CONTEXT_BUDGET)
        self.assertTrue({"iron-plate", "iron-gear-wheel", "automation-science-pack", "stone-furnace"} <= kept)
        self.assertLess(len(kept), 1000)
        self.assertEqual(sorted(context["geometry_snapshot"]["unlocked_recipes"]), sorted(kept))
        request = make_request(context, "Start iron", request_id="npc1-r1", max_entities=12, max_stages=3)
        plan = layout_planner.plan(export)
        outcome = propose(request, context, ReplayProvider(to_wire(wire(context, plan))), max_input_bytes=120_000)
        self.assertTrue(outcome.accepted, outcome.evidence.get("validation"))
        self.assertEqual(len(build_context(site())["facts"]["recipes"]["value"]), 2)   # small sites: all kept

    def test_planner_uses_the_replay_provider_through_its_transport_seam(self):
        planner = LLMPlanner(transport=lambda **_: None, credential=lambda: "unused")
        self.assertEqual(planner.config.model, "gpt-6.1-sol")


if __name__ == "__main__":
    unittest.main()
