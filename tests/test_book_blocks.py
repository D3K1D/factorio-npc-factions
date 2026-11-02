"""A book block's ports read from its own belts (companion/book_blocks.py), on the user's Science Book blocks."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion import book_blocks  # noqa: E402
from companion.modules import EAST, WEST  # noqa: E402

# "1.0 Science Book - Tileable (v3.7)" from the user's Blueprints\2 Blueprint Books.txt: blocks 2 (lab chain),
# 3 (red and green, 60/min) and 5 (blue, 45/min), converted as the library converts them; the recipes they use.
FIXTURE_FILE = ROOT / "tests" / "fixtures" / "science-book-blocks.json"   # (third-party blueprints: not in every checkout)
FIXTURE = json.loads(FIXTURE_FILE.read_text(encoding="utf-8")) if FIXTURE_FILE.exists() else None
FACTS = json.loads((ROOT / "tests" / "fixtures" / "game-facts-2.1.json").read_text(encoding="utf-8"))
SITE = {"game_facts": FACTS, "recipes": FIXTURE["recipes"] if FIXTURE else []}


def traced(block):
    return book_blocks.trace(FIXTURE["blocks"][block]["entities"], SITE)


def by_item(ports, way):
    return {p["item"]: p for p in ports if p["way"] == way}


@unittest.skipUnless(FIXTURE, "the Science Book fixture is not here")
class TraceTests(unittest.TestCase):
    def test_red_and_green_take_iron_and_copper_on_one_lane(self):
        t = traced("3")
        self.assertEqual(t["bus_side"], "west")
        ins = by_item(t["ports"], "in")
        self.assertEqual(set(ins), {"iron-plate", "copper-plate"})
        self.assertIsNone(ins["iron-plate"]["lane"])
        # (its gear assembler puts gears on the copper belt's south lane, but the block side-loads the incoming copper
        # onto the north lane itself, by a detour at x 11-13: a full copper belt is fine, no lane asked)
        self.assertIsNone(ins["copper-plate"]["lane"])
        self.assertEqual(ins["copper-plate"]["direction"], EAST)
        self.assertEqual((ins["iron-plate"]["edge"], ins["copper-plate"]["edge"]), ("west", "west"))
        self.assertEqual(ins["copper-plate"]["y"] - ins["iron-plate"]["y"], 12)   # (rows 1 and 13 of the block)
        outs = by_item(t["ports"], "out")
        self.assertEqual(set(outs), {"automation-science-pack", "logistic-science-pack"})
        self.assertEqual((outs["automation-science-pack"]["lane"], outs["logistic-science-pack"]["lane"]),
                         ("right", "left"))
        self.assertEqual(outs["automation-science-pack"]["direction"], WEST)
        self.assertEqual(sorted(p["of"][0] for p in t["passes"]), ["copper-plate", "iron-plate"])   # (for tiling)

    def test_blue_takes_its_circuits_and_sulfur_side_loaded_iron_and_steel(self):
        t = traced("5")
        ins = by_item(t["ports"], "in")
        self.assertEqual(set(ins), {"advanced-circuit", "sulfur", "iron-plate", "steel-plate"})
        # (two belts side-loaded into one: either on either; each takes both lanes of its own belt)
        self.assertEqual(ins["sulfur"]["items"], ["advanced-circuit", "sulfur"])
        self.assertIsNone(ins["sulfur"]["lane"])
        self.assertAlmostEqual(ins["advanced-circuit"]["rate"], 1.125)
        self.assertAlmostEqual(ins["sulfur"]["rate"], 0.375)
        self.assertEqual(set(by_item(t["ports"], "out")), {"chemical-science-pack"})

    def test_the_lab_chain_takes_any_pack(self):
        t = traced("2")
        ins = [p for p in t["ports"] if p["way"] == "in"]
        self.assertEqual(len(ins), 4)
        self.assertTrue(all(p["edge"] == "west" for p in ins))

    def test_a_module_for_the_bus(self):
        m = book_blocks.module(FIXTURE["blocks"]["3"], SITE)
        self.assertEqual((m.width, m.height, m.bus_side), (54, 14, "west"))
        copper = next(p for p in m.ports if p.item == "copper-plate")
        self.assertEqual((copper.kind, copper.x, copper.y, copper.lane), ("in", 0, 13, None))
        self.assertEqual({p.item for p in m.ports if p.kind == "out"},
                         {"automation-science-pack", "logistic-science-pack"})


def belt(x, y, d, kind=None):
    row = {"prototype": "underground-belt" if kind else "transport-belt", "position": {"x": x + 0.5, "y": y + 0.5},
           "direction": d}
    return {**row, "belt_to_ground_type": kind} if kind else row


def machine(x, y, recipe):
    return {"prototype": "assembling-machine-2", "position": {"x": x + 0.5, "y": y + 0.5}, "direction": 0,
            "recipe": recipe}


def inserter(x, y, d):
    return {"prototype": "inserter", "position": {"x": x + 0.5, "y": y + 0.5}, "direction": d}


@unittest.skipUnless(FIXTURE, "the Science Book fixture is not here")
class GeometryTests(unittest.TestCase):
    """Small blocks laid out here: a belt from the west edge along row 3."""

    def test_an_input_sharing_its_belt_with_the_blocks_own_items_takes_the_other_lane(self):
        # gears put on from the north (the left of an eastbound belt land on the right lane); red takes from the south
        ents = [belt(x, 3, EAST) for x in range(0, 7)] + [machine(2, 0, "iron-gear-wheel"), inserter(2, 2, 0),
                                                         machine(4, 6, "automation-science-pack"), inserter(4, 4, 0)]
        ins = by_item(book_blocks.trace(ents, SITE)["ports"], "in")
        self.assertEqual(ins["copper-plate"]["lane"], "left")

    def test_a_product_put_on_a_pass_through_belt_goes_out(self):
        # iron along row 3 through the block; a gear assembler takes it and puts gears back on the same belt
        ents = [belt(x, 3, EAST) for x in range(0, 8)] + [machine(2, 0, "iron-gear-wheel"), inserter(1, 2, 8),
                                                         inserter(3, 2, 0)]
        t = book_blocks.trace(ents, SITE)
        self.assertEqual(set(by_item(t["ports"], "in")), {"iron-plate"})
        self.assertEqual(set(by_item(t["ports"], "out")), {"iron-gear-wheel"})
        self.assertEqual(len(t["passes"]), 1)

    def test_an_entrance_finds_its_exit_past_another_entrance(self):
        # entrance, an unrelated entrance of the same kind facing the same way, then the first one's exit
        ents = [belt(0, 3, EAST), belt(1, 3, EAST, "input"), belt(3, 3, EAST, "input"), belt(5, 3, EAST, "output"),
                belt(6, 3, EAST), machine(7, 4, "iron-gear-wheel"), inserter(6, 4, 0)]
        ins = by_item(book_blocks.trace(ents, SITE)["ports"], "in")
        self.assertIn("iron-plate", ins)


@unittest.skipUnless(FIXTURE, "the Science Book fixture is not here")
class ReachAndSideLoadTests(unittest.TestCase):
    def test_an_exit_beyond_reach_is_not_its_pair(self):
        # (yellow undergrounds: an exit at most 5 tiles on, 4 between)
        far = [belt(0, 3, EAST, "input"), belt(6, 3, EAST, "output"), belt(7, 3, EAST),
               machine(8, 1, "iron-gear-wheel"), inserter(7, 2, 8)]
        near = [belt(0, 3, EAST, "input"), belt(5, 3, EAST, "output"), belt(6, 3, EAST), belt(7, 3, EAST),
                machine(8, 1, "iron-gear-wheel"), inserter(7, 2, 8)]
        self.assertNotIn("iron-plate", by_item(book_blocks.trace(far, SITE)["ports"], "in"))
        self.assertIn("iron-plate", by_item(book_blocks.trace(near, SITE)["ports"], "in"))

    def test_an_output_side_loaded_from_the_north_comes_out_on_the_left(self):
        # gears put on a southbound feeder's right lane, side-loaded from the north onto an eastbound belt with its own
        # feeder behind: on the eastbound belt they are on its north lane, its left
        ents = [belt(0, 4, EAST), belt(1, 4, EAST), belt(2, 4, EAST), belt(3, 4, EAST),
                belt(2, 2, SOUTH := 8), belt(2, 3, 8), machine(0, 2, "iron-gear-wheel"), inserter(1, 2, 12)]
        outs = by_item(book_blocks.trace(ents, SITE)["ports"], "out")
        self.assertEqual(outs["iron-gear-wheel"]["lane"], "left")


@unittest.skipUnless(FIXTURE, "the Science Book fixture is not here")
class LibraryTests(unittest.TestCase):
    def locked_or_waiting(self, raw, researched):
        """Green's recipe known to the game but not researched: imported now, locked; a recipe the game does not know
        (another mod set's): it waits (Factory's U-2: the bus plans its room ahead of green's research)."""
        from companion import blueprint_packs as packs, blueprints as bp
        green = next(r for r in SITE["recipes"] if r["name"] == "logistic-science-pack")
        early = [r for r in SITE["recipes"] if r["name"] != "logistic-science-pack"] + researched
        facts = {**FACTS, "recipes": {green["name"]: {k: v for k, v in green.items() if k != "name"}}}
        for known, imported in ((facts, 1), (FACTS, 0)):
            site = {"game_facts": known, "recipes": early}
            with tempfile.TemporaryDirectory() as d:
                folder = Path(d) / "packs"
                folder.mkdir()
                (folder / "science.txt").write_text(packs.encode({"blueprint": raw}), encoding="utf-8")
                catalog = bp.Catalog(Path(d) / "data" / "blueprints")
                record = packs.sync(catalog, site, folder=folder, log=lambda line: None)[0]
                builds = list(catalog.blueprints.values())
            self.assertEqual(len(builds), imported, known is facts)
            if imported:
                self.assertIn("logistic-science-pack", builds[0]["outputs"])
                self.assertIn("recipe:logistic-science-pack", bp.locked_items(builds[0], site))
            else:
                self.assertEqual(record["waiting"][0]["recipes"], ["logistic-science-pack"])

    def test_the_library_takes_the_traced_ports(self):
        from companion import blueprint_packs as packs, blueprints as bp
        block = FIXTURE["blocks"]["3"]
        raw = {"label": block["label"], "version": 562949954076673,
               "entities": [{"name": e["prototype"], "position": e["position"], "direction": e.get("direction", 0),
                             **({"recipe": e["recipe"]} if e.get("recipe") else {}),
                             **({"type": e["belt_to_ground_type"]} if e.get("belt_to_ground_type") else {})}
                            for e in block["entities"]]}
        researched = [{"name": n, "products": [{"name": n, "amount": 1}]} for n in
                      {e["prototype"] for e in block["entities"]}]
        site = {**SITE, "recipes": SITE["recipes"] + researched}
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "packs"
            folder.mkdir()
            (folder / "science.txt").write_text(packs.encode({"blueprint": raw}), encoding="utf-8")
            catalog = bp.Catalog(Path(d) / "data" / "blueprints")
            packs.sync(catalog, site, folder=folder, log=lambda line: None)
            imports = [b["import"] for b in catalog.blueprints.values() if b.get("import")]
        self.assertEqual(len(imports), 1)
        self.assertEqual(imports[0]["ports_from"], "trace")
        self.locked_or_waiting(raw, researched)
        copper = next(p for p in imports[0]["ports"] if p["item"] == "copper-plate")
        self.assertIsNone(copper["lane"])


if __name__ == "__main__":
    unittest.main()
