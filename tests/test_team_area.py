"""The team world's building rules for the planners (companion/team_area.py)."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion import team_area  # noqa: E402
from companion.ratios import Book  # noqa: E402

# npc-1's plot of the team world, in world tiles; a player's chunk inside it (widened by 32)
TEAM = {"plot": [-576, -576, -192, -192], "area": [-576, -576, 576, 576], "no_build": [[-352, -352, -256, -256]]}


class RuleTests(unittest.TestCase):
    def test_own_plot_or_past_the_wall_never_another_plot_or_near_players(self):
        team = team_area.team_of({"team": TEAM})
        self.assertTrue(team.box_ok(-400, -400, -397, -397))             # its plot
        self.assertFalse(team.box_ok(-100, 0, -97, 3))                   # the players'
        self.assertFalse(team.box_ok(-194, -300, -190, -297))            # astride its edge
        self.assertTrue(team.box_ok(-700, -700, -695, -695))             # an outpost past the wall
        self.assertFalse(team.box_ok(-580, -400, -570, -397))            # astride the wall line
        self.assertFalse(team.box_ok(-300, -300, -297, -297))            # by a player's wall
        tiles = team.forbidden_tiles((-600, -600, -150, -150))
        self.assertIn((-170, -400), tiles)                               # npc-2's plot
        self.assertIn((-300, -300), tiles)
        self.assertNotIn((-400, -400), tiles)
        self.assertNotIn((-590, -590), tiles)                            # beyond the team area
        self.assertIsNone(team_area.team_of({}))                          # other games: no rules
        self.assertEqual(team_area.forbidden({"area": [0, 0, 10, 10]}), set())

    def test_a_finished_plan_with_a_building_in_another_plot_is_caught(self):
        site = {"team": {"plot": [0, 0, 64, 64], "area": [-100, -100, 200, 200], "no_build": []},
                "prototypes": {"stone-furnace": {"width": 2, "height": 2}}}
        plan = {"entities": [{"id": "a", "prototype": "stone-furnace", "position": {"x": 10, "y": 10}},
                             {"id": "b", "prototype": "stone-furnace", "position": {"x": 64, "y": 10}},
                             {"id": "c", "prototype": "electric-mining-drill", "position": {"x": 63.5, "y": 30.5}}]}
        self.assertEqual(team_area.misplaced(plan, site), ["b", "c"])   # (a 3x3 drill pokes out too)
        self.assertEqual(team_area.misplaced(plan, {}), [])


class PlannerTests(unittest.TestCase):
    def test_a_bus_base_on_a_real_map_stays_in_its_plot_and_clear_of_players_builds(self):
        from companion.base_plan import plan_bus_base
        from companion.smelting import footprint
        world = json.loads((ROOT / "tests" / "fixtures" / "k-blue-map-256.json").read_text(encoding="utf-8"))
        # its plot is the middle of the map (home at its centre); a player built at (-60, 60)
        world["team"] = {"plot": [-192, -192, 192, 192], "area": [-576, -576, 576, 576],
                         "no_build": [[-96, 32, 0, 128]]}
        team = team_area.team_of(world)
        layout = plan_bus_base(world, Book.vanilla(), science_per_minute=15,
                               packs=("automation-science-pack", "logistic-science-pack"), columns=12)
        entities = layout.entities()
        self.assertGreater(len(entities), 200)
        outside = [e for e in entities for t in footprint([e]) if not team.tile_ok(*t)]
        self.assertEqual(outside, [])
        for spot in team_area.pumps(world):
            self.assertTrue(team.box_ok(spot["x"] - 1, spot["y"] - 1, spot["x"] + 1, spot["y"] + 1))


if __name__ == "__main__":
    unittest.main()


class FitsTests(unittest.TestCase):
    def test_a_planner_s_site_search_uses_the_same_plot_test_as_the_plan_check(self):
        team = team_area.team_of({"team": {"plot": [0, 0, 64, 64], "area": [-100, -100, 200, 200], "no_build": []}})
        self.assertTrue(team_area.fits(team, "boiler", 10.5, 10.5, 0))
        self.assertFalse(team_area.fits(team, "steam-engine", 1.5, 30.5, 0))   # (5 wide by the size table: spills)
        plan = {"entities": [{"id": "a", "prototype": "steam-engine", "position": {"x": 1.5, "y": 30.5}}]}
        self.assertEqual(team_area.misplaced(plan, {"team": {"plot": [0, 0, 64, 64], "area": [-100, -100, 200, 200],
                                                             "no_build": []}}), ["a"])
        self.assertTrue(team_area.fits(None, "steam-engine", 1.5, 30.5, 0))    # (no team rules: anywhere)


class DeathWorldTests(unittest.TestCase):
    def test_read_from_the_site_s_team_table_or_the_top_of_a_research_request(self):
        self.assertTrue(team_area.death_world({"team": {**TEAM, "death_world": True}}))
        self.assertFalse(team_area.death_world({"team": TEAM}))                     # (a normal team world: left out)
        self.assertTrue(team_area.death_world({"team": True, "death_world": True}))  # (a research request)
        self.assertFalse(team_area.death_world({"team": True}))
        self.assertFalse(team_area.death_world({}))
        self.assertFalse(team_area.death_world(None))
        # (the game sends it in both)
        start = (ROOT / "mod" / "scripts" / "designed_start.lua").read_text(encoding="utf-8")
        self.assertIn("death_world=team.death_world}", start)
        self.assertIn("death_world=TeamWorld.death_world() or nil", start)
