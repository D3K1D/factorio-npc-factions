"""A loss goes back while some plan of its own still wants that piece there (DesignedStart.rebuild_owner, rebuild_job,
block_plan, in Factorio's Lua 5.2; W-24). death-world-waves-power: a reinforce plan handed back with its turrets all
fallen was dropped from its history, and the game "gives up rebuilding gun-turret (its plan is gone)"."""
import unittest

import test_plan_hold
import test_rebuild_wait

GONE = """
    held['stone-furnace'] = 1
    npc.unit_index = nil
    lose(furnace, 1000)
    other = {plan_id='other', order={'f2'}, stages={}, entities={f2={id='f2', prototype=WANT or 'stone-furnace',
             item=WANT or 'stone-furnace', world={x=5, y=5}, direction=0}}}
    npc.design_history = {other}                      -- (its own plan gone: merged into, or replaced by, the other)
    out = {DS.rebuild_now(npc, 3000)}
"""


class OwnerTests(unittest.TestCase):
    def test_the_plan_that_still_wants_it_there_takes_it(self):
        lua = test_rebuild_wait.runtime()
        lua.execute(GONE)
        self.assertEqual(lua.eval("#npc.rebuild"), 1)
        self.assertTrue(lua.eval("npc.rebuild[1].plan == other and npc.rebuild[1].id == 'f2'"))
        self.assertEqual(lua.eval("out[1].entity_name"), "stone-furnace")
        self.assertIsNone(lua.eval("npc.rebuild_given_up"))

    def test_taken_over_then_left_out_by_that_plan_it_is_given_up(self):
        for mark in ("skipped={rung='x', tick=1}", "left_out=true"):
            lua = test_rebuild_wait.runtime()
            lua.execute(GONE)
            self.assertTrue(lua.eval("npc.rebuild[1].moved"))
            lua.execute(f"other.entities.f2.{mark.split('=')[0]} = {mark.split('=', 1)[1]}; "
                        "npc.rebuild[1].fails = 0; out = {DS.rebuild_now(npc, 3100)}")
            self.assertEqual(lua.eval("#npc.rebuild"), 0, mark)
            self.assertEqual(lua.eval("npc.rebuild_given_up[1].why"), "no plan of its own wants it there any more")

    def test_left_out_by_one_plan_another_that_wants_it_takes_it(self):
        # (Codex, W-24: never given up while some kept plan still wants it there)
        lua = test_rebuild_wait.runtime()
        lua.execute(GONE.replace("npc.design_history = {other}", """
            older = {plan_id='older', order={'f3'}, stages={}, entities={f3={id='f3', prototype='stone-furnace',
                     item='stone-furnace', world={x=5, y=5}, direction=0}}}
            npc.design_history = {older, other}"""))
        self.assertTrue(lua.eval("npc.rebuild[1].plan == other"))                 # (the newest first)
        lua.execute("other.entities.f2.left_out = true; out = {DS.rebuild_now(npc, 3100)}")
        self.assertEqual(lua.eval("#npc.rebuild"), 1)
        self.assertTrue(lua.eval("npc.rebuild[1].plan == older and npc.rebuild[1].id == 'f3'"))
        self.assertIsNone(lua.eval("npc.rebuild_given_up"))

    def test_a_spot_nothing_wants_any_more_is_given_up_and_logged(self):
        lua = test_rebuild_wait.runtime()
        lua.execute("WANT = 'burner-mining-drill'" + GONE)   # (that ground now wants another building)
        self.assertEqual(lua.eval("#npc.rebuild"), 0)
        self.assertEqual(lua.eval("npc.rebuild_given_up[1].why"), "no plan of its own wants it there any more")
        lua = test_rebuild_wait.runtime()
        lua.execute(GONE.replace("npc.design_history = {other}", "npc.design_history = {}"))
        self.assertEqual(lua.eval("npc.rebuild_given_up[1].name"), "stone-furnace")

    def test_a_piece_that_plan_stands_or_waits_for_already_is_not_queued_twice(self):
        lua = test_rebuild_wait.runtime()
        lua.execute("""
            stood = {valid=true}
            WANT = nil""" + GONE.replace("direction=0}}}", "direction=0, entity=stood}}}"))
        self.assertEqual(lua.eval("#npc.rebuild"), 0)                              # (it stands there: nothing to do)
        self.assertIsNone(lua.eval("npc.rebuild_given_up"))                        # (and nothing given up)
        lua = test_rebuild_wait.runtime()
        lua.execute(GONE.replace("out = {DS.rebuild_now(npc, 3000)}",
                                 "npc.rebuild[2] = {plan=other, id='f2', tick=1000, lost=1000}\n"
                                 "out = {DS.rebuild_now(npc, 3000)}"))
        self.assertEqual(lua.eval("#npc.rebuild"), 1)
        self.assertIsNone(lua.eval("npc.rebuild_given_up"))


class HandBackTests(unittest.TestCase):
    def test_a_plan_handed_back_with_its_turrets_fallen_is_kept_for_them(self):
        lua = test_plan_hold.game()
        lua.execute("""
            prototypes.entity['gun-turret'] = {name='gun-turret', type='ammo-turret'}
            d.plan.stages = {{id='s', status='building', entity_ids={'t'}}}
            d.plan.order = {'t'}
            d.plan.entities = {t={id='t', prototype='gun-turret', item='gun-turret', world={x=3, y=3}}}
            npc.rebuild = {{plan=d.plan, id='t', tick=100, lost=100}}
            DS.hand_add(d, 'gun-turret', 700)""")
        self.assertTrue(lua.eval("DS.hand_back(npc, d, 700)"))
        self.assertTrue(lua.eval("npc.design_history[#npc.design_history] == d.plan"))
        self.assertTrue(lua.eval("d.plan.partial"))

    def test_a_blocked_plan_s_lost_furnace_does_not_keep_it_but_a_lost_turret_does(self):
        # (Codex, W-24: a block salvages what stands; only what defends, or a hand-back, keeps a plan for its losses)
        for proto, kind, kept in (("stone-furnace", "furnace", False), ("gun-turret", "ammo-turret", True)):
            lua = test_plan_hold.game()
            lua.execute(f"""
                prototypes.entity['{proto}'] = {{name='{proto}', type='{kind}'}}
                d.plan.stages = {{{{id='s', status='building', entity_ids={{'p'}}}}}}
                d.plan.order = {{'p'}}
                d.plan.entities = {{p={{id='p', prototype='{proto}', item='{proto}', world={{x=3, y=3}}}}}}
                npc.rebuild = {{{{plan=d.plan, id='p', tick=100, lost=100}}}}
                npc.design_history = {{{{plan_id='old', stages={{}}, entities={{}}, order={{}}}}}}
                for k = 1, DS.MAX_STALLED do DS.stall(npc, d, 'x', 700) end""")
            self.assertEqual(lua.eval("d.status"), "blocked")
            self.assertEqual(lua.eval("#npc.design_history"), 2 if kept else 1, proto)


class OwnerStateTests(unittest.TestCase):
    """Codex, W-24: never a piece not to be built, nor one ahead of its plan's stage."""

    def test_a_piece_skipped_there_takes_nothing(self):
        lua = test_rebuild_wait.runtime()
        lua.execute(GONE.replace("direction=0}}}", "direction=0, skipped={rung='x', tick=1}}}}"))
        self.assertEqual(lua.eval("#npc.rebuild"), 0)
        self.assertEqual(lua.eval("npc.rebuild_given_up[1].why"), "no plan of its own wants it there any more")

    def test_a_piece_of_a_stage_still_to_come_is_left_to_that_stage(self):
        lua = test_rebuild_wait.runtime()
        lua.execute(GONE.replace("npc.design_history = {other}",
                                 "other.entities.f2.stage = 'later'; other.stages = {{id='first'}, {id='later'}}; "
                                 "npc.design_history = {}; "
                                 "npc.design = {status='building', plan=other, stage_index=1}"))
        self.assertEqual(lua.eval("#npc.rebuild"), 1)                 # (kept, waiting: its stage builds it, ground and all)
        self.assertEqual(lua.eval("npc.rebuild[1].wait"), "stage_to_come")
        self.assertIsNone(lua.eval("npc.rebuild_given_up"))
        self.assertIsNone(lua.eval("out[1]"))
        # (and the stage it is in now, its ground maybe still to clear: the same; Codex, W-24)
        lua.execute("npc.design.stage_index = 2; out = {DS.rebuild_now(npc, 3100)}")
        self.assertEqual(lua.eval("npc.rebuild[1].wait"), "stage_to_come")
        # (the plan shelved before that stage came: it goes back as any loss, never lost without a word)
        lua.execute("""
            npc.design_history = {other}; npc.design = nil
            out = {DS.rebuild_now(npc, 3200)}""")
        self.assertEqual(lua.eval("#npc.rebuild"), 1)
        self.assertEqual(lua.eval("out[1].entity_name"), "stone-furnace")

if __name__ == "__main__":
    unittest.main()
