"""The team world's death-world start: the same large patches and pond round every plot's home, and the traits the
world gives every engineer, feeding the military research, guard and strategist paths."""
from pathlib import Path
import tempfile
import unittest

from lupa.lua52 import LuaRuntime

from companion import blueprints as bp, catalog_planner, goal_brain, strategist
from companion.catalog_planner import CatalogPlanner

ROOT = Path(__file__).resolve().parents[1]


def runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().team_source = (ROOT / "mod" / "scripts" / "team_world.lua").read_text(encoding="utf-8")
    lua.globals().personality_source = (ROOT / "mod" / "scripts" / "personality.lua").read_text(encoding="utf-8")
    lua.execute("""
        package.preload['scripts.team_world'] = function() return assert(load(team_source))() end
        scenario = 'team'
        settings = {global = setmetatable({}, {__index = function(_, k)
            if k == 'npc-factions-scenario' then return {value = scenario} end end})}
        storage = {}
        TW = require('scripts.team_world')
        Personality = assert(load(personality_source))()
    """)
    return lua


class StartLayout(unittest.TestCase):
    def setUp(self):
        self.lua = runtime()

    def pieces(self, home):
        layout = self.lua.eval(f"TW.start_layout({{x={home[0]}, y={home[1]}}})")
        return [(p.what, p.position.x - home[0], p.position.y - home[1], p.radius) for p in layout.values()]

    def test_every_home_gets_the_same_start_relative_to_it(self):
        a, b = self.pieces((0.5, 0.5)), self.pieces((-383.5, 192.5))
        self.assertEqual(a, b)
        self.assertEqual(sorted(p[0] for p in a), ["coal", "copper-ore", "iron-ore", "stone", "water"])

    def test_patches_are_large_and_apart_and_the_pond_near_home(self):
        pieces = self.pieces((0.5, 0.5))
        ores = [p for p in pieces if p[0] != "water"]
        pond = next(p for p in pieces if p[0] == "water")
        self.assertTrue(all(p[3] >= 20 for p in ores))
        for i, p in enumerate(ores):
            for q in ores[i + 1:]:
                gap = ((p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2) ** 0.5 - p[3] - q[3]
                self.assertGreater(gap, 30, f"room to build between {p[0]} and {q[0]}")
            self.assertGreater((p[1] ** 2 + p[2] ** 2) ** 0.5 - p[3], 30, "home stays free")
        self.assertTrue(20 <= (pond[1] ** 2 + pond[2] ** 2) ** 0.5 <= 30)
        for p in ores:
            gap = ((p[1] - pond[1]) ** 2 + (p[2] - pond[2]) ** 2) ** 0.5 - p[3] - pond[3]
            self.assertGreater(gap, 8, "room for offshore pumps round the pond")

    def test_the_start_fits_a_players_half_plot(self):
        layout = self.lua.eval("TW.layout({x=0, y=0})")
        for plot in layout.plots.values():
            if plot.kind == "yard":
                continue
            home = (plot.centre.x, plot.centre.y)
            box = [plot.box[i] for i in range(1, 5)]
            for what, dx, dy, r in self.pieces(home):
                x, y = home[0] + dx, home[1] + dy
                self.assertTrue(box[0] + 16 <= x - r and x + r <= box[2] - 16 and box[1] + 16 <= y - r
                                and y + r <= box[3] - 16, f"{what} inside {plot.name} with a margin")


class WorldTraits(unittest.TestCase):
    def setUp(self):
        self.lua = runtime()

    def traits(self, npc_id=2, evil=True):
        self.lua.execute(f"npc = {{id={npc_id}}}; ok = Personality.assign(npc, "
                         f"{{'{'evil' if evil else 'smart'}', 'reckless', 'curious'}}, 99)")
        return list(self.lua.eval("Personality.ensure(npc).traits").values())

    def test_no_world_traits_outside_a_death_world(self):
        self.assertNotIn("survivor", self.traits())
        self.lua.execute("storage.team = {death_world = true}; scenario = 'none'")
        self.assertNotIn("survivor", self.traits())

    def test_a_death_world_makes_every_engineer_a_survivor_and_a_good_teammate(self):
        self.lua.execute("storage.team = {death_world = true}")
        traits = self.traits()
        self.assertIn("survivor", traits)
        self.assertNotIn("evil", traits)       # (good guys on one team)
        self.assertNotIn("reckless", traits)
        self.assertIn("curious", traits)
        self.assertEqual(list(self.lua.eval("TW.world_traits()").values()), ["survivor"])
        self.assertGreater(self.lua.eval("Personality.ensure(npc).axes.defense"), 0.8)

    def test_a_drifted_engineer_keeps_its_drift_and_the_trait_goes_when_the_world_stops_giving_it(self):
        self.traits(evil=False)
        self.lua.execute("Personality.drift(npc, 'humor', 0.2, 'jokes', 60)")
        humor = self.lua.eval("Personality.ensure(npc).axes.humor")
        self.lua.execute("storage.team = {death_world = true}")
        p = self.lua.eval("Personality.ensure(npc)")
        self.assertIn("survivor", list(p.traits.values()))
        self.assertAlmostEqual(p.axes.humor, humor, places=6)          # (survivor leaves humor alone)
        self.assertEqual(len(p.drift_log), 1)
        self.lua.execute("storage.team.death_world = false")
        traits = list(self.lua.eval("Personality.ensure(npc).traits").values())
        self.assertNotIn("survivor", traits)
        self.assertIn("smart", traits)

    def test_traits_that_gave_way_come_back_when_the_world_stops_giving_survivor(self):
        self.lua.execute("storage.team = {death_world = true}")
        self.assertNotIn("evil", self.traits())
        self.lua.execute("storage.team.death_world = false")
        traits = list(self.lua.eval("Personality.ensure(npc).traits").values())
        self.assertEqual(sorted(traits), ["curious", "evil", "reckless"])

    def test_survivor_is_never_drawn(self):
        self.assertNotIn("survivor", [t.name for t in self.lua.eval("Personality.catalog()").values()])

    def test_the_save_makers_switch_sets_the_flag(self):
        self.lua.execute("""
            storage.team = {}
        """)
        self.assertFalse(self.lua.eval("TW.death_world()"))
        self.lua.execute("storage.team.death_world = true")
        self.assertTrue(self.lua.eval("TW.death_world()"))


class Priors(unittest.TestCase):
    def request(self, traits, military=1, onward=3):
        return {"personality": {"traits": traits}, "researched": {"military": military, "rocket_path": onward}}

    def test_a_survivor_researches_arms_sooner_but_the_biters_still_bound_it(self):
        self.assertFalse(catalog_planner.military_due(self.request(["smart"]))[0])
        self.assertTrue(catalog_planner.military_due(self.request(["survivor"]))[0])
        # (the share never passes what fully evolved biters ask for)
        self.assertFalse(catalog_planner.military_due(self.request(["survivor"], military=7, onward=3))[0])

    def research(self, traits, team=True, military=1, onward=3):
        request = {"request_id": "1:r1", "npc_id": 1, "team": team or None, "personality": {"traits": traits},
                   "researched": {"military": military, "rocket_path": onward},
                   "options": [{"name": "automation", "units": 10, "unlocks": ["assembling-machine-1"]},
                               {"name": "military", "units": 10, "military": True, "unlocks": ["submachine-gun"]}]}
        with tempfile.TemporaryDirectory() as folder:
            planner = CatalogPlanner(bp.Catalog(folder), log=lambda _: None)
            return planner.choose_research(request)

    def test_a_survivor_takes_military_research_first_within_the_teams_share(self):
        self.assertEqual(self.research(["smart"]), "automation")
        self.assertEqual(self.research(["survivor"]), "military")
        self.assertEqual(self.research(["survivor"], military=0, onward=0), "military")   # (the very first pick)
        self.assertEqual(self.research(["smart"], military=0, onward=0), "automation")
        # its share met (the biters at their worst ask no more): the factory's research goes on
        self.assertEqual(self.research(["survivor"], military=7, onward=3), "automation")
        # outside the team world, the personality route arms it first
        self.assertEqual(self.research(["survivor"], team=False), "military")

    def test_a_survivor_weighs_defense_goals_up_in_the_scores(self):
        w = goal_brain.default_weights()
        parts = {"value": 1.0, "unlocks": 0.0, "speedup": 0.0, "hand_saved": 0.0, "hand_added": 0.0, "cost": 0.0,
                 "prior": 0.0, "under_way": False, "odds": 1.0, "guard": 0.4}
        plain = goal_brain.score({**parts, "militant": False}, w)
        self.assertGreater(goal_brain.score({**parts, "militant": True}, w), plain + 1.0)
        self.assertEqual(goal_brain.score({**parts, "guard": 0.0, "militant": True}, w),
                         goal_brain.score({**parts, "guard": 0.0}, w))   # (only what guards)

    def test_a_survivor_weighs_its_defenses_up_before_any_sign(self):
        plain = goal_brain.guard_of({"personality": {"traits": ["smart"]}}).value
        survivor = goal_brain.guard_of({"personality": {"traits": ["survivor"]}}).value
        self.assertGreaterEqual(survivor, goal_brain.GUARD_MILITANT)
        self.assertGreater(survivor, plain)

    def test_the_strategist_hears_it_is_a_death_world(self):
        self.assertIn("survival first", strategist.team_instructions({"death_world": True}))
        self.assertNotIn("survival first", strategist.team_instructions({}))


if __name__ == "__main__":
    unittest.main()
