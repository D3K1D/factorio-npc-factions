"""Electric mining and belt-fed fuel as soon as there is power (companion/electric_start.py; the user, 2026-10-25:
furnaces and boilers fed coal by belt early, mining electric as soon as possible, burner drills gone)."""

import json
import unittest
from pathlib import Path
from unittest import mock

from companion import catalog_planner as cp
from companion import electric_start, goal_brain, outline, outpost
from companion.smelting import footprint

FIXTURE = Path(__file__).parent / "fixtures" / "bw-tight-bus.json"
ALL_RECIPES = [{"name": n} for p in cp.PURPOSES.values() for n in p.get("requires_recipes", [])]
RESOURCES = [{"resource": r, "rows": []} for r in ("iron-ore", "copper-ore", "coal", "stone")]
BURNERS = ["iron-smelting-start", "coal-loop", "stone-supply", "copper-smelting-start", "iron-smelting-expansion"]


def site(done=(), **extra):
    out = {"request_id": "1:9", "npc_id": 1, "tick": 60_000, "recipes": ALL_RECIPES, "resources": RESOURCES,
           "completed_purposes": list(done), "base": []}
    out.update(extra)
    return out


def fixture():
    cap = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return cap["site"], cap["world"]


class ClosingTests(unittest.TestCase):

    def setUp(self):
        # (an array its plates' income buys soon: the burner pairs close; one it cannot, below, F-14)
        patcher = mock.patch("companion.plate_lead.limiting", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_burner_pairs_stay_open_while_the_array_cannot_be_afforded(self):
        # (fresh run 61: 30 iron a minute for 35 minutes, burner pairs closed once the array could be built)
        powered = site(BURNERS + ["steam-power"])
        with mock.patch("companion.plate_lead.limiting", return_value="iron-plate"):
            self.assertTrue(cp.available("iron-smelting-expansion", powered))
            self.assertTrue(cp.available("copper-smelting-start", powered))
            self.assertFalse(cp.available("coal-supply", powered))             # (coal goes electric all the same)

    def test_before_power_burner_pairs_after_it_electric_smelting_never_both(self):
        before = site(BURNERS)
        self.assertTrue(cp.available("iron-smelting-expansion", before))
        self.assertFalse(cp.available("electric-iron-smelting", before))       # (no power to run it)
        powered = site(BURNERS + ["steam-power"])
        self.assertTrue(cp.available("electric-iron-smelting", powered))
        for burner in ("iron-smelting-expansion", "copper-smelting-start", "coal-loop", "coal-supply", "stone-supply",
                       "electric-iron-mining", "electric-copper-mining"):
            self.assertFalse(cp.available(burner, powered), burner)

    def test_electric_smelting_waits_for_its_research_and_burner_iron_runs_meanwhile(self):
        missing = [r for r in ALL_RECIPES if r["name"] not in ("long-handed-inserter", "underground-belt")]
        early = site(BURNERS + ["steam-power"], recipes=missing)
        self.assertFalse(cp.available("electric-iron-smelting", early))
        self.assertTrue(cp.available("iron-smelting-expansion", early))
        self.assertTrue(cp.available("electric-coal-supply", early))           # (no long inserters in it)
        self.assertFalse(cp.available("coal-loop", early))

    def test_a_burner_goal_waits_when_its_electric_one_waits_only_on_research_under_way(self):
        # (run 37: eight burner pairs on stone went up while the electric mining drill was being researched)
        missing = [r for r in ALL_RECIPES if r["name"] != "electric-mining-drill"]
        waiting = site(BURNERS + ["steam-power"], recipes=missing)
        self.assertTrue(cp.available("iron-smelting-expansion", waiting))
        # (research merely to be had, or under way but not far along: the burner goal stays open, F-21)
        self.assertTrue(cp.available("iron-smelting-expansion", {**waiting, "coming_recipes": ["electric-mining-drill"]}))
        coming = {**waiting, "research_near": ["electric-mining-drill"]}   # (under way and well along: it closes)
        self.assertFalse(cp.available("iron-smelting-expansion", coming))
        self.assertFalse(cp.available("electric-iron-smelting", coming))      # (not buildable yet itself)
        # its fuel goes on growing meanwhile: waiting for it is coal mined by hand (run 39)
        self.assertTrue(cp.available("coal-loop", coming))

    def test_an_electric_goal_that_failed_opens_the_burner_goal_again(self):
        failed = site(BURNERS + ["steam-power"], failed_purposes=["electric-iron-smelting"])
        self.assertTrue(cp.available("iron-smelting-expansion", failed))

    def test_a_built_replacement_counts_for_what_it_replaced(self):
        done = {"steam-power", "electric-iron-smelting", "electric-brick-smelting"}
        self.assertTrue(cp.has_built("electric-iron-mining", done))           # what the bus waits on
        self.assertTrue(cp.has_built("brick-smelting", done))                 # what the wall row waits on
        self.assertFalse(cp.has_built("copper-smelting-start", done))

    def test_with_a_bus_its_own_smelting_does_it_and_coal_still_goes_electric(self):
        stages = ["power", "smelting-iron-plate", "smelting-copper-plate"]
        bus = site(BURNERS + ["steam-power", "bus-base"],
                   base_plan={"stages": stages, "done": {"power": 1, "smelting-iron-plate": 1}})
        self.assertFalse(cp.available("electric-iron-smelting", bus))     # (the bus smelts iron now)
        self.assertTrue(cp.available("electric-copper-smelting", bus))    # (its copper only planned: not a supply, F-21)
        self.assertTrue(cp.available("electric-coal-supply", bus))

    def test_the_opening_goes_electric_right_after_power_its_lab_and_red_science(self):
        # (red science by machine right after the first lab, as a speedrunner does: after the electric smelting it
        # waited until minute 67, run 46, 2026-10-26)
        order = [p for p, _ in cp.OPENING]
        self.assertLess(order.index("research-lab"), order.index("red-science-automation"))
        self.assertLess(order.index("red-science-automation"), order.index("boiler-coal"))
        for goal in ("electric-coal-supply", "electric-iron-smelting", "electric-copper-smelting"):
            self.assertLess(order.index(goal), order.index("green-science-automation"), goal)
        self.assertEqual(cp.opening_goal(site(BURNERS + ["steam-power", "research-lab", "red-science-automation",
                                                         "boiler-coal"])), "electric-coal-supply")

    def test_more_of_a_plate_is_electric_smelting_once_there_is_power(self):
        self.assertEqual(cp.ELECTRIC_SUPPLY_GOALS["iron-plate"], "electric-iron-smelting")
        self.assertEqual(cp.ELECTRIC_SUPPLY_GOALS["stone-brick"], "electric-brick-smelting")


class PlanTests(unittest.TestCase):
    """On run 33's map (2026-10-25)."""

    @classmethod
    def setUpClass(cls):
        cls.site, cls.world = fixture()

    def test_smelting_is_belt_fed_its_feeds_first_and_it_retires_the_burner_pairs(self):
        s = {**self.site, "completed_purposes": BURNERS + ["steam-power", "electric-iron-mining"]}
        part = electric_start.part_size(s, "electric-iron-smelting")
        plan = electric_start.plan_smelting(s, self.world, "electric-iron-smelting")
        names = [st["id"] for st in plan["stages"]]
        self.assertEqual(names[-1], "electric-iron-smelting-furnaces")
        self.assertTrue(all(not st["manual_supplies"] for st in plan["stages"]))   # nothing carried to it
        last = plan["stages"][-1]
        kinds = [w["kind"] for w in last["witnesses"]]
        self.assertEqual(kinds, ["entity_built", "product_counter_delta"])
        self.assertEqual(last["witnesses"][1]["item"], "iron-plate")
        protos = [e["prototype"] for e in plan["entities"]]
        self.assertEqual(protos.count("stone-furnace"), part)                     # (its first part)
        self.assertTrue(electric_start.FURNACE_STEP <= part < 24)
        self.assertNotIn("burner-mining-drill", protos)
        self.assertGreaterEqual(protos.count("electric-mining-drill"), 4)          # ore for its furnaces, and coal
        # (no burner pairs stand in this site: nothing to cover first)
        self.assertEqual(set(plan["retire"]), {"iron-smelting-start", "iron-smelting-expansion", "electric-iron-mining"})
        self.assertEqual(len({e["id"] for e in plan["entities"]}), len(plan["entities"]))
        self.assertEqual(plan["keep"]["target"], 7.5)                             # laid out whole: an array of 24
        self.assertEqual(plan["keep"]["array"], 0)

    def grown(self, s, plan, world):
        """The site and map with `plan` built."""
        ox, oy = float(s["origin"]["x"]), float(s["origin"]["y"])
        sizes = {"electric-mining-drill": 3, "stone-furnace": 2, "iron-chest": 1}
        rows = [{"n": e["prototype"], "t": e["prototype"], "x": e["position"]["x"] + ox, "y": e["position"]["y"] + oy,
                 "d": e.get("direction", 0), "f": world.get("force"), "w": sizes.get(e["prototype"], 1),
                 "h": sizes.get(e["prototype"], 1)} for e in plan["entities"]]
        furnaces = sum(1 for e in plan["entities"] if e["prototype"] == "stone-furnace")
        block = {"purpose": plan["purpose"], "machines": {"stone-furnace": furnaces}, "keep": plan.get("keep")}
        done = list(s["completed_purposes"]) + [plan["purpose"]]
        return ({**s, "completed_purposes": done, "base": list(s.get("base") or []) + [block]},
                {**world, "entities": world["entities"] + rows})

    def test_a_block_grows_in_place_its_next_part_on_the_same_belts(self):
        # (the user, 2026-10-26: early on everything is made by hand, so small builds that do the job, grown later)
        s = {**self.site, "completed_purposes": BURNERS + ["steam-power"], "base_plan": {}}   # (before its bus)
        first = electric_start.plan_smelting(s, self.world, "electric-iron-smelting")
        s2, world2 = self.grown(s, first, self.world)
        self.assertTrue(cp.available("electric-iron-smelting", s2))                # (not whole yet)
        second = electric_start.plan_smelting(s2, world2, "electric-iron-smelting")
        protos = [e["prototype"] for e in second["entities"]]
        before = sum(1 for e in first["entities"] if e["prototype"] == "stone-furnace")
        self.assertTrue(0 < protos.count("stone-furnace") <= 24 - before)        # more of the array's 24
        self.assertEqual(protos.count("transport-belt"), 0)                       # its belts stand already
        self.assertEqual(second["keep"], first["keep"])                           # the same spot and fields
        s3, world3 = self.grown(s2, second, world2)
        for _ in range(4):   # (the rest of its parts, until the array is whole)
            if electric_start.grown(s3, "electric-iron-smelting"):
                break
            s3, world3 = self.grown(s3, electric_start.plan_smelting(s3, world3, "electric-iron-smelting"), world3)
        self.assertTrue(electric_start.grown(s3, "electric-iron-smelting"))
        self.assertAlmostEqual(electric_start.built_rate(s3, "electric-iron-smelting"), 7.5)
        self.assertFalse(cp.available("electric-iron-smelting", s3))      # (whole, and no more plates wanted)

    def test_the_game_keeps_a_block_s_spot_for_its_next_part(self):
        import test_clearing
        lua = test_clearing.game()
        lua.execute("""kept = DS.keep_copy({sites={'outpost-iron-plate-x0=-473,-405', 'field-iron-plate-x0=-408,-413,8,1',
                                                  'no good; at all', 42}, target=3.75})""")
        self.assertEqual(list(lua.eval("kept.sites").values()),
                         ["outpost-iron-plate-x0=-473,-405", "field-iron-plate-x0=-408,-413,8,1"])
        self.assertEqual(lua.eval("kept.target"), 3.75)
        self.assertIsNone(lua.eval("DS.keep_copy({sites={}})"))
        memory = (Path(__file__).resolve().parents[1] / "mod" / "scripts" / "base_memory.lua").read_text(encoding="utf-8")
        self.assertIn("keep=plan.keep", memory)                                    # (exported with its block)
        self.assertIn("machines_box=", memory)                                     # (its machines alone, for the ring)

    def test_the_burner_pairs_smelt_on_until_the_electric_parts_cover_them(self):
        pairs = [{"purpose": "iron-smelting-expansion", "machines": {"burner-mining-drill": 8, "stone-furnace": 8}}]
        s = {**self.site, "completed_purposes": BURNERS + ["steam-power"], "base": pairs}
        first = electric_start.plan_smelting(s, self.world, "electric-iron-smelting")
        self.assertNotIn("retire", first)                                         # 1.25/s against their 2
        s2, world2 = self.grown(s, first, self.world)
        second = electric_start.plan_smelting(s2, world2, "electric-iron-smelting")
        self.assertIn("iron-smelting-expansion", second["retire"])                # (3.75/s now)

    def test_before_underground_belts_are_researched_its_belts_go_round_what_is_in_the_way(self):
        # (run 37: electric smelting waited on logistics research for its two underground crossings)
        early = {**self.site, "recipes": [r for r in self.site["recipes"] if r["name"] != "underground-belt"]}
        plan = electric_start.plan_smelting(early, self.world, "electric-iron-smelting")
        self.assertNotIn("underground-belt", [e["prototype"] for e in plan["entities"]])
        self.assertNotIn("underground-belt", cp.PURPOSES["electric-iron-smelting"]["requires_recipes"])

    def test_bricks_take_two_stone_each(self):
        plan = electric_start.plan_smelting(self.site, self.world, "electric-brick-smelting")
        protos = [e["prototype"] for e in plan["entities"]]
        self.assertEqual(protos.count("stone-furnace"), 4)
        self.assertGreaterEqual(protos.count("electric-mining-drill"), 5)          # 2.5 stone/s, and coal
        self.assertNotIn("retire", plan)                                           # (no burner bricks stood)

    def test_a_supply_is_electric_drills_into_a_store_with_power_and_retires_its_burner_drills(self):
        s = {**self.site, "completed_purposes": BURNERS + ["steam-power"]}
        plan = electric_start.plan_supply(s, self.world, "electric-coal-supply")
        protos = [e["prototype"] for e in plan["entities"]]
        self.assertEqual(protos.count("electric-mining-drill"), 2)
        self.assertEqual(protos.count("iron-chest"), 1)
        self.assertIn("small-electric-pole", protos)
        kinds = [w["kind"] for w in plan["stages"][0]["witnesses"]]
        self.assertIn("resource_extraction_delta", kinds)
        self.assertEqual(set(plan["retire"]), {"coal-loop"})                       # (no coal chest drills stood)

    def test_no_drill_mines_two_ores(self):
        # (the user, 2026-10-26: a drill over iron and copper puts both on the belt and backs the line up)
        from companion.smelting import ore_tiles
        ox, oy = float(self.site["origin"]["x"]), float(self.site["origin"]["y"])
        s = {**self.site, "completed_purposes": BURNERS + ["steam-power"]}
        plans = [electric_start.plan_smelting(s, self.world, p) for p in
                 ("electric-iron-smelting", "electric-copper-smelting", "electric-brick-smelting")]
        plans += [electric_start.plan_supply(s, self.world, p) for p in ("electric-coal-supply", "electric-stone-supply")]
        ores = {name: ore_tiles(self.world["resources"], name) for name in self.world["resources"]}
        for plan in plans:
            drills = [e for e in plan["entities"] if e["prototype"] == "electric-mining-drill"]
            self.assertTrue(drills, plan["purpose"])
            for e in drills:   # (its ore field's and its coal field's alike)
                x0, y0 = int(e["position"]["x"] + ox - 1.5), int(e["position"]["y"] + oy - 1.5)
                area = {(x0 + i, y0 + j) for i in range(-1, 4) for j in range(-1, 4)}   # (the drill's 5x5)
                under = {name for name, tiles in ores.items() if area & tiles}
                self.assertEqual(len(under), 1, (plan["purpose"], e["id"], under))

    def test_no_two_of_its_buildings_share_a_tile(self):
        # (run 45: the ore field laid over its own coal belt; the game kept the drills, dropped six belts, and the
        # furnaces never got coal)
        from companion.modules import Placed
        s = {**self.site, "completed_purposes": BURNERS + ["steam-power"]}
        ox, oy = float(s["origin"]["x"]), float(s["origin"]["y"])
        for purpose in ("electric-iron-smelting", "electric-copper-smelting", "electric-brick-smelting"):
            plan = electric_start.plan_smelting(s, self.world, purpose)
            placed = [Placed(e["prototype"], e["position"]["x"] + ox, e["position"]["y"] + oy, e.get("direction", 0))
                      for e in plan["entities"]]
            self.assertIsNone(outpost._clash(placed), purpose)
        a, b = Placed("electric-mining-drill", 10.5, 10.5), Placed("transport-belt", 11.5, 10.5)
        self.assertIn("electric-mining-drill over transport-belt", outpost._clash([b, a]))

    def test_the_game_s_own_two_drill_layout_keeps_off_other_ore_too(self):
        import test_clearing
        lua = test_clearing.game()
        lua.execute("""
            local function res(name, category) return {name=name, prototype={resource_category=category}} end
            under = {}
            surface = {find_entities_filtered=function() return under end}
            mines = {['basic-solid']=true}
            function count() return DS.ore_under(surface, 'iron-ore', {x=0, y=0}, 2.49, mines) end
            for i = 1, 20 do under[i] = res('iron-ore', 'basic-solid') end
            pure = count()
            under[21] = res('crude-oil', 'basic-fluid')       -- (not what a mining drill mines)
            oil = count()
            under[22] = res('copper-ore', 'basic-solid')
            mixed = count()""")
        self.assertEqual((lua.eval("pure"), lua.eval("oil"), lua.eval("mixed")), (20, 20, 0))

    def test_its_buildings_keep_off_what_stands(self):
        from companion.base_plan import blocked_tiles
        from companion.modules import Placed
        taken = blocked_tiles(self.world)
        plan = electric_start.plan_supply(self.site, self.world, "electric-stone-supply")
        ox, oy = float(self.site["origin"]["x"]), float(self.site["origin"]["y"])
        for e in plan["entities"]:
            here = footprint([Placed(e["prototype"], e["position"]["x"] + ox, e["position"]["y"] + oy)])
            self.assertFalse(here & taken, e)


class WeighingTests(unittest.TestCase):

    def test_an_electric_goal_adds_no_hand_work_and_saves_what_it_replaces(self):
        base = [{"purpose": "iron-smelting-expansion", "machines": {"burner-mining-drill": 8, "stone-furnace": 8}}]
        s = site(BURNERS + ["steam-power"], base=base)
        rows = {r["goal"]: r for r in goal_brain.weigh(s, ["electric-iron-smelting", "iron-smelting-expansion"])}
        self.assertEqual(rows["electric-iron-smelting"]["hand_added"], 0.0)
        self.assertGreater(rows["electric-iron-smelting"]["hand_saved"], 0.0)
        self.assertGreater(rows["iron-smelting-expansion"]["hand_added"], 0.0)
        made, _ = electric_start.makes("electric-iron-smelting")
        self.assertEqual(made, {"iron-plate": 7.5})

    def test_belt_fed_blocks_are_no_hand_load(self):
        fed = site(base=[{"purpose": "electric-iron-smelting", "machines": {"stone-furnace": 12}}])
        hand = site(base=[{"purpose": "iron-smelting-expansion", "machines": {"stone-furnace": 12}}])
        self.assertEqual(goal_brain.hand_load(fed), 0.0)
        self.assertGreater(goal_brain.hand_load(hand), 0.0)


class ModuleAndOutlineTests(unittest.TestCase):

    def test_a_module_is_sized_to_its_rate_and_bricks_take_twice_the_ore(self):
        iron = outpost.modules(outpost.name("iron-plate", 0, 3.75))
        self.assertEqual([sum(1 for e in m.entities if e.name == "stone-furnace") for m in iron], [12])
        brick = outpost.modules(outpost.name("stone-brick", 0, 1.25))[0]
        port = next(p for p in brick.ports if p.item == "stone")
        self.assertAlmostEqual(port.rate, 2.5)
        full = outpost.modules(outpost.name("iron-plate", 1, 15))
        self.assertEqual([sum(1 for e in m.entities if e.name == "stone-furnace") for m in full], [24, 24])

    def test_the_architect_s_outline_plans_coal_by_belt_for_furnaces_and_boilers(self):
        s, world = fixture()
        chunks = outline.make(s, world, s.get("factory_plan"))["chunks"]
        fuel = next(c for c in chunks if c["id"] == "fuel")
        roles = [a["role"] for a in fuel["areas"]]
        self.assertIn("coal", roles)
        self.assertIn("boilers", roles)
        self.assertIn("by belt", fuel["why"])
        self.assertLess([c["id"] for c in chunks].index("fuel"), [c["id"] for c in chunks].index("edge-north--574"))


if __name__ == "__main__":
    unittest.main()
