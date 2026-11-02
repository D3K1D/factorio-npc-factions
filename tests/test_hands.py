"""What the engineer's hands can do, asked before a job is given (scripts/hands.lua, in Factorio's Lua 5.2; W-25).
death-world-waves-power: a hand furnace was asked for at -381.5,-384.5 (find_non_colliding_position kept the
half-tile offset), the game refused it each time with nothing there, and the same try came twenty times; normal-7b,
NPC 6: a defense row's magazines on its belt were refused as "not plain" again and again."""
import unittest

import test_character_layer

SETUP = """
    Hands = require('scripts.hands')
    defines = defines or {}
    defines.build_check_type = {manual=0}
    prototypes.entity['stone-furnace'] = {tile_width=2, tile_height=2}
    prototypes.entity['wooden-chest'] = {tile_width=1, tile_height=1}
    prototypes.item['firearm-magazine'] = {type='ammo'}
    prototypes.item['automation-science-pack'] = {type='tool'}
    prototypes.item['blueprint'] = {type='blueprint'}
    prototypes.item['heavy-armor'] = {type='armor'}
    asked = {}
    -- (the game's own verdict: refused at -381,-384 and anywhere off its grid; fine elsewhere)
    s = {can_place_entity=function(spec)
        local q = spec.position
        asked[#asked + 1] = q
        local w = prototypes.entity[spec.name].tile_width
        local on_grid = (w % 2 == 0) and (q.x % 1 == 0 and q.y % 1 == 0) or (q.x % 1 == 0.5 and q.y % 1 == 0.5)
        return on_grid and not (q.x == -381 and q.y == -384)
    end, find_non_colliding_position=function() error('not used with prototype facts') end}
    npc = {id=1}
"""


def runtime():
    lua = test_character_layer.runtime()
    lua.execute(SETUP)
    return lua


class FreeSpotTests(unittest.TestCase):
    def test_on_its_grid_where_the_game_would_build_it(self):
        lua = runtime()
        lua.execute("q = Hands.free_spot(npc, s, 'stone-furnace', {x=-380.5, y=-383.5}, 12, nil, 100)")
        x, y = lua.eval("q.x"), lua.eval("q.y")
        self.assertEqual((x % 1, y % 1), (0, 0))                              # (a 2x2 on whole tiles)
        self.assertNotEqual((x, y), (-381, -384))                            # (never the spot the game refuses)
        self.assertLessEqual(max(abs(x + 380.5), abs(y + 383.5)), 2)          # (the nearest ring first)
        lua.execute("c = Hands.free_spot(npc, s, 'wooden-chest', {x=3, y=4}, 4, nil, 100)")
        self.assertEqual((lua.eval("c.x"), lua.eval("c.y")), (3.5, 4.5))        # (a 1x1 on a tile centre)

    def test_a_spot_the_game_refused_lately_is_not_offered_again(self):
        lua = runtime()
        lua.execute("""
            first = Hands.free_spot(npc, s, 'stone-furnace', {x=-382, y=-385}, 12, nil, 100)
            Hands.refused(npc, 'stone-furnace', first, 100)
            again = Hands.free_spot(npc, s, 'stone-furnace', {x=-382, y=-385}, 12, nil, 200)
            later = Hands.free_spot(npc, s, 'stone-furnace', {x=-382, y=-385}, 12, nil, 100 + Hands.REFUSED_FOR)""")
        self.assertEqual((lua.eval("first.x"), lua.eval("first.y")), (-382, -385))
        self.assertNotEqual((lua.eval("again.x"), lua.eval("again.y")), (-382, -385))
        self.assertEqual((lua.eval("later.x"), lua.eval("later.y")), (-382, -385))   # (in a while, tried again)
        lua.execute("for k = 1, 40 do Hands.refused(npc, 'x', {x=k, y=0}, 1) end")
        self.assertEqual(lua.eval("#npc.refused_spots"), lua.eval("Hands.REFUSED_MAX"))

    def test_none_when_nothing_near_will_do(self):
        lua = runtime()
        lua.execute("s.can_place_entity = function() return false end")
        self.assertIsNone(lua.eval("Hands.free_spot(npc, s, 'stone-furnace', {x=0, y=0}, 3, nil, 1)"))


class BeltTests(unittest.TestCase):
    def test_plain_stackable_kinds_are_taken_off_a_belt_and_items_with_data_are_not(self):
        lua = runtime()
        for item in ("iron-plate", "firearm-magazine", "automation-science-pack"):
            self.assertTrue(lua.eval(f"Hands.belt_pickable('{item}')"), item)
        for item in ("blueprint", "heavy-armor", "no-such-item"):
            self.assertFalse(lua.eval(f"Hands.belt_pickable('{item}')"), item)

    def test_taken_off_stack_by_stack_with_each_one_s_own_state(self):
        # (Codex, W-25: a fresh stack put a part-used magazine back full, a damaged turret whole, spoiling over)
        lua = runtime()
        lua.execute("""
            function stack(name, count, extra)
                local st = {valid_for_read=true, name=name, count=count, quality={name='normal'}}
                for k, v in pairs(extra or {}) do st[k] = v end
                st.clear = function() st.valid_for_read = false; st.count = 0 end
                return st
            end
            used, full, turret = stack('firearm-magazine', 1, {ammo=4}), stack('firearm-magazine', 3, {ammo=10}),
                stack('gun-turret', 1, {health=0.4})
            rare = stack('firearm-magazine', 2, {ammo=10}); rare.quality = {name='rare'}
            got = {}
            room = 99
            pockets = {insert=function(def) local n = math.min(def.count, room); room = room - n
                got[#got + 1] = def; return n end}
            moved = Hands.take_from_lines({{used, rare}, {full}}, 'firearm-magazine', 3, pockets)""")
        self.assertEqual(lua.eval("moved"), 3)
        self.assertEqual(lua.eval("got[1].ammo"), 4)                           # (the part-used one as it was)
        self.assertEqual(lua.eval("got[2].ammo"), 10)
        self.assertEqual(lua.eval("got[2].count"), 2)                          # (only what was asked)
        self.assertFalse(lua.eval("used.valid_for_read"))
        self.assertEqual(lua.eval("full.count"), 1)                            # (the rest stays on the belt)
        self.assertEqual(lua.eval("rare.count"), 2)                            # (normal quality only)
        lua.execute("t = Hands.take_from_lines({{turret}}, 'gun-turret', 1, pockets)")
        self.assertEqual(lua.eval("got[3].health"), 0.4)                       # (damaged, still damaged)

    def test_only_what_went_into_its_pockets_leaves_the_belt(self):
        lua = runtime()
        lua.execute("""
            st = {valid_for_read=true, name='iron-plate', count=4, quality={name='normal'}, clear=function() end}
            pockets = {insert=function(def) return 1 end}
            moved = Hands.take_from_lines({{st}}, 'iron-plate', 4, pockets)""")
        self.assertEqual(lua.eval("moved"), 1)
        self.assertEqual(lua.eval("st.count"), 3)
        # (one that will not go in, a damaged one say, and the next one does: Codex, W-25)
        lua.execute("""
            bad = {valid_for_read=true, name='iron-plate', count=1, quality={name='normal'}, health=0.2,
                   clear=function() end}
            good = {valid_for_read=true, name='iron-plate', count=2, quality={name='normal'}}
            function good.clear() good.valid_for_read = false end
            pockets = {insert=function(def) if def.health then return 0 end return def.count end}
            moved = Hands.take_from_lines({{good, bad}}, 'iron-plate', 2, pockets)""")
        self.assertEqual(lua.eval("moved"), 2)
        self.assertFalse(lua.eval("good.valid_for_read"))

    def test_the_worker_and_its_callers_ask_the_same(self):
        root = test_character_layer.ROOT / "mod" / "scripts"
        worker = (root / "worker.lua").read_text(encoding="utf-8")
        self.assertIn("BELT_PICKUP_ITEMS[job.item] or (proto~=nil and Hands.belt_pickable(job.item))", worker)
        self.assertIn("if #blockers==0 then Hands.refused(npc,job.entity_name,job.position,tick) end", worker)
        self.assertIn("DesignedStart.Hands.belt_pickable(it.name)",
                      (root / "designed_start.lua").read_text(encoding="utf-8"))
        self.assertIn("Hands.belt_pickable(it.name)", (root / "corrector.lua").read_text(encoding="utf-8"))
        self.assertIn("local inserted=Hands.take_from_lines(lines,job.item,job.count,destination)", worker)
        corrector = (root / "corrector.lua").read_text(encoding="utf-8")
        self.assertIn('it.quality == "normal" and Hands.belt_pickable(it.name)', corrector)   # (the sweep: normal only)
        self.assertIn('if task.stage=="build" then node.position_checked=nil end',
                      (root / "cooperation.lua").read_text(encoding="utf-8"))           # (a refused chest: looked again)
        for name in ("designed_start.lua", "agent.lua", "combat_turrets.lua", "cooperation.lua"):   # (every spot it picks)
            self.assertIn("Hands.free_spot(", (root / name).read_text(encoding="utf-8"), name)


if __name__ == "__main__":
    unittest.main()
