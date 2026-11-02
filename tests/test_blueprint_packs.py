"""The user's blueprint packs in the layout library (companion/blueprint_packs.py, F-16): strings decoded, books walked to
any depth, 1.1 and 2.x directions, a mod's settings kept, tiles and rails left out and said, buildings this mod set lacks
rejected with the reason, purpose by the rules and the designer only once, ports at the edge, judged once a mod set,
and found like a saved layout."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion import blueprint_packs as packs  # noqa: E402
from companion import blueprints as bp  # noqa: E402
from companion import build_choice  # noqa: E402

V2, V1 = 2 << 48, (1 << 48) | (1 << 32)
FACTS = {"assembling-machine-1": {"kind": "crafter", "type": "assembling-machine", "width": 3, "height": 3, "speed": 0.5,
                                  "categories": ["crafting"], "ready": True},
         "inserter": {"kind": "inserter", "width": 1, "height": 1, "ready": True},
         "transport-belt": {"kind": "belt", "width": 1, "height": 1, "items_per_second": 15, "ready": True},
         "underground-belt": {"kind": "underground", "width": 1, "height": 1, "ready": True},
         "splitter": {"kind": "splitter", "width": 2, "height": 1, "ready": True},
         "stone-furnace": {"kind": "crafter", "type": "furnace", "width": 2, "height": 2, "speed": 1, "categories": ["smelting"],
                           "ready": True},
         "small-electric-pole": {"kind": "pole", "type": "electric-pole", "width": 1, "height": 1, "ready": True},
         "wooden-chest": {"kind": "chest", "width": 1, "height": 1, "ready": True}}
RECIPES = [{"name": "iron-gear-wheel", "category": "crafting", "energy": 0.5,
            "ingredients": [{"name": "iron-plate", "amount": 2}], "products": [{"name": "iron-gear-wheel", "amount": 1}]},
           {"name": "automation-science-pack", "category": "crafting", "energy": 5,
            "ingredients": [{"name": "copper-plate", "amount": 1}, {"name": "iron-gear-wheel", "amount": 1}],
            "products": [{"name": "automation-science-pack", "amount": 1}]}]


def site():
    return {"request_id": "1:1", "npc_id": 1, "tick": 3600, "recipes": RECIPES, "base": [],
            "game_facts": {"entities": FACTS}, "prototypes": {}}


def red(version=V2, direction=4):
    return {"blueprint": {"label": "red science 1/s", "version": version, "item": "blueprint", "entities": [
        {"entity_number": 1, "name": "assembling-machine-1", "position": {"x": 1.5, "y": 1.5},
         "recipe": "iron-gear-wheel"},
        {"entity_number": 2, "name": "inserter", "position": {"x": 3.5, "y": 1.5}, "direction": direction,
         "pickup_position": {"x": -1, "y": 0}, "drop_position": {"x": 1.2, "y": 0}},
        {"entity_number": 3, "name": "assembling-machine-1", "position": {"x": 5.5, "y": 1.5},
         "recipe": "automation-science-pack"},
        {"entity_number": 4, "name": "transport-belt", "position": {"x": 6.5, "y": 3.5}, "direction": direction},
        {"entity_number": 5, "name": "underground-belt", "position": {"x": 0.5, "y": 3.5}, "direction": direction,
         "type": "input"},
        {"entity_number": 6, "name": "splitter", "position": {"x": 3, "y": 3.5}, "direction": 0,
         "input_priority": "left"},
        {"entity_number": 7, "name": "straight-rail", "position": {"x": 9, "y": 9}}],
        "tiles": [{"name": "stone-path", "position": {"x": 0, "y": 0}}]}}


def furnaces():
    return {"blueprint": {"label": "furnace row", "version": V2, "entities": [
        {"entity_number": 1, "name": "stone-furnace", "position": {"x": 1, "y": 1}},
        {"entity_number": 2, "name": "stone-furnace", "position": {"x": 3, "y": 1}}]}}


def alien():
    return {"blueprint": {"label": "bob's thing", "version": V2, "entities": [
        {"entity_number": 1, "name": "bob-mystery-machine", "position": {"x": 1.5, "y": 1.5}}]}}


def book():
    inner = {"blueprint_book": {"label": "smelting", "blueprints": [{"index": 0, **furnaces()}]}}
    return {"blueprint_book": {"label": "main bus", "blueprints": [
        {"index": 0, **red()}, {"index": 1, **inner}, {"index": 2, **alien()},
        {"index": 3, "deconstruction_planner": {"label": "x"}}]}}


class DecodeTests(unittest.TestCase):
    def test_a_string_round_trips_and_garbage_is_said(self):
        self.assertEqual(packs.decode(packs.encode(red())), red())
        with self.assertRaises(packs.BlueprintError):
            packs.decode("0not-base64!!")
        with self.assertRaises(packs.BlueprintError):
            packs.decode("")

    def test_packs_in_subfolders_the_newer_of_two_and_not_stamped_books(self):
        import os
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            (folder / "optimizer").mkdir()
            (folder / "nuclear").mkdir()
            old, new = folder / "optimizer blocks.txt", folder / "optimizer" / "optimizer blocks.txt"
            for path in (old, new, folder / "books.txt", folder / "nuclear" / "kingdud.txt"):
                path.write_text("x", encoding="utf-8")
            os.utime(old, (1_000_000, 1_000_000))   # (a copy left at the top, gone stale)
            self.assertEqual(packs.pack_files(folder), sorted([folder / "books.txt", new]))

    def test_books_are_walked_to_any_depth_with_where_each_came_from(self):
        got = [(path, b["label"]) for path, b in packs.blueprints(packs.decode(packs.encode(book())))]
        self.assertEqual(got, [(("main bus",), "red science 1/s"), (("main bus", "smelting"), "furnace row"),
                               (("main bus",), "bob's thing")])        # (the deconstruction planner passed over)

    def test_directions_as_2x_has_them(self):
        self.assertEqual(packs.direction(2, V1), 4)       # (1.1's east)
        self.assertEqual(packs.direction(4, V2), 4)
        self.assertEqual(packs.direction(6, V1), 12)


class ConvertTests(unittest.TestCase):
    def test_settings_kept_rails_and_tiles_said_and_unknown_buildings_named(self):
        protos = packs.facts_prototypes(site())
        plan, skipped, missing = packs.convert(red()["blueprint"], protos)
        by = {e["prototype"]: e for e in plan["entities"]}
        self.assertEqual(by["inserter"]["pickup_position"], {"x": -1, "y": 0})
        self.assertEqual(by["underground-belt"]["belt_to_ground_type"], "input")
        self.assertEqual(by["splitter"]["input_priority"], "left")
        self.assertNotIn("straight-rail", by)
        self.assertTrue(any("tiles" in s for s in skipped) and any("straight-rail" in s for s in skipped))
        self.assertEqual(missing, [])
        self.assertEqual(packs.convert(alien()["blueprint"], protos)[2], ["bob-mystery-machine"])
        old, _, _ = packs.convert(red(V1, 2)["blueprint"], protos)
        self.assertEqual(next(e for e in old["entities"] if e["prototype"] == "inserter")["direction"], 4)

    def test_its_end_products_and_what_it_takes(self):
        plan, _, _ = packs.convert(red()["blueprint"], packs.facts_prototypes(site()))
        self.assertEqual(packs.products_of(plan["entities"], site()),
                         (["automation-science-pack"], ["copper-plate", "iron-plate"]))


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        self.folder = root / "packs"
        self.folder.mkdir()
        (self.folder / "my-book.txt").write_text(packs.encode(book()), encoding="utf-8")
        (self.folder / "broken.txt").write_text("0garbage", encoding="utf-8")
        self.catalog = bp.Catalog(root / "data" / "blueprints")
        self.asked = []

    def tearDown(self):
        self.dir.cleanup()

    def ask(self, prompt, schema):
        self.asked.append(prompt)
        return {"purpose": "iron-smelting-expansion", "products": ["iron-plate"], "why": "furnaces in a row"}

    def test_a_book_is_imported_marked_and_rejections_say_why(self):
        logs = []
        got = {r["file"]: r for r in packs.sync(self.catalog, site(), folder=self.folder, ask=self.ask,
                                                log=logs.append)}
        book_record = got["my-book.txt"]
        self.assertEqual(sorted(i["purpose"] for i in book_record["imported"]),
                         ["iron-smelting-expansion", "red-science-automation"])
        self.assertEqual(book_record["rejected"], [{"label": "my-book/main bus/bob's thing",
                                                    "why": "not in this mod set: bob-mystery-machine"}])
        self.assertIn("not a blueprint string", got["broken.txt"]["rejected"][0]["why"])
        red_entry = next(b for b in self.catalog.blueprints.values() if b["purpose"] == "red-science-automation")
        self.assertEqual(red_entry["source"], "import:my-book/main bus/red science 1/s")
        self.assertEqual(red_entry["outputs"], ["automation-science-pack"])
        self.assertTrue(any(p["way"] == "out" for p in red_entry["import"]["ports"]))
        self.assertTrue(any("1 imported" in line or "2 imported" in line for line in logs))
        self.assertEqual(len(self.asked), 1)                  # (the furnace row: the rules could not tell)

    def test_judged_once_a_mod_set_and_the_designer_never_asked_twice(self):
        packs.sync(self.catalog, site(), folder=self.folder, ask=self.ask, log=lambda line: None)
        self.assertEqual(packs.sync(self.catalog, site(), folder=self.folder, ask=self.ask, log=lambda line: None), [])
        more = site()
        more["game_facts"] = {"entities": {**FACTS, "bob-mystery-machine": {"kind": "crafter", "width": 3,
                                                                           "height": 3, "ready": True}}}
        again = {r["file"]: r for r in packs.sync(self.catalog, more, folder=self.folder, ask=self.ask,
                                                  log=lambda line: None)}
        self.assertEqual(again["my-book.txt"]["rejected"], [])          # (now in this mod set)
        self.assertEqual(sum("furnace row" in a for a in self.asked), 1)  # (its answer kept: asked once)
        self.assertEqual(sum("bob's thing" in a for a in self.asked), 1)  # (the newly placeable one, once)

    def test_a_ports_sidecar_says_each_ports_item_rate_and_way(self):
        # (the optimizer's blocks come with ports.json: fluid ports' direction and every input, DS-3)
        folder = Path(self.dir.name) / "optimizer"   # (its own folder: the book's questions are not this test's)
        folder.mkdir()
        (folder / "red-block.txt").write_text(packs.encode(red()), encoding="utf-8")
        side = {"box": {"w": 6, "h": 5},
                "inputs": [{"item": "iron-plate", "rate": 1.0, "fluid": False, "x": -1, "y": 2, "edge": "west"},
                           {"item": "water", "rate": 20.0, "fluid": True, "x": 3, "y": -1, "edge": "north"}],
                "outputs": [{"item": "automation-science-pack", "rate": 0.5, "fluid": False, "x": 6, "y": 2,
                             "edge": "east"}]}
        (folder / "red-block.ports.json").write_text(json.dumps(side), encoding="utf-8")
        packs.sync(self.catalog, site(), folder=folder, ask=self.ask, log=lambda line: None)
        entry = next(b for b in self.catalog.blueprints.values() if "red-block" in b["source"])
        got = entry["import"]["ports"]
        self.assertEqual(entry["import"]["ports_from"], "sidecar")
        self.assertEqual([(p["way"], p["kind"], p["item"]) for p in got],
                         [("in", "belt", "iron-plate"), ("in", "fluid", "water"), ("out", "belt", "automation-science-pack")])
        self.assertEqual((got[0]["x"], got[0]["y"], got[0]["outside"]), (0.5, 2.5, [-1, 2]))
        self.assertEqual(got[2]["rate"], 0.5)
        # a changed sidecar is read again
        side["outputs"][0]["rate"] = 1.0
        (folder / "red-block.ports.json").write_text(json.dumps(side), encoding="utf-8")
        again = {r["file"]: r for r in packs.sync(self.catalog, site(), folder=folder, ask=self.ask,
                                                  log=lambda line: None)}
        self.assertIn("red-block.txt", again)

    def test_a_label_line_names_its_sidecar_in_a_subfolder_and_mirroring_is_kept(self):
        # (the optimizer pack: "optimizer: <job>" before each string, <job>.ports.json in optimizer/, DS-3)
        folder = Path(self.dir.name) / "packs-lines"
        (folder / "optimizer").mkdir(parents=True)
        block = red()
        block["blueprint"]["entities"][0]["mirror"] = True
        lines = ["optimizer: red_job", packs.encode(block), "", "optimizer: other", packs.encode(red(direction=8)), ""]
        (folder / "blocks.txt").write_text(chr(10).join(lines), encoding="utf-8")
        side = {"box": {"w": 6, "h": 5}, "inputs": [{"name": "iron-plate", "rate": 1.0, "fluid": False, "x": -1, "y": 2,
                                                     "edge": "west"}], "outputs": []}
        (folder / "optimizer" / "red_job.ports.json").write_text(json.dumps(side), encoding="utf-8")
        packs.sync(self.catalog, site(), folder=folder, ask=self.ask, log=lambda line: None)
        entry = next(b for b in self.catalog.blueprints.values() if "blocks@2" in b["source"])
        self.assertEqual(entry["import"]["ports_from"], "sidecar")
        self.assertEqual(entry["import"]["ports"][0]["item"], "iron-plate")      # (its "name" key read too)
        self.assertTrue(any(e.get("mirror") for e in entry["entities"]))
        other = [b for b in self.catalog.blueprints.values() if "blocks@5" in b["source"]]
        self.assertTrue(all(b["import"].get("ports_from") != "sidecar" for b in other))

    def test_an_imported_layout_is_found_as_a_saved_one_is(self):
        packs.sync(self.catalog, site(), folder=self.folder, ask=self.ask, log=lambda line: None)
        found = build_choice.peers(self.catalog, "red-science-automation", None)
        self.assertEqual([b["source"][:7] for b in found], ["import:"])
        by_job = build_choice.peers(self.catalog, "some-other-goal", ((), ("automation-science-pack",)))
        self.assertEqual(len(by_job), 1)                                   # (by what it makes, too)

    def test_tests_and_tools_never_read_the_user_s_folder_unasked(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NPC_BLUEPRINT_PACKS", None)
            self.assertIsNone(packs.default_folder())

    def test_no_folder_or_no_facts_does_nothing(self):
        self.assertEqual(packs.sync(self.catalog, site(), folder=Path(self.dir.name) / "none"), [])
        bare = {**site(), "game_facts": {}}
        self.assertEqual(packs.sync(self.catalog, bare, folder=self.folder), [])


class RoundOneTests(unittest.TestCase):
    """Codex's F-16 review: what the game cannot build is rejected at import, drills keep their ore, recipes lock,
    the designer is asked a few at a time, and a blueprint's settings reach the game turned with the layout."""

    def test_the_placeable_types_are_the_game_s_own(self):
        import re
        lua = (ROOT / "mod" / "scripts" / "designed_start.lua").read_text(encoding="utf-8")
        block = lua[lua.index("local PLACEABLE={"):]
        block = block[:block.index("}") + 1]
        names = set(re.findall(r'\["([a-z-]+)"\]=true', block)) | set(re.findall(r"[{,\s]([a-z]+)=true", block))
        self.assertEqual(names, set(packs.PLACEABLE))

    def test_diagonals_and_what_the_engineer_does_not_build_are_rejected_with_the_reason(self):
        # (a storage tank it builds now, for a nuclear plant's steam: DS-4; a heat interface not)
        protos = {**packs.facts_prototypes(site()), "heat-interface": {"type": "heat-interface", "width": 1, "height": 1}}
        tilted = {"version": V2, "entities": [{"name": "inserter", "position": {"x": 0.5, "y": 0.5}, "direction": 2}]}
        self.assertEqual(packs.convert(tilted, protos)[2], ["inserter (set diagonally)"])
        odd = {"version": V2, "entities": [{"name": "heat-interface", "position": {"x": 0.5, "y": 0.5}}]}
        self.assertIn("does not build a heat-interface", packs.convert(odd, protos)[2][0])

    def test_drills_take_their_ore_from_the_label_or_are_rejected(self):
        facts = dict(FACTS, **{"electric-mining-drill": {"kind": "drill", "type": "mining-drill", "width": 3,
                                                          "height": 3, "ready": True}})
        s = {**site(), "game_facts": {"entities": facts}}
        self.assertEqual(packs.ore_of("Iron mining 2x8", s), "iron-ore")
        self.assertIsNone(packs.ore_of("mining field", s))
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "packs"
            folder.mkdir()
            drill = [{"name": "electric-mining-drill", "position": {"x": 1.5, "y": 1.5}}]
            (folder / "m.txt").write_text(packs.encode({"blueprint_book": {"label": "b", "blueprints": [
                {"blueprint": {"label": "copper mine", "version": V2, "entities": drill}},
                {"blueprint": {"label": "mine", "version": V2, "entities": drill}}]}}), encoding="utf-8")
            catalog = bp.Catalog(Path(d) / "data" / "blueprints")
            record = packs.sync(catalog, s, folder=folder, log=lambda line: None)[0]
            self.assertEqual(record["rejected"], [])
            ores = sorted(r["resource"] for b in catalog.blueprints.values() for r in b["resource_requirements"])
            self.assertEqual(ores, [bp.ANY_ORE, "copper-ore"])   # (one named, one for whichever patch it is put on)
            entry = next(b for b in catalog.blueprints.values() if b["label"] == "copper mine")
            self.assertEqual(sum(len(r["tiles"]) for r in entry["resource_requirements"]), 1)   # (its centre tile)

    def test_a_layout_waits_for_the_research_of_its_recipes(self):
        entry = {"stage_bills": [], "recipes_needed": ["logistic-science-pack"]}
        self.assertEqual(bp.locked_items(entry, {"recipes": RECIPES}), ["recipe:logistic-science-pack"])
        known = RECIPES + [{"name": "logistic-science-pack", "products": [{"name": "logistic-science-pack"}]}]
        self.assertEqual(bp.locked_items(entry, {"recipes": known}), [])

    def test_the_designer_is_asked_a_few_a_minute_once_ever_and_a_failed_ask_waits(self):
        from unittest import mock
        rows = [{"blueprint": {"label": f"row {k}", "version": V2, "entities": [
            {"name": "wooden-chest", "position": {"x": 0.5 + k, "y": 0.5}}]}} for k in range(4)]
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "packs"
            folder.mkdir()
            (folder / "c.txt").write_text(packs.encode({"blueprint_book": {"label": "b", "blueprints": rows}}),
                                          encoding="utf-8")
            calls = []

            def ask(prompt, schema):
                calls.append(prompt)
                if len(calls) == 1:
                    raise TimeoutError("no answer")
                return {"purpose": "none", "products": ["wooden-chest"], "why": "storage"}
            clock = [1000.0]
            packs._ASKED.clear()
            with mock.patch.object(packs, "ASK_PER_MINUTE", 2), mock.patch.object(packs, "ASK_PER_SYNC", 2), \
                    mock.patch.object(packs.time, "monotonic", lambda: clock[0]):
                catalog = bp.Catalog(Path(d) / "data" / "blueprints")
                first = packs.sync(catalog, site(), folder=folder, ask=ask, log=lambda line: None)[0]
                self.assertEqual(len(calls), 2)                            # (two a minute, all engineers)
                self.assertEqual(len(first["pending"]), 3)                 # (one failed, two not asked yet)
                packs.sync(catalog, site(), folder=folder, ask=ask, log=lambda line: None)
                self.assertEqual(len(calls), 2)                            # (the same minute: none more)
                for _ in range(3):
                    clock[0] += 61
                    packs.sync(catalog, site(), folder=folder, ask=ask, log=lambda line: None)
                self.assertEqual(len(calls), 5)                            # (each asked once, the failed one again)
                kept = packs.answers(folder, "c.txt")
                self.assertEqual(len(kept), 4)                             # (kept beside the pack)
                # (a new run, a new data folder, another engineer: nothing asked again)
                fresh = bp.Catalog(Path(d) / "data2" / "blueprints")
                clock[0] += 61
                packs.sync(fresh, site(), folder=folder, ask=ask, log=lambda line: None)
                self.assertEqual(len(calls), 5)
                self.assertTrue((folder / "c.annotations.json").exists())
            packs._ASKED.clear()

    def test_the_same_blueprint_twice_or_in_another_pack_is_asked_about_once(self):
        import threading
        row = {"label": "row", "version": V2, "entities": [{"name": "wooden-chest", "position": {"x": 0.5, "y": 0.5}}]}
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "packs"
            folder.mkdir()
            book = {"blueprint_book": {"label": "b", "blueprints": [{"blueprint": {**row, "label": "one"}},
                                                                   {"blueprint": {**row, "label": "two"}}]}}
            (folder / "a.txt").write_text(packs.encode(book), encoding="utf-8")
            (folder / "b.txt").write_text(packs.encode({"blueprint": {**row, "label": "three"}}), encoding="utf-8")
            calls = []

            def ask(prompt, schema):
                calls.append(prompt)
                return {"purpose": "none", "products": [], "why": "a chest"}
            packs._ASKED.clear()
            catalog = bp.Catalog(Path(d) / "data" / "blueprints")   # (one run: one import record, three engineers)
            errors, done = [], []

            def engineer():
                try:
                    for _ in range(4):   # (requests one after another: a pass busy elsewhere is skipped, not waited on)
                        done.append(packs.sync(catalog, site(), folder=folder, ask=ask, log=lambda line: None))
                except Exception as exc:   # (any failure reaches the test)
                    errors.append(exc)
            runs = [threading.Thread(target=engineer) for _ in range(3)]
            for t in runs:
                t.start()
            for t in runs:
                t.join()
            self.assertEqual(errors, [])
            self.assertEqual(len(calls), 1)            # (three engineers, three copies, two packs: asked once)
            self.assertEqual(len(catalog.blueprints), 3)   # (each copy imported once into the shared library)
            packs._ASKED.clear()

    def test_rejected_or_waiting_blueprints_are_never_asked_about(self):
        green = {"blueprint": {"label": "green line", "version": V2, "entities": [
            {"name": "assembling-machine-1", "position": {"x": 1.5, "y": 1.5}, "recipe": "logistic-science-pack"}]}}
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "packs"
            folder.mkdir()
            (folder / "w.txt").write_text(packs.encode({"blueprint_book": {"label": "b", "blueprints": [
                green, alien()]}}), encoding="utf-8")
            calls = []
            packs._ASKED.clear()
            packs.sync(bp.Catalog(Path(d) / "data" / "blueprints"), site(), folder=folder,
                       ask=lambda p, s: calls.append(p) or {"purpose": "none", "products": [], "why": ""},
                       log=lambda line: None)
            self.assertEqual(calls, [])
            packs._ASKED.clear()

    def test_an_inserter_s_vectors_turn_with_the_layout(self):
        entry = {"footprint": [[0, 0]], "resource_requirements": [], "entities": [
            {"id": "i", "prototype": "inserter", "direction": 0, "position": {"x": 0.5, "y": 0.5},
             "pickup_position": {"x": 0, "y": -1}, "drop_position": {"x": 0, "y": 1.2}}]}
        turned = bp.rotated(entry, 1)["entities"][0]
        self.assertEqual(turned["pickup_position"], {"x": 1, "y": 0})
        self.assertEqual(turned["drop_position"], {"x": -1.2, "y": 0})


class GameTests(unittest.TestCase):
    """designed_start.lua: a blueprint's settings copied when well formed, and applied once placed."""

    def test_settings_copied_and_applied(self):
        sys.path.insert(0, str(ROOT / "tests"))
        import test_clearing
        lua = test_clearing.game()
        lua.execute("""
            prototypes.item = prototypes.item or {}
            prototypes.item['iron-plate'] = {name='iron-plate'}
            ins = DS.settings_copy({pickup_position={x=-1, y=0}, drop_position={x=99, y=0}}, 'inserter')
            spl = DS.settings_copy({input_priority='left', output_priority='sideways', filter={name='iron-plate'}},
                                   'splitter')
            built = {position={x=10.5, y=20.5}}
            DS.apply_settings(built, ins)
            sb = {position={x=0, y=0}}
            DS.apply_settings(sb, spl)""")
        self.assertEqual(lua.eval("ins.pickup_position.x"), -1)
        self.assertIsNone(lua.eval("ins.drop_position"))               # (out of reach: not copied)
        self.assertEqual(lua.eval("built.pickup_position.x"), 9.5)
        self.assertEqual(lua.eval("sb.splitter_input_priority"), "left")
        self.assertIsNone(lua.eval("spl.output_priority"))
        self.assertEqual(lua.eval("sb.splitter_filter.name"), "iron-plate")


class RoundTwoTests(unittest.TestCase):
    """Codex's second F-16 review: recipes not researched yet wait (their products never guessed), ores by whole
    words, and a rebuilt building gets its settings back."""

    def test_a_blueprint_set_to_a_recipe_not_researched_waits_then_comes(self):
        green = {"blueprint": {"label": "green line", "version": V2, "entities": [
            {"name": "assembling-machine-1", "position": {"x": 1.5, "y": 1.5}, "recipe": "logistic-science-pack"}]}}
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "packs"
            folder.mkdir()
            (folder / "g.txt").write_text(packs.encode(green), encoding="utf-8")
            catalog = bp.Catalog(Path(d) / "data" / "blueprints")
            first = packs.sync(catalog, site(), folder=folder, log=lambda line: None)[0]
            self.assertEqual(first["waiting"], [{"label": "g/green line", "recipes": ["logistic-science-pack"]}])
            self.assertEqual(len(catalog.blueprints), 0)
            self.assertEqual(packs.sync(catalog, site(), folder=folder, log=lambda line: None), [])   # (nothing new)
            later = {**site(), "recipes": RECIPES + [{"name": "logistic-science-pack", "ingredients": [],
                                                      "products": [{"name": "logistic-science-pack", "amount": 1}]}]}
            again = packs.sync(catalog, later, folder=folder, log=lambda line: None)[0]
            self.assertEqual([i["purpose"] for i in again["imported"]], ["green-science-automation"])
            self.assertEqual(again["waiting"], [])

    def test_a_record_of_older_rules_is_judged_again_its_readings_and_layouts_replaced(self):
        self.assertNotEqual(packs.mod_key({"a": {}}), packs._sha(sorted({"a": {}})))
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "packs"
            folder.mkdir()
            (folder / "r.txt").write_text(packs.encode(red()), encoding="utf-8")
            catalog = bp.Catalog(Path(d) / "data" / "blueprints")
            record = packs.sync(catalog, site(), folder=folder, log=lambda line: None)[0]
            entry = catalog.blueprints[record["imported"][0]["id"]]
            catalog.save({**entry, "purpose": "old-guess"})          # (as an older rule had it)
            path = next((Path(d) / "data" / "imports").glob("*.json"))
            old = json.loads(path.read_text(encoding="utf-8"))
            old["version"] = 1
            old["annotations"] = {k: {**v, "purpose": "old-guess"} for k, v in old["annotations"].items()}
            old["mods"] = "older-key"
            path.write_text(json.dumps(old), encoding="utf-8")
            catalog.save({**catalog.blueprints[entry["id"]], "uses": [{"tick": 1}]})
            packs.sync(catalog, site(), folder=folder, log=lambda line: None)
            self.assertEqual(catalog.blueprints[entry["id"]]["purpose"], "red-science-automation")
            self.assertEqual(catalog.blueprints[entry["id"]]["uses"], [{"tick": 1}])   # (what it learned kept)

    def test_the_same_row_in_two_books_is_two_layouts(self):
        row = furnaces()["blueprint"]
        two = {"blueprint_book": {"label": "b", "blueprints": [{"blueprint": {**row, "label": "iron row"}},
                                                               {"blueprint": {**row, "label": "copper row"}}]}}
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "packs"
            folder.mkdir()
            (folder / "two.txt").write_text(packs.encode(two), encoding="utf-8")
            catalog = bp.Catalog(Path(d) / "data" / "blueprints")
            packs.sync(catalog, site(), folder=folder, log=lambda line: None)
            self.assertEqual(len(catalog.blueprints), 2)

    def test_ores_by_whole_words(self):
        self.assertIsNone(packs.ore_of("charcoal kiln", site()))
        self.assertEqual(packs.ore_of("Coal mining", site()), "coal")
        self.assertEqual(packs.ore_of("iron_ore drills", site()), "iron-ore")

    def test_a_rebuilt_building_gets_its_settings_back(self):
        lua = (ROOT / "mod" / "scripts" / "designed_start.lua").read_text(encoding="utf-8")
        rebuilt = lua[lua.index("function DesignedStart.rebuilt("):]
        self.assertIn("DesignedStart.apply_settings(e,entity.settings)", rebuilt[:1500])


class F16bTests(unittest.TestCase):
    """F-16b: several strings a file, optional parts by role, rows named by their label, mining layouts for any ore,
    and variants of one design."""

    def test_several_strings_a_file_each_by_its_line(self):
        text = "Red science\n" + packs.encode(red()) + "\n\n# furnaces below\n" + packs.encode(furnaces()) + "\n"
        self.assertEqual([n for n, _ in packs.strings(text)], [2, 5])
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "packs"
            folder.mkdir()
            (folder / "two.txt").write_text(text, encoding="utf-8")
            catalog = bp.Catalog(Path(d) / "data" / "blueprints")
            record = packs.sync(catalog, site(), folder=folder, log=lambda line: None)[0]
            self.assertEqual(sorted(i["label"] for i in record["imported"]),
                             ["two@2/red science 1/s", "two@5/furnace row"])

    def test_optional_parts_left_out_and_provider_chests_made_plain(self):
        protos = {**packs.facts_prototypes(site()),
                  "roboport": {"type": "roboport", "width": 4, "height": 4},
                  "beacon": {"type": "beacon", "width": 3, "height": 3},
                  "passive-provider-chest": {"type": "logistic-container", "width": 1, "height": 1}}
        bp_ = {"version": V2, "entities": [
            {"name": "roboport", "position": {"x": 2, "y": 2}},
            {"name": "beacon", "position": {"x": 7.5, "y": 1.5}, "items": [{"id": {"name": "speed-module"}}]},
            {"name": "passive-provider-chest", "position": {"x": 10.5, "y": 0.5}},
            {"name": "inserter", "position": {"x": 11.5, "y": 0.5}, "use_filters": True,
             "filters": [{"index": 1, "name": "iron-plate"}]},
            {"name": "inserter", "position": {"x": 12.5, "y": 0.5}}]}
        plan, skipped, missing = packs.convert(bp_, protos)
        self.assertEqual(missing, [])
        self.assertEqual(sorted(e["prototype"] for e in plan["entities"]), ["inserter", "wooden-chest"])
        note = next(x for x in skipped if x.startswith("optional parts"))
        for part in ("roboports", "beacons", "filtered inserters", "logistic chests as wooden-chest"):
            self.assertIn(part, note)
        self.assertTrue(any("modules left out" in x for x in skipped))

    def test_rows_with_no_recipe_named_by_a_word_of_their_label(self):
        smelting = RECIPES + [{"name": "stone-brick", "category": "smelting", "ingredients": [{"name": "stone"}],
                               "products": [{"name": "stone-brick"}]},
                              {"name": "iron-plate", "category": "smelting", "ingredients": [{"name": "iron-ore"}],
                               "products": [{"name": "iron-plate"}]}]
        s = {**site(), "recipes": smelting}
        protos = packs.facts_prototypes(site())
        rows = packs.convert(furnaces()["blueprint"], protos)[0]["entities"]
        self.assertEqual(packs.named_items("Early Stone Smelter", rows, protos, s), ["stone-brick"])
        self.assertEqual(packs.named_items("Iron smelting 2x24", rows, protos, s), ["iron-plate"])
        self.assertEqual(packs.named_items("Main line", rows, protos, s), [])

    def test_a_mining_layout_for_any_ore_fits_whichever_patch_and_says_which(self):
        entry = {"footprint": [[0, 0], [1, 0], [0, 1], [1, 1]], "entities": [],
                 "resource_requirements": [{"entity_id": "d", "resource": bp.ANY_ORE, "tiles": [[0, 0]]}]}
        s = {"bounds": [0, 0, 8, 8], "resources": [{"resource": "copper-ore", "rows": [[3, 3, 4]]}]}
        found = bp.placements(entry, s)
        self.assertTrue(found)
        self.assertEqual({p["resource"] for p in found}, {"copper-ore"})

    def test_a_blueprint_takes_the_description_its_label_names(self):
        known = [{"label": "Stone Smelter Line", "section": "Resources", "text": "Smelts a full yellow belt.",
                  "source": "x", "words": packs._words("Stone Smelter Line")},
                 {"label": "Space Foundation", "section": "Space", "text": "Platform floor.", "source": "x",
                  "words": packs._words("Space Foundation")}]
        self.assertEqual(packs.describe("Early Stone Smelter", ("Space Age Blueprints", "Resources"), known)["label"],
                         "Stone Smelter Line")
        self.assertIsNone(packs.describe("Super Charged!", ("Space Age Blueprints", "Robo Grid"), known))

    def test_variants_of_one_design(self):
        self.assertEqual(packs.variant_of("Logistic Science AM2"), ("logistic science", 2))
        self.assertEqual(packs.variant_of("Gears In-Out (red)"), ("gears in out", 2))
        self.assertEqual(packs.variant_of("Red Science & Gears"), ("red science gears", 0))


class F16bRoundOneTests(unittest.TestCase):
    """Codex's F-16b review: indicators left out, a filter kept where it is part of the flow, mining layouts with no
    one ore's goal, a row named by its label counted as making it, and the game applying inserter filters."""

    def protos(self):
        return {**packs.facts_prototypes(site()),
                "small-lamp": {"type": "lamp", "width": 1, "height": 1},
                "constant-combinator": {"type": "constant-combinator", "width": 1, "height": 1},
                "roboport": {"type": "roboport", "width": 4, "height": 4}}

    def test_indicator_lamps_and_combinators_are_left_out(self):
        bp_ = {"version": V2, "entities": [{"name": "small-lamp", "position": {"x": 0.5, "y": 0.5}},
                                           {"name": "constant-combinator", "position": {"x": 1.5, "y": 0.5}},
                                           {"name": "inserter", "position": {"x": 2.5, "y": 0.5}}]}
        plan, skipped, missing = packs.convert(bp_, self.protos())
        self.assertEqual((missing, [e["prototype"] for e in plan["entities"]]), ([], ["inserter"]))
        self.assertIn("indicators", skipped[0])

    def test_a_filtered_inserter_is_a_marker_only_at_a_logistic_chest(self):
        protos = {**self.protos(), "passive-provider-chest": {"type": "logistic-container", "width": 1, "height": 1}}
        filtered = {"name": "inserter", "position": {"x": 2.5, "y": 0.5}, "use_filters": True,
                    "filters": [{"index": 1, "name": "iron-plate"}]}
        plain, _, _ = packs.convert({"version": V2, "entities": [filtered]}, protos)
        self.assertEqual(plain["entities"][0]["filters"], [{"index": 1, "name": "iron-plate"}])
        at_chest, _, _ = packs.convert({"version": V2, "entities": [
            filtered, {"name": "passive-provider-chest", "position": {"x": 3.5, "y": 0.5}}]}, protos)
        self.assertEqual([e["prototype"] for e in at_chest["entities"]], ["wooden-chest"])
        apart, _, _ = packs.convert({"version": V2, "entities": [
            filtered, {"name": "passive-provider-chest", "position": {"x": 9.5, "y": 0.5}}]}, protos)
        self.assertIn("inserter", [e["prototype"] for e in apart["entities"]])   # (part of the flow: kept)

    def test_combinators_with_no_lamps_are_not_indicators(self):
        bp_ = {"version": V2, "entities": [{"name": "constant-combinator", "position": {"x": 1.5, "y": 0.5}}]}
        self.assertTrue(packs.convert(bp_, self.protos())[2])          # (control the engineer cannot build: said)

    def test_a_mining_layout_for_any_ore_serves_a_job_on_one_ore(self):
        from companion import build_choice
        with tempfile.TemporaryDirectory() as d:
            catalog = bp.Catalog(Path(d) / "lib")
            entry = {"id": "m1", "purpose": "import:mining", "outputs": [], "entities": [], "stages": [],
                     "footprint": [[0, 0]], "size": [1, 1], "stage_bills": [], "prototypes": {}, "stats": {},
                     "resource_requirements": [{"entity_id": "d", "resource": bp.ANY_ORE, "tiles": [[0, 0]]}],
                     "schema": bp.SCHEMA, "source": "import:x"}
            catalog.save(entry)
            found = build_choice.peers(catalog, "coal-supply", (("coal",), ()))
            self.assertEqual([r["resource"] for f in found for r in f["resource_requirements"]], ["coal"])
            self.assertEqual(build_choice.peers(catalog, "brick-smelting", (("stone",), ("stone-brick",))), [])

    def test_a_filter_switched_off_reaches_the_game_off(self):
        entity = {"id": "i", "prototype": "inserter", "direction": 0, "position": {"x": 0.5, "y": 0.5},
                  "use_filters": False, "filters": [{"index": 1, "name": "iron-plate"}]}
        plan = {"entities": [entity], "stages": [{"id": "s", "depends_on": [], "entity_ids": ["i"],
                                                  "manual_supplies": [], "witnesses": []}]}
        built = bp.from_plan(plan, {"prototypes": {"inserter": {"type": "inserter", "width": 1, "height": 1}}},
                             source="t")
        self.assertIs(built["entities"][0]["use_filters"], False)

    def test_only_combinators_beside_a_lamp_are_indicators(self):
        bp_ = {"version": V2, "entities": [{"name": "small-lamp", "position": {"x": 0.5, "y": 0.5}},
                                           {"name": "constant-combinator", "position": {"x": 1.5, "y": 0.5}},
                                           {"name": "constant-combinator", "position": {"x": 20.5, "y": 0.5}}]}
        self.assertTrue(packs.convert(bp_, self.protos())[2])          # (the far one controls something: said)

    def test_a_mining_layout_naming_no_ore_is_mining_not_one_ore_s_goal(self):
        facts = dict(FACTS, **{"electric-mining-drill": {"kind": "drill", "type": "mining-drill", "width": 3,
                                                          "height": 3, "ready": True}})
        s = {**site(), "game_facts": {"entities": facts}}
        protos = packs.facts_prototypes(s)
        drills = packs.convert({"version": V2, "entities": [
            {"name": "electric-mining-drill", "position": {"x": 1.5, "y": 1.5}}]}, protos)[0]["entities"]
        self.assertEqual(packs.purpose_by_rules("30x4 Miners", drills, protos, [], set(), s), "import:mining")

    def test_a_row_named_by_its_label_counts_what_it_makes(self):
        smelting = RECIPES + [{"name": "stone-brick", "category": "smelting", "ingredients": [{"name": "stone"}],
                               "products": [{"name": "stone-brick"}]}]
        s = {**site(), "recipes": smelting}
        row = {"blueprint": {"label": "Early Stone Smelter", "version": V2, "entities": [
            {"name": "stone-furnace", "position": {"x": 1, "y": 1}}]}}
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "packs"
            folder.mkdir()
            (folder / "s.txt").write_text(packs.encode(row), encoding="utf-8")
            catalog = bp.Catalog(Path(d) / "data" / "blueprints")
            packs.sync(catalog, s, folder=folder, log=lambda line: None)
            entry = next(iter(catalog.blueprints.values()))
            # (what it makes kept as what it is; not offered as a job's layout until a goal feeds such rows)
            self.assertEqual((entry["purpose"], entry["import"]["products"], entry["outputs"]),
                             ("import:stone-brick", ["stone-brick"], []))

    def test_a_rail_build_is_said_not_imported_as_its_poles(self):
        rails = {"blueprint": {"label": "2-way segment", "version": V2, "entities": [
            {"name": "straight-rail", "position": {"x": 1, "y": k}} for k in range(1, 9, 2)] + [
            {"name": "small-electric-pole", "position": {"x": 3.5, "y": 0.5}}]}}
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "packs"
            folder.mkdir()
            (folder / "r.txt").write_text(packs.encode(rails), encoding="utf-8")
            catalog = bp.Catalog(Path(d) / "data" / "blueprints")
            record = packs.sync(catalog, site(), folder=folder, log=lambda line: None)[0]
            self.assertEqual([r["why"] for r in record["rejected"]], ["a rail build (rails and trains not placed yet)"])

    def test_the_game_applies_an_inserter_s_filters(self):
        sys.path.insert(0, str(ROOT / "tests"))
        import test_clearing
        lua = test_clearing.game()
        lua.execute("""
            prototypes.item = prototypes.item or {}
            prototypes.item['iron-plate'] = {name='iron-plate'}
            local s = DS.settings_copy({filters={{index=1, name='iron-plate'}, {index=2, name='bogus'}}}, 'inserter')
            ins = {position={x=0, y=0}, set = {}}
            function ins.set_filter(i, f) ins.set[i] = f.name end
            DS.apply_settings(ins, s)""")
        self.assertEqual(lua.eval("ins.set[1]"), "iron-plate")
        self.assertIsNone(lua.eval("ins.set[2]"))
        self.assertTrue(lua.eval("ins.use_filters"))
        lua.execute("""
            off = {position={x=0, y=0}, set = {}}
            function off.set_filter(i, f) off.set[i] = f.name end
            DS.apply_settings(off, DS.settings_copy({use_filters=false, filters={{index=1, name='iron-plate'}}},
                                                   'inserter'))""")
        self.assertFalse(lua.eval("off.use_filters"))                 # (dormant filters stay off)


if __name__ == "__main__":
    unittest.main()
