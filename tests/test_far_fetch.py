"""A source is worth its walk only against getting it otherwise (W-20, in Factorio's Lua 5.2): rehearsal 7g's engineer
walked 420 tiles to a teammate's chest for five stone, with rocks by the losses it wanted them for, and its three
rebuilds stood five minutes; and a defense whose pieces all stood held them all the while."""
import unittest

import test_acquire_plan
import test_rebuild_wait
import test_sharing_supply


class TeamChestTests(unittest.TestCase):
    def test_a_teammates_chest_is_passed_over_when_the_trip_is_not_worth_it(self):
        lua = test_sharing_supply.runtime()
        lua.execute("bob.storage_chests = {chest(bob_force, 100, 0, {stone=100})}")
        self.assertIsNone(lua.eval("(S.take(ada, 'stone', 5, 100, false, function() return false end))"))
        lua.execute("game.tick = 100000")                                     # (past the miss memo)
        job = lua.eval("(S.take(ada, 'stone', 5, 200000, false, function(c, n) return n > 0 end))")
        self.assertEqual((job.kind, job.item), ("transfer", "stone"))

    def test_the_nearest_not_worth_it_gives_way_to_the_next_and_none_is_not_remembered_as_none(self):
        lua = test_sharing_supply.runtime()
        lua.execute("""
            near1 = chest(bob_force, 10, 0, {stone=1})
            near5 = chest(bob_force, 12, 0, {stone=50})
            bob.storage_chests = {near5, near1}
            worth = function(c, n) return n >= 5 end""")
        self.assertTrue(lua.eval("(S.take(ada, 'stone', 5, 100, false, worth)).target == near5"))
        lua.execute("worth = function() return false end")
        self.assertIsNone(lua.eval("(S.take(ada, 'stone', 5, 200, false, worth))"))
        self.assertIsNotNone(lua.eval("(S.take(ada, 'stone', 5, 201, false, nil))"))   # (asked again at once)

    def test_what_the_team_could_hand_over_is_read_without_a_job_or_a_word(self):
        # (R-7: a research witness is not dropped while its packs can be fetched so; nothing taken, said or asked)
        lua = test_sharing_supply.runtime()
        self.assertFalse(lua.eval("S.has(ada, 'stone', 100)"))
        lua.execute("bob.storage_chests = {chest(bob_force, 100, 0, {stone=100})}")
        self.assertTrue(lua.eval("S.has(ada, 'stone', 100000)"))              # (the chest list read anew)
        lua.execute("bob.storage_chests = {}; player_chests = {chest(player_force, 40, 0, {stone=300}, andre)}")
        self.assertTrue(lua.eval("S.has(ada, 'stone', 200000)"))              # (a player's, its player could say yes)
        self.assertEqual(lua.eval("#said"), 0)

    def test_a_players_chest_not_worth_the_walk_is_not_even_asked_about(self):
        lua = test_sharing_supply.runtime()
        lua.execute("player_chests = {chest(player_force, 40, 0, {stone=300}, andre)}")
        self.assertIsNone(lua.eval("(S.take(ada, 'stone', 5, 100, true, function() return false end))"))
        self.assertEqual(lua.eval("#said"), 0)


ROCKS = """
    prototypes.item.stone = prototypes.item.stone or {name='stone'}
    rock = {valid=true, name='big-rock', position={x=6, y=0}, minable=true,
            prototype={mineable_properties={minable=true, mining_time=2, products={{type='item', name='stone', amount=20}}}}}
    DS.Obstacles.natural_names = function(item) return item == 'stone' and {'big-rock'} or {} end
    rocks_near = true
    npc.character.surface.find_entities_filtered = function(f)
        if f.name and rocks_near then return {rock} end
        return {}
    end
    DS.drills_make = function() return 0 end
    far_chest = {valid=true, type='container', unit_number=9, position={x=420, y=0}}
    team = require('scripts.team_supply')
    team.take = function(npc, item, missing, tick, ask, worth)
        if worth and not worth(far_chest, 79) then return nil end
        return {kind='transfer', target=far_chest, item=item, count=missing, mode='withdraw'}, nil, 'team_chest'
    end
"""


class AcquireTests(unittest.TestCase):
    def test_stone_is_mined_from_a_rock_near_not_fetched_from_a_chest_far_off(self):
        lua = test_acquire_plan.runtime()
        lua.execute(ROCKS + "held.stone = 0; job = want('stone', 5)")
        self.assertEqual(lua.eval("job.kind"), "mine")
        self.assertLess(lua.eval("DS.hand_plan(npc, 'stone', 5).seconds"), 5)

    def test_too_few_rocks_near_for_what_it_needs_is_no_plan(self):
        lua = test_acquire_plan.runtime()
        lua.execute(ROCKS)
        self.assertIsNotNone(lua.eval("DS.hand_plan(npc, 'stone', 20)"))
        self.assertIsNone(lua.eval("DS.hand_plan(npc, 'stone', 50)"))      # (one rock of 20: not mined three times)
        lua.execute("rock.prototype.mineable_properties.products[1].independent_probability = 0.5")
        self.assertIsNone(lua.eval("DS.hand_plan(npc, 'stone', 20)"))      # (ten to be expected from it)
        lua.execute("rock.prototype.mineable_properties.products[1].independent_probability = nil; rock.minable = false")
        self.assertIsNone(lua.eval("DS.hand_plan(npc, 'stone', 5)"))       # (not to be mined now)

    def test_a_patch_tile_gives_only_what_is_left_in_it_and_the_walk_counts(self):
        lua = test_acquire_plan.runtime()
        lua.execute(ROCKS + """
            rocks_near = false
            tile = {valid=true, minable=true, amount=1, position={x=40, y=0},
                    prototype={mineable_properties={minable=true, mining_time=1, products={{type='item', name='stone', amount=1}}}}}
            npc.character.surface.find_entities_filtered = function(f) return f.type == 'resource' and {tile} or {} end
            DS.resource_names = function(item) return item == 'stone' and {'stone'} or {} end""")
        self.assertIsNone(lua.eval("DS.hand_plan(npc, 'stone', 30)"))      # (one unit left)
        lua.execute("tile.amount = 1000")
        self.assertGreater(lua.eval("DS.hand_plan(npc, 'stone', 1).seconds"), 3)   # (37 tiles walked first)

    def test_the_route_goes_to_the_nearest_next_not_back_and_forth(self):
        lua = test_acquire_plan.runtime()
        lua.execute(ROCKS + """
            local list = {}
            for i, x in ipairs({10, -10, 12, -12}) do
                list[i] = {valid=true, minable=true, name='big-rock', position={x=x, y=0},
                    prototype={mineable_properties={minable=true, mining_time=0, products={{type='item', name='stone', amount=1}}}}}
            end
            npc.character.surface.find_entities_filtered = function(f) return f.name and list or {} end""")
        # (10, 12, then -10, -12: 7.3 + 0 + 19.3 + 0 tiles beyond reach, not 7.3 + 17.3 + 19.3 + 21.3)
        self.assertLess(lua.eval("DS.hand_plan(npc, 'stone', 4).seconds"), 30 / 9)

    def test_with_nothing_near_to_mine_the_far_chest_is_worth_it(self):
        lua = test_acquire_plan.runtime()
        lua.execute(ROCKS + "rocks_near = false; held.stone = 0; npc.fetch_memo = nil; job = want('stone', 5)")
        self.assertEqual((lua.eval("job.kind"), lua.eval("job.target == far_chest")), ("transfer", True))

    def test_a_ready_made_piece_far_off_is_not_fetched_when_its_hands_make_it_sooner(self):
        lua = test_acquire_plan.runtime()
        lua.execute(ROCKS + """
            held['iron-plate'] = 40
            plan = DS.make_plan(npc, 'iron-gear-wheel', 5)
            got = {}
            team.take = function(npc, item, missing, tick, ask, worth)
                got[#got + 1] = item
                if worth and not worth(far_chest, 79) then return nil end
                return {kind='transfer', target=far_chest, item=item, count=missing, mode='withdraw'}
            end
            job = want('iron-gear-wheel', 5)""")
        self.assertIn("iron-gear-wheel", list(lua.eval("got").values()))      # (asked, and weighed)
        self.assertFalse(lua.eval("job and job.target == far_chest or false"))


class DefenseHoldTests(unittest.TestCase):
    def test_a_defense_whose_pieces_all_stand_holds_no_rebuild(self):
        lua = test_rebuild_wait.runtime()
        lua.execute("""
            held['stone-furnace'] = 1
            lose(furnace, 1000)
            turret = {valid=true}
            npc.design = {status='building', purpose='basic-defense',
                          plan={order={'t'}, entities={t={id='t', entity=turret}}}}
            up = {DS.rebuild_now(npc, 3000)}
            turret.valid = false
            npc.rebuild[1].defense_since = nil
            down = {DS.rebuild_now(npc, 3100)}""")
        self.assertEqual(lua.eval("up[1] and up[1].entity_name"), "stone-furnace")
        self.assertIsNone(lua.eval("down[1]"))                                  # (a piece to go up: held)
        self.assertEqual(lua.eval("npc.rebuild[1].wait"), "defense_going_up")


    def test_upkeep_holds_no_rebuild_for_a_defense_that_stands(self):
        lua = test_rebuild_wait.runtime()
        lua.execute("""
            held['stone-furnace'] = 1
            lose(furnace, 1000)
            npc.design = {status='building', purpose='basic-defense',
                          plan={order={'t'}, entities={t={id='t', entity={valid=true}}}}}
            DS.defense_supply = function() return nil end
            DS.corpse_job = function() return nil end
            got = {DS.upkeep(npc, 3000)}""")
        self.assertEqual(lua.eval("got[1] and got[1].entity_name"), "stone-furnace")   # (built, not held)


if __name__ == "__main__":
    unittest.main()
