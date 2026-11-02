"""The team world's research order (designed_start.lua research_options), in Factorio's Lua 5.2 without Factorio."""
from pathlib import Path
import unittest

import test_retire

ROOT = Path(__file__).resolve().parents[1]


def runtime(team=True):
    lua = test_retire.runtime()
    lua.execute("""
        local PACKS = {red='automation-science-pack', green='logistic-science-pack', grey='military-science-pack'}
        -- every pack made by hand (so the labs can be fed); the techs below, all researchable now
        npc.character = {valid=true, prototype={crafting_categories={crafting=true}}}
        npc.force.recipes = {}
        for _, pack in pairs(PACKS) do
            npc.force.recipes[pack] = {name=pack, enabled=true, category='crafting', energy=5,
                products={{type='item', name=pack, amount=1}}, ingredients={}}
        end
        local function tech(name, units, packs, effects)
            local ingredients = {}
            for i, p in ipairs(packs) do ingredients[i] = {name=PACKS[p], amount=1} end
            return {name=name, enabled=true, researched=false, research_unit_count=units, prerequisites={},
                    research_unit_ingredients=ingredients, prototype={effects=effects or {}}}
        end
        npc.force.technologies = {
            automation=tech('automation', 10, {'red'}),
            ['electric-mining-drill']=tech('electric-mining-drill', 25, {'red'}),
            logistics=tech('logistics', 50, {'red'}),
            ['stone-wall']=tech('stone-wall', 10, {'red'}),
            military=tech('military', 10, {'red'}),
            ['military-2']=tech('military-2', 20, {'red', 'green'}),
            ['military-science-pack']=tech('military-science-pack', 30, {'red', 'green'}),
            ['physical-projectile-damage-2']=tech('physical-projectile-damage-2', 100, {'red', 'green', 'grey'},
                {{type='ammo-damage', ammo_category='bullet', modifier=0.1}}),
            ['oil-processing']=tech('oil-processing', 100, {'red', 'green'}),
        }
        -- the armory reads the techs from the prototypes: the bonus tech is military
        prototypes.technology = {}
        for name, t in pairs(npc.force.technologies) do
            prototypes.technology[name] = {name=name, effects=t.prototype.effects, research_unit_ingredients={},
                                           prerequisites={}}
        end
        prototypes.recipe = prototypes.recipe or {}
        game.forces = {player={research_queue={}}}
        storage.team_research = nil
        DS.pack_made = function() return true end   -- (every pack made: the order is what is tested here)
        function names()
            local out = {}
            for i, o in ipairs(DS.research_options(npc)) do out[i] = o.name end
            return out
        end
    """)
    if team:
        lua.execute("settings.global['npc-factions-scenario'] = {value='team'}")
    return lua


class TeamResearchOrderTests(unittest.TestCase):
    def test_the_team_arms_first_then_other_military_then_the_rest(self):
        names = list(runtime().eval("names()").values())
        self.assertEqual(names[:4], ["military", "stone-wall", "military-2", "military-science-pack"])
        self.assertEqual(names[4], "physical-projectile-damage-2")      # the armory's: a bullet damage bonus
        self.assertEqual(names[5:7], ["automation", "electric-mining-drill"])   # then the factory, as before
        options = runtime().eval("DS.research_options(npc)")
        flags = {options[i].name: options[i].military for i in range(1, len(options) + 1)}
        self.assertTrue(flags["military"])
        self.assertIsNone(flags["automation"])

    def test_survival_options_are_never_cut_off_the_list(self):
        # (twelve options at most: a radar or repair research weighed by the threat must still be offered)
        lua = runtime()
        lua.execute("""
            for i = 1, 14 do
                local name = 'cheap-' .. i
                npc.force.technologies[name] = {name=name, enabled=true, researched=false, research_unit_count=1,
                    prerequisites={}, research_unit_ingredients={{name='automation-science-pack', amount=1}},
                    prototype={effects={}}}
            end
            npc.force.technologies.radar = {name='radar', enabled=true, researched=false, research_unit_count=500,
                prerequisites={}, research_unit_ingredients={{name='automation-science-pack', amount=1}},
                prototype={effects={}}}
            for i = 1, 4 do   -- (costly weapons research, cut too: the cheap radar must still come first)
                local name = 'big-gun-' .. i
                npc.force.technologies[name] = {name=name, enabled=true, researched=false, research_unit_count=900,
                    prerequisites={}, research_unit_ingredients={{name='automation-science-pack', amount=1}},
                    prototype={effects={}}}
            end
            local survival = {radar=true}
            for i = 1, 4 do survival['big-gun-' .. i] = true end
            require('scripts.progression').survival_techs = function() return survival end
        """)
        options = lua.eval("DS.research_options(npc)")
        flags = {options[i].name: options[i].survival for i in range(1, len(options) + 1)}
        self.assertTrue(flags.get("radar"))
        self.assertLessEqual(len(flags), 15)

    def test_other_games_keep_their_order(self):
        names = list(runtime(team=False).eval("names()").values())
        self.assertEqual(names[:2], ["automation", "electric-mining-drill"])
        self.assertIsNone(runtime(team=False).eval("DS.research_options(npc)[1].military"))



class UnusedResearchTests(unittest.TestCase):
    """R-10: what each option leads to and its bonuses go to the companion; techs it finds no use for wait."""

    def test_each_option_carries_its_bonuses_and_what_it_and_the_techs_after_it_unlock(self):
        lua = runtime(team=False)
        lua.execute("""
            local t = npc.force.technologies
            t.lamp = {name='lamp', enabled=true, researched=false, research_unit_count=10, prerequisites={},
                research_unit_ingredients={{name='automation-science-pack', amount=1}},
                prototype={effects={{type='unlock-recipe', recipe='small-lamp'}}}}
            t['lamp-2'] = {name='lamp-2', enabled=true, researched=false, research_unit_count=10, prerequisites={t.lamp},
                research_unit_ingredients={{name='automation-science-pack', amount=1}},
                prototype={effects={{type='unlock-recipe', recipe='big-lamp'}, {type='laboratory-speed'}}}}
            t['lamp-3'] = {name='lamp-3', enabled=true, researched=true, prototype={effects={{type='unlock-recipe',
                recipe='old-lamp'}}}}   -- (researched already: not a lead)
            t.lamp.successors = {['lamp-2']=t['lamp-2'], ['lamp-3']=t['lamp-3']}
            require('scripts.progression').survival_techs = function() return {['lamp-2']=true} end""")
        options = {o.name: o for o in lua.eval("DS.research_options(npc)").values()}
        self.assertEqual(list(options["lamp"].leads.values()), ["big-lamp", "small-lamp"])
        self.assertEqual(list(options["lamp"].lead_bonuses.values()), ["laboratory-speed"])   # (of those after it)
        self.assertTrue(options["lamp"].leads_armed)   # (one after it is survival research)
        self.assertIsNone(options["automation"].leads_armed)
        lua.execute("DS.LEADS_KEPT = 1")   # (past the cap: the walk goes on for the flags, the list is marked cut)
        options = {o.name: o for o in lua.eval("DS.research_options(npc)").values()}
        self.assertEqual(len(options["lamp"].leads), 1)
        self.assertTrue(options["lamp"].leads_cut)
        self.assertTrue(options["lamp"].leads_armed)
        self.assertEqual(list(options["lamp"].lead_bonuses.values()), ["laboratory-speed"])
        self.assertNotIn("lamp-2", options)   # (its prerequisite not researched)
        self.assertEqual(list(options["physical-projectile-damage-2"].bonuses.values()), ["ammo-damage"])

    def test_an_idle_answer_sets_the_offered_techs_aside_for_a_while(self):
        lua = runtime(team=False)
        lua.execute("""
            game.tick = 100
            npc.research_request = {request_id='7:r1', tick=90, options={{name='automation'}, {name='logistics'}}}
            answer = DS.choose_research({request_id='7:r1', technology='', idle=true, unused={'automation'}})""")
        self.assertTrue(lua.eval("answer.accepted and answer.idle"))
        self.assertIsNone(lua.eval("npc.research_request"))
        names = list(lua.eval("names()").values())
        self.assertNotIn("automation", names)
        self.assertIn("logistics", names)   # (not judged unused: military held back for its share, say)
        self.assertIn("electric-mining-drill", names)
        lua.execute("game.tick = 100 + DS.UNUSED_TICKS")
        self.assertIn("automation", list(lua.eval("names()").values()))



class SpaceResearchTests(unittest.TestCase):
    def test_space_science_its_platform_makes_opens_the_road_on_past_the_silo(self):
        # (R-8: a pack no hand can craft, made in orbit; the thrusters' research is then on the road to space)
        lua = runtime(team=False)
        lua.execute("""
            local t = npc.force.technologies
            t['rocket-silo'] = {name='rocket-silo', enabled=true, researched=true, prerequisites={}}
            t['space-platform-thruster'] = {name='space-platform-thruster', enabled=true, researched=false,
                research_unit_count=500, prerequisites={},
                research_unit_ingredients={{name='automation-science-pack', amount=1},
                                           {name='space-science-pack', amount=1}}, prototype={effects={}}}
            Orbit_ = require('scripts.orbit')
            from_space = false
            Orbit_.makes = function(_, item) return from_space and item == 'space-science-pack' end""")
        self.assertNotIn("space-platform-thruster", list(lua.eval("names()").values()))
        lua.execute("from_space = true")
        options = {o.name: o for o in lua.eval("DS.research_options(npc)").values()}
        self.assertIn("space-platform-thruster", options)
        self.assertTrue(options["space-platform-thruster"].rocket_path)


if __name__ == "__main__":
    unittest.main()
