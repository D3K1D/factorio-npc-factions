"""The whole base outlined from the start and designed chunk by chunk in the background (companion/outline.py, the
architect's design_chunk, factory_plan.revise_chunk, plan_keeper's chunk steps): the user, 2026-10-25, "it should
plan the base chunk by chunk so it has time to design instead of all at once"."""

import copy
import json
import threading
import time
import unittest
from pathlib import Path

from companion import architect as arch
from companion import factory_plan as fp
from companion import land as lands
from companion import outline

from test_factory_plan import OPENED, WORLD, FakeCodex, planner, sec

ROOT = Path(__file__).resolve().parents[1]


def tight_bus():
    cap = json.loads((ROOT / "tests" / "fixtures" / "bw-tight-bus.json").read_text(encoding="utf-8"))
    return cap["site"], cap["world"]


class SurveyTests(unittest.TestCase):
    def test_patches_are_connected_ore_nearest_home_first_and_small_ones_left_out(self):
        world = {"resources": {"iron-ore": [[y, 10, 19] for y in range(10, 20)] + [[y, 300, 309] for y in range(0, 10)],
                               "copper-ore": [[0, 0, 2], [1, 0, 2], [2, 0, 2]],               # 9 tiles: too small
                               "crude-oil": [[50, 50, 50], [55, 52, 52], [200, 200, 200]],    # two fields of wells
                               "coal": [[y, 2000, 2010] for y in range(10)]}}                  # beyond reach
        found = outline.survey_patches(world, (0, 0))
        self.assertEqual([(p.resource, p.tiles) for p in found],
                         [("iron-ore", 100), ("crude-oil", 2), ("crude-oil", 1), ("iron-ore", 100)])
        self.assertEqual(found[0].box, (10, 10, 20, 20))
        self.assertEqual(outline.patch_id(found[0]), "iron-ore@15,15")

    def test_a_real_plot_outlines_the_bus_power_every_patch_and_the_rest(self):
        site, world = tight_bus()
        out = outline.make(site, world, site.get("factory_plan"))
        kinds = [c["kind"] for c in out["chunks"]]
        buses = kinds.index("power")
        self.assertEqual(set(kinds[:buses]), {"bus"})                       # the bus first, a few sections a chunk
        self.assertTrue(all(len(c["section_ids"]) <= outline.SECTIONS <= arch.CHUNK_SECTIONS for c in out["chunks"]))
        on_bus = {s["id"] for s in site["factory_plan"]["sections"] if fp.on_bus(s)}
        self.assertEqual({i for c in out["chunks"][:buses] for i in c["section_ids"]}, on_bus)
        self.assertTrue(out["chunks"][0]["areas"] and out["chunks"][buses]["areas"])   # by the bus, by water
        patches = [c for c in out["chunks"] if c["kind"] == "resource"]
        self.assertGreaterEqual(len(patches), 6)
        self.assertTrue({"iron-ore", "copper-ore", "coal", "stone"} <= {next(iter(c["targets"])) for c in patches})
        self.assertTrue(all(0 < v <= outline.BELT for c in patches for v in c["targets"].values()))
        self.assertLessEqual(len(out["chunks"]), outline.MAX_CHUNKS)

    def test_a_design_carries_over_while_its_chunk_is_the_same_and_is_redone_when_it_changed(self):
        site, world = tight_bus()
        plan = copy.deepcopy(site.get("factory_plan"))
        plan["outline"] = outline.make(site, world, plan)
        outline.designed(plan["outline"], "power", 100)
        again = outline.make(site, world, plan)
        self.assertEqual(next(c for c in again["chunks"] if c["id"] == "power").get("designed_key"),
                         next(c for c in again["chunks"] if c["id"] == "power")["input_key"])
        moved = {**world, "pumps": [{"x": float(world["home"]["x"]) + 3, "y": float(world["home"]["y"])}]}
        changed = outline.make(site, moved, {**plan, "outline": again})
        self.assertIsNone(next(c for c in changed["chunks"] if c["id"] == "power").get("designed_key"))


class OrderTests(unittest.TestCase):
    def test_the_next_chunk_waits_for_the_cooldown_and_a_failed_one_for_its_retry(self):
        out = {"chunks": [{"id": "a", "input_key": "1"}, {"id": "b", "input_key": "2"}]}
        self.assertEqual(outline.next_chunk(out, 1000)["id"], "a")
        self.assertIsNone(outline.next_chunk(out, 1000, last_done=1000 - outline.COOLDOWN + 1))
        outline.failed(out, "a", 1000)
        self.assertEqual(outline.next_chunk(out, 1001)["id"], "b")          # the others go on meanwhile
        outline.failed(out, "a", 1000 + outline.RETRY)
        self.assertEqual(out["chunks"][0]["retry_tick"], 1000 + 3 * outline.RETRY)   # the wait doubles
        outline.designed(out, "b", 1100)
        self.assertEqual([c["id"] for c in outline.pending(out)], ["a"])
        self.assertEqual(outline.invalidate(out), 1)                        # research moved: designed again
        self.assertEqual(len(outline.pending(out)), 2)


def base_plan():
    sections = [{"id": "bus", "kind": "bus", "priority": 0.9, "why": "the bus"},
                {"id": "mall", "kind": "mall", "makes": {"transport-belt": 1.0}, "priority": 0.6},
                {"id": "power", "kind": "power", "priority": 0.8, "status": "planned"},
                {"id": "iron-smelting", "kind": "smelting", "makes": {"iron-plate": 3.0}, "status": "built",
                 "priority": 0.85}]
    return fp.new_plan(sections, source="rules", why="the rules", site=OPENED)


def zone(zid, owner, box=(0, 0, 64, 64)):
    return {"id": zid, "box": list(box), "use": "zone", "for": owner, "clear": True, "left": {}, "priority": 0.5,
            "why": "x"}


class MergeTests(unittest.TestCase):
    def test_a_chunk_changes_its_own_sections_and_zones_only(self):
        plan = base_plan()
        power = {"id": "power", "kind": "power", "section_ids": ["power"]}
        design = {"sections": [{"id": "power", "kind": "power", "priority": 0.95, "why": "doubled ahead", "makes": {}},
                               {"id": "mall", "kind": "mall", "priority": 0.1, "why": "not this chunk's"}],
                  "zones": [zone("power-zone", "power")]}
        merged, changes = fp.revise_chunk(plan, power, design, OPENED)
        by = {s["id"]: s for s in merged["sections"]}
        self.assertEqual((by["power"]["priority"], by["power"]["zone"]), (0.95, "power-zone"))
        self.assertEqual(by["mall"]["priority"], 0.6)                      # another chunk's: as it was
        self.assertEqual(merged["version"], plan["version"] + 1)
        self.assertEqual([z["use"] for z in merged["zones"]], ["zone"])     # power is built by a goal: kept ground
        self.assertEqual(fp.reserved_boxes(merged, "bus-base"), [])         # the bus builds its own power
        self.assertEqual(fp.reserved_boxes(merged, "research-lab"), [[0, 0, 64, 64]])
        self.assertEqual(plan["zones"], [])                                 # (the plan it was given is untouched)

    def test_a_patch_gets_an_advisory_mining_section_and_built_or_on_bus_sections_keep_theirs(self):
        plan = base_plan()
        patch = {"id": "iron-ore@15,15", "kind": "resource", "section_ids": []}
        design = {"sections": [{"id": "iron-ore-15-15", "kind": "mining", "makes": {"iron-ore": 7.5}, "priority": 0.7,
                                "why": "mined in full"}],
                  "zones": [zone("iron-ore-15-15-zone", "iron-ore-15-15")]}
        merged, _ = fp.revise_chunk(plan, patch, design, OPENED)
        mining = next(s for s in merged["sections"] if s["id"] == "iron-ore-15-15")
        self.assertEqual((mining["status"], mining["chunk"]), ("planned", "iron-ore@15,15"))
        self.assertEqual([(z["use"], z.get("outline"), z["clear"]) for z in merged["zones"]],
                         [("zone", True, False)])                           # advisory
        self.assertEqual(mining["zone"], "iron-ore-15-15-zone")
        self.assertEqual(fp.reserved_boxes(merged, "bus-base"), [])          # it keeps the bus off no ore
        self.assertEqual(fp.reserved_boxes(merged, "research-lab"), [])
        bus = {"id": "bus", "kind": "bus", "section_ids": ["mall", "iron-smelting"]}
        design = {"sections": [{"id": "mall", "kind": "mall", "priority": 0.7, "makes": {"inserter": 1.0}},
                               {"id": "iron-smelting", "kind": "smelting", "makes": {"iron-plate": 15.0},
                                "priority": 0.9}],
                  "zones": [zone("mall-zone", "mall")]}
        merged, changes = fp.revise_chunk(plan, bus, design, OPENED)
        by = {s["id"]: s for s in merged["sections"]}
        self.assertNotIn("zone", by["mall"])                                # on the bus it stays
        self.assertEqual(by["mall"]["makes"], {"inserter": 1.0})
        self.assertEqual(by["iron-smelting"]["makes"], {"iron-plate": 3.0})  # built: its rates kept
        self.assertEqual(by["iron-smelting"]["priority"], 0.9)
        self.assertTrue(any("stays on the bus" in c for c in changes))
        merged, changes = fp.revise_chunk(plan, {"id": "power", "kind": "power", "section_ids": ["power"]},
                                          {"sections": [{"id": "new-thing", "kind": "other"}], "zones": []}, OPENED)
        self.assertEqual(len(merged["sections"]), len(plan["sections"]))   # only a patch's chunk adds sections


class BriefTests(unittest.TestCase):
    def test_a_chunk_brief_is_small_and_names_only_its_cells(self):
        site, world = tight_bus()
        plan = copy.deepcopy(site.get("factory_plan"))
        plan["outline"] = outline.make(site, world, plan)
        land = lands.summarize(world, site)
        patch = next(c for c in plan["outline"]["chunks"] if c["kind"] == "resource")
        text = arch.chunk_brief(site, land, plan, patch)
        self.assertLess(len(text), 4000)
        self.assertIn(patch["id"], text)
        cells = arch.chunk_cells(land, patch, plan)
        self.assertLessEqual(len(cells), arch.CHUNK_CELLS)
        self.assertTrue(all(c.line() in text for c in cells))


class BackgroundTests(unittest.TestCase):
    """The plan keeper with an architect that designs chunk by chunk: the rules' plan at once, the outline, one
    chunk at a time in the background, merged at the next request; nothing waits."""

    def chunk_planner(self, codex, stored, logs):
        p = planner(codex, stored=stored, logs=logs)
        p.architect = arch.Architect(codex, log=logs.append)            # (chunked, as the bridge makes it)
        return p

    def wait(self, p, npc=1):
        for _ in range(200):
            job = (p._chunk_jobs or {}).get(npc)
            if job is None or not job["thread"].is_alive():
                return
            time.sleep(0.02)

    def test_the_rules_plan_comes_at_once_and_the_architect_designs_it_a_chunk_at_a_time(self):
        bus_answer = {"sections": [sec("bus", "bus", priority=0.92, why="the bus, designed as a chunk")],
                      "notes": ["room south for growth"]}
        codex, stored, logs = FakeCodex(bus_answer), [], []
        p = self.chunk_planner(codex, stored, logs)
        started = time.monotonic()
        p(OPENED)
        self.assertLess(time.monotonic() - started, 30)
        first = stored[-1][1]
        self.assertEqual(first["source"], "rules")
        self.assertTrue(any("the base outlined" in l for l in logs))
        self.assertTrue(any("designs chunk bus in the background" in l for l in logs))
        self.wait(p)
        self.assertEqual(len(codex.asked), 1)
        self.assertLess(len(codex.asked[0][0]), 4000)                       # a chunk's brief: small
        self.assertIs(codex.asked[0][1], arch.CHUNK_SCHEMA)
        later = {**OPENED, "request_id": "1:31", "tick": OPENED["tick"] + 60}
        p(later)
        kept = p._known(later)
        self.assertEqual(kept["source"], "architect")
        self.assertGreater(kept["version"], first["version"])
        self.assertEqual(next(s for s in kept["sections"] if s["id"] == "bus")["why"], "the bus, designed as a chunk")
        bus = next(c for c in kept["outline"]["chunks"] if c["id"] == "bus")
        self.assertEqual(bus["designed_key"], bus["input_key"])
        self.assertTrue(any("chunk bus designed by the architect" in l for l in logs))
        self.assertEqual(len(codex.asked), 1)                               # the next chunk waits for the cooldown
        self.assertNotIn("outline", fp.bounded(kept))                      # (the host's own, not the game's)

    def test_a_failed_chunk_is_tried_again_later_and_the_next_goes_on(self):
        from companion.codex_backend import CodexError
        codex, stored, logs = FakeCodex(CodexError("no answer within 600 s"),
                                        {"sections": [sec("power", "power", priority=0.8)], "notes": []}), [], []
        p = self.chunk_planner(codex, stored, logs)
        p(OPENED)
        self.wait(p)
        tick = OPENED["tick"] + 60
        p({**OPENED, "request_id": "1:31", "tick": tick})
        self.assertTrue(any("design of chunk bus failed" in l for l in logs))
        kept = p._known({**OPENED, "tick": tick})
        bus = next(c for c in kept["outline"]["chunks"] if c["id"] == "bus")
        self.assertEqual(bus["retry_tick"], tick + outline.RETRY)
        p({**OPENED, "request_id": "1:32", "tick": tick + outline.COOLDOWN})
        self.wait(p)
        self.assertTrue(codex.asked[-1][0].split("The chunk: ")[1].startswith("bus-2"))   # the next went on

    def test_a_design_from_another_game_is_dropped(self):
        codex, stored, logs = FakeCodex({"sections": [sec("bus", "bus", priority=0.9)], "notes": []}), [], []
        p = self.chunk_planner(codex, stored, logs)
        p({**OPENED, "tick": OPENED["tick"] + 10_000})
        self.wait(p)
        p({**OPENED, "request_id": "1:40", "tick": OPENED["tick"]})        # a save from before was loaded
        self.assertTrue(any("from another game" in l for l in logs) or
                        all(c.get("designed_key") is None for c in (p._known(OPENED) or {}).get("outline", {})
                            .get("chunks", [])))


if __name__ == "__main__":
    unittest.main()


class ReviewTests(unittest.TestCase):
    """What a review of the first version found (2026-10-25)."""

    def test_the_host_s_copy_wins_a_tie_with_the_plan_come_back_through_the_game(self):
        codex, stored, logs = FakeCodex({"sections": [sec("bus", "bus", priority=0.9)], "notes": []}), [], []
        p = planner(codex, stored=stored, logs=logs)
        p.architect = arch.Architect(codex, log=logs.append)
        p(OPENED)
        mine = p._known(OPENED)
        back = json.loads(json.dumps(fp.bounded(mine)))                      # the game keeps no outline
        self.assertNotIn("outline", back)
        known = p._known({**OPENED, "factory_plan": back})
        self.assertIn("outline", known)

    def test_a_redesign_replaces_a_section_s_zone_and_room_and_a_patch_s_smelting_keeps_its_ground(self):
        plan = base_plan()
        power = {"id": "power", "kind": "power", "section_ids": ["power"]}
        first = {"sections": [{"id": "power", "kind": "power", "priority": 0.9}],
                 "zones": [zone("power-zone", "power"), {**zone("power-room", "power", (64, 0, 128, 64)), "use": "room"}]}
        merged, _ = fp.revise_chunk(plan, power, first, OPENED)
        p = next(s for s in merged["sections"] if s["id"] == "power")
        self.assertEqual((p["zone"], p["room"]), ("power-zone", "power-room"))
        again = {"sections": [{"id": "power", "kind": "power", "priority": 0.9}],
                 "zones": [{**zone("power-room", "power", (0, 64, 64, 128)), "use": "room"}]}
        merged, _ = fp.revise_chunk(merged, power, again, OPENED)
        p = next(s for s in merged["sections"] if s["id"] == "power")
        self.assertNotIn("zone", p)                                          # no stale reference
        self.assertEqual(p["room"], "power-room")
        patch = {"id": "copper-ore@40,40", "kind": "resource", "section_ids": []}
        design = {"sections": [{"id": "copper-smelting-2", "kind": "smelting", "makes": {"copper-plate": 7.5}}],
                  "zones": [zone("copper-smelting-2-zone", "copper-smelting-2")]}
        merged, changes = fp.revise_chunk(merged, patch, design, OPENED)
        s = next(x for x in merged["sections"] if x["id"] == "copper-smelting-2")
        self.assertEqual(s["zone"], "copper-smelting-2-zone")               # off the bus, by birth
        self.assertFalse(fp.on_bus(s))
        self.assertFalse(any("stays on the bus" in c for c in changes))

    def test_a_patch_chunk_keeps_its_own_sections(self):
        site, world = tight_bus()
        plan = copy.deepcopy(site.get("factory_plan"))
        plan["outline"] = outline.make(site, world, plan)
        patch = next(c for c in plan["outline"]["chunks"] if c["kind"] == "resource")
        merged, _ = fp.revise_chunk(plan, patch, {"sections": [{"id": "mine-1", "kind": "mining",
                                                                 "makes": {"iron-ore": 5.0}}], "zones": []}, site)
        again = outline.make(site, world, merged)
        mine = next(c for c in again["chunks"] if c["id"] == patch["id"])
        self.assertEqual(mine["section_ids"], ["mine-1"])
        self.assertFalse(any(c["id"] == "section-mine-1" for c in again["chunks"]))
        redone, changes = fp.revise_chunk(merged, mine, {"sections": [{"id": "mine-1", "kind": "mining",
                                                                       "makes": {"iron-ore": 7.5}}], "zones": []}, site)
        self.assertEqual(next(s for s in redone["sections"] if s["id"] == "mine-1")["makes"], {"iron-ore": 7.5})

    def test_background_architects_never_take_the_slots_requests_wait_on(self):
        from companion import codex_backend
        self.assertLess(codex_backend.BACKGROUND_RUNNING, codex_backend.MAX_RUNNING)
        self.assertIn("architect", codex_backend.BACKGROUND)
