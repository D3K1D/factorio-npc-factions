"""The team world at war on the engineer's side (agent.lua): a breach in its wall line is repaired at once,
and its strategist sees the threat, in Factorio's Lua 5.2 with the game stubbed."""
import unittest

import test_agent


class BreachOrderTests(unittest.TestCase):
    def test_a_breach_is_repaired_at_once_and_its_strategist_hears_of_it(self):
        lua = test_agent.team_runtime()
        lua.execute("""
            game.forces.enemy = {name='enemy', get_evolution_factor=function() return 0.3 end}
            game.tick = 5000
            Agent.breach(npc, {edge='north', x=-400.5, y=-572.5, layers=1, lost='stone-wall'})""")
        it = lua.eval("npc.agent.agenda[1]")
        self.assertEqual((it.tool, it.priority, it.args.goal), ("build", "now", "breach-repair"))
        self.assertEqual(lua.eval("npc.agent.events[#npc.agent.events].kind"), "breach")
        # more losses on the same wall a moment later: no second order, no second word
        lua.execute("game.tick = 5100; Agent.breach(npc, {edge='north', x=-399.5, y=-572.5, layers=0, lost='gate'})")
        self.assertEqual(lua.eval("#npc.agent.agenda"), 1)
        self.assertEqual(lua.eval("npc.agent.events[#npc.agent.events].layers"), 1)
        # building defenses already: nothing to drop for it
        lua.execute("""npc.agent.agenda = {}; game.tick = 9000
            Agent.designed.defending = function() return true end
            Agent.breach(npc, {edge='west', x=-570.5, y=-400.5, layers=2, lost='gun-turret'})""")
        self.assertEqual(lua.eval("#npc.agent.agenda"), 0)

    def test_its_strategist_sees_evolution_the_latest_attack_and_breaches(self):
        lua = test_agent.team_runtime()
        lua.execute("""
            game.forces.enemy = {name='enemy', get_evolution_factor=function() return 0.271 end}
            storage.team.war = {attacks={[1]={{first=100, last=200, x=-400, y=-560, units={['small-biter']=6},
                seen={}, count=6, biters=6, spitters=0, biggest='small'}}},
                breaches={['1:north:-25']={npc_id=1, edge='north', x=-400.5, y=-572.5, tick=300, lost='stone-wall',
                    layers=1, losses=1}}}
            game.tick = 600""")
        threat = lua.eval("Agent.situation(npc, 600).threat")
        self.assertEqual(threat.evolution, 0.27)
        self.assertEqual((threat.last_attack.units, threat.last_attack.kind, threat.last_attack.seconds_ago),
                         (6, "biters", 6))
        self.assertEqual((threat.newest_breach.edge, threat.newest_breach.layers), ("north", 1))


if __name__ == "__main__":
    unittest.main()
