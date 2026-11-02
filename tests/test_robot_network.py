"""The robot network (Ledger's L-3): what the network holds against what the base's growth will place
(companion/network_ledger.py), the roboport grid over the base (companion/robo_grid.py), its goal, blueprints kept whole
when a network is the point, and the game's export of its networks (mod/scripts/logistics.lua)."""
import unittest
from pathlib import Path

from companion import blueprint_packs, catalog_planner, network_ledger, robo_grid

ROOT = Path(__file__).resolve().parents[1]
RECIPES = [{"name": n} for n in ("roboport", "construction-robot", "storage-chest", "medium-electric-pole",
                                 "transport-belt", "inserter", "assembling-machine-2", "logistic-robot",
                                 "passive-provider-chest", "requester-chest")]


def site(**over):
    out = {"origin": {"x": 0, "y": 0}, "request_id": "1:5", "npc_id": 1, "recipes": RECIPES,
           "researched": ["construction-robotics"],
           "base": [{"id": "a", "purpose": "bus-base",
                     "parts": [{"box": [0, 0, 30, 20]}, {"box": [100, 0, 140, 30]}],
                     "pieces": {"assembling-machine-2": 12, "transport-belt": 1600, "inserter": 60, "lab": 4}}]}
    out.update(over)
    return out


WORLD = {"home": {"x": 10, "y": 10}, "area": [-200, -200, 300, 300], "force": "F", "resources": {}, "live_nets": [1],
         "entities": [{"n": "small-electric-pole", "t": "electric-pole", "x": 5.5, "y": 5.5, "f": "F", "w": 1, "h": 1,
                       "net": 1}]}


class NetworkBookTests(unittest.TestCase):

    def test_targets_follow_what_the_base_is_made_of_between_a_floor_and_a_cap(self):
        book = network_ledger.NetworkBook.from_site(site())
        self.assertEqual(book.item("assembling-machine-2").target, 10)       # 3 of 12, the floor 10
        self.assertEqual(book.item("transport-belt").target, 400)            # a quarter of 1600, under the cap
        self.assertEqual(book.item("inserter").target, 50)                   # the floor
        self.assertIsNone(book.item("lab"))                                  # (it cannot make labs: not counted)

    def test_nothing_is_kept_for_robots_before_there_are_any(self):
        book = network_ledger.NetworkBook.from_site(site(researched=[]))
        self.assertEqual((book.items(), book.mall_needs()), ([], []))

    def test_robots_by_the_network_s_cells_and_the_jobs_waiting(self):
        net = {"id": 1, "cells": [{"x": 0, "y": 0}] * 3, "robots": {"construction": 12, "logistic": 0},
               "chests": {"requesters": 5}, "ghosts": 40, "items": {"transport-belt": 500}}
        book = network_ledger.NetworkBook.from_site(site(logistics=[net], researched=[
            "construction-robotics", "logistic-robotics"]))
        robots = book.item("construction-robot")
        self.assertEqual((robots.stock, robots.target), (12, 3 * 10 + 10))   # 40 ghosts: 10 more
        self.assertEqual(book.item("logistic-robot").target, 10 + 4 * 5)
        self.assertEqual(book.item("transport-belt").stock, 500)             # the network's stock, not the chests'

    def test_without_a_network_its_chests_and_pockets_are_the_stock(self):
        s = site(inventory={"inserter": 30})
        s["base"][0]["stock"] = {"inserter": 25}
        self.assertEqual(network_ledger.NetworkBook.from_site(s).item("inserter").stock, 55)

    def test_robots_in_its_pockets_are_not_in_a_network(self):
        book = network_ledger.NetworkBook.from_site(site(inventory={"construction-robot": 60}))
        self.assertEqual(book.item("construction-robot").stock, 0)

    def test_a_planned_build_claims_its_items_on_top_and_the_mall_is_told_what_is_short(self):
        book = network_ledger.NetworkBook.from_site(site(inventory={"inserter": 50}))
        self.assertEqual(book.item("inserter").short, 0)
        book.claim({"inserter": 20, "splitter": 4}, "next-stage")
        needs = {n["item"]: n for n in book.mall_needs()}
        self.assertEqual((needs["inserter"]["short"], needs["inserter"]["per_minute"]), (20, 2.0))
        self.assertEqual(needs["splitter"]["target"], 4)
        self.assertEqual(book.item("inserter").signal, "low")


class GridTests(unittest.TestCase):

    def test_due_only_while_part_of_the_base_is_out_of_reach_and_roboports_can_be_made(self):
        self.assertEqual(robo_grid.due(site(recipes=[]))[0], False)
        ok, why = robo_grid.due(site())
        self.assertTrue(ok)
        self.assertIn("in robot range", why)
        covered = site(logistics=[{"cells": [{"x": 15, "y": 10, "logistic": 25}, {"x": 120, "y": 15, "logistic": 25}]}])
        self.assertEqual(robo_grid.due(covered), (False, "the base is covered"))

    def test_new_cells_overlap_into_one_network_and_cover_the_base(self):
        spots = robo_grid.layout(site(), set(), (10, 10))
        self.assertLessEqual(len(spots), robo_grid.MAX_NEW)
        spacing = 2 * robo_grid.LOGISTIC_RADIUS - robo_grid.OVERLAP
        for i, (x, y) in enumerate(spots[1:], start=1):   # each touches one before it
            self.assertTrue(any(abs(x - a) <= spacing + 1 and abs(y - b) <= spacing + 1 for a, b in spots[:i]))
        done = site(logistics=[{"cells": [{"x": x, "y": y, "logistic": 25} for x, y in spots]}])
        self.assertEqual(robo_grid.coverage(done)[0], 1.0)

    def test_the_plan_powers_its_roboports_and_puts_robots_and_a_store_in(self):
        plan = robo_grid.plan(site(), WORLD)
        protos = [e["prototype"] for e in plan["entities"]]
        self.assertGreaterEqual(protos.count("roboport"), 1)
        self.assertEqual(protos.count("storage-chest"), 1)
        self.assertTrue(any(p.endswith("electric-pole") for p in protos))
        supply = plan["stages"][0]["manual_supplies"]
        ports = protos.count("roboport")   # (robots for the cells it adds: 10 each, a roboport holds 50)
        self.assertEqual([(s["item"], s["slot"], s["count"]) for s in supply],
                         [("construction-robot", "robot", min(50, 10 + 10 * ports))])

    def test_the_goal_is_offered_while_due_and_says_why_not(self):
        self.assertTrue(catalog_planner.available("robot-network", site()))
        covered = site(logistics=[{"cells": [{"x": 15, "y": 10, "logistic": 25}, {"x": 120, "y": 15, "logistic": 25}]}])
        self.assertFalse(catalog_planner.available("robot-network", covered))
        self.assertEqual(catalog_planner.why_unavailable("robot-network", covered), "the base is covered")


class NetworkModeTests(unittest.TestCase):

    def test_a_network_keeps_roboports_and_logistic_chests_with_their_requests(self):
        protos = {"roboport": {"type": "roboport"}, "requester-chest": {"type": "logistic-container"},
                  "wooden-chest": {"type": "container"}, "assembling-machine-2": {"type": "assembling-machine"}}
        bp = {"entities": [{"entity_number": 1, "name": "roboport", "position": {"x": 2, "y": 2}},
                           {"entity_number": 2, "name": "requester-chest", "position": {"x": 6.5, "y": 0.5},
                            "request_filters": {"sections": [{"filters": [{"name": "iron-plate", "count": 300}]}]}},
                           {"entity_number": 3, "name": "assembling-machine-2", "position": {"x": 9.5, "y": 1.5}}]}
        plain, skipped, _ = blueprint_packs.convert(bp, protos)
        self.assertEqual(sorted(e["prototype"] for e in plain["entities"]), ["assembling-machine-2", "wooden-chest"])
        kept, _, missing = blueprint_packs.convert(bp, protos, network=True)
        self.assertEqual(missing, [])
        self.assertEqual(sorted(e["prototype"] for e in kept["entities"]),
                         ["assembling-machine-2", "requester-chest", "roboport"])
        chest = next(e for e in kept["entities"] if e["prototype"] == "requester-chest")
        self.assertEqual(chest["request_filters"]["sections"][0]["filters"][0]["count"], 300)


class FixTests(unittest.TestCase):
    """Codex, L-3 round 1."""

    def test_a_spot_moved_off_its_grid_point_still_joins_the_network(self):
        # (the grid points beside the first are blocked out to their far side: a spot found further out is no use)
        first = robo_grid.layout(site(), set(), (10, 10))[0]
        spacing = 2 * robo_grid.LOGISTIC_RADIUS - robo_grid.OVERLAP
        blocked = {(first[0] + spacing + dx, first[1] + dy) for dx in range(-6, 3) for dy in range(-8, 9)}
        spots = robo_grid.layout(site(), blocked, (10, 10))
        r = robo_grid.LOGISTIC_RADIUS
        for i, (x, y) in enumerate(spots[1:], start=1):
            self.assertTrue(any(abs(x - a) <= 2 * r and abs(y - b) <= 2 * r for a, b in spots[:i]), spots)

    def test_a_roboport_with_no_room_for_its_pole_is_not_chosen_and_the_chain_holds(self):
        # (Codex, L-3: the middle one dropped afterwards left the others 92 tiles apart)
        spots = robo_grid.layout(site(), set(), (10, 10))
        mx, my = spots[1]
        walled = {(mx + dx, my + dy) for dx in range(-9, 9) for dy in range(-9, 9)
                  if not (-2 <= dx < 2 and -2 <= dy < 2)}            # its own 4x4 free, nothing round it
        plan = robo_grid.plan(site(), {**WORLD, "entities": WORLD["entities"] + [
            {"n": "stone-wall", "t": "wall", "x": x + 0.5, "y": y + 0.5, "f": "F", "w": 1, "h": 1} for x, y in walled]})
        ports = [(e["position"]["x"], e["position"]["y"]) for e in plan["entities"] if e["prototype"] == "roboport"]
        r = robo_grid.LOGISTIC_RADIUS
        for i, (x, y) in enumerate(ports[1:], start=1):
            self.assertTrue(any(abs(x - a) <= 2 * r and abs(y - b) <= 2 * r for a, b in ports[:i]), ports)
        poles = [e for e in plan["entities"] if e["prototype"].endswith("electric-pole")]
        self.assertGreaterEqual(len(poles), len(ports))

    def test_not_offered_without_robots_to_make_nor_again_right_after_it_failed(self):
        no_robots = site(recipes=[r for r in RECIPES if r["name"] != "construction-robot"])
        self.assertEqual(robo_grid.due(no_robots), (False, "needs research first: construction-robot"))
        self.assertFalse(robo_grid.due(site(failed_purposes=["robot-network"]))[0])

    def test_no_plan_without_power_to_join(self):
        from companion import layout_planner
        with self.assertRaises(layout_planner.PlanError):
            robo_grid.plan(site(), {**WORLD, "entities": []})
        with self.assertRaises(layout_planner.PlanError):   # (poles that no generator feeds are no power: Codex)
            robo_grid.plan(site(), {**WORLD, "live_nets": []})

    def test_the_poles_cross_kept_ground_but_not_the_bus_s(self):
        # (Codex, L-3: the fallback reused the cached kept tiles; the bus's hard ground is never crossed)
        from companion import land
        w = {**WORLD, "reserved": [[0, 0, 40, 40]], "reserved_hard": [[100, 100, 110, 110]]}
        land.reserved_tiles(w)                                               # (cached, as a real dump is)
        w["_reserved_tiles"] = land.reserved_tiles(w)
        from companion.base_plan import blocked_tiles
        kept = land.reserved_tiles(w) - blocked_tiles({**w, "reserved": None, "_reserved_tiles": None})
        self.assertIn((12, 12), kept)                                       # (free kept ground: crossable)
        self.assertNotIn((105, 105), kept)

    def test_robots_put_in_are_what_the_network_is_short_of(self):
        net = {"id": 1, "cells": [{"x": 15, "y": 10, "logistic": 25}], "robots": {"construction": 15},
               "chests": {"storages": 1}}
        plan = robo_grid.plan(site(logistics=[net]), WORLD)
        supply = plan["stages"][0]["manual_supplies"]
        ports = sum(1 for e in plan["entities"] if e["prototype"] == "roboport")
        # (15 there of a 10 target: only what the new cells add)
        self.assertEqual([(s["item"], s["count"]) for s in supply], [("construction-robot", 10 + 10 * ports - 15)])
        self.assertNotIn("storage-chest", [e["prototype"] for e in plan["entities"]])

    def test_a_requester_chest_keeps_its_asks_in_the_game(self):
        import test_clearing
        lua = test_clearing.game()
        lua.execute("""
            prototypes.item['iron-plate'] = prototypes.item['iron-plate'] or {name='iron-plate'}
            kept = DS.settings_copy({request_filters={sections={{index=1, filters={
                {index=1, name='iron-plate', quality='normal', comparator='=', count=300},
                {index=3, name='iron-plate', quality='rare', comparator='=', count=50},
                {index=2, name='no-such-item', count=5}}}}, request_from_buffers=true}}, 'logistic-container')
            slots = {}
            point = {add_section=function() return {set_slot=function(i, f) slots[#slots + 1] = {i, f.value.name, f.min} end} end}
            defines.logistic_member_index = defines.logistic_member_index or {logistic_container=0}
            built = {position={x=0, y=0}, get_logistic_point=function() return point end}
            DS.apply_settings(built, kept)
        """)
        self.assertEqual([tuple(s.values()) for s in lua.eval("slots").values()], [(1, "iron-plate", 300)])
        self.assertTrue(lua.eval("built.request_from_buffers"))



class RepairTests(unittest.TestCase):
    """L-5: the network keeps repair packs for its robots, sized by the walls and turrets they cover, made in a cell."""

    NET = {"id": 1, "cells": [{"x": 15, "y": 10, "logistic": 25, "construction": 55}],
           "robots": {"construction": 20, "logistic": 10}, "chests": {"storages": 1}, "defenses": 200,
           "repair_packs": 30}
    MORE = RECIPES + [{"name": n} for n in ("repair-pack", "assembling-machine-2", "iron-gear-wheel", "electronic-circuit")]

    def test_repair_packs_kept_by_what_its_robots_cover_and_twice_under_attack(self):
        book = network_ledger.NetworkBook.from_site(site(logistics=[self.NET], recipes=self.MORE))
        packs = book.item("repair-pack")
        self.assertEqual((packs.stock, packs.target), (30, 50 + 50))       # (a floor, a quarter a wall or turret)
        attacked = network_ledger.NetworkBook.from_site(site(logistics=[self.NET], recipes=self.MORE, under_attack=True))
        self.assertEqual(attacked.item("repair-pack").target, 200)

    def test_a_maker_cell_asks_the_network_for_its_inputs_and_fills_a_provider_chest(self):
        s = site(logistics=[self.NET], recipes=self.MORE)
        self.assertTrue(robo_grid.supply_due(s)[0])
        plan = robo_grid.supply_plan(s, WORLD)
        by = {e["prototype"]: e for e in plan["entities"]}
        asks = {f["name"]: f["count"] for f in by["requester-chest"]["request_filters"]["sections"][0]["filters"]}
        self.assertEqual(asks, {"iron-gear-wheel": 40, "electronic-circuit": 40})
        self.assertEqual(by["assembling-machine-2"]["recipe"], "repair-pack")
        self.assertEqual(by["passive-provider-chest"]["bar"], robo_grid.PROVIDER_SLOTS)
        self.assertTrue(catalog_planner.available("robot-supply", s))
        # (one stands: not again; its inputs are wanted in the network)
        built = site(logistics=[self.NET], recipes=self.MORE)
        built["base"].append({"id": "cell", "purpose": "robot-supply", "recipes": ["repair-pack"], "box": [14, 12, 17, 19]})
        self.assertFalse(robo_grid.supply_due(built)[0])
        book = network_ledger.NetworkBook.from_site(built)
        self.assertEqual(book.item("iron-gear-wheel").wanted - book.item("iron-gear-wheel").target, 40)

    def test_a_network_without_logistic_robots_gets_them_with_its_cell_or_no_cell(self):
        idle = {**self.NET, "robots": {"construction": 20, "logistic": 0}}
        s = site(logistics=[idle], recipes=self.MORE)
        self.assertTrue(robo_grid.supply_due(s)[0])
        plan = robo_grid.supply_plan(s, WORLD)
        port = next(e for e in plan["entities"] if e["prototype"] == "roboport")
        supply = plan["stages"][0]["manual_supplies"]
        self.assertEqual([(m["entity_id"], m["item"], m["slot"]) for m in supply], [(port["id"], "logistic-robot", "robot")])
        self.assertGreaterEqual(supply[0]["count"], network_ledger.LOGISTIC_PER_REQUESTER)
        self.assertEqual(len({(e["position"]["x"], e["position"]["y"]) for e in plan["entities"]}), len(plan["entities"]))
        no_robots = [r for r in self.MORE if r["name"] != "logistic-robot"]
        self.assertFalse(robo_grid.supply_due(site(logistics=[idle], recipes=no_robots))[0])
        # (a network with logistic robots: no roboport brought)
        plan = robo_grid.supply_plan(site(logistics=[self.NET], recipes=self.MORE), WORLD)
        self.assertEqual(plan["stages"][0]["manual_supplies"], [])

    def test_a_maker_the_game_sees_in_the_network_counts_even_after_it_left_the_base_memory(self):
        self.assertFalse(robo_grid.supply_due(site(logistics=[{**self.NET, "makers": 1}], recipes=self.MORE))[0])

    def test_each_network_is_judged_on_its_own_and_the_cell_goes_where_packs_are_short(self):
        stocked = {**self.NET, "id": 1, "repair_packs": 500}
        bare = {**self.NET, "id": 2, "cells": [{"x": 215, "y": 10, "logistic": 25, "construction": 55}],
                "repair_packs": 0}
        s = site(logistics=[stocked, bare], recipes=self.MORE)
        due, why = robo_grid.supply_due(s)
        self.assertTrue(due)
        self.assertIn("network 2", why)
        world = {**WORLD, "entities": WORLD["entities"] + [
            {"n": "small-electric-pole", "t": "electric-pole", "x": 200.5, "y": 5.5, "f": "F", "w": 1, "h": 1,
             "net": 1}]}
        machine = next(e for e in robo_grid.supply_plan(s, world)["entities"] if e.get("recipe") == "repair-pack")
        self.assertLess(abs(machine["position"]["x"] - 215), 25)                       # (in the bare network's area)
        # (a cell standing in network 1 does not count for network 2)
        s["base"].append({"id": "cell", "purpose": "robot-supply", "recipes": ["repair-pack"], "box": [14, 12, 17, 19]})
        self.assertTrue(robo_grid.supply_due(s)[0])

    def test_no_cell_without_a_network_or_the_research(self):
        self.assertFalse(robo_grid.supply_due(site(recipes=self.MORE))[0])
        self.assertIn("needs research", robo_grid.supply_due(site(logistics=[self.NET]))[1])


class GameTests(unittest.TestCase):

    def test_the_game_exports_each_network_near_home(self):
        from lupa.lua52 import LuaRuntime
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().source = (ROOT / "mod" / "scripts" / "logistics.lua").read_text(encoding="utf-8")
        lua.execute("""
            L = assert(load(source))()
            port = {valid=true, type='roboport', name='roboport', position={x=10, y=12}, energy=5}
            net = {valid=true, network_id=7, all_construction_robots=20, available_construction_robots=15,
                   all_logistic_robots=4, available_logistic_robots=4,
                   cells={{owner=port, logistic_radius=25, construction_radius=55,
                           stationed_construction_robot_count=15, stationed_logistic_robot_count=4}},
                   get_contents=function() return {{name='transport-belt', count=300, quality='normal'},
                                                    {name='inserter', count=5, quality='rare'}} end,
                   provider_points={{}, {}}, requester_points={{}}, storage_points={}}
            port.logistic_network = net
            surface = {find_entities_filtered=function(f) return {port, port} end,
                       count_entities_filtered=function(f) return 3 end}
            npc = {force='F', home={x=0, y=0}, character={valid=true, surface=surface, position={x=0, y=0}}}
            out = L.export(npc)
        """)
        out = lua.eval("out")
        self.assertEqual(len(out), 1)                                        # (one network, found twice)
        n = out[1]
        self.assertEqual((n.id, n.robots.construction_free, n.ghosts), (7, 15, 3))
        self.assertEqual((n.cells[1].x, n.cells[1].logistic, n.cells[1].robots), (10, 25, 19))
        self.assertEqual(dict(n["items"].items()), {"transport-belt": 300})     # (normal quality only)
        self.assertEqual((n.chests.providers, n.chests.requesters, n.chests.storages), (2, 1, 0))


    def test_robots_repair_where_a_network_of_its_own_covers_with_packs(self):
        from lupa.lua52 import LuaRuntime
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().source = (ROOT / "mod" / "scripts" / "logistics.lua").read_text(encoding="utf-8")
        lua.execute("""
            L = assert(load(source))()
            prototypes = {get_item_filtered=function() return {['repair-pack']={}, ['bob-repair-pack']={}} end}
            counts = {['repair-pack']=30, ['bob-repair-pack']=5}
            net = {valid=true, network_id=7, all_construction_robots=10,
                   get_item_count=function(f) return counts[type(f) == 'table' and f.name or f] or 0 end}
            personal = {valid=true, network_id=9, all_construction_robots=10, get_item_count=function() return 99 end}
            covering = {net}
            surface = {find_logistic_networks_by_construction_area=function() return covering end}
            npc = {force='F', character={valid=true, surface=surface, logistic_cell={logistic_network=personal}}}
            here = L.can_repair(npc, {x=0, y=0})
            covering = {personal}
            own_only = L.can_repair(npc, {x=0, y=0})
            counts = {}; covering = {net}
            empty = L.can_repair(npc, {x=0, y=0})
        """)
        self.assertEqual((lua.eval("here"), lua.eval("own_only"), lua.eval("empty")), (35, 0, 0))

if __name__ == "__main__":
    unittest.main()
