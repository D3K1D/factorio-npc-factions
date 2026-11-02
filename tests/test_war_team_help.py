"""Help in the team world (war.lua): weighed, "some, not all" (the user). A teammate's plot hit and not holding gets
the nearest engineers who bring something to the fight, as many as the attack outweighs its turrets, never one whose
own plot was hit lately or stands unguarded (D-12), in Factorio's Lua 5.2 with the game stubbed."""
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
        settings = {global={['npc-factions-scenario']={value='team'}}}
        defines = {inventory={turret_ammo=1, character_guns=2}}
        function force(name)
            local f = {name=name, valid=true}
            f.get_friend = function(o) return name ~= 'enemy' and o.name ~= 'enemy' end
            f.get_cease_fire = function(o) return name ~= 'enemy' and o.name ~= 'enemy' end
            return f
        end
        enemy, players = force('enemy'), force('player')
        -- what stands round a spot: each engineer's loaded turrets round its home; biters round an attacked spot
        guards, biters = {}, {}
        local function near(p, q, r) return (p.x - q.x) ^ 2 + (p.y - q.y) ^ 2 <= r * r end
        surface = {find_entities_filtered=function(f)
            local out = {}
            if f.type[1] == 'ammo-turret' then
                for _, t in ipairs(guards) do
                    if (t.force == f.force or (type(f.force) == 'table' and f.force[1] == nil and t.force == f.force))
                        and near(t.position, f.position, f.radius) then out[#out + 1] = t end
                end
            else
                for _, b in ipairs(biters) do if near(b.position, f.position, f.radius) then out[#out + 1] = b end end
            end
            return out
        end}
        function turret(f, x, y) guards[#guards + 1] = {valid=true, type='ammo-turret', force=f, position={x=x, y=y},
            get_inventory=function() return {is_empty=function() return false end} end} end
        andre = {valid=true, index=1, name='Andre', force=players, character={valid=true}}
        game = {tick=100000, forces={enemy=enemy, player=players}, get_player=function(i) return i == 1 and andre or nil end,
                surfaces={surface}}
        War = assert(load(source))()
        storage = {npcs={}}
        -- seven engineers on a line every 100 tiles, each with a loaded turret at home, 2 turrets and 40 magazines
        for id = 1, 7 do
            local f = force('npc-faction-' .. id)
            local n = {id=id, name='E' .. id, force=f, home={x=(id - 1) * 100, y=0},
                carry={['gun-turret']=2, ['firearm-magazine']=40}}
            n.character = {valid=true, position={x=(id - 1) * 100, y=0}, surface=surface, force=f,
                get_item_count=function(name) return n.carry[name] or 0 end,
                get_inventory=function() return {} end}
            storage.npcs[id] = n
            turret(f, (id - 1) * 100 + 5, 0)
        end
        function attacked(id, n, lost)
            local x = (id - 1) * 100
            storage.npcs[id].alarm = {first=game.tick, last=game.tick, position={x=x, y=20}, hits=5,
                damage=100, lost=lost or 0, by={enemy=true}}
            storage.npcs[id].last_alarm = game.tick
            for k = 1, n do biters[#biters + 1] = {valid=true, force=enemy, max_health=15, position={x=x, y=20}} end
        end
        function helpers_of(id)
            local out = {}
            for i = 1, 7 do
                local ally = War.ally_in_need(storage.npcs[i], game.tick, 0.5)
                if ally and ally.id == id then
                    out[#out + 1] = i
                    storage.npcs[i].helping = {ally=id, brings=War.brings(storage.npcs[i])}   -- (it sets off)
                end
            end
            return out
        end
    """)
    return lua


class TeamHelpTests(unittest.TestCase):
    def test_help_is_sized_to_the_attack_none_one_or_more(self):
        lua = runtime()
        lua.execute("attacked(4, 4)")          # four small biters; its own turret there holds five: none needed
        self.assertEqual(list(lua.eval("helpers_of(4)").values()), [])
        lua = runtime()
        lua.execute("attacked(4, 12)")         # twelve: seven short; one helper brings ten (2 turrets loaded)
        self.assertEqual(len(lua.eval("helpers_of(4)")), 1)
        lua = runtime()
        lua.execute("attacked(4, 40)")         # forty: a big wave, more (at most three)
        got = sorted(lua.eval("helpers_of(4)").values())
        self.assertEqual(len(got), 3)
        self.assertTrue({3, 5} <= set(got))    # the nearest first

    def test_one_who_brings_nothing_or_whose_home_is_hit_or_unguarded_stays(self):
        lua = runtime()
        lua.execute("""
            storage.npcs[3].carry = {}                         -- only its pistol, no magazines to spare
            storage.npcs[5].last_alarm = game.tick - 6000      -- its own plot hit two minutes ago
            for i, t in ipairs(guards) do if t.position.x == 505 then table.remove(guards, i) end end   -- E6 unguarded
            attacked(4, 12)""")
        self.assertEqual(list(lua.eval("helpers_of(4)").values()), [2])   # E3, E5, E6 stay: E2 (200 off) goes

    def test_too_far_to_arrive_while_it_lasts_does_not_go_and_help_turns_back_once_it_holds(self):
        lua = runtime()
        lua.execute("attacked(1, 40)")
        self.assertNotIn(7, list(lua.eval("helpers_of(1)").values()))   # 600 off: past the fight's end
        self.assertTrue(lua.eval("War.help_wanted(storage.npcs[1], game.tick)"))
        lua.execute("biters = {}")                                       # out of sight there: still on, go on
        self.assertTrue(lua.eval("War.help_wanted(storage.npcs[1], game.tick)"))
        lua.execute("attacked(1, 3)")                                    # what is left its turret holds: turn back
        self.assertFalse(lua.eval("War.help_wanted(storage.npcs[1], game.tick)"))
        lua.execute("game.tick = game.tick + 4000")
        self.assertFalse(lua.eval("War.help_wanted(storage.npcs[1], game.tick)"))   # (over)

    def test_a_lost_building_calls_the_nearest_worthwhile_helper_or_none(self):
        # (designed_start's call when biters take a building: weighed the same way; rehearsal 7a's Fenn was called
        # away four minutes after its own plot was hit, and died)
        lua = runtime()
        lua.execute("for id = 1, 7 do storage.npcs[id].design = {} end; attacked(4, 12)")
        self.assertEqual(lua.eval("War.helper_for(storage.npcs[4], {x=300, y=20}, game.tick).id"), 3)
        lua.execute("storage.npcs[3].last_alarm = game.tick - 14000; storage.npcs[5].carry = {}")
        self.assertEqual(lua.eval("War.helper_for(storage.npcs[4], {x=300, y=20}, game.tick).id"), 2)
        lua = runtime()
        lua.execute("for id = 1, 7 do storage.npcs[id].design = {} end; attacked(4, 3)")   # its turret holds
        self.assertIsNone(lua.eval("War.helper_for(storage.npcs[4], {x=300, y=20}, game.tick)"))

    def test_codex_review_cases(self):
        lua = runtime()
        # its own gun keeps ten magazines: one turret and ten magazines bring nothing
        lua.execute("storage.npcs[3].carry = {['gun-turret']=1, ['firearm-magazine']=10}")
        self.assertEqual(lua.eval("War.brings(storage.npcs[3])"), 0)
        # one on a trip it will not break off is not counted among those nearer: a farther one goes
        lua = runtime()
        lua.execute("War.on_trip = function(n) return n.id == 3 or n.id == 5 end; attacked(4, 12)")
        self.assertEqual(list(lua.eval("helpers_of(4)").values()), [2])
        # one called to build its defense (help_request) counts as help going: covered, no second
        lua = runtime()
        lua.execute("for id = 1, 7 do storage.npcs[id].design = {} end; attacked(4, 12)"
                    "; storage.npcs[3].help_request = {npc_id=4, brings=10}")
        self.assertEqual(list(lua.eval("helpers_of(4)").values()), [])
        self.assertIsNone(lua.eval("War.helper_for(storage.npcs[4], {x=300, y=20}, game.tick)"))

    def test_a_player_under_attack_is_a_teammate_too(self):
        lua = runtime()
        lua.execute("""
            local base = {valid=true, type='assembling-machine', name='assembling-machine-1', force=players,
                          position={x=250, y=10}, last_user=andre}
            War.on_entity_died({entity=base, force=enemy, final_damage_amount=50, tick=game.tick})""")
        self.assertEqual(lua.eval("storage.war_players[1].lost"), 1)
        self.assertEqual(lua.eval("War.ally_in_need(storage.npcs[3], game.tick, 0.5).id"), "player:1")
        ally = lua.eval("War.ally('player:1')")
        self.assertEqual((ally.name, ally.player_index), ("Andre", 1))
        self.assertEqual(lua.eval("War.ally(5).name"), "E5")
        lua.execute("""settings.global['npc-factions-scenario'].value = 'factions'; storage.war_players = nil
            War.on_entity_died({entity={valid=true, type='wall', name='stone-wall', force=players,
                position={x=0, y=0}, last_user=andre}, force=enemy, tick=game.tick})""")
        self.assertIsNone(lua.eval("storage.war_players"))


if __name__ == "__main__":
    unittest.main()
