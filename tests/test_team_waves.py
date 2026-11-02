"""A death world's attack waves (team_waves.lua) and what an engineer knows of its laid start (scouting.lua), in
Factorio's Lua 5.2 with the game stubbed."""
from pathlib import Path
import unittest

from lupa.lua52 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]


def runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)
    for name in ("team_world", "team_waves", "scouting", "combat_odds", "recipes"):
        lua.globals()[name + "_source"] = (ROOT / "mod" / "scripts" / f"{name}.lua").read_text(encoding="utf-8")
    lua.execute("""
        package.preload['scripts.team_world'] = function() return assert(load(team_world_source))() end
        package.preload['scripts.team_waves'] = function() return assert(load(team_waves_source))() end
        package.preload['scripts.scouting'] = function() return assert(load(scouting_source))() end
        package.preload['scripts.combat_odds'] = function() return assert(load(combat_odds_source))() end
        package.preload['scripts.recipes'] = function() return assert(load(recipes_source))() end   -- (combat_odds prices losses)
        package.preload['scripts.armory'] = function()   -- (damage by type: the stub prototypes carry it as it is)
            return {damage_by_type = function(action) local out = {}
                for k, v in pairs(action and action.damage_by or {}) do out[k] = v end return out end}
        end
        function log(line) logged[#logged + 1] = line end
        logged = {}
        strength = 1
        settings = {global = setmetatable({}, {__index = function(_, k)
            if k == 'npc-factions-scenario' then return {value = 'team'} end
            if k == 'npc-factions-death-waves' then return {value = strength} end end})}
        defines = {command = {attack = 'attack', attack_area = 'attack_area'}, distraction = {by_enemy = 'by_enemy'},
                   inventory = {character_guns = 'guns', character_ammo = 'ammo', turret_ammo = 'turret_ammo',
                                character_main = 'main',
                                character_armor = 'armor'}}
        local function d2(a, b) return (a.x - b.x)^2 + (a.y - b.y)^2 end
        local function inside(p, a) return p.x >= a[1][1] and p.x < a[2][1] and p.y >= a[1][2] and p.y < a[2][2] end
        spawner = {result_units = {
            {unit = 'small-biter', spawn_points = {{evolution_factor = 0, weight = 0.3}, {evolution_factor = 0.6, weight = 0}}},
            {unit = 'medium-biter', spawn_points = {{evolution_factor = 0.2, weight = 0}, {evolution_factor = 0.6, weight = 0.3}}}}}
        nests, buildings, units, groups, ores = {}, {}, {}, {}, {}
        local function biter(name, hp, bite, cost)
            return {name = name, absorptions_to_join_attack = {pollution = cost},
                get_max_health = function() return hp end,
                attack_parameters = {cooldown = 35, range = 1, ammo_type = {action = {damage_by = {physical = bite}}}}}
        end
        prototypes = {entity = {
            ['small-biter'] = biter('small-biter', 15, 7, 4),
            ['medium-biter'] = biter('medium-biter', 75, 15, 20),
            ['gun-turret'] = {name = 'gun-turret', get_max_health = function() return 400 end,
                attack_parameters = {cooldown = 10, range = 18, ammo_categories = {'bullet'}}},
            ['iron-ore'] = {mineable_properties = {products = {{type = 'item'}}}}},
          item = {['iron-plate'] = {type = 'item'},
            pistol = {type = 'gun', attack_parameters = {cooldown = 15, range = 15, ammo_categories = {'bullet'}}},
            ['firearm-magazine'] = {type = 'ammo', magazine_size = 10, ammo_category = {name = 'bullet'},
                get_ammo_type = function() return {action = {damage_by = {physical = 5}}} end}}}
        -- an inventory of stacks {name, count}
        function inventory(stacks)
            local inv = {}
            for i, s in ipairs(stacks) do inv[i] = {valid_for_read = true, name = s[1], count = s[2]} end
            inv.is_empty = function() return #stacks == 0 end
            inv.get_item_count = function(name) local n = 0
                for _, s in ipairs(stacks) do if s[1] == name then n = n + s[2] end end return n end
            return setmetatable(inv, {__len = function() return #stacks end})
        end
        turrets = {}
        function turret(x, y, force, magazines)
            local inv = inventory(magazines > 0 and {{'firearm-magazine', magazines}} or {})
            turrets[#turrets + 1] = {valid = true, name = 'gun-turret', type = 'ammo-turret', position = {x = x, y = y},
                force = force, health = 400, get_inventory = function() return inv end}
        end
        function nest(x, y) nests[#nests + 1] = {valid = true, position = {x = x, y = y}, prototype = spawner} end
        function building(x, y, force, kind, pollutes, electric)
            local proto = {emissions_per_second = (pollutes and not electric) and {pollution = 0.03} or {}}
            if electric then proto.electric_energy_source_prototype = {emissions_per_joule = {pollution = 1e-7}} end
            buildings[#buildings + 1] = {valid = true, name = kind or 'stone-furnace', type = kind or 'furnace',
                position = {x = x, y = y}, force = force, is_entity_with_health = kind ~= 'entity-ghost',
                prototype = proto}
        end
        surface = {index = 1}
        function force_name(f) return type(f) == 'table' and f.name or f end
        function surface.find_entities_filtered(f)
            local out = {}
            if type(f.type) == 'table' and f.type[1] == 'ammo-turret' then
                for _, tr in ipairs(turrets) do
                    for _, fc in ipairs(f.force) do
                        if force_name(fc) == force_name(tr.force) and inside(tr.position, f.area) then out[#out + 1] = tr end
                    end
                end
            elseif f.type == 'character' then
            elseif f.type == 'unit-spawner' then
                for _, n in ipairs(nests) do if d2(n.position, f.position) <= f.radius^2 then out[#out + 1] = n end end
            elseif f.type == 'resource' then
                for _, o in ipairs(ores) do if d2(o.position, f.position) <= f.radius^2 then out[#out + 1] = o end end
            elseif f.area and f.force then
                for _, b in ipairs(buildings) do if b.force == force_name(f.force) and inside(b.position, f.area) then out[#out + 1] = b end end
            end
            return out
        end
        function surface.count_entities_filtered(f)
            local n = 0
            for _, b in ipairs(buildings) do
                if b.force == force_name(f.force) and inside(b.position, f.area) then
                    local mobile = false
                    for _, t in ipairs(f.type or {}) do if t == b.type then mobile = true end end
                    if not f.type or mobile then n = n + 1 end
                end
            end
            return n
        end
        blocked = {}   -- nests hemmed in: x -> true
        pollution = 0
        function surface.get_pollution() return pollution end
        function surface.find_non_colliding_position(name, p)
            if blocked[p.x] then return nil end
            return {x = p.x, y = p.y}
        end
        function surface.create_entity(e) local u = {name = e.name, position = e.position}; units[#units + 1] = u; return u end
        function surface.create_unit_group(g)
            local group = {position = g.position, members = {}}
            function group.add_member(u) group.members[#group.members + 1] = u end
            function group.set_command(c) group.command = c end
            function group.destroy() group.destroyed = true end
            groups[#groups + 1] = group
            return group
        end
        evolution = 0
        game = {tick = 0, surfaces = {surface}, forces = {player = {name = 'player', valid = true,
                                                                  get_friend = function() return false end},
            enemy = {get_evolution_factor = function() return evolution end}}}
        TW = require('scripts.team_world')
        Waves = require('scripts.team_waves')
        storage = {team = TW.layout({x = 0, y = 0})}
        storage.team.status, storage.team.surface_index, storage.team.death_world = 'ready', 1, true
        storage.team.homes, storage.team.added = {}, {}
        function engineer(id, magazines) local f = 'npc-faction-' .. id
            storage.npcs = storage.npcs or {}
            -- (an engineer carries its pistol and magazines in its pockets until a fight)
            local guns, ammo = inventory({}), inventory({})
            local main = inventory({{'iron-plate', 8}, {'pistol', 1}, {'firearm-magazine', magazines or 10}})
            storage.npcs[id] = {id = id, force = {valid = true, name = f, get_friend = function() return false end},
                character = {valid = true, surface = surface, health = 250, force = f,
                    get_inventory = function(which)
                        return which == 'guns' and guns or which == 'ammo' and ammo or which == 'main' and main or nil end}}
            return f end
    """)
    return lua


class SpawnTables(unittest.TestCase):
    def test_the_nests_own_table_read_at_the_current_evolution(self):
        lua = runtime()
        w0 = dict(lua.eval("Waves.weights(spawner.result_units, 0)"))
        self.assertEqual(w0, {"small-biter": 0.3})
        w4 = dict(lua.eval("Waves.weights(spawner.result_units, 0.4)"))
        self.assertAlmostEqual(w4["small-biter"], 0.1)
        self.assertAlmostEqual(w4["medium-biter"], 0.15)
        self.assertEqual(dict(lua.eval("Waves.weights(spawner.result_units, 0.9)")), {"medium-biter": 0.3})

    def test_waves_grow_with_evolution_time_and_strength(self):
        lua = runtime()
        def pace(e, m, s):
            return lua.eval(f"{{Waves.pace({e}, {m}, {s})}}")
        start, later, evolved, hard = pace(0, 0, 1), pace(0, 120, 1), pace(0.5, 0, 1), pace(0, 0, 2)
        self.assertGreater(later[1], start[1])
        self.assertLess(later[2], start[2])
        self.assertGreater(evolved[1], start[1])
        self.assertLess(evolved[2], start[2])
        self.assertEqual(hard[1], 2 * start[1])
        self.assertLess(hard[2], start[2])
        self.assertLessEqual(pace(1, 600, 5)[1], 400)


class Waves(unittest.TestCase):
    def setUp(self):
        self.lua = lua = runtime()
        lua.execute("""
            a, b = engineer(1), engineer(3)
            building(-400, -400, a, 'stone-furnace', true); building(-380, -380, a, 'wooden-chest', false)
            building(400, -400, b, 'assembling-machine', true, true)   -- (electric: its pollution is its power's)
            nest(-700, -700); nest(700, -700); nest(-720, -720)
        """)

    def run_until(self, tick):
        self.lua.execute(f"for t = 60, {tick}, 60 do game.tick = t; Waves.tick(t) end")

    def test_none_before_the_grace_then_waves_go_round_the_bases_fairly(self):
        self.run_until(8 * 3600)
        self.assertEqual(self.lua.eval("#groups"), 0)
        self.run_until(32 * 3600)
        waves = self.lua.eval("storage.team.waves")
        self.assertGreaterEqual(waves.sent, 3)
        counts = dict(waves.per_plot)
        self.assertEqual(set(counts), {"npc-1", "npc-3"})
        self.assertLessEqual(abs(counts["npc-1"] - counts["npc-3"]), 1)
        # each wave a unit group from its nests, told to attack a polluting building of the base
        first = self.lua.eval("groups[1]")
        self.assertEqual(first.command.type, "attack")
        self.assertIn(first.command.target.name, ("stone-furnace", "assembling-machine"))
        self.assertTrue(all(m.name == "small-biter" for m in first.members.values()))
        self.assertIn("death-world wave 1", self.lua.eval("logged[1]"))
        last = self.lua.eval("storage.team.waves.log[#storage.team.waves.log]")
        self.assertEqual(sum(dict(last.kinds).values()), last.size)
        self.assertTrue(any(w.command.target and w.command.target.name == "assembling-machine"
                            for w in self.lua.eval("groups").values()))

    def test_an_evolved_wave_brings_its_threat_in_fewer_bigger_biters(self):
        self.lua.execute("evolution = 0.9")
        self.run_until(12 * 3600)
        last = self.lua.eval("storage.team.waves.log[1]")
        self.assertEqual(set(dict(last.kinds)), {"medium-biter"})
        self.assertLess(last.size, last.threat)                       # (each counts five small biters)
        self.assertAlmostEqual(last.threat, 5 * last.size, delta=1)

    def test_a_players_half_with_only_ghosts_or_people_is_no_base(self):
        self.lua.execute("""
            building(-60, 0, 'player', 'entity-ghost', false); building(-50, 0, 'player', 'character', false)
        """)
        self.run_until(40 * 3600)
        self.assertNotIn("player-1", dict(self.lua.eval("storage.team.waves.per_plot")))
        self.lua.execute("building(-40, 0, 'player', 'stone-furnace', true)")
        self.run_until(80 * 3600)
        self.assertIn("player-1", dict(self.lua.eval("storage.team.waves.per_plot")))

    def test_a_stronger_mix_comes_in_fewer_units(self):
        self.lua.execute("evolution = 0.9")   # (all medium: five small biters' threat each)
        self.run_until(12 * 3600)
        medium = self.lua.eval("storage.team.waves.log[1]")
        self.assertEqual(set(dict(medium.kinds)), {"medium-biter"})
        self.assertAlmostEqual(medium.threat, 5 * medium.size, delta=1)   # (its threat in a fifth as many units)

    def test_hemmed_in_nests_give_way_to_the_next_nearest(self):
        self.lua.execute("""
            nests = {}
            nest(-600, -600); nest(-610, -610); nest(-620, -620); nest(-900, -900)
            blocked[-600], blocked[-610], blocked[-620] = true, true, true
        """)
        self.run_until(12 * 3600)
        self.assertGreaterEqual(self.lua.eval("storage.team.waves.sent"), 1)
        self.assertTrue(all(u.position.x == -900 for u in self.lua.eval("units").values()))

    def test_an_engineer_with_nothing_built_yet_is_no_base(self):
        self.lua.execute("engineer(5)")   # (alive, no building on its plot)
        self.run_until(60 * 3600)
        self.assertNotIn("npc-5", dict(self.lua.eval("storage.team.waves.per_plot")))

    def test_with_no_nest_left_the_waves_wait_and_say_so_once(self):
        self.lua.execute("nests = {}")
        self.run_until(12 * 3600)
        self.assertEqual(self.lua.eval("storage.team.waves.sent"), 0)
        self.assertEqual(self.lua.eval("storage.team.waves.paused"), "no_nests")
        self.assertEqual(sum(1 for l in self.lua.eval("logged").values() if "waves wait" in l), 1)
        self.lua.execute("nest(-700, -700)")   # (the biters expand again)
        self.run_until(14 * 3600)
        self.assertGreaterEqual(self.lua.eval("storage.team.waves.sent"), 1)

    def test_off_with_strength_zero_or_outside_a_death_world(self):
        self.lua.execute("strength = 0")
        self.run_until(20 * 3600)
        self.assertEqual(self.lua.eval("#groups"), 0)
        self.lua.execute("strength = 1; storage.team.death_world = false")
        self.run_until(20 * 3600)
        self.assertEqual(self.lua.eval("#groups"), 0)

    def test_a_base_with_no_nest_anywhere_near_is_passed_over(self):
        self.lua.execute("nests = {}; nest(-700, -700)")   # (only by npc-1: 2048 tiles reach npc-3 too)
        self.lua.execute("nests[1].position = {x = -3000, y = -3000}")
        self.run_until(12 * 3600)
        counts = dict(self.lua.eval("storage.team.waves.per_plot"))
        self.assertGreaterEqual(counts.get("npc-1", 0), 1)
        self.assertEqual(counts.get("npc-3", 0), 0)


class KnownStart(unittest.TestCase):
    def test_an_engineer_knows_its_laid_patches_from_the_start(self):
        lua = runtime()
        lua.execute("""
            prototypes = {entity = {['iron-ore'] = {mineable_properties = {products = {{type = 'item'}}}}}}
            for x = -470, -430 do for y = -460, -420 do
                if (x + 450)^2 + (y + 440)^2 <= 400 then
                    ores[#ores + 1] = {name = 'iron-ore', position = {x = x + 0.5, y = y + 0.5}, amount = 9000} end
            end end
            storage.team.added = {{plot = 'npc-1', what = 'iron-ore', position = {x = -450, y = -440}, tiles = 1257},
                                  {plot = 'npc-2', what = 'coal', position = {x = 0, y = -440}, tiles = 1257},
                                  {plot = 'npc-1', what = 'water', position = {x = -400, y = -360}, tiles = 149}}
            engineer(1)
            npc = storage.npcs[1]
            npc.home = {x = -384, y = -384}
            npc.character = {valid = true, surface = surface, position = {x = -384, y = -384}}
            S = require('scripts.scouting')
        """)
        patches = lua.eval("S.patches(npc, {{-416, -416}, {-352, -352}})")
        found = [patches[i] for i in range(1, len(patches) + 1)]
        self.assertTrue(found)
        self.assertTrue(all(p.resource == "iron-ore" for p in found))
        self.assertEqual(lua.eval("npc.ground_learned"), 0)


class SoftStartThenRamp(unittest.TestCase):
    def setUp(self):
        self.lua = lua = runtime()
        lua.execute("""
            a = engineer(1, 10)
            building(-400, -400, a, 'stone-furnace', true)
            nest(-700, -700); nest(-720, -720)
        """)

    def run_until(self, tick):
        self.lua.execute(f"for t = 60, {tick}, 60 do game.tick = t; Waves.tick(t) end")

    def waves(self):
        return [self.lua.eval(f"storage.team.waves.log[{i}]") for i in range(1, self.lua.eval("#storage.team.waves.log") + 1)]

    def test_the_ramp_and_the_odds_aimed_for(self):
        ramp, aim = self.lua.eval("Waves.ramp"), self.lua.eval("Waves.aim")
        self.assertLess(ramp(0, 0.01, 0, 5, 1), 0.2)
        self.assertGreater(ramp(60, 0.3, 3000, 200, 1), 0.95)
        self.assertGreater(ramp(10, 0.05, 100, 20, 2), ramp(10, 0.05, 100, 20, 1))   # (the knob ramps it faster)
        self.assertAlmostEqual(aim(0), 4)
        self.assertAlmostEqual(aim(1), 0.7)
        self.assertGreater(aim(0.3), aim(0.6))

    def test_a_grace_then_a_first_wave_its_pistol_can_beat(self):
        self.run_until(8 * 3600)
        self.assertEqual(self.lua.eval("#groups"), 0)
        self.run_until(10 * 3600)
        first = self.waves()[0]
        self.assertLessEqual(first.size, 4)
        self.assertIn("1 shooters", first.why)
        self.assertIn("sized to beat", self.lua.eval("logged[1]"))

    def test_turrets_up_bring_bigger_waves_and_the_ramp_presses_them(self):
        self.run_until(10 * 3600)
        bare = self.waves()[0].size
        self.lua.execute("for k = 1, 4 do turret(-420 + 10 * k, -420, a, 20) end")
        self.run_until(30 * 3600)
        later = self.waves()[-1]
        self.assertIn("5 shooters", later.why)
        self.assertGreater(later.size, 3 * bare)
        # and fully ramped (time, evolution, pollution) the wave is sized past what they can hold
        self.lua.execute("evolution = 0.4; pollution = 50")
        self.run_until(70 * 3600)
        last = self.waves()[-1]
        self.assertGreater(last.threat, later.threat)
        self.assertRegex(last.why, r"odds 0\.[7-9]")

    def test_turrets_far_from_where_the_wave_strikes_do_not_count(self):
        self.lua.execute("for k = 1, 4 do turret(-560 + 10 * k, -200, a, 20) end")   # (the plot's far side)
        self.run_until(10 * 3600)
        self.assertIn("its defense 1 shooters", self.waves()[0].why)

    def test_a_long_wait_for_nests_does_not_ripen_the_first_wave(self):
        self.lua.execute("saved = nests; nests = {}")
        self.run_until(60 * 3600)
        self.assertEqual(self.lua.eval("#groups"), 0)
        self.lua.execute("nests = saved")
        self.run_until(61 * 3600)
        first = self.waves()[0]
        self.assertIn("0 min", first.why)
        self.assertLessEqual(first.size, 4)

    def test_a_softer_knob_waits_longer_and_sends_fewer(self):
        self.lua.execute("strength = 0.5")
        self.run_until(17 * 3600)
        self.assertEqual(self.lua.eval("#groups"), 0)   # (grace 18 minutes)
        self.run_until(19 * 3600)
        self.assertEqual(self.lua.eval("#groups"), 1)


if __name__ == "__main__":
    unittest.main()
