"""The team world at war (team_war.lua): breaches of a plot's wall line, attacks, exports and cover, in
Factorio's Lua 5.2 with the game stubbed."""
from pathlib import Path
import unittest

from lupa.lua52 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]


def runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)
    for name in ("team_world", "team_war"):
        lua.globals()[name + "_source"] = (ROOT / "mod" / "scripts" / (name + ".lua")).read_text(encoding="utf-8")
    lua.execute("""
        package.preload['scripts.team_world'] = function() return assert(load(team_world_source))() end
        nests_of = {}   -- (the nest each wave unit came from, by unit number: team_waves' record)
        package.preload['scripts.team_waves'] = function()
            return {nest_of=function(u) return u and nests_of[u.unit_number] end}
        end
        settings = {global={['npc-factions-scenario']={value='team'}}}
        defines = {inventory={turret_ammo=1}}
        function log() end
        function force(name)
            local f = {name=name, valid=true, friends={}}
            f.get_friend = function(o) return f.friends[o.name] == true end
            return f
        end
        enemy = force('enemy')
        enemy.get_evolution_factor = function() return 0.42 end
        ada_force, bob_force, players, stranger = force('npc-faction-1'), force('npc-faction-2'), force('player'),
            force('npc-faction-9')
        ada_force.friends['player'], ada_force.friends['npc-faction-2'] = true, true
        game = {tick=1000, forces={enemy=enemy, player=players, ['npc-faction-1']=ada_force,
                                   ['npc-faction-2']=bob_force, ['npc-faction-9']=stranger}}
        walls, turrets = {}, {}
        local function in_area(p, a) return p.x >= a[1][1] and p.x <= a[2][1] and p.y >= a[1][2] and p.y <= a[2][2] end
        local function has(list, x) for _, v in ipairs(type(list) == 'table' and list or {list}) do
            if v == x then return true end end return false end
        surface = {index=1}
        function surface.find_entities_filtered(f)
            local out = {}
            if f.type == 'wall' or (type(f.type) == 'table' and f.type[1] == 'wall') then
                for _, w in ipairs(walls) do if w.valid and w.force == f.force and in_area(w.position, f.area) then
                    out[#out + 1] = w end end
            else
                for _, t in ipairs(turrets) do
                    local d = (t.position.x - f.position.x) ^ 2 + (t.position.y - f.position.y) ^ 2
                    if has(f.force, t.force) and d <= f.radius ^ 2 then out[#out + 1] = t end
                end
            end
            return out
        end
        function surface.count_entities_filtered(f) return #surface.find_entities_filtered(f) end
        surface.index = 1
        game.surfaces = {[1]=surface}
        logged = {}
        function log(s) logged[#logged + 1] = s end
        TW = require('scripts.team_world')
        storage = {npcs={}}
        storage.team = TW.layout({x=0, y=0})
        storage.team.status, storage.team.homes, storage.team.added, storage.team.cleared = 'ready', {}, {}, 0
        ada = {id=1, name='Ada', force=ada_force,
               character={valid=true, force=ada_force, surface=surface, position={x=-400, y=-500}}}
        storage.npcs[1] = ada
        W = assert(load(team_war_source))()
        breached = {}
        W.on_breach = function(npc, b) breached[#breached + 1] = npc.name .. ':' .. b.edge end
        function wall(x, y) local w = {valid=true, type='wall', name='stone-wall', force=ada_force,
            surface=surface, position={x=x, y=y}} walls[#walls + 1] = w return w end
        function die(e, by) W.on_entity_died({entity=e, force=by or enemy, tick=game.tick}); e.valid = false end
        function unit(id, name, spits, hp)
            return {valid=true, type='unit', name=name, unit_number=id, force=enemy,
                    prototype={attack_parameters={type=spits and 'stream' or 'projectile'},
                               get_max_health=function() return hp or 15 end}}
        end
        function bite(cause, target) W.on_entity_damaged({cause=cause, entity=target, tick=game.tick}) end
        function turret(x, y, f, empty)
            local t = {valid=true, type='ammo-turret', force=f, position={x=x, y=y}}
            t.get_inventory = function() return {is_empty=function() return empty == true end} end
            turrets[#turrets + 1] = t
            return t
        end
    """)
    return lua


class BreachTests(unittest.TestCase):
    def test_a_loss_on_the_wall_line_is_a_breach_with_the_layers_left_there(self):
        lua = runtime()
        # npc-1's plot is (-576,-576)-(-192,-192): its outer edges are north and west
        self.assertEqual(lua.eval("(W.edge_of(TW.plot('npc-1'), {x=-400, y=-570}))"), "north")
        self.assertEqual(lua.eval("(W.edge_of(TW.plot('npc-1'), {x=-570, y=-400}))"), "west")
        self.assertIsNone(lua.eval("(W.edge_of(TW.plot('npc-1'), {x=-400, y=-400}))"))
        self.assertIsNone(lua.eval("(W.edge_of(TW.plot('npc-1'), {x=-197, y=-400}))"))   # (east: a teammate's)
        lua.execute("""
            for x = -410, -390 do                                                 -- two layers...
                if x ~= -400 then wall(x + 0.5, -574.5); wall(x + 0.5, -573.5) end  -- (...save one column: D-23)
            end
            lost = wall(-399.5, -572.5)                                           -- its only piece, lost: a hole
            die(lost)""")
        b = lua.eval("W.breaches(ada, {x=-432, y=-416})")
        self.assertEqual(len(b), 1)
        self.assertEqual((b[1].layers, b[1].lost, b[1].edge), (2, "stone-wall", "north"))
        self.assertEqual((b[1].x, b[1].y), (32.5, -156.5))                       # local tiles
        self.assertEqual(list(lua.eval("breached").values()), ["Ada:north"])
        # the same 16 tiles: one breach; mid-plot, or by a teammate's fire: none
        lua.execute("game.tick = 1100; die(wall(-395.5, -572.5)); die(wall(-400.5, -400.5)); "
                    "die(wall(-300.5, -572.5), players)")
        b = lua.eval("W.breaches(ada)")
        self.assertEqual(len(b), 1)
        self.assertEqual(lua.eval("storage.team.war.breaches['1:north:-25'].losses"), 1)   # (layers behind: damage)
        self.assertEqual(len(lua.eval("W.worn_stretches(ada)")), 1)
        # 30 game minutes on, it is forgotten
        self.assertEqual(len(lua.eval("W.breaches(ada, nil, 1100 + 108001)")), 0)
        lua.execute("settings.global['npc-factions-scenario'].value = 'factions'; die(wall(-420.5, -572.5))")
        self.assertEqual(len(lua.eval("breached")), 1)                          # other games: nothing

    def test_a_loss_on_a_side_of_its_wall_ring_is_a_breach_too(self):
        # (the user, 2026-10-26: a wall ring round the factory first; the plot's edge later)
        lua = runtime()
        lua.execute("ada.design_history = {{purpose='plot-wall', keep={ring={-464, -444, -356, -356}, ring_gen=1, "
                    "ring_side='north'}}}")
        self.assertEqual(lua.eval("(W.edge_of(TW.plot('npc-1'), {x=-400, y=-441.5}, ada))"), "north")
        self.assertEqual(lua.eval("(W.edge_of(TW.plot('npc-1'), {x=-361.5, y=-400}, ada))"), "east")
        self.assertIsNone(lua.eval("(W.edge_of(TW.plot('npc-1'), {x=-400, y=-441.5}))"))   # (whose ring: none given)
        lua.execute("""
            for x = -410, -390 do if x ~= -400 then wall(x + 0.5, -441.5) end end
            lost = wall(-399.5, -440.5)
            die(lost)""")
        b = lua.eval("W.breaches(ada, {x=-432, y=-416})")
        self.assertEqual(len(b), 1)
        self.assertEqual((b[1].layers, b[1].edge), (1, "north"))

    def test_a_stretch_worn_down_again_and_again_is_damage_not_a_breach(self):
        # (the user, 2026-10-25: more turrets where it is constantly hit and damaged heavily; and D-23: walls damaged
        # but standing are no breach, only a hole is)
        lua = runtime()
        lua.execute("""
            for x = -410, -390 do wall(x + 0.5, -574.5) end
            function hurt(e, before, after, by)
                e.max_health = 350
                W.on_entity_damaged({entity=e, force=by or enemy, final_health=after,
                                     final_damage_amount=before - after, tick=game.tick})
            end
            a, b, c = walls[1], walls[5], walls[9]
            hurt(a, 200, 150); hurt(a, 150, 100)       -- below half once: one hit, however often bitten after
            hurt(b, 300, 160); hurt(c, 300, 340)""")    # (not below half: none)
        lua.execute("hurt(b, 160, 150)")                                  # (still below: no new hit)
        lua.execute("hurt(walls[12], 200, 10, players)")                  # (a teammate's fire: none)
        self.assertEqual(len(lua.eval("W.worn_stretches(ada)")), 0)
        lua.execute("hurt(c, 300, 100)")                                  # three: worn
        self.assertEqual(len(lua.eval("W.breaches(ada)")), 0)             # (no hole: no breach)
        worn = lua.eval("W.worn_stretches(ada)")
        self.assertEqual((len(worn), worn[1].edge), (1, "north"))
        self.assertEqual(len(lua.eval("breached")), 0)
        self.assertEqual(len(lua.eval("W.threat_board(ada, {x=0, y=0}, surface)").worn), 1)

    def test_a_wall_lost_with_another_layer_behind_it_is_damage_and_the_last_one_a_hole(self):
        # (D-23, the user: "don't need repair right away unless a hole forms and biters break into the perimeter")
        lua = runtime()
        lua.execute("""
            outer, inner = wall(-400.5, -574.5), wall(-400.5, -573.5)
            W.on_entity_died({entity=outer, force=enemy, tick=game.tick})   -- (still valid in its death event)
            outer.valid = false""")
        self.assertEqual(len(lua.eval("W.breaches(ada)")), 0)             # (the line still closed there)
        self.assertEqual(len(lua.eval("W.worn_stretches(ada)")), 1)
        lua.execute("W.on_entity_died({entity=inner, force=enemy, tick=game.tick}); inner.valid = false")
        self.assertEqual(len(lua.eval("W.breaches(ada)")), 1)             # (a hole: a breach, at once)
        self.assertTrue(lua.eval("W.breaches(ada)")[1].hole)
        # (its column walled again: closed, said once with how long it stood open; D-24)
        lua.execute("game.tick = game.tick + 600; again = wall(-400.5, -574.5)")
        self.assertEqual(len(lua.eval("W.breaches(ada)")), 0)
        self.assertTrue(any("breach closed after 10 s" in s for s in lua.eval("logged").values()))
        # (a turret lost on that stretch after: a breach again, a turret's, its hole mended)
        lua.execute("""
            gun = {valid=true, type='ammo-turret', name='gun-turret', force=ada_force, surface=surface,
                   position={x=-401.5, y=-569.5}}
            game.tick = game.tick + 10
            W.on_entity_died({entity=gun, force=enemy, tick=game.tick})""")
        b = lua.eval("W.breaches(ada)")
        self.assertEqual((len(b), b[1].lost, b[1].hole), (1, "gun-turret", None))

    def test_attacks_are_told_by_what_came_and_evolution_is_read(self):
        lua = runtime()
        lua.execute("""
            target = wall(-400.5, -560.5)
            small, spitter = unit(1, 'small-biter'), unit(2, 'medium-spitter', true, 75)
            bite(small, target); bite(small, target); bite(spitter, target)
            bite(unit(3, 'small-biter'), {valid=true, force=ada_force, position={x=0, y=0}})   -- not in its plot
            game.tick = 1000 + 1801
            bite(unit(4, 'big-biter'), target)""")
        board = lua.eval("W.threat_board(ada, {x=0, y=0}, surface)")
        self.assertEqual(board.evolution, 0.42)
        lua.execute("storage.npcs[2] = {id=2, name='Bob', force=bob_force, character={valid=true}}")
        team = lua.eval("W.threat_board(storage.npcs[2], nil, surface)").team    # (Ada's waves, newest first, D-15)
        self.assertEqual((len(team), team[1].count, team[1].units["big-biter"], team[2].count), (2, 1, 1, 2))
        self.assertEqual(len(board.team), 0)                                # (not its own)
        newest, first = board.attacks[1], board.attacks[2]
        self.assertEqual((newest.kind, newest.biggest), ("biters", "big"))
        self.assertEqual((first.kind, first.biggest, first.units["small-biter"], first.units["medium-spitter"]),
                         ("mixed", "medium", 1, 1))
        view = lua.eval("W.threat(ada, game.tick, surface)")
        self.assertEqual((view.attacks_lately, view.last_attack.units, view.breaches), (2, 1, 0))

    def test_an_attack_tells_the_nest_its_wave_came_from(self):
        # (NPC Offense: the strike back goes for "the nest the waves come from"; the first hit is the base's edge)
        lua = runtime()
        lua.execute("""
            target = wall(-400.5, -560.5)
            u = unit(1, 'small-biter')
            nests_of[u.unit_number] = {x=-410, y=-900}
            bite(u, target)""")
        a = lua.eval("W.threat_board(ada, {x=0, y=0}, surface)").attacks[1]
        self.assertEqual((a.nest.x, a.nest.y), (-410, -900))
        self.assertEqual((a["from"].x, a["from"].y), (-400.5, -560.5))     # (where it first hit, as before)

    def test_every_attack_is_kept_for_good_past_the_30_minutes_the_board_shows(self):
        # (the user, 2026-10-26: walls at once if it was attacked before them, however long ago)
        lua = runtime()
        lua.execute("""
            target = wall(-400.5, -560.5)
            bite(unit(1, 'small-biter'), target); bite(unit(2, 'small-biter'), target)
            game.tick = 1000 + 1801
            bite(unit(3, 'small-biter'), target)
            game.tick = 1000 + 1801 + 2 * 108000""")
        board = lua.eval("W.threat_board(ada, {x=0, y=0}, surface)")
        self.assertEqual(len(board.attacks), 0)
        lua.execute("storage.npcs[2] = {id=2, name='Bob', force=bob_force, character={valid=true}}")
        self.assertEqual(len(lua.eval("W.threat_board(storage.npcs[2], nil, surface)").team), 0)   # (old: past 30 min)
        self.assertEqual((board.ever.count, board.ever.first, board.ever.last), (2, 1000, 2801))

    def test_the_nests_its_pollution_reaches_are_known_and_how_far_round_home_none_stands(self):
        lua = runtime()
        lua.execute("""
            ada.home = {x=-400, y=-500}
            nests, smog = {}, {}
            function nest(x, y, p) nests[#nests + 1] = {valid=true, type='unit-spawner', force=enemy,
                position={x=x, y=y}}; smog[x .. ',' .. y] = p end
            nest(-400, -800, 12)      -- 300 tiles north: its pollution reaches it
            nest(-600, -500, 0)       -- 200 west: none reaches it
            nest(-400, -1200, 40)     -- 700: farther than it looks
            local plain = surface.find_entities_filtered
            function surface.find_entities_filtered(f)
                if f.type ~= 'unit-spawner' then return plain(f) end
                local out = {}
                for _, n in ipairs(nests) do
                    if (n.position.x - f.position.x) ^ 2 + (n.position.y - f.position.y) ^ 2 <= f.radius ^ 2 then
                        out[#out + 1] = n end end
                return out
            end
            function surface.get_pollution(p) return smog[p.x .. ',' .. p.y] or 0 end""")
        n = lua.eval("W.threat_board(ada, {x=-432, y=-416}, surface)").nests
        self.assertEqual(n.free, 200)                                             # (the nearest it knows to be there)
        self.assertEqual([(r.x, r.y, r.pollution) for r in n.reached.values()], [(32, -384, 12)])   # local tiles
        lua.execute("smog['-600,-500'] = 5")
        self.assertEqual(len(lua.eval("W.nests(ada, nil, surface)").reached), 1)   # (looked again 10 s on, no sooner)
        lua.execute("game.tick = game.tick + 600")
        self.assertEqual(len(lua.eval("W.nests(ada, nil, surface)").reached), 2)
        lua.execute("nests = {}; game.tick = game.tick + 600")
        self.assertEqual(lua.eval("W.nests(ada, nil, surface)").free, 250)        # (none within what it looks over)
        lua.execute("nest(-1000, -500, 7); ada.home = {x=-900, y=-500}")
        self.assertEqual(len(lua.eval("W.nests(ada, nil, surface)").reached), 1)   # (a new home: looked at at once)


class CrossingTests(unittest.TestCase):
    def test_its_belts_and_pipes_straight_across_its_rings_wall_line_are_counted_by_side(self):
        # (D-8: walls up before they could go under keep their holes; the plan that closes them needs to know)
        lua = runtime()
        lua.execute("""
            ada.design_history = {{purpose='plot-wall', keep={ring={-464, -444, -356, -356}, ring_gen=1,
                                                              ring_side='north'}}}
            things = {}
            function put(name, kind, x, y, d) things[x .. ',' .. y] = {valid=true, name=name, type=kind,
                force=ada_force, direction=d or 0, position={x=x + 0.5, y=y + 0.5}} end
            for y = -446, -436 do put('transport-belt', 'transport-belt', -420, y, 8) end   -- across the north side
            for y = -446, -440 do put('fast-transport-belt', 'transport-belt', -400, y, 8) end
            put('fast-transport-belt', 'transport-belt', -400, -439, 4)                       -- (turns under the line)
            for x = -470, -450 do put('pipe', 'pipe', x, -400) end                             -- across the west side
            for y = -446, -436 do put('transport-belt', 'transport-belt', -380, y, 4) end   -- (each running east)
            for y = -446, -436 do put('transport-belt', 'transport-belt', -360, y, 8) end   -- across, but fed ...
            put('transport-belt', 'transport-belt', -359, -442, 12)                          -- ... from beside
            for y = -446, -436 do put('transport-belt', 'transport-belt', -463, y, 8) end   -- (at the corner)
            local plain = surface.find_entities_filtered
            function surface.find_entities_filtered(f)
                if not f.area then return plain(f) end
                local out = {}
                for _, e in pairs(things) do
                    local p = e.position
                    if p.x >= f.area[1][1] and p.x <= f.area[2][1] and p.y >= f.area[1][2] and p.y <= f.area[2][2]
                        and (e.type == f.type[1] or e.type == f.type[2]) then out[#out + 1] = e end
                end
                return out
            end
            function surface.find_entity(name, p)
                local e = things[math.floor(p[1]) .. ',' .. math.floor(p[2])]
                return e and e.name == name and e or nil
            end""")
        found = lua.eval("W.threat_board(ada, nil, surface)").crossings
        north = sorted((c.along, c.under) for c in found.north.values())
        self.assertEqual(north, [(-463, "underground-belt"), (-420, "underground-belt")])   # (the planner keeps to its
        self.assertEqual([(c.along, c.under) for c in found.west.values()], [(-400, "pipe-to-ground")])   # stretch)
        self.assertIsNone(found.south)
        # the ring the whole plot: the yard's outer edges it holds are walled too (a band north of the yard)
        lua.execute("""
            local plot = TW.plot('npc-1')
            ada.design_history = {{purpose='plot-wall', keep={ring={plot.box[1], plot.box[2], plot.box[3], plot.box[4]},
                                                              ring_gen=2, ring_side='north'}}}
            plot.bands = {{name='north', box={plot.box[3], plot.box[2] - 32, plot.box[3] + 64, plot.box[2] + 64}}}
            band_y = plot.box[2] - 32
            for y = band_y - 2, band_y + 10 do put('pipe', 'pipe', plot.box[3] + 20, y) end
            game.tick = game.tick + 700""")
        north = lua.eval("W.threat_board(ada, nil, surface)").crossings.north
        self.assertIn(lua.eval("TW.plot('npc-1').box[3] + 20"),
                      [c.along for c in north.values()])


class CoverTests(unittest.TestCase):
    def test_it_falls_back_to_the_teams_nearest_loaded_turret_behind_it(self):
        lua = runtime()
        # Ada at (-400,-500), biters coming from the north (-400,-560)
        lua.execute("""
            ahead = turret(-400, -540, ada_force)          -- toward the biters: no cover
            empty = turret(-400, -470, ada_force, true)    -- unloaded
            theirs = turret(-410, -460, stranger)          -- not a teammate's
            bobs = turret(-380, -450, bob_force)           -- a teammate's, behind it
            far = turret(-400, -300, ada_force)            -- out of reach""")
        self.assertTrue(lua.eval("W.cover(ada, {x=-400, y=-560}) == bobs"))
        lua.execute("mine = turret(-400, -480, ada_force)")
        self.assertTrue(lua.eval("W.cover(ada, {x=-400, y=-560}) == mine"))
        lua.execute("for _, t in ipairs(turrets) do t.valid = false end")
        self.assertIsNone(lua.eval("W.cover(ada, {x=-400, y=-560})"))

    def test_a_turret_between_it_and_the_biters_or_a_dry_flamethrower_is_no_cover(self):
        lua = runtime()
        # (Codex's cases: Ada at (-400,-500), biters at (-400,-560))
        lua.execute("""
            between = turret(-400, -515, ada_force)
            dry = turret(-400, -480, ada_force); dry.type = 'fluid-turret'; dry.fluids_count = 0""")
        self.assertIsNone(lua.eval("W.cover(ada, {x=-400, y=-560})"))
        lua.execute("dry.fluids_count = 1; dry.get_fluid = function(i) return {name='light-oil', amount=50} end")
        self.assertTrue(lua.eval("W.cover(ada, {x=-400, y=-560}) == dry"))


if __name__ == "__main__":
    unittest.main()
