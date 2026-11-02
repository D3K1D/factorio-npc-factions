"""The yard bands lie far from home: the plan's map reaches them and the game's bounds take them
(companion/perimeter.py map_window, catalog_planner._world_over, designed_start.lua validate)."""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion import perimeter, team_area  # noqa: E402
from companion.catalog_planner import CatalogPlanner  # noqa: E402

WINDOW = 32
HOME = (384, 0)                       # npc-5's home, at its plot's centre
ORIGIN = (HOME[0] - WINDOW, HOME[1] - WINDOW)


def local(r):
    return [r[0] - ORIGIN[0], r[1] - ORIGIN[1], r[2] - ORIGIN[0], r[3] - ORIGIN[1]]


SITE = {"npc_id": 5, "origin": {"x": ORIGIN[0], "y": ORIGIN[1]}, "bounds": [0, 0, 64, 64],
        "home": {"x": WINDOW, "y": WINDOW},
        "team": {"plot": local([192, -192, 576, 192]), "area": local([-576, -576, 576, 576]), "no_build": [],
                 "outer": ["east"],
                 "bands": [{"name": "east", "box": local([552, 192, 576, 576]), "corner_lo": False,
                            "corner_hi": True}]},
        "defense": {"turrets": [], "walls": [{"name": "stone-wall", "health": 450}],
                    "gates": [{"name": "gate", "health": 350}], "ammo": {}, "repair": []}}


def world_with_own_east_wall():
    line = perimeter.edges(SITE)[0]              # its own east edge, walled already
    walls = [{"n": "stone-wall", "t": "wall", "x": t[0] + 0.5, "y": t[1] + 0.5, "w": 1, "h": 1,
              "f": "npc-faction-5"} for t in (line.tile(a, perimeter.WALL_OFFSET) for a in range(line.lo, line.hi + 1))]
    center, radius = perimeter.map_window(SITE)
    return {"ok": True, "force": "npc-faction-5", "home": center,
            "area": [center["x"] - radius, center["y"] - radius, center["x"] + radius, center["y"] + radius],
            "entities": walls, "resources": {}, "water": [], "pumps": [],
            "team": {"plot": [192, -192, 576, 192], "area": [-576, -576, 576, 576], "no_build": [], "outer": ["east"],
                     "bands": [{"name": "east", "box": [552, 192, 576, 576], "corner_lo": False, "corner_hi": True}]}}


def inside_bounds(p, reach):
    """designed_start.lua validate(): positions within reach - WINDOW of the plan's window."""
    margin = reach - WINDOW
    return -margin <= p["x"] <= 2 * WINDOW + margin and -margin <= p["y"] <= 2 * WINDOW + margin


class YardReachTests(unittest.TestCase):
    def test_the_map_for_the_edges_covers_the_plot_and_its_band(self):
        center, radius = perimeter.map_window(SITE)
        self.assertEqual((center, radius), ({"x": 384.0, "y": 192.0}, 400))   # (the game's largest dump)
        inner = {**SITE, "team": {**SITE["team"], "bands": []}}
        self.assertEqual(perimeter.map_window(inner), ({"x": 384.0, "y": 0.0}, 208))

    def test_the_yard_wall_plan_is_one_the_game_takes(self):
        plan = perimeter.plan_wall(SITE, world_with_own_east_wall())
        positions = [e["position"] for e in plan["entities"]]
        self.assertTrue(positions)
        self.assertTrue(all(p["y"] + ORIGIN[1] > 192 for p in positions))        # the band, down the yard
        self.assertFalse(all(inside_bounds(p, 512) for p in positions))          # the old reach refused it
        self.assertTrue(all(inside_bounds(p, 1024) for p in positions))          # the team world's takes it
        self.assertEqual(team_area.misplaced(plan, SITE), [])

    def test_the_game_reaches_further_in_the_team_world_only(self):
        source = (ROOT / "mod" / "scripts" / "designed_start.lua").read_text(encoding="utf-8")
        self.assertRegex(source, re.escape("local margin=(TeamWorld.active() and 1024 or PLAN_REACH)-WINDOW"))

    def test_the_catalog_asks_the_bridge_for_that_map(self):
        asked = []

        def bridge_world(site, center=None, radius=None):
            asked.append((center, radius))
            return world_with_own_east_wall()
        planner = CatalogPlanner.__new__(CatalogPlanner)
        planner.world = bridge_world
        planner._world_over(SITE, perimeter.map_window(SITE))
        self.assertEqual(asked, [({"x": 384.0, "y": 192.0}, 400)])
        planner.world = lambda site: {"home only": True}             # a map source that maps round home only
        self.assertEqual(planner._world_over(SITE, perimeter.map_window(SITE)), {"home only": True})


if __name__ == "__main__":
    unittest.main()
