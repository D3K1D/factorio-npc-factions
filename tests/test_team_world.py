"""The team world (team_world.lua): plots, nest clearing, starter ore, in Factorio's Lua 5.2 with the game stubbed."""
from pathlib import Path
import unittest

from lupa.lua52 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]


def runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().source = (ROOT / "mod" / "scripts" / "team_world.lua").read_text(encoding="utf-8")
    lua.execute("""
        scenario = 'team'
        settings = {global={['npc-factions-scenario']={}}}
        setmetatable(settings.global['npc-factions-scenario'], {__index=function() return scenario end})
        function log() end
        storage = {}
        -- the map: lakes are discs, ore and enemies are lists; everything generated unless a test says not
        lakes, resources, enemies, made, tiles_set, requested = {}, {}, {}, {}, {}, {}
        generated = true
        local function near(p, q, r) return (p.x - q.x)^2 + (p.y - q.y)^2 <= r * r end
        local function in_area(p, a) return p.x >= a[1][1] and p.x < a[2][1] and p.y >= a[1][2] and p.y < a[2][2] end
        function enemy(x, y) local e = {valid=true, position={x=x, y=y}}
            e.destroy = function() e.valid = false end
            enemies[#enemies + 1] = e; return e end
        surface = {index=1}
        built = {}   -- what players built: {type=, position=}
        function surface.find_entities_filtered(f)
            local out = {}
            if f.force == 'enemy' then
                for _, e in ipairs(enemies) do if e.valid and in_area(e.position, f.area) then out[#out + 1] = e end end
            elseif f.force == game.forces.player then
                for _, e in ipairs(built) do
                    if in_area(e.position, f.area) then out[#out + 1] = {valid=true, type=e.type, position=e.position} end
                end
            end
            return out
        end
        function surface.count_entities_filtered(f)
            local n = 0
            for _, r in ipairs(resources) do
                if (f.name == nil or f.name == r.name) and near(r, f.position, f.radius) then n = n + 1 end
            end
            return n
        end
        function surface.count_tiles_filtered(f)
            local n = 0
            for _, l in ipairs(lakes) do
                if f.area then
                    if in_area(l, f.area) then n = n + 1000 end
                elseif near(l, f.position, f.radius + l.r) then n = n + 100 end
            end
            return n
        end
        function surface.find_tiles_filtered(f)   -- (lake tiles under a disc)
            local out = {}
            for _, l in ipairs(lakes) do
                if near(l, f.position, f.radius + l.r) then out[#out + 1] = {position={x=l.x, y=l.y}} end
            end
            return out
        end
        function surface.find_non_colliding_position(name, p) return {x=p.x, y=p.y} end
        function surface.is_chunk_generated() return generated end
        function surface.request_to_generate_chunks(p, r) requested[#requested + 1] = r end
        function surface.create_entity(e)
            made[#made + 1] = e
            resources[#resources + 1] = {name=e.name, x=e.position[1], y=e.position[2]}
            return {valid=true}
        end
        function surface.set_tiles(t) for _, tile in ipairs(t) do tiles_set[#tiles_set + 1] = tile end end
        spawn = {x=10, y=-7}
        game = {tick=0, surfaces={surface},
                forces={player={get_spawn_position=function() return spawn end}}}
        T = assert(load(source))()
        function run_until_ready()
            for tick = 60, 600, 60 do T.tick(tick); if T.state().status == 'ready' then return tick end end
        end
    """)
    return lua


def box(lua, name):
    return tuple(lua.eval(f"T.plot('{name}').box[{i}]") for i in range(1, 5))


class LayoutTests(unittest.TestCase):
    def test_a_three_by_three_grid_of_384_tile_plots_with_the_players_sharing_the_centre(self):
        lua = runtime()
        self.assertEqual(lua.eval("#T.state().plots"), 10)
        self.assertEqual(lua.eval("T.npc_plots()"), 7)
        self.assertEqual(tuple(lua.eval(f"T.state().area[{i}]") for i in range(1, 5)), (-576, -576, 576, 576))
        self.assertEqual(box(lua, "npc-1"), (-576, -576, -192, -192))       # (spawn 10,-7 snapped to 0,0)
        self.assertEqual(box(lua, "player-1"), (-192, -192, 0, 192))
        self.assertEqual(box(lua, "player-2"), (0, -192, 192, 192))
        self.assertEqual(box(lua, "yard"), (192, 192, 576, 576))
        self.assertEqual(list(lua.eval("T.plot('npc-1').outer").values()), ["north", "west"])
        self.assertEqual(list(lua.eval("T.plot('npc-5').outer").values()), ["east"])
        self.assertEqual(lua.eval("#T.plot('player-1').outer"), 0)

    def test_off_in_the_factions_scenario(self):
        lua = runtime()
        lua.execute("scenario = 'factions'; T.tick(60)")
        self.assertFalse(lua.eval("T.active()"))
        self.assertEqual(lua.eval("#requested"), 0)


class BiterTests(unittest.TestCase):
    def test_nests_generated_inside_the_area_are_removed_and_none_outside_even_right_at_its_edge(self):
        lua = runtime()
        lua.execute("""
            inside, margin, outside = enemy(560, 10), enemy(600, 10), enemy(620, 10)
            T.on_chunk_generated({surface=surface, area={left_top={x=544, y=0}, right_bottom={x=576, y=32}}})
            T.on_chunk_generated({surface=surface, area={left_top={x=576, y=0}, right_bottom={x=608, y=32}}})
            T.on_chunk_generated({surface=surface, area={left_top={x=608, y=0}, right_bottom={x=640, y=32}}})""")
        # (no margin: a death world's nests may stand right at the team area's edge)
        self.assertEqual([lua.eval(f"{n}.valid") for n in ("inside", "margin", "outside")], [False, True, True])
        self.assertEqual(lua.eval("T.state().cleared"), 1)

    def test_biters_never_settle_inside_the_team_area(self):
        lua = runtime()
        lua.execute("""
            local function base(x, y) local e = {valid=true, surface=surface, position={x=x, y=y}}
                e.destroy = function() e.valid = false end; return e end
            settled, beyond = base(-300, 400), base(900, 0)
            T.on_biter_base_built({entity=settled}); T.on_biter_base_built({entity=beyond})""")
        self.assertFalse(lua.eval("settled.valid"))
        self.assertTrue(lua.eval("beyond.valid"))
        self.assertEqual(lua.eval("T.state().expansions_stopped"), 1)


class BuildRuleTests(unittest.TestCase):
    def test_an_engineer_builds_in_its_own_plot_or_beyond_the_team_area_never_in_another_plot(self):
        lua = runtime()
        lua.execute("""
            ada = {id=1, character={valid=true, surface=surface}}
            function may(x0, y0, x1, y1) local r = {T.may_build(ada, {{x0, y0}, {x1, y1}})} return r[1], r[2] end""")
        self.assertTrue(lua.eval("may(-400, -400, -397, -397)"))                 # npc-1's own plot
        self.assertEqual(lua.eval("select(2, may(-100, 0, -97, 3))"), "other_plot")      # the players'
        self.assertEqual(lua.eval("select(2, may(0, -400, 3, -397))"), "other_plot")     # npc-2's
        self.assertEqual(lua.eval("select(2, may(-194, -300, -190, -297))"), "other_plot")  # across an edge
        self.assertTrue(lua.eval("may(-700, -700, -695, -695)"))                 # an outpost past the wall
        self.assertEqual(lua.eval("select(2, may(-580, -400, -570, -397))"), "other_plot")  # astride the wall line

    def test_nothing_within_32_tiles_of_what_a_player_built_and_the_planners_are_told(self):
        lua = runtime()
        lua.execute("""
            ada = {id=1, character={valid=true, surface=surface}}
            built[#built + 1] = {type='wall', position={x=-300, y=-300}}             -- a player's wall in npc-1's plot
            built[#built + 1] = {type='character', position={x=-450, y=-450}}        -- a player walking by
            function may(x0, y0, x1, y1) local r = {T.may_build(ada, {{x0, y0}, {x1, y1}})} return r[1], r[2] end""")
        self.assertEqual(lua.eval("select(2, may(-320, -300, -317, -297))"), "player_zone")
        self.assertTrue(lua.eval("may(-340, -300, -337, -297)"))                 # 37 tiles off: fine
        self.assertTrue(lua.eval("may(-452, -452, -449, -449)"))                 # a player standing there: fine
        zones = lua.eval("T.no_build(surface, {{-576, -576}, {576, 576}})")
        self.assertEqual(len(zones), 1)
        self.assertEqual(tuple(zones[1][i] for i in range(1, 5)), (-352, -352, -256, -256))
        bounds = lua.eval("T.bounds(ada)")
        self.assertEqual(tuple(bounds.plot[i] for i in range(1, 5)), (-576, -576, -192, -192))
        lua.execute("scenario = 'factions'")
        self.assertTrue(lua.eval("may(-100, 0, -97, 3)"))                          # other games: no rules
        self.assertIsNone(lua.eval("T.bounds(ada)"))


class PlotTests(unittest.TestCase):
    def test_once_generated_every_plot_but_the_yard_gets_the_same_large_start_round_its_home(self):
        lua = runtime()
        lua.execute("""
            -- natural iron by npc-1's centre; a lake on npc-2's iron spot
            for x = -390, -380 do for y = -390, -380 do resources[#resources + 1] = {name='iron-ore', x=x, y=y} end end
            lakes[1] = {x=-44, y=-440, r=10}
            enemy(100, 100)
            generated = false
            T.tick(60)""")
        self.assertEqual(lua.eval("T.state().status"), "generating")
        self.assertGreaterEqual(lua.eval("requested[1]"), 18)              # chunks round the whole area
        lua.execute("T.tick(120)")
        self.assertEqual(lua.eval("T.state().status"), "generating")       # waits for every chunk
        lua.execute("generated = true; T.tick(180)")
        self.assertTrue(lua.eval("T.ready()"))
        self.assertFalse(lua.eval("enemies[1].valid"))                     # swept once generated
        added = [dict(lua.eval(f"T.state().added[{i}]")) for i in range(1, lua.eval("#T.state().added") + 1)]
        plots = {a["plot"] for a in added}
        self.assertEqual(len(plots), 9)                                    # every plot but the yard
        self.assertNotIn("yard", plots)
        self.assertEqual(len(added), 9 * 5)                                # four ores and a pond each, always
        for name in plots:
            x0, y0, x1, y1 = box(lua, name)
            home = ((x0 + x1) / 2, (y0 + y1) / 2)
            offsets = sorted((a["what"], a["position"].x - home[0], a["position"].y - home[1])
                             for a in added if a["plot"] == name)
            self.assertEqual(offsets, sorted([("iron-ore", -43.5, -55.5), ("copper-ore", 44.5, -55.5),
                                              ("coal", -43.5, 56.5), ("stone", 44.5, 56.5), ("water", 0.5, 28.5)]))
        self.assertTrue(all(a.get("amount", 1) > 1_000_000 for a in added if a["what"] != "water"))
        self.assertTrue(all(lua.eval(f"made[{i}].amount") >= 1 for i in range(1, lua.eval("#made") + 1)))
        # npc-2's lake under its iron is dried first; its pond is water
        names = [lua.eval(f"tiles_set[{i}].name") for i in range(1, lua.eval("#tiles_set") + 1)]
        self.assertIn("grass-1", names)
        self.assertIn("water", names)
        self.assertEqual(lua.eval("T.home(1).x"), -383.5)


if __name__ == "__main__":
    unittest.main()
