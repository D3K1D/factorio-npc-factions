"""What biters destroyed goes back (DesignedStart.rebuild_job, in Factorio's Lua 5.2): nothing lost is dropped without a
word. Each loss waits for a reason it keeps and logs, one short of its item waits on that alone and is asked again soon,
what defends goes back while a defense goes up, and only a spot that refuses it is given up, and told. (Rehearsal 7b,
2026-10-27: of 28 buildings lost one came back, 19 minutes later; brief waits counted as failures dropped the rest.)"""
import unittest

import test_retire

SETUP = r"""
    local f = iron_start.entities['iron-smelting-start2']
    f.prototype, f.item, f.world, f.direction = 'stone-furnace', 'stone-furnace', {x=5, y=5}, 0
    local m = iron_start.entities['iron-smelting-start1']
    m.prototype, m.item, m.world, m.direction = 'burner-mining-drill', 'burner-mining-drill', {x=9, y=5}, 0
    held = {['stone-furnace']=0, ['burner-mining-drill']=1, ['gun-turret']=1}
    npc.character = {valid=true, position={x=0, y=0}, surface={count_entities_filtered=function() return 0 end,
                                                             find_entities_filtered=function() return {} end},
                     get_main_inventory=function()
                         return {get_item_count=function(spec) return held[type(spec) == 'table' and spec.name or spec] or 0 end}
                     end}
    npc.force = 'npc-force'
    furnace.force, drill.force = 'npc-force', 'npc-force'
    told = {}
    require('scripts.agent').event = function(_, e) told[#told + 1] = e end
    function lose(e, tick) DS.on_entity_died({entity=e, tick=tick}); e.valid = false end
    function job(tick) local r = {DS.rebuild_job(npc, tick, defending)} return r[1], r[2] end
"""


def runtime():
    lua = test_retire.runtime()
    lua.execute(SETUP)
    return lua


class RebuildWaitTests(unittest.TestCase):
    def test_a_loss_short_of_its_item_waits_saying_why_and_is_never_dropped(self):
        lua = runtime()
        lua.execute("lose(furnace, 1000)")
        lua.execute("for t = 3000, 3000 + 60 * 600, 60 do job(t) end")      # ten minutes of tries, none to be had
        self.assertEqual(lua.eval("#npc.rebuild"), 1)                          # still wanted
        self.assertTrue(lua.eval("npc.rebuild[1].wait").startswith("materials:"))
        self.assertTrue(any(e.kind == "rebuild_waiting" for e in lua.eval("told").values()))   # told, once
        self.assertEqual(sum(1 for e in lua.eval("told").values() if e.kind == "rebuild_waiting"), 1)
        lua.execute("held['stone-furnace'] = 1")
        lua.execute("t = 3000 + 60 * 600; repeat t = t + 60; j = job(t) until j or t > 3000 + 60 * 700")
        self.assertEqual((lua.eval("j.kind"), lua.eval("j.entity_name")), ("build", "stone-furnace"))
        self.assertLess(lua.eval("t") - (3000 + 60 * 600), 60 * 20)            # asked again within seconds

    def test_one_kind_it_cannot_get_holds_up_no_other(self):
        lua = runtime()
        lua.execute("lose(furnace, 1000); lose(drill, 1000)")
        out = lua.eval("(job(3000))")
        self.assertEqual((out.kind, out.entity_name), ("build", "burner-mining-drill"))

    def test_while_a_defense_goes_up_what_defends_goes_back_and_the_rest_waits(self):
        lua = runtime()
        lua.execute("""
            held['stone-furnace'] = 1
            gun = thing('ammo-turret', {force='npc-force'})
            prototypes.entity['gun-turret'] = {type='ammo-turret', tile_width=2, tile_height=2}
            iron_start.order[#iron_start.order + 1] = 'gun'
            iron_start.entities.gun = {id='gun', entity=gun, prototype='gun-turret', item='gun-turret', world={x=20, y=5}}
            npc.unit_index = nil
            lose(furnace, 1000); lose(gun, 1000)
            defending = true""")
        out = lua.eval("(job(3000))")
        self.assertEqual(out.entity_name, "gun-turret")
        lua.execute("job(3001)")
        waits = {lua.eval(f"npc.rebuild[{i}].wait") for i in range(1, lua.eval("#npc.rebuild") + 1)}
        self.assertIn("defense_going_up", waits)

    def test_a_spot_that_refuses_it_is_given_up_and_told(self):
        lua = runtime()
        lua.execute("""
            held['stone-furnace'] = 1
            lose(furnace, 1000)
            npc.design = npc.design or {}
            for k = 1, DS.REBUILD_TRIES do
                npc.design_jobs = {j = {upkeep='rebuild', plan=iron_start, entity_id='iron-smelting-start2'}}
                DS.on_receipt(npc, {job_id='j', status='failed', reason='placement_blocked_or_out_of_reach'}, 3000 + k)
            end
            job(4000)""")
        self.assertEqual(lua.eval("#npc.rebuild"), 0)
        self.assertEqual(lua.eval("npc.rebuild_given_up[1].name"), "stone-furnace")
        self.assertTrue(any(e.kind == "rebuild_given_up" for e in lua.eval("told").values()))


    def test_biters_staying_by_the_spot_keep_one_reason_and_it_is_told(self):
        lua = runtime()
        lua.execute("""
            held['stone-furnace'] = 1
            lose(furnace, 1000)
            npc.character.surface.count_entities_filtered = function() return 1 end
            for t = 3000, 3000 + DS.REBUILD_TELL + 600, 60 do job(t) end""")
        self.assertEqual(lua.eval("npc.rebuild[1].wait"), "biters_near")
        self.assertTrue(any(e.kind == "rebuild_waiting" and e.why == "biters_near" for e in lua.eval("told").values()))

    def test_a_wait_is_told_again_while_no_strategist_takes_it(self):
        lua = runtime()
        lua.execute("""
            require('scripts.agent').event = function() return false end
            lose(furnace, 1000)
            for t = 3000, 3000 + DS.REBUILD_TELL + 600, 60 do job(t) end""")
        self.assertFalse(lua.eval("npc.rebuild[1].told"))

    def test_only_the_spots_failures_spend_its_tries(self):
        lua = runtime()
        lua.execute("""
            held['stone-furnace'] = 1
            lose(furnace, 1000)
            npc.design = npc.design or {}
            for k = 1, 10 do
                npc.design_jobs = {j = {upkeep='rebuild', plan=iron_start, entity_id='iron-smelting-start2'}}
                DS.on_receipt(npc, {job_id='j', status='failed', reason='missing_healthy_normal_placement_item'}, 3000 + k)
            end""")
        self.assertEqual(lua.eval("#npc.rebuild"), 1)
        self.assertEqual(lua.eval("npc.rebuild[1].fails or 0"), 0)

    def test_a_loss_waits_for_a_defense_going_up_only_so_long(self):
        lua = runtime()
        lua.execute("""
            held['stone-furnace'] = 1
            lose(furnace, 1000)
            defending = true
            first = job(3000)
            later = job(3000 + DS.REBUILD_DEFENSE_HOLD)""")
        self.assertIsNone(lua.eval("first"))
        self.assertEqual(lua.eval("later.entity_name"), "stone-furnace")


    def test_a_spot_a_later_plan_built_on_is_reported_not_taken_down(self):
        lua = runtime()
        lua.execute("""
            held['stone-furnace'] = 1
            prototypes.entity['stone-furnace'] = {type='furnace', tile_width=2, tile_height=2}
            lose(furnace, 1000)
            assembler.name = 'assembling-machine-1'
            npc.character.surface.find_entities_filtered = function(f) return f.area and {assembler} or {} end
            out = job(3000)""")
        self.assertIsNone(lua.eval("out"))
        self.assertIn("later plan", lua.eval("npc.rebuild_given_up[1].why"))
        self.assertTrue(lua.eval("assembler.valid"))


    def test_a_give_up_no_strategist_took_is_told_on_a_later_turn(self):
        lua = runtime()
        lua.execute("""
            accept = false
            require('scripts.agent').event = function(_, e) if accept then told[#told + 1] = e end return accept end
            held['stone-furnace'] = 1
            lose(furnace, 1000)
            npc.design = npc.design or {}
            for k = 1, DS.REBUILD_TRIES do
                npc.design_jobs = {j = {upkeep='rebuild', plan=iron_start, entity_id='iron-smelting-start2'}}
                DS.on_receipt(npc, {job_id='j', status='failed', reason='placement_collision'}, 3000 + k)
            end
            job(4000)
            accept = true
            job(4100)""")
        self.assertEqual(lua.eval("#npc.rebuild_untold"), 0)
        self.assertTrue(any(e.kind == "rebuild_given_up" for e in lua.eval("told").values()))

    def test_the_defense_hold_does_not_start_over_after_another_wait(self):
        lua = runtime()
        lua.execute("""
            lose(furnace, 1000)          -- (no furnace held: a materials wait between)
            defending = true
            job(3000)
            defending = true
            job(3000 + DS.REBUILD_DEFENSE_HOLD - 10)
            r = npc.rebuild[1]""")
        self.assertEqual(lua.eval("r.defense_since"), 3000)

    def test_a_building_put_down_after_the_index_was_made_is_queued_when_lost(self):
        lua = runtime()
        lua.execute("""
            lose(furnace, 1000)                         -- (the index is made now)
            fresh = thing('mining-drill', {force='npc-force'})
            npc.design = {status='building', plan=iron_start}
            npc.design_jobs = {b = {build=true, entity_id='iron-smelting-start1'}}
            iron_start.entities['iron-smelting-start1'].failures = 0
            DS.on_receipt(npc, {job_id='b', status='complete', entity=fresh}, 1100)
            n = #npc.rebuild
            lose(fresh, 1200)""")
        self.assertEqual(lua.eval("#npc.rebuild"), lua.eval("n") + 1)


    def test_the_defense_hold_runs_once_across_one_defense_plan_after_another(self):
        # (rehearsal 7d, 2026-10-28: burner drills waited 714 and 824 s through back-to-back defense plans)
        lua = runtime()
        lua.execute("""
            held['stone-furnace'] = 1
            lose(furnace, 1000)
            defending = true; a = job(3000)
            defending = false; b = job(3100)                 -- (between two defense plans)
            defending = true; c = job(3000 + DS.REBUILD_DEFENSE_HOLD)""")
        self.assertIsNone(lua.eval("a"))
        self.assertEqual(lua.eval("c.entity_name"), "stone-furnace")         # not held a second time

    def test_a_calm_loss_comes_back_ahead_of_its_plans_and_agenda(self):
        lua = runtime()
        lua.execute("""
            held['stone-furnace'] = 1
            lose(furnace, 1000)
            out = DS.rebuild_now(npc, 3000)""")
        self.assertEqual((lua.eval("out.kind"), lua.eval("out.entity_name")), ("build", "stone-furnace"))
        lua.execute("npc.rebuild = {}")
        self.assertIsNone(lua.eval("DS.rebuild_now(npc, 3001)"))


    def test_a_spot_a_nest_or_worm_covers_waits_as_a_plans_spot_does(self):
        lua = runtime()
        lua.execute("""
            held['stone-furnace'] = 1
            lose(furnace, 1000)
            DS.fought_over = function() return true end
            out = DS.rebuild_now(npc, 3000)""")
        self.assertIsNone(lua.eval("out"))
        self.assertEqual(lua.eval("npc.rebuild[1].wait"), "biters_near")

    def test_the_first_plans_losses_come_back_too(self):
        lua = runtime()
        lua.execute("""
            held['stone-furnace'] = 1
            npc.design = {status='building', plan=iron_start}
            npc.design_history = nil
            npc.unit_index = nil
            lose(furnace, 1000)
            out = DS.rebuild_now(npc, 3000)""")
        self.assertEqual(lua.eval("#npc.rebuild"), 1)
        self.assertEqual(lua.eval("out.entity_name"), "stone-furnace")


    def test_away_on_a_trip_only_a_loss_in_reach_with_its_item_in_hand_goes_back(self):
        lua = runtime()
        lua.execute("""
            npc.character.reach_distance = 160
            lose(furnace, 1000)
            far = {DS.rebuild_now(npc, 3000, {near=true})}   -- (none held)""")
        self.assertIsNone(lua.eval("far[1]"))
        self.assertEqual(lua.eval("npc.rebuild[1].wait"), "urgent_trip")
        lua.execute("held['stone-furnace'] = 1; near = {DS.rebuild_now(npc, 3001, {near=true})}")
        self.assertEqual(lua.eval("near[1].entity_name"), "stone-furnace")   # (5 tiles off, in reach)
        lua.execute("npc.character.reach_distance = 3; out = {DS.rebuild_now(npc, 3002, {near=true})}")
        self.assertIsNone(lua.eval("out[1]"))                                # (out of reach: waits for the trip)


    def test_away_with_one_in_hand_for_two_losses_it_builds_one_and_fetches_nothing(self):
        lua = runtime()
        lua.execute("""
            npc.character.reach_distance, npc.character.build_distance = 160, 160
            held['burner-mining-drill'] = 1
            iron_start.entities['iron-smelting-start2'].prototype = 'burner-mining-drill'
            iron_start.entities['iron-smelting-start2'].item = 'burner-mining-drill'
            lose(furnace, 1000); lose(drill, 1000)
            out = {DS.rebuild_now(npc, 3000, {near=true})}""")
        self.assertEqual((lua.eval("out[1].kind"), lua.eval("out[1].entity_name")), ("build", "burner-mining-drill"))

    def test_a_defense_asked_for_holds_the_rest_as_one_going_up_does(self):
        lua = runtime()
        lua.execute("""
            held['stone-furnace'] = 1
            lose(furnace, 1000)
            npc.design = {status='waiting', purpose='basic-defense'}
            out = {DS.rebuild_now(npc, 3000)}""")
        self.assertIsNone(lua.eval("out[1]"))
        self.assertEqual(lua.eval("npc.rebuild[1].wait"), "defense_going_up")


    def test_a_plan_set_aside_for_an_urgent_one_keeps_its_losses(self):
        # (a defense ordered now pauses the plan being built: its losses are not "its plan is gone")
        lua = runtime()
        lua.execute("""
            held['stone-furnace'] = 1
            npc.design_history = nil
            npc.paused_design = {status='building', plan=iron_start}
            npc.design = {status='waiting', purpose='steel-smelting'}
            npc.unit_index = nil
            lose(furnace, 1000)
            out = {DS.rebuild_now(npc, 3000)}""")
        self.assertEqual(lua.eval("#npc.rebuild"), 1)
        self.assertEqual(lua.eval("out[1].entity_name"), "stone-furnace")
        self.assertIsNone(lua.eval("npc.rebuild_given_up"))


if __name__ == "__main__":
    unittest.main()
