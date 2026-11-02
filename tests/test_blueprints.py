"""Offline tests for the blueprint library and layout chooser (no network)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from companion import blueprints as bp
from companion import layout_planner
from companion.catalog_planner import DESIGN_NEW, CatalogPlanner
from companion.layout_planner import rotate, square_tiles

DRILL = {"type": "mining-drill", "width": 2, "height": 2, "placement_item": "burner-mining-drill",
         "energy_source": "burner", "mining_area_radius": 0.99, "drop_vector": [-0.5, -1.3]}
FURNACE = {"type": "furnace", "width": 2, "height": 2, "placement_item": "stone-furnace", "energy_source": "burner"}


def site(ore_x=42, ore_y=29, request="1:1", **extra):
    value = {
        "request_id": request, "npc_id": 1, "purpose": "iron-smelting-start", "bounds": [0, 0, 64, 64],
        "home": {"x": 32, "y": 32}, "prototypes": {"burner-mining-drill": DRILL, "stone-furnace": FURNACE},
        "inventory": {"burner-mining-drill": 1, "stone-furnace": 1, "iron-plate": 8},
        "recipes": [{"name": "burner-mining-drill"}, {"name": "stone-furnace"}],
        "resources": [{"resource": "iron-ore", "rows": [[y, ore_x, ore_x + 5] for y in range(ore_y, ore_y + 6)]}],
        "obstacles": [], "water_rows": {}, "built": {},
    }
    value.update(extra)
    return value


# The opening is over: a coal loop, stone, ten iron and four copper furnaces, power and a lab.
OPENED = {"completed_purposes": ["iron-smelting-start", "coal-loop", "stone-supply", "steam-power", "research-lab"],
          "base": [{"purpose": "iron-smelting-start", "recipes": ["iron-plate"], "machines": {"stone-furnace": 10}},
                   {"purpose": "copper-smelting-start", "recipes": ["copper-plate"],
                    "machines": {"stone-furnace": 4}}]}

# Its steam power runs: from then on a site with no fitting saved layout goes to the designer.
POWERED = {"completed_purposes": ["iron-smelting-start", "coal-loop", "stone-supply", "steam-power"]}


def drops_into_furnaces(plan):
    entities = {e["id"]: e for e in plan["entities"]}
    for stage in plan["stages"]:
        drill, furnace = (entities[i] for i in stage["entity_ids"])
        dx, dy = rotate((-0.5, -1.3), drill["direction"])
        px, py = drill["position"]["x"] + dx, drill["position"]["y"] + dy
        fx, fy = furnace["position"]["x"], furnace["position"]["y"]
        if not (fx - 1 <= px < fx + 1 and fy - 1 <= py < fy + 1):
            return False
    return True


class BlueprintTests(unittest.TestCase):
    def setUp(self):
        self.source = site()
        self.plan = layout_planner.plan(self.source)
        self.blueprint = bp.from_plan(self.plan, self.source, source="starter-layout-planner")

    def test_normalized_to_its_own_corner_with_drill_ore_requirements(self):
        footprint = self.blueprint["footprint"]
        self.assertEqual(min(x for x, _ in footprint), 0)
        self.assertEqual(min(y for _, y in footprint), 0)
        self.assertEqual(len(self.blueprint["resource_requirements"]), 2)
        self.assertTrue(all(len(r["tiles"]) == 4 for r in self.blueprint["resource_requirements"]))
        self.assertEqual(self.blueprint["outputs"], ["iron-plate"])

    def test_every_rotation_still_drops_into_the_furnaces(self):
        for turns in range(4):
            plan = bp.instantiate(self.blueprint, {"rotation": turns, "offset": [10, 10]}, "x")
            self.assertTrue(drops_into_furnaces(plan), turns)
        self.assertEqual(bp.rotated(bp.rotated(self.blueprint, 2), 2)["entities"], bp.rotated(self.blueprint, 0)["entities"])

    def test_fits_a_different_site_and_reproduces_a_working_plan(self):
        other = site(ore_x=8, ore_y=12, request="2:1")
        spots = bp.placements(self.blueprint, other)
        self.assertTrue(spots)
        plan = bp.instantiate(self.blueprint, spots[0], "2:1")
        ore = bp.tiles_from_rows(other["resources"][0]["rows"])
        for entity in plan["entities"]:
            if entity["prototype"] == "burner-mining-drill":
                self.assertLessEqual(square_tiles(entity["position"]["x"], entity["position"]["y"], 2), ore)
        self.assertTrue(drops_into_furnaces(plan))

    def test_a_saved_layout_is_placed_on_the_rich_part_of_a_patch(self):
        # A wide patch whose rich part is the far side from home (x 50..55).
        wide = site(ore_x=36, ore_y=26, request="3:1",
                    resources=[{"resource": "iron-ore", "rows": [[y, 36, 55] for y in range(26, 38)],
                                "rich_rows": [[y, 50, 55] for y in range(26, 38)]}])
        spot = bp.placements(self.blueprint, wide)[0]
        self.assertEqual(spot["thin"], 0)
        rich = bp.tiles_from_rows([[y, 50, 55] for y in range(26, 38)])
        for entity in bp.instantiate(self.blueprint, spot, "3:1")["entities"]:
            if entity["prototype"] == "burner-mining-drill":
                self.assertLessEqual(square_tiles(entity["position"]["x"], entity["position"]["y"], 2), rich)

    def test_a_goal_asked_for_by_name_must_be_ready_else_the_engineer_goes_on_with_its_own(self):
        from companion.catalog_planner import Scout, why_unavailable
        early = site(recipes=[{"name": n} for n in ("burner-mining-drill", "stone-furnace", "offshore-pump", "boiler",
                                                   "steam-engine", "pipe", "small-electric-pole")])
        self.assertEqual(why_unavailable("steam-power", early), "build first: copper-smelting-start")
        self.assertEqual(why_unavailable("power-plant", early).split(":")[0], "needs research first")
        self.assertIsNone(why_unavailable("iron-smelting-start", early))     # the opening is always fine
        # not refused (that held the engineer two minutes): its own next goal in the same request
        logs = []
        planner = CatalogPlanner(bp.Catalog(tempfile.mkdtemp()), log=logs.append)
        try:
            planner(dict(early, purpose="steam-power"))
        except Scout:
            pass   # (its next goal may be one whose resource it has yet to find: it looks)
        self.assertIn("steam-power is not ready (build first: copper-smelting-start)", logs[0])
        self.assertIn("next goal ", logs[1])                               # (its opening's step, weighed)
        # asked for "now", setting a build of its own aside: that build carries on instead (the game resumes
        # it on a deferral), never chosen afresh beside it (a second wall row went up, 2026-10-24)
        from companion.catalog_planner import Defer
        with self.assertRaises(Defer) as deferred:
            planner(dict(early, purpose="steam-power", paused="coal-loop", request_id="1:2"))
        self.assertIn("back to coal-loop", str(deferred.exception))

    def test_trees_never_block_but_water_does(self):
        trees = [{"name": "tree-01", "type": "tree", "mineable": True, "box": [x, y, x + 1, y + 1]}
                 for x in range(40, 50) for y in range(27, 37)]
        self.assertTrue(bp.placements(self.blueprint, site(obstacles=trees)))
        water = [[y, 0, 63] for y in range(0, 64)]
        self.assertFalse(bp.placements(self.blueprint, site(water_rows=water)))

    def test_own_buildings_are_movable_at_a_cost(self):
        crowded = site(ore_x=42, ore_y=29, built=[{"name": "stone-furnace", "position": {"x": x, "y": y}}
                                                  for x in range(41, 50, 2) for y in range(28, 37, 2)])
        spots = bp.placements(self.blueprint, crowded)
        self.assertTrue(spots)
        self.assertGreater(spots[0]["relocations"], 0)
        self.assertIn("requires moving", bp.describe({"blueprint": self.blueprint, "placement": spots[0]}))

    def test_library_persists_and_learns_from_outcomes(self):
        with tempfile.TemporaryDirectory() as folder:
            library = bp.Catalog(folder)
            library.add(self.blueprint)
            library.record(self.blueprint["id"], success=True, first_output_ticks=4800, complete_ticks=13000)
            reloaded = bp.Catalog(folder)
            stats = reloaded.blueprints[self.blueprint["id"]]["stats"]
            self.assertEqual((stats["uses"], stats["successes"], stats["first_output_ticks"]), (1, 1, [4800]))
            self.assertEqual(len(reloaded.candidates(site(ore_x=8, ore_y=12), "iron-smelting-start")), 1)
            self.assertEqual(reloaded.candidates(site(), "copper-smelting-start"), [])


class FakeChooser:
    def __init__(self, pick):
        self.pick, self.seen = pick, []

    def choose(self, site, options, question="layout"):
        self.seen.append([o["id"] for o in options])
        target = next(o["id"] for o in options if o["id"] == self.pick or o["id"].startswith(self.pick))
        return target, {o["id"]: (1.0 if o["id"] == target else 0.0) for o in options}


class CatalogPlannerTests(unittest.TestCase):
    def test_first_start_designs_then_later_starts_reuse(self):
        with tempfile.TemporaryDirectory() as folder:
            calls = []

            def designer(s):
                calls.append(s["request_id"])
                return layout_planner.plan(s)

            planner = CatalogPlanner(bp.Catalog(folder), designer=designer, chooser=FakeChooser("bp-"), log=lambda _: None)
            first = planner(site(request="1:1", **POWERED))
            self.assertEqual(calls, ["1:1"])
            self.assertEqual(len(planner.catalog), 1)
            planner.record("1:1", {"status": "complete", "accepted_tick": 100, "stage_completed_ticks": [4900, 13000],
                                   "completed_tick": 13000})
            second = planner(site(ore_x=8, ore_y=12, request="2:1", **POWERED))
            self.assertEqual(calls, ["1:1"], "a fitting saved layout must not call the designer")
            self.assertEqual(second["blueprint_id"], first["blueprint_id"])
            self.assertEqual(planner.chooser.seen[-1][-1], DESIGN_NEW)
            stats = planner.catalog.blueprints[first["blueprint_id"]]["stats"]
            self.assertEqual((stats["successes"], stats["first_output_ticks"]), (1, [4800]))

    def test_system_one_can_choose_a_new_design(self):
        with tempfile.TemporaryDirectory() as folder:
            library = bp.Catalog(folder)
            source = site(**POWERED)
            library.add(bp.from_plan(layout_planner.plan(source), source, source="starter-layout-planner"))
            designed = []
            planner = CatalogPlanner(library, designer=lambda s: designed.append(1) or layout_planner.plan(s, pairs=1),
                                     chooser=FakeChooser(DESIGN_NEW), log=lambda _: None)
            planner(site(request="3:1", **POWERED))
            self.assertEqual(designed, [1])
            self.assertEqual(len(library), 2)

    def test_a_slow_design_gives_way_to_the_layout_at_hand_and_is_kept_for_next_time(self):
        # (one-engineer hour 2026-10-24: Codex took over 120 s on a small lab design, the engineer standing by)
        import threading
        import time
        from unittest import mock
        from companion import catalog_planner
        with tempfile.TemporaryDirectory() as folder:
            go = threading.Event()

            def designer(s):
                go.wait(5)
                return layout_planner.plan(s, pairs=1)
            notes = []
            planner = CatalogPlanner(bp.Catalog(folder), designer=designer, chooser=FakeChooser(DESIGN_NEW),
                                     log=notes.append)
            with mock.patch.object(catalog_planner, "DESIGN_WAIT_S", 0.2):
                plan = planner(site(request="1:1", **POWERED))
            self.assertTrue(plan["entities"])
            self.assertTrue(any("building the layout at hand now" in n for n in notes), notes)
            self.assertEqual(len(planner.catalog), 1)                      # the layout at hand, as built
            go.set()
            deadline = time.monotonic() + 5
            while len(planner.catalog) < 2 and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(len(planner.catalog), 2)                      # and the design, for next time
            self.assertTrue(any("designed while the layout at hand was built" in n for n in notes), notes)

    def test_a_design_for_another_job_is_turned_down_for_the_built_in(self):
        # (one-engineer hour 2026-10-24: asked for a research lab before red science was unlocked, the designer
        # built one iron furnace; no lab was crafted, so red science was never unlocked)
        from companion.catalog_planner import off_the_job
        lab = {"id": "lab", "prototype": "lab", "position": {"x": 30.5, "y": 30.5}, "direction": 0}
        native = {"entities": [lab], "stages": [{"id": "lab", "entity_ids": ["lab"], "depends_on": [],
                                                 "manual_supplies": [], "witnesses": []}]}
        protos = {"burner-mining-drill": DRILL, "stone-furnace": FURNACE,
                  "lab": {"type": "lab", "width": 3, "height": 3, "placement_item": "lab"}}
        asked = site(request="1:6", purpose="research-lab", prototypes=protos, suggestions={"research-lab": native},
                     recipes=[{"name": n} for n in ("burner-mining-drill", "stone-furnace", "lab", "small-electric-pole")],
                     completed_purposes=POWERED["completed_purposes"] + ["copper-smelting-start"])
        iron = layout_planner.plan(site())
        self.assertEqual(off_the_job(iron, asked, "research-lab"), ["lab"])
        self.assertEqual(off_the_job(native, asked, "research-lab"), [])
        self.assertEqual(off_the_job(iron, asked, "coal-loop"), [])          # (a goal that names no machine)
        notes = []
        with tempfile.TemporaryDirectory() as folder:
            planner = CatalogPlanner(bp.Catalog(folder), designer=lambda s: layout_planner.plan(s),
                                     chooser=FakeChooser(DESIGN_NEW), log=notes.append)
            plan = planner(asked)
        self.assertEqual(plan["purpose"], "research-lab")
        self.assertEqual([e["prototype"] for e in plan["entities"]], ["lab"])
        self.assertTrue(any("does not build the job: none of lab" in n for n in notes), notes)

    def test_a_design_needing_what_cannot_be_made_yet_is_turned_down(self):
        # live5: every red science design powered itself with solar panels (not researched): the build
        # stalled on a locked recipe; such a design is now turned down for the built-in layout
        with tempfile.TemporaryDirectory() as folder:
            def designer(s):
                plan = layout_planner.plan(s)
                first = plan["stages"][0]
                panel = {"id": "panel", "prototype": "solar-panel", "position": {"x": 20.5, "y": 20.5},
                         "direction": 0}
                plan["entities"].append(panel)
                first["entity_ids"] = first["entity_ids"] + ["panel"]
                return plan
            notes = []
            planner = CatalogPlanner(bp.Catalog(folder), designer=designer, chooser=FakeChooser("bp-"),
                                     log=notes.append)
            plan = planner(site(request="1:1", **POWERED))
            self.assertNotIn("solar-panel", [e["prototype"] for e in plan["entities"]])
            self.assertTrue(any("cannot be made yet: solar-panel" in n for n in notes), notes)

    def test_before_power_the_built_in_layout_serves_without_the_designer(self):
        # live 2.1 game: 26 of 29 designs were simple drill and furnace rows at each new ore spot
        with tempfile.TemporaryDirectory() as folder:
            calls, notes = [], []
            planner = CatalogPlanner(bp.Catalog(folder), designer=lambda s: calls.append(s) or layout_planner.plan(s),
                                     chooser=FakeChooser(DESIGN_NEW), log=notes.append)
            plan = planner(site(request="1:1"))
            self.assertEqual(calls, [], "no designer before power")
            self.assertTrue(plan["entities"])
            self.assertTrue(any("built-in before power" in n for n in notes), notes)

    def test_before_power_ground_no_built_in_layout_fits_goes_to_the_designer(self):
        # one-tile ore streaks: no burner drill sits fully on ore, so the built-in layout cannot be made
        streaks = [{"resource": "iron-ore", "rows": [[y, 42, 42] for y in range(29, 35, 2)]}]
        with tempfile.TemporaryDirectory() as folder:
            calls, notes = [], []

            def designer(s):
                calls.append(s["request_id"])
                return layout_planner.plan(site())
            planner = CatalogPlanner(bp.Catalog(folder), designer=designer, chooser=FakeChooser("bp-"),
                                     log=notes.append)
            plan = planner(site(request="1:1", resources=streaks))
            self.assertEqual(calls, ["1:1"])
            self.assertTrue(any("asking the designer" in n for n in notes), notes)
            self.assertIn("blueprint_id", plan)
        # and when the designer fails there too, the engineer looks for better ore
        from companion.catalog_planner import Scout
        with tempfile.TemporaryDirectory() as folder:
            failing = CatalogPlanner(bp.Catalog(folder), designer=lambda s: 1 / 0, log=lambda _: None)
            with self.assertRaises(Scout):
                failing(site(request="1:2", resources=streaks))

    def test_without_designer_or_key_the_deterministic_layout_is_saved(self):
        with tempfile.TemporaryDirectory() as folder:
            planner = CatalogPlanner(bp.Catalog(folder), log=lambda _: None)
            plan = planner(site())
            self.assertEqual(len(planner.catalog), 1)
            self.assertTrue(drops_into_furnaces(plan))


class GoalLoopTests(unittest.TestCase):
    def goal_site(self, **extra):
        rows = lambda x, y: [[yy, x, x + 5] for yy in range(y, y + 6)]
        return site(request="1:2", purpose="choose",
                    prototypes={"burner-mining-drill": DRILL, "stone-furnace": FURNACE,
                                "wooden-chest": {"type": "container", "width": 1, "height": 1,
                                                 "placement_item": "wooden-chest", "energy_source": "none"}},
                    resources=[{"resource": "iron-ore", "rows": rows(42, 29)},
                               {"resource": "copper-ore", "rows": rows(42, 40)},
                               {"resource": "coal", "rows": rows(20, 40)}],
                    **{"completed_purposes": ["iron-smelting-start"], **extra})

    def test_the_opening_comes_first_whatever_system_one_would_pick(self):
        with tempfile.TemporaryDirectory() as folder:
            planner = CatalogPlanner(bp.Catalog(folder), chooser=FakeChooser("research-lab"),
                                     log=lambda _: None)
            plan = planner(self.goal_site())
            # one of the opening's burner steps, weighed (plates and fuel first: stone, copper, iron, the coal loop)
            self.assertIn(plan["purpose"], {"coal-loop", "copper-smelting-start", "iron-smelting-expansion"})
            self.assertTrue(any(e["prototype"] == "burner-mining-drill" for e in plan["entities"]))   # (no power yet)
            # copper, power and a lab soon, red science fed by hand while iron grows to OPENING_IRON furnaces
            # (the user's call, 2026-10-24: research running by about minute 15), then science by machines
            from companion.catalog_planner import OPENING_IRON

            power = [{"name": n} for n in ("offshore-pump", "boiler", "steam-engine", "pipe", "small-electric-pole",
                                           "lab")]

            def grown(furnaces, *built, recipes=power, block="iron-smelting-start"):
                return self.goal_site(completed_purposes=["iron-smelting-start", "coal-loop", "stone-supply",
                                                          "copper-smelting-start", *built],
                                      base=[{"purpose": block, "recipes": ["iron-plate"],
                                             "machines": {"stone-furnace": furnaces}},
                                            {"purpose": "copper-smelting-start", "recipes": ["copper-plate"],
                                             "machines": {"stone-furnace": 4}},
                                            # (its coal loop, making the burners' coal)
                                            {"purpose": "coal-loop", "machines": {"burner-mining-drill": 4},
                                             "capacity": {"makes": {"coal": 1.0}},
                                             "ledger": {"kind": "mining", "load": 1.0}}],
                                      recipes=recipes)
            # a pair or two of iron: iron first (power waits on its plates); a few: power (run 35, 2026-10-25)
            self.assertEqual(planner.choose_purpose(grown(2)), "iron-smelting-expansion")
            self.assertEqual(planner.choose_purpose(grown(4)), "steam-power")
            # power not unlocked yet: the time goes to iron, nothing waits on it
            self.assertEqual(planner.choose_purpose(grown(2, recipes=[])), "iron-smelting-expansion")
            # (once power runs, what has no saved layout is the designer's)
            from companion.catalog_planner import opening_goal
            self.assertEqual(opening_goal(grown(2, "steam-power"), designer=True), "research-lab")
            self.assertEqual(opening_goal(grown(OPENING_IRON - 2, "steam-power", "research-lab"), designer=True),
                             "iron-smelting-expansion")
            automated = power + [{"name": n} for n in ("assembling-machine-1", "inserter", "automation-science-pack")]
            self.assertEqual(opening_goal(grown(OPENING_IRON, "steam-power", "research-lab", recipes=automated),
                                          designer=True), "red-science-automation")
            # one burst of furnaces after the lab, even a furnace or two short: then red science, and the rest
            # of the iron after the science lines (a second burst held red science past minute 53, 2026-10-24)
            burst = grown(OPENING_IRON - 1, "steam-power", "research-lab", recipes=automated,
                          block="iron-smelting-expansion")
            self.assertEqual(opening_goal(burst, designer=True), "red-science-automation")
            self.assertEqual(opening_goal({**burst, "completed_purposes": burst["completed_purposes"]
                                           + ["red-science-automation"]}, designer=True), "iron-smelting-expansion")
            # what the steps not done yet wait on research for (its research goes there first)
            from companion.catalog_planner import wanted_recipes
            waits = wanted_recipes(burst)
            self.assertIn("electric-mining-drill", waits)                  # (electric mining, the boiler belt)
            self.assertIn("logistic-science-pack", waits)                  # (green science)
            self.assertNotIn("lab", waits)                                 # (unlocked already)
            self.assertEqual(wanted_recipes(None), set())

    def test_default_goal_is_the_first_new_resource(self):
        with tempfile.TemporaryDirectory() as folder:
            planner = CatalogPlanner(bp.Catalog(folder), log=lambda _: None)
            plan = planner(self.goal_site(**{**OPENED, "completed_purposes": ["iron-smelting-start", "coal-loop",
                                                                             "stone-supply"]}))
            self.assertEqual(plan["purpose"], "copper-smelting-start")
            self.assertTrue(drops_into_furnaces(plan))
            self.assertEqual(next(iter(planner.catalog.blueprints.values()))["purpose"], "copper-smelting-start")

    def test_system_one_can_defer_to_the_project_chain(self):
        from companion.catalog_planner import CONTINUE_PROJECTS, Defer
        with tempfile.TemporaryDirectory() as folder:
            planner = CatalogPlanner(bp.Catalog(folder), chooser=FakeChooser(CONTINUE_PROJECTS), log=lambda _: None)
            with self.assertRaises(Defer) as held:
                planner(self.goal_site(**OPENED))
            # with goals it could take up it holds off two minutes, not ten (idle beside open goals, 2026-10-25)
            self.assertEqual(held.exception.ticks, 7200)

    def test_an_unfinished_bus_is_continued(self):
        from companion.catalog_planner import CONTINUE_PROJECTS, PURPOSES, purpose_options
        needed = PURPOSES["bus-base"]["requires_recipes"]
        bus_site = {"request_id": "1:9", "recipes": [{"name": n} for n in needed], "resources": [],
                    "completed_purposes": OPENED["completed_purposes"] + ["red-science-automation", "electric-iron-mining",
                                                                          "electric-copper-mining", "bus-base"],
                    "base": OPENED["base"],
                    "base_plan": {"stages": ["smelting-iron-plate", "smelting-copper-plate"],
                                  "done": {"smelting-iron-plate": 100}}}
        with tempfile.TemporaryDirectory() as folder:
            planner = CatalogPlanner(bp.Catalog(folder), log=lambda _: None)
            self.assertEqual(planner.choose_purpose(bus_site), "bus-base")
            label = next(o for o in purpose_options(bus_site) if o["id"] == "bus-base")["description"]
            self.assertIn("next stage smelting-copper-plate (1 of 2 built)", label)
            bus_site["base_plan"]["done"]["smelting-copper-plate"] = 200
            self.assertEqual(planner.choose_purpose(bus_site), CONTINUE_PROJECTS)

    def test_coal_supply_drills_drop_into_chests(self):
        plan = layout_planner.plan(self.goal_site(), resource="coal", output="chest", name="coal-supply")
        entities = {e["id"]: e for e in plan["entities"]}
        for stage in plan["stages"]:
            drill, chest = (entities[i] for i in stage["entity_ids"])
            self.assertEqual(chest["prototype"], "wooden-chest")
            dx, dy = rotate((-0.5, -1.3), drill["direction"])
            px, py = drill["position"]["x"] + dx, drill["position"]["y"] + dy
            self.assertTrue(chest["position"]["x"] - 0.5 <= px < chest["position"]["x"] + 0.5)
            self.assertTrue(chest["position"]["y"] - 0.5 <= py < chest["position"]["y"] + 0.5)


class ResearchChoiceTests(unittest.TestCase):
    REQUEST = {"request_id": "1:r9", "npc_id": 1, "npc_name": "Ada", "options": [
        {"name": "automation", "units": 10, "packs": ["automation-science-pack"],
         "unlocks": ["assembling-machine-1", "long-handed-inserter"], "rocket_path": True},
        {"name": "logistics", "units": 75, "packs": ["automation-science-pack"],
         "unlocks": ["splitter", "underground-belt"], "rocket_path": True}]}

    def test_default_is_the_first_offered_option(self):
        with tempfile.TemporaryDirectory() as folder:
            planner = CatalogPlanner(bp.Catalog(folder), log=lambda _: None)
            self.assertEqual(planner.choose_research(self.REQUEST), "automation")

    def test_system_one_picks_research(self):
        # (with the weighing picking nothing: companion/research_brain.py takes the pick when it has one)
        from unittest import mock
        with tempfile.TemporaryDirectory() as folder, mock.patch("companion.research_brain.choose", return_value=None):
            planner = CatalogPlanner(bp.Catalog(folder), chooser=FakeChooser("logistics"), log=lambda _: None)
            self.assertEqual(planner.choose_research(self.REQUEST), "logistics")

    def test_bridge_forwards_research_choices_once(self):
        from companion.designer_bridge import DesignerBridge
        request = self.REQUEST
        commands = []

        class Rcon:
            def text(self, command):
                commands.append(command)
                if command == "/npc-plan observe":
                    return "NPC_PLAN_RESULT " + json.dumps({"ok": True, "requests": [], "research_requests": [request]})
                return "NPC_PLAN_RESULT " + json.dumps({"ok": True, "accepted": True})

        with tempfile.TemporaryDirectory() as folder:
            bridge = DesignerBridge(Rcon(), CatalogPlanner(bp.Catalog(folder), log=lambda _: None), log=lambda _: None)
            bridge.poll_once()
            bridge.poll_once()
        sent = [c for c in commands if c.startswith("/npc-plan research ")]
        self.assertEqual(len(sent), 1)
        self.assertEqual(json.loads(sent[0][len("/npc-plan research "):])["technology"], "automation")

    def test_science_automation_needs_power_lab_and_unlocks(self):
        from companion.catalog_planner import available
        base = site(recipes=[{"name": n} for n in ("assembling-machine-1", "inserter", "small-electric-pole",
                                                   "lab", "automation-science-pack")],
                    suggestions={"red-science-automation": {"entities": [], "stages": []}})
        self.assertFalse(available("red-science-automation", base))
        ready = {**base, "completed_purposes": ["iron-smelting-start", "copper-smelting-start", "steam-power",
                                                "research-lab"]}
        self.assertTrue(available("red-science-automation", ready))


if __name__ == "__main__":
    unittest.main()


class SocialContextTests(unittest.TestCase):
    SOCIAL = {"mood": {"stress": 10, "morale": 50, "anger": 55}, "recent_attacks": 3, "attackers": ["Ada"],
              "relations": [{"name": "Ada", "label": "enemy", "memories": ["Ada attacked you", "Ada stole from you"]},
                            {"name": "Player1", "label": "friend", "memories": []}]}

    def setUp(self):
        from companion import failures
        failures.forget()   # (a goal failed in one test is held in the next: SO-4's memory is the process's)

    def test_social_context_names_relationships_attacks_and_mood(self):
        from companion.catalog_planner import social_context
        text = social_context({"social": self.SOCIAL})
        self.assertIn("Ada (enemy: Ada attacked you; Ada stole from you)", text)
        self.assertIn("Player1 (friend)", text)
        self.assertIn("3 time(s) in the last 10 minutes by Ada", text)
        self.assertIn("Feeling angry.", text)
        self.assertEqual(social_context({}), "")
        self.assertEqual(social_context({"social": []}), "")

    def test_goal_question_carries_the_social_context(self):
        from companion.catalog_planner import DecisionsChooser
        seen = []

        class Provider:
            def decide(self, request):
                seen.append(request["context"])
                return {"selected_id": "x", "probabilities": {"x": 1.0}}

        DecisionsChooser(Provider()).choose({"npc_name": "Borin", "social": self.SOCIAL},
                                            [{"id": "x", "description": "x"}], question="goal")
        self.assertIn("Ada (enemy", seen[0])

    def test_defense_needs_turrets_and_repeats_only_when_threatened(self):
        from companion.catalog_planner import available
        base = site(recipes=[{"name": "gun-turret"}, {"name": "firearm-magazine"}],
                    suggestions={"basic-defense": {"entities": [], "stages": []}})
        self.assertTrue(available("basic-defense", base))
        self.assertFalse(available("basic-defense", {**base, "recipes": [{"name": "firearm-magazine"}]}))
        built = {**base, "completed_purposes": ["iron-smelting-start", "basic-defense"]}
        self.assertFalse(available("basic-defense", built))
        self.assertTrue(available("basic-defense", {**built, "social": self.SOCIAL}))

    def test_a_site_specific_goal_needs_the_games_own_layout_even_with_a_designer(self):
        # (one-engineer run 2026-10-24: the factory plan chose a second steam engine with no boiler free for
        # it, and it failed three times running; such goals are never designed)
        from companion.catalog_planner import available
        powered = site(recipes=[{"name": "steam-engine"}, {"name": "small-electric-pole"}],
                       completed_purposes=["iron-smelting-start", "steam-power"])
        self.assertFalse(available("power-expansion", powered, designer=True))
        offered = {**powered, "suggestions": {"power-expansion": {"entities": [], "stages": []}}}
        self.assertTrue(available("power-expansion", offered, designer=True))
        self.assertTrue(available("research-lab", {**powered, "recipes": [{"name": "lab"}, {"name": "small-electric-pole"}],
                                                   "completed_purposes": ["steam-power", "copper-smelting-start"]},
                                  designer=True))                       # (not site-specific: the designer's)

    def test_a_failed_goal_is_named_with_the_failure_not_choose(self):
        # (the same goal, chosen afresh each time and never noted as failed, failed again and again)
        from unittest import mock
        with tempfile.TemporaryDirectory() as folder:
            planner = CatalogPlanner(bp.Catalog(folder), log=lambda _: None)

            def failing(s):
                planner._purpose_now = "power-expansion"
                raise layout_planner.PlanError("no native layout for power-expansion near home")
            with mock.patch.object(planner, "_plan", failing):
                with self.assertRaises(layout_planner.PlanError) as failed:
                    planner(site(purpose="choose"))
        self.assertEqual(failed.exception.purpose, "power-expansion")

    def test_a_goal_asked_for_by_name_is_built_as_that_goal_not_as_iron(self):
        # (one-engineer hour 2026-10-24: red science and a defense asked for by the strategist fell to the
        # starter iron layout and stood as drills and furnaces under their names)
        turret = {"id": "t1", "prototype": "gun-turret", "position": {"x": 30, "y": 30}, "direction": 0}
        native = {"entities": [turret], "stages": [{"id": "guns", "entity_ids": ["t1"], "depends_on": [],
                                                    "manual_supplies": [], "witnesses": []}]}
        asked = site(request="1:7", purpose="basic-defense", recipes=[{"name": "gun-turret"}, {"name": "firearm-magazine"}],
                     completed_purposes=["iron-smelting-start"], suggestions={"basic-defense": native})
        with tempfile.TemporaryDirectory() as folder:
            plan = CatalogPlanner(bp.Catalog(folder), log=lambda _: None)(asked)
        self.assertEqual(plan["purpose"], "basic-defense")
        self.assertEqual([e["prototype"] for e in plan["entities"]], ["gun-turret"])


class RemoteSiteTests(unittest.TestCase):
    def crowded_site(self, **extra):
        # Iron is known only as a 2x2 scrap at home: the start pair fits, an expansion does not.
        return site(request="1:5", purpose="choose", completed_purposes=OPENED["completed_purposes"],
                    base=OPENED["base"],
                    resources=[{"resource": "iron-ore", "rows": [[29, 42, 43], [30, 42, 43]]}],
                    built=[{"name": "burner-mining-drill", "position": {"x": 43, "y": 30}}], **extra)

    def planner(self, folder, chooser=None):
        return CatalogPlanner(bp.Catalog(folder), chooser=chooser, log=lambda _: None)

    def test_known_patch_elsewhere_moves_the_planning_window(self):
        from companion.catalog_planner import MoveSite
        patches = [{"resource": "iron-ore", "dx": 70, "dy": -4, "tiles": 64, "amount": 256000}]
        with tempfile.TemporaryDirectory() as folder:
            planner = self.planner(folder, FakeChooser("iron-smelting-expansion"))
            with self.assertRaises(MoveSite) as moved:
                planner(self.crowded_site(known_patches=patches))
            self.assertEqual((moved.exception.purpose, moved.exception.dx, moved.exception.dy),
                             ("iron-smelting-expansion", 70, -4))

    def test_a_nearly_mined_out_patch_is_passed_over(self):
        from companion.catalog_planner import MoveSite, SPENT_PER_TILE
        patches = [{"resource": "iron-ore", "dx": 40, "dy": 0, "tiles": 64, "amount": 64 * SPENT_PER_TILE - 1},
                   {"resource": "iron-ore", "dx": 70, "dy": -4, "tiles": 64, "amount": 256000}]
        with tempfile.TemporaryDirectory() as folder:
            planner = self.planner(folder, FakeChooser("iron-smelting-expansion"))
            with self.assertRaises(MoveSite) as moved:
                planner(self.crowded_site(known_patches=patches))
            self.assertEqual((moved.exception.dx, moved.exception.dy), (70, -4))   # nearer, but nearly empty

    def test_no_known_patch_means_scouting(self):
        from companion.catalog_planner import Scout
        with tempfile.TemporaryDirectory() as folder:
            planner = self.planner(folder, FakeChooser("iron-smelting-expansion"))
            with self.assertRaises(Scout) as scout:
                planner(self.crowded_site())
            self.assertEqual(scout.exception.resource, "iron-ore")

    def test_goal_offered_for_a_resource_seen_only_elsewhere(self):
        from companion.catalog_planner import available
        base = self.crowded_site()
        self.assertFalse(available("copper-smelting-start", base))
        far = {**base, "known_patches": [{"resource": "copper-ore", "dx": -90, "dy": 10, "tiles": 40}]}
        self.assertTrue(available("copper-smelting-start", far))

    def test_moved_request_is_planned_for_the_chosen_goal(self):
        rows = [[yy, 40, 45] for yy in range(30, 36)]
        moved = site(request="1:6", purpose="iron-smelting-expansion", home={"x": -38, "y": 36},
                     resources=[{"resource": "iron-ore", "rows": rows}])
        with tempfile.TemporaryDirectory() as folder:
            plan = self.planner(folder)(moved)
        self.assertEqual(plan["purpose"], "iron-smelting-expansion")
        self.assertTrue(drops_into_furnaces(plan))

    def test_bridge_sends_site_and_scout_commands(self):
        from companion.catalog_planner import MoveSite, Scout
        from companion.designer_bridge import DesignerBridge
        commands = []
        outcomes = iter([MoveSite("iron-smelting-expansion", 70, -4, "goal"), Scout("copper-smelting-start", "copper-ore")])

        class Rcon:
            def __init__(self):
                self.request = 0

            def text(self, command):
                commands.append(command)
                if command == "/npc-plan observe":
                    self.request += 1
                    return "NPC_PLAN_RESULT " + json.dumps({"ok": True, "requests": [
                        {"request_id": f"1:{self.request}", "purpose": "choose"}]})
                return "NPC_PLAN_RESULT " + json.dumps({"ok": True, "accepted": True})

        def planner(request):
            raise next(outcomes)

        bridge = DesignerBridge(Rcon(), planner, log=lambda _: None)
        bridge.poll_once()
        bridge.poll_once()
        site_cmd = next(c for c in commands if c.startswith("/npc-plan site "))
        scout_cmd = next(c for c in commands if c.startswith("/npc-plan scout "))
        self.assertEqual(json.loads(site_cmd[len("/npc-plan site "):])["dx"], 70)
        self.assertEqual(json.loads(scout_cmd[len("/npc-plan scout "):])["resource"], "copper-ore")
        self.assertFalse(any(c.startswith("/npc-plan fail ") for c in commands))


class OilChainTests(unittest.TestCase):
    RECIPES = ["pumpjack", "small-electric-pole", "oil-refinery", "chemical-plant", "basic-oil-processing",
               "plastic-bar", "sulfur", "engine-unit", "assembling-machine-1", "chemical-science-pack", "inserter",
               "steam-engine"]

    def oil_site(self, done, suggested=("power-expansion", "oil-gathering", "oil-processing", "engine-assembly",
                                        "blue-science-automation")):
        return site(recipes=[{"name": n} for n in self.RECIPES], completed_purposes=list(done),
                    suggestions={p: {"entities": [], "stages": []} for p in suggested})

    def test_oil_chain_unlocks_in_order(self):
        from companion.catalog_planner import available
        start = self.oil_site(["iron-smelting-start", "steam-power"])
        self.assertTrue(available("oil-gathering", start))
        self.assertFalse(available("oil-processing", start))
        self.assertFalse(available("blue-science-automation", start))
        oil = self.oil_site(["iron-smelting-start", "steam-power", "oil-gathering"])
        self.assertTrue(available("oil-processing", oil))
        ready = self.oil_site(["iron-smelting-start", "steam-power", "oil-gathering", "oil-processing",
                               "red-science-automation", "steel-smelting", "engine-assembly"])
        self.assertTrue(available("blue-science-automation", ready))

    def test_default_order_adds_power_before_oil(self):
        from companion.catalog_planner import PURPOSES
        order = list(PURPOSES)
        self.assertLess(order.index("power-expansion"), order.index("oil-gathering"))
        self.assertLess(order.index("oil-processing"), order.index("blue-science-automation"))
        self.assertLess(order.index("engine-assembly"), order.index("blue-science-automation"))

    def test_unknown_oil_sends_the_engineer_scouting(self):
        from companion.catalog_planner import Scout, available
        base = self.oil_site(["iron-smelting-start", "steam-power"], suggested=())
        self.assertTrue(available("oil-gathering", base))
        known = {**base, "known_patches": [{"resource": "crude-oil", "dx": 90, "dy": 5, "tiles": 1}]}
        self.assertFalse(available("oil-gathering", known))  # known, but no layout fits: not now
        with tempfile.TemporaryDirectory() as folder:
            planner = CatalogPlanner(bp.Catalog(folder), chooser=FakeChooser("oil-gathering"), log=lambda _: None)
            with self.assertRaises(Scout) as scout:
                planner({**base, **OPENED, "completed_purposes": base["completed_purposes"] + OPENED["completed_purposes"],
                         "request_id": "1:30", "purpose": "choose"})
        self.assertEqual(scout.exception.resource, "crude-oil")

    def test_oil_processing_needs_plastic_or_sulfur_not_both(self):
        # (stage-oil, 2026-10-28: plastics researched, sulfur not; oil processing was never offered)
        from companion.catalog_planner import available, why_unavailable
        done = ["iron-smelting-start", "steam-power", "oil-gathering"]
        for unlocked in ("plastic-bar", "sulfur"):
            recipes = [n for n in self.RECIPES if n not in ("plastic-bar", "sulfur")] + [unlocked]
            oil = {**self.oil_site(done), "recipes": [{"name": n} for n in recipes]}
            self.assertTrue(available("oil-processing", oil), unlocked)
        neither = {**self.oil_site(done), "recipes": [{"name": n} for n in self.RECIPES
                                                      if n not in ("plastic-bar", "sulfur")]}
        self.assertFalse(available("oil-processing", neither))
        self.assertIn("plastic-bar or sulfur", why_unavailable("oil-processing", neither))

    def test_a_known_field_with_no_built_in_room_goes_to_the_designer(self):
        from companion.catalog_planner import available
        base = self.oil_site(["iron-smelting-start", "steam-power"], suggested=())
        known = {**base, "known_patches": [{"resource": "crude-oil", "dx": 90, "dy": 5, "tiles": 1}]}
        self.assertFalse(available("oil-gathering", known))                # rules only: not now
        self.assertTrue(available("oil-gathering", known, designer=True))  # a saved build or the designer may

        class Designer:
            def __init__(self): self.sites = []

            def __call__(self, s):
                self.sites.append(s)
                return {"plan_id": "oil-1", "entities": [{"id": "pumpjack", "prototype": "pumpjack",
                                                          "position": {"x": 40.5, "y": 40.5}, "direction": 0}],
                        "stages": [{"id": "oil", "depends_on": [], "entity_ids": ["pumpjack"],
                                    "manual_supplies": [], "witnesses": []}]}
        from companion import catalog_planner
        from companion.catalog_planner import MoveSite, PURPOSES
        designer = Designer()
        with tempfile.TemporaryDirectory() as folder:
            planner = CatalogPlanner(bp.Catalog(folder), designer=designer, log=lambda _: None)
            # The field is known but not in this site: the site moves there first (no design blind).
            with self.assertRaises(MoveSite) as moved:
                planner._resource_layout({**known, "request_id": "1:31"}, "1:31", "oil-gathering",
                                         PURPOSES["oil-gathering"],
                                         lambda s: catalog_planner.suggested_plan(s, "oil-gathering"))
            self.assertEqual((moved.exception.dx, moved.exception.dy), (90, 5))
            self.assertEqual(designer.sites, [])
            # There, with its wells in view and still no layout of the game's: the designer lays it out.
            there = {**known, "request_id": "1:32",
                     "resources": [{"resource": "crude-oil", "amount": 600000, "rows": [[40, 40, 40]]}]}
            plan = planner._resource_layout(there, "1:32", "oil-gathering", PURPOSES["oil-gathering"],
                                            lambda s: catalog_planner.suggested_plan(s, "oil-gathering"))
        self.assertEqual(len(designer.sites), 1)
        self.assertEqual(plan["entities"][0]["prototype"], "pumpjack")

    def test_a_block_built_with_plastic_grows_when_sulfur_unlocks(self):
        # (Codex review, DS-2: a plastic-only block counted as done, and sulfur was never added)
        from companion.catalog_planner import available
        done = ["iron-smelting-start", "steam-power", "oil-gathering", "oil-processing"]
        self.assertFalse(available("oil-processing", self.oil_site(done, suggested=())))
        self.assertTrue(available("oil-processing", self.oil_site(done, suggested=("oil-processing",))))
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] / "mod" / "scripts" / "designed_start.lua").read_text(encoding="utf-8")
        self.assertIn('["oil-processing"]={make=oil_processing_suggestion,grows=true}', source)
        self.assertIn("if grows and (not matched or not (new.plastic_plant or new.sulfur_plant)) then return nil end", source)

    def test_blue_supply_follows_blue_science(self):
        from companion.catalog_planner import available
        recipes = self.RECIPES + ["advanced-circuit"]
        done = ["iron-smelting-start", "steam-power", "oil-gathering", "oil-processing", "red-science-automation",
                "steel-smelting", "engine-assembly"]
        site_ = site(recipes=[{"name": n} for n in recipes], completed_purposes=done,
                     suggestions={"blue-supply-automation": {"entities": [], "stages": []}})
        self.assertFalse(available("blue-supply-automation", site_))
        self.assertTrue(available("blue-supply-automation",
                                  {**site_, "completed_purposes": done + ["blue-science-automation"]}))


class TaughtBlueprintTests(unittest.TestCase):
    ENTRY = {"id": 3, "label": "copper smelting", "player": "andre", "entities": [
        {"name": "electric-mining-drill", "x": 1.5, "y": 1.5, "direction": 8, "w": 3, "h": 3,
         "kind": "mining-drill", "item": "electric-mining-drill"},
        {"name": "stone-furnace", "x": 2.0, "y": 4.0, "direction": 0, "w": 2, "h": 2,
         "kind": "furnace", "item": "stone-furnace"}]}

    def test_a_taught_mining_layout_needs_ore_of_its_goal(self):
        from companion.taught import to_library
        blueprint = to_library(self.ENTRY)
        self.assertEqual(blueprint["purpose"], "copper-smelting-start")
        self.assertEqual(blueprint["source"], "player:andre")
        needs = [r["resource"] for r in blueprint["resource_requirements"]]
        self.assertEqual(needs, ["copper-ore"])

    def test_assemblers_are_filed_by_their_main_recipe(self):
        from companion.taught import purpose_of
        entry = {"label": "my circuits", "entities": [
            {"name": "assembling-machine-2", "recipe": "electronic-circuit", "kind": "assembling-machine"},
            {"name": "assembling-machine-2", "recipe": "copper-cable", "kind": "assembling-machine"},
            {"name": "assembling-machine-2", "recipe": "electronic-circuit", "kind": "assembling-machine"}]}
        self.assertEqual(purpose_of(entry, set()), "module:electronic-circuit")
        self.assertEqual(purpose_of({"label": "coal-supply v2", "entities": entry["entities"]}, {"coal-supply"}),
                         "coal-supply")

    def test_the_bridge_files_taught_blueprints_and_acknowledges_them(self):
        from companion.designer_bridge import DesignerBridge
        commands = []
        entry = self.ENTRY

        class Rcon:
            def text(self, command):
                commands.append(command)
                if command == "/npc-plan observe":
                    return "NPC_PLAN_RESULT " + json.dumps({"ok": True, "requests": [], "taught": [entry]})
                return "NPC_PLAN_RESULT " + json.dumps({"ok": True})

        with tempfile.TemporaryDirectory() as folder:
            planner = CatalogPlanner(bp.Catalog(folder), log=lambda _: None)
            DesignerBridge(Rcon(), planner, log=lambda _: None).poll_once()
            self.assertEqual(len(planner.catalog), 1)
        sent = [c for c in commands if c.startswith("/npc-plan learned ")]
        self.assertEqual(json.loads(sent[0][len("/npc-plan learned "):]), {"id": 3, "purpose": "copper-smelting-start"})
