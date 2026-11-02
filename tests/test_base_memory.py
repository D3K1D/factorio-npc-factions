"""The base memory (base_memory.lua): an engineer's base as blocks, in Factorio's Lua 5.2."""
from pathlib import Path
import unittest

from lupa.lua52 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]


class BaseMemoryTests(unittest.TestCase):
    def test_each_built_plan_is_a_block_with_its_box_machines_and_how_they_do(self):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().source = (ROOT / "mod" / "scripts" / "base_memory.lua").read_text(encoding="utf-8")
        lua.execute("""
            defines = {entity_status={working=1, item_ingredient_shortage=2, full_output=3, no_power=4}}
            M = assert(load(source))()
            function e(name, kind, x, y, w, status, recipe)
                return {valid=true, name=name, type=kind, status=status,
                        bounding_box={left_top={x=x-w/2, y=y-w/2}, right_bottom={x=x+w/2, y=y+w/2}},
                        get_recipe=function() return recipe and {name=recipe} or nil end}
            end
            red = {plan_id='bus-red', purpose='bus-base', base_plan={stage='bus-red-science'},
                   order={'a', 'b', 'c', 'gone'},
                   entities={a={entity=e('assembling-machine-2', 'assembling-machine', 10.5, 20.5, 3, 3, 'automation-science-pack')},
                             b={entity=e('assembling-machine-2', 'assembling-machine', 14.5, 20.5, 3, 2, 'automation-science-pack')},
                             c={entity=e('inserter', 'inserter', 10.5, 18.5, 1, 1)},
                             gone={entity=nil}}}
            npc = {design_history={red}, design={status='complete'}}
            blocks = M.blocks(npc, {x=0, y=0})
        """)
        block = lua.eval("blocks[1]")
        self.assertEqual((block.id, block.stage, block.buildings, block.planned), ("bus-red", "bus-red-science", 3, 4))
        self.assertEqual(list(block.box.values()), [9, 18, 16, 22])
        self.assertEqual(block.machines["assembling-machine-2"], 2)
        self.assertEqual(list(block.recipes.values()), ["automation-science-pack"])
        self.assertEqual((block.status["backed_up"], block.status["starved"]), (1, 1))
        self.assertIsNone(block.stock)

    def test_a_blocks_machines_are_grouped_by_where_they_stand(self):
        # (furnaces by the factory, their drills at a far patch: two places, not one box round both; run 55's ring)
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().source = (ROOT / "mod" / "scripts" / "base_memory.lua").read_text(encoding="utf-8")
        lua.execute("""
            defines = {entity_status={working=1}}
            M = assert(load(source))()
            surface = {find_entities_filtered=function() return {} end}
            function e(name, kind, x, y, w)
                return {valid=true, name=name, type=kind, status=1, surface=surface, position={x=x, y=y},
                        prototype={mining_drill_radius=2.49},
                        bounding_box={left_top={x=x-w/2, y=y-w/2}, right_bottom={x=x+w/2, y=y+w/2}},
                        get_recipe=function() return nil end}
            end
            p = {plan_id='bricks', purpose='electric-brick-smelting', order={}, entities={}}
            for i, spot in ipairs({{'stone-furnace', 'furnace', 0, 0, 2}, {'stone-furnace', 'furnace', 3, 0, 2},
                                   {'electric-mining-drill', 'mining-drill', 4, 18, 3},     -- 15.5 tiles on: near
                                   {'electric-mining-drill', 'mining-drill', 150, 200, 3},  -- the far patch
                                   {'electric-mining-drill', 'mining-drill', 154, 200, 3},
                                   {'electric-mining-drill', 'mining-drill', 174, 200, 3},  -- 17 tiles on: apart
                                   {'small-electric-pole', 'electric-pole', 80, 100, 1}}) do
                p.order[i] = 'k' .. i
                p.entities['k' .. i] = {entity=e(spot[1], spot[2], spot[3], spot[4], spot[5])}
            end
            blocks = M.blocks({design_history={p}, design={status='complete'}}, {x=-10, y=-20})
        """)
        block = lua.eval("blocks[1]")
        self.assertEqual(list(block.machines_box.values()), [9, 19, 186, 222])   # (one box round all)
        near, far = lua.eval("blocks[1].parts[1]"), lua.eval("blocks[1].parts[2]")
        self.assertEqual(list(lua.eval("blocks[1].parts[3]").box.values()), [182, 218, 186, 222])
        self.assertIsNone(lua.eval("blocks[1].parts[4]"))
        self.assertEqual(list(near.box.values()), [9, 19, 16, 40])                # (export-local: less the origin)
        self.assertEqual(dict(near.machines), {"stone-furnace": 2, "electric-mining-drill": 1})
        self.assertEqual(list(far.box.values()), [158, 218, 166, 222])
        self.assertEqual(dict(far.machines), {"electric-mining-drill": 2})

    def test_a_block_says_what_its_chests_hold(self):
        # (walls made by machine are stocked in a chest until there are enough for the wall line)
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().source = (ROOT / "mod" / "scripts" / "base_memory.lua").read_text(encoding="utf-8")
        lua.execute("""
            defines = {entity_status={working=1}, inventory={chest=1}}
            M = assert(load(source))()
            function chest(x, contents)
                return {valid=true, name='wooden-chest', type='container',
                        bounding_box={left_top={x=x-0.5, y=-0.5}, right_bottom={x=x+0.5, y=0.5}},
                        get_inventory=function() return {get_contents=function() return contents end} end}
            end
            row = {plan_id='walls', purpose='wall-automation', order={'in', 'out'},
                   entities={['in']={entity=chest(0, {{name='stone-brick', count=40, quality='normal'}})},
                             out={entity=chest(5, {{name='stone-wall', count=120, quality='normal'},
                                                  {name='stone-wall', count=3, quality='rare'}})}}}
            npc = {design_history={row}, design={status='complete'}}
            blocks = M.blocks(npc, {x=0, y=0})
        """)
        stock = lua.eval("blocks[1].stock")
        self.assertEqual((stock["stone-wall"], stock["stone-brick"]), (120, 40))   # (normal quality only)

    def test_a_hostile_engineers_base_is_a_threat_too(self):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().source = (ROOT / "mod" / "scripts" / "base_memory.lua").read_text(encoding="utf-8")
        lua.execute("""
            M = assert(load(source))()
            war = {valid=true, name='npc-faction-2'}
            pact = {valid=true, name='npc-faction-3'}
            mine = {name='npc-faction-1',
                    get_cease_fire=function(f) return f == pact end,
                    get_friend=function(f) return f == pact end}
            surface = {find_entities_filtered=function(filter)
                if filter.force == 'enemy' then
                    return {{position={x=0, y=90}, type='unit-spawner', name='biter-spawner'}}
                elseif filter.force == war then
                    return {{position={x=150, y=0}, type='ammo-turret', name='gun-turret'}}
                end
                return {{position={x=-60, y=0}, type='furnace', name='stone-furnace'}}
            end}
            npc = {force=mine, home={x=0, y=0}, character={valid=true, surface=surface}}
            storage = {npcs={npc, {force=war}, {force=pact}}}
            threats = M.threats(npc, {x=0, y=0})
        """)
        kinds = sorted((t.kind, t.x) for t in lua.eval("threats").values())
        # the nest and the engineer at war; the ally's furnace is no threat
        self.assertEqual(kinds, [("hostile-engineer", 150), ("unit-spawner", 0)])


    def test_working_radars_show_nests_beyond_the_home_ground(self):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().source = (ROOT / "mod" / "scripts" / "base_memory.lua").read_text(encoding="utf-8")
        lua.execute("""
            defines = {entity_status={working=1, no_power=4}}
            M = assert(load(source))()
            mine = {name='npc-faction-1', get_cease_fire=function() return false end,
                    get_friend=function() return false end}
            near = {position={x=0, y=90}, type='unit-spawner', name='biter-spawner', unit_number=1}
            far = {position={x=480, y=0}, type='unit-spawner', name='biter-spawner', unit_number=2}
            radars = {}
            surface = {find_entities_filtered=function(filter)
                if filter.type == 'radar' then return radars end
                if filter.force ~= 'enemy' then return {} end
                local out = {}
                for _, e in ipairs({near, far}) do
                    local dx, dy = e.position.x - filter.position.x, e.position.y - filter.position.y
                    if dx * dx + dy * dy <= filter.radius * filter.radius then out[#out + 1] = e end
                end
                return out
            end}
            npc = {force=mine, home={x=0, y=0}, character={valid=true, surface=surface}}
            storage = {npcs={npc}}
            function seen() local n = 0 for _ in pairs(M.threats(npc, {x=0, y=0})) do n = n + 1 end return n end
            before = seen()
            radars = {{position={x=300, y=0}, status=4}}
            unpowered = seen()
            radars[1].status = 1
            watching = seen()
        """)
        g = lua.globals()
        self.assertEqual((g.before, g.unpowered, g.watching), (1, 1, 2))   # the far nest, once its radar works

    def test_where_an_engineer_hit_the_base_is_a_threat_for_a_while(self):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().source = (ROOT / "mod" / "scripts" / "base_memory.lua").read_text(encoding="utf-8")
        lua.execute("""
            M = assert(load(source))()
            game = {tick=1000}
            surface = {find_entities_filtered=function() return {} end}
            npc = {force={name='npc-faction-1'}, home={x=0, y=0}, character={valid=true, surface=surface},
                   raids={{x=12, y=-3, tick=900, by='Borin'}}}
            storage = {npcs={npc}}
            fresh = M.threats(npc, {x=10, y=0})
            game.tick = 900 + 36001
            stale = M.threats(npc, {x=10, y=0})
        """)
        fresh = lua.eval("fresh[1]")
        self.assertEqual((fresh.kind, fresh.x, fresh.y, fresh.name), ("raid", 2, -3, "Borin"))
        self.assertEqual(lua.eval("#stale"), 0)                             # forgotten after 10 minutes

    def test_a_block_knows_how_much_ore_its_drills_have_left(self):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().source = (ROOT / "mod" / "scripts" / "base_memory.lua").read_text(encoding="utf-8")
        lua.execute("""
            defines = {entity_status={working=1}}
            M = assert(load(source))()
            ores = {}
            for x = 0, 5 do for y = 0, 1 do
                ores[#ores + 1] = {name='coal', position={x=x + 0.5, y=y + 0.5}, amount=600,
                                   prototype={resource_category='basic-solid'}}
            end end
            surface = {find_entities_filtered=function(f)
                local out = {}
                for _, r in ipairs(ores) do
                    if r.position.x >= f.area[1][1] and r.position.x <= f.area[2][1]
                        and r.position.y >= f.area[1][2] and r.position.y <= f.area[2][2] then out[#out + 1] = r end
                end
                return out
            end}
            function drill(x)
                return {valid=true, name='burner-mining-drill', type='mining-drill', status=1, surface=surface,
                        position={x=x, y=1}, prototype={mining_drill_radius=0.99, mining_speed=0.25},
                        bounding_box={left_top={x=x - 1, y=0}, right_bottom={x=x + 1, y=2}},
                        get_recipe=function() return nil end}
            end
            loop = {plan_id='loop', purpose='coal-loop', order={'a', 'b'},
                    entities={a={entity=drill(1)}, b={entity=drill(2)}}}
            npc = {design_history={loop}, design={status='complete'}}
            blocks = M.blocks(npc, {x=0, y=0})
            short = M.running_out(blocks, 30)
        """)
        ore = lua.eval("blocks[1].ore")
        # the two drills' 2x2 areas overlap: tiles x 0..2 (6 tiles) counted once each
        self.assertEqual((ore.name, ore.left, ore.rate), ("coal", 6 * 600, 0.5))
        self.assertEqual(ore.minutes, 120)
        self.assertEqual(lua.eval("#short"), 0)                       # 120 minutes left: not running out
        lua.execute("for _, r in ipairs(ores) do r.amount = 50 end blocks = M.blocks(npc, {x=0, y=0})")
        self.assertEqual(lua.eval("M.running_out(blocks, 30)[1].resource"), "coal")

class LedgerTests(unittest.TestCase):
    """The factory ledger's half in the game: capacity, sampled rates and the plan's ledger block."""

    def lua(self):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().source = (ROOT / "mod" / "scripts" / "base_memory.lua").read_text(encoding="utf-8")
        lua.execute("""
            defines = {entity_status={working=1, item_ingredient_shortage=2, full_output=3, no_power=4}}
            M = assert(load(source))()
            gears = {name='iron-gear-wheel', energy=0.5, products={{name='iron-gear-wheel', amount=1}},
                     ingredients={{name='iron-plate', amount=2}}}
            plate = {name='iron-plate', energy=3.2, products={{name='iron-plate', amount=1}},
                     ingredients={{name='iron-ore', amount=1}}}
            prototypes = {recipe={['iron-plate']=plate}}
            n = 0
            function machine(kind, recipe, status, speed)
                n = n + 1
                local e = {valid=true, name=kind == 'furnace' and 'stone-furnace' or 'assembling-machine-2',
                           type=kind, status=status or 1, unit_number=n, products_finished=0,
                           crafting_speed=speed or 0.75, productivity_bonus=0,
                           bounding_box={left_top={x=n, y=0}, right_bottom={x=n + 1, y=1}}}
                e.get_recipe = function() return recipe end
                return e
            end
            function drill(status)
                n = n + 1
                return {valid=true, name='electric-mining-drill', type='mining-drill', status=status or 1,
                        unit_number=n, speed_bonus=0, productivity_bonus=0.1,
                        prototype={mining_speed=0.5},
                        mining_target={valid=true, prototype={mineable_properties={mining_time=1,
                            products={{type='item', name='iron-ore', amount=1}}}}},
                        position={x=n, y=0},
                        bounding_box={left_top={x=n, y=0}, right_bottom={x=n + 2, y=2}},
                        surface={find_entities_filtered=function() return {} end}}
            end
            function plan(id, list)
                local p = {plan_id=id, purpose='bus-base', order={}, entities={}}
                for i, e in ipairs(list) do p.order[i] = 'e' .. i; p.entities['e' .. i] = {entity=e} end
                return p
            end
        """)
        return lua

    def hand_world(self):
        """self.lua() with a world to find feeders in: chests, inserters, belts placed on tiles."""
        lua = self.lua()
        lua.execute("""
            defines.inventory = {chest=1}
            world = {inserter={}, ['mining-drill']={}, loader={}, ['loader-1x1']={}}
            surface = {find_entities_filtered=function(f) return world[f.type] or {} end}
            spot = 100
            function placed(e)
                spot = spot + 1
                e.surface, e.force = surface, 'player'
                e.position = e.position or {x=spot + 0.5, y=0.5}
                e.bounding_box = e.bounding_box or {left_top={x=e.position.x - 0.5, y=e.position.y - 0.5},
                                                    right_bottom={x=e.position.x + 0.5, y=e.position.y + 0.5}}
                return e
            end
            function chest(contents)
                return placed({valid=true, type='container',
                               get_inventory=function() return {get_contents=function() return contents end} end})
            end
            function ins(from, to) local i = placed({type='inserter', pickup_target=from, drop_target=to})
                table.insert(world.inserter, i); return i end
            function belt(into) return placed({type='transport-belt', belt_neighbours={inputs=into or {}, outputs={}}}) end
            function gear_machine() local m = machine('assembling-machine', gears)
                m.position = {x=m.bounding_box.left_top.x + 0.5, y=0.5}; return placed(m) end
        """)
        return lua

    def test_a_block_says_what_its_machines_take_by_hand(self):
        # (the user, 2026-10-29: "chests with items mean it won't be automated"; a chest an inserter fills is fed)
        lua = self.hand_world()
        lua.execute("""
            smelter = placed(machine('furnace', nil))
            from_smelter = belt(); ins(smelter, from_smelter)                -- (a belt a machine puts plates on)
            by_hand = gear_machine(); hand_chest = chest({{name='iron-plate', count=50}}); ins(hand_chest, by_hand)
            by_belt = gear_machine(); ins(from_smelter, by_belt)
            nothing = gear_machine()
            buffer = chest({{name='iron-plate', count=10}})
            from_buffer = gear_machine(); ins(buffer, from_buffer); ins(from_smelter, buffer)
            relay = chest({{name='iron-plate', count=10}})                -- (filled from a chest the engineer fills)
            from_relay = gear_machine(); ins(relay, from_relay); ins(chest({{name='iron-plate', count=90}}), relay)
            mixed_hand = gear_machine(); ins(chest({{name='iron-plate', count=5}}), mixed_hand)
            mixed_belt = gear_machine(); ins(from_smelter, mixed_belt)
            empty_hand = gear_machine(); ins(nil, empty_hand)                -- (its inserter has nothing to take from)
            -- (a generated row: a hand chest lifted onto the row's belt, the machine's inserter taking from the belt)
            row_chest = chest({{name='iron-plate', count=40}})
            row_start = belt(); row_end = belt({row_start}); ins(row_chest, row_start)
            on_row = gear_machine(); ins(row_end, on_row)
            npc = {design_history={plan('hand', {by_hand}), plan('belt', {by_belt}), plan('none', {nothing}),
                                   plan('buffer', {from_buffer}), plan('relay', {from_relay}),
                                   plan('mixed', {mixed_hand, mixed_belt}), plan('empty', {empty_hand}),
                                   plan('row', {on_row})},
                   design={status='complete'}}
            blocks = M.blocks(npc, {x=100, y=0})
        """)
        def fed(i):
            got = lua.eval(f"blocks[{i}].hand_fed")
            return None if got is None else {k: list(v.values()) for k, v in got.items()}
        gears = {"iron-gear-wheel": ["iron-plate"]}
        self.assertEqual(fed(1), gears)
        self.assertIsNone(fed(2))                       # (from a belt a machine loads)
        self.assertEqual(fed(3), gears)                 # (nothing feeds it)
        self.assertIsNone(fed(4))                       # (a chest an inserter fills from that belt)
        self.assertEqual(fed(5), gears)                 # (a chest filled from a hand-filled one)
        self.assertIsNone(fed(6))                       # (one machine of the recipe fed by belt)
        self.assertEqual(fed(7), gears)                 # (an inserter with no source is no feed)
        self.assertEqual(fed(8), gears)                 # (a belt loaded only from a hand chest)
        # (the hand chests themselves, export-local, for belts to be brought to them)
        spot = lua.eval("blocks[1].hand_chests[1]")
        self.assertEqual((spot.x, spot.y, list(spot["items"].values())),
                         (int(lua.eval("hand_chest.position.x")) - 100, 0, ["iron-plate"]))
        row = lua.eval("blocks[8].hand_chests[1]")
        self.assertEqual(row.x, int(lua.eval("row_chest.position.x")) - 100)
        self.assertIsNone(lua.eval("blocks[2].hand_chests"))

    def test_a_hand_chest_on_a_line_counts_for_its_own_item_and_through_undergrounds(self):
        lua = self.hand_world()
        lua.execute("""
            -- (a maker puts gears on one lane, a hand chest iron plate on the other: the iron is by hand)
            maker = gear_machine()
            mixed = belt(); ins(maker, mixed); ins(chest({{name='iron-plate', count=40}}), mixed)
            on_mixed = gear_machine(); ins(mixed, on_mixed)
            -- (a hand chest's belt run under something: the underground's exit reads back to its entrance)
            start = belt(); ins(chest({{name='iron-plate', count=40}}), start)
            entrance = belt({start}); entrance.type = 'underground-belt'; entrance.belt_to_ground_type = 'input'
            exit = belt(); exit.type = 'underground-belt'; exit.belt_to_ground_type = 'output'
            exit.underground_belt_neighbour = entrance
            past = gear_machine(); ins(exit, past)
            -- (a hand chest's belt passed on to another belt by an inserter)
            first = belt(); ins(chest({{name='iron-plate', count=40}}), first)
            second = belt(); ins(first, second)
            relayed = gear_machine(); ins(second, relayed)
            -- (a hand chest stocked with the wrong item still feeds the machine by hand)
            wrong = gear_machine(); ins(chest({{name='copper-plate', count=40}}), wrong)
            -- (two belts passing to each other, one loaded from a hand chest: the loop adds no source)
            loop_a = belt(); loop_b = belt(); ins(chest({{name='iron-plate', count=40}}), loop_a)
            ins(loop_a, loop_b); ins(loop_b, loop_a)
            on_loop = gear_machine(); ins(loop_b, on_loop)
            on_loop_a = gear_machine(); ins(loop_a, on_loop_a)   -- (the loaded side too)
            npc = {design_history={plan('mixed', {on_mixed}), plan('under', {past}), plan('relayed', {relayed}),
                                   plan('wrong', {wrong}), plan('loop', {on_loop}), plan('loop_a', {on_loop_a})},
                   design={status='complete'}}
            blocks = M.blocks(npc, {x=0, y=0})
        """)
        self.assertEqual(list(lua.eval("blocks[1].hand_fed['iron-gear-wheel']").values()), ["iron-plate"])
        for i in (2, 3, 4, 5, 6):
            self.assertEqual(list(lua.eval(f"blocks[{i}].hand_fed['iron-gear-wheel']").values()), ["iron-plate"], i)

    def test_what_a_belt_carries_by_hand_does_not_hang_on_which_machine_is_read_first(self):
        lua = self.hand_world()
        lua.execute("""
            -- (hand chest -> a -> inserter -> b -> inserter -> c: a machine on c reads a deep, one on b less so)
            a = belt(); ins(chest({{name='iron-plate', count=40}}), a)
            b = belt(); ins(a, b)
            c = belt(); ins(b, c)
            far = gear_machine(); ins(c, far)
            near_b = gear_machine(); ins(b, near_b)
            -- (hand chest -> la <-> lb -> lc: a machine on la read first, then one on lc)
            la = belt(); lb = belt(); lc = belt(); ins(chest({{name='iron-plate', count=40}}), la)
            ins(la, lb); ins(lb, la); ins(lb, lc)
            on_la = gear_machine(); ins(la, on_la)
            on_lc = gear_machine(); ins(lc, on_lc)
            npc = {design_history={plan('both', {far, near_b}), plan('loop', {on_la}), plan('after', {on_la, on_lc})},
                   design={status='complete'}}
            blocks = M.blocks(npc, {x=0, y=0})
        """)
        self.assertEqual(list(lua.eval("blocks[1].hand_fed['iron-gear-wheel']").values()), ["iron-plate"])
        self.assertEqual(list(lua.eval("blocks[3].hand_fed['iron-gear-wheel']").values()), ["iron-plate"])

    def test_capacity_comes_from_the_machines_as_they_stand(self):
        lua = self.lua()
        lua.execute("""
            a = machine('assembling-machine', gears); b = machine('assembling-machine', gears)
            idle = machine('furnace', nil)
            idle.previous_recipe = {name='iron-plate'}     -- a furnace with nothing to smelt right now
            fast = machine('furnace', nil, 1, 2)
            fast.previous_recipe = {name='iron-plate'}
            npc = {design_history={plan('gears', {a, b}), plan('smelt', {idle, fast}), plan('ore', {drill()})},
                   design={status='complete'}}
            blocks = M.blocks(npc, {x=0, y=0})
        """)
        gears = lua.eval("blocks[1].capacity")
        self.assertAlmostEqual(gears.makes["iron-gear-wheel"], 3.0)   # 2 x 0.75 / 0.5 s
        self.assertAlmostEqual(gears.takes["iron-plate"], 6.0)
        smelt = lua.eval("blocks[2].capacity")
        self.assertAlmostEqual(smelt.makes["iron-plate"], (0.75 + 2) / 3.2, places=3)
        self.assertAlmostEqual(lua.eval("blocks[3].capacity.makes['iron-ore']"), 0.55)   # 0.5 a second, +10%
        self.assertIsNone(lua.eval("blocks[1].measured"))   # not sampled yet

    def test_a_sample_measures_crafts_finished_and_how_the_machines_spent_their_time(self):
        lua = self.lua()
        lua.execute("""
            a = machine('assembling-machine', gears); b = machine('assembling-machine', gears, 2)
            p = plan('gears', {a, b})
            M.sample(p, 1000)                         -- the first: only where the counters stand
            first = p.ledger_samples.out30
            a.products_finished = 90                  -- 90 gears in a minute: 1.5 a second
            M.sample(p, 1000 + 3600)
            npc = {design_history={p}, design={status='complete'}}
            block = M.blocks(npc, {x=0, y=0})[1]
        """)
        self.assertIsNone(lua.eval("first"))
        m = lua.eval("block.measured")
        self.assertAlmostEqual(m.out["iron-gear-wheel"], 1.5)
        self.assertAlmostEqual(m.takes["iron-plate"], 3.0)
        self.assertAlmostEqual(m.shares["working"], 0.5)
        self.assertAlmostEqual(m.shares["starved"], 0.5)
        self.assertEqual(m.minutes, 1)
        # a machine whose recipe changed starts over: no crafts counted across the change
        lua.execute("a.get_recipe = function() return plate end; a.products_finished = 500; M.sample(p, 1000 + 7200)")
        self.assertIsNone(lua.eval("M.blocks(npc, {x=0, y=0})[1].measured.out['iron-plate']"))

    def test_the_last_half_hour_is_measured_as_it_was_not_dragged_down_by_a_slow_start(self):
        # live check 2026-10-23: an average seeded at a block's idle first minute still read a third of a
        # steady furnace's output 14 minutes on; counted between checkpoints, the window is the real one
        lua = self.lua()
        lua.execute("""
            a = machine('assembling-machine', gears)
            p = plan('gears', {a})
            for minute = 0, 45 do
                if minute > 10 then a.products_finished = a.products_finished + 60 end   -- 1 craft a second
                M.sample(p, minute * 3600)
            end
            npc = {design_history={p}, design={status='complete'}}
            m = M.blocks(npc, {x=0, y=0})[1].measured
        """)
        m = lua.eval("m")
        self.assertEqual(m.minutes, 45)
        self.assertAlmostEqual(m.out["iron-gear-wheel"], 1.0)      # the last 30 minutes, all steady
        self.assertAlmostEqual(m.out5["iron-gear-wheel"], 1.0)
        self.assertAlmostEqual(m.peak["iron-gear-wheel"], 1.0)
        self.assertAlmostEqual(m.takes["iron-plate"], 2.0)
        self.assertEqual(lua.eval("#p.ledger_samples.marks"), 7)     # 15, 20 ... 45 minutes: 30 apart

    def test_each_block_is_sampled_in_turn_about_once_a_minute(self):
        lua = self.lua()
        lua.execute("""
            one = plan('one', {machine('assembling-machine', gears)})
            two = plan('two', {machine('assembling-machine', gears)})
            going_up = plan('three', {machine('assembling-machine', gears)})
            npc = {design_history={one, two}, design={status='building', plan=going_up}}
            M.tick(npc, 100); M.tick(npc, 160)       -- one, then two
            M.tick(npc, 220)                          -- one again: not due for a minute
            early = one.ledger_samples.tick
            M.tick(npc, 100 + 3600)
            due = one.ledger_samples.tick
        """)
        g = lua.globals()
        self.assertEqual((g.early, g.due, lua.eval("two.ledger_samples.tick")), (100, 3700, 160))
        self.assertIsNone(lua.eval("going_up.ledger_samples"))      # measured once it is finished

    def belts(self):
        """Fake belts: items on each lane of the belt at each tile (`items["x,y"] = {left, right}`), yellow."""
        lua = self.lua()
        lua.execute("""
            items, asked = {}, 0
            function belt(key)
                return {valid=true, prototype={belt_speed=0.03125}, get_transport_line=function(i)
                    return {line_length=1, get_item_count=function() return items[key][i] end}
                end}
            end
            surface = {find_entities_filtered=function(f)
                asked = asked + 1
                local key = math.floor(f.area[1][1]) .. "," .. math.floor(f.area[1][2])
                return items[key] and {belt(key)} or {}
            end}
        """)
        return lua

    def test_a_line_is_measured_by_how_full_it_runs_at_its_probes(self):
        lua = self.belts()
        lua.execute("""
            line = {id='iron-plate@1,0', tiles={{1, 0, 8}, {1, 30, 8}}, probes={{1, 1}, {1, 20}}}
            items['1,1'] = {4, 4}; items['1,20'] = {1, 0}         -- full by the head, nearly empty further down
            for minute = 0, 40 do
                if minute == 35 then items['1,20'] = {4, 4} end   -- backed up lately
                M.sample_line(line, surface, 'force', minute * 3600)
            end
            m = line.measured
        """)
        m = lua.eval("m")
        self.assertEqual((m.minutes, m.rate), (40, 15))                      # a yellow belt: 15 a second
        head, down = m.probes[1], m.probes[2]
        self.assertEqual((head.x, head.y, head.f, head.f5), (1, 1, 1, 1))
        self.assertAlmostEqual(down.f, (24 * 0.125 + 6 * 1) / 30, places=3)  # since the oldest checkpoint kept (10)
        self.assertEqual(down.f5, 1)                                         # the last 5
        self.assertAlmostEqual(down.m, (24 * 0.25 + 6 * 1) / 30, places=3)  # its fuller lane: 1 of 4, then full
        self.assertEqual(down.m5, 1)
        self.assertEqual(lua.eval("asked"), 2)                               # each belt found once, then kept

    def test_a_line_recorded_without_probes_is_sampled_evenly_along_it_and_old_probes_go(self):
        lua = self.belts()
        lua.execute("""
            tiles = {}
            for y = 0, 29 do tiles[#tiles + 1] = {0, y, 8}; items['0,' .. y] = {1, 1} end
            line = {id='coal@0,0', tiles=tiles}
            M.sample_line(line, surface, 'force', 0)
            evenly, tail = #line.probes, line.probes[#line.probes][2]
            M.sample_line(line, surface, 'force', 3600)
            line.probes = {{0, 0}}                               -- reported again with fewer
            M.sample_line(line, surface, 'force', 7200)
        """)
        self.assertEqual((lua.eval("evenly"), lua.eval("tail")), (6, 29))   # the head and the end among them
        self.assertEqual(list(lua.eval("line.fill.sum").keys()), ["0,0"])
        self.assertEqual(lua.eval("#line.measured.probes"), 1)

    def test_each_line_is_sampled_in_turn_at_most_every_ten_seconds(self):
        lua = self.belts()
        lua.execute("""
            items['0,0'] = {1, 1}; items['5,0'] = {1, 1}
            npc = {force='force', character={valid=true, surface=surface},
                   belt_lines={a={id='a', tiles={{0, 0, 8}}}, b={id='b', tiles={{5, 0, 8}}}}}
            M.line_tick(npc, 60); M.line_tick(npc, 120)          -- a, then b
            M.line_tick(npc, 180)                                -- a again: not due
            early = npc.belt_lines.a.fill.tick
            M.line_tick(npc, 60 + 600)                           -- ten seconds on: not yet (its gap varies)
            still = npc.belt_lines.a.fill.tick
            M.line_tick(npc, 60 + 600 + 180)                     -- (the first gap: 3 seconds more)
            M.line_tick(npc, 60 + 600 + 240); M.line_tick(npc, 60 + 600 + 300)   -- b, then a's next gap differs
        """)
        self.assertEqual((lua.eval("early"), lua.eval("still"), lua.eval("npc.belt_lines.b.fill.tick")), (60, 60, 900))
        self.assertEqual(lua.eval("npc.belt_lines.a.fill.tick"), 840)
        self.assertEqual(lua.eval("npc.belt_lines.a.fill.next - 840"), 600 + 60 * 6)

    def test_each_lane_of_a_belt_is_measured_on_its_own(self):
        # (a belt with an item a side: iron on the left, nearly full, gears on the right, nearly empty)
        lua = self.belts()
        lua.execute("""
            items['0,5'] = {4, 0}
            line = {id='mix', tiles={{0, 0, 8}, {0, 9, 8}}, probes={{0, 5}}, sides={left='iron-plate', right='gear'}}
            for minute = 0, 12 do M.sample_line(line, surface, 'force', minute * 3600) end
            p = line.measured.probes[1]
        """)
        p = lua.eval("p")
        self.assertEqual((p.l, p.r, p.f), (1, 0, 0.5))

    def test_a_belt_with_stacks_is_measured_against_what_its_stacks_hold(self):
        # (stacks of 4 researched: a lane tile holds 16, and the belt carries four times its speed)
        lua = self.belts()
        lua.execute("""
            items['0,5'] = {8, 16}
            line = {id='stacked', tiles={{0, 0, 8}, {0, 9, 8}}, probes={{0, 5}}}
            for minute = 0, 12 do M.sample_line(line, surface, {belt_stack_size_bonus=3}, minute * 3600) end
            p = line.measured.probes[1]
            rate = line.measured.rate
        """)
        p = lua.eval("p")
        self.assertEqual((p.l, p.r, p.f, lua.eval("rate")), (0.5, 1, 0.75, 60))

    def test_a_steady_trickle_is_not_read_empty_by_a_rhythm_that_keeps_step_with_it(self):
        # (Codex, L-1: an item every 120 ticks, 32 ticks a tile; read every 600 ticks at the same phase, one tile
        # never holds it. The four tiles behind the probe, at one instant, hold what passed it over 128 ticks.)
        lua = self.belts()
        lua.execute("""
            now = 0
            function belt(key)
                local y = tonumber(key:match(',(-?%d+)'))
                return {valid=true, prototype={belt_speed=0.03125}, get_transport_line=function(i)
                    return {line_length=1, get_item_count=function()
                        if i ~= 1 then return 0 end
                        return ((now + 32 * (10 - y)) % 120 < 32) and 1 or 0
                    end}
                end}
            end
            for y = 0, 10 do items['0,' .. y] = true end
            alone = {id='alone', tiles={{0, 0, 8}, {0, 10, 8}}, probes={{0, 10}}}
            window = {id='window', tiles={{0, 0, 8}, {0, 10, 8}}, probes={{0, 10, 8}}}
            for k = 0, 40 do
                now = 60 + 600 * k
                M.sample_line(alone, surface, 'force', now); M.sample_line(window, surface, 'force', now)
            end
        """)
        self.assertEqual(lua.eval("alone.measured.probes[1].f"), 0)
        self.assertGreater(lua.eval("window.measured.probes[1].f"), 0.02)   # (about 0.27 items a lane a tile, of 8)

    def test_a_trunk_s_window_follows_its_path_round_a_bend(self):
        # (Codex, L-2: a straight step back from a bend reads another belt or none)
        lua = self.belts()
        lua.execute("""
            -- east along row 0 to x=2, then south down x=2: the probe at (2, 1) just after the bend
            tiles = {{0, 0, 4}, {1, 0, 4}, {2, 0, 8}, {2, 1, 8}, {2, 2, 8}}
            for _, t in ipairs(tiles) do items[t[1] .. ',' .. t[2]] = {1, 1} end
            items['2,-1'] = {4, 4}; items['2,-2'] = {4, 4}         -- another belt straight behind it
            line = {id='coal@0,0', kind='trunk', tiles=tiles, probes={{2, 1, 8}}}
            M.sample_line(line, surface, 'force', 0)
            read = {}
            for _, at in ipairs(line.probe_at['2,1'].at) do read[#read + 1] = at[1] .. ',' .. at[2] end
        """)
        self.assertEqual(list(lua.eval("read").values()), ["2,1", "2,0", "1,0", "0,0"])

    def test_a_probe_added_later_is_as_young_as_its_reading_not_its_line(self):
        lua = self.belts()
        lua.execute("""
            items['0,1'] = {1, 1}; items['0,20'] = {1, 1}
            line = {id='bus', tiles={{0, 0, 8}, {0, 30, 8}}, probes={{0, 1}}}
            for minute = 0, 20 do
                if minute == 18 then line.probes = {{0, 1}, {0, 20}} end   -- the lane reported again, longer
                M.sample_line(line, surface, 'force', minute * 3600)
            end
            exported = M.lines({belt_lines={bus=line}})
        """)
        m = lua.eval("line.measured")
        self.assertEqual((m.minutes, m.probes[1].min, m.probes[2].min), (20, 20, 2))
        e = lua.eval("exported[1]")
        self.assertEqual((e.id, e.head[2], e.tail[2]), ("bus", 0, 30))      # its two ends, not every tile
        self.assertIsNone(lua.eval("exported[1].tiles"))

    def test_the_plans_ledger_block_comes_back_where_the_window_now_stands(self):
        lua = self.lua()
        lua.execute("""
            p = plan('mall', {machine('assembling-machine', gears)})
            p.ledger = {kind='mall', load=0.15, ports={{item='iron-plate', dir='in', rate=2, x=110, y=220}},
                        grow={100, 200, 140, 260}}
            npc = {design_history={p}, design={status='complete'}}
            l = M.blocks(npc, {x=100, y=200})[1].ledger
        """)
        l = lua.eval("l")
        self.assertEqual((l.kind, l.load), ("mall", 0.15))
        port = l.ports[1]
        self.assertEqual((port.item, port.dir, port.x, port.y), ("iron-plate", "in", 10, 20))
        self.assertEqual(list(l.grow.values()), [0, 0, 40, 60])

    def test_the_game_keeps_a_checked_copy_of_the_plans_ledger_block(self):
        # designed_start.lua's ledger_copy, run on its own: positions arrive export-local and are kept in
        # world coordinates; anything malformed is dropped rather than trusted
        source = (ROOT / "mod" / "scripts" / "designed_start.lua").read_text(encoding="utf-8")
        start = source.index("local LEDGER_ITEMS")
        end = source.index("local function base_plan_copy")
        lua = LuaRuntime(unpack_returned_tuples=True)
        copy = lua.execute("local function finite(v) return type(v)=='number' and v==v and math.abs(v)<1e6 end\n"
                           + source[start:end] + "\nreturn ledger_copy")
        raw = lua.eval("""{kind='science', load=1.7, makes={['automation-science-pack']=0.5, bad='x'},
            ports={{item='iron-gear-wheel', dir='in', rate=0.5, x=4, y=6}, {item='x', dir='sideways'},
                   {item='copper-plate', dir='in'}},
            grow={0, 0, 10, 'far'}}""")
        out = copy(raw, lua.eval("{x=100, y=200}"))
        self.assertEqual((out.kind, out.load), ("science", 1))            # a load is a share of time: at most 1
        self.assertEqual(list(out.makes.keys()), ["automation-science-pack"])
        self.assertEqual([(p.item, p.x, p.y) for p in out.ports.values()],
                         [("iron-gear-wheel", 104, 206), ("copper-plate", None, None)])
        self.assertIsNone(out.grow)
        self.assertIsNone(copy("not a table", lua.eval("{x=0, y=0}")))


if __name__ == "__main__":
    unittest.main()
