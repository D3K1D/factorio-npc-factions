"""Team world shared stores (team_supply.lua): teammates' chests are open, players' chests are asked about."""
from pathlib import Path
import unittest

from lupa.lua52 import LuaRuntime

import test_character_layer

ROOT = Path(__file__).resolve().parents[1]


def runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)
    for name in ("team_world", "team_supply"):
        lua.globals()[name + "_source"] = (ROOT / "mod" / "scripts" / (name + ".lua")).read_text(encoding="utf-8")
    lua.execute("""
        package.preload['scripts.team_world'] = function() return assert(load(team_world_source))() end
        said = {}
        package.preload['scripts.voice'] = function()
            return {say=function(npc, kind, vars, opts)
                        said[#said + 1] = {who=npc.name, kind=kind, vars=vars, to=opts and opts.player}
                        return kind end,
                    pretty=function(name) return (name:gsub('-', ' ')) end}
        end
        S = assert(load(team_supply_source))()
        scenario = 'team'
        settings = {global={['npc-factions-scenario']={}}}
        setmetatable(settings.global['npc-factions-scenario'], {__index=function() return scenario end})
        defines = {inventory={chest=1}}
        function force(name)
            local f = {name=name, valid=true, friends={}}
            f.get_friend = function(o) return f.friends[o.name] == true end
            return f
        end
        player_force, ada_force, bob_force = force('player'), force('npc-faction-1'), force('npc-faction-2')
        for _, a in ipairs({player_force, ada_force, bob_force}) do
            for _, b in ipairs({player_force, ada_force, bob_force}) do if a ~= b then a.friends[b.name] = true end end
        end
        game = {tick=100, forces={player=player_force}}
        player_chests = {}
        surface = {name='nauvis'}
        function surface.find_entities_filtered(f)
            local out = {}
            if f.force == player_force then for _, c in ipairs(player_chests) do out[#out + 1] = c end end
            return out
        end
        function chest(owner_force, x, y, items, last_user)
            local inv = {get_item_count=function(spec) return items[spec.name] or 0 end}
            return {valid=true, type='container', force=owner_force, position={x=x, y=y}, surface=surface,
                    last_user=last_user, get_inventory=function() return inv end}
        end
        ada = {id=1, name='Ada', force=ada_force,
               character={valid=true, position={x=0, y=0}, surface=surface, force=ada_force}}
        bob = {id=2, name='Bob', force=bob_force, storage_chests={}, design_history={},
               character={valid=true, position={x=300, y=0}, surface=surface, force=bob_force}}
        storage = {npcs={ada, bob}}
        andre = {valid=true, object_name='LuaPlayer', index=1, name='Andre', connected=true}
        function take(item, n, tick, ask) local r = {S.take(ada, item, n, tick, ask)} return r[1], r[2] end
    """)
    return lua


class TeammateTests(unittest.TestCase):
    def test_a_teammates_chest_is_open_and_it_leaves_them_a_share(self):
        lua = runtime()
        lua.execute("bob.storage_chests = {chest(bob_force, 100, 0, {['iron-plate']=100})}")
        job = lua.eval("(take('iron-plate', 200, 100, true))")
        self.assertEqual((job.kind, job.mode, job.item, job.count), ("transfer", "withdraw", "iron-plate", 80))
        self.assertTrue(lua.eval("S.may_withdraw(ada, bob.storage_chests[1], 'iron-plate', 100)"))
        self.assertEqual((lua.eval("said[1].kind"), lua.eval("said[1].vars.target")), ("team_grab", "Bob"))
        # a chest it fills by hand for its own lines is not a store; a nearly empty one is left alone
        lua.execute("""
            local fed = chest(bob_force, 50, 0, {['iron-plate']=500})
            bob.storage_chests = {chest(bob_force, 60, 0, {['iron-plate']=10})}
            bob.design_history = {{order={'lane'}, entities={lane={entity=fed}},
                                   stages={{supplies={{entity_id='lane'}}}}}}""")
        self.assertIsNone(lua.eval("(take('iron-plate', 200, 700, false))"))

    def test_other_games_share_nothing(self):
        lua = runtime()
        lua.execute("bob.storage_chests = {chest(bob_force, 100, 0, {['iron-plate']=100})}; scenario = 'factions'")
        self.assertIsNone(lua.eval("(take('iron-plate', 50, 100, true))"))
        self.assertFalse(lua.eval("S.may_withdraw(ada, bob.storage_chests[1], 'iron-plate', 100)"))


class PlayerChestTests(unittest.TestCase):
    def setUp(self):
        self.lua = runtime()
        self.lua.execute("player_chests = {chest(player_force, 40, 0, {['iron-plate']=300}, andre)}")

    def test_it_asks_first_and_waits_and_a_yes_lets_it_take_for_half_an_hour(self):
        lua = self.lua
        job, why = lua.eval("take('iron-plate', 50, 100, true)")
        self.assertIsNone(job)
        self.assertEqual(why, "waiting_for_answer:Andre")
        self.assertEqual((lua.eval("said[1].kind"), lua.eval("said[1].to.name")), ("ask_chest", "Andre"))
        self.assertIn("[gps=40,0,nauvis]", lua.eval("said[1].vars.where"))
        self.assertFalse(lua.eval("S.may_withdraw(ada, player_chests[1], 'iron-plate', 150)"))
        self.assertEqual(lua.eval("select(2, take('iron-plate', 50, 200, true))"), "waiting_for_answer:Andre")
        self.assertEqual(lua.eval("#said"), 1)                                # asked once
        self.assertFalse(lua.eval("S.reply(andre, 'building the north wall', nil, 300)"))   # not an answer
        self.assertTrue(lua.eval("S.reply(andre, 'Sure, go ahead!', nil, 300)"))
        self.assertEqual(lua.eval("said[2].kind"), "chest_yes")
        lua.execute("job = take('iron-plate', 50, 400, true)")
        self.assertEqual((lua.eval("job.target == player_chests[1]"), lua.eval("job.count")), (True, 50))
        self.assertTrue(lua.eval("S.may_withdraw(ada, player_chests[1], 'iron-plate', 400)"))
        self.assertFalse(lua.eval("S.may_withdraw(ada, player_chests[1], 'copper-plate', 400)"))   # that item only
        self.assertFalse(lua.eval("S.may_withdraw(ada, player_chests[1], 'iron-plate', 300 + 108000)"))   # expired

    def test_a_no_stops_it_and_it_is_not_asked_again(self):
        lua = self.lua
        lua.execute("take('iron-plate', 50, 100, true)")
        self.assertTrue(lua.eval("S.reply(andre, \"no, don't\", ada, 200)"))
        self.assertEqual(lua.eval("said[2].kind"), "chest_no")
        self.assertIsNone(lua.eval("(take('iron-plate', 50, 300, true))"))
        self.assertEqual(lua.eval("#said"), 2)                                # no second question
        self.assertFalse(lua.eval("S.may_withdraw(ada, player_chests[1], 'iron-plate', 300)"))

    def test_what_the_team_could_hand_over_follows_the_players_word(self):
        # (TeamSupply.has, R-7: a player's chest counts while it could still be had: not after a no, nor offline)
        lua = self.lua
        self.assertTrue(lua.eval("S.has(ada, 'iron-plate', 100)"))          # (it could ask)
        lua.execute("take('iron-plate', 50, 100, true)")
        self.assertTrue(lua.eval("S.has(ada, 'iron-plate', 150)"))          # (asked, the answer still open)
        self.assertFalse(lua.eval("S.has(ada, 'copper-plate', 150)"))       # (that question is not about it)
        self.assertFalse(lua.eval("S.has(ada, 'iron-plate', 100000)"))      # (unanswered past its wait)
        lua.eval("S.reply(andre, \"no, don't\", ada, 200)")
        self.assertFalse(lua.eval("S.has(ada, 'iron-plate', 300)"))
        offline = runtime()
        offline.execute("player_chests = {chest(player_force, 40, 0, {['iron-plate']=300}, andre)}; andre.connected = false")
        self.assertFalse(offline.eval("S.has(ada, 'iron-plate', 100)"))

    def test_no_answer_within_a_minute_and_it_gets_it_another_way(self):
        lua = self.lua
        lua.execute("take('iron-plate', 50, 100, true)")
        self.assertIsNone(lua.eval("select(2, take('iron-plate', 50, 100 + 3600, true))"))   # gave up waiting
        self.assertEqual(lua.eval("#said"), 1)
        self.assertFalse(lua.eval("S.reply(andre, 'yes', nil, 100 + 3700)"))     # too late for that question

    def bob_take(self, item, n, tick):
        return self.lua.eval(f"select(2, S.take(bob, '{item}', {n}, {tick}, true))")

    def test_the_live_game_one_chest_asked_about_once_and_a_no_stands(self):
        # (2026-10-22: Fenn asked, Ada asked about the same chest 33 s later, the player said no, Fenn's
        # question ran out and wrote over the no, and Fenn asked a third time ten minutes on)
        lua = self.lua
        self.assertEqual(lua.eval("select(2, take('copper-plate', 20, 100, true))"), None)   # no copper there
        lua.execute("player_chests[1] = chest(player_force, 40, 0, {['copper-plate']=300, ['iron-plate']=300}, andre)")
        self.assertEqual(lua.eval("select(2, take('copper-plate', 20, 700, true))"), "waiting_for_answer:Andre")
        self.assertIsNone(self.bob_take("copper-plate", 20, 2700))    # Bob does not ask too
        self.assertIsNone(self.bob_take("iron-plate", 20, 2700))      # nor about anything else meanwhile
        self.assertEqual(lua.eval("#said"), 1)
        self.assertTrue(lua.eval("S.reply(andre, 'no', nil, 3500)"))
        for tick in (700 + 3600, 3500 + 36000 + 1, 3500 + 108000 + 1, 3500 + 400000):
            self.assertIsNone(self.bob_take("copper-plate", 20, tick))
            self.assertIsNone(lua.eval(f"select(2, take('iron-plate', 20, {tick}, true))"))   # that chest: never again
        self.assertEqual(lua.eval("#said - 1"), 1)                     # one question and the reply to it
        self.assertFalse(lua.eval("S.may_withdraw(ada, player_chests[1], 'copper-plate', 4000)"))

    def test_no_answer_leaves_that_chest_alone_for_good_even_if_the_asker_moved_on(self):
        lua = self.lua
        lua.execute("player_chests[2] = chest(player_force, 60, 0, {['iron-plate']=100, ['stone-brick']=50}, andre)")
        lua.execute("take('iron-plate', 50, 100, true)")              # Ada asks about the nearer chest, then does other work
        self.assertEqual(self.bob_take("stone-brick", 20, 100 + 3600), "waiting_for_answer:Andre")   # Ada's ran out
        self.assertIn("[gps=60,0,nauvis]", lua.eval("said[2].vars.where"))   # the other chest
        lua.execute("S.reply(andre, 'nah', nil, 100 + 3700)")
        self.assertIsNone(self.bob_take("iron-plate", 20, 100 + 3600 + 36000 + 1))   # chest 1 never; chest 2 said no
        self.assertEqual(lua.eval("#said"), 3)

    def test_a_yes_covers_that_chest_and_what_was_asked_and_then_it_does_not_ask_again(self):
        lua = self.lua
        lua.execute("player_chests[2] = chest(player_force, 60, 0, {['iron-plate']=100}, andre)")
        lua.execute("take('iron-plate', 50, 100, true); S.reply(andre, 'yes', nil, 200)")
        lua.execute("job = take('iron-plate', 30, 300, true)")
        self.assertEqual((lua.eval("job.target == player_chests[1]"), lua.eval("job.count")), (True, 30))
        lua.execute("job = take('iron-plate', 50, 400, true)")
        self.assertEqual(lua.eval("job.count"), 20)                   # 50 were asked for in all
        self.assertFalse(lua.eval("S.may_withdraw(ada, player_chests[2], 'iron-plate', 400)"))   # that chest only
        self.assertEqual(lua.eval("select(2, take('iron-plate', 50, 500, true))"), "waiting_for_answer:Andre")
        self.assertIn("[gps=60,0,nauvis]", lua.eval("said[3].vars.where"))   # used up: the other chest, not that one

    def test_an_offline_player_is_never_asked_and_ore_is_never_asked_for(self):
        lua = self.lua
        lua.execute("andre.connected = false")
        self.assertIsNone(lua.eval("(take('iron-plate', 50, 100, true))"))
        lua.execute("andre.connected = true; player_chests[1] = chest(player_force, 40, 0, {['iron-ore']=300}, andre)")
        self.assertIsNone(lua.eval("(take('iron-ore', 50, 100, false))"))
        self.assertEqual(lua.eval("#said"), 0)


class AcquireTests(unittest.TestCase):
    def test_short_of_plates_it_fetches_from_a_teammates_chest_before_smelting_by_hand(self):
        import test_retire
        lua = test_retire.runtime()
        lua.execute("""
            settings.global['npc-factions-scenario'] = {value='team'}
            defines.inventory.chest = 1
            held = {}
            npc.home, npc.design, npc.design_history = {x=0, y=0}, {}, {}
            npc.force.get_friend = function() return true end
            local ground = {find_entities_filtered=function() return {} end,
                            count_entities_filtered=function() return 1 end}
            npc.character = {valid=true, position={x=0, y=0}, force=npc.force, surface=ground,
                             prototype={crafting_categories={crafting=true}},
                             get_main_inventory=function()
                                 return {get_item_count=function(spec) return held[spec.name] or 0 end} end}
            local mate = storage.npcs[2]
            local stock = {get_item_count=function(spec) return spec.name == 'iron-plate' and 200 or 0 end}
            store = {valid=true, type='container', force=mate.force, position={x=50, y=0}, surface=ground,
                     get_inventory=function() return stock end}
            mate.storage_chests, mate.design_history = {store}, {}
            acquire = require('scripts.agent').designed.acquire
            job = acquire(npc, 'iron-plate', 20, 0)""")
        self.assertEqual((lua.eval("job.kind"), lua.eval("job.mode"), lua.eval("job.count")), ("transfer", "withdraw", 20))
        self.assertTrue(lua.eval("job.target == store"))
        lua.execute("settings.global['npc-factions-scenario'] = {value='factions'}; job = acquire(npc, 'iron-plate', 20, 0)")
        self.assertFalse(lua.eval("job and job.target == store"))           # other games: its own way


class TemplateTests(unittest.TestCase):
    def test_the_questions_and_answers_sound_like_the_radio(self):
        lua = test_character_layer.runtime()
        for kind in ("ask_chest", "chest_yes", "chest_no", "team_grab"):
            for tone in ("warm", "curt", "playful", "hostile"):
                text = lua.eval(f"V.render('{kind}', '{tone}', {{target='Andre', count=50, item='iron plate', "
                                f"where='[gps=40,0,nauvis]'}}, 'x')")
                self.assertTrue(text)
                self.assertNotRegex(text, "[—–;]|\\s-\\s|\\{")
        self.assertIn("[gps=40,0,nauvis]", lua.eval("V.render('ask_chest', 'warm', {target='Andre', count=50, "
                                                    "item='iron plate', where='[gps=40,0,nauvis]'}, 'x')"))


if __name__ == "__main__":
    unittest.main()
