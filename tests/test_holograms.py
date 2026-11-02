"""Holograms of a plan's layout while it is built (mod/scripts/holograms.lua, F-16b): for plans from the user's packs by
default (all, or none, by the mod setting); entity ghosts of the engineer's force, drawn for a player's force that is
not its ally; a placed building takes its ghost's place; what is left goes with the plan."""
from __future__ import annotations

import unittest
from pathlib import Path

from lupa.lua52 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]


def runtime(mode="imported", friends=True):
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().source = (ROOT / "mod" / "scripts" / "holograms.lua").read_text(encoding="utf-8")
    lua.globals().ghosts_source = (ROOT / "mod" / "scripts" / "ghosts.lua").read_text(encoding="utf-8")
    lua.execute("package.preload['scripts.ghosts'] = function() return assert(load(ghosts_source))() end")
    lua.execute(f"""
        settings = {{global={{["npc-factions-holograms"]={{value="{mode}"}}}}}}
        made, drawn = {{}}, {{}}
        npc_force = {{valid=true, name="npc-faction-1"}}
        player_force = {{valid=true, name="player"}}
        function npc_force.get_friend(f) return {str(friends).lower()} end
        function player_force.get_friend(f) return {str(friends).lower()} end
        game = {{forces={{player=player_force, ["npc-faction-1"]=npc_force, enemy={{valid=true, name="enemy"}}}}}}
        surface = {{}}
        function surface.create_entity(t)
            local g = {{valid=true, spec=t}}
            function g.destroy() g.valid = false end
            function g.set_recipe(r) g.recipe = r end
            made[#made+1] = g
            return g
        end
        rendering = {{}}
        function rendering.draw_sprite(t)
            local r = {{spec=t, gone=false}}
            function r.destroy() r.gone = true end
            drawn[#drawn+1] = r
            return r
        end
        npc = {{force=npc_force, character={{valid=true, surface=surface}}}}
        plan = {{source="catalog:import:mega/Science/Logistic Science AM1", order={{"a", "b"}}, entities={{
            a={{id="a", prototype="assembling-machine-1", world={{x=1.5, y=1.5}}, direction=0, recipe="iron-gear-wheel"}},
            b={{id="b", prototype="inserter", world={{x=3.5, y=1.5}}, direction=4}}}}}}
        H = assert(load(source, "@holograms"))()
    """)
    return lua


class HologramTests(unittest.TestCase):
    def test_a_plan_from_the_packs_stands_as_ghosts_each_replaced_when_placed(self):
        lua = runtime()
        self.assertEqual(lua.eval("H.place(npc, plan)"), 2)
        self.assertEqual(lua.eval("made[1].spec.name"), "entity-ghost")
        self.assertEqual(lua.eval("made[1].spec.inner_name"), "assembling-machine-1")
        self.assertEqual(lua.eval("made[1].recipe"), "iron-gear-wheel")
        self.assertEqual(lua.eval("#drawn"), 0)                     # (an ally sees the ghosts themselves)
        lua.execute("H.settle(plan.entities.a)")
        self.assertFalse(lua.eval("made[1].valid"))
        lua.execute("H.clear(plan)")
        self.assertFalse(lua.eval("made[2].valid"))

    def test_a_piece_its_spot_refused_gets_its_ghost_once_the_spot_is_clear(self):
        # (W-35: a ghost goes down only where the game lets one; a tree on the spot at the start is no lost hologram)
        lua = runtime(friends=False)
        lua.execute("""blocked = true; defines = {build_check_type={manual_ghost=3}}
                       function surface.can_place_entity(t) return not (blocked and t.name == 'assembling-machine-1') end
                       first = H.place(npc, plan)
                       blocked = false
                       again = H.retry(npc, plan)""")
        self.assertEqual((lua.eval("first"), lua.eval("again")), (1, 1))
        self.assertEqual(lua.eval("#drawn"), 2)                      # (its drawing made once, not again)
        self.assertEqual(lua.eval("H.retry(npc, plan)"), 0)           # (each once)

    def test_drawn_for_a_player_s_force_that_is_not_its_ally(self):
        lua = runtime(friends=False)
        lua.execute("H.place(npc, plan)")
        self.assertEqual(lua.eval("#drawn"), 2)
        self.assertEqual(lua.eval("drawn[1].spec.forces[1].name"), "player")
        lua.execute("H.clear(plan)")
        self.assertTrue(lua.eval("drawn[1].gone and drawn[2].gone"))

    def test_a_moved_piece_takes_its_hologram_along_and_one_with_none_gets_none(self):
        lua = runtime()
        lua.execute("H.place(npc, plan)")
        lua.execute("plan.entities.b.world = {x=9.5, y=1.5}; H.moved(npc, plan.entities.b)")
        self.assertFalse(lua.eval("made[2].valid"))                          # (the old ghost gone)
        self.assertEqual(lua.eval("plan.entities.b.ghost.spec.position.x"), 9.5)
        lua2 = runtime("off")
        lua2.execute("H.place(npc, plan); H.moved(npc, plan.entities.b)")
        self.assertEqual(lua2.eval("#made"), 0)                              # (holograms off: none made)

    def test_the_setting_decides_which_plans(self):
        self.assertEqual(runtime("off").eval("H.place(npc, plan)"), 0)
        lua = runtime("imported")
        lua.execute("plan.source = 'catalog:designer-llm'")
        self.assertEqual(lua.eval("H.place(npc, plan)"), 0)
        lua = runtime("all")
        lua.execute("plan.source = 'catalog:designer-llm'")
        self.assertEqual(lua.eval("H.place(npc, plan)"), 2)


class GhostsNeverInTheWayTests(unittest.TestCase):
    """The smoke run (tools/proving.py imported-block): the engineer took its hologram's ghosts for what stood in the
    way and tried to mine them (unsupported_mining_target); nothing was built in 8 minutes. Every in-the-way scan
    passes ghosts over."""

    def test_the_scans_pass_ghosts_over(self):
        ob = (ROOT / "mod" / "scripts" / "obstacles.lua").read_text(encoding="utf-8")
        ds = (ROOT / "mod" / "scripts" / "designed_start.lua").read_text(encoding="utf-8")
        breakable = ob[ob.index("function Obstacles.breakable"):][:900]
        survey = ob[ob.index("function Obstacles.survey"):][:1800]
        footprint = ds[ds.index("-- (one the planned one replaces as a player does"):][:500]
        self.assertIn("not Obstacles.GHOSTS[e.type]", breakable)
        self.assertIn("not Obstacles.GHOSTS[e.type]", survey)
        self.assertIn('e.type~="entity-ghost"', footprint)


if __name__ == "__main__":
    unittest.main()
