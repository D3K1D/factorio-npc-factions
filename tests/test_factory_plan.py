"""The factory plan (2026-10-24, Phase 3C): the architect plans an engineer's factory as large sections, the
game keeps the plan, and the planners build it. No AI is called here: the architect's Codex is faked, as is
System One."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

from lupa.lua52 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from companion import architect as arch  # noqa: E402
from companion import blueprints as bp  # noqa: E402
from companion import bus_planner, decisions_api, growth, land, mall  # noqa: E402
from companion import factory_plan as fp  # noqa: E402
from companion.base_plan import blocked_tiles, section_for  # noqa: E402
from companion.catalog_planner import PURPOSES, CatalogPlanner, Defer  # noqa: E402
from companion.ratios import Book  # noqa: E402

WORLD = json.loads((ROOT / "tests" / "fixtures" / "k-blue-map-256.json").read_text(encoding="utf-8"))
LATER = ("chemical-science-pack", "military-science-pack")   # (research not done yet in these tests)
RECIPES = [{"name": n} for p in PURPOSES.values() for n in p.get("requires_recipes", []) if n not in LATER] + [
    {"name": n} for n in ("automation-science-pack", "logistic-science-pack", "iron-gear-wheel", "electronic-circuit",
                          "copper-cable", "transport-belt", "inserter", "electric-mining-drill", "assembling-machine-1")]
# An engineer whose opening is done: power runs, and the next step is the main bus.
OPENED = {"request_id": "1:30", "npc_id": 1, "npc_name": "Ada", "purpose": "choose", "difficulty": "normal",
          "origin": {"x": 0, "y": 0}, "machines": {}, "tick": 500_000, "recipes": RECIPES,
          "researched": ["automation", "logistic-science-pack"],
          "completed_purposes": ["iron-smelting-start", "coal-loop", "stone-supply", "copper-smelting-start",
                                 "steam-power", "research-lab", "red-science-automation", "green-science-automation",
                                 "boiler-coal", "electric-iron-mining", "electric-copper-mining",
                                 "electric-coal-supply", "electric-iron-smelting", "electric-copper-smelting",
                                 "electric-stone-supply"],
          "base": [{"purpose": "coal-loop", "machines": {"burner-mining-drill": 4}},
                   {"purpose": "iron-smelting-expansion", "recipes": ["iron-plate"], "machines": {"stone-furnace": 12}},
                   {"purpose": "copper-smelting-start", "recipes": ["copper-plate"], "machines": {"stone-furnace": 6}}],
          "resources": [{"resource": r, "rows": []} for r in ("iron-ore", "copper-ore", "coal")]}
# Ada's blue-science save (test_modules.BusExtensionTests): her map at radius 256.
BLUE = {"request_id": "1:30", "difficulty": "normal", "origin": {"x": 0, "y": 0}, "machines": {}, "tick": 500_000,
        "researched": ["logistic-science-pack", "chemical-science-pack"],
        "completed_purposes": ["iron-smelting-start", "research-lab", "red-science-automation", "oil-processing",
                               "blue-science-automation"]}
BUS_CELLS = ["D5", "E5", "F5", "D6", "E6", "F6", "D7", "E7", "F7"]


class FakeCodex:
    """Stands in for codex_backend.Codex: queued answers, and the questions it was asked."""

    def __init__(self, *answers):
        self.answers, self.asked = list(answers), []

    def ask(self, prompt, schema, **kw):
        self.asked.append((prompt, schema, kw))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return copy.deepcopy(answer)


class FakeChooser:
    def __init__(self, pick=None, fail=False):
        self.pick, self.fail, self.seen = pick, fail, []

    def choose(self, site, options, question="layout"):
        self.seen.append((question, [o["id"] for o in options], [o["description"] for o in options]))
        if self.fail:
            raise RuntimeError("System One is down")
        return self.pick, {o["id"]: float(o["id"] == self.pick) for o in options}


def sec(id, kind, makes=(), zone=(), room=(), grow="copy", priority=0.5, load=None, why="", replaces=(), quality=None):
    return {"id": id, "kind": kind, "makes": [{"item": i, "per_second": r} for i, r in makes], "load": load,
            "zone": list(zone), "room": list(room), "grow": grow, "priority": priority, "why": why,
            "replaces": list(replaces), "quality": quality}


def answer(*plans):
    return {"plans": list(plans)}


def bus_plan(label="a bus south of home", rate=0.5, extra=(), zone=BUS_CELLS, room=("D8", "E8", "F8")):
    return {"label": label, "summary": "red and green on a bus south of home; iron ore kept for later",
            "sections": [sec("bus", "bus", zone=zone, room=room, priority=0.9, why="the main bus"),
                         sec("red", "science", [("automation-science-pack", rate)], priority=0.85),
                         sec("green", "science", [("logistic-science-pack", rate)], priority=0.8),
                         sec("iron", "smelting", [("iron-plate", 12)], priority=0.85),
                         sec("copper", "smelting", [("copper-plate", 8)], priority=0.85),
                         sec("mall", "mall", [("transport-belt", 4), ("inserter", 2), ("electric-mining-drill", 0.5)],
                             load=0.15, priority=0.6),
                         sec("outpost", "mining", [("iron-ore", 10)], zone=["D4"], priority=0.4, why="iron ore later"),
                         *extra],
            "lanes": [{"item": "plastic-bar", "belts": 1, "for": "blue"}],
            "keep": [{"cells": ["G5", "G6"], "for": "a solar field", "priority": 0.3}]}


def planner(codex=None, chooser=None, stored=None, logs=None):
    folder = tempfile.mkdtemp(prefix="npc-plan-test-")
    log = logs.append if logs is not None else (lambda _: None)
    p = CatalogPlanner(bp.Catalog(folder), log=log, chooser=chooser,
                       architect=arch.Architect(codex, log=log, chunked=False) if codex else None)
    p.world = lambda s: WORLD
    if stored is not None:
        p.factory_store = lambda npc, plan: stored.append((npc, json.loads(json.dumps(plan)))) or True
    return p


def as_stored(plan):
    """A plan as it comes back from the game (through JSON, as the game sends it)."""
    return json.loads(json.dumps(fp.bounded(plan)))


class LandTests(unittest.TestCase):
    def test_cells_name_the_ground_round_home_with_what_is_on_it(self):
        found = land.summarize(WORLD)
        home = next(c for c in found.cells if c.home)
        self.assertEqual(home.id, "E5")
        self.assertEqual(len(found.cells), land.MAX_CELLS)
        self.assertEqual(found.box(["E5"]), (-32, -32, 32, 32))
        self.assertEqual(found.box(["e5", "F6"]), (-32, -32, 96, 96))
        self.assertIsNone(found.box(["Z9"]))
        self.assertGreater(found.cell("D4").ore["iron-ore"], 0)
        self.assertLessEqual(sum(c.trees for c in found.cells), len(WORLD["trees"]))
        self.assertIn("E5 (home)", "\n".join(found.lines()))
        self.assertEqual(land.obstructions(WORLD, (-32, -32, 32, 32))["trees"], home.trees)

    def test_cells_that_do_not_touch_are_a_box_each_and_a_zone_round_others_is_no_ground_to_keep(self):
        # (run 25, 2026-10-25: a defense section's edge cells made one box the size of the plot, kept from every
        # other planner: the bus found no belt route to its own furnaces)
        found = land.summarize(WORLD)
        far = max(found.cells, key=lambda c: abs(c.box[0]) + abs(c.box[1])).id   # (a cell well off from home)
        self.assertEqual(found.boxes(["E5", "E6", far]), [(-32, -32, 32, 96), found.box([far])])
        self.assertEqual(found.boxes(["E5", "F6"]), [(-32, -32, 32, 32), (32, 32, 96, 96)])   # (corners only)
        edge = {"id": "edge-zone", "box": [-300, -300, 300, 300], "for": "perimeter"}
        smelting = {"id": "smelting-zone", "box": [-50, -50, 50, 50], "for": "bus-smelting"}
        oil = {"id": "oil-zone", "box": [100, 100, 160, 160], "for": "oil"}
        plan = {"sections": [{"id": "perimeter", "kind": "defense"}, {"id": "bus-smelting", "kind": "smelting"},
                             {"id": "oil", "kind": "oil"}], "zones": [edge, smelting, oil]}
        self.assertTrue(fp.region(edge, plan["zones"]))
        self.assertFalse(fp.region(oil, plan["zones"]))
        kept = fp.reserved_boxes(plan, "power-plant")
        self.assertNotIn(edge["box"], kept)
        self.assertIn(oil["box"], kept)
        # an L of cells is two rectangles, not the box round it (run 26: the edge cells met at a corner)
        ell = found.boxes(["E5", "E6", "F6"])
        self.assertEqual(len(ell), 2)
        self.assertEqual(sum((b[2] - b[0]) * (b[3] - b[1]) for b in ell), 3 * land.CELL ** 2)   # (just the cells)
        self.assertNotIn(found.box(["F5"]), ell)
        # a section placed by rule along the plot's edges keeps no cells at all
        wall = {"id": "wall-zone", "box": [-300, -300, -240, 300], "for": "perimeter"}
        ruled = {"sections": plan["sections"], "zones": [wall, oil]}
        self.assertEqual(fp.reserved_boxes(ruled, "power-plant"), [oil["box"]])

    def test_in_the_team_world_only_its_own_plot_is_listed(self):
        world = {"home": {"x": 0, "y": 0}, "area": [-256, -256, 256, 256], "entities": [], "water": [],
                 "resources": {}, "trees": [[10, 10], [200, 200]],
                 "team": {"area": [-600, -600, 600, 600], "plot": [-100, -100, 100, 100], "no_build": []}}
        found = land.summarize(world)
        self.assertTrue(found.cells)
        for c in found.cells:
            inside = max(0, min(c.box[2], 100) - max(c.box[0], -100)) * max(0, min(c.box[3], 100) - max(c.box[1], -100))
            self.assertGreaterEqual(inside, land.OWN_SHARE * land.CELL ** 2, c.id)
        self.assertEqual(sum(c.trees for c in found.cells), 1)   # the tree out at 200, 200 is not its ground

    def test_kept_ground_blocks_building_but_not_its_ore(self):
        dump = {**WORLD, "reserved": [[-96, -96, -32, -32]]}
        tiles = blocked_tiles(dump)
        ore = {(x, y) for rows in WORLD["resources"].values() for y, a, b in rows for x in range(a, b + 1)}
        inside = {(x, y) for x in range(-96, -32) for y in range(-96, -32)}
        self.assertTrue(inside - ore <= tiles)
        self.assertTrue(inside & ore)
        self.assertFalse(inside & ore & land.reserved_tiles(dump))   # mining fields still reach the ore
        self.assertFalse(land.reserved_tiles(WORLD))


class RulesPlanTests(unittest.TestCase):
    def test_the_rules_plan_builds_the_bus_as_it_is_built_without_a_plan(self):
        plan = fp.default_plan(BLUE)
        self.assertEqual((plan["source"], plan["version"]), ("rules", 1))
        targets = fp.bus_targets({**BLUE, "factory_plan": plan})
        self.assertEqual(targets.growth, growth.MAX_GROWTH)
        self.assertEqual(targets.reserve, bus_planner.LATER_LANES)
        without = bus_planner.plan_stage(BLUE, WORLD, Book.vanilla())
        with_plan = bus_planner.plan_stage({**BLUE, "factory_plan": plan}, WORLD, Book.vanilla())
        self.assertEqual(with_plan["base_plan"], without["base_plan"])
        self.assertEqual(len(with_plan["entities"]), len(without["entities"]))
        stored = as_stored(plan)
        self.assertLess(len(json.dumps(stored)), 8000)
        self.assertEqual(fp.bus_targets({**BLUE, "factory_plan": stored}), targets)   # the same once kept

    def test_where_a_bus_keeping_lanes_for_later_fits_nowhere_one_with_todays_lanes_goes_up(self):
        # (run 19, 2026-10-25: 26 columns, 20 of them for later goals, fit nowhere on its plot)
        from unittest import mock
        real = bus_planner.plan_bus_base
        full = bus_planner.plan_stage(BLUE, WORLD, Book.vanilla())["base_plan"]["columns"]

        def cramped(world, book, **kwargs):
            if kwargs["columns"] >= full:
                raise ValueError("no room for a bus near home")
            return real(world, book, **kwargs)
        with mock.patch.object(bus_planner, "plan_bus_base", cramped):
            lean = bus_planner.plan_stage(BLUE, WORLD, Book.vanilla())
        self.assertLess(lean["base_plan"]["columns"], full)
        self.assertTrue(any("a wider one fit nowhere" in n for n in lean.get("notes") or []))
        self.assertEqual(lean["base_plan"]["columns"], full - 1)            # (the widest that fits)
        with mock.patch.object(bus_planner, "plan_bus_base", side_effect=ValueError("no room for a bus near home")):
            with self.assertRaises(ValueError):
                bus_planner.plan_stage(BLUE, WORLD, Book.vanilla())

    def test_the_widths_are_tried_by_where_they_fit_and_the_bus_is_laid_out_once(self):
        # (run 55, 2026-10-26: every width tried was laid out in full, 48 layouts and 8 minutes for one stage)
        from unittest import mock
        real = bus_planner.plan_bus_base
        full = bus_planner.plan_stage(BLUE, WORLD, Book.vanilla())["base_plan"]["columns"]
        laid = []

        def cramped(world, book, **kwargs):
            if kwargs["columns"] >= full:
                raise ValueError("no room for a bus near home")
            if not kwargs.get("fit_only"):
                laid.append(kwargs["columns"])
            return real(world, book, **kwargs)
        with mock.patch.object(bus_planner, "plan_bus_base", cramped),                 mock.patch.object(bus_planner, "PLACES", 1):   # (no other places weighed: place_bus)
            lean = bus_planner.plan_stage(BLUE, WORLD, Book.vanilla())
        self.assertEqual(laid, [full - 1])
        self.assertEqual(lean["base_plan"]["columns"], full - 1)
        self.assertEqual(real(WORLD, Book.vanilla(), columns=full - 1, fit_only=True).stages, [])


class ArchitectTests(unittest.TestCase):
    def test_one_call_on_a_short_brief_and_its_plan_sets_the_bus_targets(self):
        codex, logs = FakeCodex(answer(bus_plan())), []
        site = {**OPENED, "space_path": {"target": "rocket-silo", "left": 30, "units": 20000,
                                         "packs": {"automation-science-pack": 5000, "chemical-science-pack": 4000},
                                         "next": ["sulfur-processing"]}}
        plan, how = arch.Architect(codex, log=logs.append)(site, WORLD, None, "the opening is done and power runs")
        prompt, schema, kw = codex.asked[0]
        self.assertEqual((kw["system"], kw["model"], kw["purpose"], kw["npc"]),
                         ("architect", arch.DESIGNER_MODEL, "the opening is done and power runs", 1))
        self.assertEqual(schema, arch.SCHEMA)
        self.assertLess(len(prompt), 8000)
        for part in ("E5 (home)", "Why you are asked now: the opening is done and power runs", "Path to space",
                     "the bus takes"):
            self.assertIn(part, prompt)
        self.assertNotRegex(prompt, "—")
        self.assertTrue(any("architect asked (the opening is done and power runs)" in line for line in logs))
        self.assertEqual(how, "one plan offered")
        zones = {z["id"]: z for z in plan["zones"]}
        self.assertEqual(zones["bus-zone"]["box"], [-96, -32, 96, 160])
        self.assertEqual(zones["bus-room"]["box"], [-96, 160, 96, 224])
        self.assertTrue(zones["outpost-zone"]["clear"] and zones["outpost-zone"]["left"]["trees"] > 0)
        self.assertEqual(zones["keep-1"]["use"], "keep")
        mall_ = fp.section(plan, "mall")
        self.assertIn("iron-plate", mall_["takes"])                 # worked out through the recipes
        self.assertTrue(any("needs chemical-science-pack" in n for n in plan["notes"]))
        site = {**site, "factory_plan": as_stored(plan)}
        targets = fp.bus_targets(site)
        self.assertEqual((targets.spm, targets.packs, targets.growth), (30.0, ("automation-science-pack",
                                                                               "logistic-science-pack"), 0))
        self.assertEqual(targets.mall, ("transport-belt", "inserter", "electric-mining-drill"))
        self.assertEqual(targets.area, (-96, -32, 96, 160))
        self.assertEqual(bus_planner.goal(site), (30.0, ("automation-science-pack", "logistic-science-pack")))
        kept_from_bus = fp.reserved_boxes(fp.plan_of(site), "bus-base")
        self.assertNotIn([-96, -32, 96, 160], kept_from_bus)
        self.assertIn(zones["outpost-zone"]["box"], kept_from_bus)
        self.assertIn([-96, -32, 96, 160], fp.reserved_boxes(fp.plan_of(site), "power-plant"))
        self.assertEqual(fp.reserved_boxes(fp.plan_of(site), "clear-area"), [])
        first = bus_planner.plan_stage(site, {**WORLD, "reserved": kept_from_bus}, Book.vanilla())
        saved = first["base_plan"]
        # (the plan's rate is what the bus aims at; the first bus makes half of it, growth the rest)
        self.assertEqual((saved["aim"], saved["spm"], saved["mall"]),
                         (30.0, 15.0, ["transport-belt", "inserter", "electric-mining-drill"]))
        self.assertTrue(-96 <= saved["x"] < 96 and -32 <= saved["y"] < 160, (saved["x"], saved["y"]))

    def test_the_checks_cut_what_fails_and_note_why(self):
        bad = {"label": "rough", "summary": "", "lanes": [{"item": "unobtainium", "belts": 2, "for": "x"}], "keep": [],
               "sections": [sec("bus", "bus", zone=["E5"], priority=0.9),
                            sec("red", "science", [("automation-science-pack", 2.0), ("unobtainium", 1), ("gear", -1)],
                                priority=0.8, quality="legendary"),
                            sec("far", "intermediate", [("electronic-circuit", 1)], zone=["Z9"], priority=0.5),
                            sec("clash", "intermediate", [("iron-gear-wheel", 1)], zone=["E5"], priority=0.3)]}
        found = land.summarize(WORLD)
        option = arch.check(bad, BLUE, found, WORLD, None, "a test")
        text = " | ".join(option.problems)
        for part in ("no recipe in this game makes unobtainium", "gear at -1 a second left out",
                     "quality legendary is not one of this game's", "zone cell Z9 is not its own ground",
                     "zone cell E5 is bus's", "lane for unobtainium", "nothing makes iron-plate",
                     "no builder for intermediate sections yet"):
            self.assertIn(part, text)
        self.assertEqual(fp.section(option.plan, "red")["makes"], {"automation-science-pack": 2.0})
        self.assertIsNone(fp.section(option.plan, "clash").get("zone"))
        self.assertEqual(option.plan["lanes"], [])
        self.assertLessEqual(len(option.plan["notes"]), fp.MAX_LINES)

    def test_system_one_chooses_among_the_plans_else_the_one_with_fewest_problems(self):
        rough = {**bus_plan("rough"), "sections": bus_plan()["sections"] + [sec("far", "other", zone=["Z9"])]}
        tidy = bus_plan("tidy")
        site = {**OPENED}
        chooser = FakeChooser("plan-1")
        plan, how = arch.Architect(FakeCodex(answer(rough, tidy)), log=lambda _: None)(site, WORLD, None, "r", chooser)
        self.assertEqual((plan["label"], how), ("rough", "system one"))
        question, ids, described = chooser.seen[0]
        self.assertEqual((question, ids), ("factory-plan", ["plan-1", "plan-2"]))
        self.assertIn("The checks found", described[0])
        plan, how = arch.Architect(FakeCodex(answer(rough, tidy)), log=lambda _: None)(site, WORLD, None, "r")
        self.assertEqual((plan["label"], how), ("tidy", "the rules: fewest problems"))
        plan, how = arch.Architect(FakeCodex(answer(rough, tidy)), log=lambda _: None)(
            site, WORLD, None, "r", FakeChooser(fail=True))
        self.assertEqual(plan["label"], "tidy")
        with self.assertRaises(arch.ArchitectError):
            arch.Architect(FakeCodex(answer({**tidy, "sections": []})), log=lambda _: None)(site, WORLD, None, "r")


class PlannerFlowTests(unittest.TestCase):
    def tearDown(self):
        decisions_api.resume_ai()

    def test_asked_once_when_the_opening_is_done_then_only_when_something_moves(self):
        blue = sec("blue", "science", [("chemical-science-pack", 0.5)], priority=0.75)
        codex, stored, logs = FakeCodex(answer(bus_plan()), answer(bus_plan(extra=[blue]))), [], []
        p = planner(codex, stored=stored, logs=logs)
        first = p(OPENED)
        self.assertEqual(len(codex.asked), 1)
        self.assertEqual(first["purpose"], "bus-base")
        self.assertEqual((first["base_plan"]["aim"], first["base_plan"]["spm"]), (30.0, 15.0))
        self.assertEqual(stored[-1][0], 1)
        self.assertEqual(stored[-1][1]["version"], 1)
        self.assertTrue(any("factory plan v1 by the architect (the opening is done and power runs" in l for l in logs))
        kept = stored[-1][1]
        base_plan = {**first["base_plan"], "done": {first["base_plan"]["stage"]: 1}}
        later = {**OPENED, "request_id": "1:31", "tick": OPENED["tick"] + 3600, "factory_plan": kept,
                 "base_plan": base_plan}
        p(later)
        self.assertEqual(len(codex.asked), 1)                     # nothing moved, and too soon anyway
        quiet = {**later, "request_id": "1:32", "tick": OPENED["tick"] + fp.COOLDOWN + 1,
                 "factory_plan": stored[-1][1]}
        p(quiet)
        self.assertEqual(len(codex.asked), 1)                     # long enough, but nothing moved
        blue_unlocked = {**quiet, "request_id": "1:33", "factory_plan": stored[-1][1],
                         "recipes": RECIPES + [{"name": "chemical-science-pack"}]}
        logs.clear()
        p(blue_unlocked)
        self.assertEqual(len(codex.asked), 2)
        self.assertIn("Why you are asked now: research unlocked chemical-science-pack", codex.asked[1][0])
        self.assertIn("The plan it has now:", codex.asked[1][0])
        latest = stored[-1][1]
        self.assertEqual(latest["version"], 2)
        self.assertIn("blue: new science section", latest["changes"])
        self.assertTrue(any("research unlocked chemical-science-pack" in l for l in logs))

    def test_the_strategist_may_ask_and_too_soon_it_keeps_the_plan(self):
        codex, stored, logs = FakeCodex(answer(bus_plan()), answer(bus_plan(rate=1.0))), [], []
        p = planner(codex, stored=stored, logs=logs)
        p(OPENED)
        asked = {**OPENED, "request_id": "1:40", "purpose": "factory-plan", "tick": OPENED["tick"] + 3600,
                 "factory_plan": stored[-1][1]}
        with self.assertRaises(Defer):
            p(asked)
        self.assertEqual(len(codex.asked), 1)                     # within the strategist's cooldown: kept
        self.assertTrue(any("is only 1 minutes old, kept" in l for l in logs))
        again = {**asked, "request_id": "1:41", "tick": OPENED["tick"] + fp.ASKED_COOLDOWN + 1}
        with self.assertRaises(Defer):
            p(again)
        self.assertEqual(len(codex.asked), 2)
        self.assertIn("Why you are asked now: the strategist asked", codex.asked[1][0])
        self.assertTrue(any("from 0.5 to 1 a second, by another copy" in c for c in stored[-1][1]["changes"]))
        self.assertNotIn("factory-plan", [o["id"] for o in __import__("companion.catalog_planner").catalog_planner
                                          .purpose_options(OPENED)])   # System One is never offered it

    def test_while_the_ai_is_paused_the_rules_plan_then_the_architect_when_it_is_back(self):
        codex, stored = FakeCodex(answer(bus_plan())), []
        p = planner(codex, stored=stored)
        decisions_api.pause_ai(3600, "a test")
        p(OPENED)
        self.assertEqual(codex.asked, [])
        self.assertEqual(stored[-1][1]["source"], "rules")
        self.assertTrue(stored[-1][1]["basis"]["paused"])
        decisions_api.resume_ai()
        back = {**OPENED, "request_id": "1:50", "tick": OPENED["tick"] + fp.COOLDOWN + 1, "factory_plan": stored[-1][1]}
        p(back)
        self.assertEqual(len(codex.asked), 1)
        self.assertIn("the AI is back after a pause", codex.asked[0][0])
        self.assertEqual((stored[-1][1]["source"], stored[-1][1]["version"]), ("architect", 2))

    def test_a_slow_architect_holds_up_no_request_and_its_plan_is_kept_when_it_comes(self):
        # (run 32, 2026-10-25: it took its full 120 s and the game gave up on the request it held)
        import threading
        import time
        from unittest import mock
        from companion import plan_keeper
        gate = threading.Event()

        class SlowCodex(FakeCodex):
            def ask(self, prompt, schema, **kw):
                gate.wait(10)
                return super().ask(prompt, schema, **kw)
        codex, stored, logs = SlowCodex(answer(bus_plan("the architect's"))), [], []
        p = planner(codex, stored=stored, logs=logs)
        with mock.patch.object(plan_keeper, "ARCHITECT_WAIT_S", 0.2),                 mock.patch.object(bus_planner, "PLACES", 1):   # (the bus plan's own time is not what is timed)
            started = time.monotonic()
            plan = p(OPENED)
            self.assertLess(time.monotonic() - started, 5)
            self.assertEqual(plan["purpose"], "bus-base")             # (the request went on: the rules' plan)
            self.assertEqual(stored[-1][1]["source"], "rules")
            gate.set()
            for _ in range(100):
                if stored[-1][1]["source"] == "architect":
                    break
                time.sleep(0.05)
        later = stored[-1][1]
        self.assertEqual((later["source"], later["label"]), ("architect", "the architect's"))
        self.assertGreater(later["version"], 1)
        self.assertEqual(p._known({**OPENED, "tick": OPENED["tick"] + 60})["label"], "the architect's")
        self.assertTrue(any("came later" in l for l in logs))

    def test_an_architect_that_fails_leaves_the_rules_plan_and_waits(self):
        from companion.codex_backend import CodexError
        codex, stored, logs = FakeCodex(CodexError("no answer within 180 s")), [], []
        p = planner(codex, stored=stored, logs=logs)
        plan = p(OPENED)
        self.assertEqual(plan["purpose"], "bus-base")             # the plan never stops a plan
        self.assertEqual(stored[-1][1]["source"], "rules")
        self.assertTrue(any("architect failed (no answer within 180 s)" in l for l in logs))
        p({**OPENED, "request_id": "1:61", "tick": OPENED["tick"] + 60, "factory_plan": stored[-1][1]})
        self.assertEqual(len(codex.asked), 1)                     # not asked again straight away

    def test_one_planner_for_every_engineer_keeps_each_engineer_s_plan_apart(self):
        codex, stored = FakeCodex(answer(bus_plan("Ada's")), answer(bus_plan("Borin's"))), []
        p = planner(codex, stored=stored)
        p(OPENED)
        p({**OPENED, "request_id": "2:1", "npc_id": 2, "npc_name": "Borin"})
        self.assertEqual(len(codex.asked), 2)                     # Borin's own plan, not Ada's
        self.assertEqual([(npc, plan["label"]) for npc, plan in stored if plan["version"] == 1][-1], (2, "Borin's"))
        self.assertEqual(p._known({**OPENED, "tick": OPENED["tick"] + 60})["label"], "Ada's")

    def test_without_an_architect_nothing_changes(self):
        stored, logs = [], []
        p = planner(None, stored=stored, logs=logs)
        plan = p(OPENED)
        self.assertEqual(plan["purpose"], "bus-base")
        self.assertEqual(stored, [])
        self.assertFalse(any("factory plan" in l for l in logs))
        self.assertIs(p.world(OPENED), WORLD)                     # its map unchanged: no ground kept

    def test_the_plan_s_ground_is_kept_from_other_goals_planners(self):
        codex, stored = FakeCodex(answer(bus_plan())), []
        p = planner(codex, stored=stored)
        p(OPENED)
        site = {**OPENED, "request_id": "1:70", "factory_plan": stored[-1][1]}
        p._purpose_now = "power-plant"
        dump = p.world(site)
        self.assertIn([-96, -32, 96, 160], dump["reserved"])
        self.assertIn((0, 100), blocked_tiles(dump))
        self.assertNotIn("reserved", WORLD)                       # the source's own map is left alone
        p._purpose_now = "bus-base"
        self.assertNotIn([-96, -32, 96, 160], p.world({**site, "request_id": "1:71"})["reserved"])

    def test_the_plan_s_next_section_is_the_next_goal(self):
        plan = fp.default_plan(OPENED)
        site = {**OPENED, "factory_plan": as_stored(plan)}
        logs = []
        p = planner(None, logs=logs)
        self.assertEqual(p.planned_goal(site), "bus-base")
        self.assertTrue(any("(the factory plan: bus, priority 0.90)" in l for l in logs))
        found = fp.next_purpose(site, lambda purpose: purpose == "power-plant")
        self.assertEqual((found[0], found[1]["id"]), ("power-plant", "power"))
        self.assertIsNone(fp.next_purpose(site, lambda purpose: False))
        # science that grows by upgrading in place goes to the upgrade planner first, and adds no growth copies
        upgrading = fp.new_plan([{"id": "bus", "kind": "bus", "spm": 15},
                                 {"id": "red", "kind": "science", "makes": {"automation-science-pack": 1.0},
                                  "grow": "upgrade", "priority": 0.9}], source="architect", why="x", site=OPENED)
        self.assertEqual(fp.builders(fp.section(upgrading, "red")), ("upgrade", "bus-base"))
        self.assertEqual(fp.bus_targets({**OPENED, "factory_plan": upgrading}).growth, 0)
        self.assertEqual(fp.next_purpose({**OPENED, "factory_plan": upgrading}, lambda p_: True)[0], "upgrade")
        # growth copies: sections at what the bus aims at, after its first bus (made for part of it)
        copying = fp.new_plan([{"id": "bus", "kind": "bus", "spm": 20},
                               {"id": "red", "kind": "science", "makes": {"automation-science-pack": 1.0},
                                "grow": "copy", "priority": 0.9}], source="architect", why="x", site=OPENED)
        first = {"stages": ["bus-red-science"], "spm": 10.0, "aim": 20.0}
        self.assertEqual(fp.bus_targets({**OPENED, "factory_plan": copying, "base_plan": first}).growth, 3)   # 10+3x20
        before = {"stages": ["bus-red-science"], "spm": 20.0}   # (a bus planned before: copies of its own rate)
        self.assertEqual(fp.bus_targets({**OPENED, "factory_plan": copying, "base_plan": before}).growth, 2)
        # (40 a minute, kept as 0.6667 a second: 10 + 2 x 15 makes it, no third section for the rounding)
        forty = fp.new_plan([{"id": "bus", "kind": "bus", "spm": 15},
                             {"id": "red", "kind": "science", "makes": {"automation-science-pack": 40 / 60},
                              "grow": "copy", "priority": 0.9}], source="architect", why="x", site=OPENED)
        third = {"stages": ["bus-red-science"], "spm": 10.0, "aim": 15.0}
        self.assertEqual(fp.bus_targets({**OPENED, "factory_plan": as_stored(forty), "base_plan": third}).growth, 2)
        # its own priorities move a section up
        ranked = {**site, "priorities": {"power": 1.0, "bus": 0.2}}
        self.assertEqual(fp.next_purpose(ranked, lambda p_: p_ in ("bus-base", "power-plant"))[0], "power-plant")


class DueTests(unittest.TestCase):
    def plan(self, **basis):
        plan = fp.default_plan(OPENED)
        plan["basis"].update(basis)
        return plan

    def test_the_first_plan_waits_for_the_opening(self):
        self.assertIsNone(fp.due(OPENED, None, opening_over=False))
        self.assertEqual(fp.due(OPENED, None, opening_over=True), "the opening is done and power runs")

    def test_later_plans_come_only_when_something_moved_and_never_too_soon(self):
        plan = self.plan()
        later = {**OPENED, "tick": OPENED["tick"] + fp.COOLDOWN}
        self.assertIsNone(fp.due(later, plan, opening_over=True))
        unlocked = {**later, "recipes": RECIPES + [{"name": "military-science-pack"}]}
        self.assertEqual(fp.due(unlocked, plan, opening_over=True), "research unlocked military-science-pack")
        self.assertIsNone(fp.due({**unlocked, "tick": OPENED["tick"] + 60}, plan, opening_over=True))
        facts = {"entities": {f"b{k}": {"kind": "crafter", "ready": k < 7} for k in range(8)}}
        self.assertEqual(fp.due({**later, "game_facts": facts}, self.plan(ready=3), opening_over=True),
                         "research made 4 more buildings ready")
        self.assertIsNone(fp.due({**later, "game_facts": facts}, self.plan(ready=4), opening_over=True))
        fallen = self.plan(supply={"iron-plate": 10.0})
        self.assertEqual(fp.due(later, fallen, opening_over=True), "the ledger: iron-plate fell from 10 to 0 a second")
        moved = {**later, "priorities": {"defense": 1.0, "mall": 0.2}}
        self.assertEqual(fp.due(moved, plan, opening_over=True), "the priorities moved (now first: defense)")
        short = {**later, "base": [{"id": "lab", "purpose": "bus-base", "stage": "bus-labs",
                                    "ledger": {"kind": "labs", "takes": {"automation-science-pack": 1.0}}}]}
        self.assertEqual(fp.due(short, plan, opening_over=True), "the ledger: automation-science-pack is short")
        paused = {**plan, "basis": {**plan["basis"], "paused": True}}
        self.assertEqual(fp.due(later, paused, opening_over=True), "the AI is back after a pause")
        self.assertIsNone(fp.due(later, paused, opening_over=True, ai_ready=False))


class RevisionTests(unittest.TestCase):
    SITE = {**OPENED, "base_plan": {"stages": ["bus-red-science"], "done": {"bus-red-science": 1}, "spm": 30,
                                     "x": 0, "y": 40, "length": 100, "packs": ["automation-science-pack"]}}

    def base(self, red_rate, starter=True):
        out = [{"id": "bus-red", "purpose": "bus-base", "stage": "bus-red-science", "box": [0, 40, 30, 60],
                "ledger": {"kind": "science", "makes": {"automation-science-pack": red_rate}}}]
        if starter:
            out.append({"id": "old-red", "purpose": "red-science-automation", "box": [-90, -90, -80, -80],
                        "ledger": {"kind": "science", "makes": {"automation-science-pack": 0.1}}})
        return out

    def old(self):
        plan = fp.new_plan([{"id": "bus", "kind": "bus", "priority": 0.9},
                            {"id": "red", "kind": "science", "makes": {"automation-science-pack": 0.5}, "grow": "copy",
                             "priority": 0.8},
                            {"id": "starter", "kind": "science", "makes": {"automation-science-pack": 0.1},
                             "zone": "starter-zone", "priority": 0.2}],
                           source="architect", why="first", site=self.SITE,
                           zones=[{"id": "starter-zone", "box": [-96, -96, -32, -32], "for": "starter", "use": "zone",
                                   "clear": False, "priority": 0.2}])
        fp.refresh({**self.SITE, "base": self.base(0.5)}, plan)
        return plan

    def test_a_revision_grows_keeps_built_ground_and_retires_what_is_replaced(self):
        old = self.old()
        self.assertEqual(fp.section(old, "starter")["status"], "built")
        new = fp.new_plan([{"id": "bus", "kind": "bus", "priority": 0.9},
                           {"id": "red", "kind": "science", "makes": {"automation-science-pack": 1.0}, "grow": "copy",
                            "priority": 0.8, "replaces": ["starter"]}],
                          source="architect", why="more science", site=self.SITE)
        site = {**self.SITE, "base": self.base(0.5)}
        plan = fp.revise(old, new, site)
        self.assertEqual(plan["version"], 2)
        self.assertIn("red: automation-science-pack from 0.5 to 1 a second, by another copy", plan["changes"])
        self.assertIn("starter: retires once red runs", plan["changes"])
        self.assertEqual(fp.section(plan, "starter")["status"], "retiring")
        self.assertEqual(fp.section(plan, "red")["status"], "building")   # half of its new rate stands
        self.assertEqual(fp.retirements(site, plan), ([], False))          # held until red runs
        grown = {**self.SITE, "base": self.base(1.0)}
        fp.refresh(grown, plan)
        self.assertEqual(fp.section(plan, "red")["status"], "built")
        names, changed = fp.retirements(grown, plan)
        self.assertEqual((names, changed), (["red-science-automation"], True))
        self.assertEqual(fp.retirements(grown, plan)[0], [])               # asked once, not every request
        gone = {**self.SITE, "base": self.base(1.0, starter=False)}
        self.assertEqual(fp.retirements(gone, plan), ([], True))
        self.assertEqual(fp.section(plan, "starter")["status"], "retired")
        self.assertEqual(fp.reserved_boxes(plan, "power-plant"), [])     # its ground is free again

    def test_what_still_feeds_others_stays(self):
        old = self.old()
        new = fp.new_plan([{"id": "red", "kind": "science", "makes": {"automation-science-pack": 0.5}}],
                          source="architect", why="drop it", site=self.SITE)
        hungry = {**self.SITE, "base": self.base(0.5) + [
            {"id": "labs", "purpose": "bus-base", "stage": "bus-labs", "box": [0, 70, 20, 80],
             "ledger": {"kind": "labs", "takes": {"automation-science-pack": 0.58}}}]}
        plan = fp.revise(old, new, hungry)
        self.assertIn("starter: retires once red runs", plan["changes"])
        self.assertEqual(fp.section(plan, "red")["status"], "built")
        self.assertEqual(fp.retirements(hungry, plan)[0], [])   # the labs would go short without it
        fed = {**hungry, "base": hungry["base"][:-1]}
        self.assertEqual(fp.retirements(fed, plan)[0], ["red-science-automation"])

    def test_a_standing_bus_is_never_taken_down_by_a_plan(self):
        old = self.old()
        fp.section(old, "bus")["status"] = "building"
        new = fp.new_plan([{"id": "red", "kind": "science", "makes": {"automation-science-pack": 0.5}}],
                          source="architect", why="no bus", site=self.SITE)
        plan = fp.revise(old, new, {**self.SITE, "base": self.base(0.5)})
        self.assertIn("bus: kept (a built bus stays)", plan["changes"])
        self.assertNotIn("bus", [r["section"] for r in plan["retire"]])
        self.assertEqual(fp.blocks_to_retire(fp.section(plan, "bus"), plan, {**self.SITE, "base": self.base(0.5)}), [])

    def test_a_built_section_keeps_its_ground_when_a_plan_moves_it(self):
        old = self.old()
        new = fp.new_plan([{"id": "starter", "kind": "science", "makes": {"automation-science-pack": 0.1},
                            "zone": "starter-zone"}], source="architect", why="move", site=self.SITE,
                          zones=[{"id": "starter-zone", "box": [96, 96, 160, 160], "for": "starter", "use": "zone"}])
        new["zones"][0]["id"] = "elsewhere"
        new["sections"][0]["zone"] = "elsewhere"
        plan = fp.revise(old, new, {**self.SITE, "base": self.base(0.5)})
        self.assertEqual(fp.zone_box(plan, fp.section(plan, "starter")["zone"]), (-96, -96, -32, -32))


class ZoneTests(unittest.TestCase):
    def test_zones_are_marked_cleared_once_the_map_shows_them_clear(self):
        plan = fp.new_plan([{"id": "later", "kind": "other"}], source="architect", why="x", site=OPENED,
                           zones=[{"id": "later-zone", "box": [-96, -96, -32, -32], "for": "later", "use": "zone",
                                   "clear": True, "priority": 0.6, "why": "ground for later"}])
        self.assertTrue(fp.refresh(OPENED, plan, WORLD))
        z = plan["zones"][0]
        self.assertTrue(z["clear"] and z["left"]["trees"] > 0)
        bare = {**WORLD, "trees": [], "entities": [e for e in WORLD["entities"] if e.get("t") != "simple-entity"]}
        self.assertTrue(fp.refresh({**OPENED, "tick": 600_000}, plan, bare))
        self.assertEqual((z["clear"], z["cleared"]), (False, 600_000))
        cliffs = {**bare, "entities": bare["entities"] + [{"n": "cliff", "t": "cliff", "x": -60, "y": -60, "w": 4, "h": 2}]}
        other = fp.new_plan([], source="architect", why="x", site=OPENED,
                            zones=[{"id": "k", "box": [-96, -96, -32, -32], "use": "keep", "clear": True,
                                    "priority": 0.6}])
        fp.refresh(OPENED, other, cliffs)
        self.assertEqual((other["zones"][0]["clear"], other["zones"][0]["priority"]), (True, 0.05))   # explosives first
        stored = as_stored(plan)["zones"][0]
        for key in ("id", "box", "clear", "priority", "why"):           # what the clearing planner reads
            self.assertIn(key, stored)
        both = fp.new_plan([], source="architect", why="x", site=OPENED, zones=[
            {"id": "a", "box": [0, 0, 64, 64], "clear": True, "priority": 0.2},
            {"id": "b", "box": [64, 0, 128, 64], "clear": True, "priority": 0.7},
            {"id": "c", "box": [128, 0, 192, 64], "clear": False, "priority": 0.9}])
        self.assertEqual([z["id"] for z in fp.zones_to_clear({"factory_plan": as_stored(both)})], ["b", "a"])

    def test_the_bus_s_ground_and_room_come_from_what_it_built(self):
        plan = fp.default_plan(OPENED)
        site = {**OPENED, "base_plan": {"stages": ["power", "bus-gears"], "done": {"power": 1}, "spm": 15, "x": 0,
                                        "y": 40, "length": 120, "packs": ["automation-science-pack"]},
                "base": [{"id": "g", "purpose": "bus-base", "stage": "bus-gears", "box": [-10, 0, 40, 60]}]}
        self.assertTrue(fp.refresh(site, plan, WORLD))
        bus = fp.section(plan, "bus")
        self.assertEqual(fp.zone_box(plan, bus["zone"]), (-10, 0, 40, 160))
        self.assertEqual(fp.zone_box(plan, bus["room"]), (-10, 160, 40, 160 + fp.ROOM_ROWS))
        longer = {**site, "base_plan": {**site["base_plan"], "length": 180}}
        fp.refresh(longer, plan, WORLD)
        self.assertEqual(fp.zone_box(plan, bus["room"]), (-10, 220, 40, 220 + fp.ROOM_ROWS))   # it moves on below


class MallExtensionTests(unittest.TestCase):
    def test_a_mall_extension_names_what_it_makes_and_the_assemblers_feeding_it(self):
        goal = mall.extension_goal(2, ["splitter", "fast-inserter"])
        self.assertTrue(mall.is_extension(goal))
        self.assertFalse(mall.is_extension("mall-logistics"))
        self.assertEqual(mall.extension_recipes(goal), ["splitter", "transport-belt", "fast-inserter", "inserter"])
        modules = section_for(goal, 30, Book.vanilla())
        self.assertEqual(modules[0].name, "mall-2")
        self.assertEqual(sorted(e.recipe for e in modules[0].entities if e.recipe),
                         sorted(["splitter", "transport-belt", "fast-inserter", "inserter"]))

    def test_a_finished_bus_grows_its_mall_for_what_the_plan_wants(self):
        plan = fp.new_plan([{"id": "bus", "kind": "bus", "priority": 0.9},
                            {"id": "red", "kind": "science", "makes": {"automation-science-pack": 0.25}},
                            {"id": "mall", "kind": "mall", "makes": {"transport-belt": 4.0, "fast-inserter": 1.0,
                                                                     "gun-turret": 0.01}}],
                           source="architect", why="x", site=OPENED)
        saved = {"stages": ["bus-mall-logistics"], "done": {"bus-mall-logistics": 1}, "spm": 15,
                 "packs": ["automation-science-pack"], "mall": ["transport-belt", "inserter", "gun-turret"], "extra": []}
        site = {**OPENED, "recipes": RECIPES + [{"name": "fast-inserter"}], "base_plan": saved,
                "factory_plan": as_stored(plan)}
        more = bus_planner.extendable(site)
        self.assertEqual(len(more), 1)
        self.assertEqual(mall.extension_recipes(more[0]), ["transport-belt", "fast-inserter", "inserter"])
        done = {**site, "base_plan": {**saved, "extra": list(more)}}
        self.assertEqual(bus_planner.extendable(done), ())   # (nothing more wanted, and no growth planned)
        # A bus whose own mall is its first section (mall.OWN): counted once, the plan's extension numbered after it.
        own = mall.extension_goal(mall.OWN, saved["mall"])
        sectioned = {**site, "base_plan": {**saved, "extra": [own]}}
        self.assertEqual(bus_planner.extendable(sectioned), more)
        self.assertEqual(more[0].split("-")[1], "2")
        self.assertEqual(bus_planner.extendable({**site, "base_plan": {**saved, "extra": [own, *more]}}), ())


class ShapeTests(unittest.TestCase):
    def test_a_plan_from_the_game_comes_back_in_shape_and_goes_out_within_its_limits(self):
        raw = {"version": 3, "sections": {}, "zones": {}, "lanes": {}, "retire": {}, "basis": {"packs": {}}}
        plan = fp.normalize(raw)
        self.assertEqual((plan["sections"], plan["zones"], plan["basis"]["packs"]), ([], [], []))
        site = {"factory_plan": raw}
        self.assertIs(fp.plan_of(site), fp.plan_of(site))
        big = fp.new_plan([{"id": f"s{k}", "kind": "other", "makes": {f"i{n}": n + 1.0 for n in range(20)}}
                           for k in range(40)], source="architect", why="x" * 400, site=OPENED)
        out = fp.bounded(big)
        self.assertEqual(len(out["sections"]), fp.MAX_SECTIONS)
        self.assertEqual(len(out["sections"][0]["makes"]), fp.MAX_ITEMS)
        self.assertIn("i19", out["sections"][0]["makes"])          # the biggest rates kept
        self.assertFalse(any(k.startswith("_") for k in out))

    def test_priorities_read_in_whichever_shape_they_come(self):
        self.assertEqual(fp.priorities({"priorities": {"iron-plate": 0.8, "mall": {"priority": 0.2}}}),
                         {"iron-plate": 0.8, "mall": 0.2})
        self.assertEqual(fp.priorities({"priorities": [{"item": "coal", "priority": 4}, {"section": "bus", "value": 2}]}),
                         {"coal": 1.0, "bus": 0.5})
        self.assertEqual(fp.priorities({}), {})
        s = {"id": "red", "kind": "science", "priority": 0.4, "makes": {"automation-science-pack": 1}}
        self.assertEqual(fp.effective_priority(s, {"priorities": {"automation-science-pack": 1.0}}), 0.7)

    def test_the_strategists_levels_and_the_priority_models_weights(self):
        # the game exports the strategist's settings as levels ("high"): read as the weight such a setting
        # carries, for the architect's trigger; its brief weighs the priority model's most wanted items too
        from unittest import mock
        from companion import priorities as model

        class Weighed:
            def ranked(self, n):
                return [("iron-plate", mock.Mock(weight=0.9)), ("stone-wall", mock.Mock(weight=0.45))][:n]
        site = {"priorities": [{"name": "plastic-bar", "level": "high", "left": 20}]}
        self.assertEqual(fp.priorities(site), {"plastic-bar": model.SET_WEIGHT["high"]})
        with mock.patch.object(model, "assess", return_value=Weighed()) as assess:
            self.assertEqual(fp.wanted(site), {"iron-plate": 0.9, "stone-wall": 0.45,
                                               "plastic-bar": model.SET_WEIGHT["high"]})
        self.assertFalse(assess.call_args.kwargs["hold"])   # (read only: the hysteresis is left alone)

        # read from inside the model itself (its goals ask the bus planner, whose targets weigh priorities
        # again): the deliberate ones only, not the model again (it went round without end)
        def inner(s, **kw):
            self.assertEqual(fp.wanted(s), {"plastic-bar": model.SET_WEIGHT["high"]})
            return Weighed()
        with mock.patch.object(model, "assess", side_effect=inner):
            self.assertIn("iron-plate", fp.wanted(site))


def _to_lua(lua, value):
    if isinstance(value, dict):
        return lua.table_from({k: _to_lua(lua, v) for k, v in value.items() if v is not None})
    if isinstance(value, list):
        return lua.table_from([_to_lua(lua, v) for v in value])
    return value


def _from_lua(value):
    import lupa.lua52 as lupa
    if lupa.lua_type(value) == "table":
        keys = list(value.keys())
        if keys and all(isinstance(k, int) for k in keys) and sorted(keys) == list(range(1, len(keys) + 1)):
            return [_from_lua(value[k]) for k in sorted(keys)]
        return {str(k): _from_lua(value[k]) for k in keys}
    return value


class GameStoreTests(unittest.TestCase):
    """The game's side (factory_plan.lua): what it keeps of a plan, and the /npc-plan factory command."""

    def module(self):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().source = (ROOT / "mod" / "scripts" / "factory_plan.lua").read_text(encoding="utf-8")
        lua.execute("FP = assert(load(source))()")
        return lua

    def test_the_game_keeps_a_bounded_copy_and_refuses_an_older_version(self):
        lua = self.module()
        plan = as_stored(fp.default_plan(OPENED))
        plan["sections"] += [{"id": f"x{k}", "kind": "other", "why": "w" * 500} for k in range(30)]
        plan["zones"] = [{"id": "z", "box": [0, 0, 64, 64], "clear": True, "priority": 3, "why": "w", "for": "bus"},
                         {"id": "bad", "box": [0, 0, -5, 64]}, {"id": "huge", "box": [0, 0, 5000, 64]}]
        lua.globals().raw = _to_lua(lua, plan)
        lua.execute("npc = {design={status='waiting', export_cache={tick=7}}}; kept, why = FP.store(npc, raw, 99)")
        kept = _from_lua(lua.eval("npc.factory_plan"))
        # the waiting request's export carries it, the rest kept: made again whole it froze the game (OP-3)
        self.assertEqual(lua.eval("npc.design.export_cache.tick"), 7)
        self.assertEqual(lua.eval("npc.design.export_cache.factory_plan.stored"), 99)
        self.assertEqual(len(kept["sections"]), 24)
        self.assertTrue(all(len(s.get("why", "")) <= 160 for s in kept["sections"]))
        self.assertEqual([z["id"] for z in kept["zones"]], ["z"])
        self.assertEqual((kept["zones"][0]["priority"], kept["zones"][0]["for"]), (1, "bus"))
        self.assertEqual(kept["stored"], 99)
        self.assertEqual((kept["version"], kept["updated"], kept["asked"]), (1, OPENED["tick"], OPENED["tick"]))
        back = fp.normalize(kept)
        self.assertEqual(fp.section(back, "mall")["makes"], fp.section(fp.normalize(plan), "mall")["makes"])
        lua.globals().old = _to_lua(lua, {**plan, "version": 0})
        self.assertEqual(lua.eval("select(2, FP.store(npc, old, 100))"), "plan_shape")
        lua.globals().older = _to_lua(lua, {"version": 0.5 + 0.5})
        lua.execute("npc.factory_plan.version = 5")
        self.assertEqual(lua.eval("select(2, FP.store(npc, older, 101))"), "older_version")

    def test_the_path_to_space_in_a_few_numbers(self):
        lua = self.module()
        lua.execute("""
            dag = {target='rocket-silo', order={'automation', 'oil', 'rocket-silo'}, nodes={
              automation={researched=true, research_unit_count=10, science={{name='automation-science-pack', amount=1}}},
              oil={researched=false, research_unit_count=100, science={{name='automation-science-pack', amount=1},
                                                                       {name='logistic-science-pack', amount=1}}},
              ['rocket-silo']={researched=false, research_unit_count=1000, science={{name='chemical-science-pack', amount=2}}}}}
            path = FP.space_path(dag)
        """)
        path = _from_lua(lua.eval("path"))
        self.assertEqual((path["left"], path["units"], path["next"]), (2, 1100, ["oil", "rocket-silo"]))
        self.assertEqual(path["packs"], {"automation-science-pack": 100, "logistic-science-pack": 100,
                                         "chemical-science-pack": 2000})
        self.assertIsNone(lua.eval("FP.space_path(nil)"))

    def test_the_command_keeps_it_with_the_engineer(self):
        import test_retire
        lua = test_retire.runtime()
        lua.globals().decode = lambda text: _to_lua(lua, json.loads(text))
        lua.globals().encode = lambda table: json.dumps(_from_lua(table))
        lua.execute("""
            helpers = {json_to_table=function(s) return decode(s) end, table_to_json=function(t) return encode(t) end}
            handlers, said = {}, {}
            commands = {add_command=function(name, help, f) handlers[name] = f end}
            rcon = {print=function(s) said[#said + 1] = s end}
            DS.register_commands()
        """)
        plan = as_stored(fp.default_plan(OPENED))
        lua.globals().line = "factory " + json.dumps({"npc_id": 1, "plan": plan})
        lua.execute("handlers['npc-plan']({parameter=line})")
        said = json.loads(lua.eval("said[1]").split("NPC_PLAN_RESULT ", 1)[1])
        self.assertEqual((said["ok"], said["version"]), (True, 1))
        self.assertEqual(lua.eval("storage.npcs[1].factory_plan.sections[1].id"), "bus")
        lua.globals().line = "factory " + json.dumps({"npc_id": 9, "plan": plan})
        lua.execute("handlers['npc-plan']({parameter=line})")
        self.assertIn('"reason": "unknown_npc"', lua.eval("said[2]"))
        source = (ROOT / "mod" / "scripts" / "designed_start.lua").read_text(encoding="utf-8")
        self.assertIn("factory_plan=FactoryPlan.export(npc)", source)   # every site export carries it


if __name__ == "__main__":
    unittest.main()
