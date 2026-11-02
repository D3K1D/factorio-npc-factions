"""Wall stock in big batches (lesson 16, the user's play 2026-10-30, in Factorio's Lua 5.2): the user took 306-523 walls
a trip from the wall machine's chest and laid 489 in 3.1 min; a stage fetched 50 a trip (BUILD_BATCH), 8 trips for
400. Now what its stores hold ready-made comes in one trip, up to its pockets' room, a few slots kept free."""
import unittest

import test_acquire_plan

SETUP = r"""
    prototypes.item['stone-wall'] = {stack_size=100}
    stocked, room = 0, 0
    local inv = npc.character.get_main_inventory()
    inv.get_insertable_count = function() return room end
    npc.storage_chests = {{valid=true, type='container', position={x=4, y=0},
        get_inventory=function() return {get_item_count=function() return stocked end} end}}
"""


def runtime():
    lua = test_acquire_plan.runtime()
    lua.execute(SETUP)
    return lua


class StageBatchTests(unittest.TestCase):
    def test_what_its_store_holds_comes_in_one_trip_up_to_its_room(self):
        lua = runtime()
        lua.execute("stocked, room = 523, 3000")
        self.assertEqual(lua.eval("DS.stage_batch(npc, 'stone-wall', 400)"), 400)
        lua.execute("stocked, room = 523, 2000")       # (20 slots free, 4 kept: 1600)
        self.assertEqual(lua.eval("DS.stage_batch(npc, 'stone-wall', 2000)"), 523)
        lua.execute("stocked, room = 900, 700")        # (3 slots past the four kept)
        self.assertEqual(lua.eval("DS.stage_batch(npc, 'stone-wall', 2000)"), 300)

    def test_made_by_hand_or_little_in_store_it_stays_a_batch(self):
        lua = runtime()
        lua.execute("stocked, room = 30, 3000")
        self.assertEqual(lua.eval("DS.stage_batch(npc, 'stone-wall', 400)"), 50)
        self.assertEqual(lua.eval("DS.stage_batch(npc, 'stone-wall', 20)"), 20)
        lua.execute("stocked, room = 900, 100")        # (its pockets near full: never below a batch)
        self.assertEqual(lua.eval("DS.stage_batch(npc, 'stone-wall', 400)"), 50)


if __name__ == "__main__":
    unittest.main()
