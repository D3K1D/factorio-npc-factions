"""Hand-craft only until its machine runs (lesson 15, the user's play 2026-10-30, in Factorio's Lua 5.2): the user made
62 turrets by hand until the turret machine ran, then none. An item its own machine makes now comes from that machine;
with none made yet it waits ("machine_making:<item>", logged), at most MACHINE_WAIT, and its hands make it when it is
attacked or on urgent work."""
import unittest

import test_acquire_plan

SETUP = r"""
    defines.entity_status = {working=1, no_power=2}
    held['iron-plate'] = 20
    out = 0
    machine = {valid=true, type='assembling-machine', status=1, position={x=6, y=0},
        get_recipe=function() return {products={{type='item', name='iron-gear-wheel', amount=1}}} end,
        get_output_inventory=function() return {get_item_count=function() return out end} end}
    npc.design = {status='building', plan={order={'m'}, entities={m={id='m', entity=machine}}, stages={}}}
    logged = {}; log = function(s) logged[#logged + 1] = s end
    game.tick = 1000
    -- (as a plan's stage asks: the acquire its strategist gets never waits on a machine)
    function want(item, n) local o = {DS.acquire_raw(npc, item, n or 5, 0)} return o[1], o[2] end
    function asked(item, n) local o = {DS.acquire_now(npc, item, n or 5, 0)} return o[1], o[2] end
"""


def runtime():
    lua = test_acquire_plan.runtime()
    lua.execute(SETUP)
    return lua


class MachineWaitTests(unittest.TestCase):
    def test_its_machine_at_work_it_waits_for_it_and_says_so(self):
        lua = runtime()
        self.assertEqual(lua.eval("select(2, want('iron-gear-wheel', 5))"), "machine_making:iron-gear-wheel")
        self.assertTrue(any("machine_making:iron-gear-wheel" in s for s in lua.eval("logged").values()))

    def test_what_its_machine_made_is_taken(self):
        lua = runtime()
        lua.execute("out = 3; j = want('iron-gear-wheel', 5)")
        self.assertEqual((lua.eval("j.kind"), lua.eval("j.target == machine"), lua.eval("j.count")),
                         ("transfer", True, 3))

    def test_a_machine_not_at_work_or_an_attack_or_a_long_wait_lets_its_hands_make_it(self):
        lua = runtime()
        lua.execute("machine.status = 2; npc.machine_making = nil; j = want('iron-gear-wheel', 5)")
        self.assertEqual(lua.eval("j.kind"), "craft")
        lua = runtime()
        lua.execute("npc.alarm = {last=1000}; j = want('iron-gear-wheel', 5)")
        self.assertEqual(lua.eval("j.kind"), "craft")
        lua = runtime()
        lua.execute("""want('iron-gear-wheel', 5)
                       game.tick = 1000 + DS.MACHINE_WAIT + 1; npc.machine_making = nil
                       j = want('iron-gear-wheel', 5)""")
        self.assertEqual(lua.eval("j.kind"), "craft")

    def test_an_output_its_inserters_drain_does_not_hold_its_hands_for_ever(self):
        lua = runtime()
        lua.execute("""want('iron-gear-wheel', 5)                                -- (none made: the clock starts)
                       out = 2; game.tick = 1500; npc.machine_making = nil
                       first = want('iron-gear-wheel', 5)                        -- (taken: the clock runs on)
                       out = 0; game.tick = 1000 + DS.MACHINE_WAIT + 1; npc.machine_making = nil
                       late = want('iron-gear-wheel', 5)   -- (drained again: no wait past its time)""")
        self.assertEqual(lua.eval("first.kind"), "transfer")
        self.assertEqual(lua.eval("late.kind"), "craft")                     # (its clock ran on through the takes)

    def test_its_strategist_and_kit_never_wait_on_a_machine(self):
        lua = runtime()
        lua.execute("j = asked('iron-gear-wheel', 5)")
        self.assertEqual(lua.eval("j.kind"), "craft")
        self.assertEqual(lua.eval("select(2, want('iron-gear-wheel', 5))"), "machine_making:iron-gear-wheel")


if __name__ == "__main__":
    unittest.main()
