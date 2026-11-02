"""The bus planned whole and built in steps (F-18): its whole planned ground kept from every other goal from its first
stage on, and its first lane carrying plates counted as a milestone of its own."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion import bus_planner, pace, plan_keeper  # noqa: E402


class ZoneTests(unittest.TestCase):
    def test_a_box_a_stage_from_its_buildings_tiles_overlaps_merged_far_sites_apart(self):
        from companion.modules import Placed
        stage = lambda *es: SimpleNamespace(entities=list(es))
        silo = Placed("rocket-silo", 100.5, 100.5)                     # (9x9: its edge, not its centre)
        layout = SimpleNamespace(stages=[stage(Placed("transport-belt", 10.5, 20.5)),
                                         stage(Placed("transport-belt", 11.5, 22.5)),
                                         stage(silo)])
        m = bus_planner.ZONE_MARGIN
        zones = bus_planner.planned_zones(layout)
        self.assertEqual(len(zones), 2)                                 # (the far silo: a box of its own)
        self.assertIn([10 - m, 20 - m, 12 + m, 23 + m], zones)
        self.assertIn([96 - m, 96 - m, 105 + m, 105 + m], zones)
        many = [[k * 10, 0, k * 10 + 1, 1] for k in range(20)]
        self.assertEqual(len(bus_planner._merged(many)), bus_planner.MAX_ZONES)

    def test_kept_from_every_goal_but_the_bus_s_own_ore_and_all(self):
        from companion import land
        site = {"base_plan": {"stages": ["power"], "zones": [[0, 0, 4, 4]]}}
        self.assertEqual(plan_keeper.bus_zones(site, "electric-iron-smelting"), [[0.0, 0.0, 4.0, 4.0]])
        self.assertEqual(plan_keeper.bus_zones(site, "bus-base"), [])
        self.assertEqual(plan_keeper.bus_zones(site, "clear-area"), [])
        dump = {"reserved_hard": [[0, 0, 4, 4]], "resources": {"iron-ore": [[1, 0, 3]]}}
        self.assertIn((1, 1), land.reserved_tiles(dump))                # (its ore kept for its fields)

    def test_the_wall_ring_crosses_the_bus_s_planned_ground(self):
        src = (ROOT / "companion" / "perimeter.py").read_text(encoding="utf-8")
        ground = src[src.index("class _Ground"):][:2500]
        self.assertIn('"reserved_hard"', ground)

    def test_the_game_keeps_the_zones_with_the_bus(self):
        lua = (ROOT / "mod" / "scripts" / "designed_start.lua").read_text(encoding="utf-8")
        copy = lua[lua.index("local function base_plan_copy"):][:3000]
        self.assertIn("out.zones[#out.zones+1]={z[1],z[2],z[3],z[4]}", copy)
        self.assertIn('"aim","zones"}', lua)


class SectionGateTests(unittest.TestCase):
    def test_the_old_science_goes_with_the_last_labs_section_not_the_last_stage(self):
        names = ["bus-labs", "bus-labs-blue", "smelting-copper-plate-more"]
        self.assertTrue(bus_planner.last_labs(names, {"bus-labs": 1}, "bus-labs-blue"))
        self.assertFalse(bus_planner.last_labs(names, {}, "bus-labs"))
        self.assertFalse(bus_planner.last_labs(names, {"bus-labs": 1}, "smelting-copper-plate-more"))

    def test_every_labs_section_proves_a_running_lab_before_the_old_science_goes(self):
        stage = SimpleNamespace(name="bus-labs-blue")
        ents = [{"id": 1, "prototype": "transport-belt"}, {"id": 2, "prototype": "lab"}]
        self.assertEqual([(w["kind"], w["entity_id"]) for w in bus_planner._witnesses(stage, ents)],
                         [("entity_built", 2), ("power_connected", 2)])

    def test_the_power_stage_counts_what_stands_once(self):
        src = (ROOT / "companion" / "base_plan.py").read_text(encoding="utf-8")
        sizing = src[src.index("boilers = power_plant.boilers_needed"):][:200]
        self.assertIn("to_add", sizing)
        self.assertIn("to_add[:k + 1]", src[src.index('Stage("power"') - 800:src.index('Stage("power"')])

    def test_it_grows_once_its_science_runs(self):
        site = {"base_plan": {"stages": ["bus-labs", "bus-mall-logistics"], "done": {"bus-labs": 1}}}
        self.assertTrue(bus_planner.science_runs(site))
        self.assertFalse(bus_planner.science_runs({"base_plan": {"stages": ["bus-labs"], "done": {}}}))


class LaneGateTests(unittest.TestCase):
    """Every new tap asks the lane ledger first: held back where the game measured its lane short (F-18)."""

    def gate(self, fill=None, supply=12.0):
        sys.path.insert(0, str(ROOT / "tests"))
        from test_belt_lines import gears, measured
        from companion.bus import Bus
        bus = Bus(0, 0, 4, 40)
        bus.add_lane("iron-plate", belts=2, supply=supply)
        for name, side in (("gears", "east"), ("gears-2", "west"), ("gears-3", "east")):
            bus.attach(gears(name), side=side)
        stages = [SimpleNamespace(name=n, entities=e) for n, e in bus.segments()]
        layout = SimpleNamespace(bus=bus, stages=stages)
        done = {"gears": 1, "gears-2": 1}
        built = [e for s in stages[:2] for e in s.entities]
        ops = [op for op in bus_planner.belt_lines.bus_lanes(bus, built) if op["item"] == "iron-plate"]
        lines = [measured(op, {r: fill for _, r, _ in op["probes"]}) for op in ops] if fill is not None else []
        return bus_planner.lane_gate({"belt_lines": lines}, None, layout, done, stages[2])

    def test_a_tap_goes_in_where_its_belt_carries_enough(self):
        self.assertIsNone(self.gate(fill=0.35))            # (5.25/s a belt: 2/s taken, 2/s more asked)

    def test_a_tap_waits_where_the_game_measured_its_belt_short(self):
        item, why = self.gate(fill=0.1)                    # (1.5/s a belt, 2/s taken already)
        self.assertEqual(item, "iron-plate")
        self.assertIn("refused", why)
        self.assertIn("2.00/s asked", why)

    def test_by_design_alone_the_plan_s_own_sizing_stands(self):
        self.assertIsNone(self.gate(supply=3.0))           # (short by design, not measured: smelting_first's call)

    def test_the_bus_stage_waits_or_its_supply_comes_first(self):
        import copy
        from companion import layout_planner
        from test_belt_lines import fixture, measured
        site, world = fixture()
        site = copy.deepcopy(site)
        for s in ("smelting-copper-plate", "bus-gears", "bus-green-circuits", "bus-mall-logistics",
                  "bus-mall-machines", "bus-red-science", "smelting-iron-plate-more", "smelting-copper-plate-more"):
            site["base_plan"].setdefault("done", {})[s] = 1
        plan = bus_planner.plan_stage(site, world)
        self.assertEqual(plan["base_plan"]["stage"], "bus-green-science")
        self.assertNotIn(bus_planner.LANE_ASKS, plan)                    # (not for the game)
        lanes = [op for st in plan["stages"] for op in st.get("line_ops", []) if op.get("kind") == "bus"]
        site["belt_lines"] = [measured({**op, "claims": {k: v for k, v in op["claims"].items()
                                                         if k.startswith(("gears@", "green-circuits@"))}},
                                       {r: 0.05 for _, r, _ in op["probes"]}) for op in lanes]
        try:   # (a tight bus: no rest of a block; an expansion first where one is found, else it waits)
            plan = bus_planner.plan_stage(site, world)
            self.assertTrue(any(n.startswith("lane supply first") for n in plan.get("notes") or ()), plan.get("notes"))
            self.assertNotEqual(plan["base_plan"]["stage"], "bus-green-science")
        except layout_planner.PlanError as held:
            self.assertIn("lane supply first, bus-green-science waits", str(held))
            self.assertIn("(measured)", str(held))


class LaneGateLaterTests(unittest.TestCase):
    def test_a_plan_taken_later_asks_the_lanes_again(self):
        src = (ROOT / "companion" / "bus_planner.py").read_text(encoding="utf-8")
        later = src[src.index("def plan_in_background"):][:4000]
        self.assertIn("lane_answer(site, world, plan[\"base_plan\"][\"stage\"], *asks)", later)
        self.assertNotIn(bus_planner.LANE_ASKS, str(bus_planner.plan_stage.__doc__))

    def test_a_forced_plate_expands_whatever_its_own_count_says(self):
        src = (ROOT / "companion" / "bus_planner.py").read_text(encoding="utf-8")
        self.assertIn("if not building_short and plate != force:", src)


class FirstLaneTests(unittest.TestCase):
    def test_the_first_lane_is_plates_on_the_bus_not_its_power_row(self):
        power = {"base_plan": {"stages": ["power"], "done": {"power": 1}}}
        self.assertIn("main bus begun", pace.reached(power))
        self.assertNotIn("first bus lane", pace.reached(power))
        lane = {"base_plan": {"stages": ["power"], "done": {"power": 1, "smelting-iron-plate": 2,
                                                             "bus-gears": 3}}}
        self.assertIn("first bus lane", pace.reached(lane))
        self.assertEqual(pace.BY_NAME["first bus lane"].aim, 68)


if __name__ == "__main__":
    unittest.main()


class NearTests(unittest.TestCase):
    def test_the_game_exports_only_research_well_along_as_near(self):
        lua = (ROOT / "mod" / "scripts" / "designed_start.lua").read_text(encoding="utf-8")
        near = lua[lua.index("function DesignedStart.near"):][:500]
        self.assertIn("research_progress or 0)>=DesignedStart.NEAR", near)
        self.assertIn("research_near=DesignedStart.near(npc.force)", lua)


class WaterExportTests(unittest.TestCase):
    def test_the_map_export_counts_water_by_its_collision_mask(self):
        lua = (ROOT / "mod" / "scripts" / "base_map.lua").read_text(encoding="utf-8")
        self.assertIn('collision_mask="water_tile"', lua)   # (a mod's water named otherwise counts too, F-21)
