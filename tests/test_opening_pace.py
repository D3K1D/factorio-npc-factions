"""The opening at an experienced player's pace (run 35, 2026-10-25: 42 game minutes in, still on burner drills).
Plates are what every build waits on; the next goal is asked for at once; ore carried into a furnace by hand only
starts it; a witness proves a machine works and waits for no stock; research goes first to what the next goal
waits on."""
import json
import unittest
from pathlib import Path

from companion import catalog_planner as cp
from companion import construction, goal_brain, ratios

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parents[1]


def opening_site():
    return json.loads((FIXTURES / "bw-opening-2min.json").read_text(encoding="utf-8"))


class CostTests(unittest.TestCase):

    def test_a_furnace_costs_its_stone(self):
        site = opening_site()
        book = ratios.Book.from_site(site)
        self.assertEqual(construction.cost(["stone-furnace"], book), {"stone": 5.0})
        drill = construction.cost(["burner-mining-drill"], book)
        self.assertEqual((drill.get("iron-plate"), drill.get("stone")), (9.0, 5.0))


class SpeedTests(unittest.TestCase):

    def test_plates_are_worth_most_while_they_are_scarce(self):
        class Books:
            def __init__(self, spare):
                self.spare = spare

            def budget(self, item):
                return type("B", (), {"headroom": self.spare})()
        bill = {"iron-plate": 1000.0}
        first = goal_brain.speedup({"iron-plate": 1.0}, bill, Books(0.25))
        second = goal_brain.speedup({"iron-plate": 1.0}, bill, Books(1.25))
        third = goal_brain.speedup({"iron-plate": 1.0}, bill, Books(2.25))
        self.assertEqual(first, goal_brain.SPEED_CAP)              # (capped)
        self.assertGreater(second, third)
        self.assertGreater(third, 0)
        self.assertEqual(goal_brain.speedup({"copper-plate": 1.0}, bill, Books(0.25)), 0.0)   # (nothing waits on it)

    def test_a_goal_s_plates_at_a_scarce_income_cost_it_time(self):
        class Books:
            def budget(self, item):
                return type("B", (), {"headroom": 0.25})()
        self.assertAlmostEqual(goal_brain.material_minutes({"iron-plate": 150.0}, Books()), 10.0)

    def test_hand_making_what_a_goal_makes_counts_as_the_engineer_s_time_freed(self):
        site = {"time_use": {"shares": {"supply:craft:automation-science-pack": 0.25, "plan:mine:coal": 0.5,
                                        "supply:transfer:coal": 0.2}}}
        self.assertAlmostEqual(goal_brain.craft_saved({"automation-science-pack": 0.1}, site), 15.0)
        self.assertAlmostEqual(goal_brain.craft_saved({"coal": 1.0}, site), 30.0)     # (carrying it is not making it)
        self.assertEqual(goal_brain.craft_saved({"iron-plate": 1.0}, site), 0.0)

    def test_at_minute_two_fuel_then_stone_and_plates_come_before_what_they_pay_for(self):
        # run 35's site at minute 2: one burner pair on iron, nothing making coal, stone or copper
        site = opening_site()
        goal_brain.forget()
        steps = cp.opening_steps(site)
        rows = {r["goal"]: r for r in goal_brain.weigh(site, steps, opening=steps)}
        best = max(rows.values(), key=lambda r: r["score"])["goal"]
        # nothing makes coal: the coal loop first, every other burner's coal would be mined by hand (run 36 mined coal
        # for 22 burners while the iron it held waited, 2026-10-25)
        self.assertEqual(best, "coal-loop")
        self.assertEqual(rows["coal-loop"]["fed"], 1.0)                  # (it feeds itself)
        self.assertLess(rows["iron-smelting-expansion"]["fed"], 1.0)     # (its coal is not there)
        self.assertGreater(rows["coal-loop"]["speedup"], 0)        # (it was mining coal by hand two thirds of the time)
        # with coal made: stone, then the plates (every furnace and drill takes stone)
        fueled = {**site, "base": list(site.get("base") or []) + [
            {"purpose": "coal-loop", "machines": {"burner-mining-drill": 4},
             "capacity": {"makes": {"coal": 1.0}}, "ledger": {"kind": "mining", "load": 1.0}}],
            "completed_purposes": list(site.get("completed_purposes") or []) + ["coal-loop"]}
        goal_brain.forget()
        steps = cp.opening_steps(fueled)
        rows = {r["goal"]: r for r in goal_brain.weigh(fueled, steps, opening=steps)}
        order = sorted(rows, key=lambda g: -rows[g]["score"])
        self.assertEqual(order[0], "stone-supply")
        self.assertGreater(rows["iron-smelting-expansion"]["speedup"], 10)
        self.assertGreater(rows["iron-smelting-expansion"]["score"], rows["brick-smelting"]["score"])

    def test_the_bill_counts_every_goal_still_ahead_not_only_those_buildable_now(self):
        site = opening_site()
        ahead = goal_brain.steps_ahead(site)
        self.assertIn("electric-iron-smelting", ahead)             # (not buildable before power)
        self.assertNotIn("iron-smelting-start", ahead)


class CoalLoopTests(unittest.TestCase):

    def test_the_loop_grows_before_its_burners_take_all_it_spares(self):
        # (run 38: 4 loop drills spared 0.85 coal a second against 0.72 burnt, and the engineer mined coal by hand a
        # third of its time; the loop grew only at 0.94)
        loop = {"purpose": "coal-loop", "machines": {"burner-mining-drill": 4}}
        pairs = lambda n: {"purpose": "iron-smelting-expansion",
                           "machines": {"burner-mining-drill": n, "stone-furnace": n}}
        site = lambda *base: {"completed_purposes": ["coal-loop"], "base": list(base),
                              "resources": [{"resource": "coal", "rows": []}]}
        self.assertTrue(cp.coal_enough(site(loop, pairs(8))))           # 0.48 burnt: room to spare
        tight = site(loop, pairs(12))                                    # 0.72 burnt: under what it spares, tight
        self.assertLess(cp.coal_draw(tight), 4 * cp.LOOP_NET)
        self.assertFalse(cp.coal_enough(tight))                         # so it grows now

    def test_the_burner_budget_holds_back_more_burners_not_the_loop_that_fuels_them(self):
        # (run 44: 30 burner drills, the loop capped by the budget, and a quarter of its time mining coal by hand)
        loop = {"purpose": "coal-loop", "machines": {"burner-mining-drill": 6}}
        burners = {"purpose": "iron-smelting-expansion", "machines": {"burner-mining-drill": 24, "stone-furnace": 32}}
        site = {"completed_purposes": ["coal-loop"], "base": [loop, burners],
                "resources": [{"resource": "coal", "rows": []}]}
        self.assertFalse(cp.coal_enough(site))                          # 30 in all: the loop still grows
        full = {**site, "base": [{**loop, "machines": {"burner-mining-drill": cp.LOOP_MAX}}, burners]}
        self.assertTrue(cp.coal_enough(full))                           # (to its cap)

    def test_cheap_research_it_can_start_now_counts_as_coming(self):
        # (run 44: eight burner pairs on stone while automation, ten red packs, waited only for its turn)
        import test_clearing
        lua = test_clearing.game()
        lua.execute("""
            local function tech(name, recipe, units, packs, pre, extra)
                local t = {name=name, enabled=true, researched=false, research_unit_count=units,
                           research_unit_ingredients=packs, prerequisites=pre or {},
                           prototype={effects={{type='unlock-recipe', recipe=recipe}}}}
                for k, v in pairs(extra or {}) do t[k] = v end
                return t
            end
            local red = {{name='automation-science-pack', amount=1}}
            local done = {researched=true}
            local mining = tech('electric-mining-drill', 'electric-mining-drill', 10, red)
            force = {current_research=mining, technologies={
                ['electric-mining-drill']=mining,
                automation=tech('automation', 'long-handed-inserter', 10, red, {done}),
                logistics2=tech('logistics-2', 'fast-transport-belt', 200, red, {done}),
                green=tech('green', 'engine-unit', 30, {red[1], {name='logistic-science-pack', amount=1}}, {done}),
                later=tech('later', 'gate', 10, red, {{researched=false}})}}
            coming = {}
            for _, r in ipairs(DS.coming(force)) do coming[r] = true end""")
        self.assertTrue(lua.eval("coming['electric-mining-drill']"))    # (under way)
        self.assertTrue(lua.eval("coming['long-handed-inserter']"))     # (cheap, and it can start it now)
        for far in ("fast-transport-belt", "engine-unit", "gate"):      # (dear, other packs, or not yet open)
            self.assertIsNone(lua.eval(f"coming['{far}']"), far)


class ResearchTests(unittest.TestCase):

    def test_research_ranks_follow_the_goal_waiting_on_them(self):
        site = opening_site()
        ranks = cp.wanted_ranks(site)
        if "electric-mining-drill" in ranks and "assembling-machine-1" in ranks:   # (red science by machine first)
            self.assertLessEqual(ranks["assembling-machine-1"], ranks["electric-mining-drill"])
        self.assertEqual(cp.wanted_recipes(site), set(ranks))
        self.assertEqual(cp.wanted_order(site), sorted(ranks, key=ranks.get))


class SupplyTests(unittest.TestCase):
    """Run 44 (2026-10-26): a turret's magazine the engineer could not hand over (a partly spent one, which the worker
    keeps) was tried every two seconds for 20 game minutes, and the turret plan, which supplies before it builds, never
    went on."""

    def test_a_partly_spent_magazine_is_not_one_to_hand_over(self):
        import test_clearing
        lua = test_clearing.game()
        lua.execute("""
            prototypes.item['firearm-magazine'] = {type='ammo', magazine_size=10}
            local stacks = {{valid_for_read=true, name='firearm-magazine', count=1, ammo=4}}
            held['firearm-magazine'] = 1
            local inv = setmetatable({get_item_count=function() return held['firearm-magazine'] end},
                                     {__len=function() return #stacks end, __index=function(_, i) return stacks[i] end})
            npc.character.get_main_inventory = function() return inv end
            spent = DS.giveable(npc, 'firearm-magazine')
            stacks[1].count, stacks[1].ammo, held['firearm-magazine'] = 20, 10, 20
            fresh = DS.giveable(npc, 'firearm-magazine')""")
        self.assertEqual((lua.eval("spent"), lua.eval("fresh")), (0, 20))

    def test_a_plan_s_own_supply_that_keeps_failing_is_set_aside_a_while(self):
        import test_clearing
        lua = test_clearing.game()
        lua.execute("""
            supply = {entity_id='turret', item='firearm-magazine'}
            for i = 1, 3 do
                npc.design_jobs['s' .. i] = {supply=true, entity_id='turret', item='firearm-magazine'}
                DS.on_receipt(npc, {job_id='s' .. i, status='failed', reason='source_empty'}, 100 + i)
            end
            off = DS.backed_off(npc, supply, 200)
            later = DS.backed_off(npc, supply, 100000)""")
        self.assertTrue(lua.eval("off"))
        self.assertFalse(lua.eval("later"))


class HistoryTests(unittest.TestCase):

    def test_a_layout_from_the_library_is_found_by_its_buildings_kinds(self):
        # (run 49: red science built from the library had ids of its own; its lab was never found by id, and green
        # science, which goes beside it, was never offered)
        import test_clearing
        lua = test_clearing.game()
        lua.execute("""
            local function e(proto, recipe)
                return {prototype=proto, entity={valid=true, name=proto,
                        get_recipe=function() return recipe and {name=recipe} or nil end}}
            end
            npc.design_history = {{purpose='red-science-automation', order={'a', 'b', 'c'}, entities={
                a=e('assembling-machine-1', 'iron-gear-wheel'), b=e('assembling-machine-1', 'automation-science-pack'),
                c=e('lab')}}}
            lab = DS.history_entity(npc, 'red-science-automation', 'science_lab', 'lab')
            science = DS.history_entity(npc, 'red-science-automation', 'science_assembler', 'assembling-machine-1',
                                        'automation-science-pack')
            none = DS.history_entity(npc, 'red-science-automation', 'science_lab')""")
        self.assertEqual(lua.eval("lab.name"), "lab")
        self.assertEqual(lua.eval("science.get_recipe().name"), "automation-science-pack")
        self.assertIsNone(lua.eval("none"))                               # (by id alone: not found)


class GameTests(unittest.TestCase):

    def runtime(self):
        import test_loaders
        lua = test_loaders.designed_runtime()
        lua.execute("""
            for name, p in pairs({['stone-furnace']={type='furnace', w=2, h=2},
                                  ['burner-mining-drill']={type='mining-drill', w=2, h=2}}) do
                prototypes.entity[name] = {name=name, type=p.type, tile_width=p.w, tile_height=p.h,
                                           items_to_place_this={{name=name}},
                                           get_mining_drill_radius=function() return 1 end}
            end
            for _, n in ipairs({'iron-ore', 'wood', 'coal', 'iron-plate', 'stone-furnace', 'burner-mining-drill',
                                'iron-chest'}) do
                prototypes.item[n] = prototypes.item[n] or {name=n}
            end""")
        return lua

    def test_ore_carried_into_a_furnace_only_starts_it_and_a_witness_asks_a_few_items(self):
        # (run 35: a designed layout's "bootstrap" furnace was fed iron ore by hand for good, and asked 36 plates of
        # it before the next four drills went up)
        lua = self.runtime()
        result = lua.eval("""DS.submit({request_id='1:7', plan={plan_id='p', entities={
            {id='f', prototype='stone-furnace', position={x=10, y=10}, direction=0},
            {id='d', prototype='stone-furnace', position={x=14, y=10}, direction=0},
            {id='c', prototype='iron-chest', position={x=20.5, y=10.5}, direction=0}},
            stages={{id='boot', entity_ids={'f'},
                     manual_supplies={{entity_id='f', item='iron-ore', slot='input'}, {entity_id='f', item='wood', slot='fuel'}},
                     witnesses={{kind='product_counter_delta', entity_id='f', item='iron-plate', min_delta=36}}},
                    {id='more', entity_ids={'d', 'c'},
                     manual_supplies={{entity_id='d', item='coal', slot='fuel'}, {entity_id='c', item='coal', slot='input'}},
                     witnesses={{kind='entity_built', entity_id='d'}}}}}})""")
        self.assertTrue(result.ok, dict(result))
        stages = lua.eval("npc.design.plan.stages")
        boot = {(s.entity_id, s.item): s.once for s in stages[1].supplies.values()}
        self.assertEqual(boot, {("f", "iron-ore"): True, ("f", "wood"): False})
        more = {(s.entity_id, s.item): s.once for s in stages[2].supplies.values()}
        self.assertEqual(more, {("d", "coal"): False, ("c", "coal"): False})   # (a lane's chest stays fed)
        self.assertEqual(stages[1].witnesses[1].min_delta, lua.eval("DS.WITNESS_MAX"))

    def test_a_building_out_of_reach_is_left_out_and_its_stage_goes_on_unless_its_witness_watches_it(self):
        # (run 35: one spot out of reach threw away a built electric iron block of 378 buildings)
        import test_clearing
        lua = test_clearing.game()
        lua.execute("""
            prototypes.entity['wooden-chest'] = {name='wooden-chest', type='container'}
            local stage = {id='furnaces', entity_ids={'chest', 'furnace'}, supplies={},
                           witnesses={{kind='entity_built', entity_id='furnace'},
                                      {kind='product_counter_delta', entity_id='furnace', item='iron-plate', min_delta=5}}}
            npc.design = {status='building', stage_index=1, purpose='electric-iron-smelting',
                          plan={plan_id='p', stages={stage}, order={'chest', 'furnace'}, entities={
                chest={id='chest', prototype='wooden-chest', item='wooden-chest', failures=0},
                furnace={id='furnace', prototype='stone-furnace', item='stone-furnace', failures=0}}}}
            npc.design_history = npc.design_history or {}
            function fail(id, reason)
                for k = 1, 6 do
                    npc.design_jobs['j' .. id .. k] = {build=true, entity_id=id}
                    DS.on_receipt(npc, {job_id='j' .. id .. k, status='failed', reason=reason}, 1000 + k)
                end
            end
            fail('chest', 'path_ended_outside_reach')""")
        self.assertEqual(lua.eval("npc.design.status"), "building")
        self.assertIsNotNone(lua.eval("npc.design.plan.entities.chest.left_out"))
        self.assertEqual(lua.eval("npc.obstacle_log[#npc.obstacle_log].rung"), "left_out")   # (recorded)
        lua.execute("fail('furnace', 'path_ended_outside_reach')")              # (what its witness watches)
        self.assertEqual(lua.eval("npc.design.status"), "blocked")
        self.assertEqual(lua.eval("npc.obstacle_log[#npc.obstacle_log].rung"), "hand_back")

    def test_the_engineer_s_coal_is_counted_by_where_it_came_from_and_went(self):
        # (the user, 2026-10-25: how much coal does it mine, put away, put into furnaces, use for power, in totals)
        import test_clearing
        lua = test_clearing.game()
        lua.execute("""
            local function done(kind, mode, n, target, meta)
                DS.flow(npc, meta, {status='complete', kind=kind, mode=mode, item='coal', count=n, target_type=target,
                                    tick=10})
            end
            done('mine', nil, 30)
            done('transfer', 'withdraw', 40, 'mining-drill')
            done('transfer', 'deposit', 25, 'furnace')
            done('transfer', 'deposit', 15, 'boiler')
            done('transfer', 'deposit', 20, 'container', {upkeep='store'})
            DS.flow(npc, nil, {status='failed', kind='mine', item='coal', count=5, tick=20})""")
        flow = dict(lua.eval("npc.item_flow.coal").items())
        self.assertEqual({k: v for k, v in flow.items() if k != "since"},
                         {"mined": 30, "from:mining-drill": 40, "into:furnace": 25, "into:boiler": 15,
                          "into:storage": 20})

    def test_the_next_goal_is_asked_for_ahead_of_upkeep_and_at_once(self):
        designed = (ROOT / "mod" / "scripts" / "designed_start.lua").read_text(encoding="utf-8")
        factory = (ROOT / "mod" / "scripts" / "factory.lua").read_text(encoding="utf-8")
        import re
        delay = int(re.search(r"local GOAL_DELAY=(\d+)", designed).group(1))
        self.assertLessEqual(delay, 300)
        self.assertNotIn("goal_due(npc,tick,7200)", factory)


def crafting_worker():
    """The worker with a character whose crafting queue runs as the game's does (scripts/worker.lua)."""
    from lupa import LuaRuntime
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().worker_source = (ROOT / "mod" / "scripts" / "worker.lua").read_text(encoding="utf-8")
    lua.globals().hands_source = (ROOT / "mod" / "scripts" / "hands.lua").read_text(encoding="utf-8")
    lua.execute("""
        defines = {direction={north=0}, inventory={}}
        function log() end
        local stubs = {['scripts.team_world']={active=function() return false end},
            ['scripts.navigation']={stop=function() end, clear=function() end},
            ['scripts.recipes']={made_in=function() return true end},
            ['scripts.production_credit']={summary=function() end, complete=function() end},
            ['scripts.behavior']={ensure=function() end}, ['scripts.train_jobs']={finish=function() end},
            ['scripts.rescue']={note=function() end}, ['scripts.hands']=assert(load(hands_source))()}
        function require(name) return stubs[name] or {} end
        prototypes = {item={}}
        held = {['iron-plate']=30, ['iron-gear-wheel']=15}
        inv = setmetatable({get_item_count=function(q) return held[q.name] or 0 end,
                            get_insertable_count=function() return 1000 end}, {__len=function() return 0 end})
        queue = {}
        character = {valid=true, position={x=0, y=0}, crafting_queue_size=0, crafting_queue=queue,
                     prototype={crafting_categories={crafting=true}}, get_main_inventory=function() return inv end,
                     force={recipes={['transport-belt']={enabled=true, energy=0.5,
                                                         products={{type='item', name='transport-belt', amount=2}}}}}}
        function character.begin_crafting(t)
            queue[#queue+1] = {index=#queue+1, recipe=t.recipe, count=t.count, prerequisite=false}
            character.crafting_queue_size = #queue
            return t.count
        end
        npc = {id=1, character=character}
        W = assert(load(worker_source, '@worker'))()
    """)
    return lua


class CraftingTests(unittest.TestCase):
    """The engineer crafts as a player does, its queue running while it walks and builds: standing still for each
    craft, it spent 35 to 55% of its time crafting from game minute 18 to 96 (run 41, 2026-10-26)."""

    def test_a_craft_in_the_background_is_queued_and_done_at_once(self):
        lua = crafting_worker()
        lua.execute("assert(W.enqueue(npc, {kind='craft', recipe='transport-belt', count=15, background=true})); "
                    "W.tick(npc, 1)")
        receipt = list(lua.eval("npc.receipts").values())[0]
        self.assertEqual((receipt.status, receipt.queued), ("complete", 15))
        self.assertEqual(lua.eval("queue[1].count"), 15)
        self.assertIsNone(lua.eval("npc.current_job"))                  # (free to walk and build meanwhile)

    def test_from_a_machine_it_takes_what_is_there_when_it_gets_there(self):
        # (run 48: an assembler's inserter had taken a few gears by then; the job failed, and failures count against
        # the plan)
        lua = crafting_worker()
        lua.execute("""
            prototypes.item['iron-gear-wheel'] = {type='item'}
            character.surface, character.force.name, character.reach_distance = {}, 'player', 10
            function character.can_reach_entity() return true end
            got = 0
            inv.insert = function(t) got = got + t.count; return t.count end
            function machine(n)
                local stack = {valid_for_read=true, name='iron-gear-wheel', count=n, quality={name='normal'}, health=1}
                local out = setmetatable({valid=true, get_insertable_count=function() return 0 end},
                                         {__len=function() return 1 end, __index=function(_, i) return stack end})
                return {valid=true, type='assembling-machine', surface=character.surface, force=character.force,
                        position={x=1, y=0}, get_output_inventory=function() return out end}
            end
            assert(W.enqueue(npc, {kind='transfer', target=machine(3), item='iron-gear-wheel', count=8, mode='withdraw',
                                   up_to=true}))
            W.tick(npc, 1)
            assert(W.enqueue(npc, {kind='transfer', target=machine(3), item='iron-gear-wheel', count=8, mode='withdraw'}))
            W.tick(npc, 2)""")
        receipts = sorted(lua.eval("npc.receipts").values(), key=lambda r: r.tick)
        self.assertEqual((receipts[0].status, receipts[0].count), ("complete", 3))
        self.assertEqual((receipts[1].status, receipts[1].reason), ("failed", "partial_transfer"))   # (asked all)

    def test_what_the_queue_makes_is_credited_to_the_statistics_as_it_comes_out(self):
        # (research triggers read them: run 43's first lab, crafted uncredited, never unlocked red science)
        lua = crafting_worker()
        lua.execute("""
            flows = {}
            character.surface = {}
            character.force.get_item_production_statistics = function()
                return {on_flow=function(item, n) flows[item.name] = (flows[item.name] or 0) + n end} end
            assert(W.enqueue(npc, {kind='craft', recipe='transport-belt', count=15, background=true})); W.tick(npc, 1)
            W.credit_background(npc)
            none_yet = flows['transport-belt']
            queue[1].count = 5                                              -- (ten crafts done)
            W.credit_background(npc)
            part = flows['transport-belt']
            table.remove(queue, 1); character.crafting_queue_size = 0      -- (all done)
            W.credit_background(npc); W.credit_background(npc)""")
        self.assertIsNone(lua.eval("none_yet"))
        self.assertEqual(lua.eval("part"), 20)                          # (two belts a craft)
        self.assertEqual(lua.eval("flows['transport-belt']"), 30)       # (and never twice)
        self.assertEqual(lua.eval("#npc.background_queued"), 0)

    def test_it_mines_where_what_it_needs_comes_quickest_walk_back_included(self):
        # (trace 2026-10-26: it walked 130 tiles to a huge rock for ten coal, coal ore ten tiles away)
        lua = crafting_worker()
        lua.execute("""
            character.prototype.mining_speed = 0.5
            rock = {type='simple-entity', prototype={mineable_properties={mining_time=2, products={
                {type='item', name='coal', amount_min=24, amount_max=50}, {type='item', name='stone', amount_min=24, amount_max=50}}}}}
            ore = {type='resource', amount=800, prototype={mineable_properties={mining_time=1, products={
                {type='item', name='coal', amount=1}}}}}
            function rate(e, d, needs) return W.yield_rate(character, e, 'coal', d, needs) end""")
        self.assertGreater(lua.eval("rate(ore, 10, 10)"), lua.eval("rate(rock, 130, 10)"))   # far rock: the ore near
        self.assertGreater(lua.eval("rate(rock, 30, 10)"), lua.eval("rate(ore, 10, 10)"))    # a rock near: the rock
        self.assertGreater(lua.eval("rate(ore, 10, 1)"), lua.eval("rate(rock, 30, 1)"))      # only one wanted: the ore
        # stone in the early game from rocks (the user, 2026-10-24): fifty wanted, a rock 60 tiles off beats stone ore
        lua.execute("stone = {type='resource', amount=800, prototype={mineable_properties={mining_time=1, products={"
                    "{type='item', name='stone', amount=1}}}}}")
        self.assertGreater(lua.eval("W.yield_rate(character, rock, 'stone', 60, 50)"),
                           lua.eval("W.yield_rate(character, stone, 'stone', 10, 50)"))

    def test_a_tree_it_cannot_reach_for_others_round_it_has_the_nearest_toward_it_cut_first(self):
        # (run 60, 2026-10-26: a belt tile under a tree in a forest was left out of a coal line, unreached six times)
        lua = crafting_worker()
        lua.execute("""
            nav = require('scripts.navigation')
            nav.distance = function(a, b) return math.sqrt((a.x - b.x)^2 + (a.y - b.y)^2) end
            nav.step = function() return 'failed', 'path_ended_outside_reach' end
            local function tree(x, y)
                return {valid=true, type='tree', name='tree', minable=true, force={name='neutral'}, position={x=x, y=y},
                        prototype={mineable_properties={minable=true, mining_time=0.5,
                                   products={{type='item', name='wood', amount=4}}}}}
            end
            surf = {}
            far, way, behind, aside = tree(10, 0), tree(2, 0), tree(-2, 0), tree(3, 4)
            for _, t in ipairs({far, way, behind, aside}) do t.surface = surf end
            surf.find_entities_filtered = function() return {far, way, behind, aside} end
            character.surface, character.resource_reach_distance, character.reach_distance = surf, 2.7, 10
            inv.can_insert = function() return true end
            assert(W.enqueue(npc, {kind='mine', target=far, count=1}))
            W.tick(npc, 1)""")
        # (a detour to the one on the line toward it, not the one behind nor the one off to the side; the job keeps its
        # target, no failure)
        self.assertTrue(lua.eval("npc.current_job.detour == way"))
        self.assertTrue(lua.eval("npc.current_job.target == far"))
        self.assertEqual(len(lua.eval("npc.failures") or {}), 0)
        # the one in the way cut (its wood no part of the job), it goes on to its target and mines it: the job is done on
        # its own target (Codex's review, thread W-7-boilers-forest)
        lua.execute("""held.wood = 4; way.valid = false
            nav.step = function() return 'arrived' end
            W.tick(npc, 2); W.tick(npc, 3)""")
        self.assertIsNone(lua.eval("npc.receipts and next(npc.receipts)"))   # (no receipt for the detour)
        self.assertTrue(lua.eval("npc.character.selected == far"))
        lua.execute("held.wood = 8; far.valid = false; W.tick(npc, 4)")
        receipt = list(lua.eval("npc.receipts").values())[-1]
        self.assertEqual((receipt.status, receipt.count), ("complete", 4))
        # still out of reach after its cut, nothing more in the way: it fails as before
        lua.execute("""far.valid, way.valid = true, true
            nav.step = function() return 'failed', 'path_ended_outside_reach' end
            assert(W.enqueue(npc, {kind='mine', target=far, count=1}))
            W.tick(npc, 5); way.valid = false; W.tick(npc, 6); W.tick(npc, 7)""")
        self.assertEqual(list(lua.eval("npc.failures").values())[-1].reason, "path_ended_outside_reach")

    def test_a_building_of_its_own_within_reach_is_picked_up_from_where_it_stands(self):
        # (run 60's checkpoint replayed, 2026-10-26: a belt boxed in by walls on both sides, never within a tree's reach,
        # and a whole wall plan given up)
        lua = crafting_worker()
        lua.execute("""
            nav = require('scripts.navigation')
            nav.distance = function(a, b) return math.sqrt((a.x - b.x)^2 + (a.y - b.y)^2) end
            nav.step = function() return 'failed', 'path_ended_outside_reach' end
            surf = {}
            belt = {valid=true, type='transport-belt', name='transport-belt', minable=true, force=character.force,
                    position={x=2.5, y=0}, surface=surf,
                    prototype={mineable_properties={minable=true, mining_time=0.1,
                               products={{type='item', name='transport-belt', amount=1}}}}}
            character.surface, character.resource_reach_distance, character.reach_distance = surf, 2.7, 10
            character.can_reach_entity = function(e) return true end
            npc.force = character.force
            character.mine_entity = function(e) e.valid = false; held['transport-belt'] = (held['transport-belt'] or 0) + 1; return true end
            inv.can_insert = function() return true end
            assert(W.enqueue(npc, {kind='mine', target=belt, count=1, mode='deconstruct'}))
            W.tick(npc, 1); W.tick(npc, 2)""")
        receipt = list(lua.eval("npc.receipts").values())[-1]
        self.assertEqual((receipt.status, receipt.count), ("complete", 1))

    def test_an_owned_craft_waits_for_the_background_queue_and_does_not_fail(self):
        lua = crafting_worker()
        lua.execute("""
            assert(W.enqueue(npc, {kind='craft', recipe='transport-belt', count=15, background=true})); W.tick(npc, 1)
            assert(W.enqueue(npc, {kind='craft', recipe='transport-belt', count=1})); W.tick(npc, 2); W.tick(npc, 3)""")
        self.assertEqual(len(lua.eval("npc.failures") or {}), 0)
        self.assertEqual(lua.eval("npc.current_job.kind"), "craft")
        self.assertEqual(lua.eval("#queue"), 1)                         # (its own turn not begun yet)

    def test_while_its_items_are_crafted_the_stage_s_other_buildings_go_up(self):
        import test_clearing
        lua = test_clearing.game()
        lua.execute("""
            prototypes.recipe = {['transport-belt']={products={{type='item', name='transport-belt', amount=2}}}}
            prototypes.entity['transport-belt'].tile_width, prototypes.entity['transport-belt'].tile_height = 1, 1
            prototypes.entity['stone-furnace'].tile_width, prototypes.entity['stone-furnace'].tile_height = 2, 2
            npc.character.crafting_queue_size = 1
            npc.character.crafting_queue = {{index=1, recipe='transport-belt', count=10, prerequisite=false}}
            held['stone-furnace'] = 1
            DS.fought_over = function() return false end
            local stage = {id='s', entity_ids={'a', 'b'}, supplies={}, witnesses={}}
            npc.design = {status='building', stage_index=1, plan={stages={stage}, entities={
                a={id='a', prototype='transport-belt', item='transport-belt', world={x=5.5, y=5.5}, direction=0},
                b={id='b', prototype='stone-furnace', item='stone-furnace', world={x=20, y=5}, direction=0}}}}
            first = {DS.next(npc, 100)}
            npc.design.plan.entities.b.entity = {valid=true}             -- (the furnace put down)
            again = {DS.next(npc, 101)}""")
        job = lua.eval("first[1]")
        self.assertEqual((job.kind, job.item), ("build", "stone-furnace"))   # (the belts are on their way)
        self.assertIsNone(lua.eval("again[1]"))
        self.assertEqual(lua.eval("again[2]"), "crafting:transport-belt")   # (waits for them, no failure)
        self.assertEqual(lua.eval("npc.design.status"), "building")
        self.assertIsNone(lua.eval("npc.design.plan.stages[1].witness_tick"))

    def test_its_hands_are_quick_for_what_its_machines_do_not_stock_for_it(self):
        # (the user, 2026-10-26: ten times a player's crafting speed, but not for what is automated)
        import test_clearing
        lua = test_clearing.game()
        lua.execute("""
            defines.inventory.chest = 1
            prototypes.recipe = {['transport-belt']={products={{type='item', name='transport-belt', amount=2}}},
                                 ['stone-wall']={products={{type='item', name='stone-wall', amount=1}}}}
            npc.character.crafting_queue_size = 1
            npc.character.crafting_queue = {{index=1, recipe='transport-belt', count=5}}
            DS.crafting_pace(npc, 1)
            early = npc.character.character_crafting_speed_modifier
            -- a wall row: an assembler making walls, an inserter putting them into a chest
            local row = {valid=true, type='assembling-machine', get_recipe=function() return prototypes.recipe['stone-wall'] end}
            local chest = {valid=true, type='container'}
            local inserter = {valid=true, type='inserter', pickup_target=row, drop_target=chest}
            npc.design_history = {{entities={i={entity=inserter}}, order={'i'}}}
            game.tick = 700
            stocked = DS.stocked(npc)
            npc.character.crafting_queue = {{index=1, recipe='stone-wall', count=5}}
            DS.crafting_pace(npc, 701)
            walls = npc.character.character_crafting_speed_modifier""")
        self.assertEqual(lua.eval("early"), 9)                      # (ten times a player's speed)
        self.assertTrue(lua.eval("stocked['stone-wall']"))
        self.assertIsNone(lua.eval("stocked['transport-belt']"))
        self.assertEqual(lua.eval("walls"), 0)                      # (its machines make them: a player's speed)

    def test_a_background_craft_counts_its_queue_time_as_hand_making(self):
        # (what automating it would take over: goal_brain.craft_saved reads these shares)
        import test_clearing
        lua = test_clearing.game()
        lua.execute("""
            prototypes.recipe = {['automation-science-pack']={energy=5}}
            npc.character.crafting_speed = 1
            DS.tally(npc, {upkeep='supply'}, {kind='craft', recipe='automation-science-pack', queued=10,
                                              tick=6000, started_tick=6000})
            shares = DS.time_use(npc)""")
        self.assertEqual(lua.eval("shares.seconds"), 50)
        self.assertEqual(lua.eval("shares.shares['supply:craft:automation-science-pack']"), 1)

    def test_what_its_machines_make_is_fetched_off_its_belts_not_crafted(self):
        # (the user, 2026-10-25: once gears or green circuits are made by machines, it grabs some off the belts)
        import test_clearing
        lua = test_clearing.game()
        lua.execute("""
            function belt(x, gears)
                local line = {get_contents=function() return gears > 0 and {{name='iron-gear-wheel', count=gears,
                                                                              quality='normal'}} or {} end}
                return {valid=true, type='transport-belt', position={x=x, y=0},
                        get_transport_line=function() return line end}
            end
            local belts = {belt(0.5, 4), belt(1.5, 4), belt(2.5, 3), belt(30.5, 6), belt(60.5, 0)}
            local entities, order = {}, {}
            for i, b in ipairs(belts) do entities['b' .. i] = {entity=b}; order[i] = 'b' .. i end
            npc.design_history = {{entities=entities, order=order}}
            best, n, near = DS.on_belts(npc, 'iron-gear-wheel')""")
        self.assertEqual(lua.eval("n"), 22)                                  # (each belt's two lanes, round x 0..2)
        self.assertLess(lua.eval("best.position.x"), 3)
        self.assertEqual(lua.eval("#near"), 3)
        self.assertEqual(lua.eval("select(2, DS.on_belts(npc, 'copper-cable'))"), 0)

    def test_what_a_chest_is_filled_with_by_hand_is_not_taken_off_the_belts_it_heads(self):
        # (D-24, late-combat: the ammo loop's magazines put on it from its hand-filled chest were taken off it again,
        # by its own side's plan and by the loop's other sides; another goal's belts are a source as ever)
        import test_clearing
        lua = test_clearing.game()
        lua.execute("""
            defines.inventory.chest = 1
            function belt(x)
                local line = {get_contents=function() return {{name='iron-gear-wheel', count=4, quality='normal'}} end}
                return {valid=true, type='transport-belt', position={x=x, y=0},
                        get_transport_line=function() return line end}
            end
            local chest = {valid=true, type='container', position={x=0, y=1}}
            local head = {entities={c={entity=chest}, b1={entity=belt(0.5)}}, order={'c', 'b1'}, purpose='ammo-line',
                          stages={{supplies={{entity_id='c', item='iron-gear-wheel', slot='input'}}}}}
            local side = {entities={b2={entity=belt(40.5)}}, order={'b2'}, purpose='ammo-line', stages={{supplies={}}}}
            local other = {entities={b3={entity=belt(80.5)}}, order={'b3'}, purpose='bus-base', stages={{supplies={}}}}
            npc.design_history = {head, side}
            best, n = DS.on_belts(npc, 'iron-gear-wheel')""")
        self.assertEqual(lua.eval("n"), 0)
        lua.execute("""npc.belt_items = nil
            npc.design_history[3] = {entities={b3={entity=belt(80.5)}}, order={'b3'}, purpose='bus-base',
                                     stages={{supplies={}}}}
            best, n = DS.on_belts(npc, 'iron-gear-wheel')""")
        self.assertEqual(lua.eval("best.position.x"), 80.5)

    def test_a_belt_pickup_sweeps_its_belts_within_reach(self):
        lua = crafting_worker()
        lua.execute("""
            prototypes.item['iron-gear-wheel'] = {type='item'}
            character.surface = {}
            character.force.name = 'player'
            character.reach_distance = 10
            function character.can_reach_entity(e) return math.abs(e.position.x) < 10 end
            got = 0
            inv.insert = function(t) got = got + t.count; return t.count end
            function belt(x, n)
                local st = {valid_for_read=n > 0, name='iron-gear-wheel', count=n, quality={name='normal'}}
                function st.clear() st.valid_for_read = false; st.count = 0 end
                local line = {st}   -- (its stacks, as #line and line[i] give them)
                function line.get_item_count() return st.valid_for_read and st.count or 0 end
                function line.line_equals(other) return other == line end
                return {valid=true, type='transport-belt', position={x=x, y=0}, force=character.force,
                        surface=character.surface, get_transport_line=function() return line end}
            end
            a, b, far = belt(1, 4), belt(2, 4), belt(40, 4)
            assert(W.enqueue(npc, {kind='belt_pickup', target=a, item='iron-gear-wheel', count=50, up_to=true,
                                   sweep={a, b, far}}))
            W.tick(npc, 1)""")
        self.assertEqual(lua.eval("got"), 8)                                 # (it and the belt beside it, not the far one)


class FarBuildTests(unittest.TestCase):
    """Building far from home (the user's live game 2026-10-23: Dax took 13 minutes for 2 turrets and 3 walls on his
    plot's edge; replayed with today's code, a wall cost a 90 s walk home and back)."""

    def test_short_of_a_whole_batch_it_makes_what_it_carries_makes_now(self):
        import test_clearing
        lua = test_clearing.game()
        lua.execute("""
            local R = require('scripts.recipes')
            R.hand = function() return {} end
            R.for_item = function(_, item) if item == 'stone-wall' then
                return {name='stone-wall', ingredients={{type='item', name='stone-brick', amount=5}}, energy=0.5} end end
            R.made_in = function() return true end
            R.yield = function() return 1 end
            held['stone-brick'] = 57
            prototypes.entity['stone-wall'] = {name='stone-wall', type='wall', tile_width=1, tile_height=1,
                                               collision_box={{-0.29, -0.29}, {0.29, 0.29}}, collision_mask={layers={object=true}}}
            DS.fought_over = function() return false end
            local ids, ents = {}, {}
            for i = 1, 30 do
                ids[i] = 'w' .. i
                ents['w' .. i] = {id='w' .. i, prototype='stone-wall', item='stone-wall', world={x=200 + i, y=0}}
            end
            npc.design = {status='building', stage_index=1,
                          plan={stages={{id='s', entity_ids=ids, supplies={}, witnesses={}}}, entities=ents}}
            npc.design_history = {}
            first = {DS.next(npc, 100)}""")
        self.assertEqual(lua.eval("DS.makeable(npc, 'stone-wall')"), 11)
        job = lua.eval("first[1]")
        # (bricks for all thirty cannot be had: the eleven its 57 bricks make, not one)
        self.assertEqual((job.kind, job.recipe, job.count), ("craft", "stone-wall", 11))

    def test_with_its_furnaces_all_at_work_and_slow_over_the_rest_it_puts_one_more_down(self):
        import test_clearing
        lua = test_clearing.game()
        lua.execute("prototypes.recipe = {['stone-brick']={energy=3.2, ingredients={{type='item', name='stone', amount=2}}}}")
        self.assertEqual(lua.eval("DS.ore_per('stone-brick', 'stone')"), 2)      # (two stone a brick)
        self.assertEqual(lua.eval("DS.ore_per('iron-plate', 'iron-ore')"), 1)
        self.assertTrue(lua.eval("DS.more_smelters(npc, 'stone-brick', 20, 1)"))      # (64 s in one furnace)
        self.assertFalse(lua.eval("DS.more_smelters(npc, 'stone-brick', 5, 1)"))      # (16 s: it waits)
        self.assertFalse(lua.eval("DS.more_smelters(npc, 'stone-brick', 200, 4)"))    # (four at work at most)

    def test_at_home_before_far_work_it_sees_to_everything_due_in_one_visit(self):
        import test_clearing
        lua = test_clearing.game()
        lua.execute("""
            npc.design = {status='building', work_at={x=260, y=0}}
            npc.character.position = {x=5, y=0}
            npc.time_use = {since=0, tags={['supply:transfer:coal']=100}}   -- (its building starved of time)
            home = DS.leaving(npc, 100)
            starved_at_home = DS.build_starved(npc)
            npc.character.position = {x=255, y=0}
            out = DS.leaving(npc, 200)
            away = DS.away(npc)
            starved_out = DS.build_starved(npc)""")
        self.assertTrue(lua.eval("home"))
        self.assertFalse(lua.eval("starved_at_home"))     # (the round at home goes first)
        self.assertFalse(lua.eval("out"))
        self.assertTrue(lua.eval("away"))                 # (out at the work: what can wait at home waits)
        self.assertTrue(lua.eval("starved_out"))
        self.assertIsNone(lua.eval("npc.home_round"))
        # work near home: no round, no "away"
        lua.execute("npc.design.work_at = {x=20, y=0}; npc.character.position = {x=5, y=0}")
        self.assertFalse(lua.eval("DS.leaving(npc, 300)"))
        self.assertFalse(lua.eval("DS.away(npc)"))

    def test_its_first_far_build_waits_one_round_at_home_and_a_far_boiler_is_seen_to_in_time(self):
        # (Codex's review, thread W-3-far-building)
        import test_clearing
        lua = test_clearing.game()
        lua.execute("""
            held['stone-wall'] = 5
            prototypes.entity['stone-wall'] = {name='stone-wall', type='wall', tile_width=1, tile_height=1,
                                               collision_box={{-0.29, -0.29}, {0.29, 0.29}}, collision_mask={layers={object=true}}}
            DS.fought_over = function() return false end
            npc.character.position = {x=5, y=0}
            npc.design = {status='building', stage_index=1, plan={stages={{id='s', entity_ids={'w1'}, supplies={}, witnesses={}}},
                          entities={w1={id='w1', prototype='stone-wall', item='stone-wall', world={x=260, y=0}}}}}
            npc.design_history = {}
            first = {DS.next(npc, 100)}
            npc.home_round.done = true
            second = {DS.next(npc, 101)}""")
        self.assertIsNone(lua.eval("first[1]"))
        self.assertEqual(lua.eval("first[2]"), "home_round")
        self.assertEqual((lua.eval("second[1].kind"), lua.eval("second[1].entity_name")), ("build", "stone-wall"))
        # four furnaces it smelts in by hand already: no fifth
        lua.execute("npc.hand_smelters = {{valid=true}, {valid=true}, {valid=true}, {valid=true}}")
        self.assertFalse(lua.eval("DS.more_smelters(npc, 'stone-brick', 200, 1)"))
        # a boiler at home (a fill lasts 111 s) with 40 s left: from far work it is due now (the walk back is 28 s), from
        # beside it not yet
        lua.execute("""
            defines.inventory.fuel = 1
            prototypes.item.coal.fuel_value = 4e6
            fuel = {coal=0}
            npc.hand_smelters = nil
            boiler = {valid=true, type='boiler', unit_number=9, position={x=0, y=0},
                      prototype={get_max_energy_usage=function() return 1800 * 1000 / 60 end},
                      burner={remaining_burning_fuel=40 * 1.8e6}}
            inv_b = {get_item_count=function(spec) return fuel[spec.name] or 0 end, get_contents=function() return {} end}
            npc.character.position = {x=255, y=0}
            far = DS.fuel_job(npc, boiler, inv_b, 'coal', true) ~= nil
            npc.character.position = {x=2, y=0}
            near = DS.fuel_job(npc, boiler, inv_b, 'coal', true) ~= nil""")
        self.assertTrue(lua.eval("far"))
        self.assertFalse(lua.eval("near"))


class SteelTests(unittest.TestCase):
    """Steel soon after its research, a furnace of it beside an iron furnace: the bus makes steel only with blue
    science's extension (R-1: steel and military science not reached in 250 game minutes, run 51)."""

    def test_steel_is_taken_up_once_researched_and_done_once_something_makes_it(self):
        site = opening_site()
        done = dict(cp.opening_order(site))["steel-smelting"]
        self.assertFalse(done(site))
        self.assertTrue(done({**site, "completed_purposes": ["steel-smelting"]}))
        self.assertTrue(done({**site, "base": [{"purpose": "bus-base", "recipes": {"steel-plate": 2},
                                                "machines": {"stone-furnace": 2}}]}))
        self.assertNotIn("steel-smelting", cp.opening_steps(site))           # (not researched yet)
        researched = {**site, "recipes": [*site["recipes"], {"name": "steel-plate"}],
                      "suggestions": {**(site.get("suggestions") or {}), "steel-smelting": {"entities": [], "stages": []}}}
        self.assertIn("steel-smelting", cp.opening_steps(researched))
        # due at once, ahead of the weighing; not once something makes steel
        self.assertEqual(cp.first_source_due(researched), "steel-smelting")
        self.assertIsNone(cp.first_source_due({**researched, "base": [{"recipes": ["steel-plate"]}]}))
        self.assertIsNone(cp.first_source_due(site))
        self.assertIsNone(cp.first_source_due({**researched, "failed_purposes": ["steel-smelting"]}))
        import tempfile
        from companion import blueprints
        planner = cp.CatalogPlanner(blueprints.Catalog(tempfile.mkdtemp()), log=lambda _: None)
        self.assertEqual(planner.choose_purpose(researched), "steel-smelting")   # (ahead of the weighing)


if __name__ == "__main__":
    unittest.main()
