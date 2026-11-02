"""A planned piece the game refuses is never refused for "nothing" (scripts/obstacles.lua's survey): every kind of
blocker is named (the engineer itself on the spot, even only touching its edge; someone else's character; ground the
piece cannot stand on; else the game is asked), a passing cause is tried again, a lasting one is handed back, and the
spot handed back is kept off by the planners so a layout is put elsewhere. (The proving ground, 2026-10-28: a saved lab
layout was refused at "nothing_seen" three times, the engineer standing on the lab's edge.)"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_obstacles  # noqa: E402

FURNACE = "p = plan_of({{'f', 'stone-furnace', 5.5, 0.5}})"


def ladder():
    lua = test_obstacles.ladder()
    lua.execute("""
        character = npc.character
        character.valid = true
        -- far from the spot unless a test moves it there
        character.bounding_box = {left_top={x=-50.2, y=-0.2}, right_bottom={x=-49.8, y=0.2}}
        function stand(x, y)
            character.position = {x=x, y=y}
            character.bounding_box = {left_top={x=x-0.2, y=y-0.2}, right_bottom={x=x+0.2, y=y+0.2}}
        end
        Ob = DS.Obstacles
    """)
    return lua


class SurveyTests(unittest.TestCase):
    def test_itself_on_the_spot_is_named_and_it_steps_aside_rather_than_hand_back(self):
        lua = ladder()
        lua.execute(FURNACE + "; stand(5.5, 0.5); fail('f', 'placement_blocked_or_out_of_reach')")
        self.assertEqual(lua.eval("last().rung"), "clear")
        self.assertIn("self", lua.eval("last().what"))
        self.assertEqual(lua.eval("npc.design.status"), "building")

    def test_itself_only_touching_the_edge_counts_too(self):
        lua = ladder()
        # the furnace's box is 5..6: the character's right edge on 5.0 exactly
        lua.execute(FURNACE + "; stand(4.8, 0.5); s = Ob.survey(npc, {{5, 0}, {6, 1}}, nil, 'stone-furnace')")
        self.assertTrue(lua.eval("s.self"))
        lua.execute("stand(4.6, 0.5); s = Ob.survey(npc, {{5, 0}, {6, 1}}, nil, 'stone-furnace')")
        self.assertFalse(lua.eval("s.self"))   # (a tile's fifth away: clear)

    def test_someone_elses_character_is_named_and_waited_out(self):
        lua = ladder()
        lua.execute(FURNACE + """
            prototypes.entity['character'] = {name='character', type='character', collision_mask={layers={player=true}},
                                              mineable_properties={minable=false}}
            mate = put('character', 5.5, 0.5); mate.force = {name='teammate'}
            fail('f', 'placement_blocked_or_out_of_reach')""")
        self.assertIn("characters:1", lua.eval("last().what"))
        self.assertEqual(lua.eval("last().rung"), "clear")

    def test_ground_it_cannot_stand_on_is_named_and_handed_back(self):
        lua = ladder()
        lua.execute(FURNACE + """
            surface.find_tiles_filtered = function(f)
                if f.collision_mask == 'water_tile' then return {} end
                return {{name='out-of-map', position={x=5, y=0},
                         collides_with=function(layer) return layer ~= 'water_tile' end}}
            end
            fail('f', 'placement_blocked_or_out_of_reach')""")
        self.assertIn("tiles:out-of-map", lua.eval("last().what"))
        self.assertNotEqual(lua.eval("last().rung"), "clear")   # (never cleared: left out where safe, else handed back)
        lua.execute("p.stages[1].witnesses = {{kind='entity_built', entity_id='f'}}; "
                    "p.entities.f.failures = 0; p.entities.f.rungs = nil; p.entities.f.left_out = nil; "
                    "npc.design.status = 'building'; fail('f', 'placement_blocked_or_out_of_reach')")
        if lua.eval("last().rung") == "hand_back":
            self.assertEqual(lua.eval("npc.unbuildable[#npc.unbuildable].kind"), "tile")

    def test_nothing_there_and_it_fits_now_is_tried_again(self):
        lua = ladder()
        lua.execute(FURNACE + "; fail('f', 'placement_blocked_or_out_of_reach')")
        self.assertIn("clear_now", lua.eval("last().what"))
        self.assertEqual(lua.eval("last().rung"), "clear")

    def test_nothing_there_and_the_game_still_refuses_is_named_refused_and_handed_back(self):
        lua = ladder()
        lua.execute(FURNACE + "; surface.can_place_entity = function() return false end; "
                    "fail('f', 'placement_blocked_or_out_of_reach')")
        self.assertIn("refused", lua.eval("last().what"))
        self.assertNotIn("nothing_seen", lua.eval("last().what"))
        self.assertIn(lua.eval("last().rung"), ("left_out", "hand_back"))   # (a lasting cause: never tried as is)


class GameCheckTests(unittest.TestCase):
    """The game's own check comes first, before any trip (the user, 2026-10-28: "use the blueprint placement system
    like the vanilla game to check if something is blocking a placement and if its breakable or not")."""

    @staticmethod
    def game(answer):
        lua = ladder()
        lua.execute(FURNACE + """
            asked = {}
            answer = '%s'
            surface.can_place_entity = function(spec)
                asked[#asked + 1] = spec.forced and 'blueprint' or 'manual'
                if answer == 'free' then return true end
                if answer == 'clearable' then return spec.forced == true end
                return false
            end""" % answer)
        return lua

    def test_a_spot_that_fits_goes_on_to_be_built(self):
        lua = self.game("free")
        lua.execute("job, why = DS.check_spot(npc, npc.design, p.entities.f, 10)")
        self.assertIsNone(lua.eval("job"))
        self.assertIsNone(lua.eval("why"))
        self.assertEqual(lua.eval("#asked"), 1)

    def test_only_what_a_blueprint_clears_is_broken_by_hand_first(self):
        lua = self.game("clearable")
        lua.execute("tree = put('oak-of-legend', 5.5, 0.5); job, meta = DS.check_spot(npc, npc.design, p.entities.f, 10)")
        self.assertEqual(lua.eval("job.kind"), "mine")
        self.assertEqual(lua.eval("job.target.name"), "oak-of-legend")
        self.assertTrue(lua.eval("meta.clear"))
        self.assertEqual(lua.eval("last().rung"), "break")

    def test_clearable_by_nothing_it_may_break_goes_up_the_ladder(self):
        lua = self.game("clearable")
        lua.execute("put('ridge', 5.5, 0.5); job, why = DS.check_spot(npc, npc.design, p.entities.f, 10)")
        self.assertIsNone(lua.eval("job"))
        self.assertTrue(lua.eval("why").startswith("in_the_way:"))
        self.assertIn("cliffs:1", lua.eval("last().what"))

    def test_blocked_goes_up_the_ladder_at_once_with_no_trip(self):
        lua = self.game("blocked")
        lua.execute("theirs('old-ruin-wall', 5.5, 0.5); job, why = DS.check_spot(npc, npc.design, p.entities.f, 10)")
        self.assertIsNone(lua.eval("job"))
        self.assertIn("kept:", lua.eval("last().what"))
        self.assertNotEqual(lua.eval("last().rung"), "clear")
        self.assertEqual(lua.eval("p.entities.f.failures or 0"), 0)   # (never tried: the game said so first)

    def test_someone_passing_is_waited_for_before_any_rung_is_spent(self):
        lua = self.game("blocked")
        lua.execute("""
            prototypes.entity['character'] = {name='character', type='character', collision_mask={layers={player=true}},
                                              mineable_properties={minable=false}}
            mate = put('character', 5.5, 0.5); mate.force = {name='teammate'}
            n0 = #(npc.obstacle_log or {})
            job, why = DS.check_spot(npc, npc.design, p.entities.f, 10)
            job2, why2 = DS.check_spot(npc, npc.design, p.entities.f, 1000)""")
        self.assertTrue(lua.eval("why").startswith("waiting_for_passing:"))
        self.assertTrue(lua.eval("why2").startswith("waiting_for_passing:"))
        self.assertEqual(lua.eval("#(npc.obstacle_log or {}) - n0"), 0)   # (no rung spent on it)
        lua.execute("job3, why3 = DS.check_spot(npc, npc.design, p.entities.f, 10 + DS.PASSING_WAIT)")
        self.assertTrue(lua.eval("why3").startswith("in_the_way:"))     # (still there after a minute: the ladder)

    def test_nothing_is_broken_that_does_not_collide_or_yields_nothing(self):
        lua = self.game("clearable")
        lua.execute("put('moss', 5.5, 0.5); put('ridge', 5.6, 0.5); job, why = DS.check_spot(npc, npc.design, p.entities.f, 10)")
        self.assertIsNone(lua.eval("job"))   # (moss is a doodad under it; the cliff is the ladder's)

    def test_itself_on_the_spot_is_left_to_the_worker_to_step_off(self):
        lua = self.game("blocked")
        lua.execute("stand(5.5, 0.5); job, why = DS.check_spot(npc, npc.design, p.entities.f, 10)")
        self.assertIsNone(lua.eval("job"))
        self.assertIsNone(lua.eval("why"))


class AvoidTests(unittest.TestCase):
    def test_spots_handed_back_are_exported_until_what_stood_there_is_gone(self):
        lua = ladder()
        lua.execute("""
            game.tick = 100
            npc.unbuildable = {{x0=4, y0=0, x1=5, y1=1, kind='refused'},
                               {x0=10, y0=10, x1=12, y1=12, kind='unreachable', until_tick=200},
                               {x0=20, y0=20, x1=21, y1=21, kind='container'}}
            surface.count_entities_filtered = function() return 0 end   -- (the container has gone)
            a = Ob.avoid(npc, {x=2, y=0})""")
        self.assertEqual(lua.eval("#a"), 2)
        self.assertEqual(lua.eval("a[1].x0"), 2)            # local tiles round the origin
        self.assertEqual(lua.eval("a[2].kind"), "unreachable")
        lua.execute("a = Ob.avoid(npc, {x=0, y=0}, 300)")   # the spot it could not get to is free again
        self.assertEqual(lua.eval("#a"), 1)

    def test_water_handed_back_is_free_again_once_filled(self):
        lua = ladder()
        lua.execute("""
            npc.unbuildable = {{x0=4, y0=0, x1=5, y1=1, kind='water'}}
            wet = 1
            surface.count_tiles_filtered = function() return wet end
            a = Ob.avoid(npc, {x=0, y=0})""")
        self.assertEqual(lua.eval("#a"), 1)
        lua.execute("wet = 0; a = Ob.avoid(npc, {x=0, y=0})")
        self.assertIsNone(lua.eval("a"))

    def test_every_planner_reads_the_same_boxes(self):
        from companion import avoid
        site = {"avoid": [{"x0": -2, "y0": 3, "x1": -1, "y1": 3, "kind": "tile"}, {"x0": "bad"}, "junk"]}
        self.assertEqual(avoid.rects(site), [(-2, 3, 0, 4)])
        self.assertEqual(avoid.tiles(site), {(-2, 3), (-1, 3)})
        self.assertEqual(avoid.rects({}), [])

    def test_the_starter_layouts_keep_off_ground_handed_back(self):
        from companion import layout_planner
        from test_blueprints import site
        first = layout_planner.plan(site())
        spot = next(e for e in first["entities"] if e["prototype"] == "burner-mining-drill")["position"]
        x, y = int(spot["x"]), int(spot["y"])
        again = layout_planner.plan({**site(), "avoid": [{"x0": x - 1, "y0": y - 1, "x1": x, "y1": y, "kind": "refused"}]})
        moved = [e["position"] for e in again["entities"] if e["prototype"] == "burner-mining-drill"]
        self.assertNotIn(spot, moved)

    def test_a_saved_layout_does_not_fit_on_ground_handed_back(self):
        from companion import blueprints
        site = {"avoid": [{"x0": 4, "y0": 0, "x1": 5, "y1": 1, "kind": "refused"}, {"bad": True}]}
        _, blocked, _ = blueprints.site_tiles(site)
        self.assertEqual({(4, 0), (5, 0), (4, 1), (5, 1)} & blocked, {(4, 0), (5, 0), (4, 1), (5, 1)})
        self.assertNotIn((6, 0), blocked)


if __name__ == "__main__":
    unittest.main()
