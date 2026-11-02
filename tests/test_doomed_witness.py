"""A stage witness that can never pass is dropped after a while, said why, and its stage goes on
(DesignedStart.witness_cannot / witness_doomed, R-7), in Factorio's Lua 5.2. OP-8b: a research witness held its whole
plan forever, the only research open needing packs its lab could not get."""
import unittest

import test_clearing

SETUP = """
    RED, GREY = 'automation-science-pack', 'military-science-pack'
    tech = function(name, packs, researched)
        local ings = {}
        for _, p in ipairs(packs) do ings[#ings + 1] = {type='item', name=p, amount=1} end
        return {name=name, enabled=true, researched=researched or false, prerequisites={}, research_unit_ingredients=ings}
    end
    made = {}   -- (packs its force made in the last hour)
    force = {technologies={}, research_progress=0, get_item_production_statistics=function()
        return {get_flow_count=function(q) return (q.name.quality == 'normal' and made[q.name.name]) or 0 end} end}
    defines.flow_precision_index = defines.flow_precision_index or {one_hour=4}
    lab = {valid=true, type='lab', name='lab', unit_number=21, force=force, surface={}, status=1, position={x=5, y=5},
           get_inventory=function() return {get_item_count=function() return 0 end} end}
    asm = {valid=true, type='assembling-machine', name='assembling-machine-1', unit_number=22, products_finished=0,
           position={x=9, y=5}, get_recipe=function() return {name='iron-gear-wheel',
               products={{type='item', name='iron-gear-wheel', amount=1}}} end}
    local lab_stage = {id='labs', entity_ids={'lab'}, supplies={},
        witnesses={{kind='research_progress_delta', entity_id='lab', min_delta=1}}}
    npc.design = {status='building', stage_index=1, purpose='research-lab', plan={plan_id='p',
        stages={lab_stage}, order={'lab', 'asm'}, entities={
            lab={id='lab', entity=lab, prototype='lab', item='lab', world={x=5, y=5}, failures=0},
            asm={id='asm', entity=asm, prototype='assembling-machine-1', item='assembling-machine-1',
                 world={x=9, y=5}, failures=0}}}}
    npc.design_history = {}
    stage = lab_stage
    logged = {}
    log = function(line) logged[#logged + 1] = line end
"""


class DoomedWitnessTests(unittest.TestCase):
    def run_stage(self, lua, *ticks):
        for t in ticks:
            lua.execute(f"DS.next(npc, {t})")

    def lone_grey(self, extra=""):
        lua = test_clearing.game()
        lua.execute(SETUP + """
            force.current_research = tech('military-2', {GREY})
            force.technologies = {['military-2']=force.current_research}""" + extra)
        self.run_stage(lua, 100, 200, 8000)
        return lua

    def test_research_only_unreachable_packs_run_is_dropped_after_a_while_and_said(self):
        lua = test_clearing.game()
        lua.execute(SETUP + """
            force.current_research = tech('military-2', {RED, GREY})
            force.technologies = {['military-2']=force.current_research}""")
        self.run_stage(lua, 100, 200, 1000)
        self.assertNotEqual(lua.eval("stage.status"), "complete")      # (not before its bound)
        self.run_stage(lua, 8000)
        self.assertEqual(lua.eval("stage.status"), "complete")
        self.assertIn("automation-science-pack, military-science-pack", lua.eval("stage.witnesses[1].dropped"))
        self.assertTrue(any("dropped" in line for line in lua.eval("logged").values()))

    def test_research_whose_packs_are_made_is_waited_on(self):
        lua = test_clearing.game()
        lua.execute(SETUP + """
            force.current_research = tech('automation', {RED})
            force.technologies = {automation=force.current_research}
            made[RED] = 3""")
        self.run_stage(lua, 100, 200, 8000, 16000)
        self.assertNotEqual(lua.eval("stage.status"), "complete")
        self.assertIsNone(lua.eval("stage.witnesses[1].dropped"))

    def test_another_open_research_its_packs_run_keeps_it_waiting(self):
        lua = test_clearing.game()
        lua.execute(SETUP + """
            force.current_research = tech('military-2', {RED, GREY})
            force.technologies = {['military-2']=force.current_research, automation=tech('automation', {RED})}
            made[RED] = 3""")
        self.run_stage(lua, 100, 200, 8000, 16000)
        self.assertIsNone(lua.eval("stage.witnesses[1].dropped"))

    def test_a_production_witness_on_a_machine_that_makes_something_else_is_dropped(self):
        lua = test_clearing.game()
        lua.execute(SETUP + """
            stage.entity_ids = {'asm'}
            stage.witnesses = {{kind='product_counter_delta', entity_id='asm', item='electronic-circuit', min_delta=2}}""")
        self.run_stage(lua, 100, 200, 8000)
        self.assertEqual(lua.eval("stage.status"), "complete")
        self.assertIn("not electronic-circuit", lua.eval("stage.witnesses[1].dropped"))

    def test_it_must_stay_impossible_the_whole_while(self):
        lua = test_clearing.game()
        lua.execute(SETUP + """
            force.current_research = tech('military-2', {GREY})
            force.technologies = {['military-2']=force.current_research}""")
        self.run_stage(lua, 100, 200)
        lua.execute("made[GREY] = 5")
        self.run_stage(lua, 900)                                       # (the pack made now: the clock resets)
        lua.execute("made[GREY] = nil")
        self.run_stage(lua, 1600, 8000)
        self.assertNotEqual(lua.eval("stage.status"), "complete")      # (impossible only since 1600)
        self.run_stage(lua, 9000)
        self.assertEqual(lua.eval("stage.status"), "complete")

    def test_packs_in_its_storage_chests_or_made_lately_by_it_or_a_teammate_count(self):
        chests = self.lone_grey("""
            npc.storage_chests = {{valid=true, get_item_count=function(id) return id.name == GREY and 40 or 0 end}}""")
        self.assertIsNone(chests.eval("stage.witnesses[1].dropped"))
        own = self.lone_grey("""
            made[GREY] = 12""")
        self.assertIsNone(own.eval("stage.witnesses[1].dropped"))
        team = self.lone_grey("""
            mate = {get_item_production_statistics=function()
                return {get_flow_count=function(q) return q.name.name == GREY and 7 or 0 end} end}
            force.get_friend = function(f) return f == mate end
            game.forces = {player=force, mate=mate}   -- (a teammate: an allied force of its own)""")
        self.assertIsNone(team.eval("stage.witnesses[1].dropped"))

    def test_packs_waiting_on_the_belt_that_feeds_the_lab_count(self):
        lua = self.lone_grey("""
            belt = {valid=true, type='transport-belt', get_transport_line=function(i)
                return {get_item_count=function(id) return (i == 2 and id.name == GREY) and 30 or 0 end} end}
            feeder = {drop_target=lab, pickup_target=belt, force='a teammate'}
            lab.surface = {find_entities_filtered=function(f)   -- (a force filter would leave the teammate's out)
                if f.force and f.force ~= feeder.force then return {} end
                return f.type == 'inserter' and {feeder} or {} end}""")
        self.assertIsNone(lua.eval("stage.witnesses[1].dropped"))

    def test_packs_in_a_teammates_chest_it_could_fetch_count(self):
        lua = self.lone_grey("""
            DS_team = require('scripts.team_supply')
            DS_team.has = function(_, item) return item == GREY end""")
        self.assertIsNone(lua.eval("stage.witnesses[1].dropped"))

    def test_packs_of_any_quality_count(self):
        lua = self.lone_grey("""
            prototypes.quality = {normal={}, uncommon={}}
            lab.get_inventory = function() return {get_item_count=function(id)
                return (id.name == GREY and id.quality == 'uncommon') and 2 or 0 end} end""")
        self.assertIsNone(lua.eval("stage.witnesses[1].dropped"))

    def test_packs_of_any_quality_made_lately_count(self):
        lua = self.lone_grey("""
            prototypes.quality = {normal={}, uncommon={}}
            force.get_item_production_statistics = function() return {get_flow_count=function(q)
                return (q.name.name == GREY and q.name.quality == 'uncommon') and 4 or 0 end} end""")
        self.assertIsNone(lua.eval("stage.witnesses[1].dropped"))

    def test_a_machine_set_to_the_pack_that_made_none_lately_does_not_count(self):
        lua = self.lone_grey("""
            asm.products_finished = 50   -- (gears, before it was switched to the pack)
            asm.get_recipe = function() return {name=GREY, products={{type='item', name=GREY, amount=1}}} end""")
        self.assertEqual(lua.eval("stage.status"), "complete")

    def test_a_trigger_technology_open_does_not_keep_it_waiting(self):
        lua = self.lone_grey("""
            force.technologies['steam-power'] = tech('steam-power', {})""")
        self.assertEqual(lua.eval("stage.status"), "complete")


class PackMadeTests(unittest.TestCase):
    """Research is offered only on packs being made (DesignedStart.pack_made, R-9 lesson 19)."""
    def ground(self):
        lua = test_clearing.game()
        lua.execute(SETUP + """
            lab.prototype = {lab_inputs={RED, GREY}}
            local surface = npc.character.surface
            local find = surface.find_entities_filtered
            surface.find_entities_filtered = function(f)
                if f.type == 'assembling-machine' then return machines end
                return find(f)
            end
            machines = {}
            DS.Orbit_ = require('scripts.orbit')""")
        return lua

    def test_a_pack_nothing_makes_or_holds_is_not_made_once_a_line_makes_another(self):
        lua = self.ground()
        lua.execute("""machines = {{get_recipe=function() return {name=RED, products={{type='item', name=RED}}} end}}""")
        self.assertTrue(lua.eval("DS.pack_made(npc, RED)"))                  # (a machine set to it)
        self.assertFalse(lua.eval("DS.pack_made(npc, GREY)"))                # (no line, none had or made lately)
        lua.execute("made[GREY] = 4")
        self.assertTrue(lua.eval("DS.pack_made(npc, GREY)"))                 # (made in the last hour)

    def test_the_platform_making_it_counts(self):
        lua = self.ground()
        lua.execute("""machines = {{get_recipe=function() return {name=RED, products={{type='item', name=RED}}} end}}
                       DS.Orbit_.makes = function(_, item) return item == GREY end""")
        self.assertTrue(lua.eval("DS.pack_made(npc, GREY)"))

    def test_unmade_packs_are_listed(self):
        lua = self.ground()
        lua.execute("""machines = {{get_recipe=function() return {name=RED, products={{type='item', name=RED}}} end}}""")
        packs = lua.eval("DS.unmade_packs(npc, tech('military-2', {RED, GREY}), {})")
        self.assertEqual(list(packs.values()), ["military-science-pack"])


if __name__ == "__main__":
    unittest.main()
