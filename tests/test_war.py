"""The war side of an engineer's mind (war.lua): its home's alarm and whether the attack calls it back,
in Factorio's Lua 5.2 with the game stubbed."""
from pathlib import Path
import unittest

from lupa.lua52 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]


def runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().source = (ROOT / "mod" / "scripts" / "war.lua").read_text(encoding="utf-8")
    lua.globals().team_source = (ROOT / "mod" / "scripts" / "team_world.lua").read_text(encoding="utf-8")
    lua.execute("""
        package.preload['scripts.team_world'] = function() return assert(load(team_source))() end
        War = assert(load(source))()
        defines = {inventory={turret_ammo=1}}
        game = {tick=1000}
        function force(name)
            local f = {name=name, valid=true, friends={}, truce={}}
            f.get_friend = function(o) return f.friends[o.name] == true end
            f.get_cease_fire = function(o) return f.truce[o.name] == true end
            return f
        end
        ada_force, biters, kevin_force, cora_force = force('npc-faction-1'), force('enemy'), force('npc-faction-2'),
            force('npc-faction-3')
        ada_force.friends['npc-faction-3'] = true
        turrets, attackers = {}, {}
        surface = {find_entities_filtered=function(f)
            if f.type[1] == 'ammo-turret' then return turrets end
            return attackers
        end}
        npc = {id=1, name='Ada', force=ada_force, home={x=0, y=0},
               character={valid=true, position={x=1000, y=0}, surface=surface}}
        storage = {npcs={npc}}
        function building(x, y) return {valid=true, type='assembling-machine', name='assembling-machine-1',
                                        force=ada_force, position={x=x, y=y}} end
        function hit(entity, by, tick) War.on_entity_damaged{entity=entity, force=by, final_damage_amount=10, tick=tick} end
        function loaded_turret()
            return {valid=true, type='ammo-turret', get_inventory=function() return {is_empty=function() return false end} end}
        end
        function biter(health) return {valid=true, force=biters, max_health=health or 15} end
    """)
    return lua


class AlarmTests(unittest.TestCase):
    def test_any_hit_on_its_buildings_at_home_raises_the_alarm_biters_too(self):
        lua = runtime()
        lua.execute("hit(building(20, 0), biters, 1000)")
        self.assertEqual(lua.eval("npc.alarm.hits"), 1)
        self.assertEqual(lua.eval("npc.alarm.position.x"), 20)
        self.assertTrue(lua.eval("npc.alarm.by.enemy"))
        self.assertIsNone(lua.eval("npc.alarm.people"))
        lua.execute("hit(building(25, 0), kevin_force, 1100)")
        self.assertEqual((lua.eval("npc.alarm.hits"), lua.eval("npc.alarm.people")), (2, True))

    def test_its_own_fire_a_friends_its_body_and_far_outposts_raise_none(self):
        lua = runtime()
        lua.execute("""
            hit(building(20, 0), ada_force, 1000)               -- its own grenade
            hit(building(20, 0), cora_force, 1000)              -- an ally's stray shot
            hit({valid=true, type='character', force=ada_force, position={x=0, y=0}}, biters, 1000)
            hit(building(900, 0), biters, 1000)                 -- far beyond home""")
        self.assertIsNone(lua.eval("npc.alarm"))

    def test_the_alarm_lapses_after_a_quiet_minute_and_a_new_attack_starts_afresh(self):
        lua = runtime()
        lua.execute("hit(building(20, 0), biters, 1000)")
        self.assertIsNone(lua.eval("War.tick(npc, 1000 + 3000)"))       # (it holds: nothing there now)
        self.assertIsNotNone(lua.eval("npc.alarm"))
        self.assertFalse(lua.eval("War.over(npc, 1000 + 3000)"))
        lua.eval("War.tick(npc, 1000 + 3700)")
        self.assertIsNone(lua.eval("npc.alarm"))
        self.assertTrue(lua.eval("War.over(npc, 1000 + 3700)"))
        lua.execute("War.on_entity_died{entity=building(20, 0), force=biters, tick=9000}")
        self.assertEqual((lua.eval("npc.alarm.hits"), lua.eval("npc.alarm.lost")), (1, 1))


class CallHomeTests(unittest.TestCase):
    def test_an_engineer_at_its_buildings_calls_it_home(self):
        lua = runtime()
        lua.execute("hit(building(20, 0), kevin_force, 1000)")
        self.assertEqual(lua.eval("War.tick(npc, 1060)"), "attacked by an engineer or a player")

    def test_biters_its_loaded_turrets_hold_off_do_not(self):
        lua = runtime()
        lua.execute("""
            turrets = {loaded_turret()}
            attackers = {biter(), biter(), biter(), biter()}
            hit(building(20, 0), biters, 1000)""")
        self.assertIsNone(lua.eval("War.tick(npc, 1060)"))
        lua.execute("for i = 1, 6 do attackers[#attackers + 1] = biter(75) end")   # a bigger wave than it holds
        self.assertEqual(lua.eval("War.tick(npc, 1120)"), "turrets not holding")

    def test_biters_at_an_undefended_building_call_it_unless_few_and_it_is_far(self):
        lua = runtime()
        lua.execute("attackers = {biter(), biter()}; hit(building(20, 0), biters, 1000)")
        self.assertIsNone(lua.eval("War.tick(npc, 1060)"))                # two small biters, 1000 tiles off
        lua.execute("npc.character.position = {x=200, y=0}")
        self.assertEqual(lua.eval("War.tick(npc, 1120)"), "undefended")    # near enough to come
        lua.execute("npc.character.position = {x=1000, y=0}; War.on_entity_died{entity=building(20, 0), force=biters, tick=1130}")
        self.assertEqual(lua.eval("War.tick(npc, 1180)"), "buildings lost")

    def test_there_already_or_fighting_for_its_life_it_is_not_called(self):
        lua = runtime()
        lua.execute("hit(building(20, 0), kevin_force, 1000); npc.character.position = {x=30, y=10}")
        self.assertIsNone(lua.eval("War.tick(npc, 1060)"))                 # its combat answers them
        lua.execute("npc.character.position = {x=1000, y=0}; npc.combat = {active=true}")
        self.assertIsNone(lua.eval("War.tick(npc, 1120)"))                 # itself first
        lua.execute("npc.combat = nil; npc.recall = {}")
        self.assertIsNone(lua.eval("War.tick(npc, 1180)"))                 # on its way already


def allies():
    """Ada at home (two loaded turrets there) and her friend Cora, 600 tiles off, hit by biters."""
    lua = runtime()
    lua.execute("""
        game.tick = 100000
        game.forces = {enemy=biters, ['npc-faction-2']=kevin_force}
        ada_force.friends['npc-faction-3'] = true
        npc.character.position = {x=5, y=0}
        home_turrets = {loaded_turret(), loaded_turret()}
        cora_turrets, cora_attackers = {}, {biter(), biter(), biter()}
        surface.find_entities_filtered = function(f)
            if f.type[1] == 'ammo-turret' then return f.position == npc.home and home_turrets or {} end
            return attackers
        end
        cora_surface = {find_entities_filtered=function(f)
            if f.type[1] == 'ammo-turret' then return cora_turrets end
            return cora_attackers
        end}
        cora = {id=3, name='Cora', force=cora_force, home={x=600, y=0},
                character={valid=true, position={x=600, y=0}, surface=cora_surface}}
        storage.npcs[3] = cora
        War.alarm(cora, {x=610, y=0}, biters, 10, nil, game.tick)""")
    return lua


class AllyTests(unittest.TestCase):
    def test_it_goes_to_help_a_friend_its_own_home_holding_without_it(self):
        lua = allies()
        self.assertEqual(lua.eval("War.ally_in_need(npc, game.tick, 0.6).name"), "Cora")

    def test_not_when_unwilling_or_its_own_home_needs_it(self):
        lua = allies()
        self.assertIsNone(lua.eval("War.ally_in_need(npc, game.tick, 0.2)"))           # not one to help
        lua.execute("home_turrets = {loaded_turret()}")
        self.assertIsNone(lua.eval("War.ally_in_need(npc, game.tick, 0.6)"))           # too little at home
        lua.execute("home_turrets = {loaded_turret(), loaded_turret()}; npc.last_alarm = game.tick - 6000")
        self.assertIsNone(lua.eval("War.ally_in_need(npc, game.tick, 0.6)"))           # attacked lately itself
        lua.execute("npc.last_alarm = nil; npc.combat = {active=true}")
        self.assertIsNone(lua.eval("War.ally_in_need(npc, game.tick, 0.6)"))           # fighting itself

    def test_not_when_their_turrets_hold_someone_is_on_the_way_or_it_cannot_fight_them(self):
        lua = allies()
        lua.execute("cora_turrets = {loaded_turret()}")
        self.assertIsNone(lua.eval("War.ally_in_need(npc, game.tick, 0.6)"))           # they hold
        lua.execute("cora_turrets = {}; storage.npcs[4] = {id=4, helping={ally=3}}")
        self.assertIsNone(lua.eval("War.ally_in_need(npc, game.tick, 0.6)"))           # Dax is going
        lua.execute("storage.npcs[4] = nil; War.alarm(cora, {x=610, y=0}, kevin_force, 10, nil, game.tick)")
        lua.execute("ada_force.truce['npc-faction-2'] = true")
        self.assertIsNone(lua.eval("War.ally_in_need(npc, game.tick, 0.6)"))           # at peace with Kevin
        lua.execute("ada_force.truce['npc-faction-2'] = nil")
        self.assertEqual(lua.eval("War.ally_in_need(npc, game.tick, 0.6).name"), "Cora")   # at war: yes


    def test_a_friend_attacked_by_someone_it_is_at_peace_with_is_told_once_and_nothing_more(self):
        lua = allies()
        lua.execute("""
            storage.npcs[2] = {id=2, name='Kevin', force=kevin_force}
            ada_force.truce['npc-faction-2'] = true
            War.alarm(cora, {x=610, y=0}, kevin_force, 10, nil, game.tick)""")
        told = lua.eval("War.friends_attacked(npc)")
        self.assertEqual((told[1].ally, told[1].by), ("Cora", "Kevin, biters"))
        self.assertEqual(len(lua.eval("War.friends_attacked(npc)")), 0)                # once per attack
        self.assertIsNone(lua.eval("War.ally_in_need(npc, game.tick, 0.9)"))         # no help by itself
        lua.execute("ada_force.truce['npc-faction-2'] = nil")                         # its own choice: war
        self.assertEqual(lua.eval("War.ally_in_need(npc, game.tick, 0.9).name"), "Cora")


class SpoilsTests(unittest.TestCase):
    def test_take_the_base_or_take_what_is_in_it(self):
        lua = runtime()
        lua.execute("""
            function techs(n) local t = {} for i = 1, n do t['t' .. i] = {researched=true} end return t end
            ada_force.technologies, kevin_force.technologies = techs(10), techs(10)""")
        self.assertEqual(lua.eval("(War.spoils(npc, kevin_force, {x=500, y=0}))"), "take")     # near, as far along
        self.assertEqual(lua.eval("(War.spoils(npc, kevin_force, {x=2000, y=0}))"), "loot")    # too far to hold
        lua.execute("ada_force.technologies = techs(16)")
        self.assertEqual(lua.eval("(War.spoils(npc, kevin_force, {x=500, y=0}))"), "loot")     # it is ahead of them
        lua.execute("ada_force.technologies = techs(12)")
        self.assertEqual(lua.eval("(War.spoils(npc, kevin_force, {x=500, y=0}))"), "take")
        self.assertEqual(lua.eval("(War.spoils(npc, kevin_force, {x=500, y=0}, true))"), "loot")   # a car to load

    def test_a_base_it_took_over_is_defended_like_home(self):
        lua = runtime()
        lua.execute("hit(building(2000, 0), biters, 1000)")
        self.assertIsNone(lua.eval("npc.alarm"))
        lua.execute("npc.outposts = {{x=2000, y=10}}; hit(building(2000, 0), biters, 1000)")
        self.assertEqual(lua.eval("npc.alarm.hits"), 1)


def intel():
    lua = runtime()
    lua.globals().intel_source = (ROOT / "mod" / "scripts" / "intel.lua").read_text(encoding="utf-8")
    lua.execute("""
        Intel = assert(load(intel_source))()
        game.connected_players = {}
        players_force = force('player')
        radar_list, found = {}, {}
        surface.index = 1
        surface.find_entities_filtered = function(f)
            if f.type == 'radar' then return f.force == ada_force and radar_list or {} end
            local out = {}
            for _, e in ipairs(found) do
                if (e.position.x - f.position.x)^2 + (e.position.y - f.position.y)^2 <= f.radius^2 then out[#out+1] = e end
            end
            return out
        end
        npc.character.position = {x=0, y=0}
        cora_force.friends['npc-faction-1'] = true   -- allied both ways
        cora = {id=3, name='Cora', force=cora_force, character={valid=true, position={x=1000, y=0}, surface=surface}}
        storage.npcs[3] = cora
        noted = {}
        function note(_, e) noted[#noted+1] = e.name end""")
    return lua


class IntelTests(unittest.TestCase):
    def test_it_sees_with_its_own_eyes_its_friends_and_their_powered_radars(self):
        lua = intel()
        self.assertTrue(lua.eval("Intel.sees(npc, {x=100, y=0}, 1)"))         # its own eyes
        self.assertFalse(lua.eval("Intel.sees(npc, {x=500, y=0}, 1)"))
        self.assertTrue(lua.eval("Intel.sees(npc, {x=1100, y=0}, 1)"))        # Cora, a friend, is there
        cora = "cora.force = kevin_force"
        lua.execute(cora)
        self.assertFalse(lua.eval("Intel.sees(npc, {x=1100, y=0}, 1)"))       # Kevin is no friend of hers
        lua.execute("radar_list = {{valid=true, energy=10, position={x=500, y=0}}}; storage.intel_radars = nil")
        self.assertTrue(lua.eval("Intel.sees(npc, {x=550, y=0}, 1)"))          # its radar, powered
        lua.execute("radar_list[1].energy = 0")
        self.assertFalse(lua.eval("Intel.sees(npc, {x=550, y=0}, 1)"))

    def test_it_knows_only_roughly_where_someone_started_and_always_the_same(self):
        lua = intel()
        p = lua.eval("Intel.roughly(npc, 'npc:2', {x=300, y=0})")
        d = ((p.x - 300) ** 2 + p.y ** 2) ** 0.5
        self.assertTrue(16 <= d <= 48.01, d)
        q = lua.eval("Intel.roughly(npc, 'npc:2', {x=300, y=0})")
        self.assertEqual((p.x, p.y), (q.x, q.y))

    def test_what_friends_and_radars_see_of_nests_and_enemy_turrets_it_knows_too(self):
        lua = intel()
        lua.execute("""
            found = {{valid=true, name='nest-by-cora', force=biters, position={x=1050, y=0}},
                     {valid=true, name='kevin-turret', force=kevin_force, position={x=1060, y=0}},
                     {valid=true, name='cora-turret', force=cora_force, position={x=1070, y=0}},
                     {valid=true, name='far-nest', force=biters, position={x=500, y=300}}}
            Intel.watch(npc, 100, note)""")
        self.assertEqual(sorted(lua.eval("noted").values()), ["kevin-turret", "nest-by-cora"])
        lua.execute("""noted = {}
            radar_list = {{valid=true, energy=10, position={x=500, y=0}}}; storage.intel_radars = nil
            Intel.watch(npc, 3800, note)""")                                   # its radar's sweep: 448 round it
        self.assertIn("far-nest", list(lua.eval("noted").values()))
        lua.execute("noted = {}; Intel.watch(npc, 3900, note)")                # the next sweep is a minute off
        self.assertNotIn("far-nest", list(lua.eval("noted").values()))


if __name__ == "__main__":
    unittest.main()
