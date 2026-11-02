"""What a force can defend with (Armory.defense), for the team world's defense planners."""
import unittest

import test_armory


class DefenseTests(unittest.TestCase):
    def test_only_what_it_can_make_best_first(self):
        lua = test_armory.runtime()
        lua.execute("""for _, n in ipairs({'gun-turret', 'stone-wall', 'firearm-magazine'}) do unlock(n) end
                       d = Armory.defense(force, 0.12)""")
        self.assertEqual([lua.eval(f"d.turrets[{i}].name") for i in range(1, lua.eval("#d.turrets") + 1)],
                         ["gun-turret"])
        gun = lua.eval("d.turrets[1]")
        self.assertEqual((gun.kind, gun.range, gun.w, gun.h, gun.ammo_categories[1]), ("ammo", 18, 2, 2, "bullet"))
        self.assertEqual(lua.eval("d.walls[1].name"), "stone-wall")
        self.assertEqual(lua.eval("#d.gates"), 0)
        self.assertEqual(list(lua.eval("d.ammo.bullet").values()), ["firearm-magazine"])
        self.assertEqual((lua.eval("#d.repair"), lua.eval("d.evolution")), (0, 0.12))
        lua.execute("""for _, n in ipairs({'laser-turret', 'piercing-rounds-magazine', 'repair-turret',
                                           'bob-reinforced-wall'}) do unlock(n) end
                       d = Armory.defense(force)""")
        self.assertEqual([lua.eval(f"d.turrets[{i}].name") for i in (1, 2)], ["laser-turret", "gun-turret"])
        self.assertEqual([lua.eval(f"d.walls[{i}].name") for i in (1, 2)], ["bob-reinforced-wall", "stone-wall"])
        self.assertEqual(list(lua.eval("d.ammo.bullet").values()), ["piercing-rounds-magazine", "firearm-magazine"])
        self.assertEqual(lua.eval("d.repair[1].radius"), 32)


if __name__ == "__main__":
    unittest.main()
