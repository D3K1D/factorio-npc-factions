"""The armory (armory.lua): military prototypes sorted by their data, in Factorio's Lua 5.2 with the game stubbed."""
from pathlib import Path
import unittest

from lupa.lua52 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]


def runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)
    for name in ("recipes", "armory"):
        lua.globals()[name + "_source"] = (ROOT / "mod" / "scripts" / (name + ".lua")).read_text(encoding="utf-8")
    lua.execute("""
        package.preload['scripts.recipes'] = function() return assert(load(recipes_source))() end
        defines = {inventory={roboport_robot=1, roboport_material=2}}
        Armory = assert(load(armory_source))()
        local function hit(amount, kind)
            return {{type='direct', action_delivery={{type='instant',
                target_effects={{type='damage', damage={amount=amount, type=kind}}}}}}}
        end
        local function entity(type_, name, extra)
            local p = {type=type_, name=name, tile_width=2, tile_height=2, order='a',
                       items_to_place_this={{name=name, count=1}}}
            for k, v in pairs(extra or {}) do p[k] = v end
            return p
        end
        entities = {
            ['gun-turret']=entity('ammo-turret', 'gun-turret', {max_health=400,
                attack_parameters={range=18, cooldown=6, ammo_categories={'bullet'}},
                resistances={acid={decrease=3, percent=0.2}}}),
            ['laser-turret']=entity('electric-turret', 'laser-turret', {max_health=1000,
                attack_parameters={range=24, ammo_type={action=hit(20, 'laser')}}}),
            ['artillery-turret']=entity('artillery-turret', 'artillery-turret', {max_health=2000,
                guns={['artillery-wagon-cannon']={attack_parameters={range=224, min_range=32,
                    ammo_categories={'artillery-shell'}}}}}),
            ['stone-wall']=entity('wall', 'stone-wall', {max_health=450}),
            ['bob-reinforced-wall']=entity('wall', 'bob-reinforced-wall', {max_health=1250}),
            ['repair-turret']=entity('roboport', 'repair-turret', {max_health=500, construction_radius=32,
                get_inventory_size=function(i) return i == defines.inventory.roboport_robot and 0 or 1 end}),
            ['roboport']=entity('roboport', 'roboport', {max_health=500, construction_radius=55,
                get_inventory_size=function() return 7 end}),
            ['factory-roboport']=entity('roboport', 'factory-roboport', {hidden=true, construction_radius=64,
                get_inventory_size=function(i) return i == defines.inventory.roboport_robot and 0 or 1 end}),
            lab=entity('lab', 'lab', {lab_inputs={'automation-science-pack', 'military-science-pack'}}),
        }
        local function ammo(name, category, action, order)
            return {name=name, type='ammo', order=order, ammo_category={name=category}, magazine_size=10,
                    get_ammo_type=function() return {action=action} end}
        end
        items = {
            ['firearm-magazine']=ammo('firearm-magazine', 'bullet', hit(5, 'physical'), 'a'),
            ['piercing-rounds-magazine']=ammo('piercing-rounds-magazine', 'bullet',
                {hit(8, 'physical')[1], hit(8, 'bob-pierce')[1]}, 'b'),
            ['artillery-shell']=ammo('artillery-shell', 'artillery-shell', nil, 'a'),
            ['automation-science-pack']={name='automation-science-pack', type='tool', order='a'},
            ['military-science-pack']={name='military-science-pack', type='tool', order='e'},
        }
        recipes = {
            ['gun-turret']={products={{type='item', name='gun-turret', amount=1}}},
            ['piercing-rounds-magazine']={products={{type='item', name='piercing-rounds-magazine', amount=1}}},
        }
        prototypes = {entity=entities, item=items, recipe=recipes,
            technology={
                ['gun-turret']={effects={{type='unlock-recipe', recipe='gun-turret'}},
                    research_unit_ingredients={{name='automation-science-pack', amount=1}}, prerequisites={},
                    research_unit_count=10},
                ['military-2']={effects={{type='unlock-recipe', recipe='piercing-rounds-magazine'}},
                    research_unit_ingredients={}, prerequisites={['gun-turret']={}}},
                ['physical-projectile-damage-1']={effects={{type='ammo-damage', ammo_category='bullet', modifier=0.1}},
                    research_unit_ingredients={}, prerequisites={}},
                ['automation']={effects={{type='unlock-recipe', recipe='assembling-machine-1'}},
                    research_unit_ingredients={}, prerequisites={}}}}
        function prototypes.get_entity_filtered(filters)
            local out = {}
            for _, f in ipairs(filters) do
                for name, p in pairs(entities) do if p.type == f.type then out[name] = p end end
            end
            return out
        end
        function prototypes.get_item_filtered(filters)
            local out = {}
            for name, p in pairs(items) do if p.type == filters[1].type then out[name] = p end end
            return out
        end
        script = {active_mods={base='2.0.77', bobwarfare='2.1.0'}}
        force = {recipes={}, technologies={['gun-turret']={researched=true}}}
        function unlock(name)
            force.recipes[name] = {name=name, enabled=true, category='crafting', ingredients={},
                                   products={{type='item', name=name, amount=1}}}
        end
        c = Armory.catalog()
    """)
    return lua


class ArmoryTests(unittest.TestCase):
    def test_turrets_are_read_from_their_data_whatever_mod_adds_them(self):
        lua = runtime()
        gun = lua.eval("c.by_name['gun-turret']")
        self.assertEqual((gun.kind, gun.range, gun.health, gun.ammo_categories[1]), ("ammo", 18, 400, "bullet"))
        self.assertEqual((gun.resistances.acid.decrease, gun.resistances.acid.percent), (3, 20))
        laser = lua.eval("c.by_name['laser-turret']")
        self.assertEqual((laser.kind, laser.damage), ("electric", 20))
        artillery = lua.eval("c.by_name['artillery-turret']")     # (range and shells from its gun)
        self.assertEqual((artillery.range, artillery.min_range, artillery.ammo_categories[1]),
                         (224, 32, "artillery-shell"))

    def test_ammo_walls_and_repair_turrets_are_ranked_by_what_they_do(self):
        lua = runtime()
        self.assertEqual([lua.eval(f"c.ammo[{i}].name") for i in (1, 2)],
                         ["piercing-rounds-magazine", "firearm-magazine"])
        self.assertEqual(lua.eval("c.ammo[1].damage"), 16)
        self.assertEqual(lua.eval("c.walls[1].name"), "bob-reinforced-wall")
        self.assertEqual(lua.eval("#c.repair"), 1)                  # a roboport with robots is no repair turret
        self.assertEqual(lua.eval("c.repair[1].radius"), 32)
        self.assertEqual([lua.eval(f"c.packs[{i}].name") for i in (1, 2)],
                         ["automation-science-pack", "military-science-pack"])

    def test_military_techs_are_those_that_unlock_weapons_or_make_them_stronger(self):
        lua = runtime()
        names = [lua.eval(f"c.techs[{i}].name") for i in range(1, lua.eval("#c.techs") + 1)]
        self.assertEqual(names, ["gun-turret", "military-2", "physical-projectile-damage-1"])
        self.assertEqual(lua.eval("c.techs[1].unlocks[1]"), "gun-turret")
        self.assertEqual(lua.eval("c.techs[2].before[1]"), "gun-turret")
        self.assertEqual(lua.eval("c.techs[3].bonuses[1].of"), "bullet")

    def test_ammo_for_a_turret_is_what_fits_it_and_the_force_can_make_or_holds(self):
        lua = runtime()
        self.assertEqual(list(lua.eval("Armory.ammo_for('gun-turret')").values()),
                         ["piercing-rounds-magazine", "firearm-magazine"])
        lua.execute("unlock('firearm-magazine')")
        self.assertEqual(list(lua.eval("Armory.ammo_for('gun-turret', force)").values()), ["firearm-magazine"])
        held = lua.eval("Armory.ammo_for('gun-turret', force, function(i) return i == 'piercing-rounds-magazine' "
                        "and 5 or 0 end)")
        self.assertEqual(list(held.values()), ["piercing-rounds-magazine", "firearm-magazine"])

    def test_the_export_says_what_the_force_can_make_and_has_researched(self):
        lua = runtime()
        lua.execute("unlock('gun-turret'); out = Armory.export(force)")
        self.assertEqual(lua.eval("out.schema"), "npc-armory-v1")
        self.assertEqual(lua.eval("out.mods.bobwarfare"), "2.1.0")
        turrets = {lua.eval(f"out.turrets[{i}].name"): lua.eval(f"out.turrets[{i}].can_make")
                   for i in range(1, lua.eval("#out.turrets") + 1)}
        self.assertEqual(turrets, {"artillery-turret": False, "gun-turret": True, "laser-turret": False})
        self.assertTrue(lua.eval("out.techs[1].researched"))
        self.assertIsNone(lua.eval("out.by_name"))
        lua.execute("unlock('firearm-magazine')")
        self.assertEqual(list(lua.eval("Armory.makeable(force)").values()), ["gun-turret", "firearm-magazine"])


if __name__ == "__main__":
    unittest.main()
