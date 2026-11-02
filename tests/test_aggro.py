"""How much an engineer puts up with (aggro.lua) and the turrets it puts down in a fight
(combat_turrets.lua), in Factorio's Lua 5.2 without Factorio."""
from pathlib import Path
import unittest

from lupa.lua52 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]


def runtime(name):
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().source = (ROOT / "mod" / "scripts" / (name + ".lua")).read_text(encoding="utf-8")
    lua.globals().hands_source = (ROOT / "mod" / "scripts" / "hands.lua").read_text(encoding="utf-8")
    lua.execute("package.preload['scripts.hands'] = function() return assert(load(hands_source))() end")
    lua.execute("M = assert(load(source))()")
    return lua


class AggroTests(unittest.TestCase):
    def test_hits_add_up_to_war_sooner_for_aggressive_characters(self):
        lua = runtime("aggro")
        self.assertEqual(lua.eval("M.limit(1)"), 100)
        self.assertEqual(lua.eval("M.limit(0)"), 350)
        self.assertEqual(lua.eval("M.limit(0.5, true)"), 450)     # an ally gets twice the patience
        lua.execute("npc = {}")
        # a pistol at its buildings: 5 a shot; the aggressive one has had enough after 100
        fed_up = [lua.eval("M.hit(npc, 'player:1', 5, 0, {aggression=1})") for _ in range(20)]
        self.assertEqual(fed_up.index(True), 19)
        # on its own body it counts double
        lua.execute("calm = {}")
        fed_up = [lua.eval("M.hit(calm, 'player:1', 5, 0, {aggression=1, personal=true})") for _ in range(10)]
        self.assertEqual(fed_up.index(True), 9)

    def test_it_fades_when_left_alone_and_a_building_lost_is_enough_at_once(self):
        lua = runtime("aggro")
        lua.execute("npc = {}")
        self.assertFalse(lua.eval("M.hit(npc, 'npc:2', 90, 0, {aggression=1})"))
        self.assertFalse(lua.eval("M.hit(npc, 'npc:2', 20, 18000, {aggression=1})"))   # 90 halved, + 20
        self.assertAlmostEqual(lua.eval("npc.aggro['npc:2'].damage"), 65)
        self.assertTrue(lua.eval("M.hit(npc, 'npc:2', 0, 18001, {destroyed='burner-mining-drill'})"))
        # already at war with them: it only keeps count
        self.assertFalse(lua.eval("M.hit(npc, 'npc:2', 500, 18002, {at_war=true})"))
        lua.execute("M.forgive(npc, 'npc:2')")
        self.assertIsNone(lua.eval("npc.aggro['npc:2']"))


def turrets():
    lua = runtime("combat_turrets")
    lua.execute("""
        defines = {inventory={character_ammo=4, turret_ammo=1}, direction={north=0}}
        function inventory(items)
            local inv = {items=items}
            inv.get_item_count = function(name)
                if name then return inv.items[name] or 0 end
                local n = 0; for _, c in pairs(inv.items) do n = n + c end; return n
            end
            inv.remove = function(spec) inv.items[spec.name] = (inv.items[spec.name] or 0) - spec.count; return spec.count end
            return inv
        end
        placed = {}
        main = inventory({['gun-turret']=2, ['firearm-magazine']=30})
        slot = inventory({['firearm-magazine']=4})
        surface = {find_entities_filtered=function() return mine end,
                   find_non_colliding_position=function(_, p) return {x=math.floor(p.x + 0.5), y=math.floor(p.y + 0.5)} end}
        mine = {}
        character = {position={x=0, y=0}, surface=surface, force={name='npc-faction-1'}, reach_distance=10,
                     build_distance=10, get_main_inventory=function() return main end,
                     get_inventory=function(i) if i == defines.inventory.character_ammo then return slot end end}
        npc = {character=character}
        function turret_at(p)
            local inside = inventory({})
            local t = {position=p, get_inventory=function() return inside end, inside=inside}
            t.insert = function(spec) inside.items[spec.name] = (inside.items[spec.name] or 0) + spec.count; return spec.count end
            return t
        end
        function place(spec)
            local t = turret_at(spec.position)
            placed[#placed + 1] = t
            main.items['gun-turret'] = main.items['gun-turret'] - 1
            mine[#mine + 1] = t
            return t
        end
        enemy = {valid=true, force={name='player'}, position={x=20, y=0}}
    """)
    return lua


class TurretTests(unittest.TestCase):
    def test_only_against_people(self):
        lua = turrets()
        self.assertTrue(lua.eval("M.wanted(enemy)"))
        self.assertFalse(lua.eval("M.wanted({valid=true, force={name='enemy'}, position={x=1, y=1}})"))

    def test_turrets_it_carries_go_down_toward_them_loaded_one_at_a_time(self):
        lua = turrets()
        self.assertTrue(lua.eval("M.tick(npc, enemy, 100, place)"))
        self.assertEqual((lua.eval("placed[1].position.x"), lua.eval("placed[1].position.y")), (3, 0))   # toward them
        # 30 loose, 4 in the gun: it keeps 6 more back for the gun and loads 10
        self.assertEqual(lua.eval("placed[1].inside.items['firearm-magazine']"), 10)
        self.assertEqual(lua.eval("main.items['firearm-magazine']"), 20)
        self.assertFalse(lua.eval("M.tick(npc, enemy, 110, place)"))                   # half a second between
        self.assertTrue(lua.eval("M.tick(npc, enemy, 130, place)"))
        self.assertEqual(lua.eval("placed[2].position.y"), 3)                           # beside the first
        self.assertFalse(lua.eval("M.tick(npc, enemy, 200, place)"))                   # none left to carry
        # a turret of its own running low is topped up
        lua.execute("placed[1].inside.items['firearm-magazine'] = 2")
        self.assertTrue(lua.eval("M.tick(npc, enemy, 260, place)"))
        self.assertEqual(lua.eval("placed[1].inside.items['firearm-magazine']"), 6)     # only what it can spare
        self.assertEqual(lua.eval("main.items['firearm-magazine']"), 6)

    def test_without_magazines_to_spare_nothing_goes_down(self):
        lua = turrets()
        lua.execute("main.items['firearm-magazine'] = 6")
        self.assertFalse(lua.eval("M.tick(npc, enemy, 100, place)"))
        self.assertEqual(lua.eval("#placed"), 0)


if __name__ == "__main__":
    unittest.main()
