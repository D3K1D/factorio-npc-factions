"""The engineers' reach as the players' (scripts/reach.lua, designed_start's DesignedStart.far), in Factorio's Lua 5.2:
with far-reach on, its bonuses go on every engineer's body, a new body's too, and the plans count what it reaches as
near. The user, 2026-10-28: "give the NPC far reach like how I had it with the far reach mod"."""
from pathlib import Path
import unittest

import test_retire

ROOT = Path(__file__).resolve().parents[1]


def reach_runtime():
    from lupa import LuaRuntime
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().source = (ROOT / "mod" / "scripts" / "reach.lua").read_text(encoding="utf-8")
    lua.execute("""
        script = {active_mods={['far-reach']='2.1.0'}}
        settings = {global={['far-reach-build-distance-bonus']={value=150}, ['far-reach-reach-distance-bonus']={value=150},
            ['far-reach-resource-reach-distance-bonus']={value=150}, ['far-reach-item-drop-distance-bonus']={value=150},
            ['far-reach-item-pickup-distance-bonus']={value=0}}}
        storage = {npcs={}}
        Reach = assert(load(source, '@reach'))()
        function body(n) return {valid=true, unit_number=n, character_build_distance_bonus=0,
            character_reach_distance_bonus=0, character_resource_reach_distance_bonus=0,
            character_item_drop_distance_bonus=0, character_item_pickup_distance_bonus=0} end
        npc = {id=1, character=body(1)}
        storage.npcs[1] = npc
    """)
    return lua


class ReachTests(unittest.TestCase):
    def test_its_body_gets_the_bonuses_far_reach_gives_players(self):
        lua = reach_runtime()
        lua.execute("Reach.keep(npc, 1)")
        self.assertEqual(lua.eval("npc.character.character_build_distance_bonus"), 150)
        self.assertEqual(lua.eval("npc.character.character_resource_reach_distance_bonus"), 150)
        self.assertEqual(lua.eval("npc.character.character_item_pickup_distance_bonus"), 0)

    def test_a_new_body_gets_them_at_once_and_a_changed_setting_on_the_next_tick(self):
        lua = reach_runtime()
        lua.execute("Reach.keep(npc, 1); npc.character = body(2); Reach.keep(npc, 2)")      # (a respawn, a swap)
        self.assertEqual(lua.eval("npc.character.character_reach_distance_bonus"), 150)
        lua.execute("""settings.global['far-reach-reach-distance-bonus'].value = 40
                       Reach.keep(npc, 3)""")
        self.assertEqual(lua.eval("npc.character.character_reach_distance_bonus"), 150)    # (not looked at yet)
        lua.execute("Reach.changed(); Reach.keep(npc, 4)")
        self.assertEqual(lua.eval("npc.character.character_reach_distance_bonus"), 40)

    def test_taken_off_what_it_put_on_goes_and_another_mods_bonus_stays(self):
        lua = reach_runtime()
        lua.execute("""Reach.keep(npc, 1)
                       npc.character.character_build_distance_bonus = 7      -- (another mod's since)
                       script.active_mods = {}; Reach.changed(); Reach.keep(npc, 2)""")
        self.assertEqual(lua.eval("npc.character.character_reach_distance_bonus"), 0)
        self.assertEqual(lua.eval("npc.character.character_build_distance_bonus"), 7)

    def test_without_the_mod_it_keeps_vanilla_reach(self):
        lua = reach_runtime()
        lua.execute("script.active_mods = {}; Reach.keep(npc, 1)")
        self.assertEqual(lua.eval("npc.character.character_build_distance_bonus"), 0)


class FarTests(unittest.TestCase):
    def test_far_is_what_it_cannot_reach(self):
        lua = test_retire.runtime()
        lua.execute("npc.character = {valid=true, reach_distance=10}")
        self.assertEqual(lua.eval("DS.far(npc)"), lua.eval("DS.AWAY"))                      # vanilla: AWAY
        lua.execute("npc.character.reach_distance = 160")
        self.assertEqual(lua.eval("DS.far(npc)"), 160)                                       # far reach


if __name__ == "__main__":
    unittest.main()
