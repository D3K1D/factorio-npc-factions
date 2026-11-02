"""The engineer's own defense kit where biters are about (defense_kit.lua), and the turrets it puts down at its feet
against a group of biters (Combat.drops_turrets), in Factorio's Lua 5.2 with the game stubbed. The user, 2026-10-26:
caught by biters, it should put turrets down at its feet and load them, pick them up after; or back off shooting its
gun, toward turret cover."""
from pathlib import Path
import unittest

from lupa.lua52 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]


def runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().source = (ROOT / "mod" / "scripts" / "defense_kit.lua").read_text(encoding="utf-8")
    lua.globals().hands_source = (ROOT / "mod" / "scripts" / "hands.lua").read_text(encoding="utf-8")
    lua.execute("package.preload['scripts.hands'] = function() return assert(load(hands_source))() end")
    lua.execute("""
        defines = {inventory={character_main=1, character_guns=2, character_ammo=3, character_armor=4}}
        unlocked = {['gun-turret']=true, ['firearm-magazine']=true, ['light-armor']=true, ['submachine-gun']=true}
        team = true
        equipped = 0
        package.preload['scripts.recipes'] = function() return {ready=function(_, name) return unlocked[name] == true end} end
        package.preload['scripts.team_world'] = function() return {active=function() return team end} end
        package.preload['scripts.combat'] = function() return {equip_now=function() equipped = equipped + 1 end} end
        package.preload['scripts.agent'] = function() Agent = Agent or {}; return Agent end
        board = {attacks={}}   -- (the waves its plot has seen, newest first: team_war's threat board)
        storage = {npcs={}}
        package.preload['scripts.team_war'] = function() return {threat_board=function() return board end} end
        Kit = assert(load(source))()
        held = {pistol=1, ['firearm-magazine']=10}
        local function inventory(kinds)
            return {get_item_count=function(what) return kinds and held[what.name] or 0 end}
        end
        foes = {}
        force = {get_friend=function() return false end, get_cease_fire=function() return false end}
        npc = {force=force, character={valid=true, force=force, position={x=0, y=0},
            get_inventory=function(i) return inventory(i == defines.inventory.character_main) end,
            surface={find_entities_filtered=function(f)
                local out = {}
                for _, e in ipairs(foes) do
                    if (e.position.x - f.position.x)^2 + (e.position.y - f.position.y)^2 <= f.radius^2 then out[#out + 1] = e end
                end
                return out
            end}}}
        storage.npcs[1] = npc
        asked = {}
        function acquire(_, item, count) asked[#asked + 1] = item .. ':' .. count
            return {kind='craft', recipe=item, count=count, background=true} end
        function wants() local out = {} for _, w in ipairs(Kit.wants(npc)) do out[#out + 1] = w[1] .. ':' .. w[2] end return out end
    """)
    return lua


class KitTests(unittest.TestCase):
    def test_it_wants_the_best_gun_and_armor_it_can_make_magazines_and_turrets(self):
        lua = runtime()
        self.assertEqual(list(lua.eval("wants()").values()),
                         ["submachine-gun:1", "light-armor:1", "firearm-magazine:30", "gun-turret:2"])
        lua.execute("held['submachine-gun'], held['light-armor'], held['gun-turret'] = 1, 1, 2; "
                    "held['piercing-rounds-magazine'] = 25")
        self.assertEqual(list(lua.eval("wants()").values()), [])                 # (piercing rounds count too)
        lua.execute("held['piercing-rounds-magazine'] = 5; held['gun-turret'] = 0")
        self.assertEqual(list(lua.eval("wants()").values()), ["firearm-magazine:25", "gun-turret:2"])
        lua.execute("unlocked['submachine-gun'] = nil; held['submachine-gun'] = nil")
        self.assertNotIn("submachine-gun:1", list(lua.eval("wants()").values()))   # the pistol it has, then

    def test_kept_where_biters_are_about_once_it_has_turned_to_defense(self):
        lua = runtime()
        job, meta = lua.eval("Kit.next(npc, 100, acquire)")
        self.assertEqual((job.recipe, meta.upkeep), ("submachine-gun", "kit"))
        lua.execute("unlocked['gun-turret'] = nil")
        self.assertIsNone(lua.eval("Kit.next(npc, 100, acquire)"))              # the opening's iron first
        lua.execute("unlocked['gun-turret'] = true; team = false")
        self.assertIsNone(lua.eval("Kit.next(npc, 100, acquire)"))              # no biters about yet
        lua.execute("npc.combat = {entries=1}")
        self.assertIsNotNone(lua.eval("Kit.next(npc, 100, acquire)"))           # it has had to fight
        # what it cannot get now: a while before it looks again; what it has, worn and in hand
        lua.execute("function acquire() return nil, 'waiting_for:iron-plate' end")
        self.assertIsNone(lua.eval("Kit.next(npc, 200, acquire)"))
        self.assertEqual(lua.eval("npc.kit_next"), 200 + lua.eval("Kit.CHECK_EVERY"))
        self.assertEqual(lua.eval("equipped"), 1)
        self.assertIsNone(lua.eval("Kit.next(npc, 300, acquire)"))
        self.assertEqual(lua.eval("equipped"), 1)                                # (not looked again yet)

    def test_turrets_put_down_in_a_fight_are_picked_up_once_it_is_over_and_clear(self):
        lua = runtime()
        lua.execute("""
            held['submachine-gun'], held['light-armor'], held['gun-turret'], held['firearm-magazine'] = 1, 1, 2, 30
            turret = {valid=true, minable=true, unit_number=7, position={x=3, y=0}}
            npc.combat_turrets = {dropped={turret}}
            npc.combat = {active=true, entries=1}""")
        self.assertIsNone(lua.eval("Kit.next(npc, 1000, acquire)"))             # still fighting
        lua.execute("npc.combat = {active=false, entries=1, finished_tick=900}")
        self.assertIsNone(lua.eval("Kit.next(npc, 1000, acquire, true)"))       # not long enough after
        lua.execute("foes = {{force={}, valid=true, position={x=10, y=0}}}")
        self.assertIsNone(lua.eval("Kit.next(npc, 2000, acquire, true)"))       # biters by it still
        lua.execute("foes = {}")
        job, meta = lua.eval("Kit.next(npc, 2000, acquire, true)")
        self.assertEqual((job.kind, job.mode, meta.upkeep), ("mine", "deconstruct", "kit_pickup"))
        lua.execute("turret.valid = false")                                       # picked up
        self.assertIsNone(lua.eval("Kit.next(npc, 2100, acquire, true)"))
        self.assertEqual(len(lua.eval("npc.combat_turrets.dropped")), 0)
        # one it cannot get to is left where it stands after a few tries
        lua.execute("t2 = {valid=true, minable=true, unit_number=8, position={x=3, y=0}}; npc.combat_turrets.dropped = {t2}")
        for _ in range(lua.eval("Kit.PACK_TRIES")):
            self.assertIsNotNone(lua.eval("Kit.next(npc, 2200, acquire, true)"))
        self.assertIsNone(lua.eval("Kit.next(npc, 2200, acquire, true)"))

    def test_it_says_when_its_kit_is_still_being_made_and_waits_after_a_failed_fetch(self):
        # (the review, 2026-10-26: "nothing to do" and "on its way" were one answer, and it walked off unarmed)
        lua = runtime()
        lua.execute("function acquire(_, item) return nil, 'crafting:' .. item end")
        self.assertEqual(lua.eval("select(2, Kit.next(npc, 100, acquire, true))"), "crafting")
        lua.execute("function acquire() return {kind='transfer'} end; npc.kit_failed_until = 700")
        self.assertIsNone(lua.eval("Kit.next(npc, 300, acquire, true)"))       # a fetch just failed: not now
        self.assertIsNotNone(lua.eval("Kit.next(npc, 800, acquire, true)"))

    def test_a_turret_a_plan_took_over_where_it_stood_is_left_to_the_plan(self):
        lua = runtime()
        lua.execute("""
            held['submachine-gun'], held['light-armor'], held['gun-turret'], held['firearm-magazine'] = 1, 1, 2, 30
            turret = {valid=true, minable=true, unit_number=7, position={x=3, y=0}}
            npc.combat_turrets = {dropped={turret}}
            npc.combat = {active=false, entries=1, finished_tick=0}
            Agent.designed = {used_by_a_plan=function(_, e) return e == turret end}""")
        self.assertIsNone(lua.eval("Kit.next(npc, 2000, acquire, true)"))
        self.assertEqual(len(lua.eval("npc.combat_turrets.dropped")), 0)
        self.assertTrue(lua.eval("turret.valid"))

    def test_out_at_far_work_what_would_take_it_off_waits_unless_it_heads_back_to_an_attack(self):
        # (the user's live game, replayed 2026-10-26: 79 iron plates fetched from home as a far build started, 90 s)
        lua = runtime()
        lua.execute("""
            Agent.designed = {away=function() return far end}
            far = true
            home_chest = {valid=true, position={x=-300, y=0}}
            function acquire(_, item) return {kind='transfer', target=home_chest, item='iron-plate', count=79} end""")
        self.assertIsNone(lua.eval("Kit.next(npc, 100, acquire)"))                   # (it waits to be home)
        self.assertIsNotNone(lua.eval("Kit.next(npc, 100, acquire, true)"))          # heading back to an attack
        lua.execute("home_chest.position = {x=20, y=0}; npc.kit_next = nil")
        self.assertIsNotNone(lua.eval("Kit.next(npc, 100, acquire)"))                # a store right by it
        lua.execute("function acquire(_, item) return {kind='craft', recipe=item, count=1} end; npc.kit_next = nil")
        self.assertIsNotNone(lua.eval("Kit.next(npc, 100, acquire)"))                # made from what it carries
        lua.execute("""
            ore = 0
            npc.character.surface.count_entities_filtered = function(f) return f.name == 'iron-ore' and ore or 0 end
            function acquire(_, item) return {kind='mine', item='iron-ore', count=20} end
            npc.kit_next = nil""")
        self.assertIsNone(lua.eval("Kit.next(npc, 100, acquire)"))                   # no ore by it: it waits
        lua.execute("ore = 1; npc.kit_next = nil")
        self.assertIsNotNone(lua.eval("Kit.next(npc, 100, acquire)"))                # ore right by it
        lua.execute("far = false; home_chest.position = {x=-300, y=0}; npc.kit_next = nil "
                    "function acquire(_, item) return {kind='transfer', target=home_chest, item='iron-plate'} end")
        self.assertIsNotNone(lua.eval("Kit.next(npc, 100, acquire)"))                # home: as before
        # a turret it put down far off stays there, no try counted, until it is home
        lua.execute("""
            held['submachine-gun'], held['light-armor'], held['gun-turret'], held['firearm-magazine'] = 1, 1, 2, 30
            far = true
            left = {valid=true, minable=true, unit_number=9, position={x=-300, y=0}}
            npc.combat_turrets = {dropped={left}}
            npc.combat = {active=false, entries=1, finished_tick=0}""")
        self.assertIsNone(lua.eval("Kit.next(npc, 5000, acquire)"))
        self.assertIsNone(lua.eval("npc.combat_turrets.pack_tries[9]"))
        lua.execute("far = false")
        self.assertEqual(lua.eval("Kit.next(npc, 5000, acquire)")[0].kind, "mine")


    def test_a_plan_of_its_own_that_still_needs_turrets_comes_before_the_kit(self):
        # (D-11: its kit's pair of turrets and 30 magazines, refilled as its plan used them, kept a two-turret plan
        # building for 35 minutes in a death world)
        lua = runtime()
        lua.execute("""
            held['firearm-magazine'] = 0
            npc.design = {status='building', plan={order={'t1', 't2'},
                entities={t1={item='gun-turret', entity={valid=true}}, t2={item='gun-turret'}},
                stages={{manual_supplies={{entity_id='t2', item='firearm-magazine', slot='ammo'}}}}}}""")
        wants = list(lua.eval("wants()").values())
        self.assertNotIn("gun-turret:2", wants)
        self.assertIn("firearm-magazine:" + str(lua.eval("Kit.AMMO")), wants)   # (its magazines it keeps: reloads)
        lua.execute("npc.design.status = 'complete'")
        self.assertIn("gun-turret:2", list(lua.eval("wants()").values()))


    def test_the_kit_is_sized_to_the_waves_its_plot_has_seen(self):
        # (D-14: the user put six turrets down at their first fight; a fixed pair holds ten small biters)
        lua = runtime()
        self.assertEqual(list(lua.eval("{Kit.size(npc)}").values()), [2, 30])     # nothing seen: the least kit
        lua.execute("board = {attacks={{units={['small-biter']=9}}, {units={['small-biter']=6}}}}")
        self.assertEqual(list(lua.eval("{Kit.size(npc)}").values()), [3, 40])     # 9, growing from 6: 13.5 -> 3
        lua.execute("board = {attacks={{units={['big-biter']=10}}}}")              # 37.5 small biters
        self.assertEqual(list(lua.eval("{Kit.size(npc)}").values()), [6, 70])     # (at most six)
        self.assertIn("gun-turret:6", list(lua.eval("wants()").values()))
        # a plan of its own wanting ammunition: the kit keeps its least magazines meanwhile (Codex, D-14-kit-size)
        lua.execute("""held['firearm-magazine'] = 0
            npc.design = {status='building', plan={order={'t'}, entities={t={item='stone-wall'}},
                stages={{supplies={{entity_id='t', item='firearm-magazine'}}}}}}""")
        self.assertIn("firearm-magazine:30", list(lua.eval("wants()").values()))
        # a teammate's wave counts: the first big one on its own plot finds it ready
        lua = runtime()
        lua.execute("""
            local mate = {character={valid=true}}
            storage.npcs[2] = mate
            local own = board
            package.loaded['scripts.team_war'].threat_board = function(n)
                if n == mate then return {attacks={{units={['small-biter']=22}}}} end
                return own end""")
        self.assertEqual(list(lua.eval("{Kit.size(npc)}").values()), [5, 60])


class GunTests(unittest.TestCase):
    def test_a_better_gun_with_no_magazines_of_its_own_takes_the_worse_ones_place(self):
        # (the review, 2026-10-26: the new submachine gun went in an empty slot with no magazines; the pistol kept firing)
        import test_combat_routes
        lua = test_combat_routes.combat()
        lua.execute("""
            defines = {inventory={character_main=1, character_guns=2, character_ammo=3, character_armor=4}}
            game = {tick=100}
            local function gun(name, cooldown, range)
                return {valid_for_read=true, name=name, count=1, prototype={type='gun',
                    attack_parameters={range=range, cooldown=cooldown, ammo_categories={'bullet'}}}}
            end
            local empty = function() return {valid_for_read=false} end
            local function slot(stack)
                local s = stack or empty()
                s.swap_stack = function(other)
                    local a, b = {}, {}
                    for k, v in pairs(s) do if type(v) ~= 'function' then a[k] = v end end
                    for k, v in pairs(other) do if type(v) ~= 'function' then b[k] = v end end
                    for k in pairs(a) do s[k] = nil end
                    for k in pairs(b) do other[k] = nil end
                    for k, v in pairs(b) do s[k] = v end
                    for k, v in pairs(a) do other[k] = v end
                    return true
                end
                s.transfer_stack = s.swap_stack
                return s
            end
            magazines = slot({valid_for_read=true, name='firearm-magazine', count=30, ammo=10,
                prototype={type='ammo', ammo_category='bullet', magazine_size=10}})
            guns = {slot(gun('pistol', 15, 15)), slot(), slot()}
            ammo = {magazines, slot(), slot()}
            main = {slot(gun('submachine-gun', 6, 18)), slot()}
            armor = {slot()}
            npc = {combat={}, character={valid=true,
                get_main_inventory=function() return main end,
                get_inventory=function(i) return i == 2 and guns or i == 3 and ammo or i == 4 and armor or main end}}
            C.equip_now(npc)""")
        self.assertEqual(lua.eval("guns[1].name"), "submachine-gun")                # with the magazines
        self.assertEqual(lua.eval("ammo[1].name"), "firearm-magazine")


class DropTests(unittest.TestCase):
    def test_a_group_of_biters_gets_its_turrets_at_its_feet_wherever_it_is(self):
        import test_combat_routes
        lua = test_combat_routes.combat()
        lua.execute("""
            package.loaded['scripts.combat_turrets'].wanted = function(t) return t.force.name ~= 'enemy' end
            function foe(kind, force) return {valid=true, type=kind, force={name=force}} end""")
        drops = lambda target, state: lua.eval(f"C.drops_turrets(npc, {target}, {state})")  # noqa: E731
        self.assertTrue(drops("foe('unit', 'enemy')", "{threat_count=3}"))     # a group: near home too
        self.assertFalse(drops("foe('unit', 'enemy')", "{threat_count=1}"))    # one biter: its gun does
        self.assertTrue(drops("foe('character', 'npc-faction-2')", "{threat_count=1}"))   # people: always
        self.assertFalse(drops("foe('unit', 'enemy')", "{threat_count=5, breaking={dropped=1}}"))   # one behind it


    def test_magazines_in_its_guns_slot_load_the_turrets_too(self):
        # (taking up its gun moves the whole stack into the gun's slot: none left loose in its pockets)
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().source = (ROOT / "mod" / "scripts" / "combat_turrets.lua").read_text(encoding="utf-8")
        lua.globals().hands_source = (ROOT / "mod" / "scripts" / "hands.lua").read_text(encoding="utf-8")
        lua.execute("package.preload['scripts.hands'] = function() return assert(load(hands_source))() end")
        lua.execute("""
            defines = {inventory={turret_ammo=1, character_ammo=3}, direction={north=0}}
            T = assert(load(source))()
            function stock(items)
                local s = {items=items}
                s.get_item_count = function(name) return s.items[name] or 0 end
                s.remove = function(what) local n = math.min(what.count, s.items[what.name] or 0)
                    s.items[what.name] = (s.items[what.name] or 0) - n; return n end
                return s
            end
            main, slot = stock({['gun-turret']=2}), stock({['firearm-magazine']=30})
            placed = {}
            c = {position={x=0, y=0}, build_distance=10, reach_distance=10,
                get_main_inventory=function() return main end,
                get_inventory=function() return slot end,
                surface={find_entities_filtered=function() return {} end,
                         find_non_colliding_position=function(_, p) return p end}}
            npc = {character=c}
            function place(spec)
                local inside = 0
                local t = {position=spec.position, insert=function(what) inside = inside + what.count; return what.count end}
                placed[#placed + 1] = t
                main.items['gun-turret'] = main.items['gun-turret'] - 1
                return t
            end
            T.tick(npc, {position={x=10, y=0}}, 100, place)""")
        self.assertEqual(len(lua.eval("placed")), 1)
        self.assertEqual(lua.eval("slot.items['firearm-magazine']"), 20)       # ten into it, ten kept for the gun

    def test_in_a_fight_as_many_go_down_as_what_comes_wants(self):
        # (D-14) a group of 30 small biters: six loaded round it, not a fixed three; people: three
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().source = (ROOT / "mod" / "scripts" / "combat_turrets.lua").read_text(encoding="utf-8")
        lua.globals().hands_source = (ROOT / "mod" / "scripts" / "hands.lua").read_text(encoding="utf-8")
        lua.execute("package.preload['scripts.hands'] = function() return assert(load(hands_source))() end")
        lua.execute("T = assert(load(source))(); biters = {valid=true, force={name='enemy'}}")
        self.assertEqual(lua.eval("T.enough(biters, 30)"), 6)
        self.assertEqual(lua.eval("T.enough(biters, 4)"), 3)
        self.assertEqual(lua.eval("T.enough(biters, 200)"), 8)                  # (at most eight)
        self.assertEqual(lua.eval("T.enough({valid=true, force={name='npc-faction-2'}}, 30)"), 3)


if __name__ == "__main__":
    unittest.main()
