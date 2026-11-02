"""Engineers' own moves toward players (diplomacy.lua) in Factorio's Lua 5.2, without Factorio."""
from pathlib import Path
import unittest

from lupa.lua52 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]


def runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().source = (ROOT / "mod" / "scripts" / "diplomacy.lua").read_text(encoding="utf-8")
    lua.globals().aggro_source = (ROOT / "mod" / "scripts" / "aggro.lua").read_text(encoding="utf-8")
    lua.globals().team_source = (ROOT / "mod" / "scripts" / "team_world.lua").read_text(encoding="utf-8")
    lua.execute("""
        package.preload['scripts.team_world'] = function() return assert(load(team_source))() end
        for _, name in ipairs({'personality', 'strategy_policy', 'player_trade', 'character', 'teach'}) do
            package.preload['scripts.' .. name] = function()
                return {on_player_treaty=function() end, axes=function() return {} end}
            end
        end
        refused = false
        package.preload['scripts.relations'] = function()
            return {refused_recently=function() return refused end,
                    get=function() return {trust=0, grudge=refused and 90 or 0} end,
                    stance=function() return refused and -1 or 0 end, note_refusal=function() end}
        end
        package.preload['scripts.aggro'] = function() return assert(load(aggro_source))() end
        function force(name)
            local f = {name=name, valid=true, friend={}, cease={}}
            f.get_friend = function(o) return f.friend[o.name] == true end
            f.set_friend = function(o, v) f.friend[o.name] = v end
            f.get_cease_fire = function(o) return f.cease[o.name] == true end
            f.set_cease_fire = function(o, v) f.cease[o.name] = v end
            return f
        end
        printed, heard = {}, {}
        game = {tick=100, forces={}, print=function(t) printed[#printed + 1] = t end}
        players = force('player')
        ada_force = force('npc-faction-1')
        game.forces.player, game.forces['npc-faction-1'] = players, ada_force
        andre = {valid=true, index=1, name='Andre', force=players, print=function(t) heard[#heard + 1] = t end}
        game.get_player = function() return andre end
        ada = {id=1, name='Ada', force=ada_force, character={valid=true, force=ada_force}}
        storage = {npcs={ada}, founding_force=players}
        D = assert(load(source))()
        D.init()
        -- engineers start at peace with the players: a cease-fire both ways, not friends
        ada_force.set_cease_fire(players, true); players.set_cease_fire(ada_force, true)
    """)
    return lua


class DiplomacyTests(unittest.TestCase):
    def test_war_is_declared_and_peace_offered_to_a_player(self):
        lua = runtime()
        self.assertEqual(lua.eval("D.state(ada_force, players)"), "peace")
        self.assertTrue(lua.eval("D.npc_to_player(ada, andre, 'declare_war')"))
        self.assertEqual(lua.eval("D.state(ada_force, players)"), "war")
        self.assertIn("Ada declares war on Andre", lua.eval("printed[1]"))
        self.assertEqual(lua.eval("select(2, D.npc_to_player(ada, andre, 'declare_war'))"), "already_at_war")
        # its own offer of peace is its yes, whatever it felt before
        lua.execute("refused = true")
        self.assertTrue(lua.eval("D.npc_to_player(ada, andre, 'offer_peace')"))
        self.assertIn("/npc peace 1", lua.eval("heard[1]"))
        lua.execute("D.talk(andre, 'peace', 1)")
        self.assertEqual(lua.eval("D.state(ada_force, players)"), "peace")

    def test_on_one_team_nobody_declares_offers_or_trades(self):
        lua = runtime()
        lua.execute("""settings = {global={['npc-factions-scenario']={value='team'}}}
                       ada_force.set_friend(players, true); players.set_friend(ada_force, true)""")
        self.assertEqual(lua.eval("select(2, D.npc_to_player(ada, andre, 'declare_war'))"), "one_team")
        self.assertEqual(lua.eval("select(2, D.declare(ada, players, 'war'))"), "one_team")
        self.assertEqual(lua.eval("D.state(ada_force, players)"), "allied")
        self.assertEqual(lua.eval("select(2, D.talk(andre, 'war', 1))"), "one_team")
        self.assertIn("on your team", lua.eval("heard[1]"))          # it explains the team instead
        lua.execute("heard = {}; D.trade_command(andre, 'trades')")
        self.assertIn("No trading", lua.eval("heard[1]"))

    def test_without_its_offer_a_grudge_still_says_no(self):
        lua = runtime()
        lua.execute("D.npc_to_player(ada, andre, 'declare_war'); refused = true; D.talk(andre, 'peace', 1)")
        self.assertEqual(lua.eval("D.state(ada_force, players)"), "war")
        self.assertIn("decline", lua.eval("heard[#heard]"))

    def test_an_old_save_keeps_only_alliances_made_since(self):
        lua = runtime()
        lua.execute("""
            ada_force.set_friend(players, true); players.set_friend(ada_force, true)   -- allied at spawn, before
            moved = D.migrate_start_alliances(players)""")
        self.assertEqual((lua.eval("moved"), lua.eval("D.state(ada_force, players)")), (1, "peace"))
        lua.execute("D.declare(ada, players, 'ally'); moved = D.migrate_start_alliances(players)")
        self.assertEqual((lua.eval("moved"), lua.eval("D.state(ada_force, players)")), (0, "allied"))


if __name__ == "__main__":
    unittest.main()
