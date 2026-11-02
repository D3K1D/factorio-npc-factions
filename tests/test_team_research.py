"""Shared research in the team world (team_research.lua), in Factorio's Lua 5.2 with forces stubbed."""
from pathlib import Path
import unittest

from lupa.lua52 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]


def runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().source = (ROOT / "mod" / "scripts" / "team_research.lua").read_text(encoding="utf-8")
    lua.execute("""
        function log() end
        storage = {}
        game = {tick=0, forces={}}
        R = assert(load(source))()
        TECHS = {'automation', 'gun-turret', 'military', 'logistics', 'stone-wall', 'turrets'}
        events, announced = {}, {}
        -- A force as the game has it: finishing a tech raises on_research_finished at once (here into R,
        -- then the "other handlers" when R says it is no copy).
        function make_force(name)
            local f = {queue={}, progress=0}
            local proxy = {}
            local techs = {}
            for _, t in ipairs(TECHS) do
                local raw = {name=t, researched=false}
                techs[t] = setmetatable({}, {
                    __index=function(_, k) if k == 'force' then return proxy end return raw[k] end,
                    __newindex=function(_, k, v)
                        raw[k] = v
                        if k == 'researched' and v then
                            for i = #f.queue, 1, -1 do
                                if f.queue[i] == t then table.remove(f.queue, i); if i == 1 then f.progress = 0 end end
                            end
                            local event = {research=techs[t], by_script=true}
                            events[#events + 1] = name .. ':' .. t
                            if not R.on_research_finished(event) then announced[#announced + 1] = name .. ':' .. t end
                        end
                    end})
            end
            local function names(list)
                local out = {}
                for _, n in ipairs(list) do if not techs[n].researched then out[#out + 1] = n end end
                return out
            end
            return setmetatable(proxy, {
                __index=function(_, k)
                    if k == 'name' then return name elseif k == 'valid' then return true
                    elseif k == 'technologies' then return techs
                    elseif k == 'research_progress' then return f.progress
                    elseif k == 'current_research' then return f.queue[1] and techs[f.queue[1]] or nil
                    elseif k == 'research_queue' then
                        local out = {} for i, n in ipairs(f.queue) do out[i] = techs[n] end return out
                    elseif k == 'add_research' then
                        return function(n) for _, q in ipairs(f.queue) do if q == n then return true end end
                            f.queue[#f.queue + 1] = n; return true end
                    end
                end,
                __newindex=function(_, k, v)
                    if k == 'research_progress' then f.progress = v
                    elseif k == 'research_queue' then
                        local before = f.queue[1]
                        f.queue = names(v)
                        if f.queue[1] ~= before then f.progress = 0 end
                    end
                end})
        end
        player = make_force('player')
        game.forces.player = player
        storage.npcs = {}
        for id = 1, 2 do
            local f = make_force('npc-faction-' .. id)
            game.forces[f.name] = f
            storage.npcs[id] = {id=id, force=f}
        end
        ada, bob = storage.npcs[1], storage.npcs[2]
    """)
    return lua


def queue(lua, force="player"):
    q = lua.eval(f"game.forces['{force}'].research_queue")
    return [q[i].name for i in range(1, len(q) + 1)]


class TeamResearchTests(unittest.TestCase):
    def test_engineers_add_their_picks_at_the_end_and_never_crowd_the_queue(self):
        lua = runtime()
        lua.execute("player.add_research('automation')")                    # a player's pick
        self.assertTrue(lua.eval("R.request(ada, 'gun-turret', 100)"))
        self.assertTrue(lua.eval("R.request(bob, 'gun-turret', 100)"))       # already there: fine
        self.assertTrue(lua.eval("R.request(bob, 'military', 100)"))
        self.assertEqual(lua.eval("select(2, R.request(ada, 'stone-wall', 100))"), "queue_full")
        self.assertEqual(queue(lua), ["automation", "gun-turret", "military"])
        view = lua.eval("R.view()")
        self.assertEqual([view.queue[i].by for i in (1, 2, 3)], ["player", "engineer", "engineer"])

    def test_a_pick_a_player_takes_out_stays_out_for_half_an_hour(self):
        lua = runtime()
        lua.execute("R.request(ada, 'gun-turret', 100)")
        lua.execute("R.on_research_cancelled({player_index=1, research={['gun-turret']=1}})")
        self.assertEqual(lua.eval("select(2, R.request(ada, 'gun-turret', 200))"),
                         "a player took it out of the queue")
        self.assertTrue(lua.eval("R.request(ada, 'gun-turret', 200 + 108000)"))
        # the script taking a tech out (no player) is no veto
        lua.execute("R.on_research_cancelled({research={['military']=1}})")
        self.assertTrue(lua.eval("R.request(ada, 'military', 300)"))

    def test_every_lab_works_on_the_teams_tech_and_their_progress_is_pooled(self):
        lua = runtime()
        lua.execute("player.add_research('automation'); player.add_research('turrets'); R.tick(7)")
        self.assertEqual(queue(lua, "npc-faction-1"), ["automation"])       # the engineers follow the players
        lua.execute("""game.forces['npc-faction-1'].research_progress = 0.25
                       game.forces['npc-faction-2'].research_progress = 0.10
                       player.research_progress = 0.05
                       R.tick(67)""")
        self.assertAlmostEqual(lua.eval("player.research_progress"), 0.40)
        self.assertAlmostEqual(lua.eval("game.forces['npc-faction-2'].research_progress"), 0.40)   # kept in step
        lua.execute("""game.forces['npc-faction-1'].research_progress = 0.75
                       game.forces['npc-faction-2'].research_progress = 0.70
                       R.tick(127)""")                                     # 0.40 + 0.35 + 0.30
        # done: everyone has it, said once (the copies are no news), and everyone is on the next tech
        self.assertTrue(lua.eval("game.forces['npc-faction-2'].technologies['automation'].researched"))
        self.assertEqual(list(lua.eval("announced").values()), ["player:automation"])
        self.assertEqual(queue(lua), ["turrets"])
        self.assertEqual(queue(lua, "npc-faction-2"), ["turrets"])

    def test_a_tech_an_engineer_finishes_itself_is_the_teams(self):
        lua = runtime()
        lua.execute("game.forces['npc-faction-2'].technologies['stone-wall'].researched = true")   # (a trigger tech)
        self.assertTrue(lua.eval("player.technologies['stone-wall'].researched"))
        self.assertTrue(lua.eval("game.forces['npc-faction-1'].technologies['stone-wall'].researched"))
        self.assertEqual(list(lua.eval("announced").values()), ["npc-faction-2:stone-wall"])

    def test_engineers_picks_go_by_need_behind_every_players_item_the_one_under_way_kept(self):
        # (R-9: in arrival order, 20-unit logistics two bus builds waited on sat behind green science and steel)
        lua = runtime()
        lua.execute("""player.add_research('automation')                  -- a player's, under way
                       R.request(ada, 'gun-turret', 100, {class='c'})
                       R.request(bob, 'military', 100, {class='d'})""")
        self.assertEqual(queue(lua), ["automation", "gun-turret", "military"])
        lua.execute("player.add_research('turrets')")                         # a player's, after them
        lua.execute("R.request(ada, 'gun-turret', 200, {class='a', step=0})") # (needed more now: its need merged)
        self.assertEqual(queue(lua), ["automation", "gun-turret", "military", "turrets"])   # (never past turrets)
        self.assertEqual(lua.eval("storage.team_research.added['gun-turret'].need.class"), "a")

    def test_within_the_engineers_picks_a_build_waiting_goes_first_then_the_nearer_goal_then_less_lab_time(self):
        lua = runtime()
        lua.execute("""
            local t = player.technologies
            player.add_research('automation')                     -- under way: never moved
            R.request(ada, 'military', 100, {class='d'})
            R.request(bob, 'logistics', 100, {class='a', step=0})""")
        self.assertEqual(queue(lua), ["automation", "logistics", "military"])
        lua.execute("""storage.team_research.added['military'] = nil   -- (as if a player had put it there)
                       R.request(ada, 'stone-wall', 100, {class='b', step=2})""")
        self.assertEqual(queue(lua), ["automation", "logistics", "military", "stone-wall"])   # (behind the player's)

    def test_a_full_queue_gives_way_to_a_pick_needed_more_and_only_that(self):
        lua = runtime()
        lua.execute("""player.add_research('automation')
                       R.request(ada, 'gun-turret', 100, {class='c'})
                       R.request(bob, 'military', 100, {class='d'})""")
        self.assertEqual(lua.eval("select(2, R.request(ada, 'stone-wall', 100, {class='d'}))"), "queue_full")
        self.assertFalse(lua.eval("(function() game.tick = 200; return R.room() end)()"))   # (asked lately: later)
        lua.execute("game.tick = 100 + 1800")
        self.assertTrue(lua.eval("R.room()"))                                 # (one waiting is less needed)
        self.assertTrue(lua.eval("R.request(bob, 'logistics', 2000, {class='a', step=0})"))
        self.assertEqual(queue(lua), ["automation", "logistics", "gun-turret"])   # (military, the least, gave way)
        self.assertTrue(lua.eval("R.request(ada, 'stone-wall', 2100, {class='b', step=1})"))   # (b before c)
        self.assertEqual(queue(lua), ["automation", "logistics", "stone-wall"])
        self.assertEqual(lua.eval("select(2, R.request(bob, 'turrets', 2200, {class='b', step=3}))"), "queue_full")

    def test_an_engineers_pick_under_way_is_never_displaced_or_moved(self):
        lua = runtime()
        lua.execute("""R.request(ada, 'military', 100, {class='d'})        -- first in the queue: under way
                       R.request(bob, 'gun-turret', 100, {class='d'})""")
        lua.execute("R.request(ada, 'stone-wall', 150, {class='d'})")         # (two waiting behind it)
        self.assertTrue(lua.eval("R.request(ada, 'logistics', 200, {class='a', step=0})"))
        self.assertEqual(queue(lua), ["military", "logistics", "gun-turret"])   # (stone-wall gave way, not military)

    def test_the_research_under_way_is_not_counted_as_waiting(self):
        lua = runtime()
        lua.execute("""R.request(ada, 'military', 100, {class='d'})        -- under way
                       R.request(bob, 'logistics', 100, {class='a', step=0})""")
        self.assertTrue(lua.eval("R.room()"))
        self.assertTrue(lua.eval("R.request(ada, 'gun-turret', 200, {class='a', step=1})"))
        self.assertEqual(queue(lua), ["military", "logistics", "gun-turret"])

    def test_a_pick_on_packs_nothing_makes_goes_last_and_the_first_gives_way(self):
        # (lesson 19: the user's labs stood 41 minutes on a tech whose military packs nothing made)
        lua = runtime()
        lua.execute("""unmade = {}
                       R.unmade = function(tech) return unmade[tech.name] or {} end
                       R.request(ada, 'military', 100, {class='a', step=0})       -- under way
                       R.request(bob, 'gun-turret', 100, {class='a', step=0})
                       R.request(ada, 'logistics', 100, {class='d'})""")
        self.assertEqual(queue(lua), ["military", "gun-turret", "logistics"])
        lua.execute("unmade['gun-turret'] = {'military-science-pack'}; R.order()")
        self.assertEqual(queue(lua), ["military", "logistics", "gun-turret"])   # (the one that can run first)
        lua.execute("unmade['military'] = {'military-science-pack'}; R.order()")
        self.assertEqual(queue(lua), ["logistics", "gun-turret", "military"])   # (the first gives way: it cannot go on)
        waits = lua.eval("R.waits()")
        self.assertEqual([waits[i].tech for i in (1, 2)], ["gun-turret", "military"])
        self.assertEqual(waits[1].packs[1], "military-science-pack")

    def test_an_engineers_pick_before_a_players_item_is_never_taken_out(self):
        # (taking it out would move the player's item up, and could take out what the player's research needs)
        lua = runtime()
        lua.execute("""player.add_research('automation')                          -- a player's, under way
                       R.request(ada, 'military', 100, {class='d'})
                       player.add_research('turrets')                             -- a player's, after it
                       R.request(bob, 'stone-wall', 100, {class='c'})""")
        self.assertTrue(lua.eval("R.request(ada, 'logistics', 200, {class='a', step=0})"))   # (stone-wall gave way)
        self.assertEqual(queue(lua), ["automation", "military", "turrets", "logistics"])
        self.assertEqual(lua.eval("select(2, R.request(bob, 'gun-turret', 300, {class='a', step=0}))"), "queue_full")
        self.assertEqual(queue(lua), ["automation", "military", "turrets", "logistics"])   # (military kept)

    def test_every_ten_seconds_the_picks_are_sorted_again_without_a_new_pick(self):
        lua = runtime()
        lua.execute("""unmade = {}
                       R.unmade = function(tech) return unmade[tech.name] or {} end
                       R.request(ada, 'military', 100, {class='a', step=0})
                       R.request(bob, 'gun-turret', 100, {class='a', step=0})
                       R.request(ada, 'logistics', 100, {class='a', step=0})
                       unmade['military'] = {'military-science-pack'}""")
        self.assertEqual(queue(lua)[0], "military")
        lua.execute("R.tick(607)")
        self.assertEqual(queue(lua), ["gun-turret", "logistics", "military"])

    def test_a_players_item_on_unmade_packs_is_never_moved(self):
        lua = runtime()
        lua.execute("""R.unmade = function(tech) return tech.name == 'automation' and {'x'} or {} end
                       player.add_research('automation')                          -- a player's, under way
                       R.request(ada, 'logistics', 100, {class='a', step=0})
                       R.order()""")
        self.assertEqual(queue(lua), ["automation", "logistics"])
        self.assertEqual(lua.eval("R.waits()[1].by"), "player")

    def test_a_finished_research_is_logged_with_its_tick(self):
        lua = runtime()
        lua.execute("""said = {}; function log(line) said[#said + 1] = line end
                       game.tick = 4321
                       R.log_finished({research={valid=true, name='automation', force={name='player'}}})""")
        self.assertEqual(lua.eval("said[1]"), "[npc-factions] research finished automation tick 4321 force player")

    def test_a_new_engineer_gets_everything_the_team_has_researched(self):
        lua = runtime()
        lua.execute("""player.technologies['logistics'].researched = true
                       local f = make_force('npc-faction-3'); game.forces[f.name] = f
                       storage.npcs[3] = {id=3, force=f}
                       events = {}
                       R.sync()""")
        self.assertTrue(lua.eval("game.forces['npc-faction-3'].technologies['logistics'].researched"))
        self.assertEqual(list(lua.eval("announced").values()), ["player:logistics"])   # the sync says nothing


if __name__ == "__main__":
    unittest.main()
