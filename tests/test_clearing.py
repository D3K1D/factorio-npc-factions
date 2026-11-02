"""Clearing ground ahead of time: the zones and their slices (companion/clearing.py), when the catalog
planner clears (after the opening, never ahead of anything urgent, defensive or military, by zone priority),
and the game's side (scripts/clearing.lua with designed_start and the worker) in Factorio's Lua 5.2."""
from pathlib import Path
import unittest
from unittest import mock

from companion import catalog_planner, clearing
from companion.catalog_planner import CONTINUE_PROJECTS, CatalogPlanner, available, clearing_due

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = {"x": -32, "y": -32}          # the site window's corner: home at (0, 0) in world tiles


def site(zones=(), *, clearing_report=None, done=("coal-loop", "steam-power"), **extra) -> dict:
    """An engineer past its opening (power runs; nothing else of the opening can be built here), with these
    zones in its factory plan."""
    out = {"request_id": "1:9", "npc_id": 1, "purpose": "choose", "origin": dict(ORIGIN), "home": {"x": 32, "y": 32},
           "tick": 100000, "completed_purposes": list(done), "failed_purposes": [], "recipes": [], "resources": [],
           "base": [], "factory_plan": {"zones": list(zones)}}
    if clearing_report is not None:
        out["clearing"] = {"frame": "world", "zones": clearing_report}
    out.update(extra)
    return out


def zone(zone_id="east", box=(40, -16, 104, 16), priority=0.3, **extra) -> dict:
    return {"id": zone_id, "box": list(box), "clear": True, "priority": priority, "why": "room for smelting", **extra}


def surveyed(zone_id, zone_box, boxes, source="plan") -> dict:
    """The game's record of a zone: each box as {"box": [...], "mine": n, ...}."""
    rows = [{"mine": 0, "blast": 0, "later": 0, "fixed": 0, "outside": 0, "tick": 90000, **b} for b in boxes]
    return {"id": zone_id, "box": list(zone_box), "source": source, "priority": 0.3, "boxes": rows}


class ZoneTests(unittest.TestCase):
    def test_a_zone_is_whole_tiles_its_priority_bounded_and_one_not_to_clear_is_none(self):
        z = clearing.zone_of({"id": "a", "box": [10.5, 2.2, 3.1, 40.9], "priority": 7})
        self.assertEqual((z["box"], z["priority"], z["source"]), ((3, 2, 11, 41), 1.0, "plan"))
        self.assertIsNone(clearing.zone_of({"id": "b", "box": [0, 0, 10, 10], "clear": False}))
        self.assertIsNone(clearing.zone_of({"id": "c", "box": [0, 0, 0, 10]}))
        self.assertIsNone(clearing.zone_of({"id": "d"}))
        big = clearing.zone_of({"id": "e", "box": [0, 0, 2000, 40]})
        self.assertEqual(big["box"], (0, 0, clearing.MAX_SIDE, 40))          # cut to what one zone may be

    def test_slices_cover_the_zone_nearest_home_first(self):
        z = clearing.zone_of(zone(box=(40, -16, 104, 30)))
        cut = clearing.slices(z, (0.0, 0.0))
        self.assertEqual(len(cut), 4)                                          # 64 wide, 46 high: 2 by 2
        self.assertEqual(cut[0], (40, -16, 72, 16))                            # the corner nearest home
        self.assertEqual(sum((b[2] - b[0]) * (b[3] - b[1]) for b in cut), 64 * 46)

    def test_pending_zones_go_by_priority_then_distance_and_leave_out_what_is_clear(self):
        near, far, urgent = zone("near", (40, 0, 72, 32)), zone("far", (300, 0, 332, 32)), \
            zone("next", (-200, 0, -168, 32), priority=0.9)
        s = site([near, far, urgent])
        self.assertEqual([z["id"] for z in clearing.pending_zones(s)], ["next", "near", "far"])
        cleared = surveyed("near", (40, 0, 72, 32), [{"box": [40, 0, 72, 32], "fixed": 2, "outside": 1}])
        s = site([near, far, urgent], clearing_report=[cleared])
        self.assertEqual([z["id"] for z in clearing.pending_zones(s)], ["next", "far"])   # what nothing clears
        p = clearing.progress(s, clearing.zone_of(near))
        self.assertEqual((p["done"], p["fraction"], p["left"]["fixed"]), (True, 1.0, 2))

    def test_a_slice_with_things_left_or_cliffs_it_can_blast_is_still_pending(self):
        z = zone("z", (40, 0, 72, 32))
        def state(**b):
            s = site([z], clearing_report=[surveyed("z", (40, 0, 72, 32), [{"box": [40, 0, 72, 32], **b}])])
            return clearing.done(s, clearing.zone_of(z))
        self.assertFalse(state(mine=3))
        self.assertFalse(state(blast=2))                                       # it can make the charges
        self.assertTrue(state(blast=2, short=True, tick=99000))               # could not get one: later
        self.assertFalse(state(blast=2, short=True, tick=100000 - clearing.BLAST_RETRY))   # tried again
        self.assertTrue(state(later=4, explosives=["cliff-explosives"]))      # not researched yet
        s = site([z], recipes=[{"name": "cliff-explosives", "products": [{"name": "cliff-explosives"}]}],
                 clearing_report=[surveyed("z", (40, 0, 72, 32), [{"box": [40, 0, 72, 32], "later": 4,
                                                                    "explosives": ["cliff-explosives"]}])])
        self.assertFalse(clearing.done(s, clearing.zone_of(z)))               # researched since: blast them

    def test_a_record_of_a_redrawn_zone_counts_for_nothing(self):
        z = zone("z", (40, 0, 72, 32))
        old = surveyed("z", (40, 0, 104, 32), [{"box": [40, 0, 72, 32]}])
        s = site([z], clearing_report=[old])
        self.assertFalse(clearing.done(s, clearing.zone_of(z)))

    def test_in_the_team_world_only_its_own_ground_is_cleared(self):
        # its plot is x 0..384 (world); the plot to its west is a teammate's; the team area ends at y 384
        team = {"plot": [32, 32, 416, 416], "area": [-352, -352, 800, 416], "no_build": [], "bands": []}
        theirs = zone("theirs", (-100, 40, -40, 80))
        mixed = zone("mixed", (-16, 40, 48, 72))
        outside = zone("past-wall", (100, 400, 132, 440))
        s = site([theirs, mixed, outside], team=team)
        self.assertEqual({z["id"] for z in clearing.pending_zones(s)}, {"mixed", "past-wall"})
        cut = [b for b, _, _ in clearing._state(s, clearing.zone_of(mixed))]
        self.assertEqual(cut, [(-16, 40, 16, 72), (16, 40, 48, 72)])          # (its plot starts at x 0)

    def test_a_zone_asked_for_with_the_request_comes_first_and_is_remembered_until_done(self):
        asked = {"id": "for-the-bus", "box": [0, 100, 32, 132], "priority": 0.2}
        s = site([zone(priority=0.9)], order={"zone": asked})
        self.assertEqual(clearing.zone_for(s)["id"], "for-the-bus")
        later = site([], clearing_report=[surveyed("for-the-bus", (0, 100, 32, 132),
                                                   [{"box": [0, 100, 32, 132], "mine": 5}], source="asked")])
        self.assertEqual([z["id"] for z in clearing.pending_zones(later)], ["for-the-bus"])
        gone = site([], clearing_report=[surveyed("old-plan", (0, 100, 32, 132),
                                                  [{"box": [0, 100, 32, 132], "mine": 5}], source="plan")])
        self.assertEqual(clearing.pending_zones(gone), [])                    # the factory plan dropped it


class PlanTests(unittest.TestCase):
    def test_the_next_stage_takes_the_nearest_slices_not_known_clear_in_local_tiles(self):
        z = zone("east", (40, -16, 168, 16))                 # four slices along x
        report = [surveyed("east", (40, -16, 168, 16), [{"box": [40, -16, 72, 16]}])]   # the nearest is clear
        plan = clearing.plan_clearing(site([z], clearing_report=report), z, stages=2)
        self.assertEqual((plan["purpose"], plan["entities"], plan["source"]), ("clear-area", [], "clearing"))
        boxes = [s["clear"]["box"] for s in plan["stages"]]
        self.assertEqual(boxes, [[104, 16, 136, 48], [136, 16, 168, 48]])      # world 72..136 less the origin
        self.assertEqual(plan["stages"][1]["depends_on"], ["clear-1"])
        self.assertEqual(plan["stages"][0]["witnesses"], [{"kind": "area_clear"}])
        self.assertEqual(plan["clearing"]["zone"]["box"], [72, 16, 200, 48])
        self.assertEqual((plan["clearing"]["budget"], plan["clearing"]["ticks"]), (clearing.BUDGET, clearing.TICKS))

    def test_a_zone_with_nothing_left_has_no_next_stage(self):
        z = zone("z", (40, 0, 72, 32))
        s = site([z], clearing_report=[surveyed("z", (40, 0, 72, 32), [{"box": [40, 0, 72, 32]}])])
        with self.assertRaises(ValueError):
            clearing.plan_clearing(s, z)

    def test_slices_beyond_the_games_reach_are_not_planned(self):
        z = zone("far", (900, 0, 932, 32))
        self.assertEqual(clearing.pending_zones(site([z])), [])


class Chooser:
    def __init__(self, pick=CONTINUE_PROJECTS):
        self.pick, self.offered = pick, []

    def choose(self, site, options, question="layout"):
        self.offered.append([o["id"] for o in options])
        return self.pick, {self.pick: 1.0}


def planner(chooser=None) -> CatalogPlanner:
    p = CatalogPlanner.__new__(CatalogPlanner)
    p.catalog, p.designer, p.chooser, p.fallback, p.log, p.seed = None, None, chooser, None, lambda m: None, 0
    p.assignments, p.tried_sites, p.held_scouts, p.world, p.modules = {}, {}, {}, None, None
    return p


class ScheduleTests(unittest.TestCase):
    def test_not_in_the_opening(self):
        self.assertTrue(available("clear-area", site([zone()])))
        self.assertFalse(available("clear-area", site([zone()], done=("coal-loop",))))            # no power yet
        stone = site([zone()], resources=[{"resource": "stone"}])
        self.assertFalse(available("clear-area", stone))                     # stone supply is next: the opening
        self.assertFalse(available("clear-area", site([])))                  # nothing to clear

    def test_a_low_priority_zone_waits_until_nothing_else_is_to_be_built(self):
        coal = site([zone(priority=0.3)], resources=[{"resource": "coal"}])
        self.assertEqual(planner().choose_purpose(coal), "coal-supply")
        self.assertEqual(planner().choose_purpose(site([zone(priority=0.3)])), "clear-area")
        self.assertEqual(planner().choose_purpose(site([])), CONTINUE_PROJECTS)

    def test_a_zone_whose_section_is_next_comes_before_new_goals_but_after_anything_urgent(self):
        coal = site([zone(priority=0.9)], resources=[{"resource": "coal"}])
        self.assertEqual(planner().choose_purpose(coal), "clear-area")
        recipes = [{"name": n} for n in catalog_planner.PURPOSES["power-plant"]["requires_recipes"]]
        short = site([zone(priority=0.9)], resources=[{"resource": "coal"}], power_short=True, recipes=recipes)
        self.assertEqual(planner().choose_purpose(short), "power-plant")
        attacked = site([zone(priority=0.9)], resources=[{"resource": "coal"}],
                        social={"recent_attacks": 2, "attackers": ["biters"]})
        self.assertEqual(planner().choose_purpose(attacked), "coal-supply")  # not off clearing after an attack
        self.assertIsNone(clearing_due(attacked))

    def test_system_one_never_weighs_clearing_and_its_nothing_new_becomes_clearing(self):
        pick = Chooser()
        coal = site([zone(priority=0.3)], resources=[{"resource": "coal"}])
        self.assertEqual(planner(pick).choose_purpose(coal), "clear-area")
        self.assertNotIn("clear-area", pick.offered[0])
        self.assertEqual(planner(Chooser("coal-supply")).choose_purpose(coal), "coal-supply")

    def test_in_the_team_world_the_plots_edge_comes_first(self):
        team = {"plot": [0, 0, 384, 384], "area": [-384, -384, 768, 768], "no_build": [], "bands": []}
        blocks = [{"purpose": "plot-turrets"}, {"purpose": "plot-turrets"}, {"purpose": "plot-wall"}]
        s = site([zone(priority=0.95, box=(40, 40, 72, 72))], team=team, base=blocks)
        from companion import perimeter
        edge = [perimeter.Edge("north", (0, 0, 384, 384), 0, 383)]
        with mock.patch("companion.perimeter.edges", return_value=edge), \
                mock.patch.object(catalog_planner, "perimeter_available", lambda p, _s: p == "plot-turrets"):
            self.assertIsNone(clearing_due(s))                                 # turret spots still open
            self.assertEqual(planner().choose_purpose(s), "plot-turrets")
        with mock.patch("companion.perimeter.edges", return_value=edge), \
                mock.patch.object(catalog_planner, "perimeter_available", lambda p, _s: False):
            self.assertEqual(planner().choose_purpose(s), "clear-area")

    def test_the_plan_is_routed_by_choice_and_by_name_with_a_zone_asked_for(self):
        p = planner()
        plan = p(site([zone(priority=0.3)]))
        self.assertEqual((plan["purpose"], len(plan["stages"])), ("clear-area", 2))
        asked = site([], purpose="clear-area", order={"zone": {"id": "pad", "box": [0, 60, 32, 92]}})
        plan = p(asked)
        self.assertEqual(plan["clearing"]["zone"]["id"], "pad")
        self.assertEqual(plan["clearing"]["zone"]["source"], "asked")
        with self.assertRaises(catalog_planner.Defer):   # nothing to clear: its own next goal, nothing new here
            p(site([], purpose="clear-area"))


# --- the game's side, in Factorio's Lua 5.2 without Factorio ------------------------------------------

GAME = r"""
    defines.inventory = defines.inventory or {}
    -- what a section is built from: these layers are what "in the way" means here
    local building = {layers={item=true, object=true, player=true, water_tile=true}}
    for name, kind in pairs({['assembling-machine-1']='assembling-machine', ['stone-furnace']='furnace',
                             ['transport-belt']='transport-belt'}) do
        prototypes.entity[name] = {name=name, type=kind, collision_mask=building}
    end
    prototypes.get_entity_filtered = function(filters)
        local out = {}
        for name, p in pairs(prototypes.entity) do
            local kinds = type(filters[1].type) == 'table' and filters[1].type or {filters[1].type}
            for _, t in ipairs(kinds) do if p.type == t then out[name] = p end end
        end
        return out
    end
    -- the map's own things, named as no list would know them
    local function proto(name, kind, layers, extra)
        local p = {name=name, type=kind, collision_mask={layers=layers}}
        for k, v in pairs(extra or {}) do p[k] = v end
        prototypes.entity[name] = p
    end
    proto('oak-of-legend', 'tree', {object=true, player=true},
          {mineable_properties={minable=true, mining_time=0.5, products={{type='item', name='wood', amount=4}}}})
    proto('glowing-boulder', 'simple-entity', {object=true}, {mineable_properties={minable=true, mining_time=1}})
    proto('old-ruin-wall', 'simple-entity-with-owner', {object=true}, {mineable_properties={minable=false}})
    proto('ridge', 'cliff', {object=true, player=true}, {mineable_properties={minable=false},
          cliff_explosive_prototype='boom-charge'})
    proto('rich-ore', 'resource', {resource=true}, {mineable_properties={minable=true,
          products={{type='item', name='rich-ore', amount=1}}}})
    proto('gold-fish', 'fish', {player=true}, {mineable_properties={minable=true}})
    proto('moss', 'simple-entity', {doodad=true}, {mineable_properties={minable=true}})
    proto('dropped-stuff', 'item-entity', {item=true}, {mineable_properties={minable=true}})
    prototypes.item['boom-charge'] = {name='boom-charge', capsule_action={attack_parameters={range=10}}}
    world = {}
    function put(name, x, y)
        local e = {valid=true, name=name, type=prototypes.entity[name].type, prototype=prototypes.entity[name],
                   position={x=x, y=y}, force={name='neutral'}, minable=prototypes.entity[name].mineable_properties.minable,
                   bounding_box={left_top={x=x-0.4, y=y-0.4}, right_bottom={x=x+0.4, y=y+0.4}}}
        e.destroy = function() e.valid = false return true end
        world[#world+1] = e
        return e
    end
    enemies = 0
    surface.find_entities_filtered = function(f)
        local out = {}
        local a = f.area
        for _, e in ipairs(world) do
            local b = e.bounding_box
            if e.valid and (not f.force or e.force.name == f.force) and b.right_bottom.x > a[1][1]
                    and b.left_top.x < a[2][1] and b.right_bottom.y > a[1][2] and b.left_top.y < a[2][2] then
                out[#out+1] = e
            end
        end
        return out
    end
    surface.count_entities_filtered = function() return enemies end
    surface.find_non_colliding_position = function(_, p) return {x=p.x+1, y=p.y} end
    held, thrown = {}, {}
    inv = setmetatable({}, {__len=function() return held['boom-charge'] and held['boom-charge'] > 0 and 1 or 0 end,
        __index=function(_, i)
            if i == 1 and held['boom-charge'] and held['boom-charge'] > 0 then
                return {valid_for_read=true, name='boom-charge', count=held['boom-charge'], quality={name='normal'},
                        use_capsule=function(_, at)
                            held['boom-charge'] = held['boom-charge'] - 1
                            thrown[#thrown+1] = at
                            for _, e in ipairs(world) do
                                if e.type == 'cliff' and math.abs(e.position.x-at.x) < 1.5
                                        and math.abs(e.position.y-at.y) < 1.5 then e.valid = false end
                            end
                            return {}
                        end}
            end
        end})
    inv.get_item_count = function(q) return held[type(q) == 'table' and q.name or q] or 0 end
    inv.count_empty_stacks = function() return free_slots or 40 end
    inv.remove = function(q) local n = math.min(held[q.name] or 0, q.count or 1); held[q.name] = (held[q.name] or 0) - n; return n end
    inv.get_contents = function() return {} end
    npc = storage.npcs[1]
    npc.home, npc.design_jobs = {x=0, y=0}, {}
    npc.character = {valid=true, surface=surface, position={x=0, y=0}, force=npc.force,
                     prototype={crafting_categories={crafting=true}}, get_main_inventory=function() return inv end}
    Clearing = require('scripts.clearing')
    TeamWorld = require('scripts.team_world')
    -- One clearing plan, submitted as the companion sends it (local tiles round origin -32,-32).
    function clearing_plan(boxes, extra)
        npc.design = {status='waiting', request_id='1:20', origin={x=-32, y=-32}, purpose='choose'}
        local stages = {}
        for i, b in ipairs(boxes) do
            stages[i] = {id='clear-'..i, entity_ids={}, depends_on={}, manual_supplies={}, clear={box=b},
                         witnesses={{kind='area_clear'}}}
        end
        local c = {zone={id='east', box={32, 22, 112, 42}, priority=0.4, source='plan'}, budget=50, ticks=14400}
        for k, v in pairs(extra or {}) do c[k] = v end
        return DS.submit({request_id='1:20', plan={plan_id='clear-east', purpose='clear-area', source='clearing',
                          entities={}, clearing=c, stages=stages}})
    end
    -- The worker, played: a mine job takes the thing away (and walks there); anything else just completes.
    function run(max, each)
        local jobs = {}
        for i = 1, max or 200 do
            game.tick = game.tick + 1
            local job, meta = DS.next(npc, game.tick)
            if job then
                local id = 'j' .. i
                DS.bind(npc, id, meta)
                jobs[#jobs+1] = job
                if each then each(job, #jobs) end
                if job.kind == 'mine' then
                    game.tick = game.tick + 120              -- (a couple of seconds a tree, walking included)
                    npc.character.position = job.target.position
                    job.target.valid = false
                elseif job.kind == 'move' then
                    npc.character.position = job.position
                end
                DS.on_receipt(npc, {job_id=id, status='complete'}, game.tick)
            end
            if npc.design.status ~= 'building' then break end
        end
        return jobs
    end
"""


def game():
    import test_retire
    lua = test_retire.runtime()
    lua.execute(GAME)
    return lua


class FightTests(unittest.TestCase):
    """Never into a fight: a building with biters by its spot is not put down now (run 34, 2026-10-25: the engineer
    walked into a 60-strong wave to put up the turrets meant against it, and died)."""

    def test_a_building_fought_over_waits_and_the_next_is_built_and_all_fought_over_the_plan_waits(self):
        lua = game()
        lua.execute("""
            prototypes.entity['transport-belt'].tile_width, prototypes.entity['transport-belt'].tile_height = 1, 1
            held['transport-belt'] = 5
            local stage = {id='s', entity_ids={'a', 'b'}, supplies={}, witnesses={}}
            npc.design = {status='building', stage_index=1, plan={stages={stage}, entities={
                a={id='a', prototype='transport-belt', item='transport-belt', world={x=5.5, y=5.5}, direction=0},
                b={id='b', prototype='transport-belt', item='transport-belt', world={x=40.5, y=5.5}, direction=0}}}}
            biters_by = function(p) return p.x < 10 end
            DS.fought_over = function(_, p) return biters_by(p) end
            first = {DS.next(npc, 100)}""")
        job = lua.eval("first[1]")
        self.assertEqual((job.kind, job.position.x), ("build", 40.5))       # the other one meanwhile
        lua.execute("biters_by = function() return true end; again = {DS.next(npc, 200)}")
        self.assertIsNone(lua.eval("again[1]"))
        self.assertEqual(lua.eval("again[2]"), "fought_over")
        self.assertTrue(lua.eval("npc.phase").startswith("designed-start:waiting-for-calm"))

    def test_biters_by_a_spot_a_nest_beside_it_or_a_worm_that_reaches_it_make_it_fought_over(self):
        # (a nest 20 tiles off, or a worm out of its range, does not: a death world's wall went up never, 2026-10-27)
        lua = game()
        lua.execute("""
            foes = {}
            local function d(a, b) return math.sqrt((a.x-b.x)^2 + (a.y-b.y)^2) end
            local function near(f)
                local out = {}
                for _, e in ipairs(foes) do
                    if e.type == f.type and d(e.position, f.position) <= f.radius then out[#out + 1] = e end
                end
                return out
            end
            surface.count_entities_filtered = function(f) return #near(f) end
            surface.find_entities_filtered = function(f) return near(f) end
            function foe(kind, x, range)
                foes[#foes + 1] = {type=kind, position={x=x, y=0},
                    prototype={attack_parameters=range and {range=range} or nil}}
            end""")
        fought = lambda: lua.eval("DS.fought_over(npc, {x=0, y=0})")
        self.assertFalse(fought())
        lua.execute("foe('unit-spawner', 20)")
        self.assertFalse(fought())                                              # a nest 20 tiles off
        lua.execute("foe('turret', 40, 25)")
        self.assertFalse(fought())                                              # a worm that cannot reach it
        lua.execute("foe('turret', 28, 25)")
        self.assertTrue(fought())                                               # one that can (with a margin)
        lua.execute("foes = {}; foe('unit-spawner', 10)")
        self.assertTrue(fought())                                               # a nest right beside it
        lua.execute("foes = {}; foe('unit', 30)")
        self.assertTrue(fought())                                               # biters near

    def test_a_stage_with_only_fought_over_pieces_left_goes_back_to_be_planned_again_in_a_while(self):
        lua = game()
        lua.execute("""
            prototypes.entity['stone-wall'] = {name='stone-wall', type='wall', tile_width=1, tile_height=1}
            held['stone-wall'] = 5
            local stage = {id='s', entity_ids={'a'}, supplies={}, witnesses={}}
            npc.design = {status='building', stage_index=1, purpose='wall', plan={plan_id='w', stages={stage},
                order={'a'}, entities={a={id='a', prototype='stone-wall', item='stone-wall', world={x=5.5, y=5.5}}}}}
            npc.design_history = npc.design_history or {}
            DS.fought_over = function() return true end
            first = {DS.next(npc, 100)}
            told = npc.fight_wanted.tick
            later = {DS.next(npc, 100 + DS.FOUGHT_WAIT)}""")
        self.assertEqual(lua.eval("first[2]"), "fought_over")
        self.assertEqual(lua.eval("told"), 100)                               # the war is told at once
        self.assertEqual(lua.eval("later[2]"), "blocked")
        self.assertEqual(lua.eval("npc.design.reason"), "in_the_way:a:enemies")
        self.assertEqual(lua.eval("npc.obstacle_log[#npc.obstacle_log].rung"), "hand_back")
        self.assertEqual(lua.eval("npc.fight_wanted.position.x"), 5.5)


class GameSurveyTests(unittest.TestCase):
    def test_what_is_in_the_way_comes_from_the_games_own_data(self):
        lua = game()
        lua.execute("""
            put('oak-of-legend', 5, 5); put('glowing-boulder', 6, 5); put('old-ruin-wall', 7, 5); put('ridge', 8, 5)
            put('rich-ore', 9, 5); put('gold-fish', 10, 5); put('moss', 11, 5); put('dropped-stuff', 12, 5)
            survey, work = Clearing.survey(npc, {0, 0, 32, 32})""")
        survey = lua.eval("survey")
        self.assertEqual({k: survey[k] for k in ("mine", "blast", "later", "fixed", "outside")},
                         {"mine": 2, "blast": 0, "later": 1, "fixed": 1, "outside": 0})
        self.assertEqual(list(survey["explosives"].keys()), ["boom-charge"])     # the cliff's own charge
        self.assertEqual(sorted(w.entity.name for w in lua.eval("work").values()), ["glowing-boulder", "oak-of-legend"])
        # once the charge can be made, the cliff is work too
        lua.execute("""npc.force.recipes = {['boom-charge']={name='boom-charge', enabled=true, category='crafting',
                       products={{type='item', name='boom-charge', amount=1}}, ingredients={}}}""")
        self.assertEqual(lua.eval("select(1, Clearing.survey(npc, {0, 0, 32, 32})).blast"), 1)

    def test_what_lies_outside_its_own_ground_is_left(self):
        lua = game()
        lua.execute("""
            put('oak-of-legend', 5, 5); put('oak-of-legend', 45, 5)
            TeamWorld.may_build = function(_, box) if box[1][1] >= 40 then return true end return nil, 'other_plot' end
            survey, work = Clearing.survey(npc, {0, 0, 64, 32})""")
        self.assertEqual((lua.eval("survey.mine"), lua.eval("survey.outside")), (1, 1))
        self.assertEqual(lua.eval("work[1].entity.position.x"), 45)


class GroundItemTests(unittest.TestCase):
    def test_what_lies_on_the_ground_where_a_belt_goes_is_picked_up_first(self):
        # (run 30, 2026-10-25: bricks spilled from furnaces cleared out of the bus's way were taken onto its belts)
        lua = game()
        lua.execute("""
            prototypes.entity['transport-belt'].tile_width, prototypes.entity['transport-belt'].tile_height = 1, 1
            got = {}
            inv.insert = function(s) got[s.name] = (got[s.name] or 0) + s.count return s.count end
            spill = put('dropped-stuff', 5.5, 5.5)
            spill.stack = {valid_for_read=true, name='stone-brick', count=4, quality='normal'}
            elsewhere = put('dropped-stuff', 20.5, 5.5)
            elsewhere.stack = {valid_for_read=true, name='stone-brick', count=9, quality='normal'}
            job = DS.clear_footprint(npc, {prototype='transport-belt', world={x=5.5, y=5.5}, direction=0})""")
        self.assertFalse(lua.eval("spill.valid"))
        self.assertEqual(lua.eval("got['stone-brick']"), 4)
        self.assertTrue(lua.eval("elsewhere.valid"))                  # (only what is under it)


class GamePlanTests(unittest.TestCase):
    def test_a_plan_with_no_buildings_is_taken_only_when_it_clears_ground(self):
        lua = game()
        self.assertTrue(lua.eval("clearing_plan({{32, 22, 64, 42}}).accepted"))
        lua.execute("npc.design = {status='waiting', request_id='1:21', origin={x=-32, y=-32}}")
        self.assertEqual(lua.eval("DS.submit({request_id='1:21', plan={entities={}, stages={{id='a'}}}}).reason"),
                         "entity_count")
        self.assertEqual(lua.eval("clearing_plan({{32, 22, 400, 42}}).reason"), "clear_box:clear-1")   # too big
        lua.execute("npc.design = {status='waiting', request_id='1:22', origin={x=-32, y=-32}}")
        self.assertEqual(lua.eval("""DS.submit({request_id='1:22', plan={entities={}, clearing={},
            stages={{id='a', witnesses={{kind='area_clear'}}}}}}).reason"""), "witness_box:a")

    def test_it_clears_nearest_home_first_keeps_no_history_and_reports_the_zone(self):
        lua = game()
        lua.execute("""
            far = put('oak-of-legend', 20, 3); near = put('oak-of-legend', 2, 1); put('glowing-boulder', 4, 2)
            put('old-ruin-wall', 6, 6); put('ridge', 8, 8); put('moss', 3, 3); put('oak-of-legend', 40, 3)
            history = #npc.design_history
            assert(clearing_plan({{32, 22, 64, 42}, {64, 22, 96, 42}, {96, 22, 112, 42}}).accepted)
            jobs = run()""")
        jobs = list(lua.eval("jobs").values())
        self.assertTrue(all(j.kind == "mine" and j.mode == "clear" for j in jobs))
        self.assertEqual([(j.target.name, j.target.position.x) for j in jobs],
                         [("oak-of-legend", 2), ("glowing-boulder", 4), ("oak-of-legend", 20), ("oak-of-legend", 40)])
        self.assertEqual(lua.eval("npc.design.status"), "complete")
        self.assertEqual(lua.eval("#npc.design_history"), lua.eval("history"))      # cleared ground is no build
        self.assertEqual(lua.eval("npc.design.next_goal_tick - game.tick") <= lua.eval("Clearing.GOAL_DELAY"), True)
        report = lua.eval("Clearing.export(npc)")
        self.assertEqual(report.frame, "world")
        z = report.zones[1]
        self.assertEqual((z.id, z.source, z.cleared, z.left, z.stages), ("east", "plan", 4, 0, 3))
        self.assertEqual(list(z.box.values()), [0, -10, 80, 10])                    # world tiles
        first = z.boxes[1]
        self.assertEqual(list(first.box.values()), [0, -10, 32, 10])
        self.assertEqual((first.mine, first.later, first.fixed), (0, 1, 1))         # the ruin and the cliff stay

    def test_the_plans_budget_bounds_all_its_stages(self):
        lua = game()
        lua.execute("""
            for x = 1, 5 do put('oak-of-legend', x, 1) end
            put('oak-of-legend', 40, 1)
            assert(clearing_plan({{32, 22, 64, 42}, {64, 22, 96, 42}}, {budget=2}).accepted)
            jobs = run()""")
        self.assertEqual(len(lua.eval("jobs")), 2)
        report = lua.eval("Clearing.export(npc)").zones[1]
        self.assertEqual(len(report.boxes), 1)                    # the second slice was never surveyed: still to do
        self.assertEqual(report.boxes[1].mine, 3)
        self.assertEqual(lua.eval("npc.design.plan.clearing.over"), "budget")

    def test_anything_urgent_ends_the_clearing_at_once(self):
        lua = game()
        lua.execute("""
            for x = 1, 6 do put('oak-of-legend', x, 1) end
            assert(clearing_plan({{32, 22, 64, 42}, {64, 22, 96, 42}}).accepted)
            jobs = run(200, function(job, n) if n == 2 then npc.help_request = {kind='defend'} end end)""")
        # (it looks every few seconds: the job under way finishes, then it stops)
        self.assertLess(len(lua.eval("jobs")), 6)
        self.assertEqual(lua.eval("npc.design.status"), "complete")
        self.assertEqual(lua.eval("npc.design.plan.clearing.over"), "urgent:help")
        lua.execute("npc.help_request = nil; enemies = 1")
        self.assertTrue(lua.eval("clearing_plan({{32, 22, 64, 42}}).accepted"))
        self.assertEqual(len(lua.eval("run()")), 0)               # biters near: nothing is cleared there
        self.assertEqual(lua.eval("npc.design.plan.clearing.over"), "urgent:biters")

    def test_a_cliff_is_blasted_with_its_own_charge_once_it_has_one(self):
        lua = game()
        lua.execute("""
            cliff = put('ridge', 30, 0)
            npc.design_history = {}          -- (no chests or machines of its own to take from: it makes them)
            npc.force.recipes = {['boom-charge']={name='boom-charge', enabled=true, category='crafting', energy=1,
                products={{type='item', name='boom-charge', amount=1}}, ingredients={{type='item', name='stone', amount=2}}}}
            assert(clearing_plan({{32, 22, 64, 42}}).accepted)
            first = {DS.next(npc, 1)}""")
        job, meta = lua.eval("first[1]"), lua.eval("first[2]")
        self.assertEqual((job.kind, job.item, meta.how), ("mine", "stone", "acquire"))   # makes one, as any item
        lua.execute("held['boom-charge'] = 1; jobs = run()")
        jobs = list(lua.eval("jobs").values())
        self.assertEqual([j.kind for j in jobs], ["move"])                              # within throwing range
        self.assertEqual(len(lua.eval("thrown")), 1)
        self.assertFalse(lua.eval("cliff.valid"))
        self.assertEqual(lua.eval("held['boom-charge']"), 0)                            # spent
        self.assertEqual(lua.eval("Clearing.export(npc)").zones[1].cleared, 1)

    def test_an_area_clear_witness_with_a_box_of_its_own_checks_any_plan(self):
        lua = game()
        lua.execute("tree = put('oak-of-legend', 5, 5)")
        self.assertFalse(lua.eval("Clearing.witness(npc, {box={0, 0, 32, 32}})"))
        lua.execute("tree.valid = false")
        self.assertTrue(lua.eval("Clearing.witness(npc, {box={0, 0, 32, 32}})"))


def worker():
    from lupa import LuaRuntime
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().worker_source = (ROOT / "mod" / "scripts" / "worker.lua").read_text(encoding="utf-8")
    lua.globals().base_map_source = (ROOT / "mod" / "scripts" / "base_map.lua").read_text(encoding="utf-8")
    lua.execute("""
        defines = {direction={north=0}, inventory={}}
        function log() end
        local stubs = {['scripts.team_world']={active=function() return false end},
            ['scripts.navigation']={distance=function(a, b) return math.sqrt((a.x-b.x)^2+(a.y-b.y)^2) end,
                                    step=function() return 'arrived' end, stop=function() end, clear=function() end},
            ['scripts.behavior']={ensure=function() end}, ['scripts.train_jobs']={finish=function() end},
            ['scripts.rescue']={note=function() end}}
        function require(name) return stubs[name] or {} end
        stubs['scripts.base_map'] = assert(load(base_map_source, '@base_map'))()
        prototypes = {item={}}
        room = true
        inv = {get_item_count=function() return 0 end, can_insert=function() return room end}
        character = {valid=true, surface={}, position={x=0, y=0}, resource_reach_distance=2.7, reach_distance=10,
                     get_main_inventory=function() return inv end}
        npc = {id=1, character=character}
        W = assert(load(worker_source, '@worker'))()
        function thing(force, products, minable)
            return {valid=true, minable=minable ~= false, surface=character.surface, position={x=1, y=0},
                    force={name=force}, prototype={mineable_properties={minable=minable ~= false, mining_time=1,
                    products=products}}}
        end
        function job(target, tick)
            assert(W.enqueue(npc, {kind='mine', target=target, count=1, mode='clear'}))
            W.tick(npc, tick)
        end
    """)
    return lua


class WorkerClearTests(unittest.TestCase):
    def test_a_clear_job_is_done_once_its_target_is_gone_whatever_it_gave(self):
        lua = worker()
        lua.execute("rock = thing('neutral', nil); job(rock, 1)")              # gives nothing when mined
        self.assertEqual(lua.eval("character.mining_state.mining"), True)
        lua.execute("rock.valid = false; W.tick(npc, 2)")
        receipt = list(lua.eval("npc.receipts").values())[0]
        self.assertEqual(receipt.status, "complete")

    def test_it_mines_nothing_of_anyone_elses_and_nothing_it_cannot_carry(self):
        lua = worker()
        lua.execute("job(thing('player', nil), 1)")
        self.assertEqual(lua.eval("npc.failures[1].reason"), "forbidden_mining_target")
        lua.execute("room = false; job(thing('neutral', {{type='item', name='wood', amount=4}}), 2)")
        self.assertEqual(lua.eval("npc.failures[2].reason"), "inventory_full")
        lua.execute("room = true; job(thing('neutral', nil, false), 3)")
        self.assertEqual(lua.eval("npc.failures[3].reason"), "forbidden_mining_target")


class WorkerMiningTests(unittest.TestCase):
    def test_it_mines_what_gives_the_most_a_second_a_big_rock_over_the_ore_beside_it(self):
        # (the user, 2026-10-24: "a lot of stone in the early game by just breaking the stone rocks around the
        # starting area"): from the products and mining times the game gives, walk included, no rock names
        lua = worker()
        lua.execute("""
            character.prototype = {mining_speed=0.5}
            function at(x, kind, products, time)
                local e = thing('neutral', products)
                e.position, e.type, e.name = {x=x, y=0}, kind, kind .. x
                e.prototype.mineable_properties.mining_time = time
                return e
            end
            ore = at(1.5, 'resource', {{type='item', name='stone', amount=1}}, 1)
            rock = at(20, 'simple-entity', {{type='item', name='stone', amount_min=24, amount_max=50},
                                            {type='item', name='coal', amount_min=24, amount_max=50}}, 2)
            tree = at(2, 'tree', {{type='item', name='wood', amount=4}}, 0.55)
            found = {ore, rock, tree}
            character.surface.find_entities_filtered = function() return found end
            assert(W.enqueue(npc, {kind='mine', item='stone', count=30}))
            W.tick(npc, 1)""")
        self.assertEqual(lua.eval("npc.current_job.target.name"), "simple-entity20")
        lua.execute("""
            npc.current_job, npc.jobs = nil, {}
            assert(W.enqueue(npc, {kind='mine', item='stone', count=1}))   -- a stone or so: the ore beside it
            W.tick(npc, 2)""")
        self.assertEqual(lua.eval("npc.current_job.target.name"), "resource1.5")


if __name__ == "__main__":
    unittest.main()
