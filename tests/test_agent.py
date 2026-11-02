"""The strategist's side in the game (agent.lua), in Factorio's Lua 5.2 with the game stubbed."""
from pathlib import Path
import unittest

from lupa.lua52 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]


def runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().source = (ROOT / "mod" / "scripts" / "agent.lua").read_text(encoding="utf-8")
    lua.globals().aggro_source = (ROOT / "mod" / "scripts" / "aggro.lua").read_text(encoding="utf-8")
    lua.globals().war_source = (ROOT / "mod" / "scripts" / "war.lua").read_text(encoding="utf-8")
    lua.globals().recipes_source = (ROOT / "mod" / "scripts" / "recipes.lua").read_text(encoding="utf-8")
    lua.globals().armory_source = (ROOT / "mod" / "scripts" / "armory.lua").read_text(encoding="utf-8")
    for name in ("team_world", "team_research", "radio", "team_war", "priorities", "progression", "combat_odds",
                 "hands"):
        lua.globals()[name + "_source"] = (ROOT / "mod" / "scripts" / (name + ".lua")).read_text(encoding="utf-8")
    lua.execute("""
        for _, name in ipairs({'team_world', 'team_research', 'radio', 'team_war', 'priorities', 'progression',
                               'combat_odds', 'hands'}) do
            package.preload['scripts.' .. name] = function() return assert(load(_G[name .. '_source']))() end
        end
        package.preload['scripts.team_waves'] = function() return {nest_of=function() return nil end} end
        package.preload['scripts.aggro'] = function() return assert(load(aggro_source))() end
        package.preload['scripts.war'] = function() return assert(load(war_source))() end
        package.preload['scripts.recipes'] = function() return assert(load(recipes_source))() end
        package.preload['scripts.armory'] = function() return assert(load(armory_source))() end
        -- what its side knows: places exact (fuzz off), players seen unless a test says not
        fuzz, unseen, spawn = 0, false, {x=0, y=0}
        package.preload['scripts.intel'] = function()
            return {roughly=function(npc, key, at) return {x=at.x + fuzz, y=at.y} end,
                    spawn_of=function() return {x=spawn.x, y=spawn.y} end,
                    sees=function() return not unseen end, watch=function() end}
        end
        -- the driving autopilot: off unless a test gives it a vehicle
        can_drive = false
        package.preload['scripts.drive'] = function()
            return {can=function() return can_drive end, vehicle_at=function() return parked_at end,
                    start=function(npc, goal, near, tick, opts) npc.drive = {goal={x=goal.x, y=goal.y}, near=near, opts=opts} end}
        end
        canceled = {}
        package.preload['scripts.worker'] = function()
            return {cancel=function(npc, reason) canceled[#canceled+1] = reason end}
        end
        -- routes: straight to the goal (the planner has its own tests)
        package.preload['scripts.combat_routes'] = function()
            local function route(npc, to, spare, nests, wide)
                routed_wide[#routed_wide+1] = wide or false
                return {points={{x=to.x, y=to.y}}, k=1, known={}, goal={x=to.x, y=to.y},
                        safe=not (wide and no_way_round), wide=wide,
                        crossings=wide and 0 or nests_crossed}
            end
            return {for_npc=route, request=route,
                    new_sighting=function() return false end, look=function() return false end,
                    block=function(npc, p) blocked[#blocked+1] = p end}
        end
        -- where forces stand: set per pair in a test (relations[a.name..'|'..b.name]), peace otherwise
        relations = {}
        blocked = {}
        routed_wide, no_way_round, nests_crossed = {}, false, 0
        declarations = {}
        to_players = {}
        package.preload['scripts.diplomacy'] = function()
            return {state=function(a, b) return relations[a.name .. '|' .. b.name] or 'peace' end,
                    declare=function(npc, force, kind)
                        declarations[#declarations+1] = force.name .. ':' .. kind
                        relations[npc.force.name .. '|' .. force.name] = kind == 'ally' and 'allied' or kind
                        return true end,
                    npc_to_player=function(npc, player, action)
                        to_players[#to_players+1] = player.name .. ':' .. action
                        if action == 'declare_war' then relations[npc.force.name .. '|' .. player.force.name] = 'war' end
                        return true end}
        end
        package.preload['scripts.character'] = function()
            return {social_summary=function() return {} end, name_of=function(k) return tostring(k) end}
        end
        package.preload['scripts.personality'] = function()
            return {observe=function() return {traits={'cautious'}} end}
        end
        declared = {}
        package.preload['scripts.society'] = function()
            return {declare_war=function(npc, other) declared[#declared+1] = other.name; return true end}
        end
        offers = {}
        package.preload['scripts.combat'] = function() return {equip_now=function() return true end,
            stepping_out=function() return false end} end
        package.preload['scripts.player_trade'] = function()
            return {offer=function(npc, player, terms, large)
                offers[#offers+1] = terms; return {id=7, status='pending_player'} end}
        end
        package.preload['scripts.base_memory'] = function()
            return {threats=function() return {} end}
        end
        Agent = assert(load(source))()
        storage = {npcs={}}
        logged = {}
        function log(t) logged[#logged+1] = t end
        game = {tick=100, connected_players={}, players={}, print=function(t) printed = t end}
        function game.get_player(i) return game.players[i] end
        defines = {inventory={chest=1, character_main=1, character_guns=2, character_ammo=3, character_armor=5}}
        prototypes = {item={['gun-turret']={}, ['firearm-magazine']={}, ['wooden-chest']={}, ['stone-wall']={},
                            ['iron-plate']={}}}
        said = {}
        tags = {}
        andre_force = {name='player', add_chart_tag=function(s, tag) tags[#tags+1] = tag end}
        andre = {valid=true, index=1, name='Andre', position={x=100, y=50}, force=andre_force,
                 print=function(t) said[#said+1] = t end}
        game.players[1] = andre
        game.connected_players = {andre}
        held = {}
        inventory = {get_contents=function() return {} end,
                     get_item_count=function(spec) return held[type(spec) == 'table' and spec.name or spec] or 0 end}
        busy_biters = 0
        surface = {find_non_colliding_position=function(name, p) return {x=p.x+1, y=p.y} end,
                   count_entities_filtered=function(f) return busy_biters end,
                   find_entities_filtered=function() return {} end}
        ada_force = {name='npc-faction-1', get_friend=function(f) return true end, recipes={},
                     get_cease_fire=function() return true end, current_research=nil,
                     is_chunk_charted=function() return true end}
        -- a recipe researched, made by hand (a magazine from 4 plates, as in the base game)
        local INGREDIENTS = {['firearm-magazine']={{type='item', name='iron-plate', amount=4}}}
        function unlock(name, ingredients)
            ada_force.recipes[name] = {name=name, enabled=true, category='crafting', energy=1,
                ingredients=ingredients or INGREDIENTS[name] or {}, products={{type='item', name=name, amount=1}}}
        end
        npc = {id=1, name='Ada', force=ada_force, home={x=0, y=0},
               character={valid=true, health=250, max_health=250, position={x=0, y=0}, surface=surface,
                          prototype={crafting_categories={crafting=true}},
                          get_main_inventory=function() return inventory end,
                          get_inventory=function(i) if i == defines.inventory.character_main then return inventory end end},
               design={status='complete', purpose='coal-supply'}}
        storage.npcs[1] = npc
        active = false
        begun = {}
        Agent.designed = {
            count=function(_, name) return held[name] or 0 end,
            active=function() return active end,
            begin=function(_, tick, goal, purpose, at, order) begun[#begun+1] = purpose; last_order = order
                      npc.design = {status='waiting', purpose=purpose} end,
            acquire=function(_, item, n) if (held[item] or 0) >= n then return nil end
                        return {kind='craft', recipe=item, count=n} end,
            completed=function() return {'coal-supply'} end}
        function step()
            local out = {Agent.next(npc, game.tick)}
            return out[1], out[2]
        end
    """)
    return lua


def step(lua):
    out = lua.eval("{step()}")
    return out[1], out[2]


class AgentTests(unittest.TestCase):
    def test_it_thinks_when_there_is_something_to_think_about(self):
        # (the token audit, 2026-10-23: a routine thought every 3 minutes whatever happened, and every event
        # at once, were most of the AI calls)
        lua = runtime()
        lua.execute("""
            npc.agent = {events={}, agenda={}, notes={}, results={}, seq=0, next_initiative=1e9}
            function at(tick)                     -- the companion answered the last thought; the game ticks on
                game.tick = tick; storage.agent_bridge_tick = tick; npc.agent.pending = nil
                Agent.tick(npc, tick)
                return npc.agent.pending and npc.agent.pending.reason or 'none'
            end""")
        at = lambda tick: lua.eval(f"at({tick})")
        self.assertEqual(at(100), "none")                         # (the first heartbeat is 3 minutes off)
        self.assertEqual(at(100 + 10800), "routine")
        self.assertEqual(at(100 + 2 * 10800), "none")             # nothing moved on: no thought
        self.assertEqual(at(100 + 3 * 10800), "none")
        self.assertEqual(at(100 + 10800 + 36000), "routine")      # but one every 10 minutes
        lua.execute("npc.design.status = 'building'")             # building as planned: not news
        self.assertEqual(at(100 + 10800 + 36000 + 10800), "none")
        lua.execute("npc.agent.agenda = {{tool='stockpile', args={item='gun-turret', count=10}}}")
        self.assertEqual(at(100 + 10800 + 36000 + 2 * 10800), "routine")   # a task of its own: news
        t = 100 + 10800 + 36000 + 2 * 10800
        lua.execute("Agent.event(npc, {kind='attacked', by='enemy'})")
        self.assertEqual(at(t + 600), "none")                     # a fight's reports gather...
        lua.execute("Agent.event(npc, {kind='attacked', by='enemy'})")
        self.assertEqual(at(t + 3600), "events")                  # ...into one thought a minute
        self.assertEqual(lua.eval("#npc.agent.pending.events"), 2)
        lua.execute("Agent.event(npc, {kind='chat', from='Andre', text='Ada, status?'})")
        self.assertEqual(at(t + 3700), "events")                  # a player talking is answered at once

    def test_a_finished_plan_wakes_nobody_and_rides_along_with_the_next_thought(self):
        # (replay 2026-10-23: 24 of 28 thoughts were engineers waking because they had finished a plan)
        lua = runtime()
        lua.execute("""
            npc.agent = {events={}, agenda={}, notes={}, results={}, seq=0, next_initiative=1e9}
            function at(tick)
                game.tick = tick; storage.agent_bridge_tick = tick; npc.agent.pending = nil
                Agent.tick(npc, tick)
                return npc.agent.pending and npc.agent.pending.reason or 'none'
            end""")
        at = lambda tick: lua.eval(f"at({tick})")
        self.assertEqual(at(100), "none")                             # (the clock starts)
        self.assertEqual(at(100 + 10800), "routine")
        lua.execute("npc.design_history = {{purpose='coal-loop'}}; Agent.plan_finished(npc, 'coal-loop', 'complete')")
        self.assertEqual(at(100 + 10800 + 3600), "none")              # a minute on: still no thought for it
        self.assertEqual(at(100 + 2 * 10800), "none")                 # nor at the next heartbeat: not news
        self.assertEqual(at(100 + 10800 + 36000), "routine")          # its 10-minute thought hears of it
        self.assertEqual(lua.eval("npc.agent.pending.events[1].kind"), "plan_done")
        lua.execute("Agent.plan_finished(npc, 'stone-supply', 'failed', 'no_stone')")
        self.assertEqual(at(100 + 10800 + 36000 + 3600), "events")    # a failed one still wakes it

    def test_without_a_strategist_the_engineer_does_not_wait_for_one(self):
        lua = runtime()
        self.assertFalse(lua.eval("Agent.event(npc, {kind='chat', text='hi'})"))

    def test_a_chat_message_wakes_it_and_it_answers_through_its_strategist(self):
        lua = runtime()
        lua.eval("Agent.observe()")                       # the companion connects
        lua.execute("Agent.event(npc, {kind='chat', from='Andre', text='biters keep hitting my north wall'})")
        lua.execute("Agent.tick(npc, 160)")
        seen = lua.eval("Agent.observe()")
        request = seen.requests[1]
        self.assertEqual((request.reason, request.events[1].kind), ("events", "chat"))
        self.assertEqual(request.situation.name, "Ada")
        answer = lua.eval("""Agent.submit({request_id='%s', intentions={
            {tool='say', args={text='On my way with turrets.', to='Andre'}},
            {tool='deliver', priority='now', reason='an ally is under attack',
             args={to='Andre', items={{name='gun-turret', count=4}, {name='firearm-magazine', count=50}}}},
            {tool='fly', args={}}}})""" % request.request_id)
        self.assertEqual((answer.accepted, answer.rejected[1].reason), (2, "unknown_tool"))
        self.assertIn("On my way", lua.eval("said[1]"))
        self.assertEqual(lua.eval("#npc.agent.agenda"), 1)

    def test_a_delivery_is_gathered_carried_left_in_a_chest_and_marked(self):
        lua = runtime()
        lua.eval("Agent.observe()")
        lua.execute("if npc.agent then npc.agent.last_thought = nil end; Agent.event(npc, {kind='routine'}); Agent.tick(npc, 160)")
        rid = lua.eval("npc.agent.pending.request_id")
        lua.eval("""Agent.submit({request_id='%s', intentions={{tool='deliver', priority='now',
            args={to='Andre', items={{name='gun-turret', count=4}}}}}})""" % rid)
        job, _ = step(lua)
        self.assertEqual((job.kind, job.recipe), ("craft", "gun-turret"))          # gather first
        lua.execute("held['gun-turret'] = 4")
        job, _ = step(lua)
        self.assertEqual((job.kind, job.recipe), ("craft", "wooden-chest"))
        lua.execute("held['wooden-chest'] = 1")
        job, meta = step(lua)
        self.assertEqual((job.kind, meta.step), ("move", "walk"))
        lua.execute("npc.character.position = {x=98, y=50}")
        job, meta = step(lua)
        self.assertEqual((job.kind, job.entity_name, meta.step), ("build", "wooden-chest", "chest"))
        lua.execute("""chest = {valid=true, position={x=101, y=50}, surface=surface, force=ada_force}
                       Agent.on_receipt(npc, {step='chest', intention=npc.agent.agenda[1]},
                                        {status='complete', entity=chest}, 200)""")
        job, meta = step(lua)
        self.assertEqual((job.kind, job.mode, job.count), ("transfer", "deposit", 4))
        lua.execute("""Agent.on_receipt(npc, {step='fill', item='gun-turret', intention=npc.agent.agenda[1]},
                                        {status='complete', count=4}, 210)""")
        self.assertIsNone(step(lua)[0])                                     # handed over
        self.assertEqual(lua.eval("chest.force.name"), "player")                  # the chest is theirs
        self.assertEqual(lua.eval("tags[1].text"), "Ada: supplies")               # marked on their map
        self.assertIn("4 gun turret", lua.eval("said[#said]"))
        self.assertEqual(lua.eval("#npc.agent.agenda"), 0)
        results = lua.eval("Agent.observe()").results
        self.assertEqual((results[1].tool, results[1].status), ("deliver", "done"))

    def test_a_cleared_group_leads_on_to_the_next_stop_of_its_tour_when_it_carries_enough(self):
        # (O-9: one loop round the base; home only when the next group needs more than it carries)
        lua = runtime()
        lua.execute("""
            Agent.hostile_count = function() return 0 end
            Agent.creep_kit = function() return {name='gun-turret', item='gun-turret'} end
            Agent.attack_made = function(_, it) made = it.args.place.x end
            it = {tool='attack', creep={phase='pickup'}, arrived=10, before=5}
            args = {target='nests', place={x=100, y=0}, x=100, y=0,
                    tour={{x=70, y=70, turrets=2, magazines=10}, {x=-100, y=0, turrets=40, magazines=10}}}
            it.args = args
            held['gun-turret'] = 4; held['firearm-magazine'] = 50
            moved = Agent.tour_on(npc, it, args, 100)""")
        self.assertTrue(lua.eval("moved"))
        self.assertEqual((lua.eval("args.place.x"), lua.eval("args.place.y"), lua.eval("#args.tour")), (70, 70, 1))
        self.assertIsNone(lua.eval("it.arrived"))
        self.assertIsNone(lua.eval("it.creep"))
        self.assertEqual(lua.eval("it.started"), 100)                    # the leg's own trip time
        self.assertEqual(lua.eval("made"), 100)                          # the first stop logged as its own attack
        self.assertFalse(lua.eval("Agent.tour_on(npc, it, args, 200)"))  # 40 turrets: home to restock first
        self.assertEqual(lua.eval("#args.tour"), 1)
        lua.execute("Agent.hostile_count = function() return 3 end; args.tour[1].turrets = 2")
        self.assertFalse(lua.eval("Agent.tour_on(npc, it, args, 300)"))  # not cleared: no further

    def test_it_knows_only_the_nests_its_cloud_reaches_its_side_sees_or_attacks_came_from(self):
        # (O-9: the cloud plus seen, radar, ally and attack sources, nothing else; not every nest within 600 tiles)
        lua = runtime()
        lua.execute("""
            local Intel = require('scripts.intel')
            local TeamWar = require('scripts.team_war')
            local smog = {['100,0']=40, ['-200,32']=5, ['500,0']=0.2}
            npc.character.surface.get_pollution = function(p) return smog[p.x .. ',' .. p.y] or 0 end
            Intel.sees = function(_, p) return p.x == 0 and p.y == 300 end
            TeamWar.threat_board = function() return {attacks={{nest={x=310, y=-20}}}} end
            npc.seen_dangers = {list={{x=-410, y=410, r=30, nest=true, kind='unit-spawner'},
                                      {x=210, y=190, r=30, nest=true, blocked=true}}}   -- a failed route, no nest
            known = Agent.known_nests(npc, {
              {x=100, y=0}, {x=-200, y=0}, {x=0, y=300}, {x=300, y=0}, {x=-400, y=400}, {x=200, y=200}, {x=500, y=0}}, 1)""")
        rows = {(lua.eval("known[%d].x" % i), lua.eval("known[%d].y" % i)): i for i in range(1, lua.eval("#known") + 1)}
        self.assertEqual(set(rows), {(100, 0), (-200, 0), (0, 300), (300, 0), (-400, 400), (500, 0)})   # not (200, 200)
        get = lambda xy, k: lua.eval("known[%d].%s" % (rows[xy], k))
        self.assertEqual(get((100, 0), "cloud"), 40)
        self.assertTrue(get((-200, 0), "near_cloud"))                     # the chunk beside it is polluted
        self.assertTrue(get((0, 300), "seen"))                            # its side sees it
        self.assertTrue(get((-400, 400), "seen"))                         # it remembers it
        self.assertTrue(get((300, 0), "source"))                          # attacks came from there
        self.assertGreater(get((500, 0), "cloud"), 0)                     # a trace of cloud still counts
        lua.execute("few = Agent.known_nests(npc, {{x=200, y=200}, {x=0, y=300}, {x=100, y=0}, {x=-400, y=400}}, 1, 2)")
        self.assertEqual([lua.eval("few[%d].x" % i) for i in (1, 2)], [100, 0])   # the cloud's first, then the seen

    def test_a_delivery_of_nothing_it_can_make_is_given_up(self):
        lua = runtime()
        lua.eval("Agent.observe()")
        lua.execute("if npc.agent then npc.agent.last_thought = nil end; Agent.event(npc, {kind='routine'}); Agent.tick(npc, 160)")
        rid = lua.eval("npc.agent.pending.request_id")
        lua.eval("""Agent.submit({request_id='%s', intentions={{tool='deliver', priority='now',
            args={to='Andre', items={{name='gun-turret', count=4}}}}}})""" % rid)
        lua.execute("""Agent.designed.acquire = function() return nil, 'recipe_locked:gun-turret' end""")
        self.assertIsNone(step(lua)[0])
        results = lua.eval("Agent.observe()").results
        self.assertEqual((results[1].status, results[1].reason), ("failed", "nothing_to_give"))
        self.assertEqual(lua.eval("Agent.situation(npc, 200).cannot_make_yet[1]"), "gun-turret")

    def test_a_stockpile_of_what_its_machines_make_is_collected_not_crafted_but_in_a_hurry(self):
        # (one-engineer run 2026-10-24: a strategist's 200 walls were crafted by hand beside the wall row; the
        # user: walls are an automated system, by hand only in an emergency)
        lua = runtime()
        lua.execute("""
            Agent.designed.machine_made = function(_, item) return item == 'stone-wall' end
            chest = {valid=true, name='wooden-chest'}
            ready = 0
            Agent.designed.collect = function(_, item, n)
                if ready > 0 then return {kind='transfer', target=chest, item=item, count=math.min(ready, n), mode='withdraw'} end
            end""")
        lua.eval("Agent.observe()")
        lua.execute("if npc.agent then npc.agent.last_thought = nil end; Agent.event(npc, {kind='routine'}); Agent.tick(npc, 160)")
        rid = lua.eval("npc.agent.pending.request_id")
        lua.eval("""Agent.submit({request_id='%s', intentions={{tool='stockpile', priority='next',
            args={item='stone-wall', count=200}}}})""" % rid)
        self.assertIsNone(step(lua)[0])                                   # none made yet: it waits, crafts nothing
        lua.execute("ready = 40")
        job = step(lua)[0]
        self.assertEqual((job.kind, job.mode, job.count), ("transfer", "withdraw", 40))
        # what nothing of its own makes is still made by hand, and so is anything wanted "now"
        lua.execute("ready = 0; npc.agent.agenda = {}")
        lua.eval("""Agent.order(npc, {tool='stockpile', priority='now', args={item='stone-wall', count=200}})""")
        self.assertEqual(step(lua)[0].kind, "craft")

    def test_a_reserve_made_by_hand_stops_at_its_hand_budget_and_urgent_work_is_known(self):
        # (run 20, 2026-10-25: its strategist asked for walls again and again; a fifth of its time went to them)
        lua = runtime()
        lua.eval("Agent.observe()")
        lua.execute("if npc.agent then npc.agent.last_thought = nil end; Agent.event(npc, {kind='routine'}); Agent.tick(npc, 160)")
        rid = lua.eval("npc.agent.pending.request_id")
        lua.eval("""Agent.submit({request_id='%s', intentions={{tool='stockpile', priority='next',
            args={item='stone-wall', count=200}}}})""" % rid)
        self.assertFalse(lua.eval("Agent.urgent(npc)"))
        job, meta = step(lua)
        self.assertEqual(job.kind, "craft")
        it = "npc.agent.agenda[1]"
        lua.execute(f"Agent.on_receipt(npc, {{intention={it}}}, {{status='complete', tick=Agent.STOCKPILE_HAND + 200, "
                    f"started_tick=100}}, Agent.STOCKPILE_HAND + 200)")
        self.assertGreaterEqual(lua.eval(f"{it}.spent"), lua.eval("Agent.STOCKPILE_HAND"))
        step(lua)
        self.assertEqual(lua.eval("#npc.agent.agenda"), 0)                 # its hand budget spent: done for now
        self.assertIn("hand_budget", lua.eval("npc.agent.log[#npc.agent.log]"))
        lua.eval("""Agent.order(npc, {tool='stockpile', priority='now', args={item='stone-wall', count=200}})""")
        self.assertTrue(lua.eval("Agent.urgent(npc)"))

    def test_the_same_intention_twice_is_kept_once_at_the_more_urgent_priority(self):
        lua = runtime()
        lua.eval("Agent.observe()")
        lua.execute("active = true")
        for priority, accepted in (("next", 1), ("next", 0), ("now", 1)):
            lua.execute("if npc.agent then npc.agent.last_thought = nil end; Agent.event(npc, {kind='routine'}); Agent.tick(npc, game.tick)")
            rid = lua.eval("npc.agent.pending.request_id")
            answer = lua.eval("""Agent.submit({request_id='%s', intentions={{tool='build', priority='%s',
                args={goal='power-plant'}}}})""" % (rid, priority))
            self.assertEqual(answer.accepted, accepted)
        self.assertEqual(lua.eval("#npc.agent.agenda"), 1)
        self.assertEqual(lua.eval("npc.agent.agenda[1].priority"), "now")

    def test_places_in_what_it_says_are_links_on_the_map(self):
        lua = runtime()
        self.assertEqual(lua.eval("Agent.gps('My base is at (1553.5, -26), come by', 'nauvis')"),
                         "My base is at [gps=1554,-26,nauvis], come by")
        self.assertEqual(lua.eval("Agent.gps('x=12, y=-40 is clear')"), "[gps=12,-40,nauvis] is clear")
        self.assertEqual(lua.eval("Agent.gps('3 turrets (and ammo)')"), "3 turrets (and ammo)")

    def test_asked_to_come_over_it_walks_to_the_player_and_stays_a_while(self):
        lua = runtime()
        lua.eval("Agent.observe()")
        lua.execute("if npc.agent then npc.agent.last_thought = nil end; Agent.event(npc, {kind='routine'}); Agent.tick(npc, 160)")
        rid = lua.eval("npc.agent.pending.request_id")
        answer = lua.eval("""Agent.submit({request_id='%s', intentions={{tool='visit', priority='now',
            args={to='Andre', minutes=2}}}})""" % rid)
        self.assertEqual(answer.accepted, 1)
        job, meta = step(lua)
        self.assertEqual((job.kind, job.position.x, meta.step), ("move", 101, "visit"))
        lua.execute("npc.character.position = {x=99, y=50}")
        job, meta = step(lua)
        self.assertEqual((job, meta), (False, "visit"))                  # there: it stays
        self.assertEqual(lua.eval("npc.agent.events[#npc.agent.events].kind"), "arrived")   # its strategist hears
        lua.execute("andre.character = {valid=true}; andre.position = {x=160, y=50}")   # they walk off: it follows
        self.assertEqual(step(lua)[0].kind, "move")
        lua.execute("npc.character.position = {x=158, y=50}; game.tick = game.tick + 2*3600 + 1")
        self.assertIsNone(step(lua)[0])                                  # the visit is over
        self.assertEqual(lua.eval("Agent.observe()").results[1].status, "done")

    def test_it_hands_items_to_a_player_from_its_own_stock(self):
        lua = runtime()
        lua.eval("Agent.observe()")
        lua.execute("""
            mine = {items={['iron-plate']=300}}
            function mine.get_item_count(spec) return mine.items[spec.name] or 0 end
            function mine.remove(spec) mine.items[spec.name] = mine.items[spec.name] - spec.count end
            theirs = {items={}}
            function theirs.insert(spec) theirs.items[spec.name] = (theirs.items[spec.name] or 0) + spec.count; return spec.count end
            npc.character.get_main_inventory = function() return mine end
            andre.character = {valid=true, get_main_inventory=function() return theirs end}
            held['iron-plate'] = 300
            if npc.agent then npc.agent.last_thought = nil end; Agent.event(npc, {kind='routine'}); Agent.tick(npc, 160)""")
        rid = lua.eval("npc.agent.pending.request_id")
        lua.eval("""Agent.submit({request_id='%s', intentions={{tool='give', priority='now',
            args={to='Andre', items={{name='iron-plate', count=200}}}}}})""" % rid)
        job, meta = step(lua)
        self.assertEqual((job.kind, meta.step), ("move", "give"))             # walks over to them
        lua.execute("npc.character.position = {x=99, y=50}")
        self.assertIsNone(step(lua)[0])                                      # handed over
        self.assertEqual((lua.eval("theirs.items['iron-plate']"), lua.eval("mine.items['iron-plate']")), (200, 100))
        result = lua.eval("Agent.observe()").results[1]
        self.assertEqual((result.tool, result.status, result.reason), ("give", "done", "200 iron-plate"))

    def test_a_trade_is_offered_in_the_amounts_players_trade(self):
        lua = runtime()
        lua.eval("Agent.observe()")
        lua.execute("held['iron-plate'] = 800; if npc.agent then npc.agent.last_thought = nil end; Agent.event(npc, {kind='routine'}); Agent.tick(npc, 160)")
        rid = lua.eval("npc.agent.pending.request_id")
        answer = lua.eval("""Agent.submit({request_id='%s', intentions={
            {tool='trade', args={to='Andre', give_item='iron-plate', give_count=20, want_item='firearm-magazine', want_count=5}},
            {tool='trade', priority='now', args={to='Andre', give_item='iron-plate', give_count=600,
                                                 want_item='firearm-magazine', want_count=100}}}})""" % rid)
        self.assertEqual((answer.accepted, answer.rejected[1].reason), (1, "too_small"))
        self.assertIsNone(step(lua)[0])
        self.assertEqual((lua.eval("offers[1].npc_count"), lua.eval("offers[1].player_gives")), (600, "firearm-magazine"))
        self.assertIn("accept 7", lua.eval("said[#said]"))

    def test_joining_a_team_and_going_to_war_for_it(self):
        lua = runtime()
        lua.eval("Agent.observe()")
        lua.execute("""
            kevin = {id=2, name='Kevin', force={name='npc-faction-2', get_cease_fire=function() return true end},
                     home={x=300, y=0}, character={valid=true, position={x=300, y=0}}}
            storage.npcs[2] = kevin
            held['firearm-magazine'] = 50
            Agent.event(npc, {kind='chat', from='Andre', text='join me and hit Kevin'}); Agent.tick(npc, 160)""")
        rid = lua.eval("npc.agent.pending.request_id")
        answer = lua.eval("""Agent.submit({request_id='%s', intentions={
            {tool='join', args={to='Andre', join=true}},
            {tool='attack', priority='now', args={target='Kevin', minutes=2}}}})""" % rid)
        self.assertEqual(answer.accepted, 2)
        self.assertEqual(lua.eval("npc.joined.name"), "Andre")
        self.assertEqual(lua.eval("Agent.situation(npc, 200).joined"), "Andre")
        job, meta = step(lua)
        self.assertEqual((lua.eval("declared[1]"), job.kind, meta.step), ("Kevin", "move", "attack"))
        lua.execute("npc.character.position = {x=290, y=0}")
        self.assertEqual(step(lua), (False, "attack"))                  # there: it holds and fights
        self.assertEqual(lua.eval("npc.agent.events[#npc.agent.events].kind"), "arrived")

    def test_ordered_to_raid_it_shoots_their_production_once_nothing_armed_is_left(self):
        lua = runtime()
        lua.execute("""
            defines.shooting = {not_shooting=0, shooting_selected=2}
            held['piercing-rounds-magazine'] = 100
            kevin_force = {name='npc-faction-2', valid=true, get_cease_fire=function() return false end}
            storage.npcs[2] = {id=2, name='Kevin', force=kevin_force, home={x=300, y=0},
                               character={valid=true, position={x=300, y=0}}}
            ada_force.get_cease_fire = function() return false end   -- already at war
            furnace = {valid=true, type='furnace', health=200, position={x=330, y=0}}
            pole = {valid=true, type='electric-pole', health=100, position={x=305, y=0}}
            surface.find_entities_filtered = function(f)
              if f.force == kevin_force and f.type[1] ~= 'ammo-turret' then return {pole, furnace} end
              return {}   -- no turrets: it goes straight in
            end
            ok, why = Agent.order(npc, {tool='attack', priority='now', args={target='Kevin', minutes=2}})""")
        self.assertTrue(lua.eval("ok"))
        job, _ = step(lua)
        self.assertEqual(job.kind, "move")                     # its piercing rounds count: no detour to craft
        lua.execute("npc.character.position = {x=290, y=0}")
        job, meta = step(lua)
        self.assertEqual((job.kind, meta.step), ("move", "attack"))
        self.assertLess(abs(job.position.x - 305), 14)        # nothing in reach: in to their nearest building
        lua.execute("npc.character.position = {x=320, y=0}")
        self.assertEqual(step(lua), (False, "attack"))
        self.assertEqual(lua.eval("npc.character.shooting_state.position.x"), 330)   # the furnace before the pole
        self.assertTrue(lua.eval("npc.character.selected == furnace"))     # a building is shot once selected
        lua.execute("game.tick = game.tick + 2 * 3600 + 10")
        job, _ = step(lua)
        self.assertEqual(lua.eval("npc.character.shooting_state.state"), 0)   # time up: it stops
        self.assertEqual((job.kind, job.position.x), ("move", 0))             # and goes home
        lua.execute("npc.character.position = {x=2, y=0}")
        step(lua)
        self.assertEqual(lua.eval("#npc.agent.agenda"), 0)

    def test_while_turrets_go_up_against_an_attack_only_urgent_intentions_go_first(self):
        lua = runtime()
        lua.execute("""
            unlock('firearm-magazine')
            defending = true
            Agent.designed.defending = function() return defending end
            Agent.order(npc, {tool='arm_up', priority='next', args={focus='ammo', ammo=50}})""")
        self.assertIsNone(step(lua)[0])                          # waits behind the turrets
        lua.execute("defending = false")
        self.assertEqual(step(lua)[0].kind, "craft")             # then arms up
        lua.execute("""defending = true; npc.agent.agenda = {}
            Agent.order(npc, {tool='arm_up', priority='now', args={focus='ammo', ammo=50}})""")
        self.assertEqual(step(lua)[0].kind, "craft")             # what is urgent still goes first

    def test_attacked_it_arms_up_with_the_best_it_can_make_then_wears_it(self):
        lua = runtime()
        lua.eval("Agent.observe()")
        lua.execute("""
            for _, n in ipairs({'light-armor', 'heavy-armor', 'pistol', 'submachine-gun', 'firearm-magazine'}) do
              unlock(n) end
            require('scripts.combat').equip_now = function() equipped = true end
            Agent.event(npc, {kind='attacked', by='biters', enemies=6, strength=4.5}); Agent.tick(npc, 160)""")
        rid = lua.eval("npc.agent.pending.request_id")
        answer = lua.eval("""Agent.submit({request_id='%s', intentions={
            {tool='arm_up', priority='now', args={focus='all', ammo=100}}}})""" % rid)
        self.assertEqual(answer.accepted, 1)
        made = []
        for _ in range(6):
            job, _meta = step(lua)
            if not job or job.kind != "craft":
                break
            made.append((job.recipe, job.count))
            lua.execute("held['%s'] = %d" % (job.recipe, job.count))   # made it
        # the best armor and gun it can make (not light armor or a pistol as well), then ammunition
        self.assertEqual(made, [("heavy-armor", 1), ("submachine-gun", 1), ("firearm-magazine", 100)])
        self.assertTrue(lua.eval("equipped"))
        self.assertEqual(lua.eval("Agent.situation(npc, 300).armed.magazines"), 100)

    def test_it_arms_up_with_what_it_throws_once_it_can_make_it_and_slowdown_only_for_a_foe(self):
        lua = runtime()
        lua.execute("""
            for _, n in ipairs({'grenade', 'slowdown-capsule', 'defender-capsule'}) do
              unlock(n) end
            Agent.order(npc, {tool='arm_up', priority='now', args={focus='capsules'}})""")
        job, _ = step(lua)
        self.assertEqual((job.recipe, job.count), ("grenade", 10))
        lua.execute("held.grenade = 10")
        job, _ = step(lua)
        self.assertEqual((job.recipe, job.count), ("defender-capsule", 5))    # no foe: no slowdown capsules
        lua.execute("held['defender-capsule'] = 5; npc.foe = {key='npc:2', name='Kevin'}"
                    "; Agent.order(npc, {tool='arm_up', priority='now', args={focus='capsules'}})")
        lua.execute("npc.agent.agenda = {npc.agent.agenda[#npc.agent.agenda]}")
        job, _ = step(lua)
        self.assertEqual((job.recipe, job.count), ("slowdown-capsule", 5))

    def test_heavy_armor_wanting_only_steel_gets_a_steel_smelter_built(self):
        lua = runtime()
        lua.execute("""
            for _, n in ipairs({'heavy-armor', 'steel-plate'}) do unlock(n) end
            local acquire = Agent.designed.acquire
            Agent.designed.acquire = function(npc, item, n, d)
                if item == 'heavy-armor' then return nil end          -- waiting for steel
                return acquire(npc, item, n, d) end
            Agent.order(npc, {tool='arm_up', priority='now', args={focus='armor'}})""")
        step(lua)
        tools = [lua.eval("npc.agent.agenda[%d].tool" % i) for i in range(1, lua.eval("#npc.agent.agenda") + 1)]
        self.assertIn("build", tools)
        self.assertEqual(lua.eval("npc.agent.agenda[#npc.agent.agenda].args.goal"), "steel-smelting")

    def test_its_diary_keeps_a_plan_and_secrets_apart_from_notes(self):
        lua = runtime()
        lua.eval("Agent.observe()")
        lua.execute("if npc.agent then npc.agent.last_thought = nil end; Agent.event(npc, {kind='routine'}); Agent.tick(npc, 160)")
        rid = lua.eval("npc.agent.pending.request_id")
        lua.eval("""Agent.submit({request_id='%s', intentions={
            {tool='remember', args={note='Kevin has copper to spare', kind='note'}},
            {tool='remember', args={note='Build up, then take the copper field from Kevin', kind='plan'}},
            {tool='remember', args={note='Ally with Kevin until his turrets face the biters', kind='secret'}}}})""" % rid)
        diary = lua.eval("Agent.situation(npc, 300).diary")
        self.assertEqual(diary.plan, "Build up, then take the copper field from Kevin")
        self.assertEqual(diary.secrets[1], "Ally with Kevin until his turrets face the biters")
        self.assertEqual(lua.eval("Agent.situation(npc, 300).notes[1]"), "Kevin has copper to spare")

    def test_diplomacy_goes_through_the_factions_rules_and_loyalty_holds(self):
        lua = runtime()
        lua.eval("Agent.observe()")
        lua.execute("""
            applied = {}
            require('scripts.society').apply = function(npc, d) applied[#applied+1] = d.id; return true end
            kevin = {id=2, name='Kevin', force={name='npc-faction-2'}, home={x=300, y=0},
                     character={valid=true, position={x=300, y=0}}}
            storage.npcs[2] = kevin
            if npc.agent then npc.agent.last_thought = nil end; Agent.event(npc, {kind='routine'}); Agent.tick(npc, 160)""")
        rid = lua.eval("npc.agent.pending.request_id")
        answer = lua.eval("""Agent.submit({request_id='%s', intentions={
            {tool='diplomacy', args={action='offer_alliance', target='Kevin'}},
            {tool='quiet', args={on=true}}}})""" % rid)
        self.assertEqual(answer.accepted, 2)
        self.assertEqual(lua.eval("applied[1]"), "offer-alliance:2")
        self.assertTrue(lua.eval("npc.quiet"))
        # a loyal engineer does not turn on an ally
        lua.execute("""require('scripts.personality').has = function(_, name) return name == 'loyal' end
            ada_force.get_friend = function() return true end
            if npc.agent then npc.agent.last_thought = nil end; Agent.event(npc, {kind='routine'}); Agent.tick(npc, 400)""")
        rid = lua.eval("npc.agent.pending.request_id")
        answer = lua.eval("""Agent.submit({request_id='%s', intentions={
            {tool='diplomacy', args={action='declare_war', target='Kevin'}}}})""" % rid)
        self.assertEqual((answer.accepted, answer.rejected[1].reason), (0, "loyal"))

    def test_relocating_walks_away_from_the_threat_and_starts_over(self):
        lua = runtime()
        lua.eval("Agent.observe()")
        lua.execute("""
            moved_home = nil
            Agent.designed.relocate = function(_, at) moved_home = at end
            require('scripts.base_memory').threats = function() return {{x=-100, y=0}} end
            Agent.event(npc, {kind='attacked', by='biters', enemies=30, strength=20}); Agent.tick(npc, 160)""")
        rid = lua.eval("npc.agent.pending.request_id")
        answer = lua.eval("""Agent.submit({request_id='%s', intentions={
            {tool='relocate', priority='now', args={distance=300}}}})""" % rid)
        self.assertEqual(answer.accepted, 1)
        job, meta = step(lua)
        self.assertEqual((job.kind, meta.step), ("move", "relocate"))
        self.assertGreater(job.position.x, 250)                      # away from the nests in the west
        lua.execute("npc.character.position = {x=%f, y=%f}" % (job.position.x, job.position.y))
        step(lua)
        self.assertGreater(lua.eval("moved_home.x"), 250)
        self.assertEqual(lua.eval("npc.agent.events[#npc.agent.events].kind"), "relocated")

    def test_it_creeps_up_on_a_nest_behind_its_own_turrets(self):
        lua = runtime()
        lua.eval("Agent.observe()")
        lua.execute("""
            for _, n in ipairs({'firearm-magazine', 'gun-turret'}) do unlock(n) end
            held['gun-turret'] = 2; held['firearm-magazine'] = 100
            worm = {valid=true, position={x=200, y=0}, force={name='enemy'}}
            ada_force.get_cease_fire = function(f) return f.name ~= 'enemy' end
            turrets = {}
            surface.find_entities_filtered = function(f)
              if f.type then
                if worm.valid and (worm.position.x - f.position.x)^2 + (worm.position.y - f.position.y)^2 <= f.radius^2 then
                  return {worm} end
                return {}
              end
              local out = {}
              for _, t in ipairs(turrets) do
                if t.valid and (t.position.x - f.position.x)^2 + (t.position.y - f.position.y)^2 <= f.radius^2 then
                  out[#out+1] = t end
              end
              return out
            end
            if npc.agent then npc.agent.last_thought = nil end; Agent.event(npc, {kind='routine'}); Agent.tick(npc, 160)""")
        rid = lua.eval("npc.agent.pending.request_id")
        answer = lua.eval("""Agent.submit({request_id='%s', intentions={
            {tool='attack', priority='now', args={target='nests', x=200, y=0, minutes=5}}}})""" % rid)
        self.assertEqual(answer.accepted, 1)
        job, _ = step(lua)
        self.assertEqual(job.kind, "move")                                # march, stopping out of its reach
        lua.execute("npc.character.position = {x=160, y=0}")
        placed = []
        for _ in range(2):
            job, _ = step(lua)
            self.assertEqual((job.kind, job.entity_name), ("build", "gun-turret"))
            self.assertLess(abs(job.position.x - 185), 3)               # just short of the worm
            placed.append((job.position.x, job.position.y))
            lua.execute("""turrets[#turrets+1] = {valid=true, position={x=%f, y=%f},
                get_inventory=function() return {get_item_count=function() return 0 end} end}
                held['gun-turret'] = held['gun-turret'] - 1""" % (job.position.x, job.position.y))
            job, _ = step(lua)                                            # loaded as soon as it stands (O-9)
            self.assertEqual((job.kind, job.mode, job.item), ("transfer", "deposit", "firearm-magazine"))
            self.assertEqual(job.count, 20)                               # filled: its 90 spare shared, 20 at most
            lua.execute("turrets[#turrets].get_inventory = function() return {get_item_count=function() return 20 end} end")
        self.assertEqual(step(lua)[0], False)                            # they fight together
        lua.execute("worm.valid = false")                                # the worm is dead
        job, _ = step(lua)
        self.assertEqual((job.kind, job.mode), ("mine", "deconstruct"))  # turrets back in hand

    def test_its_ring_is_three_turrets_a_building_and_comes_back_in_one_pickup(self):
        # (O-9, the user's clear: about 3 turrets per spawner and worm, placed together, filled, all picked up)
        lua = runtime()
        lua.eval("Agent.observe()")
        lua.execute("""
            for _, n in ipairs({'firearm-magazine', 'gun-turret'}) do unlock(n) end
            held['gun-turret'] = 8; held['firearm-magazine'] = 200
            worms = {{valid=true, position={x=200, y=0}, force={name='enemy'}},
                     {valid=true, position={x=206, y=4}, force={name='enemy'}}}
            ada_force.get_cease_fire = function(f) return f.name ~= 'enemy' end
            turrets = {}
            surface.find_entities_filtered = function(f)
              local out = {}
              for _, e in ipairs(f.type and worms or turrets) do
                if e.valid and (e.position.x - f.position.x)^2 + (e.position.y - f.position.y)^2 <= f.radius^2 then
                  out[#out+1] = e end
              end
              return out
            end
            Agent.designed = Agent.designed or {}
            Agent.designed.pickup_job = function(_, es) return {kind='pickup', targets=es}, {upkeep='pickup'} end
            if npc.agent then npc.agent.last_thought = nil end; Agent.event(npc, {kind='routine'}); Agent.tick(npc, 160)""")
        rid = lua.eval("npc.agent.pending.request_id")
        lua.eval("""Agent.submit({request_id='%s', intentions={
            {tool='attack', priority='now', args={target='nests', x=200, y=0, minutes=5, turrets=8}}}})""" % rid)
        step(lua)
        lua.execute("npc.character.position = {x=160, y=0}")
        builds, spots = 0, []
        job, _ = step(lua)
        while job and job.kind == "build":
            builds += 1
            spots.append((job.position.x, job.position.y))
            lua.execute("""turrets[#turrets+1] = {valid=true, position={x=%f, y=%f},
                get_inventory=function() return {get_item_count=function() return 20 end} end}
                held['gun-turret'] = held['gun-turret'] - 1""" % (job.position.x, job.position.y))
            job, _ = step(lua)
        self.assertEqual(builds, 6)                                       # 3 for each of the two worms, not all 8
        far = [((x - 203) ** 2 + (y - 2) ** 2) ** 0.5 for x, y in spots]
        self.assertTrue(all(9 < d < 18 for d in far))                     # a ring just outside them
        self.assertTrue(any(x > 206 for x, _ in spots))                   # all round, the far side too
        lua.execute("require('scripts.combat').stepping_out = function() return true end")
        self.assertEqual(step(lua)[0], False)                            # hurt: it holds out of reach till healed
        lua.execute("require('scripts.combat').stepping_out = function() return false end")
        lua.execute("for _, w in ipairs(worms) do w.valid = false end")   # the group is dead
        job, _ = step(lua)
        self.assertEqual((job.kind, len(job.targets)), ("pickup", 6))    # all of them back at once

    def test_a_spread_group_gets_turrets_in_reach_of_every_building_each_loaded_as_it_stands(self):
        # (Codex's review: one circle round a spread group left its middle out of the turrets' 18 tiles)
        lua = runtime()
        lua.eval("Agent.observe()")
        lua.execute("""
            for _, n in ipairs({'firearm-magazine', 'gun-turret'}) do unlock(n) end
            held['gun-turret'] = 9; held['firearm-magazine'] = 300
            worms = {{valid=true, position={x=200, y=-30}, force={name='enemy'}},
                     {valid=true, position={x=200, y=0}, force={name='enemy'}},
                     {valid=true, position={x=200, y=30}, force={name='enemy'}}}
            ada_force.get_cease_fire = function(f) return f.name ~= 'enemy' end
            turrets = {}
            surface.find_entities_filtered = function(f)
              local out = {}
              for _, e in ipairs(f.type and worms or turrets) do
                if e.valid and (e.position.x - f.position.x)^2 + (e.position.y - f.position.y)^2 <= f.radius^2 then
                  out[#out+1] = e end
              end
              return out
            end
            if npc.agent then npc.agent.last_thought = nil end; Agent.event(npc, {kind='routine'}); Agent.tick(npc, 160)""")
        rid = lua.eval("npc.agent.pending.request_id")
        lua.eval("""Agent.submit({request_id='%s', intentions={
            {tool='attack', priority='now', args={target='nests', x=200, y=0, minutes=5, turrets=9}}}})""" % rid)
        step(lua)
        lua.execute("npc.character.position = {x=160, y=0}")
        spots = []
        job, _ = step(lua)
        while job and job.kind in ("build", "transfer"):
            if job.kind == "build":
                spots.append((job.position.x, job.position.y))
                lua.execute("""turrets[#turrets+1] = {valid=true, position={x=%f, y=%f},
                    get_inventory=function() return {get_item_count=function() return 0 end} end}
                    held['gun-turret'] = held['gun-turret'] - 1""" % (job.position.x, job.position.y))
                job, _ = step(lua)
                self.assertEqual(job.kind, "transfer")                    # loaded before the next goes down
                lua.execute("turrets[#turrets].get_inventory = function() return {get_item_count=function() return 30 end} end")
            job, _ = step(lua)
        self.assertEqual(len(spots), 9)
        for wy in (-30, 0, 30):                                           # each building: 3 within a turret's reach
            near = [1 for x, y in spots if ((x - 200) ** 2 + (y - wy) ** 2) ** 0.5 <= 18]
            self.assertGreaterEqual(len(near), 3)

    def test_late_game_it_shells_a_nest_from_out_of_their_reach_with_turrets_round_its_artillery(self):
        # (O-7, the user: "defend and attack correctly even late game")
        lua = runtime()
        lua.eval("Agent.observe()")
        lua.execute("""
            for _, n in ipairs({'uranium-rounds-magazine', 'gun-turret', 'artillery-turret'}) do unlock(n) end
            held['gun-turret'] = 6; held['uranium-rounds-magazine'] = 200
            held['artillery-turret'] = 1; held['artillery-shell'] = 40
            Agent.artillery_kit = function() return {name='artillery-turret', item='artillery-turret', range=200}, 'artillery-shell' end
            Agent.creep_kit = function() return {name='gun-turret', item='gun-turret', range=18}, 'uranium-rounds-magazine' end
            nest = {valid=true, position={x=400, y=0}, force={name='enemy'}}
            ada_force.get_cease_fire = function(f) return f.name ~= 'enemy' end
            built = {}
            shells_in, ammo_in = 0, 0
            local function inv(kind) return {get_item_count=function() return kind == 'art' and shells_in or ammo_in end} end
            surface.find_entities_filtered = function(f)
              if f.type then
                if nest.valid and (nest.position.x - f.position.x)^2 + (nest.position.y - f.position.y)^2 <= f.radius^2 then
                  return {nest} end
                return {}
              end
              local out = {}
              for _, e in ipairs(built) do
                if e.valid and (not f.name or f.name == e.name)
                    and (e.position.x - f.position.x)^2 + (e.position.y - f.position.y)^2 <= f.radius^2 then out[#out+1] = e end
              end
              return out
            end
            function put(job)
              local e = {valid=true, name=job.entity_name, position={x=job.position.x, y=job.position.y}}
              e.get_inventory = function() return inv(job.entity_name == 'artillery-turret' and 'art' or 'gun') end
              built[#built+1] = e
              held[job.item] = held[job.item] - 1
            end
            if npc.agent then npc.agent.last_thought = nil end; Agent.event(npc, {kind='routine'}); Agent.tick(npc, 160)""")
        rid = lua.eval("npc.agent.pending.request_id")
        answer = lua.eval("""Agent.submit({request_id='%s', intentions={
            {tool='attack', priority='now', args={target='nests', x=400, y=0, minutes=5, method='artillery', turrets=6}}}})""" % rid)
        self.assertEqual(answer.accepted, 1)
        job, _ = step(lua)
        self.assertEqual(job.kind, "move")
        self.assertLess(job.position.x, 400 - 150)                           # it stops far out: 160 off them
        lua.execute("npc.character.position = {x=240, y=0}")
        job, _ = step(lua)
        self.assertEqual((job.kind, job.entity_name), ("build", "artillery-turret"))
        lua.execute("put(...)", job) if False else lua.globals().put(job)
        for _ in range(3):                                                    # its guard, between it and them
            job, _ = step(lua)
            self.assertEqual((job.kind, job.entity_name), ("build", "gun-turret"))
            self.assertGreater(job.position.x, 240)
            lua.globals().put(job)
        job, _ = step(lua)
        self.assertEqual((job.kind, job.item), ("transfer", "artillery-shell"))   # shells in
        lua.execute("shells_in = 20")
        job, _ = step(lua)
        self.assertEqual((job.kind, job.item), ("transfer", "uranium-rounds-magazine"))   # its guards loaded
        lua.execute("ammo_in = 20")
        self.assertEqual(step(lua)[0], False)                                 # shelling them, standing by
        # called home meanwhile: its artillery and guards come along (packed), the trip starts over later
        lua.execute("""require('scripts.war').alarm(npc, {x=10, y=0}, {name='npc-faction-9'}, 20, nil, game.tick)
            Agent.tick(npc, game.tick)""")
        pack = lua.eval("npc.recall and #npc.recall.pack or 0")
        self.assertEqual(pack, 4)                                             # the artillery and three guards
        self.assertIsNone(lua.eval("npc.agent.agenda[1].artillery"))

    def test_its_artillery_guard_is_what_it_holds_and_its_shells_what_fit(self):
        lua = runtime()
        lua.eval("Agent.observe()")
        lua.execute("""
            held['gun-turret'] = 2; held['uranium-rounds-magazine'] = 50; held['artillery-turret'] = 1; held['artillery-shell'] = 40
            Agent.artillery_kit = function() return {name='artillery-turret', item='artillery-turret', range=200}, 'artillery-shell' end
            Agent.creep_kit = function() if (held['gun-turret'] or 0) >= 2 then
                return {name='gun-turret', item='gun-turret', range=18}, 'uranium-rounds-magazine' end end
            nest = {valid=true, position={x=400, y=0}, force={name='enemy'}}
            ada_force.get_cease_fire = function(f) return f.name ~= 'enemy' end
            built = {}
            surface.find_entities_filtered = function(f)
              if f.type then return nest.valid and {nest} or {} end
              local out = {}
              for _, e in ipairs(built) do
                if (not f.name or f.name == e.name) and (e.position.x - f.position.x)^2 + (e.position.y - f.position.y)^2 <= f.radius^2 then
                  out[#out+1] = e end
              end
              return out
            end
            function put(job)
              local e = {valid=true, name=job.entity_name, position={x=job.position.x, y=job.position.y}}
              e.get_inventory = function() return {get_item_count=function() return 0 end,
                get_insertable_count=function() return 15 end} end
              built[#built+1] = e
              held[job.item] = held[job.item] - 1
            end
            npc.character.position = {x=240, y=0}
            it = {tool='attack', args={target='nests', place={x=400, y=0}, method='artillery', minutes=5}}""")
        kinds = []
        for _ in range(6):
            job, status = lua.eval("{Agent.artillery(npc, it, it.args, game.tick, false)}").values() if False else \
                (lua.eval("Agent.artillery(npc, it, it.args, game.tick, false)"), None)
            if not job or job is True or isinstance(job, str):
                break
            kinds.append((job.kind, job.entity_name or job.item, job.count))
            if job.kind == "build":
                lua.globals().put(job)
            else:
                break
        self.assertEqual([k[1] for k in kinds[:3]], ["artillery-turret", "gun-turret", "gun-turret"])   # 2 held: 2 guards
        self.assertEqual(kinds[3][:2], ("transfer", "artillery-shell"))
        self.assertEqual(kinds[3][2], 15)                                     # as many shells as fit

    def test_creep_turrets_get_the_guns_spare_ammo_and_never_stand_empty(self):
        # (run 19, 2026-10-25: 100 magazines sat in its gun while two creep turrets stood empty, four attacks
        # running, each ending "done" on its timer)
        def creep_to_load(gun_magazines):
            lua = runtime()
            lua.eval("Agent.observe()")
            lua.execute("""
                for _, n in ipairs({'firearm-magazine', 'gun-turret'}) do unlock(n) end
                held['gun-turret'] = 2
                gun = {['firearm-magazine']=%d}
                ammo_slot = {get_item_count=function(spec) return gun[type(spec) == 'table' and spec.name or spec] or 0 end,
                             remove=function(stack) gun[stack.name] = gun[stack.name] - stack.count; return stack.count end}
                inventory.insert = function(stack) held[stack.name] = (held[stack.name] or 0) + stack.count; return stack.count end
                npc.character.get_inventory = function(i)
                  if i == defines.inventory.character_main then return inventory end
                  if i == defines.inventory.character_ammo then return ammo_slot end
                end
                worm = {valid=true, position={x=200, y=0}, force={name='enemy'}}
                ada_force.get_cease_fire = function(f) return f.name ~= 'enemy' end
                turrets = {}
                surface.find_entities_filtered = function(f)
                  if f.type then
                    if worm.valid and (worm.position.x - f.position.x)^2 + (worm.position.y - f.position.y)^2 <= f.radius^2 then
                      return {worm} end
                    return {}
                  end
                  local out = {}
                  for _, t in ipairs(turrets) do
                    if t.valid and (t.position.x - f.position.x)^2 + (t.position.y - f.position.y)^2 <= f.radius^2 then
                      out[#out+1] = t end
                  end
                  return out
                end
                if npc.agent then npc.agent.last_thought = nil end; Agent.event(npc, {kind='routine'}); Agent.tick(npc, 160)""" % gun_magazines)
            rid = lua.eval("npc.agent.pending.request_id")
            lua.eval("""Agent.submit({request_id='%s', intentions={
                {tool='attack', priority='now', args={target='nests', x=200, y=0, minutes=5}}}})""" % rid)
            job, _ = step(lua)
            self.assertEqual(job.kind, "move")
            lua.execute("npc.character.position = {x=160, y=0}")
            return lua

        def place(lua):
            job, _ = step(lua)
            self.assertEqual((job.kind, job.entity_name), ("build", "gun-turret"))
            lua.execute("""turrets[#turrets+1] = {valid=true, position={x=%f, y=%f},
                get_inventory=function() return {get_item_count=function() return 0 end} end}
                held['gun-turret'] = held['gun-turret'] - 1""" % (job.position.x, job.position.y))
        lua = creep_to_load(100)
        place(lua)
        job, _ = step(lua)                                                # loaded as soon as it stands (O-9)
        self.assertEqual((job.kind, job.mode, job.item), ("transfer", "deposit", "firearm-magazine"))
        self.assertEqual((lua.eval("held['firearm-magazine']"), lua.eval("gun['firearm-magazine']")), (20, 80))
        # nothing to spare (the gun keeps its reserve): the turrets come back and the attack ends, remembered so
        lua = creep_to_load(100)
        place(lua)
        lua.execute("gun['firearm-magazine'] = 10; held['firearm-magazine'] = 0")   # (spent on the way)
        place(lua)
        for _ in range(2):
            job, _ = step(lua)
            self.assertEqual((job.kind, job.mode), ("mine", "deconstruct"))
            lua.execute("for _, t in ipairs(turrets) do if t.valid then t.valid = false; break end end")
        step(lua)
        self.assertEqual(lua.eval("npc.attack_log[1].outcome"), "turrets_empty")
        self.assertEqual((lua.eval("npc.attack_log[1].before"), lua.eval("npc.attack_log[1].after")), (1, 1))
        self.assertEqual(lua.eval("Agent.situation(npc, 200).attacks_made[1].outcome"), "turrets_empty")

    def test_a_block_is_built_at_a_players_base_on_request(self):
        lua = runtime()
        lua.eval("Agent.observe()")
        lua.execute("prototypes.item['electronic-circuit'] = {}; if npc.agent then npc.agent.last_thought = nil end; Agent.event(npc, {kind='routine'}); Agent.tick(npc, 160)")
        rid = lua.eval("npc.agent.pending.request_id")
        answer = lua.eval("""Agent.submit({request_id='%s', intentions={{tool='build_at', priority='next',
            args={item='electronic-circuit', rate=30, place='Andre'}}}})""" % rid)
        self.assertEqual(answer.accepted, 1)
        self.assertIsNone(step(lua)[0])
        self.assertEqual(lua.eval("begun[1]"), "module")
        self.assertEqual((lua.eval("last_order.item"), lua.eval("last_order.rate"), lua.eval("last_order.player_index"),
                          lua.eval("last_order.at.x")), ("electronic-circuit", 30, 1, 100))

    def test_a_goal_the_planners_just_failed_is_refused(self):
        lua = runtime()
        lua.eval("Agent.observe()")
        lua.execute("Agent.designed.failed = function() return {'power-plant'} end")
        lua.execute("if npc.agent then npc.agent.last_thought = nil end; Agent.event(npc, {kind='routine'}); Agent.tick(npc, 160)")
        rid = lua.eval("npc.agent.pending.request_id")
        answer = lua.eval("""Agent.submit({request_id='%s', intentions={{tool='build', priority='now',
            args={goal='power-plant'}}}})""" % rid)
        self.assertEqual((answer.accepted, answer.rejected[1].reason), (0, "failed_recently"))
        self.assertEqual(lua.eval("Agent.situation(npc, 200).failed_recently[1]"), "power-plant")

    def test_an_urgent_build_pauses_the_plan_and_it_resumes_after(self):
        lua = runtime()
        lua.eval("Agent.observe()")
        lua.execute("""active = true; npc.design = {status='building', purpose='mall'}
                       Agent.event(npc, {kind='attacked'}); Agent.tick(npc, 160)""")
        rid = lua.eval("npc.agent.pending.request_id")
        lua.eval("""Agent.submit({request_id='%s', intentions={{tool='build', priority='now',
            args={goal='outpost-defense', why='Kevin attacked me'}}}})""" % rid)
        step(lua)
        self.assertEqual(lua.eval("npc.paused_design.purpose"), "mall")
        self.assertEqual(lua.eval("begun[1]"), "outpost-defense")
        lua.execute("npc.design.status = 'complete'; Agent.plan_finished(npc, 'outpost-defense', 'complete')")
        self.assertEqual(lua.eval("npc.design.purpose"), "mall")                  # back to the mall
        self.assertIsNone(lua.eval("npc.paused_design"))

    def test_a_defense_ordered_now_in_an_attack_stops_holding_the_home_once_it_is_won(self):
        # (ST-2, rehearsal 7g: build(now) starter-turrets waited behind a reinforce plan, the fight won and the
        # turrets standing, and held the home's rebuilds as an "urgent_trip" for 10 minutes)
        lua = runtime()
        lua.execute("""
            active = true
            npc.design = {status='building', purpose='reinforce'}
            npc.paused_design = {status='building', purpose='iron-smelting-expansion'}
            npc.alarm = {first=game.tick - 300, last=game.tick, hits=6, damage=90, lost=0, by={enemy=true}}
            Agent.order(npc, {tool='build', priority='now', args={goal='starter-turrets', why='biters on the plot'}})""")
        self.assertIsNone(step(lua)[0])                             # the paused slot is taken: it waits
        self.assertTrue(lua.eval("Agent.urgent(npc)"))              # urgent while the attack goes on
        lua.execute("game.tick = npc.alarm.last + 3601")           # a minute without a hit: won
        self.assertFalse(lua.eval("Agent.urgent(npc)"))             # the rebuilds go back
        self.assertEqual(lua.eval("npc.agent.agenda[1].priority"), "next")   # and the order still stands
        # An order from an earlier attack is not kept urgent by a new one.
        lua.execute("""npc.agent.agenda = {}
            npc.alarm = {first=game.tick - 300, last=game.tick, hits=6, damage=90, lost=0, by={enemy=true}}
            Agent.order(npc, {tool='build', priority='now', args={goal='starter-turrets'}})
            npc.alarm = {first=game.tick + 10, last=game.tick + 10, hits=1, damage=5, lost=0, by={enemy=true}}""")
        self.assertFalse(lua.eval("Agent.urgent(npc)"))

    def test_now_ends_with_the_attack_it_answered(self):
        lua = runtime()
        lua.execute("""
            active = true
            npc.design = {status='building', purpose='mall'}
            npc.paused_design = {status='building', purpose='coal-loop'}
            function attack() npc.alarm = {first=game.tick - 600, last=game.tick, hits=4, damage=80, lost=1,
                                           by={enemy=true}} end
            attack()
            Agent.order(npc, {tool='build', priority='now', args={goal='outpost-defense'}})
            npc.alarm = nil                                    -- (this one is wanted now for its own sake)
            Agent.order(npc, {tool='build', priority='now', args={goal='power-plant'}})
            attack()""")
        self.assertIsNone(step(lua)[0])                             # (the paused slot is taken: it waits)
        self.assertTrue(lua.eval("Agent.urgent(npc)"))
        self.assertEqual(lua.eval("npc.agent.agenda[1].priority"), "now")
        lua.execute("game.tick = npc.alarm.last + 3601")           # a minute without a hit: the attack is over
        self.assertTrue(lua.eval("Agent.urgent(npc)"))              # the power plant is still wanted now
        self.assertEqual([lua.eval(f"npc.agent.agenda[{i}].args.goal .. ':' .. npc.agent.agenda[{i}].priority")
                          for i in (1, 2)], ["power-plant:now", "outpost-defense:next"])

    def test_a_visit_with_no_fight_there_waits_for_the_build(self):
        # (LIVE-7c-b, normal-7c: Dax answered radio chatter with visit(now) "I'll cover while your iron ramps
        # up" three times in five minutes, nobody under attack; its science build stood still until the watchdog)
        lua = runtime()
        lua.execute("""active = true; npc.design = {status='building', purpose='science-tier'}
            Agent.order(npc, {tool='visit', priority='now', args={to='cover', x=200, y=0, minutes=3}})""")
        self.assertIsNone(step(lua)[0])                             # it stays at its build
        self.assertFalse(lua.eval("Agent.urgent(npc)"))
        self.assertEqual(lua.eval("npc.agent.agenda[1].priority"), "later")   # for when the plan is done
        lua.execute("active = false; npc.design.status = 'complete'")
        self.assertEqual(step(lua)[0].kind, "move")                  # then it goes
        # Biters there: a fight to cover, it goes now, build or not.
        lua.execute("""npc.agent.agenda = {}; active = true; npc.design.status = 'building'; busy_biters = 5
            Agent.order(npc, {tool='visit', priority='now', args={to='cover', x=200, y=0, minutes=3}})""")
        self.assertEqual(step(lua)[0].kind, "move")
        self.assertEqual(lua.eval("npc.agent.agenda[1].priority"), "now")
        # Parked while it was quiet, then biters come there: back to "now".
        lua.execute("""npc.agent.agenda = {}; busy_biters = 0
            Agent.order(npc, {tool='visit', priority='now', args={to='cover', x=200, y=0, minutes=3}})""")
        self.assertIsNone(step(lua)[0])
        lua.execute("busy_biters = 4; game.tick = game.tick + Agent.AWAY_LOOK + 1")
        self.assertEqual(step(lua)[0].kind, "move")
        self.assertEqual(lua.eval("npc.agent.agenda[1].priority"), "now")
        # Its stay already over when it goes quiet: it ends there, not sent back after the plan.
        lua.execute("""busy_biters = 0; npc.agent.agenda[1].arrived = game.tick - 3 * 3600 - 1
            npc.agent.agenda[1].fight_until = 0; game.tick = game.tick + Agent.AWAY_LOOK + 1""")
        step(lua)
        self.assertEqual(lua.eval("#npc.agent.agenda"), 0)
        # A player who asked for it (their chat in the thought it answers): theirs to call. Going to a player
        # unasked is like any other visit.
        lua.eval("Agent.observe()")
        lua.execute("""Agent.event(npc, {kind='chat', from='Andre', text='come over here'})
            if npc.agent then npc.agent.last_thought = nil end; Agent.tick(npc, game.tick)""")
        rid = lua.eval("npc.agent.pending.request_id")
        lua.eval("""Agent.submit({request_id='%s', intentions={{tool='visit', priority='now',
            args={to='Andre', minutes=3}}}})""" % rid)
        self.assertEqual(step(lua)[0].kind, "move")
        lua.execute("""npc.agent.agenda = {}
            Agent.order(npc, {tool='visit', priority='now', args={to='Andre', minutes=3}})""")
        self.assertIsNone(step(lua)[0])
        # A line to everyone is no request to come.
        lua.execute("""npc.agent.agenda = {}
            Agent.event(npc, {kind='chat', from='Andre', text='status?', to_everyone=true})
            if npc.agent then npc.agent.last_thought = nil end; Agent.tick(npc, game.tick)""")
        rid = lua.eval("npc.agent.pending.request_id")
        lua.eval("""Agent.submit({request_id='%s', intentions={{tool='visit', priority='now',
            args={to='Andre', minutes=3}}}})""" % rid)
        self.assertIsNone(step(lua)[0])
        # Restored by a fight, it goes ahead of what is only "next".
        lua.execute("""npc.agent.agenda = {}
            Agent.order(npc, {tool='visit', priority='now', args={to='cover', x=200, y=0, minutes=3}})""")
        step(lua)
        lua.execute("""Agent.order(npc, {tool='stockpile', priority='next', args={item='stone-wall', count=5}})
            busy_biters = 3; game.tick = game.tick + Agent.AWAY_LOOK + 1""")
        self.assertEqual(step(lua)[0].kind, "move")

    def test_any_away_action_with_nothing_urgent_waits_for_the_build(self):
        # (LIVE-7e-b, normal-7e: Elin's deliver(now) of 100 walls and 100 magazines, offered on the radio, took
        # it off its copper smelting build until the stall watchdog fired)
        lua = runtime()
        lua.execute("""active = true; npc.design = {status='building', purpose='electric-copper-smelting'}
            Agent.order(npc, {tool='deliver', priority='now', args={to='Andre',
                items={{name='stone-wall', count=100}, {name='firearm-magazine', count=100}}}})
            Agent.order(npc, {tool='give', priority='next', args={to='Andre', items={{name='iron-plate', count=50}}}})
            Agent.order(npc, {tool='stockpile', priority='next', args={item='stone-wall', count=200}})""")
        self.assertIsNone(step(lua)[0])                              # all three wait for the plan
        self.assertFalse(lua.eval("Agent.urgent(npc)"))
        self.assertEqual([lua.eval(f"npc.agent.agenda[{i}].priority") for i in (1, 2, 3)], ["later"] * 3)
        # Decided during an attack on its home, while that attack goes on: it goes now.
        lua.execute("""npc.agent.agenda = {}
            npc.alarm = {first=game.tick - 300, last=game.tick, hits=3, damage=40, lost=0, by={enemy=true}}
            Agent.order(npc, {tool='stockpile', priority='now', args={item='firearm-magazine', count=20}})""")
        self.assertEqual(step(lua)[0].kind, "craft")
        lua.execute("game.tick = npc.alarm.last + 3601")              # the attack over: it waits again
        self.assertIsNone(step(lua)[0])
        self.assertEqual(lua.eval("npc.agent.agenda[1].priority"), "later")

    def test_a_gift_brings_what_it_has_once_gathering_runs_long_or_comes_up_empty(self):
        lua = runtime()
        lua.execute("""held['stone-wall'] = 30; andre.character = {valid=true}; andre.surface = surface
            Agent.order(npc, {tool='give', priority='next', args={to='Andre', items={{name='stone-wall', count=100}}}})""")
        job, _ = step(lua)
        self.assertEqual(job.kind, "craft")                          # it sets about making the rest
        it = "npc.agent.agenda[1]"
        lua.execute(f"Agent.on_receipt(npc, {{intention={it}}}, {{status='failed', reason='source_empty'}}, game.tick)")
        job, _ = step(lua)
        self.assertEqual(job.kind, "move")                           # the 30 it has, to Andre
        self.assertEqual(lua.eval(f"{it}.args.items[1].count"), 30)
        # Nothing at hand at all once its time on it is spent: the gift is off.
        lua.execute(f"""npc.agent.agenda = {{}}; held['stone-wall'] = 0
            Agent.order(npc, {{tool='give', priority='next', args={{to='Andre', items={{{{name='stone-wall', count=100}}}}}}}})
            {it}.spent = Agent.GATHER_HAND""")
        step(lua)
        self.assertEqual(lua.eval("#npc.agent.agenda"), 0)
        self.assertIn("nothing_to_give", lua.eval("npc.agent.log[#npc.agent.log]"))

    def test_now_that_can_only_wait_stops_holding_everything_behind_it(self):
        lua = runtime()
        lua.execute("""
            Agent.designed.acquire = function(_, item, n)
                if item == 'gun-turret' then return nil end            -- nothing it can do about it yet
                return {kind='craft', recipe=item, count=n} end
            Agent.order(npc, {tool='stockpile', priority='now', args={item='gun-turret', count=10}})
            Agent.order(npc, {tool='stockpile', priority='next', args={item='stone-wall', count=20}})""")
        self.assertIsNone(step(lua)[0])                             # waits, and so does what is behind it
        self.assertTrue(lua.eval("Agent.urgent(npc)"))
        lua.execute("game.tick = game.tick + Agent.NOW_WAIT + 1")
        self.assertFalse(lua.eval("Agent.urgent(npc)"))
        job, _ = step(lua)
        self.assertEqual(job.recipe, "stone-wall")
        self.assertEqual(lua.eval("npc.agent.agenda[1].priority"), "next")   # (still wanted, just not first)

    def test_a_question_that_wants_a_place_is_answered_with_mark_location(self):
        lua = runtime()
        lua.eval("Agent.observe()")
        lua.execute("if npc.agent then npc.agent.last_thought = nil end; Agent.event(npc, {kind='routine'}); Agent.tick(npc, 160)")
        rid = lua.eval("npc.agent.pending.request_id")
        lua.eval("""Agent.submit({request_id='%s', intentions={{tool='ask', priority='now',
            args={to='Andre', question='Where should I build walls?', wants_place=true}}}})""" % rid)
        step(lua)
        self.assertIn("Where should I build walls?", lua.eval("said[1]"))
        lua.execute("andre.surface = surface; Agent.location_marked(andre, {x=120.4, y=-30.6})")
        answer = lua.eval("npc.agent.events[#npc.agent.events]")
        self.assertEqual((answer.kind, answer.place.x, answer.place.y), ("answer", 120, -31))
        self.assertEqual(lua.eval("storage.player_marks[1].x"), 120)


class RelationTests(unittest.TestCase):
    """Engineers are nobody's team by default: peace with the players, war when hurt enough."""

    def submit(self, lua, intentions):
        lua.execute("if npc.agent then npc.agent.last_thought = nil end; Agent.event(npc, {kind='routine'}); Agent.tick(npc, game.tick + 60)")
        rid = lua.eval("npc.agent.pending.request_id")
        return lua.eval("Agent.submit({request_id='%s', intentions={%s}})" % (rid, intentions))

    def test_it_knows_where_it_stands_with_each_player_and_engineer(self):
        lua = runtime()
        lua.execute("""
            storage.npcs[2] = {id=2, name='Kevin', force={name='npc-faction-2'}, home={x=300, y=0},
                               character={valid=true, position={x=300, y=0}}}
            relations['npc-faction-1|npc-faction-2'] = 'war'
            seen = Agent.situation(npc, 200)""")
        self.assertEqual(lua.eval("seen.players[1].relation"), "peace")
        self.assertEqual(lua.eval("seen.engineers[1].relation"), "war")

    def test_hurt_enough_by_a_player_it_goes_to_war_and_means_to_take_them_out(self):
        lua = runtime()
        lua.eval("Agent.observe()")
        lua.execute("""
            andre_force.valid = true
            hurt = require('scripts.character').damage_listener
            for i = 1, 11 do hurt('npc:1', 'player:1', 10, npc.character) end""")   # 220: it puts up with it
        self.assertEqual(len(lua.eval("declarations")), 0)
        lua.execute("hurt('npc:1', 'player:1', 10, npc.character)")
        self.assertEqual(lua.eval("declarations[1]"), "player:war")
        self.assertEqual(lua.eval("npc.foe.name"), "Andre")
        self.assertIn("Ada is now at war with Andre", lua.eval("printed"))
        self.assertEqual(lua.eval("npc.agent.events[#npc.agent.events].kind"), "war")
        self.assertEqual((lua.eval("npc.agent.agenda[1].tool"), lua.eval("npc.agent.agenda[1].priority")),
                         ("arm_up", "now"))
        # at war, more hits change nothing more
        lua.execute("hurt('npc:1', 'player:1', 50, npc.character)")
        self.assertEqual((len(lua.eval("declarations")), lua.eval("#npc.agent.agenda")), (1, 1))

    def test_an_engineer_that_destroys_its_building_is_at_war_with_it_at_once(self):
        lua = runtime()
        lua.execute("""
            kevin = {id=2, name='Kevin', force={name='npc-faction-2', valid=true}, home={x=300, y=0},
                     character={valid=true, position={x=300, y=0}}}
            storage.npcs[2] = kevin
            require('scripts.character').damage_listener('npc:1', 'npc:2', 0, {}, 'burner-mining-drill')""")
        self.assertEqual(lua.eval("declared[1]"), "Kevin")
        self.assertIn("destroyed a burner mining drill", lua.eval("printed"))

    def test_players_are_dealt_with_too_and_a_refusal_is_heard(self):
        lua = runtime()
        lua.eval("Agent.observe()")
        answer = self.submit(lua, "{tool='diplomacy', args={action='offer_peace', target='Andre'}},"
                                  "{tool='diplomacy', args={action='offer_trade', target='Andre'}},"
                                  "{tool='diplomacy', args={action='declare_war', target='Nobody'}}")
        self.assertEqual(answer.accepted, 1)
        self.assertEqual(lua.eval("to_players[1]"), "Andre:offer_peace")
        results = lua.eval("Agent.observe()").results
        rejected = sorted(r.reason for r in results.values() if r.status == "rejected")
        self.assertEqual(rejected, ["action", "target"])       # the strategist hears why
        answer = self.submit(lua, "{tool='diplomacy', args={action='declare_war', target='Andre'}}")
        self.assertEqual((answer.accepted, lua.eval("to_players[2]"), lua.eval("npc.foe.name")),
                         (1, "Andre:declare_war", "Andre"))

    def test_it_goes_after_a_player_where_they_are(self):
        lua = runtime()
        lua.eval("Agent.observe()")
        lua.execute("andre.character = {valid=true, surface=surface, position={x=100, y=50}}; held['firearm-magazine'] = 50")
        answer = self.submit(lua, "{tool='attack', priority='now', args={target='Andre', minutes=2}}")
        self.assertEqual(answer.accepted, 1)
        job, meta = step(lua)
        self.assertEqual(lua.eval("to_players[1]"), "Andre:declare_war")     # war first
        self.assertEqual((job.kind, meta.step), ("move", "attack"))
        # toward a stop 68 short of them (out of their turrets' reach), a 40-tile leg at a time
        self.assertAlmostEqual(job.position.x, 36.78, places=2)
        self.assertAlmostEqual(job.position.y, 17.89, places=2)
        lua.execute("andre.character.position = {x=40, y=0}")                  # they moved, close
        job, _ = step(lua)
        self.assertEqual((job.position.x, job.position.y), (29, 0))          # in, to 12 short of them
        lua.execute("npc.character.position = {x=35, y=0}")
        self.assertEqual(step(lua), (False, "attack"))                          # there: its combat fights
        lua.execute("andre.character = nil")
        step(lua)
        self.assertEqual(lua.eval("#npc.agent.agenda"), 0)                      # they are down: over
        self.assertIsNone(lua.eval("npc.combat_objective"))                    # and its mission with it

    def test_joining_a_player_is_an_alliance_and_leaving_is_peace(self):
        lua = runtime()
        lua.eval("Agent.observe()")
        self.submit(lua, "{tool='join', args={to='Andre', join=true}}")
        self.assertEqual(lua.eval("declarations[1]"), "player:ally")
        self.submit(lua, "{tool='join', args={to='Andre', join=false}}")
        self.assertEqual(lua.eval("declarations[2]"), "player:peace")

    def test_a_silent_engineer_says_nothing(self):
        lua = runtime()
        lua.eval("Agent.observe()")
        lua.execute("npc.silent = true")
        answer = self.submit(lua, "{tool='say', args={text='You will regret this', to='Andre'}}")
        self.assertEqual((answer.accepted, answer.rejected[1].reason), (0, "silent"))
        self.assertEqual(len(lua.eval("said")), 0)

    def test_fights_on_the_way_do_not_make_it_give_up(self):
        lua = runtime()
        lua.execute("""
            andre.character = {valid=true, surface=surface, position={x=100, y=50}}
            Agent.order(npc, {tool='attack', priority='now', args={target='Andre', minutes=2}})
            it = npc.agent.agenda[1]
            for i = 1, 5 do Agent.on_receipt(npc, {intention=it}, {status='canceled', reason='combat_interrupt'}, 200) end""")
        self.assertEqual(lua.eval("#npc.agent.agenda"), 1)
        lua.execute("for i = 1, 3 do Agent.on_receipt(npc, {intention=it}, {status='failed', reason='no_path'}, 200) end")
        self.assertEqual(lua.eval("#npc.agent.agenda"), 0)

    def test_it_stops_short_of_their_base_looks_and_backs_off_from_turrets_it_cannot_beat(self):
        lua = runtime()
        lua.eval("Agent.observe()")
        lua.execute("""
            held['firearm-magazine'] = 50
            kevin_force = {name='npc-faction-2', valid=true, get_cease_fire=function() return false end}
            storage.npcs[2] = {id=2, name='Kevin', force=kevin_force, home={x=300, y=0},
                               character={valid=true, position={x=300, y=0}}}
            ada_force.get_cease_fire = function() return false end
            turret = {valid=true, type='ammo-turret', position={x=290, y=0}}
            surface.find_entities_filtered = function(f)
              if f.force == kevin_force and f.type[1] == 'ammo-turret' then return {turret} end
              return {}
            end
            Agent.order(npc, {tool='attack', priority='now', args={target='Kevin', minutes=2}})""")
        job, _ = step(lua)
        self.assertEqual((job.kind, job.position.x), ("move", 41))        # on its way, a leg at a time
        lua.execute("npc.character.position = {x=235, y=0}")              # 65 short of their base
        step(lua)
        self.assertEqual(lua.eval("#npc.agent.agenda"), 0)                 # it does not walk into them
        aborted = lua.eval("npc.agent.events[#npc.agent.events]")
        self.assertEqual((aborted.kind, aborted.turrets), ("attack_aborted", 1))

    def test_hurt_in_the_attack_it_falls_back_heals_and_goes_in_again(self):
        lua = runtime()
        lua.execute("""
            held['firearm-magazine'] = 50
            kevin_force = {name='npc-faction-2', valid=true, get_cease_fire=function() return false end}
            storage.npcs[2] = {id=2, name='Kevin', force=kevin_force, home={x=300, y=0},
                               character={valid=true, position={x=300, y=0}}}
            ada_force.get_cease_fire = function() return false end
            surface.find_entities_filtered = function() return {} end
            npc.character.position = {x=235, y=0}
            Agent.order(npc, {tool='attack', priority='now', args={target='Kevin', minutes=2}})""")
        step(lua)                                                          # stops at 235, nothing defends: in
        lua.execute("npc.character.position = {x=290, y=0}; npc.character.health = 60")
        job, _ = step(lua)
        self.assertEqual((job.kind, job.position.x), ("move", 236))       # back where it stopped
        lua.execute("npc.character.position = {x=235, y=0}")
        self.assertEqual(step(lua), (False, "attack"))                    # out of the fight, healing
        lua.execute("npc.character.health = 220")
        job, _ = step(lua)
        self.assertEqual(lua.eval("npc.agent.agenda[1].regroup"), None)    # healed: in again

    def test_hurt_on_a_march_it_stops_and_heals_before_walking_on(self):
        lua = runtime()
        lua.execute("""
            andre.character = {valid=true, surface=surface, position={x=300, y=0}}; held['firearm-magazine'] = 50
            Agent.order(npc, {tool='attack', priority='now', args={target='Andre', minutes=2}})
            npc.character.health = 100""")
        self.assertEqual(step(lua), (False, "attack"))                  # resting, out of the fight
        lua.execute("npc.combat = {target={valid=true}}")                 # biters on it: no rest, walk on
        job, _ = step(lua)
        self.assertEqual(job.kind, "move")
        lua.execute("npc.combat = nil; npc.character.health = 240")
        job, _ = step(lua)
        self.assertEqual(job.kind, "move")                              # healed: on its way

    def test_a_foe_it_made_peace_with_is_its_foe_no_longer(self):
        lua = runtime()
        lua.execute("""
            andre_force.valid = true
            npc.foe = {key='player:1', name='Andre', since=0}
            relations['npc-faction-1|player'] = 'war'
            Agent.tick(npc, 200)""")
        self.assertEqual(lua.eval("npc.foe.name"), "Andre")             # still at war
        lua.execute("relations['npc-faction-1|player'] = 'peace'; Agent.tick(npc, 260)")
        self.assertIsNone(lua.eval("npc.foe"))

    def test_a_loyal_engineer_does_not_attack_the_player_it_joined(self):
        lua = runtime()
        lua.execute("""
            require('scripts.personality').has = function(_, name) return name == 'loyal' end
            andre.character = {valid=true, surface=surface, position={x=100, y=50}}
            npc.joined = {player_index=1, name='Andre'}
            ok, why = Agent.order(npc, {tool='attack', priority='now', args={target='Andre', minutes=2}})""")
        self.assertEqual((lua.eval("ok"), lua.eval("why")), (False, "loyal"))

    def test_an_attack_waiting_behind_another_gets_its_full_time_once_it_starts(self):
        lua = runtime()
        lua.execute("""
            andre.character = {valid=true, surface=surface, position={x=3000, y=0}}; held['firearm-magazine'] = 50
            Agent.order(npc, {tool='attack', priority='now', args={target='Andre', minutes=2}})
            it = npc.agent.agenda[1]
            it.created = game.tick - 60000             -- decided long ago, waiting behind another raid
            game.tick = game.tick + 1
            step()
            game.tick = game.tick + 30000              -- 8 minutes of marching since it set off
            step()""")
        self.assertEqual(lua.eval("#npc.agent.agenda"), 1)          # still going
        lua.execute("game.tick = game.tick + 50000; step()")         # far past its time
        self.assertEqual(lua.eval("#npc.agent.agenda"), 0)

    def test_out_of_magazines_on_the_way_it_makes_more_from_its_plates(self):
        lua = runtime()
        lua.execute("""
            unlock('firearm-magazine')
            andre.character = {valid=true, surface=surface, position={x=3000, y=0}}
            andre.position = {x=3000, y=0}
            held['iron-plate'] = 50; held['firearm-magazine'] = 6
            Agent.order(npc, {tool='attack', priority='now', args={target='Andre', minutes=2}})
            npc.agent.agenda[1].step = 2                 -- set off already, and used up its magazines""")
        job, _ = step(lua)
        self.assertEqual((job.kind, job.recipe, job.count), ("craft", "firearm-magazine", 12))
        lua.execute("held['firearm-magazine'] = 25")
        job, _ = step(lua)
        self.assertEqual(job.kind, "move")

    def test_with_bobs_magazines_it_makes_the_empty_magazines_first(self):
        # Bob's Warfare: a magazine is an empty magazine ("empty-recipe", 2 plates) and 2 plates. The magazines are
        # queued in the background once they can be made, and the game's queue makes the empty ones first (W-12).
        lua = runtime()
        lua.execute("""
            unlock('empty-recipe', {{type='item', name='iron-plate', amount=2}})
            ada_force.recipes['empty-recipe'].products = {{type='item', name='empty-magazine', amount=1}}
            unlock('firearm-magazine', {{type='item', name='empty-magazine', amount=1},
                                        {type='item', name='iron-plate', amount=2}})
            andre.character = {valid=true, surface=surface, position={x=3000, y=0}}
            andre.position = {x=3000, y=0}
            held['iron-plate'] = 50; held['firearm-magazine'] = 6
            Agent.order(npc, {tool='attack', priority='now', args={target='Andre', minutes=2}})
            npc.agent.agenda[1].step = 2""")
        job, _ = step(lua)
        self.assertEqual((job.kind, job.recipe, job.count, job.background), ("craft", "firearm-magazine", 12, True))

    def test_after_a_far_raid_it_marches_home_by_the_safe_route(self):
        lua = runtime()
        lua.execute("""
            held['firearm-magazine'] = 50
            andre.character = {valid=true, surface=surface, position={x=3000, y=0}}
            npc.character.position = {x=2990, y=0}
            Agent.order(npc, {tool='attack', priority='now', args={target='Andre', minutes=2}})
            npc.agent.agenda[1].step = 2
            andre.character = nil
            step()                                   -- they are down: the raid is over, far from home""")
        self.assertEqual(lua.eval("#npc.agent.agenda"), 0)
        job, meta = step(lua)
        self.assertEqual((job.kind, meta.intention.tool), ("move", "homeward"))
        self.assertLess(job.position.x, 2990)         # toward home (0, 0)
        lua.execute("npc.character.position = {x=10, y=0}")
        self.assertIsNone(step(lua)[0])              # home: done
        self.assertIsNone(lua.eval("npc.homeward"))
        # Something of its own where a far raid ends (an outpost in biter country): still the safe route home.
        lua.execute("""
            Agent.designed.on_own_ground = function(n, pos) return true end
            andre.character = {valid=true, surface=surface, position={x=3000, y=0}}
            npc.character.position = {x=2990, y=0}
            Agent.order(npc, {tool='attack', priority='now', args={target='Andre', minutes=2}})
            npc.agent.agenda[1].step = 2
            andre.character = nil
            step()""")
        self.assertEqual(lua.eval("npc.homeward and npc.homeward.tool"), "homeward")
        # Near home on its own ground (its plot's edge, its wall, W-36): home already; off it: home.
        self.assertFalse(lua.eval("Agent.off_own_ground(npc, {x=120, y=0})"))
        lua.execute("Agent.designed.on_own_ground = function() return false end")
        self.assertTrue(lua.eval("Agent.off_own_ground(npc, {x=120, y=0})"))

    def test_a_far_raid_needs_magazines_to_set_off_and_turns_back_with_some_left(self):
        lua = runtime()
        lua.eval("Agent.observe()")
        lua.execute("""
            andre.character = {valid=true, surface=surface, position={x=3000, y=0}}
            andre.position = {x=3000, y=0}
            held['firearm-magazine'] = 40
            Agent.order(npc, {tool='attack', priority='now', args={target='Andre', minutes=2}})
            step()""")
        self.assertEqual(lua.eval("#npc.agent.agenda"), 0)                     # 40 will not do for 3000 tiles
        self.assertEqual(lua.eval("npc.agent.events[#npc.agent.events].why"), "not enough ammunition for the trip")
        lua.execute("""
            held['firearm-magazine'] = 120
            Agent.order(npc, {tool='attack', priority='now', args={target='Andre', minutes=2}})
            step()""")
        self.assertEqual(lua.eval("npc.agent.agenda[1].step"), 2)               # enough: off it goes
        lua.execute("npc.character.position = {x=1500, y=0}; held['firearm-magazine'] = 25; step()")
        self.assertEqual(lua.eval("#npc.agent.agenda"), 0)                     # halfway, running short: back
        self.assertEqual(lua.eval("npc.agent.events[#npc.agent.events].why"), "out of ammunition")

    def test_a_leg_it_cannot_walk_is_walked_round_and_the_march_goes_on(self):
        lua = runtime()
        lua.execute("""
            held['firearm-magazine'] = 50
            andre.character = {valid=true, surface=surface, position={x=100, y=50}}
            Agent.order(npc, {tool='attack', priority='now', args={target='Andre', minutes=2}})
            it = npc.agent.agenda[1]""")
        job, meta = step(lua)
        self.assertEqual(job.kind, "move")
        for _ in range(4):   # no path there (a lake), again and again: blocked, re-planned, still going
            lua.execute("Agent.on_receipt(npc, {intention=it}, {status='failed', reason='no_path'}, 300)")
            self.assertIsNone(lua.eval("it.route"))
            step(lua)
        self.assertEqual((lua.eval("#npc.agent.agenda"), lua.eval("#blocked")), (1, 4))

    def test_a_long_march_goes_a_leg_at_a_time_so_it_looks_again_on_the_way(self):
        lua = runtime()
        lua.execute("""
            held['firearm-magazine'] = 150; held['iron-plate'] = 100
            andre.character = {valid=true, surface=surface, position={x=2000, y=0}}
            Agent.order(npc, {tool='attack', priority='now', args={target='Andre', minutes=2}})
            npc.agent.agenda[1].step = 2""")
        job, _ = step(lua)
        self.assertEqual(job.kind, "move")
        self.assertLessEqual(job.position.x, 41 + 1)        # a leg of at most 40 tiles (+1: the test's land finder)

    def test_a_warlord_is_at_war_with_everyone_from_the_start_and_silent(self):
        lua = runtime()
        lua.execute("""
            assigned = nil
            local P = require('scripts.personality')
            P.ensure = function() return {seed=7} end
            P.assign = function(_, names) assigned = table.concat(names, ','); return true end
            storage.founding_force = andre_force
            storage.npcs[2] = {id=2, name='Kevin', force={name='npc-faction-2'}, home={x=300, y=0},
                               character={valid=true, position={x=300, y=0}}}
            ok = Agent.warlord(npc)
            seen = Agent.situation(npc, 200)""")
        self.assertTrue(lua.eval("ok"))
        self.assertIn("aggressive", lua.eval("assigned"))
        self.assertEqual((lua.eval("declarations[1]"), lua.eval("declared[1]")), ("player:war", "Kevin"))
        self.assertTrue(lua.eval("seen.silent"))
        self.assertIn("Take out the players", lua.eval("seen.mission"))

def team_runtime():
    """Ada and Bob on one team with the players (the team world): Bob listens on the radio."""
    lua = runtime()
    lua.execute("""
        settings = {global={['npc-factions-scenario']={value='team'}}}
        local TW = require('scripts.team_world')
        storage.team = TW.layout({x=0, y=0})
        storage.team.status, storage.team.homes, storage.team.added, storage.team.cleared = 'ready', {}, {}, 0
        andre_force.research_queue, andre_force.research_progress = {}, 0
        andre_force.technologies = {}
        game.forces = {player=andre_force}
        bob_force = {name='npc-faction-2', valid=true, get_friend=function() return true end,
                     get_cease_fire=function() return true end, recipes={}}
        bob = {id=2, name='Bob', force=bob_force, home={x=384, y=-384}, design={status='complete'},
               character={valid=true, health=250, max_health=250, position={x=384, y=-384}, surface=surface,
                          get_main_inventory=function() return inventory end,
                          get_inventory=function(i) if i == defines.inventory.character_main then return inventory end end}}
        storage.npcs[2] = bob
        Agent.observe()                              -- the companion is connected
        local n = 0
        function submit(intentions)
            n = n + 1
            npc.agent = npc.agent or {events={}, agenda={}, notes={}, results={}, seq=0}
            npc.agent.pending = {request_id='t' .. n, tick=game.tick, events={}}
            return Agent.submit({request_id='t' .. n, intentions=intentions})
        end
    """)
    return lua


class TeamTests(unittest.TestCase):
    def test_on_one_team_there_are_no_deals_treaties_or_attacks_on_teammates(self):
        lua = team_runtime()
        answer = lua.eval("""submit({
            {tool='trade', args={to='Andre', give_item='iron-plate', give_count=400, want_item='stone-wall',
                                 want_count=50}},
            {tool='diplomacy', args={target='Bob', action='declare_war'}},
            {tool='join', args={to='Andre'}},
            {tool='attack', args={target='Bob'}},
            {tool='attack', args={target='Andre'}},
            {tool='relocate', args={}},
            {tool='build_at', args={item='iron-gear-wheel', rate=1, place='there'}}})""")
        reasons = [answer.rejected[i].reason for i in range(1, 8)]
        self.assertEqual(reasons, ["one_team"] * 7)

    def test_a_teammates_stray_fire_starts_no_war(self):
        lua = team_runtime()
        lua.execute("require('scripts.character').damage_listener('npc:1', 'player:1', 5000, nil, 'gun-turret')")
        self.assertIsNone(lua.eval("npc.foe"))
        self.assertEqual(lua.eval("#declarations"), 0)

    def test_what_it_says_goes_out_on_the_team_radio_and_only_its_name_wakes_a_teammate(self):
        lua = team_runtime()
        lua.execute("game.tick = 1000")
        self.assertEqual(lua.eval("submit({{tool='say', args={text='Biters on the north wall — holding.'}}}).accepted"), 1)
        self.assertEqual(lua.eval("printed"), "[Ada] Biters on the north wall, holding.")   # no dashes on the radio
        self.assertEqual(lua.eval("bob.agent.heard[1].text"), "Biters on the north wall, holding.")
        self.assertEqual(lua.eval("#bob.agent.events"), 0)                  # heard, not woken
        # too soon for another line to everyone; an answer to a player is fine
        self.assertEqual(lua.eval("submit({{tool='say', args={text='Still holding.'}}}).rejected[1].reason"),
                         "spoke_just_now")
        lua.execute("game.tick = 1200")
        self.assertEqual(lua.eval("submit({{tool='say', args={text='Got it.', to='Andre'}}}).accepted"), 1)
        lua.execute("game.tick = 2000")
        lua.eval("submit({{tool='say', args={text='Bob, bring ammo to the north wall.'}}})")
        self.assertEqual(lua.eval("bob.agent.events[1].kind"), "radio")      # named: it wakes
        view = lua.eval("Agent.situation(bob, 2100)")
        self.assertTrue(view.team)
        self.assertEqual(view.plot.name, "npc-2")
        self.assertEqual(view.radio[2].text, "Bob, bring ammo to the north wall.")
        self.assertIsNone(view.diplomacy)
        self.assertEqual(lua.eval("#Agent.situation(bob, 2100).team_research.queue"), 0)


class LoadTimeRequireTests(unittest.TestCase):
    def test_the_mod_requires_its_modules_only_while_it_loads(self):
        # Factorio stops the game on a require after control.lua has loaded ("Require can't be used
        # outside of control.lua parsing"), even inside pcall it fails: requires belong at the top.
        import re
        late = []
        for path in sorted((ROOT / "mod").rglob("*.lua")):
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                code = line.split("--", 1)[0]
                if re.match(r"\s+\S", code) and re.search(r"\brequire\s*[(\"']", code):
                    late.append(f"{path.relative_to(ROOT)}:{n}")
        self.assertEqual(late, [])


def raid_on_kevin(lua):
    """Ada (at 235, 0) on her way to raid Kevin's base at (300, 0), stopped short of it."""
    lua.execute("""
        held['firearm-magazine'] = 50
        kevin_force = {name='npc-faction-2', valid=true, get_cease_fire=function() return false end}
        storage.npcs[2] = {id=2, name='Kevin', force=kevin_force, home={x=300, y=0},
                           character={valid=true, position={x=300, y=0}}}
        ada_force.get_cease_fire = function() return false end
        surface.find_entities_filtered = function() return {} end
        npc.character.position = {x=235, y=0}
        Agent.order(npc, {tool='attack', priority='now', args={target='Kevin', minutes=2}})
        it = npc.agent.agenda[1]""")


class WarTests(unittest.TestCase):
    def test_called_home_it_breaks_off_the_raid_packs_up_defends_and_then_goes_again(self):
        lua = runtime()
        lua.eval("Agent.observe()")
        raid_on_kevin(lua)
        job, _ = step(lua)
        self.assertEqual(job.kind, "move")                                     # staged: going in
        self.assertIsNotNone(lua.eval("it.staged"))
        lua.execute("""
            turret = {valid=true, position={x=240, y=0}}
            npc.combat_turrets = {dropped={turret}}
            npc.combat_objective = {x=300, y=0}
            require('scripts.war').alarm(npc, {x=10, y=0}, kevin_force, 20, nil, game.tick)   -- Kevin at home
            Agent.tick(npc, game.tick)""")
        self.assertEqual(lua.eval("npc.recall.why"), "attacked by an engineer or a player")
        self.assertIsNone(lua.eval("it.staged"))                               # the trip starts over later
        self.assertIsNone(lua.eval("npc.combat_objective"))
        self.assertEqual(lua.eval("canceled[1]"), "combat_interrupt")          # the job in hand broken off
        event = lua.eval("npc.agent.pending.events[1]")                       # its strategist is asked
        self.assertEqual((event.kind, event.by, event.position.x), ("home_attacked", "npc-faction-2", 10))
        job, meta = step(lua)
        self.assertEqual((job.kind, meta.intention.tool), ("mine", "recall"))  # its turret comes along
        lua.execute("turret.valid = false")
        job, _ = step(lua)
        self.assertEqual(job.kind, "move")
        self.assertLess(job.position.x, 235)                                   # home
        lua.execute("npc.character.position = {x=12, y=0}")
        self.assertEqual(step(lua), (False, "defend_home"))                    # standing by where it is hit
        lua.execute("game.tick = game.tick + 3700")                            # a quiet minute: over
        self.assertIsNone(step(lua)[0])
        self.assertIsNone(lua.eval("npc.recall"))
        job, meta = step(lua)
        self.assertEqual((job.kind, meta.intention.tool), ("move", "attack"))  # and off again

    def test_called_home_it_arms_before_it_walks_into_the_attack(self):
        # (run 52, 2026-10-26: a pistol and 13 magazines against 18 biters; the user: turrets at its feet, its gun)
        lua = runtime()
        lua.eval("Agent.observe()")
        raid_on_kevin(lua)
        step(lua)
        lua.execute("""
            kit_calls = 0
            Agent.kit = {next=function(_, _, acquire, now)
                kit_calls = kit_calls + 1
                if kit_calls == 1 and acquire and now then
                    return {kind='craft', recipe='submachine-gun', count=1, background=true}
                end
            end}
            require('scripts.war').alarm(npc, {x=10, y=0}, kevin_force, 20, nil, game.tick)
            Agent.tick(npc, game.tick)""")
        job, meta = step(lua)
        self.assertEqual((job.kind, job.recipe, meta.intention.tool), ("craft", "submachine-gun", "recall"))
        job, _ = step(lua)
        self.assertEqual(job.kind, "move")                                     # armed: home
        self.assertEqual(lua.eval("kit_calls"), 2)

    def test_called_home_to_a_wave_it_would_lose_to_it_stands_off_until_the_odds_turn(self):
        # (O-2: biters outrun it; run 34's engineer walked into a wave of 60 and died. combat.lua keeps it clear.)
        lua = runtime()
        lua.eval("Agent.observe()")
        raid_on_kevin(lua)
        step(lua)
        lua.execute("""
            clear = true
            require('scripts.combat').keeping_clear = function() return clear end
            require('scripts.war').alarm(npc, {x=10, y=0}, kevin_force, 20, nil, game.tick)
            Agent.tick(npc, game.tick)""")
        # out of their sight, and not idle: the rest of its work goes on (O-4: it stood off 14 minutes of 30)
        self.assertEqual(step(lua), (None, None))
        self.assertIsNotNone(lua.eval("npc.standing_off"))
        self.assertIsNotNone(lua.eval("npc.recall"))                           # still called home
        # what it means to do meanwhile goes on (arming up), but no trip away
        lua.execute("Agent.order(npc, {tool='arm_up', priority='now', args={focus='all', ammo=100}})")
        step(lua)
        tools = [it.tool for it in lua.eval("npc.agent.agenda").values()]
        self.assertNotIn("arm_up", tools)                                      # armed up (here: nothing to make)
        self.assertIn("attack", tools)
        self.assertIsNone(lua.eval("npc.agent.agenda[1].started"))             # the raid waits
        # kept from all work for long: its strategist hears of it, once a while
        lua.execute("npc.current_job = nil; npc.combat = {wave={n=12, ratio=0, nearest={x=40, y=0}}}")
        self.assertFalse(lua.eval("Agent.kept_from_work(npc, game.tick)"))
        self.assertTrue(lua.eval("Agent.kept_from_work(npc, game.tick + Agent.KEPT_IDLE)"))
        self.assertFalse(lua.eval("Agent.kept_from_work(npc, game.tick + Agent.KEPT_IDLE + 60)"))
        lua.execute("clear = false")
        job, meta = step(lua)
        self.assertEqual((job.kind, meta.intention.tool), ("move", "recall"))  # the odds turned: in it goes

    def test_biters_camped_at_its_buildings_are_cleared_when_it_can_and_an_attack_when_it_cannot(self):
        # (O-6: nothing answered biters that stood at its buildings without hitting them)
        lua = runtime()
        lua.execute("""group = {x=60, y=0, n=4, ratio=2}
            require('scripts.combat').intruders = function() return group end
            require('scripts.combat_odds').ENGAGE = 1.2
            game.forces = game.forces or {enemy={name='enemy'}}""")
        self.assertEqual(lua.eval("Agent.lingering(npc, game.tick)"), "clear")
        self.assertEqual((lua.eval("npc.clearing.x"), lua.eval("npc.clearing.n")), (60, 4))
        self.assertIsNone(lua.eval("npc.alarm"))
        lua.execute("group = {x=60, y=0, n=60, ratio=0.1}")
        self.assertEqual(lua.eval("Agent.lingering(npc, game.tick)"), "arm")       # first it arms up for them
        self.assertTrue(lua.eval("Agent.has_intention(npc, 'arm_up')"))
        self.assertIsNone(lua.eval("npc.clearing"))
        self.assertEqual(lua.eval("npc.alarm.position.x"), 60)                    # and it is an attack going on
        self.assertIsNotNone(lua.eval("npc.alarm.lingering"))
        self.assertEqual(lua.eval("Agent.lingering(npc, game.tick + 300)"), "alarm")   # (once a while)
        lua.execute("group = nil")
        self.assertIsNone(lua.eval("Agent.lingering(npc, game.tick)"))
        self.assertIsNone(lua.eval("npc.clearing"))

    def test_called_home_it_waits_a_little_for_its_kit_being_made(self):
        lua = runtime()
        lua.eval("Agent.observe()")
        raid_on_kevin(lua)
        step(lua)
        lua.execute("""
            Agent.kit = {next=function() return nil, 'crafting' end}
            require('scripts.war').alarm(npc, {x=10, y=0}, kevin_force, 20, nil, game.tick)
            Agent.tick(npc, game.tick)""")
        self.assertEqual(step(lua), (False, "arming"))                        # its gun is on its way
        lua.execute("game.tick = game.tick + 1900")
        job, _ = step(lua)
        self.assertEqual(job.kind, "move")                                     # not for ever
        self.assertGreater(job.position.x, 12)

    def test_biters_at_their_base_it_waits_back_out_of_their_way_then_goes_in(self):
        lua = runtime()
        raid_on_kevin(lua)
        step(lua)                                                              # staged at 235
        lua.execute("busy_biters = 6; npc.character.position = {x=260, y=0}")
        job, _ = step(lua)
        self.assertEqual((job.kind, job.position.x), ("move", 236))           # back where it stopped
        lua.execute("npc.character.position = {x=235, y=0}")
        self.assertEqual(step(lua), (False, "attack"))                        # waiting them out
        lua.execute("busy_biters = 0")
        job, _ = step(lua)
        self.assertGreater(job.position.x, 235)                               # gone: in it goes
        lua.execute("busy_biters = 6")
        self.assertEqual(step(lua), (False, "attack"))
        lua.execute("game.tick = game.tick + 18001")                          # they keep at it a long while
        job, _ = step(lua)
        self.assertGreater(job.position.x, 235)                               # in anyway


    def test_a_friend_under_attack_it_arms_goes_defends_leaves_turrets_and_goes_home(self):
        lua = runtime()
        lua.execute("""
            War = require('scripts.war')
            loaded = {valid=true, type='ammo-turret', get_inventory=function() return {is_empty=function() return false end} end}
            surface.find_entities_filtered = function(f)
                if f.type[1] == 'ammo-turret' then return {loaded, loaded} end   -- its home's turrets
                return {}
            end
            local function no() return false end
            biters = {name='enemy', get_friend=no, get_cease_fire=no}
            cora_force = {name='npc-faction-3', valid=true, get_friend=no, get_cease_fire=no}
            cora = {id=3, name='Cora', force=cora_force, home={x=500, y=0},
                    character={valid=true, position={x=500, y=0}, surface={find_entities_filtered=function(f)
                        if f.type[1] == 'ammo-turret' then return {} end
                        return {{valid=true, force=biters, max_health=15}}
                    end}}}
            storage.npcs[3] = cora
            ada_force.get_cease_fire = function(f) return false end
            War.alarm(cora, {x=510, y=0}, biters, 10, nil, game.tick)
            held['firearm-magazine'] = 60; held['gun-turret'] = 2; unlock('gun-turret')
            inventory.remove = function(spec) held[spec.name] = held[spec.name] - spec.count; return spec.count end
            Agent.tick(npc, game.tick)""")
        self.assertEqual(lua.eval("npc.helping.name"), "Cora")
        job, meta = step(lua)
        self.assertEqual((job.kind, meta.intention.tool), ("move", "help"))     # armed already: on its way
        lua.execute("npc.character.position = {x=495, y=0}")
        self.assertEqual(step(lua), (False, "helping"))                         # beside them while it lasts
        lua.execute("game.tick = game.tick + 3700")                              # over
        for _ in range(2):                                                       # two turrets, loaded, theirs
            job, meta = step(lua)
            self.assertEqual((job.kind, job.entity_name, meta.step), ("build", "gun-turret", "gift"))
            lua.execute("""
                t = {valid=true, insert=function(stack) return stack.count end}
                Agent.on_receipt(npc, {intention=npc.helping, step='gift'}, {status='complete', entity=t}, game.tick)""")
            self.assertEqual(lua.eval("t.force.name"), "npc-faction-3")
        self.assertEqual(lua.eval("held['firearm-magazine']"), 40)
        job, meta = step(lua)
        self.assertIsNone(lua.eval("npc.helping"))
        self.assertEqual(lua.eval("npc.homeward.tool"), "homeward")             # and home, at once
        self.assertEqual(meta.intention.tool, "homeward")

    def test_a_raid_far_from_home_ends_with_the_safe_route_home(self):
        lua = runtime()
        raid_on_kevin(lua)
        lua.execute("""
            storage.npcs[2].home = {x=3000, y=0}; storage.npcs[2].character.position = {x=3000, y=0}
            it.step = 2; it.staged = {x=2930, y=0}; it.arrived = game.tick; it.creep = {phase='hold', turrets={}}
            npc.character.position = {x=2990, y=0}
            defines.shooting = {not_shooting=0, shooting_selected=1}
            game.tick = game.tick + 2 * 3600 + 1""")
        job, meta = step(lua)                                                 # over: done, and home at once
        self.assertEqual(lua.eval("#npc.agent.agenda"), 0)                    # (not a round left to upkeep)
        self.assertEqual((job.kind, meta.intention.tool), ("move", "homeward"))  # by the safe route
        self.assertLess(job.position.x, 2990)

    def test_it_never_goes_into_biters_reach_whatever_its_gear(self):
        lua = runtime()
        lua.eval("Agent.observe()")
        raid_on_kevin(lua)
        lua.execute("npc.character.position = {x=0, y=0}")
        job, _ = step(lua)
        self.assertEqual(job.kind, "move")
        self.assertEqual(lua.eval("routed_wide[#routed_wide]"), 0)           # their reach is a wall
        lua.execute("""
            for _, n in ipairs({'submachine-gun', 'heavy-armor'}) do held[n] = 1 end
            held['firearm-magazine'] = 300; held['gun-turret'] = 6
            Agent.tick(npc, game.tick); it.route = nil""")
        step(lua)
        self.assertEqual(lua.eval("routed_wide[#routed_wide]"), 0)           # however well armed
        lua.execute("no_way_round = true; it.route = nil")
        step(lua)
        self.assertEqual(lua.eval("#npc.agent.agenda"), 0)                   # the way ahead not clear: back
        lua.execute("no_way_round = false")
        raid_on_kevin(lua)
        lua.execute("npc.character.position = {x=0, y=0}; no_way_round = true")
        step(lua)
        self.assertEqual(lua.eval("#npc.agent.agenda"), 0)                   # no way clear of them: not going
        self.assertEqual(lua.eval("npc.agent.events[#npc.agent.events].why"), "no way there clear of the biters")
        self.assertEqual(lua.eval("npc.walled_off.target"), "Kevin")         # home, for what gets it through
        lua.execute("""
            npc.foe = {key='npc:2', name='Kevin'}
            for _, n in ipairs({'car', 'tank'}) do unlock(n) end
            Agent.order(npc, {tool='arm_up', priority='now', args={focus='all'}})""")
        crafts = []
        for _ in range(4):
            job, _ = step(lua)
            if not job or job.kind != "craft":
                break
            crafts.append(job.recipe)
            lua.execute("held['%s'] = 1" % job.recipe)
        self.assertIn("tank", crafts)                                        # to drive through them
        lua.execute("""                                                     -- home: it waits for a clear way
            npc.character.position = {x=900, y=0}
            npc.homeward = {tool='homeward', args={}, created=game.tick, priority='next'}""")
        self.assertEqual(step(lua), (False, "homeward"))
        lua.execute("game.tick = game.tick + 10801")                        # none came: the least bad way
        job, meta = step(lua)
        self.assertEqual((job.kind, meta.intention.tool), ("move", "homeward"))

    def test_it_knows_only_roughly_where_their_base_is_and_finds_it_by_looking(self):
        lua = runtime()
        lua.eval("Agent.observe()")
        lua.execute("""
            fuzz = 30                                    -- it thinks Kevin started at (330, 0)
            held['firearm-magazine'] = 50
            kevin_force = {name='npc-faction-2', valid=true, get_cease_fire=function() return false end}
            storage.npcs[2] = {id=2, name='Kevin', force=kevin_force, home={x=300, y=0},
                               character={valid=true, position={x=300, y=0}}}
            ada_force.get_cease_fire = function() return false end
            buildings = {{valid=true, type='furnace', position={x=296, y=0}},
                         {valid=true, type='assembling-machine', position={x=304, y=2}}}
            surface.find_entities_filtered = function(f)
                if f.force == kevin_force and not f.type then return buildings end
                return {}
            end
            npc.character.position = {x=262, y=0}
            Agent.order(npc, {tool='attack', priority='now', args={target='Kevin', minutes=2}})
            it = npc.agent.agenda[1]""")
        self.assertEqual(lua.eval("it.args.place.x"), 330)
        step(lua)                                                             # 68 short: there it looks
        self.assertEqual((lua.eval("it.args.place.x"), lua.eval("it.args.located")), (296, True))
        self.assertIsNotNone(lua.eval("it.staged"))                          # and stops short of the real one
        lua.execute("""
            buildings = {}
            npc.character.position = {x=262, y=0}
            Agent.order(npc, {tool='attack', priority='now', args={target='Kevin', minutes=2}})
            npc.agent.agenda = {npc.agent.agenda[#npc.agent.agenda]}
            step()""")
        self.assertEqual(lua.eval("#npc.agent.agenda"), 0)                   # nothing of theirs there
        self.assertEqual(lua.eval("npc.agent.events[#npc.agent.events].why"), "their base is not where I thought")

    def test_a_player_out_of_sight_is_looked_for_where_they_started(self):
        lua = runtime()
        lua.execute("""
            andre.character = {valid=true, surface=surface, position={x=100, y=50}}
            held['firearm-magazine'] = 50
            unseen, spawn = true, {x=-300, y=0}
            Agent.order(npc, {tool='attack', priority='now', args={target='Andre', minutes=2}})
            it = npc.agent.agenda[1]""")
        self.assertEqual(lua.eval("it.args.place.x"), -300)                  # not seen: where they started
        job, _ = step(lua)
        self.assertLess(job.position.x, 0)
        lua.execute("unseen = false")
        job, _ = step(lua)
        self.assertEqual(lua.eval("it.args.place.x"), 100)                   # seen: after them
        lua.execute("unseen = true; game.tick = game.tick + 600")
        step(lua)
        self.assertEqual(lua.eval("it.args.place.x"), 100)                   # gone from sight: where last seen

    def test_a_beaten_base_is_taken_over_or_looted_as_it_decides(self):
        lua = runtime()
        raid_on_kevin(lua)
        lua.execute("""
            it.step = 2; it.staged = {x=235, y=0}; it.arrived = game.tick
            storage.npcs[2].character = {valid=false}                       -- Kevin is dead
            chest_inv = {get_item_count=function(spec) return spec.name == 'iron-plate' and 120 or 0 end}
            chest = {valid=true, type='container', get_inventory=function() return chest_inv end}
            furnace = {valid=true, type='furnace', get_output_inventory=function() return {get_item_count=function() return 0 end} end}
            surface.find_entities_filtered = function(f)
                if f.force == kevin_force then return {chest, furnace} end
                return {}
            end
            inventory.count_empty_stacks = function() return 10 end
            decision = 'loot'
            require('scripts.war').spoils = function() return decision end""")
        job, _ = step(lua)
        self.assertEqual((job.kind, job.mode, job.item, job.count), ("transfer", "withdraw", "iron-plate", 120))
        self.assertTrue(lua.eval("npc.transfer_permissions['npc-faction-2'].withdraw"))   # the spoils are its
        lua.execute("chest_inv.get_item_count = function() return 0 end")
        self.assertIsNone(step(lua)[0])                                      # nothing left: done
        self.assertIsNone(lua.eval("npc.transfer_permissions['npc-faction-2']"))
        raid_on_kevin(lua)
        lua.execute("""
            it.step = 2; it.staged = {x=235, y=0}; it.arrived = game.tick; decision = 'take'
            npc.agent.agenda = {it}
            storage.npcs[2].character = {valid=false}
            surface.find_entities_filtered = function(f)
                if f.force == kevin_force then return {chest, furnace} end
                return {}
            end""")
        self.assertIsNone(step(lua)[0])
        self.assertEqual((lua.eval("chest.force.name"), lua.eval("furnace.force.name")), ("npc-faction-1",) * 2)
        self.assertEqual(lua.eval("npc.outposts[1].x"), 300)                 # its outpost now

    def test_on_a_trip_of_war_its_society_jobs_wait(self):
        lua = runtime()
        raid_on_kevin(lua)
        self.assertFalse(lua.eval("Agent.on_war_trip(npc)"))                 # decided, not set out yet
        step(lua)
        self.assertTrue(lua.eval("Agent.on_war_trip(npc)"))
        lua.execute("npc.agent.agenda = {}; npc.recall = {}")
        self.assertTrue(lua.eval("Agent.on_war_trip(npc)"))

    def test_a_march_that_finds_no_way_is_no_arrival(self):
        lua = runtime()
        lua.eval("Agent.observe()")
        raid_on_kevin(lua)
        lua.execute("""
            npc.character.position = {x=0, y=0}
            -- every leg ends where it stands: dry land only here (a lake all round)
            surface.find_non_colliding_position = function() return {x=npc.character.position.x, y=0} end""")
        step(lua)
        self.assertEqual(lua.eval("#npc.agent.agenda"), 0)
        self.assertEqual(lua.eval("npc.agent.events[#npc.agent.events].why"), "no way there that I could find")
        lua.execute("npc.character.position = {x=900, y=0}; npc.homeward = {tool='homeward', args={}, created=game.tick}")
        self.assertEqual(step(lua), (False, "homeward"))                     # home it must go: later
        self.assertIsNotNone(lua.eval("npc.homeward"))
        # (SO-5) but not for ever: no way home, again and again, and it gives up and tells its strategist
        lua.execute("log = log or function() end")
        for _ in range(12):
            lua.execute("game.tick = game.tick + 601")
            step(lua)
            if lua.eval("npc.homeward") is None:
                break
        self.assertIsNone(lua.eval("npc.homeward"))
        self.assertEqual(lua.eval("npc.agent.events[#npc.agent.events].kind"), "homeward_given_up")
        self.assertEqual(lua.eval("npc.agent.events[#npc.agent.events].why"), "no way home")

    def test_back_from_a_far_trip_home_comes_before_the_next_raid(self):
        lua = runtime()
        raid_on_kevin(lua)
        lua.execute("""
            npc.character.position = {x=900, y=0}
            npc.homeward = {tool='homeward', args={}, created=game.tick, priority='next'}""")
        job, meta = step(lua)
        self.assertEqual(meta.intention.tool, "homeward")                     # not arming for it out here
        self.assertLess(job.position.x, 900)

    def test_with_a_car_of_its_own_a_long_trip_is_driven_and_the_rest_walked(self):
        lua = runtime()
        raid_on_kevin(lua)
        lua.execute("npc.character.position = {x=0, y=0}; can_drive = true; parked_at = {x=40, y=0}")
        job, _ = step(lua)
        self.assertEqual((job.kind, job.position.x), ("move", 41))           # to its parked car, on foot
        lua.execute("parked_at = nil")
        self.assertEqual(step(lua), (False, "attack"))                       # the autopilot drives
        self.assertEqual(lua.eval("npc.drive.goal.x"), 300)
        self.assertTrue(lua.eval("npc.drive.opts.strict"))                    # a raid: only by a clear way
        self.assertGreater(lua.eval("npc.drive.opts.spare"), 0)               # their defenses are what it comes for
        self.assertEqual(lua.eval("canceled[#canceled]"), "combat_interrupt")
        self.assertEqual(step(lua), (False, "attack"))                       # still driving: it holds
        lua.execute("npc.drive.done = true; npc.character.position = {x=220, y=0}")
        job, _ = step(lua)
        self.assertEqual(job.kind, "move")                                   # parked: on foot from there
        self.assertIsNone(lua.eval("npc.drive"))
        lua.execute("npc.drive = {goal={x=300, y=0}, failed=true}; npc.character.position = {x=0, y=0}")
        job, _ = step(lua)
        self.assertEqual(job.kind, "move")                                   # the drive failed: walked
        self.assertIsNone(lua.eval("npc.drive"))

    def test_asked_for_a_vehicle_of_its_own_it_makes_that_one_then_fuel_for_it(self):
        lua = runtime()
        lua.execute("""
            for _, n in ipairs({'car', 'tank'}) do unlock(n) end
            require('scripts.drive').fuels = function(name) return {'solid-fuel', 'coal', 'wood'} end
            prototypes.recipe = {['solid-fuel']={}}                 -- made, but not by it yet; coal is mined
            Agent.order(npc, {tool='arm_up', priority='now', args={focus='vehicle', vehicle='car'}})""")
        self.assertEqual(lua.eval("npc.agent.agenda[#npc.agent.agenda].args.vehicle"), "car")
        job, _ = step(lua)
        self.assertEqual((job.recipe, job.count), ("car", 1))         # the one asked for, not the tank
        lua.execute("held.car = 1")
        job, _ = step(lua)
        self.assertEqual((job.recipe, job.count), ("coal", 20))       # the best fuel it can get
        lua.execute("held.coal = 20")
        self.assertIsNone(step(lua)[0])                               # done
        # a vehicle its force cannot drive is not passed on
        lua.execute("Agent.order(npc, {tool='arm_up', priority='now', args={focus='vehicle', vehicle='rocket-silo'}})")
        self.assertIsNone(lua.eval("npc.agent.agenda[#npc.agent.agenda] and npc.agent.agenda[#npc.agent.agenda].args.vehicle"))

    def test_after_breaking_off_a_fight_it_heals_before_going_back_in(self):
        lua = runtime()
        lua.execute("""
            require('scripts.war').alarm(npc, {x=200, y=0}, {name='npc-faction-2'}, 20, nil, game.tick)
            Agent.tick(npc, game.tick)
            npc.broke_off = game.tick; npc.character.health = 100""")
        self.assertEqual(step(lua), (False, "regroup"))                       # out of it, healing
        lua.execute("game.tick = game.tick + 2000")
        self.assertEqual(step(lua), (False, "regroup"))                       # still hurt
        lua.execute("npc.character.health = 220")
        job, _ = step(lua)
        self.assertEqual(job.kind, "move")                                    # healed: back to defend

    def test_beaten_by_a_raider_still_at_its_base_it_waits_the_raid_out(self):
        lua = runtime()
        lua.execute("""
            War = require('scripts.war')
            War.alarm(npc, {x=200, y=0}, {name='npc-faction-2'}, 20, nil, game.tick)
            Agent.tick(npc, game.tick)
            npc.lost_to = {force='npc-faction-2', tick=game.tick - 4000}       -- it lost to Kevin, and healed""")
        self.assertEqual(step(lua), (False, "waiting_out"))
        lua.execute("game.tick = game.tick + 3700")                              # the raid is over
        job, _ = step(lua)
        self.assertEqual(job.kind, "move")                                       # back to see what is left

    def test_its_own_home_attacked_while_it_helps_it_goes_back(self):
        lua = runtime()
        lua.execute("""
            npc.helping = {tool='help', ally=3, place={x=500, y=0}, since=game.tick, step='go'}
            npc.character.position = {x=480, y=0}
            require('scripts.war').alarm(npc, {x=10, y=0}, {name='npc-faction-2'}, 20, nil, game.tick)
            Agent.tick(npc, game.tick)""")
        self.assertIsNone(lua.eval("npc.helping"))
        self.assertEqual(lua.eval("npc.recall.why"), "attacked by an engineer or a player")


if __name__ == "__main__":
    unittest.main()
