"""A row is built of what the engineer can make (companion/tier_builds.py modules_for and locked_parts,
blue_modules.row_module; W-23). death-world-waves-power: the defense row for 0.06 magazines a second had an
assembler 2 (the offline book's, allowed by default) and a fast inserter (sized for the machine flat out), neither
researched; the game blocked the plan the moment it was accepted, and the edge was held by hand again."""
from __future__ import annotations

import collections
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion import tier_builds as tb  # noqa: E402
from companion.ratios import Book  # noqa: E402


def parts(item, rate, machine="assembling-machine-1"):
    return collections.Counter(e.name for m in tb.modules_for(item, rate, Book.from_site({}), machine)
                               for e in m.entities)


class RowTests(unittest.TestCase):
    def test_its_own_assembler_not_the_offline_book_s_best(self):
        got = parts("firearm-magazine", 0.06)
        self.assertIn("assembling-machine-1", got)
        self.assertNotIn("assembling-machine-2", got)
        self.assertIn("assembling-machine-2", parts("firearm-magazine", 0.06, "assembling-machine-2"))

    def test_inserters_sized_for_the_row_s_rate(self):
        self.assertNotIn("fast-inserter", parts("firearm-magazine", 0.06))
        self.assertNotIn("fast-inserter", parts("gun-turret", 0.005))
        self.assertIn("fast-inserter", parts("firearm-magazine", 3.0))      # (a fast row still gets them)


class LockedTests(unittest.TestCase):
    SITE = {"recipes": [{"name": "inserter", "products": [{"name": "inserter"}]},
                        {"name": "assembling-machine-1", "products": [{"name": "assembling-machine-1"}]}],
            "inventory": {"fast-inserter": 1}}

    def test_what_it_can_neither_make_nor_holds_is_named(self):
        entities = [{"prototype": "assembling-machine-2"}, {"prototype": "inserter"}, {"prototype": "fast-inserter"}]
        self.assertEqual(tb.locked_parts(self.SITE, entities), ["assembling-machine-2"])   # (one fast inserter held)
        self.assertEqual(tb.locked_parts(self.SITE, entities + [{"prototype": "fast-inserter"}]),
                         ["assembling-machine-2", "fast-inserter"])                       # (two wanted, one held)

    def test_by_what_places_it(self):
        site = {**self.SITE, "recipes": self.SITE["recipes"] + [{"name": "maker-kit", "products": [{"name": "maker-kit"}]}],
                "prototypes": {"modded-maker": {"placement_item": "maker-kit"}}}
        self.assertEqual(tb.locked_parts(site, [{"prototype": "modded-maker"}]), [])

    def test_nothing_refused_without_a_recipe_list(self):
        self.assertEqual(tb.locked_parts({}, [{"prototype": "assembling-machine-2"}]), [])

    def test_the_rows_plan_refuses_them(self):
        src = (ROOT / "companion" / "tier_builds.py").read_text(encoding="utf-8")
        self.assertIn("locked = locked_parts(site, all_entities)", src)


if __name__ == "__main__":
    unittest.main()
