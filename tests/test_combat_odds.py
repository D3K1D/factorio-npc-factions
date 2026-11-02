"""The odds of a fight with biters (combat_odds.lua), and what the engineer does with them (combat.lua): biters outrun it
(0.24 against its 0.15 with the user's mods), so a fight it cannot win it keeps clear of before they are on it, and
one it is in it fights from behind its turrets (O-2: run 34's engineer walked into a wave of 60 to build turrets and
died). In Factorio's Lua 5.2 with the game stubbed; the prototypes carry the base game's numbers."""
from pathlib import Path
import unittest

from lupa.lua52 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]

PROTOTYPES = """
    local function bite(n) return {type='melee', range=0.5, cooldown=35, damage_modifier=1,
        ammo_type={action={{type='direct', action_delivery={{type='instant', target_effects={
            {type='damage', damage={amount=n, type='physical'}}}}}}}}} end
    local function shot(n) return function() return {action={{type='direct', action_delivery={{type='instant',
        target_effects={{type='damage', damage={amount=n, type='physical'}}}}}}}} end end
    local function biter(name, hp, damage, decrease, percent)
        return {name=name, get_max_health=function() return hp end, attack_parameters=bite(damage),
            resistances={physical={decrease=decrease or 0, percent=percent or 0}}, vision_distance=30, speed=0.24}
    end
    prototypes = {
        entity={
            ['small-biter']=biter('small-biter', 15, 7),
            ['medium-biter']=biter('medium-biter', 75, 15, 4, 0.1),
            ['gun-turret']={name='gun-turret', get_max_health=function() return 400 end,
                attack_parameters={cooldown=6, range=18, damage_modifier=1}, resistances={}},
            ['biter-spawner']={name='biter-spawner', get_max_health=function() return 350 end, max_count_of_owned_units=7,
                resistances={physical={decrease=2, percent=0.15}},
                result_units={{unit='small-biter', spawn_points={{evolution_factor=0, weight=0.3}, {evolution_factor=0.6, weight=0}}},
                              {unit='medium-biter', spawn_points={{evolution_factor=0.2, weight=0}, {evolution_factor=0.6, weight=0.3}}}}},
        },
        item={
            ['submachine-gun']={attack_parameters={cooldown=10, range=18, damage_modifier=1, ammo_categories={'bullet'}}},
            ['pistol']={attack_parameters={cooldown=15, range=15, damage_modifier=1, ammo_categories={'bullet'}}},
            ['firearm-magazine']={magazine_size=10, ammo_category={name='bullet'}, get_ammo_type=shot(5)},
            ['piercing-rounds-magazine']={magazine_size=10, ammo_category={name='bullet'}, get_ammo_type=shot(8)},
        },
    }
    light_armor = {resistances={physical={decrease=3, percent=0.2}}}
"""


def odds():
    lua = LuaRuntime(unpack_returned_tuples=True)
    for name in ("combat_odds", "armory"):
        lua.globals()[name + "_source"] = (ROOT / "mod" / "scripts" / (name + ".lua")).read_text(encoding="utf-8")
    lua.execute("""
        package.preload['scripts.recipes'] = function() return {} end
        package.preload['scripts.armory'] = function() return assert(load(armory_source))() end
        Odds = assert(load(combat_odds_source))()
    """ + PROTOTYPES + """
        force = {get_friend=function() return false end, get_cease_fire=function() return false end}
        enemy = {name='enemy', get_friend=function() return false end, get_cease_fire=function() return false end}
        function us(gun, ammo, rounds, turrets)
            local out = {shooters={Odds.shooter(force, gun, ammo, rounds)}, bodies={{hp=250, resist=light_armor.resistances}}}
            for _ = 1, turrets or 0 do
                local t = Odds.shooter(force, 'gun-turret', ammo, 100, true)
                out.shooters[#out.shooters + 1] = t
                out.bodies[#out.bodies + 1] = {hp=t.hp, resist=t.resist}
            end
            return out
        end
        function them(name, n)
            local facts = Odds.foe_facts(prototypes.entity[name])
            return {{hp=facts.hp, count=n, facts=facts}}
        end
        function ratio(...) local a = {...}; return Odds.fight(us(a[1], a[2], a[3], a[4]), them(a[5], a[6])).ratio end
    """)
    return lua


class OddsTests(unittest.TestCase):
    def test_damage_through_resistances_as_the_game_works_it_out(self):
        lua = odds()
        self.assertAlmostEqual(lua.eval("Odds.after(7, {decrease=3, percent=0.2})"), 3.2)        # a bite on light armor
        self.assertAlmostEqual(lua.eval("Odds.after(5, {decrease=4, percent=0.1})"), 0.9)        # a magazine on a medium
        self.assertAlmostEqual(lua.eval("Odds.after(3, {decrease=4, percent=0})"), 1 / 3)        # below 1: tails off
        self.assertEqual(lua.eval("Odds.after(5, nil)"), 5)

    def test_a_few_small_biters_it_takes_on_a_wave_it_does_not(self):
        lua = odds()
        few = lua.eval("ratio('submachine-gun', 'firearm-magazine', 200, 0, 'small-biter', 10)")
        self.assertGreater(few, lua.eval("Odds.ENGAGE"))                                       # about 1.8
        wave = lua.eval("ratio('submachine-gun', 'firearm-magazine', 200, 0, 'small-biter', 60)")
        self.assertLess(wave, lua.eval("Odds.LOSING"))                                         # run 34's wave of 60
        turrets = lua.eval("ratio('submachine-gun', 'firearm-magazine', 100, 2, 'small-biter', 60)")
        self.assertGreater(turrets, wave * 5)                                                  # turrets at its feet help
        self.assertLess(turrets, lua.eval("Odds.ENGAGE"))                                      # but not enough for 60
        pistol = lua.eval("ratio('pistol', 'firearm-magazine', 200, 0, 'small-biter', 10)")
        self.assertLess(pistol, few)

    def test_plain_magazines_hardly_scratch_medium_biters(self):
        lua = odds()
        plain = lua.eval("ratio('submachine-gun', 'firearm-magazine', 1000, 0, 'medium-biter', 5)")
        piercing = lua.eval("ratio('submachine-gun', 'piercing-rounds-magazine', 1000, 0, 'medium-biter', 5)")
        self.assertLess(plain, 0.2)                                                            # 0.9 a round
        self.assertAlmostEqual(piercing / plain, 4)                                            # 3.6 a round

    def test_short_of_rounds_to_kill_them_all_it_counts_only_what_it_can_kill(self):
        lua = odds()
        fight = lua.eval("Odds.fight(us('submachine-gun', 'firearm-magazine', 15, 0), them('small-biter', 10))")
        self.assertAlmostEqual(fight.killable, 0.5)                                            # 15 rounds of 30
        full = lua.eval("Odds.fight(us('submachine-gun', 'firearm-magazine', 300, 0), them('small-biter', 10))")
        self.assertLess(fight.ratio, lua.eval("Odds.LOSING"))                                  # no win without them
        self.assertGreater(full.ratio, lua.eval("Odds.ENGAGE"))
        lua.execute("nine = Odds.fight(us('submachine-gun', 'firearm-magazine', 27, 0), them('small-biter', 10))")
        self.assertLess(lua.eval("nine.ratio"), lua.eval("Odds.ENGAGE"))                     # 90%: the last outruns it
        # a nest's own health costs rounds, not firepower (the review)
        lua.execute("""bare = Odds.fight(us('submachine-gun', 'firearm-magazine', 3000, 0), them('small-biter', 10))
            nest = Odds.fight(us('submachine-gun', 'firearm-magazine', 3000, 0), them('small-biter', 10),
                {{hp=350, resist={physical={decrease=2, percent=0.15}}}})""")
        self.assertAlmostEqual(lua.eval("nest.ratio"), lua.eval("bare.ratio"))
        lua.execute("short = Odds.fight(us('submachine-gun', 'firearm-magazine', 60, 0), them('small-biter', 10), "
                    "{{hp=350, resist={physical={decrease=2, percent=0.15}}}})")
        self.assertLess(lua.eval("short.killable"), 1)                                     # but it must have them
        lua.execute("empty = {{hp=350, resist={}}}")
        self.assertEqual(lua.eval("Odds.fight({shooters={}, bodies={{hp=250}}}, {}, empty).ratio"), 0)   # no gun
        self.assertEqual(lua.eval("Odds.fight(us('pistol', 'firearm-magazine', 10, 0), {}, empty).ratio"), 0)
        self.assertGreater(lua.eval("Odds.fight(us('pistol', 'firearm-magazine', 100, 0), {}, empty).ratio"), 99)
        self.assertEqual(lua.eval("Odds.fight({shooters={}, bodies={{hp=250}}}, them('small-biter', 3)).ratio"), 0)

    def test_damage_sent_out_in_a_stream_or_a_projectile_is_read_from_what_it_sends(self):
        # (the headless check, 2026-10-26: a small spitter read 60 a spit and a small worm 1080)
        lua = odds()
        lua.execute("""
            local function sends(kind, name, modifier)
                return {attack_parameters={cooldown=100, damage_modifier=modifier, ammo_type={action={{type='direct',
                    action_delivery={{type=kind, [kind == 'stream' and 'stream' or 'projectile']=name}}}}}},
                    get_max_health=function() return 15 end, name=name .. '-shooter'}
            end
            prototypes.entity['acid-stream'] = {initial_action={{type='direct', action_delivery={{type='instant',
                target_effects={{type='damage', damage={amount=1, type='acid'}}}}}}}}
            prototypes.entity['rocket'] = {action={{type='direct', action_delivery={{type='instant',
                target_effects={{type='damage', damage={amount=200, type='explosion'}}}}}}}}
            spit = Odds.foe_facts(sends('stream', 'acid-stream', 12))
            rocket = Odds.foe_facts(sends('projectile', 'rocket', 1))
            unknown = Odds.foe_facts(sends('stream', 'not-in-the-data', 36))""")
        self.assertEqual(dict(lua.eval("spit.hit").items()), {"acid": 12})
        self.assertEqual(dict(lua.eval("rocket.hit").items()), {"explosion": 200})
        self.assertEqual(dict(lua.eval("unknown.hit").items()), {"physical": 5})   # a stand-in, not times 36

    def test_an_item_is_priced_in_plates_down_its_recipes(self):
        # (O-5 late: the war chest's prices; plates and what no recipe makes count one, fluids nothing)
        lua = odds()
        lua.execute("""
            local R = require('scripts.recipes')
            local book = {
                ['gun-turret']={ingredients={{type='item', name='iron-gear-wheel', amount=10},
                    {type='item', name='copper-plate', amount=10}, {type='item', name='iron-plate', amount=20}}},
                ['iron-gear-wheel']={ingredients={{type='item', name='iron-plate', amount=2}}},
                ['uranium-rounds-magazine']={ingredients={{type='item', name='piercing-rounds-magazine', amount=1},
                    {type='item', name='uranium-238', amount=1}, {type='fluid', name='water', amount=50}}},
                ['piercing-rounds-magazine']={ingredients={{type='item', name='iron-plate', amount=5}}},
            }
            prototypes.recipe = book   -- (by name, as recipes.lua gives them)
            book['catalyst'] = {ingredients={{type='item', name='catalyst', amount=40}, {type='item', name='iron-plate', amount=1}}}
            R.candidates = function(_, item) return book[item] and {item} or {} end
            R.yield = function() return 1 end""")
        self.assertEqual(lua.eval("Odds.plates(nil, 'gun-turret')"), 50)
        self.assertAlmostEqual(lua.eval("Odds.plates(nil, 'uranium-rounds-magazine')"), 11)   # 5 plates, 1 uranium, 50 water
        self.assertEqual(lua.eval("Odds.plates(nil, 'catalyst')"), 1)            # its own output left out

    def test_research_bonuses_count(self):
        lua = odds()
        plain = lua.eval("ratio('submachine-gun', 'firearm-magazine', 200, 0, 'medium-biter', 5)")
        lua.execute("force.get_ammo_damage_modifier = function() return 0.5 end")
        self.assertGreater(lua.eval("ratio('submachine-gun', 'firearm-magazine', 200, 0, 'medium-biter', 5)"), plain * 2)

    def test_what_a_spawner_sends_out_follows_the_evolution(self):
        lua = odds()
        early = lua.eval("Odds.mix(prototypes.entity['biter-spawner'], 0.1)")
        self.assertEqual(dict(early.items()), {"small-biter": 1.0})
        later = lua.eval("Odds.mix(prototypes.entity['biter-spawner'], 0.4)")
        self.assertAlmostEqual(later["small-biter"], 0.4)                                      # 0.1 of 0.25
        self.assertAlmostEqual(later["medium-biter"], 0.6)                                     # 0.15 of 0.25

    def test_a_nest_cluster_counts_the_biters_there_and_those_its_spawners_send_out(self):
        lua = odds()
        lua.execute("""
            evolution = 0.1
            game = {forces={enemy={get_evolution_factor=function() return evolution end}}}
            nest = {}
            function put(name, kind, x)
                local p = prototypes.entity[name]
                nest[#nest + 1] = {valid=true, type=kind, force=enemy, prototype=p, position={x=x, y=0},
                    health=p.get_max_health()}
            end
            put('biter-spawner', 'unit-spawner', 0); put('small-biter', 'unit', 3); put('small-biter', 'unit', 4)
            main = {get_item_count=function(name) return name == 'gun-turret' and 2 or 0 end}
            npc = {character={force=force, health=250, surface={find_entities_filtered=function() return nest end},
                get_main_inventory=function() return main end,
                get_inventory=function() return {{valid_for_read=true, prototype=light_armor}} end}}
            gear = {ready=true, gun='submachine-gun', ammo_name='firearm-magazine', ammo_units=400}
            k = Odds.cluster(npc, {x=0, y=0}, gear)""")
        self.assertEqual((lua.eval("k.defenders"), lua.eval("k.spawners")), (2, 1))
        self.assertEqual(lua.eval("k.reinforcements"), 4)                                     # half of the 7 it keeps
        self.assertEqual(lua.eval("k.rounds"), 400)
        self.assertGreater(lua.eval("k.ratio"), lua.eval("Odds.ENGAGE"))                       # a small nest early on
        lua.execute("evolution = 0.5; k = Odds.cluster(npc, {x=0, y=0}, gear)")
        self.assertLess(lua.eval("k.ratio"), lua.eval("Odds.ENGAGE"))                          # mediums come out now
        lua.execute("gear.ammo_name = 'piercing-rounds-magazine'; k2 = Odds.cluster(npc, {x=0, y=0}, gear)")
        self.assertGreater(lua.eval("k2.ratio"), lua.eval("k.ratio"))


def combat():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().source = (ROOT / "mod" / "scripts" / "combat.lua").read_text(encoding="utf-8")
    for name in ("team_world", "team_war", "combat_odds", "armory"):
        lua.globals()[name + "_source"] = (ROOT / "mod" / "scripts" / (name + ".lua")).read_text(encoding="utf-8")
    lua.execute("""
        for _, name in ipairs({'combat_policy', 'combat_squads', 'combat_equipment', 'combat_turrets', 'recipes'}) do
            package.preload['scripts.' .. name] = function() return {} end
        end
        canceled = {}
        package.preload['scripts.worker'] = function()
            return {cancel=function(npc, reason) canceled[#canceled + 1] = reason; return {status='canceled'} end}
        end
        package.preload['scripts.movement_policy'] = function()
            return {halt=function(c) c.walking_state = {walking=false} end, finish=function() end,
                    steer=function(nav, p, g) return {x=g.x - p.x, y=g.y - p.y} end}
        end
        package.preload['scripts.personality'] = function()
            return {axes=function() return {courage=0.5, aggression=0.5, defense=0} end}
        end
        package.preload['scripts.team_waves'] = function() return {nest_of=function() return nil end} end
        for _, name in ipairs({'team_world', 'team_war', 'combat_odds', 'armory'}) do
            package.preload['scripts.' .. name] = function() return assert(load(_G[name .. '_source']))() end
        end
        C = assert(load(source))()
        Odds = require('scripts.combat_odds')
    """ + PROTOTYPES + """
        defines = {inventory={character_main=1, character_guns=2, character_ammo=3, character_armor=4, turret_ammo=5},
                   shooting={not_shooting=0, shooting_selected=2}}
        storage = {}
        game = {tick=1000, forces={}}
        enemy = {name='enemy', get_friend=function() return false end, get_cease_fire=function() return false end}
        mine = {name='player', get_friend=function() return false end, get_cease_fire=function() return false end,
                character_running_speed_modifier=0}
        world = {}
        surface = {}
        local function near(e, f)
            return not f.radius or (e.position.x - f.position.x)^2 + (e.position.y - f.position.y)^2 <= f.radius^2
        end
        local function kind(e, f)
            if not f.type then return true end
            if type(f.type) == 'string' then return e.type == f.type end
            for _, t in ipairs(f.type) do if t == e.type then return true end end
            return false
        end
        function surface.find_entities_filtered(f)
            local out = {}
            for _, e in ipairs(world) do
                if e.valid and near(e, f) and kind(e, f) and (not f.force or e.force == f.force or type(f.force) == 'table'
                    and (f.force[1] == e.force or f.force[2] == e.force)) then out[#out + 1] = e end
            end
            return out
        end
        function surface.count_entities_filtered(f) return #surface.find_entities_filtered(f) end
        function surface.find_non_colliding_position(_, p) return p end
        local function stack(name, kind, count, extra)
            local s = {valid_for_read=true, name=name, count=count, ammo=10, prototype={type=kind}}
            for k, v in pairs(extra or {}) do s.prototype[k] = v end
            return s
        end
        smg = stack('submachine-gun', 'gun', 1, {attack_parameters={ammo_categories={'bullet'}, range=18, cooldown=10}})
        mags = stack('firearm-magazine', 'ammo', 20, {ammo_category={name='bullet'}, magazine_size=10})
        armor = {valid_for_read=true, name='light-armor', prototype=light_armor}
        pocket = {turrets=0}
        main = setmetatable({get_item_count=function(name) return name == 'gun-turret' and pocket.turrets or 0 end,
            is_empty=function() return true end}, {__len=function() return 0 end})
        npc = {id=1, home={x=-200, y=0}, combat={}}
        npc.character = {valid=true, unit_number=1, surface=surface, force=mine, position={x=0, y=0}, health=250,
            max_health=250, prototype={running_speed=0.15}, reach_distance=10, build_distance=10,
            get_main_inventory=function() return main end,
            get_inventory=function(i)
                if i == defines.inventory.character_guns then return {smg} end
                if i == defines.inventory.character_ammo then return {mags} end
                if i == defines.inventory.character_armor then return {armor} end
                return main
            end}
        function biters(n, x, name)
            for k = 1, n do
                local p = prototypes.entity[name or 'small-biter']
                world[#world + 1] = {valid=true, type='unit', name=p.name, force=enemy, prototype=p, surface=surface,
                    position={x=x + (k % 5), y=math.floor(k / 5)}, health=p.get_max_health(),
                    max_health=p.get_max_health(), is_military_target=true, speed=0.24, unit_number=100 + k}
            end
        end
    """)
    return lua


class KeepClearTests(unittest.TestCase):
    def test_a_wave_it_would_lose_to_and_not_on_it_it_holds_fire_and_keeps_clear(self):
        lua = combat()
        lua.execute("biters(60, 24)")                       # run 34's wave, busy with the wall 24 tiles off
        self.assertTrue(lua.eval("C.tick(npc, 1000)"))      # combat holds the body
        self.assertEqual(lua.eval("npc.combat.mode"), "keep_clear")
        self.assertFalse(lua.eval("npc.combat.active"))    # no fight started
        self.assertEqual(lua.eval("npc.character.shooting_state.state"), 0)   # not a shot: it would call them all
        self.assertEqual(list(lua.eval("canceled").values()), ["combat_interrupt"])   # what walked it here, broken off
        self.assertLess(lua.eval("npc.combat.goal.x"), -10)                          # away from them, toward home
        self.assertTrue(lua.eval("npc.character.walking_state.walking"))
        self.assertTrue(lua.eval("C.keeping_clear(npc, 1000)"))
        # its build sites in their sight wait (designed_start's fought_over), those out of it go on (O-4)
        self.assertTrue(lua.eval("C.in_reach(npc, {x=0, y=0}, 1000)"))
        self.assertFalse(lua.eval("C.in_reach(npc, {x=-45, y=0}, 1000)"))
        self.assertEqual(lua.eval("npc.combat.odds_log[1].choice"), "keep_clear")
        # out beyond their sight: it holds a moment (the fence), then lets its work go on, but never back in
        lua.execute("npc.character.position = {x=-20, y=0}")
        self.assertTrue(lua.eval("C.tick(npc, 1031)"))
        self.assertTrue(lua.eval("npc.character.walking_state.walking"))     # (they come 14 tiles in two looks: on out)
        lua.execute("npc.character.position = {x=-29, y=0}")
        self.assertTrue(lua.eval("C.tick(npc, 1062)"))
        self.assertFalse(lua.eval("npc.character.walking_state.walking"))
        self.assertFalse(lua.eval("C.tick(npc, 1062 + 121)"))
        lua.execute("npc.character.position = {x=-5, y=0}")   # walked back in by its work
        self.assertTrue(lua.eval("C.tick(npc, 1214)"))
        self.assertEqual(len(lua.eval("canceled")), 2)        # broken off again
        self.assertEqual(lua.eval("npc.combat.odds_log[2].choice"), "fence:2")
        self.assertEqual(lua.eval("npc.combat.fence_until"), 1214 + 240)   # and it holds out twice as long

    def test_a_group_it_can_beat_or_one_already_on_it_is_fought(self):
        lua = combat()
        lua.execute("biters(6, 20)")
        w = lua.eval("C.watch(npc, 1000)")
        self.assertGreater(w.ratio, lua.eval("Odds.ENGAGE"))
        self.assertFalse(lua.eval("C.keeps_clear(npc, npc.combat.wave)"))
        lua.execute("world = {}; npc.combat = {}; biters(60, 24); npc.combat.hurt_tick = 990")   # biting it already
        lua.eval("C.watch(npc, 1000)")
        self.assertTrue(lua.eval("npc.combat.wave.on_it"))
        self.assertFalse(lua.eval("C.keeps_clear(npc, npc.combat.wave)"))   # no choice now: it fights
        lua.execute("""npc.combat = {}
            world[1].commandable = {distraction_command={target=npc.character}}""")   # one of them after it
        lua.eval("C.watch(npc, 1000)")
        self.assertTrue(lua.eval("npc.combat.wave.on_it"))
        lua.execute("""npc.combat = {}
            world[1].commandable = {command={target=npc.character}}""")   # a group sent at it
        lua.eval("C.watch(npc, 1000)")
        self.assertTrue(lua.eval("npc.combat.wave.on_it"))
        lua.execute("world[1].commandable = nil; npc.combat = {}")
        lua.eval("C.watch(npc, 1000)")
        self.assertFalse(lua.eval("npc.combat.wave.on_it"))
        lua.execute("npc.combat.hurt_tick = 1010")                    # bitten since the last look
        self.assertFalse(lua.eval("C.keeps_clear(npc, npc.combat.wave, 1015)"))

    def test_its_turrets_and_the_teams_count_toward_the_odds(self):
        lua = combat()
        lua.execute("biters(20, 24)")
        alone = lua.eval("C.watch(npc, 1000).ratio")
        lua.execute("""
            npc.combat = {}
            local p = prototypes.entity['gun-turret']
            for k = 1, 4 do
                world[#world + 1] = {valid=true, type='ammo-turret', name='gun-turret', force=mine, prototype=p,
                    position={x=6, y=k * 3 - 6}, health=400,
                    get_inventory=function() return {is_empty=function() return false end,
                        get_item_count=function() return 20 end,
                        {valid_for_read=true, name='firearm-magazine'}} end}
            end""")
        covered = lua.eval("C.watch(npc, 1000).ratio")
        self.assertGreater(covered, alone * 4)
        self.assertGreater(covered, lua.eval("Odds.ENGAGE"))   # behind the wall's turrets it joins in

    def test_walls_in_their_way_and_armed_friends_beside_it_count(self):
        lua = combat()
        lua.execute("biters(20, 24)")
        alone = lua.eval("C.watch(npc, 1000).ratio")
        lua.execute("""npc.combat = {}
            for k = 1, 10 do world[#world + 1] = {valid=true, type='wall', force=mine, position={x=6, y=k - 5},
                health=350, prototype={resistances={}}} end""")
        walled = lua.eval("C.watch(npc, 1000).ratio")
        self.assertGreater(walled, alone)
        lua.execute("""npc.combat = {}
            for _, e in ipairs(world) do if e.type == 'wall' then e.position.x = -6 end end""")   # behind it
        self.assertAlmostEqual(lua.eval("C.watch(npc, 1000).ratio"), alone)            # not in their way: no help
        # a wave from two sides: the wall on one side counts for only those it stops (the review)
        lua.execute("""npc.combat = {}
            for _, e in ipairs(world) do if e.type == 'wall' then e.position.x = 6 end end
            biters(20, -24)""")
        both = lua.eval("C.watch(npc, 1000)")
        lua.execute("npc.combat = {}; for _, e in ipairs(world) do if e.type == 'wall' then e.valid = false end end")
        bare = lua.eval("C.watch(npc, 1000).ratio")
        lua.execute("for _, e in ipairs(world) do if e.type == 'wall' then e.valid = true end end")
        self.assertLess(both.ratio / bare, walled / alone)                               # less help than from one side
        lua.execute("for k = #world, 1, -1 do if world[k].type == 'unit' and world[k].position.x < 0 then table.remove(world, k) end end")
        lua.execute("""npc.combat = {}
            for _, e in ipairs(world) do if e.type == 'wall' then e.position.x = 6 end end""")
        lua.execute("""npc.combat = {}
            for k = 1, 3 do world[#world + 1] = {valid=true, type='character', force=mine, position={x=-2, y=k},
                health=250, get_inventory=npc.character.get_inventory, get_main_inventory=npc.character.get_main_inventory} end""")
        self.assertGreater(lua.eval("C.watch(npc, 1000).ratio"), walled * 2)   # three more guns
        lua.execute("""npc.combat = {}
            for _, e in ipairs(world) do if e.type == 'character' then e.position.x = -25 end end""")
        self.assertAlmostEqual(lua.eval("C.watch(npc, 1000).ratio"), walled)           # too far off to shoot

    def test_biters_camped_at_its_buildings_are_found_once_they_linger_and_cleared_when_it_can(self):
        # (O-6: a wave's four small biters stayed by its iron field; the furnace they broke waited all run)
        lua = combat()
        lua.execute("""npc.home = {x=0, y=0}
            world[#world + 1] = {valid=true, type='mining-drill', force=mine, position={x=60, y=2}}
            biters(4, 62)""")
        self.assertIsNone(lua.eval("C.intruders(npc, 1000)"))                  # just come: a wave passing, maybe
        g = lua.eval("C.intruders(npc, 1000 + C.LINGER_FOR)")
        self.assertEqual((g.n, g.since), (4, 1000))
        self.assertGreater(g.ratio, lua.eval("Odds.ENGAGE"))                   # four small ones: an easy win
        lua.execute("for _, e in ipairs(world) do if e.type == 'mining-drill' then e.valid = false end end")
        self.assertIsNone(lua.eval("C.intruders(npc, 1000 + 2 * C.LINGER_FOR)"))   # out in the wild: not its business
        # sent to clear them: it walks there (the work in hand broken off), its fight takes over in range
        lua.execute("npc.clearing = {x=62, y=0, n=4, ratio=2, until_tick=9000}")
        self.assertTrue(lua.eval("C.go_clear(npc, 2000)"))
        self.assertEqual(lua.eval("npc.combat.mode"), "clear")
        self.assertEqual(list(lua.eval("canceled").values()), ["combat_interrupt"])
        self.assertGreater(lua.eval("npc.combat.goal.x"), 40)
        lua.execute("npc.character.position = {x=46, y=0}")
        self.assertFalse(lua.eval("C.go_clear(npc, 2100)"))                    # there: the fight's own
        self.assertFalse(lua.eval("C.go_clear(npc, 9001)"))                    # (and given up in time)
        # a big group it cannot beat: no walk to them
        lua.execute("""world[#world + 1] = {valid=true, type='furnace', force=mine, position={x=60, y=2}}
            biters(60, 62); npc.lingering = nil; npc.character.position = {x=0, y=0}""")
        lua.eval("C.intruders(npc, 3000)")
        g = lua.eval("C.intruders(npc, 3000 + C.LINGER_FOR)")
        self.assertLess(g.ratio, lua.eval("Odds.ENGAGE"))

    def test_a_drive_stops_short_of_biters_it_would_lose_to_ahead_or_where_it_goes(self):
        # (O-8: on a build job it drove its tank in among 57 biters and big worms; driving, its fights are not ticked)
        lua = combat()
        lua.execute("biters(60, 300)")
        self.assertEqual(lua.eval("C.should_abort_drive(npc, 1000, {x=300, y=0}, {x=60, y=0})"), "biters_at_goal")
        self.assertEqual(lua.eval("C.should_abort_drive(npc, 1000, {x=900, y=0}, {x=290, y=0})"), "biters_ahead")
        self.assertIsNone(lua.eval("C.should_abort_drive(npc, 1000, {x=-300, y=0}, {x=-60, y=0})"))   # clear road
        lua.execute("world = {}; biters(4, 300)")
        self.assertIsNone(lua.eval("C.should_abort_drive(npc, 1000, {x=300, y=0}, nil)"))   # a few it beats: on
        self.assertEqual(lua.eval("npc.combat.odds_log[#npc.combat.odds_log].choice"), "drive_stop:ahead")

    def test_inside_a_worms_reach_it_steps_straight_out_unless_it_chose_that_fight(self):
        # (O-8: a recall walked it within a behemoth worm's reach; dead in two seconds; worms were not in its look)
        lua = combat()
        lua.execute("""world[#world + 1] = {valid=true, type='turret', name='behemoth-worm-turret', force=enemy,
            surface=surface, position={x=40, y=0}, health=3000, prototype={attack_parameters={range=48}}}""")
        self.assertTrue(lua.eval("C.tick(npc, 1000)"))
        self.assertEqual(lua.eval("npc.combat.mode"), "keep_clear")
        self.assertLess(lua.eval("npc.combat.goal.x"), -10)                   # out of its 48 (+6), away from it
        lua.execute("npc.combat = {hurt_tick=990}")                           # being spat at: out all the same
        self.assertTrue(lua.eval("C.tick(npc, 1100)"))
        self.assertEqual(lua.eval("npc.combat.mode"), "keep_clear")
        # a worm ahead and biters behind: out sideways, not into the biters (the review)
        lua.execute("npc.combat = {}; biters(30, -30)")
        lua.eval("C.tick(npc, 1150)")
        self.assertGreater(lua.eval("npc.combat.goal.x"), -20)                 # not walked back into them
        lua.execute("for k = #world, 1, -1 do if world[k].type == 'unit' then table.remove(world, k) end end")
        # (out of the worm's reach, then a wave near: the next step must not stumble on the worm's entry, the crash)
        lua.execute("world[#world].valid = false; biters(60, 24)")
        self.assertTrue(lua.eval("C.tick(npc, 1200)"))
        lua.execute("world = {}; npc.combat = {}; npc.combat_objective = {x=40, y=0}; npc.combat_mission_until = 5000")
        lua.execute("""world[#world + 1] = {valid=true, type='turret', name='behemoth-worm-turret', force=enemy,
            surface=surface, position={x=40, y=0}, health=3000, prototype={attack_parameters={range=48}}}""")
        lua.eval("C.watch(npc, 1200)")
        self.assertIsNone(lua.eval("npc.combat.wave"))                        # its own attack on them: no

    def test_hurt_on_its_own_attack_it_steps_out_of_the_worms_reach_until_healed(self):
        # (O-9, the user's clear: below 60% on an attack it stepped out of worm and biter reach, back in healed)
        lua = combat()
        lua.execute("""world = {}; npc.combat = {}; npc.combat_objective = {x=40, y=0}; npc.combat_mission_until = 5000
            world[#world + 1] = {valid=true, type='turret', name='behemoth-worm-turret', force=enemy,
            surface=surface, position={x=40, y=0}, health=3000, prototype={attack_parameters={range=48}}}""")
        lua.eval("C.watch(npc, 1200)")
        self.assertIsNone(lua.eval("npc.combat.wave"))                        # healthy: its own fight
        lua.execute("npc.character.health = 0.5 * npc.character.max_health; npc.combat.wave = nil")
        lua.eval("C.watch(npc, 1300)")
        self.assertIsNotNone(lua.eval("npc.combat.wave and npc.combat.wave.worm"))   # hurt: out of its reach
        lua.execute("npc.character.health = 0.7 * npc.character.max_health; npc.combat.wave = nil")
        lua.eval("C.watch(npc, 1400)")
        self.assertIsNotNone(lua.eval("npc.combat.wave and npc.combat.wave.worm"))   # not yet healed
        lua.execute("npc.character.health = npc.character.max_health; npc.combat.wave = nil")
        lua.eval("C.watch(npc, 1500)")
        self.assertIsNone(lua.eval("npc.combat.wave"))                        # healed: back in
        lua.execute("npc.character.health = 0.5 * npc.character.max_health; npc.combat_mission_until = 1000")
        self.assertFalse(lua.eval("C.stepping_out(npc, npc.character, 1600)"))   # not on an attack: the usual rules

    def test_in_a_fight_its_place_is_behind_its_turrets(self):
        lua = combat()
        lua.execute("""
            biters(1, 6)
            target = world[1]
            local p = prototypes.entity['gun-turret']
            world[#world + 1] = {valid=true, type='ammo-turret', name='gun-turret', force=mine, prototype=p,
                position={x=3, y=0}, health=400,
                get_inventory=function() return {is_empty=function() return false end} end}""")
        p, by_turret = lua.eval("C.back_off(npc, target, 1, 0)")
        self.assertTrue(by_turret)
        self.assertLess(p.x, 3)                              # behind its turret, away from the biter
        self.assertGreater(p.x, -2)                          # not on away from it
        lua.execute("""options = {{id='retreat'}, {id='hold'}}
            function pick(d) return options[C.rule_pick(options, d, 6, 16, true)].id end""")
        self.assertEqual(lua.eval("pick(3)"), "hold")        # behind it already: it stands and shoots
        # its turret past the biter: no kite through it to get behind (the review)
        lua.execute("world[2].position = {x=8, y=0}; target.position = {x=5, y=0}")
        p, by_turret = lua.eval("C.back_off(npc, target, 1, 0)")
        self.assertFalse(by_turret)
        self.assertLess(p.x, 0)                              # straight back instead

    def test_from_faster_biters_no_run_past_them_to_a_friend(self):
        lua = combat()
        lua.execute("biters(1, -10); target = world[1]")
        self.assertTrue(lua.eval("C.reachable(npc, target, {x=20, y=0})"))       # behind it, a short run
        self.assertFalse(lua.eval("C.reachable(npc, target, {x=-30, y=0})"))     # past the biter
        self.assertFalse(lua.eval("C.reachable(npc, target, {x=70, y=0})"))      # too far: caught on the way
        lua.execute("target.position = {x=3, y=0}")
        self.assertFalse(lua.eval("C.reachable(npc, target, {x=9, y=0})"))       # one at its elbow: not past it
        self.assertTrue(lua.eval("C.reachable(npc, target, {x=-9, y=0})"))
        lua.execute("target.speed = 0.1; target.prototype = {speed=0.1}")
        self.assertTrue(lua.eval("C.reachable(npc, target, {x=-30, y=0})"))      # slow ones it can go round


if __name__ == "__main__":
    unittest.main()
