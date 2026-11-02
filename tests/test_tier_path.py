"""The science tiers and what is due on the way (companion/tier_path.py, Progression.tiers in progression.lua, the
next tier's research in research_brain and choose_research)."""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

from lupa.lua52 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion import research_brain, tier_path  # noqa: E402

RED, GREEN, GREY, BLUE = ("automation-science-pack", "logistic-science-pack", "military-science-pack",
                          "chemical-science-pack")


def row(name, per, category, enabled=True, tech=None):
    out = {"name": name, "type": "item", "per": per, "recipe": name, "category": category}
    if enabled:
        out["enabled"] = True
    else:
        out["tech"] = tech
    return out


def research(*names_units):
    return [{"name": n, "units": u, "time": 15, "packs": [RED, GREEN]} for n, u in names_units]


# As the game exports it (Progression.tiers), cut down: grey's research not done, steel researched.
TIERS = {
    "tiers": [
        {"pack": RED, "recipe": RED, "enabled": True, "category": "crafting", "rocket": True, "machine_ready": True,
         "cost": 3, "research": [],
         "chain": [row("copper-plate", 1, "smelting"), row("iron-gear-wheel", 1, "crafting"),
                   row("iron-plate", 2, "smelting"), {"name": "iron-ore", "per": 2, "raw": True}]},
        {"pack": GREEN, "recipe": GREEN, "enabled": True, "category": "crafting", "rocket": True,
         "machine_ready": True, "cost": 78, "research": [],
         "chain": [row("inserter", 1, "crafting"), row("transport-belt", 1, "crafting"),
                   row("iron-plate", 5.5, "smelting")]},
        {"pack": GREY, "recipe": GREY, "unlock": "military-science-pack", "category": "crafting", "military": True,
         "serves": 2000, "machine_ready": True, "cost": 248,
         "research": research(("military-2", 20), ("military-science-pack", 30)),
         "chain": [row("piercing-rounds-magazine", 0.5, "crafting", False, "military-2"),
                   row("steel-plate", 0.5, "smelting"), row("grenade", 0.5, "crafting", False, "military-2"),
                   row("stone-wall", 1, "crafting"), row("stone-brick", 5, "smelting"),
                   {"name": "coal", "per": 5, "raw": True}]},
        {"pack": BLUE, "recipe": BLUE, "unlock": BLUE, "category": "crafting", "rocket": True, "machine_ready": True,
         "cost": 1969,
         "research": research(("engine", 100), ("plastics", 200), ("advanced-circuit", 200), (BLUE, 75))
         + [{"name": "oil-processing", "units": 0, "time": 0, "packs": [], "trigger": True}],
         "chain": [row("engine-unit", 1, "advanced-crafting", False, "engine"), row("steel-plate", 1, "smelting"),
                   row("plastic-bar", 3, "chemistry", False, "plastics"),
                   {"name": "petroleum-gas", "type": "fluid", "per": 30, "recipe": "basic-oil-processing",
                    "category": "oil-processing", "tech": "oil-processing"}]},
        {"pack": "production-science-pack", "recipe": "production-science-pack", "category": "crafting",
         "machine_ready": True, "cost": 4000, "research": research(("railway", 75)), "chain": []},
    ],
    "made_in": {"crafting": ["assembling-machine-1", "assembling-machine-2"],
                "advanced-crafting": ["assembling-machine-1", "assembling-machine-2"],
                "smelting": ["steel-furnace", "stone-furnace"], "chemistry": ["chemical-plant"],
                "oil-processing": ["oil-refinery"]},
    "labs": {"speeds": {"lab": 1}, "bonus": 0},
    "rocket": {"units": 3000, "time": 30},
    "share": 1,
}


def block(purpose, machines, recipes, out=None):
    b = {"id": purpose, "purpose": purpose, "machines": machines, "recipes": recipes}
    if out:
        b["measured"] = {"minutes": 30, "out5": out}
    return b


RED_BLOCK = block("red-science-automation", {"assembling-machine-1": 2, "lab": 1}, [RED, "iron-gear-wheel"],
                  {RED: 0.05, "iron-gear-wheel": 0.05})
GREEN_BLOCK = block("green-science-automation", {"assembling-machine-1": 1}, [GREEN], {GREEN: 0.05})
LABS = block("research-lab", {"lab": 1}, [])
IRON = block("electric-iron-smelting", {"stone-furnace": 16, "electric-mining-drill": 8}, ["iron-plate"],
             {"iron-plate": 6.0})
COPPER = block("electric-copper-smelting", {"stone-furnace": 8}, ["copper-plate"], {"copper-plate": 3.0})
BRICKS = block("electric-brick-smelting", {"stone-furnace": 4}, ["stone-brick"], {"stone-brick": 2.0})


def site(minute=100, base=None, team=True, tiers=None, **extra):
    out = {"tick": minute * 3600, "base": [RED_BLOCK, GREEN_BLOCK, LABS, IRON, COPPER, BRICKS] if base is None else base,
           "tiers": copy.deepcopy(TIERS) if tiers is None else tiers,
           "space_path": {"packs": {RED: 3000, GREEN: 3000, BLUE: 2000}}}
    if team:
        out["team"] = {"plot": [0, 0, 384, 384], "area": [0, 0, 1152, 1152]}
    out.update(extra)
    return out


def researched(tiers, *techs):
    """The export once these techs are done: their recipes unlocked, gone from the research lists."""
    tiers = copy.deepcopy(tiers)
    for tier in tiers["tiers"]:
        tier["research"] = [r for r in tier["research"] if r["name"] not in techs]
        if tier.get("unlock") in techs:
            tier["enabled"] = True
            tier.pop("unlock")
        for c in tier["chain"]:
            if c.get("tech") in techs:
                c["enabled"] = True
                c.pop("tech")
    return tiers


class TiersTests(unittest.TestCase):
    def test_the_wanted_tiers_in_order_the_military_one_only_where_arms_matter(self):
        packs = [t.pack for t in tier_path.assess(site()).tiers]
        self.assertEqual(packs, [RED, GREEN, GREY, BLUE])          # (production science: not on any way wanted)
        self.assertEqual([t.pack for t in tier_path.assess(site(team=False)).tiers], [RED, GREEN, BLUE])
        aggressive = site(team=False, personality={"traits": ["aggressive"]})
        self.assertIn(GREY, [t.pack for t in tier_path.assess(aggressive).tiers])
        path = tier_path.assess(site())
        self.assertEqual([t.aim for t in path.tiers], [25, 100, 120, 210])   # (the user's pace targets)
        self.assertEqual(path.tier(GREEN).aim_from, 50)     # green: 50 to 100, when it can run at clean ratios
        self.assertEqual(path.next.pack, GREY)                              # red and green made by machines

    def test_an_old_site_without_tiers_has_nothing_due(self):
        path = tier_path.assess({"tick": 3600})
        self.assertEqual((path.tiers, path.dues(), path.research()), ([], [], []))
        self.assertFalse(path.due("steel-plate").due)


class HandFedTests(unittest.TestCase):
    """A pack line fed from chests by hand is made by machine but not automated (the user, 2026-10-29: "chests with
    items mean it won't be automated"): no second line for it, its packs still go to the labs, and it is not done."""

    def by_hand(self, b, *items):
        return {**b, "hand_fed": {r: list(items) for r in b["recipes"]}}

    def test_a_line_fed_by_hand_stands_but_is_not_automated(self):
        base = [self.by_hand(RED_BLOCK, "iron-plate"), GREEN_BLOCK, LABS, IRON, COPPER, BRICKS]
        path = tier_path.assess(site(base=base))
        red = path.tier(RED)
        self.assertEqual((red.machine, red.automated), (True, False))
        self.assertEqual(red.hand_fed, ("iron-plate", "iron-gear-wheel"))   # (its own gears too: no other maker)
        self.assertEqual(path.next.pack, GREY)   # (B, the coordinator: the path moves on once made by machine)
        self.assertEqual(tier_path.bridged(site(base=base)), {RED: ("iron-plate", "iron-gear-wheel")})
        d = path.due(RED)
        self.assertFalse(d.due)                                     # (no second line: belts to it are what is wanted)
        self.assertIn("fed by hand", d.why)
        from companion import tier_builds
        self.assertIn(RED, tier_builds._lab_packs(site(base=base), path, ""))   # (its packs still feed the labs)
        self.assertEqual(path.as_dict()["tiers"][0]["hand_fed"], ["iron-plate", "iron-gear-wheel"])

    def test_a_hand_fed_tier_says_where_its_hand_chests_are_and_since_when(self):
        red = {**self.by_hand(RED_BLOCK, "iron-plate"), "accepted": 20 * 3600,
               "hand_chests": [{"x": 3, "y": 4, "items": ["iron-plate"]}]}
        t = tier_path.assess(site(base=[red, GREEN_BLOCK, LABS, IRON, COPPER, BRICKS])).tier(RED)
        self.assertEqual(t.hand_chests, [{"block": RED_BLOCK["id"], "x": 3, "y": 4, "items": ["iron-plate"]}])
        self.assertEqual(t.hand_since, 20.0)

    def test_one_maker_fed_by_belts_is_enough(self):
        base = [self.by_hand(RED_BLOCK, "iron-plate"), {**RED_BLOCK, "id": "red-2"}, GREEN_BLOCK, LABS, IRON,
                COPPER, BRICKS]
        self.assertTrue(tier_path.assess(site(base=base)).tier(RED).automated)

    def test_a_chain_item_made_only_by_hand_fed_rows_keeps_the_tier_from_automated(self):
        rows = self.by_hand(block("tier-inserter", {"assembling-machine-1": 1}, ["inserter"]), "electronic-circuit")
        path = tier_path.assess(site(base=[RED_BLOCK, GREEN_BLOCK, LABS, IRON, COPPER, BRICKS, rows]))
        green = path.tier(GREEN)
        self.assertFalse(green.automated)
        self.assertIn("inserter", green.hand_fed)
        self.assertTrue(path.tier(RED).automated)                   # (red takes no inserters)

    def test_a_hand_fed_machine_of_another_recipe_in_the_block_does_not_count(self):
        red = {**RED_BLOCK, "recipes": [RED, "iron-gear-wheel", "stone-furnace"],
               "hand_fed": {"stone-furnace": ["stone"]}}
        self.assertTrue(tier_path.assess(site(base=[red, GREEN_BLOCK, LABS, IRON, COPPER, BRICKS])).tier(RED).automated)

    def test_the_pace_milestone_reads_the_whole_chain(self):
        rows = self.by_hand(block("tier-inserter", {"assembling-machine-1": 1}, ["inserter"]), "electronic-circuit")
        s = site(base=[RED_BLOCK, GREEN_BLOCK, LABS, IRON, COPPER, BRICKS, rows])
        self.assertIn("inserter", tier_path.pack_hand_fed(s, GREEN))   # (the green line itself is fed by belt)
        self.assertEqual(tier_path.pack_hand_fed(s, RED), ())
        walls = self.by_hand(block("tier-wall", {"assembling-machine-1": 1}, ["piercing-rounds-magazine"]), "iron-plate")
        grey = block("grey", {"assembling-machine-1": 1}, [GREY])
        off_path = site(team=False, base=[RED_BLOCK, GREEN_BLOCK, LABS, IRON, COPPER, BRICKS, grey, walls])
        self.assertIsNone(tier_path.assess(off_path).tier(GREY))           # (arms do not matter here)
        self.assertIn("piercing-rounds-magazine", tier_path.pack_hand_fed(off_path, GREY))   # (its chain all the same)

    def test_plates_from_furnaces_fed_by_hand_count_too(self):
        base = [RED_BLOCK, GREEN_BLOCK, LABS, self.by_hand(IRON, "iron-ore"), COPPER, BRICKS]
        self.assertIn("iron-plate", tier_path.assess(site(base=base)).tier(RED).hand_fed)


class SteelTests(unittest.TestCase):
    def test_steel_is_due_once_grey_science_is_next_and_its_recipe_is_researched(self):
        d = tier_path.due(site(), "steel-plate")
        self.assertTrue(d.due, d.why)
        self.assertEqual((d.tier, d.have, d.aim), (GREY, 0.0, 90))         # grey's aim less the supply lead
        self.assertIn("none made yet", d.why)
        self.assertGreater(d.rate, 0)

    def test_steel_waits_for_the_tier_before_and_for_its_research(self):
        no_green = site(base=[RED_BLOCK, LABS, IRON, COPPER])
        # (researched ahead of its tier, a supply is due once the rocket's tiers before it run: green science first)
        self.assertFalse(tier_path.due(no_green, "steel-plate").due)
        tiers = copy.deepcopy(TIERS)
        for tier in tiers["tiers"]:
            for c in tier["chain"]:
                if c["name"] == "steel-plate":
                    c.pop("enabled")
                    c["tech"] = "steel-processing"
        d = tier_path.due(site(tiers=tiers), "steel-plate")
        self.assertFalse(d.due)
        self.assertIn("steel-processing", d.why)

    def test_steel_made_enough_is_not_due(self):
        steel = block("steel-smelting", {"stone-furnace": 2}, ["steel-plate"], {"steel-plate": 1.0})
        self.assertFalse(tier_path.due(site(base=[RED_BLOCK, GREEN_BLOCK, LABS, IRON, steel]), "steel-plate").due)


class PackTests(unittest.TestCase):
    def test_grey_science_waits_for_its_research_then_is_due_and_says_what_it_waits_on(self):
        d = tier_path.due(site(), GREY)
        self.assertFalse(d.due)
        self.assertIn("military-2", d.why)
        ready = site(tiers=researched(TIERS, "military-2", "military-science-pack"))
        d = tier_path.due(ready, GREY)
        self.assertTrue(d.due, d.why)
        self.assertEqual((d.aim, d.waits), (120, ("steel-plate",)))         # no steel made yet
        self.assertGreaterEqual(d.rate, tier_path.MIN_RATE)

    def test_more_of_a_pack_is_due_when_the_research_ahead_wants_more(self):
        d = tier_path.due(site(), RED)
        self.assertTrue(d.due, d.why)
        # the rocket's 3000 units by minute 360, from minute 100, with the slack, a second
        self.assertAlmostEqual(d.rate, 1.25 * 3000 / 260 / 60, places=4)
        self.assertAlmostEqual(d.have, 0.05)
        fast = block("red-science-automation", {"assembling-machine-1": 10}, [RED], {RED: 0.3})
        self.assertFalse(tier_path.due(site(base=[fast, GREEN_BLOCK]), RED).due)

    def test_the_team_shares_the_research(self):
        tiers = copy.deepcopy(TIERS)
        tiers["share"] = 4
        one, team = tier_path.due(site(), RED), tier_path.due(site(tiers=tiers), RED)
        self.assertAlmostEqual(team.rate, one.rate / 4)

    def test_grey_is_sized_by_the_military_share_more_as_the_biters_evolve(self):
        ready = researched(TIERS, "military-2", "military-science-pack")
        calm = tier_path.assess(site(tiers=ready)).wanted(GREY)
        evolved = tier_path.assess(site(tiers=ready, threat_board={"evolution": 1.0})).wanted(GREY)
        self.assertGreater(evolved, calm)
        rocket = tier_path.assess(site(tiers=ready)).wanted(RED)
        self.assertLess(calm, rocket)                        # (a share beside the way to space, not all of it)


class LabTests(unittest.TestCase):
    def test_labs_are_due_when_packs_wait_for_them(self):
        busy = [block("red-science-automation", {"assembling-machine-1": 4}, [RED], {RED: 0.2}),
                block("green-science-automation", {"assembling-machine-1": 4}, [GREEN], {GREEN: 0.2}),
                block("research-lab", {"lab": 2}, [])]
        d = tier_path.due(site(base=busy), "lab")
        self.assertTrue(d.due, d.why)          # 0.2 units a second, 30 s each: 6 labs' work for 2 labs
        self.assertEqual(d.have, 2)
        self.assertGreaterEqual(d.rate, 6)
        enough = busy[:2] + [block("research-lab", {"lab": 8}, [])]
        self.assertFalse(tier_path.due(site(base=enough), "lab").due)

    def test_labs_are_not_the_answer_while_the_packs_are_short(self):
        d = tier_path.due(site(), "lab")       # 0.05 a second keeps under 2 labs busy: more science instead
        self.assertFalse(d.due)
        self.assertTrue(tier_path.due(site(), RED).due)


class ResearchTests(unittest.TestCase):
    def test_research_goes_to_the_next_tier_then_the_one_after_once_the_next_can_be_made(self):
        self.assertEqual(tier_path.assess(site()).research(), ["military-2", "military-science-pack"])
        ready = site(tiers=researched(TIERS, "military-2", "military-science-pack"))
        self.assertEqual(tier_path.assess(ready).research(), ["engine", "plastics", "advanced-circuit", BLUE])
        # (a trigger technology is no research to pick)

    def test_the_first_tier_waits_for_a_machine_to_make_it_in(self):
        tiers = copy.deepcopy(TIERS)
        tiers["tiers"][0]["machine_ready"] = False
        tiers["tiers"][0]["research"] = [{"name": "automation", "units": 10, "time": 10, "packs": [RED]}]
        path = tier_path.assess(site(base=[], tiers=tiers))
        self.assertEqual(path.research(), ["automation"])       # (not green's research yet)
        self.assertFalse(path.due(RED).due)

    def test_the_tier_pick_takes_the_next_tiers_research_before_the_military_share_and_the_rocket(self):
        from companion.catalog_planner import CatalogPlanner
        planner = CatalogPlanner(catalog=None, log=lambda _: None)
        planner._last_site = site()
        options = [{"name": "oil-processing", "rocket_path": True, "units": 100, "packs": [RED, GREEN]},
                   {"name": "military-2", "military": True, "units": 20, "packs": [RED, GREEN]},
                   {"name": "toolbelt", "units": 100, "packs": [RED, GREEN]}]
        request = {"request_id": "1:r9", "team": True, "options": options, "tick": 100 * 3600,
                   "researched": {"military": 10, "rocket_path": 20},          # (its military share is not due)
                   "threat_board": {"evolution": 0, "attacks": []}}
        self.assertEqual(planner.choose_research(request), "military-2")
        self.assertEqual(research_brain.tier_pick(options, site()), ("military-2", research_brain.tier_pick(
            options, site())[1]))
        self.assertIsNone(research_brain.tier_pick(options[:1] + options[2:], site()))   # not offered: nothing
        rows = {r["name"]: r for r in research_brain.weigh(options, site())}
        self.assertTrue(rows["military-2"]["tier"])
        self.assertFalse(rows["toolbelt"]["tier"])


def lua_runtime(amd_enabled=True, extra=""):
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().recipes_source = (ROOT / "mod" / "scripts" / "recipes.lua").read_text(encoding="utf-8")
    lua.globals().progression_source = (ROOT / "mod" / "scripts" / "progression.lua").read_text(encoding="utf-8")
    lua.globals().amd_enabled = amd_enabled
    lua.execute("""
        R = assert(load(recipes_source))()
        P = assert(load(progression_source))()
        RED, GREEN, GREY = 'automation-science-pack', 'logistic-science-pack', 'military-science-pack'
        function recipe(name, enabled, category, ingredients, n)
            local list = {}
            for i, pair in ipairs(ingredients) do list[i] = {type='item', name=pair[1], amount=pair[2]} end
            return {name=name, enabled=enabled, category=category, ingredients=list,
                    products={{type='item', name=name, amount=n or 1}}, prototype={}}
        end
        recipes = {
            [RED]=recipe(RED, true, 'crafting', {{'copper-plate', 1}, {'iron-gear-wheel', 1}}),
            [GREEN]=recipe(GREEN, true, 'crafting', {{'inserter', 1}, {'iron-plate', 1}}),
            [GREY]=recipe(GREY, false, 'crafting', {{'piercing-rounds-magazine', 1}, {'stone-wall', 2}}, 2),
            ['piercing-rounds-magazine']=recipe('piercing-rounds-magazine', false, 'crafting',
                                                {{'steel-plate', 1}, {'copper-plate', 5}}),
            ['steel-plate']=recipe('steel-plate', true, 'smelting', {{'iron-plate', 5}}),
            ['iron-plate']=recipe('iron-plate', true, 'smelting', {{'iron-ore', 1}}),
            ['copper-plate']=recipe('copper-plate', true, 'smelting', {{'copper-ore', 1}}),
            ['iron-gear-wheel']=recipe('iron-gear-wheel', true, 'crafting', {{'iron-plate', 2}}),
            ['inserter']=recipe('inserter', true, 'crafting', {{'iron-plate', 2}}),
            ['stone-wall']=recipe('stone-wall', true, 'crafting', {{'stone', 5}}),
            ['assembling-machine-1']=recipe('assembling-machine-1', amd_enabled, 'crafting', {{'iron-plate', 9}}),
            ['stone-furnace']=recipe('stone-furnace', true, 'crafting', {{'stone', 5}}),
        }
        techs = {}
        function tech(name, units, packs, unlocks, prereqs, done)
            local ingredients, effects, before = {}, {}, {}
            for i, p in ipairs(packs) do ingredients[i] = {type='item', name=p, amount=1} end
            for i, r in ipairs(unlocks) do effects[i] = {type='unlock-recipe', recipe=r} end
            for _, p in ipairs(prereqs) do before[p] = techs[p] end
            techs[name] = {name=name, enabled=true, researched=done or false, research_unit_count=units,
                           research_unit_energy=900, research_unit_ingredients=ingredients, prerequisites=before,
                           prototype={effects=effects}, effects=effects}
        end
        tech('automation', 10, {RED}, {'assembling-machine-1'}, {}, amd_enabled)
        tech('logistic-science-pack', 75, {RED}, {}, {'automation'}, true)
        tech('military-2', 20, {RED, GREEN}, {'piercing-rounds-magazine'}, {'logistic-science-pack'})
        tech('military-science-pack', 30, {RED, GREEN}, {GREY}, {'military-2'})
        tech('rocket-silo', 1000, {RED, GREEN}, {}, {'logistic-science-pack'})
        tech('physical-projectile-damage-3', 300, {RED, GREEN, GREY}, {}, {'military-science-pack'})
        force = {technologies=techs, recipes=recipes, laboratory_speed_modifier=0.5}
        prototypes = {technology=techs, recipe=recipes,
            entity={['iron-ore']={mineable_properties={products={{name='iron-ore'}}}},
                    ['copper-ore']={mineable_properties={products={{name='copper-ore'}}}},
                    ['rock']={mineable_properties={products={{name='stone'}}}},
                    ['assembling-machine-1']={items_to_place_this={{name='assembling-machine-1'}}},
                    ['stone-furnace']={items_to_place_this={{name='stone-furnace'}}}},
            tile={water={fluid={name='water'}}}}
        function prototypes.get_entity_filtered(filters)
            local f = filters[1]
            if f.filter == 'type' and f.type == 'lab' then
                return {lab={lab_inputs={RED, GREEN, GREY}, get_researching_speed=function() return 1 end}}
            elseif f.filter == 'crafting-machine' then
                local out = {['assembling-machine-1']={crafting_categories={crafting=true}},
                             ['stone-furnace']={crafting_categories={smelting=true}}}
                for k, v in pairs(more_machines or {}) do out[k] = v end
                return out
            end
            return {}
        end
        game = {tick=0}
        placed_tiles = {water=true}   -- (the tiles this surface actually has)
        surface = {index=1, map_gen_settings={autoplace_settings={entity={settings={['iron-ore']={}, ['copper-ore']={},
                   rock={}}}, tile={settings={water={}}}}}, get_property=function() return 1 end,
                   count_tiles_filtered=function(f)
                       for _, n in ipairs(f.name) do if placed_tiles[n] then return 1 end end
                       return 0
                   end,
                   find_entities_filtered=function() return {} end}
        storage = {npcs={{character={valid=true}}, {character={valid=true}}}}
        military = {{name='physical-projectile-damage-3', packs={RED, GREEN, GREY}},
                    {name='military-2', packs={RED, GREEN}}}
        function tiers(team)
            return P.tiers(force, surface, {recipes=R, military_techs=military, team=team})
        end
    """)
    if extra:
        lua.execute(extra)
    return lua


def lua_to_py(value):
    if hasattr(value, "items"):
        keys = list(value.keys())
        if keys and all(isinstance(k, int) for k in keys):
            return [lua_to_py(value[i]) for i in range(1, len(keys) + 1)]
        return {k: lua_to_py(v) for k, v in value.items()}
    return value


class GameExportTests(unittest.TestCase):
    def test_the_game_lists_each_pack_its_research_and_what_it_is_made_of(self):
        out = lua_to_py(lua_runtime().eval("tiers(true)"))
        tiers = {t["pack"]: t for t in out["tiers"]}
        self.assertEqual([t["pack"] for t in out["tiers"]], [RED, GREEN, GREY])    # by the research before each
        grey = tiers[GREY]
        self.assertEqual([r["name"] for r in grey["research"]], ["military-2", "military-science-pack"])
        self.assertTrue(grey["military"])           # the one pack a weapons tech needs beside the rocket's
        self.assertFalse(tiers[RED].get("military", False))
        self.assertTrue(tiers[RED]["rocket"] and tiers[GREEN]["rocket"])
        self.assertEqual(grey["serves"], 300)
        chain = {c["name"]: c for c in grey["chain"]}
        self.assertEqual(chain["steel-plate"]["per"], 0.5)          # a craft makes two packs
        self.assertEqual(chain["iron-plate"]["per"], 2.5)
        self.assertEqual(chain["piercing-rounds-magazine"]["tech"], "military-2")
        self.assertTrue(chain["iron-ore"]["raw"] and chain["stone"]["raw"])
        self.assertEqual(out["made_in"]["smelting"], ["stone-furnace"])
        self.assertEqual((out["share"], out["labs"]["bonus"]), (2, 0.5))
        self.assertEqual(lua_to_py(lua_runtime().eval("tiers(false)"))["share"], 1)

    def test_a_pack_with_no_machine_unlocked_waits_for_the_research_that_unlocks_one(self):
        out = lua_to_py(lua_runtime(amd_enabled=False).eval("tiers(false)"))
        red = next(t for t in out["tiers"] if t["pack"] == RED)
        self.assertIs(red["machine_ready"], False)
        self.assertEqual([r["name"] for r in red["research"]], ["automation"])

    def test_a_cheaper_route_the_force_cannot_research_is_not_the_one_named(self):
        out = lua_to_py(lua_runtime(extra="""
            tech('old-grey', 1, {RED}, {GREY}, {})
            techs['old-grey'].enabled = false
        """).eval("tiers(true)"))
        grey = next(t for t in out["tiers"] if t["pack"] == GREY)
        self.assertEqual(grey["unlock"], "military-science-pack")
        self.assertNotIn("old-grey", [r["name"] for r in grey["research"]])

    def test_what_this_surface_cannot_make_or_its_map_does_not_place_is_unavailable_not_raw(self):
        out = lua_to_py(lua_runtime(extra="""
            recipes['steel-plate'].prototype = {surface_conditions={{property='pressure', min=4000, max=4000}}}
            surface.map_gen_settings.autoplace_settings.entity.settings['copper-ore'] = {size=0}
        """).eval("tiers(true)"))
        tiers = {t["pack"]: t for t in out["tiers"]}
        chain = {c["name"]: c for c in tiers[GREY]["chain"]}
        self.assertTrue(chain["steel-plate"].get("unavailable"))
        self.assertFalse(chain["steel-plate"].get("raw", False))
        red = {c["name"]: c for c in tiers[RED]["chain"]}
        self.assertTrue(red["copper-ore"].get("unavailable"))        # (switched off on this map)
        exported = {**TIERS, "tiers": out["tiers"], "made_in": out["made_in"]}
        path = tier_path.assess(site(tiers=researched(exported, "military-2", "military-science-pack")))
        self.assertIn("steel-plate", path.tier(GREY).unavailable)
        self.assertFalse(path.tier(GREY).unlocked)
        self.assertFalse(path.due(GREY).due)
        self.assertIn("cannot be had", path.due(GREY).why)

    def test_water_is_raw_by_the_maps_default_and_not_where_the_map_switches_it_off(self):
        lua = lua_runtime(extra="""
            recipes['stone-wall'] = recipe('stone-wall', true, 'crafting', {{'stone', 5}})
            recipes['stone-wall'].ingredients[2] = {type='fluid', name='water', amount=10}
            prototypes.tile.water.autoplace_specification = {}
        """)
        def water(settings_lua):
            lua.execute(settings_lua + "; storage.npc_tier_present = nil")   # (as an hour later: looked at again)
            out = lua_to_py(lua.eval("tiers(true)"))
            grey = next(t for t in out["tiers"] if t["pack"] == GREY)
            return next(c for c in grey["chain"] if c["name"] == "water")
        self.assertTrue(water("surface.map_gen_settings.autoplace_settings.tile = {settings={water={}}}").get("raw"))
        self.assertTrue(water("surface.map_gen_settings.autoplace_settings.tile = "
                              "{treat_missing_as_default=true, settings={}}").get("raw"))
        self.assertTrue(water("surface.map_gen_settings.autoplace_settings.tile = "
                              "{settings={water={size=0}}}").get("unavailable"))

    def test_a_chain_item_whose_machine_is_locked_waits_for_that_research(self):
        out = lua_to_py(lua_runtime(extra="""
            recipes['stone-furnace'].enabled = false
            tech('furnaces', 15, {RED}, {'stone-furnace'}, {})
        """).eval("tiers(true)"))
        grey = next(t for t in out["tiers"] if t["pack"] == GREY)
        steel = next(c for c in grey["chain"] if c["name"] == "steel-plate")
        self.assertEqual(steel["machine_tech"], "furnaces")
        self.assertIn("furnaces", [r["name"] for r in grey["research"]])
        ready = researched({**TIERS, "tiers": out["tiers"], "made_in": out["made_in"]}, "military-2",
                           "military-science-pack")
        self.assertFalse(tier_path.assess(site(tiers=ready)).tier(GREY).unlocked)
        d = tier_path.due(site(tiers=ready), "steel-plate")
        self.assertFalse(d.due)                                  # (no furnace to make it in yet)
        self.assertIn("furnaces", d.why)

    def test_the_game_counts_the_pack_sets_of_the_research_ahead(self):
        out = lua_to_py(lua_runtime().eval("tiers(true)"))
        sets = {tuple(r["packs"]): r["units"] for r in out["sets"]}
        self.assertEqual(sets[(RED, GREEN)], 1000)                          # the rocket's
        self.assertEqual(sets[(RED, GREEN, GREY)], 300)                     # the weapons research grey serves

    def test_the_launch_is_what_the_silos_research_unlocks_that_only_a_silo_makes(self):
        out = lua_to_py(lua_runtime(extra="""
            more_machines = {['rocket-silo']={crafting_categories={['rocket-building']=true}}}
            prototypes.entity['rocket-silo'] = {type='rocket-silo', items_to_place_this={{name='rocket-silo'}}}
            recipes['rocket-silo'] = recipe('rocket-silo', true, 'crafting', {{'steel-plate', 100}})
            recipes['rocket-part'] = recipe('rocket-part', true, 'rocket-building', {{'steel-plate', 1}})
            techs['rocket-silo'].effects = {{type='unlock-recipe', recipe='rocket-part'},
                                            {type='unlock-recipe', recipe='rocket-silo'}}
        """).eval("tiers(true)"))
        launch = [t for t in out["tiers"] if t.get("launch")]
        self.assertEqual([t["pack"] for t in launch], ["rocket-part"])
        self.assertTrue(launch[0]["rocket"] and launch[0]["machine_ready"])
        path = tier_path.assess(site(tiers={**TIERS, "tiers": TIERS["tiers"] + launch,
                                            "made_in": {**TIERS["made_in"], **out["made_in"]}}))
        self.assertEqual(path.tier("rocket-part").aim, 480)

    def test_a_pack_no_machine_can_ever_make_is_unavailable(self):
        out = lua_to_py(lua_runtime(amd_enabled=False, extra="""
            prototypes.entity['assembling-machine-1'].items_to_place_this = {}
        """).eval("tiers(false)"))
        red = next(t for t in out["tiers"] if t["pack"] == RED)
        self.assertIs(red["machine_ready"], False)
        self.assertTrue(red.get("unavailable"))

    def test_a_locked_machine_shared_by_two_categories_joins_nothing(self):
        out = lua_to_py(lua_runtime(extra="""
            more_machines = {universal={crafting_categories={crafting=true, smelting=true}}}
            prototypes.entity.universal = {items_to_place_this={{name='universal'}}}
            recipes.universal = recipe('universal', false, 'crafting', {{'iron-plate', 50}})
            tech('universal-tech', 500, {RED, GREEN}, {'universal'}, {})
        """).eval("tiers(true)"))
        self.assertEqual(out["made_in"]["smelting"], ["stone-furnace"])
        self.assertTrue(tier_path.due(site(tiers={**TIERS, "made_in": out["made_in"]}), "steel-plate").due)


class FindingTests(unittest.TestCase):
    """The review's cases (thread R-1-tier-path), one class: what is stopped, stale, slow or out of reach must not
    be read as running, fresh, fast or at hand."""

    def test_a_stopped_block_makes_nothing(self):
        stopped = block("red-science-automation", {"assembling-machine-1": 10}, [RED])
        stopped["measured"] = {"minutes": 120, "shares": {"starved": 1.0}}   # (measured: no rates, all zero)
        stopped["capacity"] = {"makes": {RED: 1.0}}
        self.assertEqual(tier_path.made_rate(site(base=[stopped]), RED, RED), 0.0)
        self.assertTrue(tier_path.due(site(base=[stopped, GREEN_BLOCK]), RED).due)
        new = dict(stopped)
        new.pop("measured")                                   # just built, not measured: what it is built to make
        self.assertEqual(tier_path.made_rate(site(base=[new]), RED, RED), 1.0)

    def test_a_backed_up_block_has_spare_for_the_taking_not_a_shortfall(self):
        full = block("electric-steel-smelting", {"stone-furnace": 4}, ["steel-plate"])
        full["measured"] = {"minutes": 60, "out": {"steel-plate": 0.05}, "shares": {"backed_up": 0.9, "working": 0.1}}
        full["capacity"] = {"makes": {"steel-plate": 0.5}}
        d = tier_path.due(site(base=[RED_BLOCK, GREEN_BLOCK, IRON, full]), "steel-plate")
        self.assertFalse(d.due, d.why)                      # made 0 lately, but 0.45 a second waits to be taken
        self.assertAlmostEqual(d.have, 0.45)
        # a furnace that never made steel among backed-up assemblers: the block's share is not its spare
        mixed = block("mall", {"assembling-machine-1": 9, "stone-furnace": 1}, ["iron-gear-wheel", "steel-plate"])
        mixed["measured"] = {"minutes": 60, "out": {"iron-gear-wheel": 2.0}, "shares": {"backed_up": 0.9}}
        mixed["capacity"] = {"makes": {"steel-plate": 0.5, "iron-gear-wheel": 3.0}}
        self.assertTrue(tier_path.due(site(base=[RED_BLOCK, GREEN_BLOCK, IRON, mixed]), "steel-plate").due)

    def test_a_rate_missing_lately_is_none_whatever_it_made_before(self):
        # (a full list of the block's biggest rates, or a line stopped since: no guess from the longer window)
        many = block("bus-base", {"assembling-machine-2": 20}, [RED])
        many["measured"] = {"out5": {f"x{i}": 1.0 + i for i in range(8)}, "out": {RED: 5.0}}
        self.assertEqual(tier_path.made_rate(site(base=[many]), RED, RED), 0.0)

    def test_spare_never_puts_a_block_above_what_it_is_built_to_make(self):
        flat_out = block("red-science-automation", {"assembling-machine-1": 10}, [RED], {RED: 1.0})
        flat_out["measured"]["shares"] = {"backed_up": 0.8}    # (once, over the longer window, or other machines)
        flat_out["capacity"] = {"makes": {RED: 1.0}}
        self.assertAlmostEqual(tier_path.have_rate(site(base=[flat_out]), RED, RED), 1.0)

    def test_the_research_requests_own_tiers_are_used_over_an_older_site(self):
        options = [{"name": "engine", "units": 100, "packs": [RED, GREEN]},
                   {"name": "military-2", "units": 20, "packs": [RED, GREEN]}]
        fresh = researched(TIERS, "military-2", "military-science-pack")
        self.assertEqual(research_brain.tier_pick(options, site(), {"tiers": fresh, "tick": 101 * 3600})[0], "engine")
        self.assertEqual(research_brain.tier_pick(options, site())[0], "military-2")
        self.assertIsNone(research_brain.tier_pick(options, None, {"tiers": fresh}))   # (no plan request yet)
        focused = research_brain.tier_pick(options, site(base=[RED_BLOCK, LABS]),
                                           {"tiers": TIERS, "personality": {"traits": ["aggressive"]}})
        self.assertEqual(focused[0], "military-2")      # (the request's traits: grey beside green)

    def test_a_slow_new_tier_does_not_hide_the_labs_the_others_need(self):
        busy = [block("red-science-automation", {"assembling-machine-1": 4}, [RED], {RED: 0.2}),
                block("green-science-automation", {"assembling-machine-1": 4}, [GREEN], {GREEN: 0.2}),
                block("research-lab", {"lab": 2}, [])]
        slow_grey = busy + [block("military-science-automation", {"assembling-machine-1": 1}, [GREY], {GREY: 0.02})]
        ready = researched(TIERS, "military-2", "military-science-pack")
        self.assertTrue(tier_path.due(site(base=busy, tiers=ready), "lab").due)
        self.assertTrue(tier_path.due(site(base=slow_grey, tiers=ready), "lab").due)

    def test_a_supply_its_packs_machines_can_also_make_is_not_separate(self):
        shared = copy.deepcopy(TIERS)
        shared["made_in"]["smelting"] = ["assembling-machine-1", "stone-furnace"]   # (a mod's machine does both)
        self.assertFalse(tier_path.due(site(tiers=shared), "steel-plate").due)
        self.assertTrue(tier_path.due(site(), "steel-plate").due)

    def test_the_tier_pick_with_real_tiers_goes_after_what_goals_wait_on_and_before_the_early_list(self):
        from unittest import mock
        from companion import catalog_planner
        planner = catalog_planner.CatalogPlanner(catalog=None, log=lambda _: None)
        planner._last_site = site()
        options = [{"name": "fast-inserter", "early": True, "units": 30, "packs": [RED], "unlocks": ["fast-inserter"]},
                   {"name": "military-2", "units": 20, "packs": [RED, GREEN], "unlocks": ["piercing-rounds-magazine"]},
                   {"name": "logistics", "units": 75, "packs": [RED], "unlocks": ["splitter"]}]
        request = {"request_id": "1:r1", "options": options, "tick": 100 * 3600}
        with mock.patch.object(catalog_planner, "wanted_ranks", lambda s: {"splitter": 0}):
            self.assertEqual(planner.choose_research(request), "logistics")       # a goal waits on it
        with mock.patch.object(catalog_planner, "wanted_ranks", lambda s: {}):
            self.assertEqual(planner.choose_research(request), "military-2")       # the next tier, then the early list
            later = [o for o in options if o["name"] != "military-2"]
            self.assertEqual(planner.choose_research({**request, "options": later}), "fast-inserter")


class MilitaryFocusTests(unittest.TestCase):
    """The military focus (the traits choose_research arms first for) brings grey science forward, its supplies and
    the labs with it."""

    def focused(self, **extra):
        return site(personality={"traits": ["aggressive"]}, **extra)

    def test_grey_comes_sooner_and_alongside_green(self):
        self.assertEqual(tier_path.assess(self.focused()).tier(GREY).aim, 90)      # 120, three quarters
        before_green = [RED_BLOCK, LABS, IRON, COPPER, BRICKS]
        path = tier_path.assess(self.focused(base=before_green))
        self.assertEqual(path.next.pack, GREEN)
        self.assertTrue(path.ahead(path.tier(GREY)))                                # beside green, not after it
        self.assertEqual(path.research()[:2], ["military-2", "military-science-pack"])
        plain = tier_path.assess(site(base=before_green))
        self.assertFalse(plain.ahead(plain.tier(GREY)))       # (its research comes while green is built, its line after)
        self.assertTrue(tier_path.due(self.focused(base=before_green), "steel-plate").due)

    def test_a_focus_raises_the_weapons_share_and_brings_the_grey_line_and_its_labs(self):
        ready = researched(TIERS, "military-2", "military-science-pack")
        self.assertGreater(tier_path.assess(self.focused(tiers=ready)).wanted(GREY),
                           tier_path.assess(site(tiers=ready)).wanted(GREY))
        before_green = [RED_BLOCK, LABS, IRON, COPPER, BRICKS]
        dues = {d.item for d in tier_path.assess(self.focused(base=before_green, tiers=ready)).dues()}
        self.assertTrue({GREY, "steel-plate", GREEN} <= dues, dues)
        # (without the focus too, once grey's research is done: R-3; the focus's own part is its research and aim)
        self.assertIn(GREY, {d.item for d in tier_path.assess(site(base=before_green, tiers=ready)).dues()})


class SurvivorFocusTests(unittest.TestCase):
    def test_a_death_worlds_survivor_has_the_military_focus_as_the_aggressive_do(self):
        def traits(*names):
            return {"personality": {"traits": list(names)}}
        self.assertTrue(tier_path.military_focus(traits("survivor")))       # the death world's trait (team_world)
        self.assertTrue(tier_path.military_focus(traits("aggressive")))
        self.assertFalse(tier_path.military_focus(traits("cautious", "curious")))


class SurvivalTests(unittest.TestCase):
    """R-2: survival research (weapons, walls, armor, repair, radar) weighed by how hard the biters press, against the
    next tier's research: calm, the tier path; under threat, a cheap survival tech; a late tier needs more threat."""
    OPTIONS = [{"name": "military-2", "units": 20, "packs": [RED, GREEN]},
               {"name": "radar", "units": 20, "packs": [RED], "survival": True},
               {"name": "heavy-armor", "units": 30, "packs": [RED], "survival": True}]

    def pick(self, minute=100, attacks=(), evolution=0.0, options=None, **extra):
        board = {"evolution": evolution, "attacks": [{"tick": t * 3600, "x": 0, "y": 0} for t in attacks]}
        return research_brain.pick(options or self.OPTIONS, site(minute=minute, threat_board=board, **extra))

    def test_calm_the_tier_path_wins(self):
        self.assertEqual(self.pick()[0], "military-2")

    def test_under_attack_a_cheap_survival_tech_wins(self):
        picked = self.pick(attacks=[99.5, 99.8])
        self.assertEqual(picked[0], "radar", picked[1])          # (the cheaper of the two)
        self.assertIn("survival", picked[1])

    def test_a_tier_far_past_its_aim_needs_more_threat(self):
        # grey's aim is minute 120: at minute 400 it pulls hard, and an old attack's pressure no longer beats it
        self.assertEqual(self.pick(minute=400, attacks=[392])[0], "military-2")
        self.assertEqual(self.pick(minute=100, attacks=[92])[0], "radar")

    def test_evolved_biters_press_before_any_attack_and_survival_comes_alone_when_no_tier_waits(self):
        survival_only = self.OPTIONS[1:]
        self.assertIsNone(self.pick(options=survival_only))                       # calm: nothing pulls
        self.assertEqual(self.pick(options=survival_only, evolution=0.9)[0], "radar")
        level, _ = research_brain.pressure(site(threat_board={"evolution": 0.9, "attacks": []}))
        self.assertGreater(level, research_brain.TIER_PULL)

    def test_the_planner_takes_survival_through_its_research_choice(self):
        from companion.catalog_planner import CatalogPlanner
        planner = CatalogPlanner(catalog=None, log=lambda _: None)
        planner._last_site = site()
        request = {"request_id": "1:r2", "team": True, "options": self.OPTIONS, "tick": 100 * 3600,
                   "researched": {"military": 10, "rocket_path": 20},
                   "threat_board": {"evolution": 0.9, "attacks": []}}
        self.assertEqual(planner.choose_research(request), "radar")
        calm = {**request, "threat_board": {"evolution": 0.0, "attacks": []}}
        self.assertEqual(planner.choose_research(calm), "military-2")

    def test_under_attack_survival_beats_the_teams_basics_once_its_defense_list_is_done(self):
        from companion.catalog_planner import CatalogPlanner
        planner = CatalogPlanner(catalog=None, log=lambda _: None)
        planner._last_site = site()
        options = [{"name": "logistic-science-pack", "units": 75, "packs": [RED]}] + self.OPTIONS[1:]
        request = {"request_id": "1:r3", "team": True, "options": options, "tick": 100 * 3600,
                   "researched": {"military": 2, "rocket_path": 20},
                   "threat_board": {"evolution": 0.2, "attacks": [{"tick": 100 * 3600 - 600, "x": 0, "y": 0},
                                                                  {"tick": 100 * 3600 - 3000, "x": 0, "y": 0}]}}
        self.assertEqual(planner.choose_research(request), "radar")

    def test_under_pressure_the_weighing_comes_before_a_death_worlds_fixed_military_share(self):
        from companion.catalog_planner import CatalogPlanner
        planner = CatalogPlanner(catalog=None, log=lambda _: None)
        planner._last_site = site()
        options = [{"name": "military-3", "units": 400, "packs": [RED, GREEN, GREY], "military": True,
                    "survival": True}] + self.OPTIONS[1:]
        request = {"request_id": "1:r4", "team": True, "options": options, "tick": 100 * 3600,
                   "personality": {"traits": ["survivor"]}, "researched": {"military": 0, "rocket_path": 20},
                   "threat_board": {"evolution": 0.9, "attacks": []}}
        self.assertEqual(planner.choose_research(request), "radar")             # cheaper, weighed
        calm = {**request, "threat_board": {"evolution": 0.0, "attacks": []}}
        self.assertEqual(planner.choose_research(calm), "military-3")           # calm: its share, as before

    def test_a_full_emergency_beats_even_a_long_late_tier(self):
        breached = {"breaches": [{"x": 0, "y": 0, "tick": 400 * 3600}]}
        picked = self.pick(minute=400, attacks=[399.9, 399.5], **breached)
        self.assertEqual(picked[0], "radar", picked[1])

    def test_each_pick_logs_what_was_weighed(self):
        import json
        import os
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {"NPC_FACTIONS_DATA": folder}):
            self.pick(attacks=[99.5, 99.8])
            row = json.loads((Path(folder) / "decisions" / research_brain.LOG_NAME).read_text(
                encoding="utf-8").splitlines()[-1])
        self.assertEqual(row["chose"], "radar")
        self.assertGreater(row["pressure"], row["tier_score"])
        self.assertIn("heavy-armor", row["survival"])

    def test_the_game_marks_repair_and_radar_research_as_survival(self):
        lua = lua_runtime(extra="""
            prototypes.item = {['repair-pack']={type='repair-tool'}, radar={type='item', place_result={type='radar'}},
                               lamp={type='item', place_result={type='lamp'}}}
            recipes['repair-pack'] = recipe('repair-pack', false, 'crafting', {{'iron-plate', 2}})
            recipes.radar = recipe('radar', false, 'crafting', {{'iron-plate', 10}})
            recipes.lamp = recipe('lamp', false, 'crafting', {{'iron-plate', 1}})
            tech('repair-tech', 10, {RED}, {'repair-pack'}, {})
            tech('radar', 10, {RED}, {'radar'}, {})
            tech('lamp', 10, {RED}, {'lamp'}, {})
        """)
        marked = lua_to_py(lua.eval("P.survival_techs({{name='gun-turret'}})"))
        self.assertTrue(marked.get("repair-tech") and marked.get("radar") and marked.get("gun-turret"))
        self.assertFalse(marked.get("lamp", False))


if __name__ == "__main__":
    unittest.main()
