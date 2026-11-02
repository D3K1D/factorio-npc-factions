"""Early robots (scripts/robots.lua, in Factorio's Lua 5.2; W-26): its kit given once at its tier, and what its robots
can do they do (a plan's pieces, rebuilds and clearing as ghosts and marks), while it stands in their reach; by hand
what they cannot do or have not done within a while."""
import unittest

import test_character_layer

SETUP = """
    Robots = require('scripts.robots')
    defines = defines or {}
    defines.inventory = {character_armor=5}
    settings.global['npc-factions-early-robots'] = {value='green-science'}
    function radius_is(r, bots) cell = {construction_radius=r, logistic_network={all_construction_robots=bots or 10}} end
    radius_is(25)
    held = {['stone-furnace']=3}
    ghosts = {}
    surface = {find_entities_filtered=function() return {} end,
        create_entity=function(spec)
            local g = {valid=true, name='entity-ghost', ghost_name=spec.inner_name, position=spec.position,
                       direction=spec.direction, belt_to_ground_type=spec.type}
            function g.set_recipe(r) g.recipe = r end
            function g.get_recipe() return g.recipe and {name=g.recipe} end
            function g.destroy() g.valid = false end
            ghosts[#ghosts + 1] = g
            return g
        end}
    npc = {id=1, force='f', character={valid=true, position={x=0, y=0}, surface=surface,
        logistic_cell=nil, get_main_inventory=function()
            return {get_item_count=function(name) return held[name] or 0 end} end}}
    setmetatable(npc.character, {__index=function(_, k) if k == 'logistic_cell' then return cell end end})
    piece = {prototype='stone-furnace', world={x=4, y=0}, direction=0}
    hand = {kind='build', entity_name='stone-furnace', item='stone-furnace', position=piece.world, direction=0}
"""


def runtime():
    lua = test_character_layer.runtime()
    lua.execute(SETUP)
    return lua


class InsteadTests(unittest.TestCase):
    def test_in_their_reach_a_ghost_goes_up_and_it_waits(self):
        lua = runtime()
        lua.execute("job, why = Robots.instead(npc, hand, 100, piece)")
        self.assertIsNone(lua.eval("job"))
        self.assertEqual(lua.eval("why"), "robots:build")
        self.assertEqual(lua.eval("#ghosts"), 1)
        self.assertTrue(lua.eval("piece.ghost == ghosts[1]"))
        lua.execute("job, why = Robots.instead(npc, hand, 200, piece)")
        self.assertEqual(lua.eval("#ghosts"), 1)                                # (one ghost, kept)

    def test_out_of_their_reach_it_walks_in(self):
        lua = runtime()
        lua.execute("piece.world = {x=80, y=0}; hand.position = piece.world; job = Robots.instead(npc, hand, 100, piece)")
        self.assertEqual(lua.eval("job.kind"), "move")
        x = lua.eval("job.position.x")
        self.assertLess(abs(80 - x), 24)                                       # (within their reach once there)

    def test_by_hand_without_robots_without_the_item_or_after_a_while(self):
        lua = runtime()
        lua.execute("radius_is(25, 0)")
        self.assertTrue(lua.eval("Robots.instead(npc, hand, 100, piece) == hand"))   # (no robots)
        lua = runtime()
        lua.execute("held['stone-furnace'] = 0")
        self.assertTrue(lua.eval("Robots.instead(npc, hand, 100, piece) == hand"))   # (nothing in its pockets)
        lua = runtime()
        lua.execute("Robots.instead(npc, hand, 100, piece)")
        lua.execute("for t = 400, 100 + Robots.PATIENCE, 300 do Robots.instead(npc, hand, t, piece) end; "
                    "late = Robots.instead(npc, hand, 100 + Robots.PATIENCE + 1, piece)")   # (asked every 5 s, as its turn does)
        self.assertTrue(lua.eval("late == hand"))                              # (not done within a while)
        self.assertFalse(lua.eval("ghosts[1].valid"))
        self.assertTrue(lua.eval("Robots.instead(npc, hand, 100 + Robots.PATIENCE + 2, piece) == hand"))

    def test_a_machine_whose_recipe_its_ghost_cannot_take_is_built_by_hand(self):
        lua = runtime()
        lua.execute("""
            surface.create_entity = function(spec)
                local g = {valid=true}
                function g.set_recipe() error('no recipe on this ghost') end
                function g.destroy() g.valid = false end
                ghosts[#ghosts + 1] = g
                return g
            end
            piece.recipe = 'iron-gear-wheel'""")
        self.assertTrue(lua.eval("Robots.instead(npc, hand, 100, piece) == hand"))
        self.assertFalse(lua.eval("ghosts[1].valid"))

    def test_a_hologram_not_as_planned_is_built_by_hand(self):
        # (Codex, W-26: an assembler ghost without its recipe, an underground's wrong end)
        lua = runtime()
        lua.execute("""
            old = {valid=true, ghost_name='assembling-machine-1', direction=0, get_recipe=function() return nil end}
            surface.find_entities_filtered = function() return {old} end
            m = {prototype='assembling-machine-1', world={x=4, y=0}, direction=0, recipe='iron-gear-wheel'}
            mh = {kind='build', entity_name='assembling-machine-1', item='stone-furnace', position=m.world}""")
        self.assertTrue(lua.eval("Robots.instead(npc, mh, 100, m) == mh"))
        self.assertTrue(lua.eval("old.valid"))                                  # (the hologram's, left alone)
        lua = runtime()
        lua.execute("""
            surface.create_entity = function(spec)
                local g = {valid=true, direction=spec.direction, belt_to_ground_type='input'}
                function g.destroy() g.valid = false end
                ghosts[#ghosts + 1] = g
                return g
            end
            u = {prototype='underground-belt', world={x=4, y=0}, direction=0, belt_to_ground_type='output'}
            uh = {kind='build', entity_name='underground-belt', item='stone-furnace', position=u.world}""")
        self.assertTrue(lua.eval("Robots.instead(npc, uh, 100, u) == uh"))
        self.assertFalse(lua.eval("ghosts[1].valid"))

    def test_time_away_does_not_count_against_its_robots(self):
        # (Codex, W-26: the defense took it away a minute; back, its robots get their time again)
        lua = runtime()
        lua.execute("Robots.instead(npc, hand, 100, piece); back = {Robots.instead(npc, hand, 100 + 3600, piece)}")
        self.assertIsNone(lua.eval("back[1]"))
        self.assertEqual(lua.eval("back[2]"), "robots:build")

    def test_its_plan_s_own_hologram_not_as_planned_is_built_by_hand(self):
        # (Codex, W-26: the ghost Holograms.one keeps on the piece)
        lua = runtime()
        lua.execute("""
            m = {prototype='assembling-machine-1', world={x=4, y=0}, direction=0, recipe='iron-gear-wheel',
                 ghost={valid=true, direction=0, get_recipe=function() return nil end}}
            mh = {kind='build', entity_name='assembling-machine-1', item='stone-furnace', position=m.world}""")
        self.assertTrue(lua.eval("Robots.instead(npc, mh, 100, m) == mh"))

    def test_walks_that_never_bring_it_into_their_reach_end_in_hands(self):
        # (Codex, W-26: a stand point it cannot reach)
        lua = runtime()
        lua.execute("piece.world = {x=80, y=0}; hand.position = piece.world")
        self.assertEqual(lua.eval("Robots.instead(npc, hand, 100, piece).kind"), "move")
        self.assertEqual(lua.eval("Robots.instead(npc, hand, 100 + Robots.REACH_FOR - 1, piece).kind"), "move")
        self.assertTrue(lua.eval("Robots.instead(npc, hand, 100 + Robots.REACH_FOR + 1, piece) == hand"))

    def test_given_to_its_hands_it_stays_its_hands(self):
        # (Codex, W-26: no ghost again for a piece by hand; a tree's mark taken off)
        lua = runtime()
        lua.execute("for t = 100, 100 + Robots.PATIENCE + 300, 300 do Robots.instead(npc, hand, t, piece) end")
        self.assertIsNone(lua.eval("Robots.ghost(npc, piece)"))
        n = lua.eval("#ghosts")
        lua.execute("Robots.instead(npc, hand, 100 + Robots.PATIENCE + 600, piece)")
        self.assertEqual(lua.eval("#ghosts"), n)
        lua.execute("npc.robot_waits = {}")                                     # (pruned: still its hands')
        self.assertTrue(lua.eval("Robots.instead(npc, hand, 100 + Robots.PATIENCE + 900, piece) == hand"))
        self.assertEqual(lua.eval("#ghosts"), n)
        lua.execute("""
            tree = {valid=true, type='tree', name='tree-01', position={x=3, y=1}, marked=false}
            function tree.to_be_deconstructed() return tree.marked end
            function tree.order_deconstruction() tree.marked = true; return true end
            function tree.cancel_deconstruction() tree.marked = false end
            j = {kind='mine', target=tree, count=1}
            for t = 100, 100 + Robots.PATIENCE + 300, 300 do Robots.instead(npc, j, t) end""")
        self.assertFalse(lua.eval("tree.marked"))

    def test_a_tree_in_the_way_is_marked_for_its_robots(self):
        lua = runtime()
        lua.execute("""
            tree = {valid=true, type='tree', name='tree-01', position={x=3, y=1}, marked=false}
            function tree.to_be_deconstructed() return tree.marked end
            function tree.order_deconstruction(force) tree.marked = true; return true end
            job, why = Robots.instead(npc, {kind='mine', target=tree, count=1}, 100)""")
        self.assertTrue(lua.eval("tree.marked"))
        self.assertEqual(lua.eval("why"), "robots:mine")
        lua.execute("ore = {valid=true, type='resource', name='iron-ore', position={x=1, y=1}}")
        self.assertTrue(lua.eval("(function() local j = {kind='mine', target=ore} return Robots.instead(npc, j, 100) == j end)()"))


class LiveSevenTests(unittest.TestCase):
    """LIVE-7c: at night its robots stood without charge, and every piece waited 45 s or more on them."""

    def test_a_grid_run_down_is_as_no_robots(self):
        lua = runtime()
        lua.execute("""
            defines.inventory.character_armor = 5
            grid = {battery_capacity=40e6, available_in_batteries=1e6}
            armor = {valid_for_read=true, grid=grid}
            npc.character.get_inventory = function() return {armor} end""")
        self.assertIsNone(lua.eval("Robots.reach(npc)"))
        lua.execute("grid.available_in_batteries = 30e6")
        self.assertEqual(lua.eval("Robots.reach(npc)"), 25)

    def test_robots_that_built_nothing_in_a_patience_are_switched_off_a_while(self):
        lua = runtime()
        lua.execute("game = game or {}; game.tick = 100")
        lua.execute("for t = 100, 100 + Robots.PATIENCE, 300 do Robots.instead(npc, hand, t, piece) end")
        lua.execute("game.tick = 100 + Robots.PATIENCE + 300; Robots.instead(npc, hand, game.tick, piece)")
        self.assertIsNotNone(lua.eval("npc.robots_off_until"))
        self.assertIsNone(lua.eval("Robots.reach(npc)"))
        lua.execute("game.tick = npc.robots_off_until")
        self.assertEqual(lua.eval("Robots.reach(npc)"), 25)


class NetworkTests(unittest.TestCase):
    """W-26b: its fixed network's robots build from the network's chests where it covers the spot."""

    def runtime(self, stock=8):
        lua = runtime()
        lua.execute(f"""
            radius_is(25, 0)                                   -- (no robots of its own)
            held['stone-furnace'] = 0
            net = {{valid=true, network_id=7, available_construction_robots=20,
                    get_item_count=function(item) return item == 'stone-furnace' and {stock} or 0 end}}
            L = require('scripts.logistics')
            L.network_at = function(n, pos) return net end""")
        return lua

    def test_a_ghost_it_neither_carries_nor_walks_to(self):
        lua = self.runtime()
        lua.execute("piece.world = {x=200, y=0}; hand.position = piece.world; job, why = Robots.instead(npc, hand, 100, piece)")
        self.assertIsNone(lua.eval("job"))
        self.assertEqual(lua.eval("why"), "robots:network")
        self.assertEqual(lua.eval("#ghosts"), 1)
        self.assertTrue(lua.eval("Robots.network_has(npc, piece.world, 'stone-furnace')"))

    def test_by_hand_when_the_network_holds_none_or_its_robots_do_not_build_it(self):
        lua = self.runtime(stock=0)
        self.assertTrue(lua.eval("Robots.instead(npc, hand, 100, piece) == hand"))
        lua = self.runtime()
        lua.execute("for t = 100, 100 + Robots.PATIENCE, 300 do Robots.instead(npc, hand, t, piece) end")
        self.assertTrue(lua.eval("Robots.instead(npc, hand, 100 + Robots.PATIENCE + 300, piece) == hand"))
        self.assertTrue(lua.eval("piece.by_hand"))

    def test_its_own_personal_cell_is_no_network(self):
        lua = self.runtime()
        lua.execute("radius_is(25, 10); cell.logistic_network.network_id = 7")
        self.assertIsNone(lua.eval("Robots.network(npc, {x=0, y=0})"))


class GrantTests(unittest.TestCase):
    def test_given_once_at_its_tier(self):
        lua = runtime()
        lua.execute("""
            given = 0
            Robots.grant = function() given = given + 1; npc.robots_granted = {}; return true end
            made = 0
            machine = {valid=true, type='assembling-machine', get_recipe=function() return {name='logistic-science-pack'} end}
            machine.products_finished = 0
            machine.unit_number = 3
            npc.design_history = {{order={'m'}, entities={m={entity=machine}}}}""")
        lua.execute("Robots.tick(npc, 1)")                                    # (not its turn)
        lua.execute("Robots.tick(npc, Robots.CHECK_EVERY + 1)")                # (its turn, none made yet)
        self.assertEqual(lua.eval("given"), 0)
        lua.execute("machine.products_finished = 3; Robots.tick(npc, 2 * Robots.CHECK_EVERY + 1)")
        self.assertEqual(lua.eval("given"), 1)
        lua.execute("Robots.tick(npc, 3 * Robots.CHECK_EVERY + 1)")
        self.assertEqual(lua.eval("given"), 1)                                 # (once)

    def test_a_machine_planned_for_its_pack_counts_what_it_made_before_the_first_look(self):
        # (Codex, W-26)
        lua = runtime()
        lua.execute("""
            given = 0
            Robots.grant = function() given = given + 1; npc.robots_granted = {} end
            machine = {valid=true, type='assembling-machine', products_finished=1, unit_number=9,
                       get_recipe=function() return {name='logistic-science-pack'} end}
            npc.design_history = {{order={'m'}, entities={m={entity=machine, recipe='logistic-science-pack'}}}}
            Robots.tick(npc, Robots.CHECK_EVERY + 1)""")
        self.assertEqual(lua.eval("given"), 1)

    def test_off_or_another_tier(self):
        lua = runtime()
        lua.execute("""
            given = 0
            Robots.grant = function() given = given + 1 end
            machine = {valid=true, type='assembling-machine', products_finished=3,
                       get_recipe=function() return {name='automation-science-pack'} end}
            machine.unit_number = 7
            npc.design_history = {{order={'m'}, entities={m={entity=machine}}}}
            Robots.tick(npc, Robots.CHECK_EVERY + 1)""")
        self.assertEqual(lua.eval("given"), 0)                                 # (red is not green)
        lua.execute("settings.global['npc-factions-early-robots'].value = 'red-science'; Robots.tick(npc, Robots.CHECK_EVERY + 1)")
        self.assertEqual(lua.eval("given"), 0)                                 # (its count before: none since seen)
        lua.execute("machine.products_finished = 4; Robots.tick(npc, 2 * Robots.CHECK_EVERY + 1)")
        self.assertEqual(lua.eval("given"), 1)
        lua.execute("npc.robots_granted = nil; given = 0; settings.global['npc-factions-early-robots'].value = 'off'; "
                    "Robots.tick(npc, Robots.CHECK_EVERY + 1)")
        self.assertEqual(lua.eval("given"), 0)

    def test_robots_that_did_not_fit_in_its_pockets_are_given_later(self):
        # (Codex, W-26)
        lua = runtime()
        lua.execute("""
            room = 4
            npc.character.get_main_inventory = function()
                return {insert=function(def) local n = math.min(def.count, room); room = room - n; return n end} end
            npc.robots_granted = {robot='construction-robot', robots=4, owed=6}
            room = 0; Robots.tick(npc, Robots.CHECK_EVERY + 1)""")
        self.assertEqual(lua.eval("npc.robots_granted.owed"), 6)
        lua.execute("room = 10; Robots.tick(npc, 2 * Robots.CHECK_EVERY + 1)")
        self.assertEqual((lua.eval("npc.robots_granted.owed"), lua.eval("npc.robots_granted.robots")), (0, 10))

    def test_wired_into_its_turn_its_plans_and_its_rebuilds(self):
        root = test_character_layer.ROOT / "mod"
        factory = (root / "scripts" / "factory.lua").read_text(encoding="utf-8")
        self.assertIn("DesignedStart.robots_tick(npc,tick)", factory)
        ds = (root / "scripts" / "designed_start.lua").read_text(encoding="utf-8")
        self.assertEqual(ds.count("DesignedStart.Robots.instead("), 5)
        self.assertIn("not DesignedStart.Robots.network_has(npc,entity.world,entity.item)) then", ds)   # (not fetched)        # (stage build, footprint, zone, rebuild x2)
        control = (root / "control.lua").read_text(encoding="utf-8")
        self.assertIn("on_robot_built_entity", control)
        settings = (root / "settings.lua").read_text(encoding="utf-8")
        self.assertIn('name="npc-factions-early-robots"', settings)


class FactoryTickTests(unittest.TestCase):
    def test_its_turn_looks_at_its_robots(self):
        # (new Factory.tick code runs in a harness: the W-16 lesson)
        import test_factory_rebuild
        lua = test_factory_rebuild.runtime()
        lua.execute("seen = {}; DS.robots_tick = function(n, t) seen[#seen + 1] = t end; Factory.tick(npc, 600)")
        self.assertEqual(lua.eval("seen[1]"), 600)


if __name__ == "__main__":
    unittest.main()
