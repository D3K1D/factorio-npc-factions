"""The engineers' boosts (scripts/boosts.lua), in Factorio's Lua 5.2: hand mining speed, extra inventory slots and
health on every engineer's body (a new body's too), only what they set coming off when turned down, and a quick start
kit once at its first spawn. The user, 2026-10-29: "simple cheats, improve them immensely but not too crazy"."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def runtime():
    from lupa.lua52 import LuaRuntime
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().source = (ROOT / "mod" / "scripts" / "boosts.lua").read_text(encoding="utf-8")
    lua.execute("""
        package.preload['scripts.armory'] = function()
            return {catalog = function() return {guns = {{name = 'smg'}, {name = 'rifle'}}} end,
                    ammo_for = function(gun) return gun == 'pistol' and {'piercing-magazine', 'firearm-magazine'}
                        or {'rifle-rounds'} end}
        end
        defines = {inventory = {character_guns = 'guns', character_main = 'main'}}
        values = {['npc-factions-boost-mining-speed'] = 2, ['npc-factions-boost-inventory'] = 30,
                  ['npc-factions-boost-health'] = 2.5, ['npc-factions-quick-start'] = true,
                  ['npc-factions-quick-start-size'] = 1}
        settings = {global = setmetatable({}, {__index = function(_, k)
            if values[k] ~= nil then return {value = values[k]} end end})}
        items = {}
        for _, name in ipairs({'burner-mining-drill', 'stone-furnace', 'iron-plate', 'copper-plate', 'pistol',
                               'firearm-magazine', 'rifle', 'rifle-rounds', 'coal'}) do items[name] = {name = name} end
        items['burner-mining-drill'].place_result = {burner_prototype = {}}
        items['stone-furnace'].place_result = {burner_prototype = {}}
        items.pistol.type, items.rifle.type = 'gun', 'gun'
        prototypes = {item = items}
        storage = {npcs = {}}
        Boosts = assert(load(source, '@boosts'))()
        function body(n)
            local got, guns = {}, {}
            local c = {valid = true, unit_number = n, character_mining_speed_modifier = 0,
                character_inventory_slots_bonus = 0, character_health_bonus = 0,
                prototype = {get_max_health = function() return 250 end}, got = got}
            room = room or 1e9
            function c.insert(s) local n = math.min(s.count, room); room = room - n
                if n > 0 then got[s.name] = (got[s.name] or 0) + n end; return n end
            function c.get_item_count(name) return got[name] or 0 end
            spilled = {}
            c.surface = {spill_item_stack = function(t) spilled[t.stack.name] = t.stack.count; return {{}} end}
            c.position = {x = 0, y = 0}
            function c.get_inventory(which)
                local list = {}
                if which == 'main' then
                    for name, count in pairs(got) do
                        list[#list + 1] = {valid_for_read = true, name = name, count = count, prototype = items[name]}
                    end
                end
                return list
            end
            return c
        end
        npc = {id = 1, character = body(1), created_tick = 0}
        storage.npcs[1] = npc
    """)
    return lua


class BoostTests(unittest.TestCase):
    def test_its_body_gets_the_boosts(self):
        lua = runtime()
        lua.execute("Boosts.keep(npc, 60)")
        self.assertEqual(lua.eval("npc.character.character_mining_speed_modifier"), 1)
        self.assertEqual(lua.eval("npc.character.character_inventory_slots_bonus"), 30)
        self.assertEqual(lua.eval("npc.character.character_health_bonus"), 375)   # (250 more 1.5 times over)

    def test_a_new_body_gets_them_at_once_and_a_changed_setting_on_the_next_look(self):
        lua = runtime()
        lua.execute("Boosts.keep(npc, 60); npc.character = body(2); Boosts.keep(npc, 61)")   # (a respawn, a swap)
        self.assertEqual(lua.eval("npc.character.character_inventory_slots_bonus"), 30)
        lua.execute("values['npc-factions-boost-inventory'] = 10; Boosts.keep(npc, 62)")
        self.assertEqual(lua.eval("npc.character.character_inventory_slots_bonus"), 30)   # (not looked at yet)
        lua.execute("Boosts.changed(); Boosts.keep(npc, 63)")
        self.assertEqual(lua.eval("npc.character.character_inventory_slots_bonus"), 10)

    def test_another_mods_part_is_kept_while_ours_is_on(self):
        lua = runtime()
        lua.execute("""npc.character.character_inventory_slots_bonus = 10    -- (another mod's, before ours)
                       Boosts.keep(npc, 60)""")
        self.assertEqual(lua.eval("npc.character.character_inventory_slots_bonus"), 40)
        lua.execute("""npc.character.character_inventory_slots_bonus = 20    -- (that mod writes its field again)
                       Boosts.changed(); Boosts.keep(npc, 61)""")
        self.assertEqual(lua.eval("npc.character.character_inventory_slots_bonus"), 50)
        lua.execute("values['npc-factions-boost-inventory'] = 0; Boosts.changed(); Boosts.keep(npc, 62)")
        self.assertEqual(lua.eval("npc.character.character_inventory_slots_bonus"), 20)

    def test_turned_down_only_what_it_set_comes_off(self):
        lua = runtime()
        lua.execute("""Boosts.keep(npc, 60)
                       npc.character.character_health_bonus = 99      -- (another mod's since)
                       values['npc-factions-boost-health'] = 1; values['npc-factions-boost-mining-speed'] = 1
                       Boosts.changed(); Boosts.keep(npc, 61)""")
        self.assertEqual(lua.eval("npc.character.character_mining_speed_modifier"), 0)
        self.assertEqual(lua.eval("npc.character.character_health_bonus"), 99)

    def test_the_quick_start_once_at_its_first_spawn(self):
        lua = runtime()
        lua.execute("Boosts.keep(npc, 60)")
        got = dict(lua.eval("npc.character.got"))
        self.assertEqual(got, {"burner-mining-drill": 8, "stone-furnace": 8, "iron-plate": 50, "copper-plate": 20,
                               "pistol": 1, "firearm-magazine": 50, "coal": 160})   # (fuel for its 16 burners)

    def test_items_a_locked_techs_trigger_counts_are_left_for_it_to_make(self):
        lua = runtime()
        lua.execute("""prototypes.technology = {
                           ['steam-power'] = {research_trigger = {type = 'craft-item', item = 'iron-plate', count = 50}},
                           electronics = {research_trigger = {type = 'craft-item', item = {name = 'copper-plate'}}},
                           ['done-one'] = {research_trigger = {type = 'craft-item', item = 'stone-furnace'}}}
                       npc.character.force = {technologies = {['done-one'] = {researched = true}}}
                       Boosts.keep(npc, 60)""")
        got = dict(lua.eval("npc.character.got"))
        self.assertNotIn("iron-plate", got)
        self.assertNotIn("copper-plate", got)
        self.assertEqual(got["stone-furnace"], 8)                 # (its tech is open already)
        self.assertEqual(dict(lua.eval("npc.quick_start.left_for_triggers")),
                         {"iron-plate": "steam-power", "copper-plate": "electronics"})

    def test_what_it_carries_counts_toward_the_kit_and_what_does_not_fit_is_spilled(self):
        lua = runtime()
        lua.execute("""npc.character.got['iron-plate'] = 8; npc.character.got['burner-mining-drill'] = 1
                       room = 30
                       Boosts.keep(npc, 60)""")
        got = dict(lua.eval("npc.character.got"))
        self.assertEqual(got["burner-mining-drill"], 8)            # (7 more: its freeplay one counts)
        self.assertEqual(sum(got.values()) - 9, 30)               # (room for 30 more)
        self.assertTrue(dict(lua.eval("spilled")))                 # (the rest at its feet, never lost)
        self.assertTrue(dict(lua.eval("npc.quick_start.spilled")))

    def test_without_coal_its_burners_get_the_mods_best_chemical_fuel(self):
        lua = runtime()
        lua.execute("""items.coal = nil
                       items['wood'] = {name = 'wood', fuel_categories = {chemical = true}, fuel_value = 2e6}
                       items['solid-fuel'] = {name = 'solid-fuel', fuel_categories = {chemical = true}, fuel_value = 12e6}
                       items['uranium-fuel-cell'] = {name = 'uranium-fuel-cell', fuel_categories = {nuclear = true},
                           fuel_value = 8e9}
                       Boosts.keep(npc, 60)""")
        got = dict(lua.eval("npc.character.got"))
        self.assertEqual(got.get("solid-fuel"), 160)
        self.assertNotIn("uranium-fuel-cell", got)

    def test_engineers_already_in_a_game_get_no_kit_after_an_update(self):
        lua = runtime()
        lua.execute("Boosts.migrate(); Boosts.keep(npc, 60)")
        self.assertEqual(dict(lua.eval("npc.character.got")), {})
        lua.execute("npc.character = body(2); Boosts.keep(npc, 61)")   # (a respawn: none again)
        self.assertEqual(dict(lua.eval("npc.character.got")), {})

    def test_its_size_and_its_gun_when_it_has_one_already(self):
        lua = runtime()
        lua.execute("""values['npc-factions-quick-start-size'] = 2
                       npc.character.got.pistol = 1
                       Boosts.keep(npc, 60)""")
        got = dict(lua.eval("npc.character.got"))
        self.assertEqual(got["burner-mining-drill"], 16)
        self.assertEqual(got["firearm-magazine"], 100)
        self.assertEqual(got["pistol"], 1)

    def test_a_modded_game_without_the_pistol_gets_its_weakest_gun_and_rounds_that_fit(self):
        lua = runtime()
        lua.execute("items.pistol = nil; items['firearm-magazine'] = nil; Boosts.keep(npc, 60)")
        got = dict(lua.eval("npc.character.got"))
        self.assertEqual(got.get("rifle"), 1)
        self.assertEqual(got.get("rifle-rounds"), 50)

    def test_no_kit_when_off_for_an_old_engineer_or_in_a_fixture(self):
        lua = runtime()
        lua.execute("values['npc-factions-quick-start'] = false; Boosts.keep(npc, 60)")
        self.assertEqual(dict(lua.eval("npc.character.got")), {})
        lua.execute("values['npc-factions-quick-start'] = true; npc.created_tick = -99999; Boosts.changed(); Boosts.keep(npc, 61)")
        self.assertEqual(dict(lua.eval("npc.character.got")), {})
        self.assertEqual(lua.eval("npc.quick_start.skipped"), "not_new")
        lua2 = runtime()
        lua2.execute("storage.test = {}; Boosts.keep(npc, 60)")
        self.assertEqual(dict(lua2.eval("npc.character.got")), {})


if __name__ == "__main__":
    unittest.main()
