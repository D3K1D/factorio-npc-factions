"""The corrector (small repairs) in Factorio's Lua 5.2, without Factorio."""
from pathlib import Path
import unittest

from lupa.lua52 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]


def runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().source = (ROOT / "mod" / "scripts" / "corrector.lua").read_text(encoding="utf-8")
    lua.globals().hands_source = (ROOT / "mod" / "scripts" / "hands.lua").read_text(encoding="utf-8")
    lua.execute("package.preload['scripts.hands'] = function() return assert(load(hands_source))() end")
    lua.execute("""
        storage = {}
        logged = {}
        function log(s) logged[#logged+1] = s end
        defines = {entity_status={waiting_for_source_items=1, no_power=2, working=3},
                   inventory={chest=1}, build_check_type={manual=0}}
        prototypes = {entity={['small-electric-pole']={get_max_wire_distance=function() return 7.5 end,
                                                        items_to_place_this={{name='small-electric-pole'}}},
                              ['iron-chest']={items_to_place_this={{name='iron-chest'}}},
                              inserter={items_to_place_this={{name='inserter'}}}}}
        C = assert(load(source))()
        occupied = {}
        surface = {can_place_entity=function(spec) return not occupied[spec.position.x .. ',' .. spec.position.y] end,
                   find_entities_filtered=function() return poles or {} end}
        local n = 0
        function thing(extra)
            n = n + 1
            local e = {valid=true, unit_number=n, surface=surface, position={x=0.5, y=0.5}, direction=0, status=3,
                       prototype={}, name='x', type='x'}
            for k, v in pairs(extra or {}) do e[k] = v end
            return e
        end
        function belt_with(item)
            local line = {get_item_count=function(i) return i == item and 5 or 0 end}
            return thing({type='transport-belt', get_transport_line=function() return line end})
        end
        function machine(recipe_item, output_empty)
            return thing({type='assembling-machine',
                get_output_inventory=function() return {is_empty=function() return output_empty end} end,
                get_recipe=function() return {ingredients={{type='item', name=recipe_item, amount=1}}} end})
        end
        function chest(full, limited, item)
            local inv = {count_empty_stacks=function() return full and 0 or 3 end, supports_bar=function() return true end,
                         get_bar=function() return limited and 5 or 33 end,
                         get_contents=function() return {{name=item or 'iron-plate', count=1600}} end}
            setmetatable(inv, {__len=function() return 32 end})
            return thing({type='container', name='iron-chest', get_inventory=function() return inv end})
        end
        npc = {id=1, force='f'}
    """)
    return lua


class CorrectorTests(unittest.TestCase):
    def test_it_knows_the_problems_it_can_fix(self):
        lua = runtime()
        lua.execute("""
            backwards = thing({type='inserter', status=1, pickup_target=machine('iron-plate', true),
                               drop_target=belt_with('iron-plate')})
            fine = thing({type='inserter', status=1, pickup_target=belt_with('copper-plate'),
                          drop_target=machine('iron-plate', true)})
            unpowered = thing({type='assembling-machine', status=2, prototype={electric_energy_source_prototype={}}})
        """)
        self.assertEqual(lua.eval("C.symptom(backwards)"), "backwards")
        self.assertIsNone(lua.eval("C.symptom(fine)"))
        # machine to machine (a mall chain): both ends use iron, but it is not backwards
        lua.execute("""chain = thing({type='inserter', status=1, pickup_target=machine('iron-plate', true),
                                      drop_target=machine('iron-plate', false)})
                       chain.drop_target.get_item_count = function() return 5 end""")
        self.assertIsNone(lua.eval("C.symptom(chain)"))
        self.assertEqual(lua.eval("C.symptom(chest(true, false))"), "full_chest")
        self.assertIsNone(lua.eval("C.symptom(chest(true, true))"))     # limited on purpose (mall, bus end)
        self.assertIsNone(lua.eval("C.symptom(chest(false, false))"))
        self.assertEqual(lua.eval("C.symptom(unpowered)"), "unpowered")
        # on a pole group no generator feeds (a far field never joined to the base): no power either
        lua.execute("island = thing({type='mining-drill', status=2, electric_network_id=7, "
                    "prototype={electric_energy_source_prototype={}}})")
        self.assertEqual(lua.eval("C.symptom(island, {[3]=true})"), "unpowered")
        self.assertIsNone(lua.eval("C.symptom(island, {[7]=true})"))   # its network has power: just short
        self.assertIsNone(lua.eval("C.symptom(island)"))                # nothing known: leave it

    def test_it_learns_which_fix_works(self):
        lua = runtime()
        self.assertEqual(lua.eval("C.pick('full_chest')"), "overflow_chest")   # untried, in order
        lua.execute("storage.corrector_stats['full_chest/overflow_chest'] = {tries=4, wins=0}")
        self.assertEqual(lua.eval("C.pick('full_chest')"), "empty_by_hand")    # the other is untried
        lua.execute("storage.corrector_stats['full_chest/empty_by_hand'] = {tries=4, wins=4}")
        self.assertEqual(lua.eval("C.pick('full_chest')"), "empty_by_hand")    # it worked; the other never did

    def test_an_overflow_chest_goes_where_there_is_room_and_its_inserter_has_what_it_runs_on(self):
        lua = runtime()
        lua.execute("""
            npc.force = {name='f', recipes={['burner-inserter']={enabled=true}}}
            prototypes.item = {coal={fuel_value=4000000}, stone={fuel_value=0}, ['iron-plate']={}}
            prototypes.entity['burner-inserter'] = {items_to_place_this={{name='burner-inserter'}}}
            generators, poles = {thing({electric_network_id=1})}, {}
            surface.find_entities_filtered = function(spec)
                if type(spec.type) == 'table' then return generators end
                if spec.type == 'electric-pole' then return poles end
                return {}
            end
            full = chest(true, false); occupied['1.5,0.5'] = true   -- east is taken
        """)

        def steps(target="full"):
            out = lua.eval(f"C.steps(npc, {target}, 'full_chest', 'overflow_chest')")
            return [out[i] for i in sorted(out.keys())] if out else None
        # a live pole beside it: an electric inserter
        lua.execute("poles = {thing({type='electric-pole', position={x=0.5, y=2.5}, electric_network_id=1, "
                    "prototype={get_supply_area_distance=function() return 2.5 end}})}")
        place = steps()
        self.assertEqual(place[0].name, "iron-chest")
        self.assertEqual(place[1].name, "inserter")
        # the inserter sits between the chests and picks from the full one
        self.assertEqual(abs(place[1].position.x - 0.5) + abs(place[1].position.y - 0.5), 1)
        self.assertNotEqual((place[1].position.x, place[1].position.y), (1.5, 0.5))
        # no power there: a burner inserter moving coal feeds itself; one moving stone could not run, so none
        lua.execute("poles = {}")
        self.assertIsNone(steps())
        lua.execute("coal = chest(true, false, 'coal'); stone = chest(true, false, 'stone')")
        self.assertEqual(steps("coal")[1].name, "burner-inserter")
        self.assertIsNone(steps("stone"))
        # a pole of a network no generator feeds is no power
        lua.execute("poles = {thing({type='electric-pole', position={x=0.5, y=2.5}, electric_network_id=9, "
                    "prototype={get_supply_area_distance=function() return 2.5 end}})}")
        self.assertIsNone(steps("stone"))

    def test_a_belts_store_gets_no_overflow_chest_and_a_burner_inserter_only_for_its_fuel(self):
        # (a smelting store at the end of its belt fills on purpose: then its splitter sends the rest to the bus)
        lua = runtime()
        lua.execute("""
            npc.force = {name='f', recipes={['burner-inserter']={enabled=true}}}
            npc.character = {valid=true, surface=surface, position={x=0.5, y=0.5}}
            prototypes.item = {coal={fuel_value=4000000, fuel_categories={'chemical'}},
                               cell={fuel_value=8000000000, fuel_categories={'nuclear'}}, ['iron-plate']={}}
            prototypes.entity['burner-inserter'] = {items_to_place_this={{name='burner-inserter'}},
                                                   burner_prototype={fuel_categories={chemical=true}}}
            inserters = {}
            surface.find_entities_filtered = function(spec)
                if type(spec.type) == 'table' and spec.type[1] == 'inserter' then return inserters end
                return {}
            end
            store = chest(true, false, 'coal')
            inserters = {thing({type='inserter', drop_target=store, pickup_target=thing({type='transport-belt'})})}
        """)
        self.assertIsNone(lua.eval("C.steps(npc, store, 'full_chest', 'overflow_chest')"))
        # nor is it a fault, even with the engineer short of what it holds: it takes from stores as it needs (acquire);
        # emptying it would hold back the bus's feed until it filled again
        lua.execute("""plan = {order={'s'}, entities={s={entity=store}}, stages={{id='x', entity_ids={'s'}}}}
                       npc.design_history = {plan}
                       function acquire() return nil end""")
        self.assertIsNone(lua.eval("C.symptom(store, nil, nil, function() return true end)"))
        lua.eval("C.step(npc, 1000, acquire, {short=function() return true end})")
        self.assertIsNone(lua.eval("npc.corrector.fix"))
        # a fix that does not apply hands over to the next that does, no failed try recorded
        lua.execute("loose = chest(true, false, 'iron-plate')")   # (unpowered, no fuel: no overflow chest)
        lua.execute("plan.entities.s.entity = loose; npc.corrector.next_scan = 0; npc.corrector.plan, npc.corrector.index = 1, 0")
        lua.eval("C.step(npc, 2000, acquire, {short=function() return true end})")
        self.assertEqual(lua.eval("npc.corrector.fix.fix"), "empty_by_hand")
        self.assertIsNone(lua.eval("(storage.corrector_stats or {})['full_chest/overflow_chest']"))
        # not belt-fed: a burner inserter for coal, none for a fuel its burner cannot take
        lua.execute("inserters = {}; cells = chest(true, false, 'cell')")
        self.assertEqual(lua.eval("C.steps(npc, store, 'full_chest', 'overflow_chest')[2].name"), "burner-inserter")
        self.assertIsNone(lua.eval("C.steps(npc, cells, 'full_chest', 'overflow_chest')"))

    def test_an_overflow_chest_kept_from_before_is_dropped_where_its_inserter_would_not_run(self):
        lua = runtime()
        lua.execute("""
            npc.force = {name='f', recipes={}}
            npc.character = {valid=true, surface=surface, position={x=0.5, y=0.5}}
            prototypes.item = {stone={}}
            surface.find_entities_filtered = function() return {} end
            stone = chest(true, false, 'stone')
            npc.corrector = {plan=1, index=0, next_scan=0, tries={}, gave_up={}, log={}, fix={symptom='full_chest',
                fix='overflow_chest', target=stone, steps={{kind='place', name='iron-chest', position={x=2.5, y=0.5}},
                {kind='place', name='inserter', position={x=1.5, y=0.5}, direction=12}}, step=1, key='k',
                where={x=0.5, y=0.5}, started=900}}
            function acquire() return nil end
        """)
        self.assertIsNone(lua.eval("C.step(npc, 1000, acquire)"))
        self.assertIsNone(lua.eval("npc.corrector.fix"))
        self.assertTrue(any("drops the overflow chest" in str(v) for v in lua.eval("logged").values()))

    def test_an_overflow_chest_whose_pole_is_lost_meanwhile_is_dropped_before_its_inserter(self):
        lua = runtime()
        lua.execute("""
            npc.force = {name='f', recipes={}}
            npc.character = {valid=true, surface=surface, position={x=0.5, y=0.5}}
            prototypes.item = {['iron-plate']={}}
            generators = {thing({electric_network_id=1})}
            poles = {thing({type='electric-pole', position={x=0.5, y=2.5}, electric_network_id=1,
                            prototype={get_supply_area_distance=function() return 2.5 end}})}
            surface.find_entities_filtered = function(spec)
                if type(spec.type) == 'table' and spec.type[1] == 'inserter' then return {} end
                if type(spec.type) == 'table' then return generators end
                if spec.type == 'electric-pole' then return poles end
                return {}
            end
            full = chest(true, false)
            npc.corrector = {plan=1, index=0, next_scan=1e9, tries={}, gave_up={}, log={}}
            npc.corrector.fix = {symptom='full_chest', fix='overflow_chest', target=full,
                steps=C.steps(npc, full, 'full_chest', 'overflow_chest'), step=1, key='k', where={x=0.5, y=0.5},
                started=900}
            function acquire() return nil end
        """)
        job, meta = lua.eval("C.step(npc, 1000, acquire)")
        self.assertEqual(job.kind, "build_run")   # (powered: chest and inserter in one walk)
        lua.execute("poles = {}")                  # (its pole lost before the walk got there)
        self.assertIsNone(lua.eval("C.step(npc, 1001, acquire)"))
        self.assertIsNone(lua.eval("npc.corrector.fix"))

    def test_a_full_store_is_no_fault_while_the_engineer_has_plenty_of_what_it_holds(self):
        lua = runtime()
        lua.execute("""
            stone = chest(true, false, 'stone')
            carried = {stone=500}
            function short(item) return (carried[item] or 0) < 200 end
        """)
        self.assertIsNone(lua.eval("C.symptom(stone, nil, nil, short)"))   # (its drill waits: nothing is lost)
        lua.execute("carried.stone = 10")
        self.assertEqual(lua.eval("C.symptom(stone, nil, nil, short)"), "full_chest")

    def test_a_backwards_inserter_is_rotated_and_the_fix_judged(self):
        lua = runtime()
        lua.execute("""
            bad = thing({type='inserter', status=1, direction=4, pickup_target=machine('iron-plate', true),
                         drop_target=belt_with('iron-plate')})
            plan = {order={'a'}, entities={a={entity=bad}}, stages={{id='s', entity_ids={'a'}}}}
            npc.design_history = {plan}
            function acquire() return nil end
        """)
        self.assertIsNone(lua.eval("C.step(npc, 1000, acquire)"))   # the scan finds it and plans the fix
        job, meta = lua.eval("C.step(npc, 1001, acquire)")
        self.assertEqual((job.kind, job.direction), ("configure_direction", 12))
        lua.execute("C.on_receipt(npc, {upkeep='repair', step=1}, {status='complete'}, 1100)")
        self.assertIsNone(lua.eval("C.step(npc, 1101, acquire)"))   # done: wait, then judge
        lua.execute("bad.direction = 12; bad.status = 3")            # the game shows it working now
        lua.eval("C.step(npc, 1101 + 3600, acquire)")
        self.assertEqual(lua.eval("storage.corrector_stats['backwards/rotate'].wins"), 1)
        self.assertEqual(lua.eval("C.summary(npc)[1].result"), "fixed")


class LaneTests(unittest.TestCase):
    """Wrong items on a belt lane (run 32: coal on the iron ore lane after biters broke the ore belt) are picked off
    along the line: by the plan's lane contract, or where there is none, when the machines it feeds starve beside a
    belt whose every lane holds only what they cannot use."""

    def runtime(self):
        lua = runtime()
        lua.execute("""
            defines.entity_status.no_ingredients = 4
            -- (Factorio 2.1 lists a recipe's categories; 2.0 had one, and a 2.1 prototype refuses the old key)
            prototypes.recipe = {['iron-plate']=setmetatable({categories={'smelting'}, ingredients={{type='item', name='iron-ore'}}},
                                     {__index=function(_, k) error("LuaRecipePrototype doesn't contain key " .. k) end}),
                                 ['copper-plate']={category='smelting', ingredients={{type='item', name='copper-ore'}}},
                                 gear={category='crafting', ingredients={{type='item', name='iron-plate'}}}}
            prototypes.item = {coal={type='item'}, ['iron-ore']={type='item'}}
            -- a belt whose lanes hold these items ({name=count} each), linked to the belts after it
            function lanes(a, b, extra)
                local held = {a or {}, b or {}}
                local function line(i)
                    return {get_contents=function()
                                local out = {}
                                for name, count in pairs(held[i]) do
                                    if count > 0 then out[#out + 1] = {name=name, count=count} end
                                end
                                table.sort(out, function(x, y) return x.name < y.name end)
                                return out
                            end,
                            get_item_count=function(name) return held[i][name] or 0 end}
                end
                local e = thing({type='transport-belt', held=held, belt_neighbours={inputs={}, outputs={}}})
                e.get_transport_line = function(i) return line(i) end
                for k, v in pairs(extra or {}) do e[k] = v end
                return e
            end
            function furnace() return thing({type='furnace', name='stone-furnace', status=4,
                prototype={crafting_categories={smelting=true}}}) end
        """)
        return lua

    def test_a_contract_says_what_is_wrong_on_each_lane(self):
        lua = self.runtime()
        lua.execute("""
            record = {lanes={[2]={['iron-ore']=true}}}            -- the left lane has no contract
            jammed = lanes({coal=4}, {coal=3, ['iron-ore']=1})
            clean = lanes({coal=4}, {['iron-ore']=4})
        """)
        self.assertEqual(lua.eval("C.symptom(jammed, nil, record)"), "wrong_lane")
        self.assertIsNone(lua.eval("C.symptom(clean, nil, record)"))
        self.assertIsNone(lua.eval("C.symptom(jammed)"))            # no contract: nothing to judge by
        wrong = lua.eval("C.wrong_items(jammed, record)")
        self.assertEqual([(wrong[i].line, wrong[i].name, wrong[i].count) for i in sorted(wrong.keys())],
                         [(2, "coal", 3)])

    def test_a_starving_furnace_beside_a_belt_of_only_coal_is_a_blocked_feed(self):
        lua = self.runtime()
        lua.execute("""
            function feed(a, b) return thing({type='inserter', status=1, pickup_target=lanes(a, b),
                                              drop_target=furnace()}) end
            both = feed({coal=4}, {coal=4})
            one_free = feed({coal=4}, {})                       -- ore has not come yet: nothing blocks it
            some_ore = feed({coal=4}, {coal=2, ['iron-ore']=1})
            working = feed({coal=4}, {coal=4}); working.drop_target.status = 3
        """)
        self.assertEqual(lua.eval("C.symptom(both)"), "blocked_feed")
        self.assertIsNone(lua.eval("C.symptom(one_free)"))
        self.assertIsNone(lua.eval("C.symptom(some_ore)"))
        self.assertIsNone(lua.eval("C.symptom(working)"))
        copper = lua.eval("C.symptom(feed({coal=4}, {['copper-ore']=1}))")   # (smeltable by the 2.0 key too)
        self.assertIsNone(copper)

    def test_a_clean_up_picks_the_line_clean_then_is_judged(self):
        lua = self.runtime()
        lua.execute("""
            -- the column's head (fed from the side by the coal line, not from behind), and its tail
            coal_line = lanes({}, {coal=4}, {direction=12})
            head = lanes({coal=4}, {coal=4}, {direction=8})
            tail = lanes({}, {coal=2}, {direction=8})      -- its fuel lane drained: the jammed one looks waiting
            head.belt_neighbours = {inputs={coal_line}, outputs={tail}}
            tail.belt_neighbours = {inputs={head}, outputs={}}
            coal_line.belt_neighbours = {inputs={}, outputs={head}}
            ins = thing({type='inserter', status=1, pickup_target=head, drop_target=furnace()})
            plan = {order={'i', 'h', 't'}, entities={i={entity=ins}, h={entity=head}, t={entity=tail}},
                    stages={{id='s', entity_ids={'i', 'h', 't'}}}}
            npc.design_history = {plan}
            npc.character = {valid=true, position={x=0.5, y=0.5}}
            function acquire() return nil end
        """)
        self.assertIsNone(lua.eval("C.symptom(tail)"))               # seen alone, the tail is not blocked
        self.assertIsNone(lua.eval("C.step(npc, 1000, acquire)"))   # found, and the clean-up planned
        self.assertEqual(lua.eval("npc.corrector.fix.symptom"), "blocked_feed")
        self.assertEqual(lua.eval("#npc.corrector.fix.belts"), 2)    # the column, not the coal line joining it
        picked = []
        for n in range(8):
            out = lua.eval(f"C.step(npc, {1001 + n}, acquire)")
            if out is None:
                break
            job, meta = out
            self.assertEqual((job.kind, job.item, job.up_to, job.line), ("belt_pickup", "coal", True, None))
            self.assertTrue(meta.sweep)
            lua.execute("local j = ...; j.target.held[1].coal = 0; j.target.held[2].coal = 0", job)   # both lanes
            lua.execute(f"C.on_receipt(npc, {{upkeep='repair', step=1, sweep=true}}, "
                        f"{{status='complete', item='coal', count={job.count}}}, {1001 + n})")
            picked.append(job.count)
        self.assertEqual(picked, [8, 2])                              # the head, then the tail's jammed lane
        self.assertEqual(lua.eval("npc.corrector.fix.picked.coal"), 10)
        self.assertIsNone(lua.eval("C.step(npc, 2000, acquire)"))      # clean: wait, then judge
        lua.eval("C.step(npc, 2000 + 3600, acquire)")
        self.assertEqual(lua.eval("C.summary(npc)[1].result"), "not fixed")   # the furnace still starves
        lua.execute("""head.held[1].coal = 4; head.held[2]['iron-ore'] = 4
                       ins.status = 3; ins.drop_target.status = 3
                       npc.corrector.next_scan = 0""")
        lua.eval("C.step(npc, 9000, acquire)")                         # nothing wrong is found now
        self.assertIsNone(lua.eval("npc.corrector.fix"))

    def test_a_belt_with_a_contract_is_cleaned_lane_by_lane_along_every_branch(self):
        lua = self.runtime()
        lua.execute("""
            side = lanes({}, {coal=3}, {direction=12})
            head = lanes({coal=4}, {coal=4}, {direction=8})
            side.belt_neighbours = {inputs={}, outputs={head}}
            head.belt_neighbours = {inputs={side}, outputs={}}
            plan = {order={'s', 'h'}, entities={s={entity=side, lanes={[1]={coal=true}, [2]={coal=true}}},
                                                h={entity=head, lanes={[1]={coal=true}, [2]={['iron-ore']=true}}}},
                    stages={{id='s', entity_ids={'s', 'h'}}}}
            npc.design_history = {plan}
            npc.character = {valid=true, position={x=0.5, y=0.5}}
            function acquire() return nil end
        """)
        lua.eval("C.step(npc, 1000, acquire)")
        self.assertEqual(lua.eval("npc.corrector.fix.symptom"), "wrong_lane")
        self.assertEqual(lua.eval("#npc.corrector.fix.belts"), 2)
        job, meta = lua.eval("C.step(npc, 1001, acquire)")
        self.assertEqual((job.item, job.line, job.count), ("coal", 2, 4))   # only the ore lane's coal

    def test_a_building_given_up_on_is_looked_at_again_after_a_while(self):
        lua = self.runtime()
        lua.execute("""
            ins = thing({type='inserter', status=1, pickup_target=lanes({coal=4}, {coal=4}), drop_target=furnace()})
            plan = {order={'i'}, entities={i={entity=ins}}, stages={{id='s', entity_ids={'i'}}}}
            npc.design_history = {plan}
            npc.corrector = {plan=1, index=0, next_scan=0, tries={}, gave_up={[ins.unit_number .. ':blocked_feed']=100}, log={}}
            function acquire() return nil end
        """)
        lua.eval("C.step(npc, 1000, acquire)")
        self.assertIsNone(lua.eval("npc.corrector.fix"))
        lua.execute("npc.corrector.next_scan = 0")
        lua.eval("C.step(npc, 100 + 216000, acquire)")
        self.assertEqual(lua.eval("npc.corrector.fix.symptom"), "blocked_feed")


class PoleBridgeTests(unittest.TestCase):
    """A building on no live network: the gap between its network (or itself) and a live pole is closed with poles
    got at once and put down in one walk, weighed against what it brings back (run 59, 2026-10-26: a coal field's own
    pole line ended 8.5 tiles short of the engines, and a line from the drill itself, a pole a trip, took 32 minutes)."""

    def runtime(self):
        lua = runtime()
        lua.execute("""
            prototypes.entity['medium-electric-pole'] = {get_max_wire_distance=function() return 9 end,
                                                        items_to_place_this={{name='medium-electric-pole'}}}
            npc.force = {name='f', recipes={}}
            npc.character = {valid=true, surface=surface}
            generators = {thing({electric_network_id=1})}
            surface.find_entities_filtered = function(spec)
                if type(spec.type) == 'table' then return generators end
                if spec.type == 'electric-pole' then return poles end
                return {}
            end
            standing = {}
            surface.find_entity = function(name, p) return standing[name .. '@' .. p.x .. ',' .. p.y] end
            function pole(x, y, net) return thing({name='small-electric-pole', type='electric-pole',
                                                   position={x=x, y=y}, electric_network_id=net}) end
            function drill(x, y, net) return thing({name='electric-mining-drill', type='mining-drill', status=2,
                position={x=x, y=y}, electric_network_id=net, prototype={electric_energy_source_prototype={}},
                bounding_box={left_top={x=x - 1.5, y=y - 1.5}, right_bottom={x=x + 1.5, y=y + 1.5}}}) end
            -- the base's live pole at the origin; a coal field 76 tiles south on its own line of poles, 9 short of it
            poles = {pole(0.5, 0.5, 1)}
            for k = 0, 9 do poles[#poles + 1] = pole(0.5, 9.5 + 7 * k, 16) end
            field = {drill(0.5, 76.5, 16), drill(4.5, 76.5, 16), drill(0.5, 80.5, 16), drill(4.5, 80.5, 16)}
            plan = {order={}, entities={}, stages={{id='power', entity_ids={}, status='complete'}}}
            for k, d in ipairs(field) do plan.order[k] = 'd' .. k; plan.entities['d' .. k] = {entity=d, stage='power'} end
            npc.design_history = {plan}
        """)
        return lua

    def steps(self, lua, target="field[1]"):
        steps = lua.eval(f"C.steps(npc, {target}, 'unpowered', 'pole_bridge')")
        return [steps[i] for i in sorted(steps.keys())] if steps else None

    def test_the_gap_between_its_network_and_a_live_one_is_closed(self):
        lua = self.runtime()
        line = self.steps(lua)
        self.assertEqual(len(line), 1)   # (from the drill itself: eleven)
        p = line[0].position
        self.assertLessEqual(((p.x - 0.5) ** 2 + (p.y - 9.5) ** 2) ** 0.5, 7.0)   # within reach of its line's end
        self.assertLessEqual(((p.x - 0.5) ** 2 + (p.y - 0.5) ** 2) ** 0.5, 7.0)   # and of the live pole
        lua.execute("lonely = drill(0.5, 76.5, nil)")   # on no network: from beside it, as before
        self.assertGreater(len(self.steps(lua, "lonely")), 9)
        # medium poles once researched: no wood, farther apart
        lua.execute("npc.force.recipes['medium-electric-pole'] = {enabled=true}")
        medium = self.steps(lua, "lonely")
        self.assertEqual({s.name for s in medium}, {"medium-electric-pole"})
        lua.execute("npc.force.recipes['medium-electric-pole'] = nil")
        self.assertLess(len(medium), len(self.steps(lua, "lonely")))

    def test_its_poles_are_got_at_once_and_put_down_in_one_walk(self):
        lua = self.runtime()
        lua.execute("""
            asked = {}
            have = 0
            function acquire(npc, item, need) asked[#asked + 1] = need
                if have >= need then return nil end
                if crafting then return nil, 'crafting:' .. item end
                return {kind='craft', item=item, count=need - have} end
            poles = {pole(0.5, 0.5, 1)}   -- (the field's own line gone: a line from the drill)
            field[1].electric_network_id = nil
        """)
        self.assertIsNone(lua.eval("C.step(npc, 1000, acquire)"))
        n = lua.eval("#npc.corrector.fix.steps")
        self.assertGreater(n, 3)
        job, meta = lua.eval("C.step(npc, 1001, acquire)")
        self.assertEqual((job.kind, lua.eval("asked[1]")), ("craft", n))   # all of them, once
        self.assertEqual(job.deadline, 1000 + 18000)   # (a fetch ends with the repair's time too)
        lua.execute("crafting = true")
        for t in range(20):   # (crafting in the background: waiting, not failing)
            self.assertIsNone(lua.eval(f"C.step(npc, {1002 + t}, acquire)"))
        self.assertIsNone(lua.eval("npc.corrector.fix.failures"))
        lua.execute(f"have = {n}")
        job, meta = lua.eval("C.step(npc, 1100, acquire)")
        self.assertEqual((job.kind, len(job.placements), meta.run), ("build_run", n, True))
        self.assertEqual(job.deadline, 1000 + 18000)   # (the walk ends with the repair's time)
        # the walk put down all but the last: the last alone next
        lua.execute(f"""placed = {{}}
            for k = 1, {n - 1} do placed[k] = {{id='fix-' .. k, entity=thing({{name='small-electric-pole'}})}} end
            C.on_receipt(npc, meta_run, {{status='failed', reason='blocked', placed=placed}}, 1200)""".replace(
            "meta_run", "{upkeep='repair', run=true}"))
        self.assertEqual(lua.eval("npc.corrector.fix.step"), n)
        self.assertEqual(lua.eval("#plan.order"), 4 + n - 1)   # (each placed pole kept with the plan)
        job, meta = lua.eval("C.step(npc, 1201, acquire)")
        self.assertEqual((job.kind, meta.step, job.deadline), ("build", n, 1000 + 18000))

    def test_a_walk_cut_short_keeps_what_it_put_down_and_the_rest_goes_one_at_a_time(self):
        lua = self.runtime()
        lua.execute("""
            function acquire() return nil end
            poles = {pole(0.5, 0.5, 1)}
            field[1].electric_network_id = nil
        """)
        lua.eval("C.step(npc, 1000, acquire)")
        n = lua.eval("#npc.corrector.fix.steps")
        job, meta = lua.eval("C.step(npc, 1001, acquire)")
        self.assertEqual(job.kind, "build_run")
        # (cut short: the receipt lists nothing, yet the first two poles stand where they were to go)
        lua.execute("""for i = 1, 3 do local s = npc.corrector.fix.steps[i]   -- (the third another force's)
                           standing[s.name .. '@' .. s.position.x .. ',' .. s.position.y] =
                               thing({name=s.name, force=(i < 3) and npc.force or {name='enemy'}}) end
                       field[1].valid = false   -- (the drill itself lost meanwhile: what stands is kept all the same)
                       C.on_receipt(npc, {upkeep='repair', run=true}, {status='failed', reason='job_deadline'}, 1100)
                       field[1].valid = true""")
        self.assertEqual(lua.eval("npc.corrector.fix.step"), 3)
        self.assertEqual(lua.eval("#plan.order"), 4 + 2)
        job, meta = lua.eval("C.step(npc, 1101, acquire)")
        self.assertEqual((job.kind, meta.step), ("build", 3))   # (the rest one at a time: the builder clears its spot)
        self.assertGreater(n, 3)

    def test_a_big_repair_waits_while_a_stage_is_built_and_none_runs_forever(self):
        lua = self.runtime()
        lua.execute("""
            function acquire() return nil end
            poles = {pole(0.5, 0.5, 1)}
            field[1].electric_network_id = nil   -- (one drill on no network, far out: many poles for one building)
            npc.design = {status='building'}
        """)
        self.assertIsNone(lua.eval("C.step(npc, 1000, acquire)"))
        self.assertIsNone(lua.eval("npc.corrector.fix"))
        self.assertTrue(any("waits for the build" in str(s) for s in lua.eval("logged").values()))
        lua.execute("npc.corrector.next_scan = 0; npc.design = nil")   # between builds: under way
        lua.eval("C.step(npc, 2000, acquire)")
        self.assertEqual(lua.eval("npc.corrector.fix.fix"), "pole_bridge")
        # not done within REPAIR_FOR: failed, and judged so
        lua.eval("C.step(npc, 2000 + 18001, acquire)")
        self.assertIsNone(lua.eval("npc.corrector.fix"))
        self.assertEqual(lua.eval("C.summary(npc)[1].result"), "not fixed")
        self.assertEqual(lua.eval("C.summary(npc)[1].reason"), "took too long")
        # a short gap is closed while building: its four drills are worth more than one pole
        lua.execute("""poles = {pole(0.5, 0.5, 1)}
            for k = 0, 9 do poles[#poles + 1] = pole(0.5, 9.5 + 7 * k, 16) end
            field[1].electric_network_id = 16
            npc.design = {status='building'}; npc.corrector.next_scan = 0; npc.corrector.gave_up = {}
            npc.corrector.backoff = {}   -- (a changed field: its place's back-off lifted too)""")
        lua.eval("C.step(npc, 30000, acquire)")
        self.assertEqual(lua.eval("#npc.corrector.fix.steps"), 1)

    def test_what_waits_for_a_build_is_weighed_again_in_the_next(self):
        lua = self.runtime()
        lua.execute("""
            function acquire() return nil end
            poles = {pole(0.5, 0.5, 1)}
            field[1].electric_network_id = nil
            npc.design = {status='building', plan={plan_id='bus-base-power-1', order={}, entities={}, stages={}}}
        """)
        lua.eval("C.step(npc, 1000, acquire)")
        self.assertIsNone(lua.eval("npc.corrector.fix"))
        waits = lambda: sum("waits for the build" in str(v) for v in lua.eval("logged").values())
        self.assertEqual(waits(), 1)
        lua.execute("npc.corrector.next_scan = 0; npc.corrector.plan, npc.corrector.index = 1, 0")
        lua.eval("C.step(npc, 2000, acquire)")
        self.assertEqual(waits(), 1)   # (the same build: still waiting, not laid out again)
        # the next stage straight after: weighed again (and here still too big: it waits again)
        lua.execute("""npc.design = {status='building', plan={plan_id='bus-base-feed-1', order={}, entities={}, stages={}}}
                       npc.corrector.next_scan = 0; npc.corrector.plan, npc.corrector.index = 1, 0""")
        lua.eval("C.step(npc, 3000, acquire)")
        self.assertEqual(waits(), 2)


class AwayTests(unittest.TestCase):
    """Out at far work, a repair far from the engineer waits for its return, as supplies do (run 60, 2026-10-26: one
    pole near home pulled it 230 tiles back from the coal field it was clearing), unless the plan being built needs it."""

    def test_a_repair_far_from_the_engineer_at_far_work_waits_for_its_return(self):
        lua = runtime()
        lua.execute("""
            npc.character = {valid=true, surface=surface, position={x=0.5, y=300.5}}
            bad = thing({type='inserter', status=1, direction=4, pickup_target=machine('iron-plate', true),
                         drop_target=belt_with('iron-plate')})
            plan = {order={'a'}, entities={a={entity=bad, stage='s'}}, stages={{id='s', entity_ids={'a'}}}}
            npc.design_history = {plan}
            function acquire() return nil end
            away = {away=true, near=64}
        """)
        lua.eval("C.step(npc, 1000, acquire, away)")
        self.assertIsNone(lua.eval("npc.corrector.fix"))
        lua.execute("npc.corrector.next_scan = 0; npc.corrector.plan, npc.corrector.index = 1, 0; "
                    "npc.character.position = {x=10.5, y=20.5}")   # (near it now; the next pass of the scan)
        lua.eval("C.step(npc, 2000, acquire, away)")
        self.assertEqual(lua.eval("npc.corrector.fix.fix"), "rotate")
        # the plan being built needs it: no waiting, however far
        lua.execute("""npc.corrector = nil; npc.character.position = {x=0.5, y=300.5}
            plan.stages[1].status = 'complete'; npc.design_history = {}
            npc.design = {status='building', plan=plan}""")
        lua.eval("C.step(npc, 3000, acquire, away)")
        self.assertEqual(lua.eval("npc.corrector.fix.fix"), "rotate")


if __name__ == "__main__":
    unittest.main()
