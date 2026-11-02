"""Offline tests for the deterministic starter layout planner and plan bridge."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from companion import layout_planner
from companion.designer_bridge import DesignerBridge
from companion.layout_planner import PlanError, rotate, square_tiles


def site(**overrides):
    value = {
        "schema": "npc-site-v1", "request_id": "1:1", "bounds": [0, 0, 64, 64], "home": {"x": 32, "y": 32},
        "prototypes": {
            "burner-mining-drill": {"type": "mining-drill", "width": 2, "height": 2, "drop_vector": [-0.5, -1.3],
                                    "energy_source": "burner", "mining_area_radius": 0.99},
            "stone-furnace": {"type": "furnace", "width": 2, "height": 2, "energy_source": "burner"},
        },
        "resources": [{"resource": "iron-ore", "rows": [[y, 42, 47] for y in range(29, 35)]}],
        "obstacles": [], "water_rows": [], "built": [],
    }
    value.update(overrides)
    return value


def drop_point(entity):
    x, y = entity["position"]["x"], entity["position"]["y"]
    dx, dy = rotate((-0.5, -1.3), entity["direction"])
    return x + dx, y + dy


class LayoutPlannerTests(unittest.TestCase):
    def test_rotation_matches_factorio_directions(self):
        self.assertEqual(rotate((-0.5, -1.3), 0), (-0.5, -1.3))
        self.assertEqual(rotate((-0.5, -1.3), 4), (1.3, -0.5))
        self.assertEqual(rotate((-0.5, -1.3), 8), (0.5, 1.3))
        self.assertEqual(rotate((-0.5, -1.3), 12), (-1.3, 0.5))

    def test_each_drill_sits_on_ore_and_drops_into_its_furnace(self):
        plan = layout_planner.plan(site())
        entities = {e["id"]: e for e in plan["entities"]}
        ore = layout_planner.tiles_from_rows(site()["resources"][0]["rows"])
        self.assertEqual([s["id"] for s in plan["stages"]], ["pair-1", "pair-2"])
        occupied = set()
        for stage in plan["stages"]:
            drill_id, furnace_id = stage["entity_ids"]
            drill, furnace = entities[drill_id], entities[furnace_id]
            drill_tiles = square_tiles(drill["position"]["x"], drill["position"]["y"], 2)
            furnace_tiles = square_tiles(furnace["position"]["x"], furnace["position"]["y"], 2)
            self.assertLessEqual(drill_tiles, ore)
            px, py = drop_point(drill)
            fx, fy = furnace["position"]["x"], furnace["position"]["y"]
            self.assertTrue(fx - 1 <= px < fx + 1 and fy - 1 <= py < fy + 1)
            self.assertFalse((drill_tiles | furnace_tiles) & occupied)
            occupied |= drill_tiles | furnace_tiles
            self.assertEqual({s["slot"] for s in stage["manual_supplies"]}, {"fuel"})

    def test_a_coal_loop_feeds_each_drill_from_the_one_before(self):
        coal = site(resources=[{"resource": "coal", "rows": [[y, 40, 47] for y in range(28, 36)]}])
        plan = layout_planner.plan(coal, pairs=4, resource="coal", output="loop", name="coal-loop")
        drills = plan["entities"]
        self.assertEqual(len(drills), 4)
        ore = layout_planner.tiles_from_rows(coal["resources"][0]["rows"])
        for i, drill in enumerate(drills):
            self.assertLessEqual(square_tiles(drill["position"]["x"], drill["position"]["y"], 2), ore)
            nxt = drills[(i + 1) % 4]["position"]
            px, py = drop_point(drill)
            self.assertTrue(nxt["x"] - 1 <= px < nxt["x"] + 1 and nxt["y"] - 1 <= py < nxt["y"] + 1)
        stage = plan["stages"][0]
        self.assertEqual({m["slot"] for m in stage["manual_supplies"]}, {"fuel"})
        # A patch too thin for a square loop still gets two drills facing each other.
        thin = site(resources=[{"resource": "coal", "rows": [[y, 40, 41] for y in range(28, 36)]}])
        pair = layout_planner.plan(thin, pairs=4, resource="coal", output="loop")["entities"]
        self.assertEqual(len(pair), 2)
        self.assertTrue(abs(drop_point(pair[0])[1] - pair[1]["position"]["y"]) < 1)

    def test_drills_go_deep_into_the_patch_not_on_its_thin_edge(self):
        # Rich ore only in the far half of the patch (x 45..47); home is at x 32, on the thin side.
        rich = [[y, 45, 47] for y in range(29, 35)]
        plan = layout_planner.plan(site(resources=[{"resource": "iron-ore", "rows": [[y, 42, 47] for y in range(29, 35)],
                                                    "rich_rows": rich}]), pairs=1)
        drill = next(e for e in plan["entities"] if e["id"] == "drill-1")
        self.assertLessEqual(square_tiles(drill["position"]["x"], drill["position"]["y"], 2),
                             layout_planner.tiles_from_rows(rich))

    def test_unmineable_obstacles_are_avoided(self):
        rocks = [{"name": "huge-rock", "type": "simple-entity", "mineable": False, "box": [42, 29, 48, 32]}]
        plan = layout_planner.plan(site(obstacles=rocks))
        for entity in plan["entities"]:
            tiles = square_tiles(entity["position"]["x"], entity["position"]["y"], 2)
            self.assertFalse(tiles & layout_planner.box_tiles(rocks[0]["box"]))

    def test_no_ore_is_a_plan_error(self):
        with self.assertRaises(PlanError):
            layout_planner.plan(site(resources=[]))


class FakeRcon:
    def __init__(self, request):
        self.request = request
        self.commands = []

    def text(self, command):
        self.commands.append(command)
        if command == "/npc-plan observe":
            return "NPC_PLAN_RESULT " + json.dumps({"ok": True, "requests": [self.request]})
        return "NPC_PLAN_RESULT " + json.dumps({"ok": True, "accepted": command.startswith("/npc-plan submit")})


class DesignerBridgeTests(unittest.TestCase):
    def test_submits_once_per_request(self):
        rcon = FakeRcon(site())
        bridge = DesignerBridge(rcon, layout_planner.plan, log=lambda _: None)
        bridge.poll_once()
        bridge.poll_once()
        submits = [c for c in rcon.commands if c.startswith("/npc-plan submit ")]
        self.assertEqual(len(submits), 1)
        payload = json.loads(submits[0][len("/npc-plan submit "):])
        self.assertEqual(payload["request_id"], "1:1")
        self.assertEqual(bridge.handled["1:1"], "accepted")

    def test_a_plan_too_big_to_send_fails_its_request_not_the_link(self):
        # (run 27, 2026-10-25: a bus stage over the old 64 KB cap was taken for a lost game, planned again and again)
        from unittest import mock
        from companion import rcon as rcon_module
        fake = FakeRcon(site())
        bridge = DesignerBridge(fake, layout_planner.plan, log=lambda _: None)
        with mock.patch.object(rcon_module, "MAX_COMMAND", 200):
            bridge.poll_once()
        self.assertFalse(any(c.startswith("/npc-plan submit ") for c in fake.commands))
        fails = [json.loads(c[len("/npc-plan fail "):]) for c in fake.commands if c.startswith("/npc-plan fail ")]
        self.assertEqual(fails[0]["reason"], "plan_too_large")
        self.assertEqual(bridge.handled["1:1"], "failed")
        self.assertGreaterEqual(rcon_module.MAX_COMMAND, 1024 * 1024)       # (the game takes a megabyte)

    def test_planner_failure_reports_fail(self):
        rcon = FakeRcon(site(resources=[]))
        bridge = DesignerBridge(rcon, layout_planner.plan, log=lambda _: None)
        bridge.poll_once()
        self.assertTrue(any(c.startswith("/npc-plan fail ") for c in rcon.commands))
        self.assertEqual(bridge.handled["1:1"], "failed")


if __name__ == "__main__":
    unittest.main()
