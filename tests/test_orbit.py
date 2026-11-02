"""Getting to orbit from a designed base: pack, platform, load, launch, in Factorio's Lua 5.2."""
import math
from pathlib import Path
import unittest

from lupa.lua52 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]


def runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)
    sources = lua.table()
    for name in ("orbit", "platform_launch", "platform_build", "space_feed", "ghosts"):
        sources["scripts." + name] = (ROOT / "mod" / "scripts" / (name + ".lua")).read_text(encoding="utf-8")
    lua.globals().sources = sources
    lua.execute("""
        package.preload['scripts.progression'] = function() return {} end
        package.preload['scripts.team_world'] = function() return {active = function() return false end} end
        for name, source in pairs(sources) do
            package.preload[name] = function() return assert(load(source))() end
        end
        storage = {}
        script = {active_mods = {['space-age'] = '2.0.77'}}
        defines = {rocket_silo_status = {rocket_ready = 7, building_rocket = 1},
                   inventory = {rocket_silo_rocket = 9, hub_main = 1, cargo_unit = 2},
                   direction = {north = 0}, build_check_type = {manual_ghost = 3},
                   cargo_destination = {space_platform = 4, station = 5}}
        Orbit = require('scripts.orbit')
        cargo = {count = 0, valid = true, room = 50}
        function cargo.get_item_count() return cargo.count end
        function cargo.get_insertable_count() return cargo.room end
        function cargo.is_empty() return cargo.count == 0 end
        force = {technologies = {['rocket-silo'] = {researched = true}}}
        silo = {valid = true, type = 'rocket-silo', rocket_parts = 50, rocket_silo_status = 7}
        silo.force = force
        function silo.get_inventory() return cargo end
        npc = {id = 1, name = 'Ada', force = force,
               design_history = {{order = {'s'}, entities = {s = {entity = silo}}}}}
        held = 0
        function count() return held end
        asked = nil
        function acquire(_, item, n) asked = item; return {kind = 'craft', recipe = item, count = n} end
    """)
    return lua


class OrbitTests(unittest.TestCase):
    def step(self, lua):
        out = lua.eval("{Orbit.step(npc, 100, acquire, count)}")
        job, meta = out[1], out[2]
        if isinstance(meta, str):
            return None, meta            # no job: why it waits
        return job and job.kind, meta and meta.step

    def test_a_ready_rocket_takes_the_engineer_to_orbit(self):
        lua = runtime()
        self.assertEqual(self.step(lua), ("craft", "pack"))
        self.assertEqual(lua.eval("asked"), "space-platform-starter-pack")
        lua.execute("held = 1")
        self.assertEqual(self.step(lua), ("request_platform", "request"))
        lua.execute("storage.platform_launch = {requests = {[1] = {platform = {valid = true}}}}")
        self.assertEqual(self.step(lua), ("transfer", "load"))
        lua.execute("cargo.count = 1; held = 0")
        self.assertEqual(self.step(lua), ("launch_platform", "launch"))
        lua.execute("storage.platform_launch.requests[1].status = 'native_launch_ordered'")
        self.assertEqual(self.step(lua), (None, None))
        lua.execute("storage.platform_launch.requests[1].platform.hub = {}")
        self.assertTrue(lua.eval("Orbit.reached(npc)"))

    def test_the_pack_is_made_while_the_last_parts_are_built(self):
        lua = runtime()
        lua.execute("silo.rocket_silo_status = 1; silo.rocket_parts = 20")
        self.assertEqual(self.step(lua), (None, None))        # far from ready: nothing to do yet
        lua.execute("silo.rocket_parts = 45")
        self.assertEqual(self.step(lua), ("craft", "pack"))   # nearly there: make the pack
        lua.execute("held = 1")
        self.assertEqual(self.step(lua), (None, "waiting_for_rocket"))

    def test_a_ready_rocket_goes_before_the_build_under_way_its_pack_not_made_ahead_then(self):
        # (R-8: on a running base it was always building, and a ready rocket waited the whole run)
        lua = runtime()
        building = lambda: lua.eval("{Orbit.step(npc, 100, acquire, count, true)}")
        self.assertEqual(building()[1].kind, "craft")                  # (ready: the way to space goes first)
        lua.execute("silo.rocket_silo_status = 1; silo.rocket_parts = 45")
        self.assertIsNone(building()[1])                               # (not ready yet: the build goes on)
        self.assertEqual(self.step(lua), ("craft", "pack"))            # (between plans: the pack made ahead)

    def test_no_silo_or_no_research_means_no_launch(self):
        lua = runtime()
        lua.execute("force.technologies['rocket-silo'].researched = false")
        self.assertEqual(self.step(lua), (None, None))
        lua.execute("force.technologies['rocket-silo'].researched = true; npc.design_history = {}")
        self.assertEqual(self.step(lua), (None, None))


# A platform in orbit: a surface of foundation tiles (`tiles[x][y]`), the ghosts laid on it, the hub's stock.
PLATFORM = """
    TILE = 'space-platform-foundation'
    METAL = 'metallic-asteroid-crushing'
    prototypes = {entity = {crusher = {}, ['asteroid-collector'] = {}}, item = {[TILE] = {weight = 20000, stack_size = 100}},
                  recipe = {[METAL] = {ingredients = {{name = 'metallic-asteroid-chunk'}},
                                       products = {{type = 'item', name = 'iron-ore'},
                                                   {type = 'item', name = 'metallic-asteroid-chunk'}}}}}
    force.recipes = {crusher = {enabled = true}, ['asteroid-collector'] = {enabled = true}, [METAL] = {enabled = true}}
    function prototypes.get_entity_filtered() return {lab = {lab_inputs = {'automation-science-pack', 'space-science-pack'}}} end
    -- (a small layout: the foundation round the hub, a crusher on it)
    L = {foundation = {{-8, -8}, {7, 7}}, entities = {{name = 'crusher', x = 6, y = 0, direction = 'north', recipe = METAL},
         {name = 'asteroid-collector', x = -6.5, y = -6.5, direction = 'north', filter = 'metallic-asteroid-chunk'}}}
    tiles, ghosts, built = {}, {}, {}
    for x = -5, 4 do tiles[x] = {} for y = -5, 4 do tiles[x][y] = true end end
    local function tile_at(x, y) return tiles[x] and tiles[x][y] end
    surface = {}
    function surface.get_tile(x, y)
        return {name = tile_at(x, y) and TILE or 'empty-space',
                has_tile_ghost = function() return ghosts[x .. ',' .. y] ~= nil end}
    end
    function surface.create_entity(e)
        local x, y = math.floor(e.position[1]), math.floor(e.position[2])
        local items = {{name = e.inner_name, count = 1}}
        ghosts[x .. ',' .. y] = {name = e.name, ghost_name = e.inner_name, ghost_prototype = {items_to_place_this = items}}
        return ghosts[x .. ',' .. y]
    end
    function surface.find_entities_filtered(f)
        local out = {}
        if f.type then for _, g in pairs(ghosts) do out[#out + 1] = g end
        else
            local names = type(f.name) == 'table' and f.name or {f.name}
            for _, e in ipairs(built) do
                for _, n in ipairs(names) do if e.name == n then out[#out + 1] = e end end
            end
        end
        return out
    end
    function surface.can_place_entity(e)   -- (on foundation only)
        return tile_at(math.floor(e.position[1]), math.floor(e.position[2])) ~= nil
    end
    stock = {}
    hub = {valid = true, position = {x = 0, y = 0}, get_inventory = function()
        return {get_item_count = function(id) return stock[id.name] or 0 end} end}
    platform = {valid = true, hub = hub, surface = surface}
    storage.platform_launch = {requests = {[1] = {platform = platform, status = 'native_launch_ordered'}}}
    Build = require('scripts.platform_build')
    held_of = {}
    function count(_, item) return held_of[item] or 0 end
    function acquire(_, item, n) asked = item; return {kind = 'craft', recipe = item, count = n} end
"""


def build_all(lua):
    """The platform builds every ghost (the test stands in for its own construction)."""
    lua.execute("""
        for key, g in pairs(ghosts) do
            local x, y = key:match('(-?%d+),(-?%d+)')
            x, y = tonumber(x), tonumber(y)
            if g.ghost_name == TILE then tiles[x] = tiles[x] or {}; tiles[x][y] = true
            else
                local e = {name = g.ghost_name}
                function e.get_recipe() return e.recipe and {name = e.recipe} end
                function e.set_recipe(r) e.recipe = r end
                function e.get_filter() return e.filter end
                function e.set_filter(_, f) e.filter = f end
                built[#built + 1] = e
            end
            ghosts[key] = nil
        end""")


class PlatformBuildTests(unittest.TestCase):
    def test_the_layout_is_laid_as_ghosts_its_pieces_once_foundation_is_under_them(self):
        lua = runtime()
        lua.execute(PLATFORM)
        self.assertEqual(lua.eval("Build.place(npc, platform, 1000, L)"), 16 * 16 - 100)   # (the crusher's spot: space yet)
        self.assertEqual(lua.eval("Build.place(npc, platform, 1100, L)"), 0)   # (not looked again so soon)
        build_all(lua)
        self.assertFalse(lua.eval("Build.done(npc, platform, L)"))
        self.assertEqual(lua.eval("Build.place(npc, platform, 2000, L)"), 2)   # (crusher, collector: on foundation now)
        build_all(lua)
        self.assertFalse(lua.eval("Build.done(npc, platform, L)"))       # (built, its recipe and filter not set yet)
        self.assertEqual(lua.eval("Build.place(npc, platform, 3000, L)"), 2)
        set_up = {e.name: (e.recipe, e.filter) for e in lua.eval("built").values()}
        self.assertEqual(set_up, {"crusher": ("metallic-asteroid-crushing", None),
                                  "asteroid-collector": (None, "metallic-asteroid-chunk")})
        self.assertTrue(lua.eval("Build.done(npc, platform, L)"))

    def test_a_recipe_not_unlocked_is_left_unset_and_does_not_hold_the_layout(self):
        lua = runtime()
        lua.execute(PLATFORM + "force.recipes[METAL].enabled = false")
        lua.execute("Build.place(npc, platform, 1000, L)")
        build_all(lua)
        lua.execute("Build.place(npc, platform, 2000, L)")
        build_all(lua)
        self.assertEqual(lua.eval("Build.place(npc, platform, 3000, L)"), 1)   # (the collector's filter only)
        self.assertTrue(lua.eval("Build.done(npc, platform, L)"))

    def test_the_default_layouts_pieces_do_not_overlap_the_hub_or_each_other_and_stand_on_its_foundation(self):
        lua = runtime()
        lua.execute(PLATFORM)
        size = {"asteroid-collector": (3, 3), "crusher": (2, 3), "inserter": (1, 1), "medium-electric-pole": (1, 1),
                "solar-panel": (3, 3), "electric-furnace": (3, 3), "assembling-machine-3": (3, 3),
                "chemical-plant": (3, 3), "pipe": (1, 1), "thruster": (4, 5), "gun-turret": (2, 2)}
        taken = {(x, y) for x in range(-4, 4) for y in range(-4, 4)}             # (the hub)
        rects = [[tuple(c.values()) for c in r.values()] for r in lua.eval("Build.LAYOUT.foundation").values()]
        on = lambda x, y: any(a[0] <= x <= b[0] and a[1] <= y <= b[1] for a, b in rects)
        y0 = min(a[1] for a, b in rects)
        for p in lua.eval("Build.LAYOUT.entities").values():
            name = p.name if isinstance(p.name, str) else p.name[1]
            w, h = size[name]
            tiles = {(math.floor(p.x - w / 2) + i, math.floor(p.y - h / 2) + j) for i in range(w) for j in range(h)}
            self.assertFalse(tiles & taken, name)
            taken |= tiles
            back = tiles if name != "asteroid-collector" else {t for t in tiles if t[1] > p.y - 1}
            self.assertTrue(all(on(x, y) for x, y in back), name)
            if name == "asteroid-collector":   # (its arm over empty space)
                self.assertTrue(all(not on(x, y) for x, y in tiles - back), name)
            if name == "thruster":             # (open space behind it: its exhaust)
                self.assertTrue(all(not on(x, y) for x in range(math.floor(p.x) - 2, math.floor(p.x) + 2)
                                    for y in range(math.floor(p.y + 3), math.floor(p.y + 90))), name)

    def test_an_inserter_with_a_filter_has_its_filters_turned_on(self):
        lua = runtime()
        lua.execute(PLATFORM + """
            prototypes.entity.inserter = {}; force.recipes.inserter = {enabled = true}
            L3 = {foundation = {{-5, -5}, {4, 4}}, entities = {{name = 'inserter', x = 0.5, y = 0.5, filter = 'iron-ore'}}}
            Build.place(npc, platform, 1000, L3)""")
        build_all(lua)
        lua.execute("built[1].type = 'inserter'; Build.place(npc, platform, 2000, L3)")
        self.assertEqual((lua.eval("built[1].filter.name"), lua.eval("built[1].use_filters")), ("iron-ore", True))
        self.assertTrue(lua.eval("Build.done(npc, platform, L3)"))

    def test_the_thrust_pipes_join_each_melter_to_its_plants_first_input_and_each_plant_to_its_thruster_input(self):
        # (fluid connections from the game's data: a chemical plant facing north takes fluid in at (-1,-1) first and
        # (1,-1), north, gives out at (-1,1) and (1,1), south; the thruster's fuel at (-1.5,-2) west and (1.5,0) east,
        # its oxidizer at (1.5,-2) east and (-1.5,0) west; turned clockwise for east)
        lua = runtime()
        lua.execute(PLATFORM)
        pieces = [p for p in lua.eval("Build.LAYOUT.entities").values()]
        pipes = {(math.floor(p.x), math.floor(p.y)) for p in pieces if p.name == "pipe"}
        turn = {"north": lambda x, y: (x, y), "east": lambda x, y: (-y, x), "south": lambda x, y: (-x, -y),
                "west": lambda x, y: (y, -x)}
        step = {"north": (0, -1), "east": (1, 0), "south": (0, 1), "west": (-1, 0)}
        def tile(p, x, y, facing):
            r = turn[p.direction or "north"]
            (dx, dy), (fx, fy) = r(x, y), r(*step[facing])
            return math.floor(p.x + dx + fx), math.floor(p.y + dy + fy)
        def joined(a, b):          # (a and b in one pipe network)
            seen, todo = set(), [a]
            while todo:
                t = todo.pop()
                if t in seen or t not in pipes:
                    continue
                seen.add(t)
                todo += [(t[0] + 1, t[1]), (t[0] - 1, t[1]), (t[0], t[1] + 1), (t[0], t[1] - 1)]
            return b in seen
        plants = {p.recipe: p for p in pieces if p.name == "chemical-plant" and p.recipe != "ice-melting"}
        melters = [p for p in pieces if p.recipe == "ice-melting"]
        thruster = next(p for p in pieces if p.name == "thruster")
        fuel_in = tile(thruster, -1.5, -2, "west")
        oxidizer_in = tile(thruster, 1.5, -2, "east")
        for plant, into in ((plants["thruster-fuel"], fuel_in), (plants["thruster-oxidizer"], oxidizer_in)):
            first = tile(plant, -1, -1, "north")
            self.assertTrue(any(joined(tile(m, x, 1, "south"), first) for m in melters for x in (-1, 1)), plant.recipe)
            outs = [tile(plant, x, 1, "south") for x in (-1, 1)]
            self.assertTrue(all(joined(o, into) for o in outs), plant.recipe)
        self.assertFalse(joined(fuel_in, oxidizer_in))                          # (never mixed)
        for m in melters:
            for x in (-1, 1):
                self.assertFalse(joined(tile(m, x, 1, "south"), fuel_in))     # (water kept out of the thruster)

    def test_a_piece_named_by_several_machines_takes_the_best_its_force_makes(self):
        lua = runtime()
        lua.execute(PLATFORM + """
            prototypes.entity['assembling-machine-1'] = {}; prototypes.entity['assembling-machine-2'] = {}
            force.recipes['assembling-machine-1'] = {enabled = true}
            force.recipes['assembling-machine-2'] = {enabled = false}
            tiles[6] = {[0] = true}
            L2 = {foundation = {{-5, -5}, {4, 4}}, entities = {{name = {'assembling-machine-2', 'assembling-machine-1'},
                  x = 6.5, y = 0.5}}}
            Build.place(npc, platform, 1000, L2)""")
        self.assertEqual([g.ghost_name for g in lua.eval("ghosts").values()], ["assembling-machine-1"])

    def test_what_its_machines_made_is_read_from_the_hub_not_the_chunks_they_give_back(self):
        lua = runtime()
        lua.execute(PLATFORM + "stock['metallic-asteroid-chunk'] = 9")
        made = lua.eval("Build.made(platform, L)")
        self.assertEqual(dict(made.items()), {"iron-ore": 0})
        lua.execute("stock['iron-ore'] = 40")
        self.assertEqual(lua.eval("Build.made(platform, L)")["iron-ore"], 40)

    def test_the_default_layout_mines_each_kind_of_chunk_and_powers_it(self):
        lua = runtime()
        lua.execute(PLATFORM)
        names = [p.name for p in lua.eval("Build.LAYOUT.entities").values()]
        for name in ("asteroid-collector", "crusher", "inserter", "solar-panel", "medium-electric-pole"):
            self.assertIn(name, names)
        recipes = {p.recipe for p in lua.eval("Build.LAYOUT.entities").values() if p.recipe}
        self.assertIn("space-science-pack", recipes)
        recipes = {r for r in recipes if r.endswith("-asteroid-crushing")}
        filters = {p.filter for p in lua.eval("Build.LAYOUT.entities").values() if p.filter and p.name == "asteroid-collector"}
        self.assertEqual(len(recipes), 3)
        self.assertEqual({f.split("-")[0] for f in filters}, {r.split("-")[0] for r in recipes})   # (each line its own kind)

    def test_a_pack_counts_as_got_from_space_once_a_machine_there_stands_set_to_make_it(self):
        lua = runtime()
        lua.execute(PLATFORM + """
            storage.platform_launch.requests[1].platform = platform
            Launch = require('scripts.platform_launch')
            npc.platform_placed = nil""")
        self.assertFalse(lua.eval("Orbit.makes(npc, 'iron-ore')"))
        lua.execute("Build.place(npc, platform, 1000, L)")
        build_all(lua)
        lua.execute("Build.place(npc, platform, 2000, L)")
        build_all(lua)
        self.assertFalse(lua.eval("Build.makes(npc, platform, 'iron-ore', L)"))   # (built, its recipe not set yet)
        lua.execute("Build.place(npc, platform, 3000, L)")
        self.assertTrue(lua.eval("Build.makes(npc, platform, 'iron-ore', L)"))
        self.assertFalse(lua.eval("Build.makes(npc, platform, 'carbon', L)"))
        self.assertFalse(lua.eval("Orbit.fed(npc)"))                    # (no way down to the labs yet)
        lua.execute("npc.space_feed = {pad = {valid = true}, chest = {valid = true}}")
        self.assertTrue(lua.eval("Orbit.fed(npc)"))
        lua.execute("npc.space_feed.chest.valid = false")
        self.assertFalse(lua.eval("Orbit.fed(npc)"))

    def test_what_the_hub_holds_or_is_on_its_way_is_not_wanted_again_foundation_first(self):
        lua = runtime()
        lua.execute(PLATFORM + """
            Build.place(npc, platform, 1000, L)
            ghosts.crusher = {ghost_name = 'crusher', ghost_prototype = {items_to_place_this = {{name = 'crusher', count = 1}}}}
            stock[TILE] = 10; stock['asteroid-collector'] = 1""")
        needs = lua.eval("Build.needs(npc, platform, {[TILE] = 40})")
        self.assertEqual((needs[1].item, needs[1].count), ("space-platform-foundation", 156 - 50))
        self.assertEqual(needs[2].item, "crusher")
        lua.execute("stock.crusher = 1; stock[TILE] = 156")
        self.assertEqual(len(lua.eval("Build.needs(npc, platform, {})")), 0)

    def test_in_orbit_a_ready_rocket_is_loaded_with_what_the_platform_lacks_then_launched(self):
        lua = runtime()
        lua.execute(PLATFORM)
        supply = lambda: lua.eval("{Orbit.step(npc, 1000, acquire, count)}")
        job = supply()[1]
        self.assertEqual((job.kind, lua.eval("asked")), ("craft", "space-platform-foundation"))
        self.assertEqual(job.count, 50)                              # (as many as the rocket lifts, not all it lacks)
        lua.execute("held_of[TILE] = 80")
        job = supply()[1]
        self.assertEqual((job.kind, job.item, job.count), ("transfer", "space-platform-foundation", 50))
        lua.execute("cargo.count = 50; cargo.room = 0")
        self.assertEqual(supply()[1].kind, "launch_platform")
        lua.execute("silo.rocket_silo_status = 1; silo.rocket_parts = 10; cargo.count = 0")
        self.assertIsNone(supply()[1])                               # (the next rocket far off: nothing yet)

    def test_a_cargo_launch_goes_to_the_built_platform_and_counts_on_its_way(self):
        lua = runtime()
        lua.execute(PLATFORM + """
            Launch = require('scripts.platform_launch')
            npc.character = {valid = true, surface = 'nauvis', can_reach_entity = function() return true end}
            silo.surface = 'nauvis'
            pod = {valid = true, get_inventory = function() return {get_contents = function()
                return {{name = TILE, count = 50}} end} end}
            silo.rocket = {valid = true, force = force, attached_cargo_pod = pod}
            function silo.launch_rocket(d) sent = d; return true end
            cargo.count = 50""")
        self.assertTrue(lua.eval("Launch.launch(npc, silo)"))
        self.assertTrue(lua.eval("sent.type == 5 and sent.station == hub"))   # (to its hub: not as a starter pack)
        self.assertEqual(lua.eval("Launch.in_flight(npc)")["space-platform-foundation"], 50)
        lua.execute("pod.valid = false")                               # (landed in the hub)
        self.assertIsNone(lua.eval("Launch.in_flight(npc)")["space-platform-foundation"])


# The ground by a lab: what stands (`placed`), what blocks a spot (`blocked`), one pole by the lab.
GROUND = """
    Feed = require('scripts.space_feed')
    prototypes = prototypes or {}; prototypes.entity = prototypes.entity or {}
    prototypes.entity.inserter = {}; prototypes.entity['long-handed-inserter'] = {}
    defines.direction = {north = 0, east = 4, south = 8, west = 12}
    defines.build_check_type = {manual = 0, manual_ghost = 3}
    placed, blocked = {}, {}
    ground = {}
    lab = {valid = true, type = 'lab', position = {x = 0.5, y = 0.5}, surface = ground}
    pole = {position = {x = 0.5, y = 3.5}, prototype = {get_supply_area_distance = function() return 3.5 end}}
    local function key(p) return p.x .. ',' .. p.y end
    function ground.can_place_entity(e) return not blocked[e.name .. '@' .. key(e.position)] and not placed[key(e.position)] end
    function ground.find_entities_filtered(f)
        if f.type == 'electric-pole' then return {pole} end
        if f.type == 'lab' then return {lab} end
        if f.type == 'inserter' then return {} end
        local e = placed[key(f.position)]
        return (e and e.name == f.name) and {e} or {}
    end
    npc.character = {valid = true, surface = ground, position = {x = 0, y = 0}}
    npc.home = {x = 0, y = 0}
    held_of = {}
    function count(_, item) return held_of[item] or 0 end
    function acquire(_, item, n) asked = item; return {kind = 'craft', recipe = item, count = n} end
    asks = {}
    pad_sections = {sections_count = 0}
    function pad_sections.add_section()
        local s = {filters_count = 0, slots = {}}
        function s.get_slot(i) return s.slots[i] end
        function s.set_slot(i, f) s.slots[i] = f; s.filters_count = math.max(s.filters_count, i) end
        pad_sections.s = s; pad_sections.sections_count = 1
        return s
    end
    function pad_sections.get_section(i) return pad_sections.s end
    function stand(job)   -- (the worker builds what the job asks)
        local e = {valid = true, name = job.entity_name, position = job.position, direction = job.direction}
        if e.name == 'cargo-landing-pad' then e.get_logistic_sections = function() return pad_sections end; the_pad = e
        else e.pickup_target = the_pad; e.drop_target = lab end
        placed[key(job.position)] = e
    end
"""


class SpaceFeedTests(unittest.TestCase):
    def test_the_pad_goes_against_an_inserter_by_a_labs_side_the_inserter_facing_it(self):
        lua = runtime()
        lua.execute(GROUND)
        site = lua.eval("Feed.site(npc, lab)")
        self.assertEqual((site.inserter.x, site.inserter.y, site.direction), (2.5, 0.5, "east"))
        # (a plain inserter would put the pad's centre at x 7, off its 2x2 grid: a long-handed one reaches it at 8)
        self.assertEqual((site.picker, site.pad.x, site.pad.y), ("long-handed-inserter", 8, 0))
        lua.execute("lab.position = {x = 1.5, y = 0.5}")
        site = lua.eval("Feed.site(npc, lab)")
        self.assertEqual((site.picker, site.pad.x, site.pad.y), ("inserter", 8, 0))
        lua.execute("blocked['cargo-landing-pad@8,0'] = true")
        self.assertEqual(lua.eval("Feed.site(npc, lab).pad.y"), 2)    # (shifted along the side, on the grid)
        lua.execute("pole.position = {x = 50, y = 50}")
        self.assertIsNone(lua.eval("Feed.site(npc, lab)"))           # (no power for the inserter)

    def test_it_gathers_and_builds_the_pad_then_the_inserter_then_the_pad_asks_for_the_packs(self):
        lua = runtime()
        lua.execute(GROUND)
        step = lambda: lua.eval("{Feed.step(npc, acquire, count, {'space-science-pack'})}")
        self.assertEqual((step()[1].kind, lua.eval("asked")), ("craft", "cargo-landing-pad"))
        lua.execute("held_of['cargo-landing-pad'] = 1")
        job = step()[1]
        self.assertEqual((job.kind, job.entity_name), ("build", "cargo-landing-pad"))
        lua.execute("stand(({Feed.step(npc, acquire, count, {'space-science-pack'})})[1]); held_of['long-handed-inserter'] = 1")
        job = step()[1]
        self.assertEqual((job.kind, job.entity_name, job.direction), ("build", "long-handed-inserter", 4))
        lua.execute("stand(({Feed.step(npc, acquire, count, {'space-science-pack'})})[1])")
        self.assertIsNone(step()[1])
        self.assertTrue(lua.eval("Orbit.fed(npc)"))
        slot = lua.eval("pad_sections.s.slots[1]")
        self.assertEqual((slot.value.name, slot.min), ("space-science-pack", 200))
        lua.execute("Feed.ask(npc.space_feed.pad, 'space-science-pack', 200)")
        self.assertEqual(lua.eval("pad_sections.s.filters_count"), 1)  # (asked once)


    def test_with_no_room_beside_a_lab_the_pad_goes_on_free_ground_for_a_belt_and_counts_once_packs_reach_a_lab(self):
        lua = runtime()
        lua.execute(GROUND + """
            pole.position = {x = 50, y = 50}   -- (no power by the lab: no inserter of its own there)
            held_of['cargo-landing-pad'] = 1
            lab_holds = 0
            lab.get_inventory = function() return {get_item_count = function() return lab_holds end} end""")
        step = lambda: lua.eval("{Feed.step(npc, acquire, count, {'space-science-pack'})}")
        job = step()[1]
        self.assertEqual(job.entity_name, "cargo-landing-pad")
        self.assertEqual((job.position.x % 2, job.position.y % 2), (0, 0))    # (on its grid)
        lua.execute("stand(({Feed.step(npc, acquire, count, {'space-science-pack'})})[1])")
        self.assertEqual(step()[2], "space_feed:belt_awaited")
        self.assertFalse(lua.eval("Orbit.fed(npc)"))
        out = lua.eval("Feed.export(npc, {x = 1, y = 1})")
        self.assertEqual((out[1].item, out[1].x, out[1].y), ("space-science-pack", job.position.x - 1, job.position.y - 1))
        lua.execute("lab_holds = 3")
        self.assertEqual(step()[2], "space_feed:belt_awaited")         # (a pack in a lab, but nothing unloads the pad)
        lua.execute("""
            -- (an inserter at the pad onto a belt of two pieces, an inserter from the second into the lab)
            b2 = {valid = true, type = 'transport-belt', unit_number = 2, position = {x = 3, y = 3},
                  belt_neighbours = {outputs = {}}}
            b1 = {valid = true, type = 'transport-belt', unit_number = 1, position = {x = 4, y = 3},
                  belt_neighbours = {outputs = {b2}}}
            unloading = {valid = true, pickup_target = the_pad, drop_target = nil}
            feeding = {valid = true, pickup_target = b2, drop_target = lab}
            local find = ground.find_entities_filtered
            function ground.find_entities_filtered(f)
                if f.type == 'inserter' and f.area then return {unloading} end
                if f.type == 'inserter' then return f.position == b2.position and {feeding} or {} end
                return find(f)
            end""")
        self.assertEqual(step()[2], "space_feed:belt_awaited")         # (it unloads the pad, but onto nothing)
        lua.execute("unloading.drop_target = b1")
        self.assertIsNone(step()[1])
        self.assertTrue(lua.eval("Orbit.fed(npc)"))
        lua.execute("feeding.valid = false")
        self.assertFalse(lua.eval("Orbit.fed(npc)"))                    # (the way into the lab gone: no longer fed)

    def test_in_orbit_a_platform_making_science_has_its_way_down_built_and_ore_does_not(self):
        lua = runtime()
        lua.execute(PLATFORM + GROUND + """
            silo.rocket_silo_status = 1; silo.rocket_parts = 0
            npc.platform_placed = 0   -- (nothing more laid this tick)
            Build.made = function() return made_now end
            Build.makes = function(_, _, item) return true end
            made_now = {['iron-ore'] = 50}""")
        self.assertIsNone(lua.eval("({Orbit.step(npc, 100, acquire, count)})[1]"))   # (ore is no lab's)
        lua.execute("made_now = {['space-science-pack'] = 5}")
        job = lua.eval("({Orbit.step(npc, 100, acquire, count, true)})[1]")    # (packs waiting: before the build)
        self.assertEqual((job.kind, lua.eval("asked")), ("craft", "cargo-landing-pad"))
        lua.execute("made_now = {['space-science-pack'] = 0}")
        self.assertIsNone(lua.eval("({Orbit.step(npc, 100, acquire, count, true)})[1]"))   # (none yet: the build first)


if __name__ == "__main__":
    unittest.main()
