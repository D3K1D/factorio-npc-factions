"""Loaders (the Vanilla Loaders mod): the engineers place them with the right end and facing, and the planners
use them rarely, as the user plays: only where the items per second needed are more than the best inserter
the engineer can make moves (a chest feeding a bus lane its modules take from fast, a fast module port);
ordinary chests, outposts and slow ports keep their inserters. Footprints are two tiles long, a reused layout
keeps each loader's end, and a design's loaders get theirs from what they touch."""
from __future__ import annotations

import json
import math
import sys
import unittest
from pathlib import Path

from lupa.lua52 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "designer"))

from companion import blueprints as bp  # noqa: E402
from companion import layout_planner, team_area  # noqa: E402
from companion import military_modules as mm  # noqa: E402
from companion import modules as mods  # noqa: E402
from companion.base_plan import Stage, _side_lanes, blocked_tiles  # noqa: E402
from companion.bus import Bus  # noqa: E402
from companion.modules import EAST, LOADER, Module, NORTH, Placed, Port, SOUTH, WEST, _drop_overlaps  # noqa: E402
from companion.smelting import footprint  # noqa: E402

STEP = {NORTH: (0, -1), EAST: (1, 0), SOUTH: (0, 1), WEST: (-1, 0)}
REACH = 1.5   # the Vanilla Loaders' container_distance, as Factorio 2.1 reports it


def ends(e) -> tuple[tuple[int, int], tuple[int, int]]:
    """The tiles just ahead of and just behind a loader (`e` a Placed)."""
    dx, dy = STEP[e.direction]
    return ((math.floor(e.x + dx * REACH), math.floor(e.y + dy * REACH)),
            (math.floor(e.x - dx * REACH), math.floor(e.y - dy * REACH)))


class FootprintTests(unittest.TestCase):
    def test_a_loader_covers_two_tiles_along_its_facing(self):
        self.assertEqual(footprint([Placed(LOADER, 0.5, 2.0, NORTH)]), {(0, 1), (0, 2)})
        self.assertEqual(footprint([Placed(LOADER, 2.0, 0.5, WEST)]), {(1, 0), (2, 0)})

    def test_belts_give_way_to_a_loader(self):
        m = Module("m", entities=[Placed("transport-belt", 1.5, 0.5, EAST), Placed("transport-belt", 3.5, 0.5, EAST),
                                  Placed(LOADER, 2.0, 0.5, EAST, kind="output")])
        self.assertEqual([(e.name, e.x) for e in _drop_overlaps(m).entities],
                         [("transport-belt", 3.5), (LOADER, 2.0)])

    def test_the_team_rules_measure_a_loader_without_facts(self):
        self.assertEqual(team_area._size("loader"), (1, 2))
        self.assertEqual(team_area._size("stone-furnace"), (2, 2))
        team = {"team": {"plot": [0, 0, 10, 10], "area": [-100, -100, 100, 100], "no_build": []}}
        inside = {"entities": [{"id": "l", "prototype": "loader", "position": {"x": 9.0, "y": 5.5}, "direction": EAST}]}
        self.assertEqual(team_area.misplaced(inside, team), [])
        # facing north the same centre would reach a tile beyond the plot's edge
        across = {"entities": [{"id": "l", "prototype": "loader", "position": {"x": 9.5, "y": 10.0}, "direction": NORTH}]}
        self.assertEqual(team_area.misplaced(across, team), ["l"])

    def test_the_map_keeps_new_belts_off_a_loaders_belt_end(self):
        def loader(io):
            return {"entities": [{"n": "loader", "t": "loader", "x": 4.5, "y": 4.0, "d": NORTH, "w": 1, "h": 2,
                                  "f": "npc-faction-1", "io": io}]}
        body = {(4, 3), (4, 4)}
        self.assertEqual(blocked_tiles(loader("output")), body | {(4, 2)})    # it puts items ahead
        self.assertEqual(blocked_tiles(loader("input")), body | {(4, 5)})     # it takes them from behind
        self.assertEqual(blocked_tiles(loader(None)), body | {(4, 2), (4, 5)})


class LibraryTests(unittest.TestCase):
    SITE = {"purpose": "coal-supply", "prototypes": {
        "loader": {"type": "loader", "width": 1, "height": 2, "placement_item": "loader"},
        "wooden-chest": {"type": "container", "width": 1, "height": 1, "placement_item": "wooden-chest"},
        "transport-belt": {"type": "transport-belt", "width": 1, "height": 1, "placement_item": "transport-belt"}}}
    PLAN = {"entities": [
        {"id": "belt", "prototype": "transport-belt", "position": {"x": 10.5, "y": 10.5}, "direction": EAST},
        {"id": "loader", "prototype": "loader", "position": {"x": 12.0, "y": 10.5}, "direction": EAST,
         "belt_to_ground_type": "input"},
        {"id": "chest", "prototype": "wooden-chest", "position": {"x": 13.5, "y": 10.5}, "direction": 0}],
        "stages": [{"id": "s", "depends_on": [], "entity_ids": ["belt", "loader", "chest"], "manual_supplies": [],
                    "witnesses": []}]}

    def test_a_reused_layout_keeps_each_loaders_end_through_any_turn(self):
        blueprint = bp.from_plan(self.PLAN, self.SITE, source="test")
        self.assertEqual(blueprint["size"], [4, 1])
        loader = next(e for e in blueprint["entities"] if e["id"] == "loader")
        self.assertEqual(loader["belt_to_ground_type"], "input")
        for turns in range(4):
            plan = bp.instantiate(blueprint, {"rotation": turns, "offset": [5, 5]}, "1:2")
            placed = {e["id"]: e for e in plan["entities"]}
            self.assertEqual(placed["loader"]["belt_to_ground_type"], "input")
            self.assertNotIn("belt_to_ground_type", placed["chest"])
            # the chest is still just ahead of the loader, the belt just behind it
            lx, ly = placed["loader"]["position"]["x"], placed["loader"]["position"]["y"]
            dx, dy = STEP[placed["loader"]["direction"]]
            self.assertEqual((placed["chest"]["position"]["x"], placed["chest"]["position"]["y"]),
                             (lx + dx * REACH, ly + dy * REACH))
            self.assertEqual((placed["belt"]["position"]["x"], placed["belt"]["position"]["y"]),
                             (lx - dx * REACH, ly - dy * REACH))


def coal_site(recipes: list[str]) -> dict:
    """A 40x40 site with an 8x8 coal patch and the burner phase's facts (a north drill drops at -0.3,-1.3)."""
    return {"request_id": "1:4", "bounds": [0, 0, 40, 40], "home": {"x": 20, "y": 20},
            "resources": [{"resource": "coal", "rows": [[y, 10, 17] for y in range(10, 18)]}],
            "recipes": [{"name": r} for r in recipes],
            "prototypes": {"burner-mining-drill": {"type": "mining-drill", "width": 2, "height": 2,
                                                   "drop_vector": [-0.3, -1.3]},
                           "wooden-chest": {"type": "container", "width": 1, "height": 1}}}


def inserters(**bonus) -> dict:
    """A site with loaders and the vanilla inserters unlocked, their facts as Factorio 2.1.23 reports them."""
    return {"recipes": [{"name": n} for n in ("loader", "inserter", "fast-inserter", "burner-inserter")],
            "inserter_bonus": {"stack": bonus.get("stack", 0), "bulk": bonus.get("bulk", 0)},
            "prototypes": {
                "burner-inserter": {"type": "inserter", "rotation_speed": 0.013, "hand_bonus": 0, "bulk": False,
                                    "stack_bonus": True},
                "inserter": {"type": "inserter", "rotation_speed": 0.014, "hand_bonus": 0, "bulk": False,
                             "stack_bonus": True},
                "fast-inserter": {"type": "inserter", "rotation_speed": 0.04, "hand_bonus": 0, "bulk": False,
                                  "stack_bonus": True},
                "bulk-inserter": {"type": "inserter", "rotation_speed": 0.04, "hand_bonus": 0, "bulk": True,
                                  "stack_bonus": True}}}


class ThresholdTests(unittest.TestCase):
    def test_an_inserter_moves_a_hand_a_turn(self):
        site = inserters()
        self.assertAlmostEqual(mods.inserter_rate("inserter", site), 0.84)
        self.assertAlmostEqual(mods.inserter_rate("fast-inserter", site), 2.4)
        self.assertAlmostEqual(mods.inserter_rate("fast-inserter", inserters(stack=1)), 4.8)   # capacity research
        self.assertAlmostEqual(mods.inserter_rate("bulk-inserter", inserters(stack=1, bulk=2)), 7.2)   # its own bonus
        self.assertEqual(mods.inserter_rate("fast-inserter"), mods.INSERTER_RATE["fast-inserter"])  # no facts

    def test_the_limit_is_the_best_inserter_it_can_make(self):
        self.assertAlmostEqual(mods.loader_limit(inserters()), 2.4)              # fast, not the bulk (locked)
        self.assertAlmostEqual(mods.loader_limit(inserters(stack=2)), 7.2)
        self.assertEqual(mods.loader_limit({"recipes": [{"name": "loader"}, {"name": "inserter"}]}),
                         mods.INSERTER_RATE["inserter"])
        self.assertIsNone(mods.loader_limit({**inserters(), "recipes": [{"name": "fast-inserter"}]}))


class OutpostTests(unittest.TestCase):
    def test_a_coal_supply_keeps_a_chest_per_drill_with_loaders_researched(self):
        site = coal_site(["loader", "transport-belt"])
        site["prototypes"]["loader"] = {"type": "loader", "width": 1, "height": 2, "container_distance": REACH}
        kinds = [e["prototype"] for e in layout_planner.plan(site, pairs=2, resource="coal", output="chest",
                                                             name="coal-supply")["entities"]]
        self.assertNotIn("loader", kinds)
        self.assertEqual(kinds.count("wooden-chest"), 2)


class MilitaryChestTests(unittest.TestCase):
    def module(self, rate: float) -> Module:
        m = Module("row", width=6, height=4)
        m.ports = [Port("iron-plate", "in", 0, 1, EAST, rate), Port("piercing-rounds-magazine", "out", 0, 3, WEST, 0.1)]
        m.entities = [Placed("transport-belt", 0.5, 1.5, EAST), Placed("transport-belt", 0.5, 3.5, WEST)]
        return m

    def test_a_port_faster_than_an_inserter_gets_a_loader(self):
        parts = mm._with_chests(self.module(rate=3.0), limit=2.4)
        loaders = [e for e in parts if e.name == LOADER]
        self.assertEqual([e.kind for e in loaders], ["output"])     # the fast input; the slow product keeps its inserter
        self.assertEqual(sum(1 for e in parts if e.name == "inserter"), 1)
        loader = loaders[0]
        ahead, behind = ends(loader)
        chests = {(math.floor(e.x), math.floor(e.y)) for e in parts if e.name == mm.END_CHEST}
        belts = {(math.floor(e.x), math.floor(e.y)): e for e in parts if e.name == "transport-belt"}
        self.assertIn(behind, chests)                  # it empties the chest behind it...
        self.assertEqual(belts[ahead].direction, EAST)  # ...onto the port belt running into the module
        self.assertEqual(len(footprint(parts)), sum(len(footprint([e])) for e in parts))

    def test_ports_an_inserter_keeps_up_with_keep_it(self):
        for limit in (None, 2.4):
            parts = mm._with_chests(self.module(rate=0.5), limit=limit)
            self.assertFalse([e for e in parts if e.name == LOADER])
            self.assertEqual(sum(1 for e in parts if e.name == "inserter"), 2)


class LaneHeadTests(unittest.TestCase):
    """A bus lane started from a chest the engineer fills (no machine of its own makes the item)."""

    def lane(self, need: float, loader_above: float | None) -> tuple[list[Placed], list[str]]:
        bus = Bus(10, 10, 4, 60)
        bus.add_lane("plastic-bar", start=30, source="side")
        bus.taken["plastic-bar"] = need
        stages, notes = [Stage("bus-red-circuits", [])], []
        _side_lanes({"entities": [], "force": "npc-faction-1"}, bus, 30, ["plastic-bar"], set(), stages, notes, 30.0,
                    loader_above=loader_above)
        return stages[0].entities, notes

    def test_a_lane_taken_from_faster_than_an_inserter_moves_is_unloaded_by_a_loader(self):
        line, notes = self.lane(need=3.0, loader_above=2.4)
        self.assertEqual(sorted(e.name for e in line), ["iron-chest", LOADER])
        loader = next(e for e in line if e.name == LOADER)
        self.assertEqual(loader.kind, "output")
        ahead, behind = ends(loader)
        chest = next(e for e in line if e.name == "iron-chest")
        self.assertEqual((math.floor(chest.x), math.floor(chest.y)), behind)
        self.assertEqual(ahead, (Bus(10, 10, 4, 60).lane_x(0), 30))   # it puts the plastic on the lane's head
        self.assertIn("more than an inserter's 2.4/s", notes[0])

    def test_a_slow_lane_or_no_loaders_keeps_the_inserter(self):
        for need, above in ((1.0, 2.4), (3.0, None)):
            line, _ = self.lane(need=need, loader_above=above)
            self.assertEqual(sorted(e.name for e in line), ["inserter", "iron-chest"])


class DesignerPlanTests(unittest.TestCase):
    def test_a_designed_loader_gets_its_end_from_what_it_touches(self):
        from designer_llm.context import validate_context
        from designer_llm.proposal_schema import to_wire
        from designer_llm.providers import ReplayProvider
        from designer_llm.request import make_request
        from designer_llm.workflow import propose
        from companion.designer_planner import to_plan
        fixtures = ROOT / "designer" / "fixtures"
        context = json.loads((fixtures / "context.json").read_text())
        raw = json.loads((fixtures / "proposal.json").read_text())
        raw["entities"], raw["stages"] = raw["entities"][:2], raw["stages"][:1]
        snapshot = context["geometry_snapshot"]
        for name, facts in {"iron-chest": {"type": "container"}, "transport-belt": {"type": "transport-belt"},
                            "loader": {"type": "loader", "height": 2, "container_distance": REACH}}.items():
            snapshot["items"].append(name)
            snapshot["prototypes"][name] = {"placement_item": name, "width": 1, "height": 1, "energy_source": "none",
                                            **facts}
        stage = raw["stages"][0]
        for eid, name, x, y, d in (("in-chest", "iron-chest", 30.5, 20.5, 0), ("unloader", "loader", 32.0, 20.5, EAST),
                                   ("belt", "transport-belt", 33.5, 20.5, EAST), ("filler", "loader", 35.0, 20.5, EAST),
                                   ("out-chest", "iron-chest", 36.5, 20.5, 0)):
            raw["entities"].append({"id": eid, "prototype": name, "position": {"x": x, "y": y}, "direction": d})
            stage["entity_ids"].append(eid)
            stage["planned_bill"][name] = stage["planned_bill"].get(name, 0) + 1
        context.pop("geometry_snapshot_sha256", None)
        context = validate_context(context)
        raw["snapshot_sha256"] = context["geometry_snapshot_sha256"]
        outcome = propose(make_request(context, "Chest to belt to chest"), context, ReplayProvider(to_wire(raw)))
        self.assertEqual(outcome.status, "accepted_offline", outcome.evidence)
        placed = {e["id"]: e for e in to_plan(outcome.accepted.document)["entities"]}
        self.assertEqual(placed["unloader"]["belt_to_ground_type"], "output")
        self.assertEqual(placed["filler"]["belt_to_ground_type"], "input")
        self.assertNotIn("belt_to_ground_type", placed["belt"])


# --- the game side, in Factorio's Lua 5.2 without Factorio ---------------------------------------------

def worker_runtime() -> LuaRuntime:
    """worker.lua with what place_one needs: an engineer carrying loaders, a surface that records what it
    is asked to create, and a stand-in for the Vanilla Loaders mod turning a new loader round."""
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().worker_source = (ROOT / "mod" / "scripts" / "worker.lua").read_text(encoding="utf-8")
    lua.globals().ghosts_source = (ROOT / "mod" / "scripts" / "ghosts.lua").read_text(encoding="utf-8")
    lua.execute("""
        defines = {direction={north=0, east=4, south=8, west=12}, build_check_type={manual=1},
                   inventory={character_ammo=1, character_guns=2, character_armor=3}}
        function log() end
        local stubs = {['scripts.team_world']={active=function() return false end},
                       ['scripts.ghosts']=assert(load(ghosts_source))()}
        function require(name) return stubs[name] or {} end
        prototypes = {item={loader={type='item', name='loader', place_result={name='loader'}}},
                      entity={loader={type='loader', name='loader',
                                      collision_box={left_top={x=-0.4, y=-0.9}, right_bottom={x=0.4, y=0.9}}}}}
        snap = true
        created = {}
        script = {raise_script_built=function(e)
            -- Vanilla Loaders' snapping, when it finds no belt: the loader flipped (its end, then its facing)
            if snap and e.entity.type == 'loader' then
                e.entity.loader_type = e.entity.loader_type == 'input' and 'output' or 'input'
                e.entity.direction = (e.entity.direction + 8) % 16
            end
        end}
        stack = {valid_for_read=true, name='loader', count=3, health=1, quality={name='normal'}}
        inventory = setmetatable({insert=function() return 1 end}, {__len=function() return 1 end,
            __index=function(_, i) if i == 1 then return stack end end})
        surface = {can_place_entity=function() return true end,
            create_entity=function(spec)
                created[#created+1] = spec
                return {valid=true, type='loader', name=spec.name, position=spec.position,
                        direction=spec.direction, loader_type=spec.type or 'input'}
            end}
        character = {valid=true, surface=surface, force={name='npc-faction-1'}, build_distance=10,
            position={x=10, y=10}, bounding_box={left_top={x=9.8, y=9.8}, right_bottom={x=10.2, y=10.2}},
            can_place_entity=function() return true end, get_main_inventory=function() return inventory end}
        npc = {id=1, character=character}
        W = assert(load(worker_source, '@worker'))()
    """)
    return lua


class WorkerLoaderTests(unittest.TestCase):
    def test_a_loader_goes_down_with_the_planned_end_and_facing(self):
        lua = worker_runtime()
        entity = lua.eval("W.place_one(npc, {entity_name='loader', item='loader', position={x=2.5, y=3}, "
                          "direction=defines.direction.east, belt_to_ground_type='output'})")
        self.assertIsNotNone(entity)
        self.assertEqual(lua.eval("created[1].type"), "output")          # create_entity was told the end
        self.assertEqual(lua.eval("created[1].direction"), EAST)
        # the snapping mod turned it round; the plan's end and facing were put back
        self.assertEqual((entity.loader_type, entity.direction), ("output", EAST))
        self.assertEqual(lua.eval("stack.count"), 2)

    def test_an_input_loader_left_alone_stays_as_built(self):
        lua = worker_runtime()
        lua.execute("snap = false")
        entity = lua.eval("W.place_one(npc, {entity_name='loader', item='loader', position={x=2.5, y=3}, "
                          "direction=defines.direction.north, belt_to_ground_type='input'})")
        self.assertEqual((entity.loader_type, entity.direction), ("input", NORTH))


def designed_runtime():
    import test_retire
    lua = test_retire.runtime()
    lua.execute("""
        for name, p in pairs({loader={type='loader', w=1, h=2}, ['iron-chest']={type='container', w=1, h=1},
                              ['transport-belt']={type='transport-belt', w=1, h=1}}) do
            prototypes.entity[name] = {name=name, type=p.type, tile_width=p.w, tile_height=p.h,
                                       items_to_place_this={{name=name}}}
        end
        npc = storage.npcs[1]
        npc.character.surface = {count_entities_filtered=function() return 0 end}
        npc.design = {status='waiting', request_id='1:7', origin={x=100, y=100}}
    """)
    return lua


class DesignedStartLoaderTests(unittest.TestCase):
    def test_the_game_keeps_each_planned_loaders_end(self):
        lua = designed_runtime()
        result = lua.eval("""DS.submit(helpers_json or {request_id='1:7', plan={plan_id='p', entities={
            {id='chest', prototype='iron-chest', position={x=10.5, y=10.5}, direction=0},
            {id='unloader', prototype='loader', position={x=12, y=10.5}, direction=4, belt_to_ground_type='output'},
            {id='belt', prototype='transport-belt', position={x=13.5, y=10.5}, direction=4},
            {id='filler', prototype='loader', position={x=15, y=10.5}, direction=4, belt_to_ground_type='input'},
            {id='spare', prototype='loader', position={x=20.5, y=20}, direction=0}},
            stages={{id='s', entity_ids={'chest', 'unloader', 'belt', 'filler', 'spare'},
                     witnesses={{kind='entity_built', entity_id='unloader'}}}}}})""")
        self.assertTrue(result.ok, dict(result))
        entities = lua.eval("npc.design.plan.entities")
        self.assertEqual(entities["unloader"].belt_to_ground_type, "output")
        self.assertEqual(entities["filler"].belt_to_ground_type, "input")
        self.assertEqual(entities["spare"].belt_to_ground_type, "input")    # as the game places one by default
        self.assertIsNone(entities["chest"].belt_to_ground_type)

    def test_a_loader_is_placeable_and_a_build_job(self):
        import re
        designed = (ROOT / "mod" / "scripts" / "designed_start.lua").read_text(encoding="utf-8")
        worker = (ROOT / "mod" / "scripts" / "worker.lua").read_text(encoding="utf-8")
        placeable = re.search(r"local PLACEABLE=\{(.*?)\}", designed, re.S).group(1)
        build = re.search(r"local BUILD_TYPES = \{(.*?)\}", worker, re.S).group(1)
        self.assertRegex(placeable, r"\bloader=true")
        self.assertRegex(build, r"\bloader = true")


if __name__ == "__main__":
    unittest.main()
