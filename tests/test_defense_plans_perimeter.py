"""The wall ring in the team world (companion/perimeter.py and its catalog goals): round the factory first, moved out
as it grows, at the plot's outer edges with no factory to go round (and in the end)."""
from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion import blueprints as bp  # noqa: E402
from companion import layout_planner, perimeter, team_area  # noqa: E402
from companion.catalog_planner import CatalogPlanner, available, team_urgent  # noqa: E402
from companion.modules import EAST, NORTH  # noqa: E402
from companion.smelting import footprint  # noqa: E402
from companion.modules import Placed  # noqa: E402

GUN = {"name": "gun-turret", "kind": "ammo", "range": 18, "min_range": 0, "w": 2, "h": 2, "ammo_categories": ["bullet"]}
LASER = {"name": "laser-turret", "kind": "electric", "range": 24, "min_range": 0, "w": 2, "h": 2, "ammo_categories": []}
DEFENSE = {"turrets": [GUN], "walls": [{"name": "stone-wall", "health": 450}], "gates": [{"name": "gate", "health": 350}],
           "ammo": {"bullet": ["piercing-rounds-magazine", "firearm-magazine"]}, "repair": [], "evolution": 0.1}


def site(**extra):
    """npc-1's plot of the team world, the north-west corner (outer edges north and west); local = world."""
    out = {"request_id": "1:9", "purpose": "choose", "origin": {"x": 0, "y": 0},
           "team": {"plot": [-576, -576, -192, -192], "area": [-576, -576, 576, 576], "no_build": [],
                    "outer": ["north", "west"]},
           "threats": [{"x": -380, "y": -700}], "defense": DEFENSE, "base": [], "completed_purposes": [],
           "recipes": [{"name": n} for n in ("gun-turret", "stone-wall", "gate", "firearm-magazine")]}
    out.update(extra)
    return out


def world(entities=(), water=()):
    return {"ok": True, "area": [-640, -640, -150, -150], "home": {"x": -384, "y": -384}, "force": "npc-faction-1",
            "entities": list(entities), "water": list(water), "resources": {}, "live_nets": []}


def wall(x, y, name="stone-wall", t="wall"):
    return {"n": name, "t": t, "x": x + 0.5, "y": y + 0.5, "w": 1, "h": 1, "f": "npc-faction-1"}


PLOT = [-576, -576, -192, -192]   # (world tiles: local = world here)


def line(side, buildings, ring=PLOT, gen=1, purpose="plot-wall"):
    """A wall (turret, breach) plan the game exported on one side of a ring."""
    return {"purpose": purpose, "buildings": buildings, "keep": {"ring": list(ring), "ring_gen": gen, "ring_side": side}}


def tiles_of(plan):
    out = []
    for e in plan["entities"]:
        p = Placed(e["prototype"], e["position"]["x"], e["position"]["y"], e.get("direction", 0))
        out.append(footprint([p]))
    return out


class PerimeterTests(unittest.TestCase):
    def check(self, plan, s):
        self.assertEqual(team_area.misplaced(plan, s), [])
        self.assertLessEqual(len(plan["stages"]), 16)
        self.assertLessEqual(len(plan["entities"]), 900)
        seen = set()
        for t in tiles_of(plan):
            self.assertFalse(t & seen)
            seen |= t
        ids = {e["id"] for e in plan["entities"]}
        staged = [i for st in plan["stages"] for i in st["entity_ids"]]
        self.assertEqual(sorted(staged), sorted(ids))

    def test_turrets_go_up_first_on_the_edge_facing_the_nests_spread_out(self):
        s = site()
        plan = perimeter.plan_turrets(s, world())
        self.check(plan, s)
        guns = [e for e in plan["entities"] if e["prototype"] == "gun-turret"]
        self.assertEqual(len(plan["stages"]), perimeter.TURRETS_PER_PLAN)          # nests 130 tiles off: no calm start
        self.assertEqual(len(guns), perimeter.TURRETS_PER_PLAN)                     # one at each spot
        self.assertTrue(all(-570 <= g["position"]["y"] <= -566 for g in guns))      # behind the north wall line
        xs = sorted(g["position"]["x"] for g in guns)
        self.assertLess(abs((xs[0] + xs[-1]) / 2 - (-380)), 60)                      # centred on the threat
        # (the user, 2026-10-26: too many too close together) spread out, each stretch in reach of two
        gaps = [b - a for a, b in zip(xs, xs[1:])]
        self.assertTrue(all(perimeter.spacing(GUN) - 1 <= g <= perimeter.spacing(GUN) + 1 for g in gaps), gaps)
        walls = [e for e in plan["entities"] if e["prototype"] == "stone-wall"]
        self.assertTrue(walls and all(w["position"]["y"] == -573.5 for w in walls))  # each one's wall in front
        supply = plan["stages"][0]["manual_supplies"]
        self.assertEqual({m["item"] for m in supply}, {"firearm-magazine"})   # the best ammo it can make (D-24: no piercing recipe)
        self.assertEqual({m["slot"] for m in supply}, {"ammo"})
        # nests to the west instead: the west edge first
        west = perimeter.plan_turrets(site(threats=[{"x": -700, "y": -300}]), world())
        self.assertTrue(all(-570 <= g["position"]["x"] <= -566 for g in west["entities"]
                            if g["prototype"] == "gun-turret"))
        # none seen round home, but its pollution reaches nests to the west (the threat board's): the west too
        reached = {"evolution": 0, "attacks": [], "nests": {"reached": [{"x": -700, "y": -300, "pollution": 2}],
                                                            "free": 250}}
        smoke = perimeter.plan_turrets(site(threats=[], threat_board=reached), world())
        self.assertTrue(all(-570 <= g["position"]["x"] <= -566 for g in smoke["entities"]
                            if g["prototype"] == "gun-turret"))
        from companion import defense_planner   # (one nest in both lists is one nest: Codex, D-guard-kit)
        both = site(threats=[{"x": -700, "y": -300, "kind": "unit-spawner"}], threat_board=reached)
        self.assertEqual(defense_planner.threats({}, both), [(-700.0, -300.0)])

    def test_turrets_already_standing_are_not_doubled_and_lasers_need_power(self):
        s = site()
        first = perimeter.plan_turrets(s, world())
        built = [{"n": "gun-turret", "t": "ammo-turret", "x": e["position"]["x"], "y": e["position"]["y"], "w": 2, "h": 2,
                  "f": "npc-faction-1"} for e in first["entities"] if e["prototype"] == "gun-turret"]
        second = perimeter.plan_turrets(s, world(built))
        old = {(round(b["x"]), round(b["y"])) for b in built}
        new = [e for e in second["entities"] if e["prototype"] == "gun-turret"]
        self.assertTrue(new)
        self.assertFalse(any(math.dist((e["position"]["x"], e["position"]["y"]), o) < 12 for e in new for o in old))
        # lasers come first when they can be powered, else the gun turrets do
        lasers = site(defense={**DEFENSE, "turrets": [LASER, GUN]})
        unpowered = perimeter.plan_turrets(lasers, world())
        self.assertEqual({e["prototype"] for e in unpowered["entities"]} - {"stone-wall"}, {"gun-turret"})
        pole = {"n": "small-electric-pole", "t": "electric-pole", "x": -380.5, "y": -560.5, "w": 1, "h": 1,
                "f": "npc-faction-1"}
        powered = perimeter.plan_turrets(lasers, world([pole]))
        self.check(powered, lasers)
        self.assertIn("laser-turret", {e["prototype"] for e in powered["entities"]})
        self.assertIn("small-electric-pole", {e["prototype"] for e in powered["entities"]})

    def test_its_door_goes_where_its_way_out_to_a_block_beyond_the_side_crosses_it(self):
        # (the user's play, 2026-10-30, lesson 12: gates where its routes crossed the wall)
        def door_x(s):
            plan = perimeter.plan_wall(s, world())
            return sorted(math.floor(g["position"]["x"]) for g in plan["entities"] if g["prototype"] == "gate")
        middle = door_x(site())
        self.assertLessEqual(abs(sum(middle) / len(middle) - (-384)), 3)          # (no way out known: the middle)
        mine = {"purpose": "electric-iron-mining", "box": [-300, -640, -280, -620], "buildings": 12}
        out = door_x(site(base=[mine]))
        # (the line from the plot's middle (-384, -384) to the mine's (-290, -630) crosses the wall row near x -312)
        self.assertLessEqual(abs(sum(out) / len(out) - (-312)), 3)
        self.assertEqual(out, list(range(out[0], out[0] + perimeter.DOOR)))
        # (from its own home, not the map's centre: home at (-450, -384) crosses near x -327; Codex)
        away = door_x(site(base=[mine], home={"x": -450, "y": -384}))
        self.assertLessEqual(abs(sum(away) / len(away) - (-327)), 3)

    def test_the_wall_line_runs_along_the_outer_edges_with_a_gate_and_keeps_out_of_the_water(self):
        s = site()
        lake = [[-573, -420, -400]]                       # water across the wall line
        already = [wall(x, -574) for x in range(-500, -480)]
        plan = perimeter.plan_wall(s, world(already, lake))
        self.check(plan, s)
        pieces = [e for e in plan["entities"] if e["prototype"] == "stone-wall"]
        self.assertTrue(all(p["position"]["y"] == -573.5 for p in pieces))         # the north edge, two tiles in
        xs = {math.floor(p["position"]["x"]) for p in pieces}
        self.assertEqual(min(xs), -574)                                             # joins the west line
        self.assertEqual(max(xs), -193)                                             # and runs to the plot's end
        self.assertFalse(xs & set(range(-500, -480)))                               # walls already there stay
        gate = [e for e in plan["entities"] if e["prototype"] == "gate"]
        self.assertEqual(len(gate), perimeter.DOOR)                                 # a door a tank fits through
        gx = sorted(math.floor(g["position"]["x"]) for g in gate)
        self.assertEqual(gx, list(range(gx[0], gx[0] + perimeter.DOOR)))           # side by side
        self.assertTrue(all(g["direction"] == EAST for g in gate))
        self.assertEqual(plan["stages"][0]["entity_ids"], [g["id"] for g in gate])  # the door before the wall
        self.assertTrue(all(len(st["entity_ids"]) <= perimeter.WALL_STAGE for st in plan["stages"]))
        # the north edge walled: the west edge next (its gate faces north)
        done = already + [wall(math.floor(p["position"]["x"]), -574) for p in pieces]
        done += [{**wall(math.floor(g["position"]["x"]), -574, "gate", "gate")} for g in gate]
        west = perimeter.plan_wall(s, world(done))
        self.assertTrue(all(e["position"]["x"] == -573.5 for e in west["entities"]))
        self.assertEqual([e for e in west["entities"] if e["prototype"] == "gate"][0]["direction"], NORTH)
        everything = done + [wall(-574, math.floor(e["position"]["y"])) for e in west["entities"]]
        with self.assertRaises(perimeter.Done):
            perimeter.plan_wall(s, world(everything))

    def test_a_breach_gets_a_layer_behind_the_others_and_more_turrets_where_they_keep_hitting(self):
        s = site(breaches=[{"x": -380, "y": -573.5, "tick": 900, "lost": "stone-wall", "layers": 1}])
        standing = [wall(x, -574) for x in range(-400, -360)]
        plan = perimeter.plan_breach(s, world(standing))
        self.check(plan, s)
        # (its layer stands: no layer more, D-23; the turrets are the answer)
        self.assertNotIn("stone-wall", {e["prototype"] for e in plan["entities"]})
        self.assertEqual(sum(1 for e in plan["entities"] if e["prototype"] == "gun-turret"), 1)
        # (a row of it gone: refilled where it stood)
        gone = [wall(x, -574) for x in range(-400, -360) if not -385 <= x <= -375]
        holed = perimeter.plan_breach(s, world(gone + [wall(x, -573) for x in range(-400, -360)]))
        refill = [e for e in holed["entities"] if e["prototype"] == "stone-wall"]
        self.assertTrue(refill and all(e["position"]["y"] == -573.5 for e in refill))
        # (a single piece gone, a hole: put back in its column, though its row mostly stands; Codex's review)
        one = [wall(x, -574) for x in range(-400, -360) if x != -380]
        hole = site(breaches=[{"x": -380, "y": -573.5, "tick": 900, "lost": "stone-wall", "layers": 1, "hole": True}])
        back = [e for e in perimeter.plan_breach(hole, world(one))["entities"] if e["prototype"] == "stone-wall"]
        self.assertEqual([(e["position"]["x"], e["position"]["y"]) for e in back], [(-379.5, -573.5)])
        # (a gate lost: a gate back; an urgent plan with no walls to spare: none)
        gate_gone = site(breaches=[{**hole["breaches"][0], "hole_piece": "gate"}])
        self.assertIn("gate", {e["prototype"] for e in perimeter.plan_breach(gate_gone, world(one))["entities"]})
        self.assertNotIn("stone-wall", {e["prototype"] for e in
                                        perimeter.plan_breach(hole, world(one), wall_limit=0)["entities"]})
        # hit there again and again (two attacks, then the breach): turrets side by side, beside the one standing
        one = [e for e in plan["entities"] if e["prototype"] == "gun-turret"][0]
        gun = {"n": "gun-turret", "t": "ammo-turret", "x": one["position"]["x"], "y": one["position"]["y"], "w": 2,
               "h": 2, "f": "npc-faction-1"}
        board = {"attacks": [{"x": -381, "y": -576, "tick": 800, "count": 20}, {"x": -379, "y": -576, "tick": 850}]}
        hot = site(breaches=s["breaches"], threat_board=board)
        again = perimeter.plan_breach(hot, world(standing + [gun]))
        new = [e for e in again["entities"] if e["prototype"] == "gun-turret"]
        self.assertGreaterEqual(len(new), 2)          # three hits: three there at least (sized to the wave: D-11)
        for e in new:
            self.assertLessEqual(abs(e["position"]["x"] - gun["x"]), 2 * 3 + 0.5)  # side by side
            self.assertEqual(e["position"]["y"], gun["y"])
        four = standing + [wall(x, y) for x in range(-400, -360) for y in (-573, -572, -571)]
        thick = perimeter.plan_breach(s, world(four))
        self.assertNotIn("stone-wall", {e["prototype"] for e in thick["entities"]})  # four layers: turrets only


class DoorTests(unittest.TestCase):
    """Doors the engineer, its cars and tanks pass through, as thick as the wall; the wall thickened where it is hit
    or walls are spare, up to four layers (the user, 2026-10-25)."""

    def north_walled(self, layers=1):
        """The north and west edges walled `layers` deep, the north edge's door at x -386..-383 through each."""
        out = []
        for o in range(layers):
            y = -574 + o
            out += [wall(x, y, "gate", "gate") if -386 <= x <= -383 else wall(x, y) for x in range(-574, -192)]
            out += [wall(-574 + o, y2) for y2 in range(-574, -192)]
        return out

    def test_a_breach_by_a_door_gets_turrets_not_a_wall_layer_more(self):
        # (D-23, the user: more turrets, not thicker walls; a door's gates through each layer: the spare-walls test)
        s = site(breaches=[{"x": -385, "y": -575, "tick": 100}])
        plan = perimeter.plan_breach(s, world(self.north_walled(1)))
        self.assertFalse({"gate", "stone-wall"} & {e["prototype"] for e in plan["entities"]})
        self.assertIn("gun-turret", {e["prototype"] for e in plan["entities"]})

    def test_spare_walls_thicken_the_thinnest_edge_by_a_layer_and_only_what_is_spare(self):
        s = site()
        w = world(self.north_walled(1))
        with self.assertRaises(perimeter.Done):
            perimeter.plan_wall(s, w, limit=perimeter.WALL_RESERVE + 50)      # (what is spare is too little)
        plan = perimeter.plan_wall(s, w, limit=perimeter.WALL_RESERVE + 300)
        walls = [e for e in plan["entities"] if e["prototype"] == "stone-wall"]
        gates = [e for e in plan["entities"] if e["prototype"] == "gate"]
        self.assertEqual(len(walls), 300)                                   # what is over the reserve
        self.assertEqual({e["position"]["y"] for e in walls + gates}, {-572.5})   # the north edge, a layer in
        self.assertEqual(sorted(math.floor(g["position"]["x"]) for g in gates), [-386, -385, -384, -383])
        four = world(self.north_walled(4))
        with self.assertRaises(perimeter.Done):
            perimeter.plan_wall(s, four, limit=5000)                         # four deep: thick enough

    def test_the_wall_goal_comes_back_for_a_layer_more_with_walls_spare(self):
        edges = perimeter.edges(site())
        base = [line(e.name, e.hi - e.lo + 1) for e in edges]
        self.assertFalse(perimeter.thicken_due(site(base=base, inventory={"stone-wall": 100})))
        self.assertTrue(perimeter.thicken_due(site(base=base, inventory={"stone-wall": 500})))
        deep = [line(e.name, 4 * (e.hi - e.lo + 1)) for e in edges]
        self.assertFalse(perimeter.thicken_due(site(base=deep, inventory={"stone-wall": 5000})))

    def test_turrets_keep_out_of_the_way_through_a_door(self):
        s = site()
        plan = perimeter.plan_turrets(s, world(self.north_walled(1)))
        for e in plan["entities"]:
            if e["prototype"] == "gun-turret":
                x = e["position"]["x"]
                self.assertFalse(-386 - perimeter.DOOR_CLEAR - 1 <= x <= -383 + perimeter.DOOR_CLEAR + 1, x)

    def test_electric_turrets_come_first_where_it_can_make_them(self):
        kit = perimeter.arms(site(defense={**DEFENSE, "turrets": [GUN, LASER]}))
        self.assertEqual([t["name"] for t in kit["turrets"]], ["laser-turret", "gun-turret"])


class CatalogTests(unittest.TestCase):
    def test_in_the_team_world_an_attack_brings_turrets_at_once_and_a_breach_its_repair(self):
        s = site()
        self.assertIsNone(team_urgent(s))                        # no attack yet: they come in the opening
        # (D-11) hit an hour ago, no wall standing, the threat calm now: walls at once all the same, by hand
        hit = {"evolution": 0, "attacks": [], "ever": {"count": 1, "first": 5, "last": 9}}
        stocked = site(threat_board=hit, inventory={"stone-wall": 60})
        self.assertEqual(team_urgent(stocked), "plot-wall")
        self.assertEqual(team_urgent(site(social={"recent_attacks": 1})), "plot-turrets")
        # biters hitting the base count too (the game's threat board; one-engineer run 2026-10-24: three biter
        # attacks brought no turrets, and the engineer died)
        board = {"attacks": [{"x": 11, "y": -23, "tick": 292059, "units": {"small-biter": 28}, "kind": "biters"}]}
        self.assertEqual(team_urgent(site(tick=294775, threat_board=board)), "plot-turrets")
        self.assertIsNone(team_urgent(site(tick=292059 + 40000, threat_board=board)))   # long ago: calm again
        self.assertFalse(available("breach-repair", s))
        breached = site(breaches=[{"x": -380, "y": -573.5, "tick": 900, "lost": "stone-wall", "layers": 1}])
        self.assertEqual(team_urgent(breached), "breach-repair")
        walled = {"id": "w", "box": [-574, -574, -194, -573], **line("north", 200)}
        built = site(base=[{"id": "p", "box": [-420, -572, -340, -566], **line("north", 4, purpose="plot-turrets")},
                           walled])
        self.assertIsNone(team_urgent(built))                    # one batch up and no attack: the opening goes on
        self.assertTrue(available("plot-turrets", built))        # (more stretches to hold)
        # not in the team world, or no turret to make yet: nothing of this
        plain = {k: v for k, v in s.items() if k != "team"}
        self.assertFalse(available("plot-turrets", plain))
        self.assertIsNone(team_urgent(plain))
        unarmed = site(defense={**DEFENSE, "turrets": []})
        self.assertFalse(available("plot-turrets", unarmed))

    def test_the_wall_line_goes_up_from_walls_stocked_and_turrets_along_it_after(self):
        # (the user, 2026-10-24: walls made by machine into a chest, "like 200" before the perimeter, then
        # turrets on it; by hand only in an emergency)
        s = site()
        self.assertFalse(available("plot-wall", s))                       # none stocked yet
        self.assertFalse(available("plot-turrets", s))                    # and no wall to stand behind
        chest = {"id": "w", "purpose": "wall-automation", "box": [-400, -400, -390, -390], "buildings": 9,
                 "stock": {"stone-wall": perimeter.WALL_STOCK - 50}}
        self.assertFalse(available("plot-wall", site(base=[chest])))
        self.assertTrue(available("plot-wall", site(base=[chest], inventory={"stone-wall": 50})))
        attacked = site(social={"recent_attacks": 1})
        # an emergency: the walls it has, and what it can make by hand before the next wave (none from nothing:
        # a whole edge made by hand held a run for 40 minutes, the review, 2026-10-25)
        self.assertFalse(available("plot-wall", attacked))
        self.assertTrue(available("plot-wall", site(social={"recent_attacks": 1}, inventory={"stone-wall": 20})))
        self.assertTrue(available("plot-turrets", attacked))
        # turrets along the wall have no walls of their own made by hand, but in an emergency
        calm = perimeter.plan_turrets(s, world(), shield=False)
        self.assertNotIn("stone-wall", {e["prototype"] for e in calm["entities"]})
        self.assertIn("stone-wall", {e["prototype"] for e in perimeter.plan_turrets(attacked, world())["entities"]})
        # with the walls it has, a run of the line facing the nests; the rest later
        plan = perimeter.plan_wall(s, world(), limit=40)
        pieces = [e for e in plan["entities"] if e["prototype"] == "stone-wall"]
        self.assertEqual(len(pieces), 40)
        along = sorted(e["position"]["x"] for e in pieces)
        self.assertLessEqual(along[-1] - along[0], 40 + perimeter.DOOR)   # one unbroken run (the door in it)
        self.assertLess(abs((along[0] + along[-1]) / 2 - -380), 25)       # facing the nest at x -380
        self.assertGreater(len(perimeter.plan_wall(s, world())["entities"]), 300)   # (in an emergency: all)
        # the line counts as done once nearly all of it stands
        done = site(base=[line(side, 380) for side in ("north", "west")])
        self.assertTrue(perimeter.walled(done))
        self.assertFalse(available("plot-wall", site(base=done["base"], inventory={"stone-wall": 300})))
        # (with walls spare over the repair reserve: back for a layer more, the user, 2026-10-25)
        self.assertTrue(available("plot-wall", site(base=done["base"], inventory={"stone-wall": 999})))

    def test_starter_turrets_stand_close_to_the_factory_facing_the_nests(self):
        factory = [{"purpose": "iron-smelting-start", "box": [-400, -400, -390, -394],
                    "machines": {"burner-mining-drill": 2, "stone-furnace": 2}},
                   {"purpose": "research-lab", "box": [-388, -398, -384, -394], "machines": {"lab": 1}},
                   {"purpose": "coal-loop", "box": [-300, -300, -296, -296], "machines": {"burner-mining-drill": 4}}]
        s = site(base=factory)
        self.assertEqual(perimeter.core_box(s), (-400, -400, -384, -394))   # (drills alone are not vital)
        self.assertTrue(available("starter-turrets", s))
        plan = perimeter.plan_starter(s, world())
        guns = [e for e in plan["entities"] if e["prototype"] == "gun-turret"]
        self.assertEqual(len(guns), perimeter.STARTER_TURRETS)
        self.assertTrue(all(-410 < g["position"]["y"] < -400 for g in guns))   # just north: the nest is at y -700
        xs = sorted(g["position"]["x"] for g in guns)
        self.assertTrue(all(-420 < x < -350 for x in xs))
        self.assertTrue(all(b - a >= perimeter.spacing(GUN) - 1 for a, b in zip(xs, xs[1:])))   # spread along it
        self.assertEqual({sup["slot"] for st in plan["stages"] for sup in st["manual_supplies"]}, {"ammo"})
        # once, and not once turrets stand on the wall line; those take their place (retired once built)
        self.assertFalse(available("starter-turrets", site(base=factory, completed_purposes=["starter-turrets"])))
        self.assertFalse(available("starter-turrets", site(base=factory + [{"purpose": "plot-turrets"}])))
        self.assertFalse(available("starter-turrets", site()))            # no factory to guard yet

    def test_the_wall_row_makes_walls_from_what_its_recipe_takes_into_a_chest(self):
        from companion import wall_block
        from companion.bus import END_CHEST
        recipes = [{"name": "stone-wall", "category": "crafting", "energy": 0.5,
                    "ingredients": [{"name": "stone-brick", "amount": 5}], "products": [{"name": "stone-wall", "amount": 1}]},
                   {"name": "stone-brick", "category": "smelting", "energy": 3.2,
                    "ingredients": [{"name": "stone", "amount": 2}], "products": [{"name": "stone-brick", "amount": 1}]}]
        made = [{"name": n} for n in ("assembling-machine-1", "inserter", "transport-belt", "small-electric-pole",
                                      "long-handed-inserter", END_CHEST)]
        s = site(recipes=recipes + made, machines={"assembling-machine-1": {}})
        pole = {"n": "small-electric-pole", "t": "electric-pole", "x": -384.5, "y": -380.5, "w": 1, "h": 1,
                "f": "npc-faction-1", "net": 1}
        mapped = {**world([pole]), "live_nets": [1]}
        plan = wall_block.plan_block(s, mapped)
        protos = [e["prototype"] for e in plan["entities"]]
        # (one-engineer hour 2026-10-24: laid out for the assembler's full speed it asked for fast inserters,
        # not researched yet, and the build was blocked)
        self.assertNotIn("fast-inserter", protos)
        self.assertTrue(set(protos) <= {r["name"] for r in made})
        with self.assertRaises(layout_planner.PlanError):              # what cannot be made: refused here
            wall_block.plan_block(site(recipes=recipes + made[1:], machines={"assembling-machine-1": {}}), mapped)
        self.assertEqual(protos.count(END_CHEST), 2)                      # bricks in, walls out
        maker = [e for e in plan["entities"] if e.get("recipe") == "stone-wall"]
        self.assertEqual(len(maker), 1)
        stage = plan["stages"][0]
        self.assertEqual([(m["item"], m["slot"]) for m in stage["manual_supplies"]], [("stone-brick", "input")])
        self.assertEqual(stage["witnesses"][0]["item"], "stone-wall")
        self.assertEqual(team_area.misplaced(plan, s), [])
        # a better assembler researched but made with steel it cannot have yet: the plain one (run 25, 2026-10-25)
        steel = [{"name": "steel-plate", "category": "smelting", "energy": 16,
                  "ingredients": [{"name": "iron-plate", "amount": 5}], "products": [{"name": "steel-plate", "amount": 1}]},
                 {"name": "assembling-machine-2", "category": "crafting", "energy": 0.5,
                  "ingredients": [{"name": "steel-plate", "amount": 2}, {"name": "assembling-machine-1", "amount": 1}],
                  "products": [{"name": "assembling-machine-2", "amount": 1}]}]
        better = site(recipes=recipes + made + steel, machines={"assembling-machine-1": {"speed": 0.5},
                                                                "assembling-machine-2": {"speed": 0.75}})
        protos = [e["prototype"] for e in wall_block.plan_block(better, mapped)["entities"]]
        self.assertNotIn("assembling-machine-2", protos)
        self.assertIn("assembling-machine-1", protos)

    def test_power_and_a_lab_come_before_the_first_turrets_and_the_whole_wall_comes_later(self):
        # (live game 2026-10-22: turrets right after the coal loop kept every engineer off power for 30 minutes)
        from companion.catalog_planner import team_opening
        order = [purpose for purpose, _ in team_opening(site())]
        first_turrets, first_wall = order.index("plot-turrets"), order.index("plot-wall")
        self.assertLess(order.index("steam-power"), first_turrets)
        self.assertLess(order.index("research-lab"), first_turrets)
        # (one-engineer hour 2026-10-24: turrets straight after the lab starved on the starter iron furnaces)
        self.assertLess(order.index("iron-smelting-expansion"), first_turrets)
        self.assertLess(order.index("red-science-automation"), first_turrets)
        self.assertLess(order.index("red-science-automation"), first_wall)
        self.assertEqual(order.count("plot-turrets"), 2)
        self.assertGreater(len(order) - order[::-1].index("plot-wall") - 1, order.index("electric-copper-mining"))

    def test_with_the_nests_far_off_the_first_turret_plan_is_a_start_not_the_whole_edge(self):
        far = site(threats=[{"x": -380, "y": -1500}])
        plan = perimeter.plan_turrets(far, world())
        self.assertEqual(team_area.misplaced(plan, far), [])
        self.assertEqual(len(plan["stages"]), perimeter.FIRST_TURRETS)
        self.assertEqual(sum(1 for e in plan["entities"] if e["prototype"] == "gun-turret"), perimeter.FIRST_TURRETS)
        held = site(threats=[{"x": -380, "y": -1500}], base=[{"id": "p", "purpose": "plot-turrets",
                                                             "box": [-420, -572, -340, -566]}])
        self.assertEqual(len(perimeter.plan_turrets(held, world())["stages"]), perimeter.TURRETS_PER_PLAN)
        attacked = site(threats=[{"x": -380, "y": -1500}], social={"recent_attacks": 2})
        self.assertEqual(len(perimeter.plan_turrets(attacked, world())["stages"]), perimeter.TURRETS_PER_PLAN)

    def test_the_planner_holds_the_edge_and_its_plan_passes_the_team_check(self):
        attacked = site(social={"recent_attacks": 1})
        with tempfile.TemporaryDirectory() as folder:
            planner = CatalogPlanner(bp.Catalog(folder), log=lambda _: None)
            planner.world = lambda s: world()
            plan = planner(attacked)
            self.assertEqual(plan["purpose"], "plot-turrets")
            self.assertEqual(team_area.misplaced(plan, attacked), [])
            self.assertNotIn("retire", plan)
            # with starter turrets by the factory: taken down once these stand, for the next groups
            guarded = site(social={"recent_attacks": 1}, request_id="1:10",
                           base=[{"purpose": "starter-turrets", "box": [-410, -410, -400, -408]}])
            self.assertEqual(planner(guarded)["retire"], ["starter-turrets"])

    def test_on_a_real_map_with_a_plot_round_home(self):
        dump = json.loads((ROOT / "tests" / "fixtures" / "k-blue-map-256.json").read_text(encoding="utf-8"))
        # home at (0, 0) in the middle of a plot whose north and east edges are the team area's border
        dump["team"] = {"plot": [-192, -192, 192, 192], "area": [-576, -192, 192, 576], "no_build": []}
        s = site(origin={"x": -32, "y": -32},
                 team={"plot": [-160, -160, 224, 224], "area": [-544, -160, 224, 608], "no_build": []},
                 threats=[{"x": 300, "y": -100}])               # (local: nests out past the east edge)
        self.assertEqual([e.name for e in perimeter.edges(s)], ["north", "east"])
        taken = team_area.forbidden(dump) | set().union(*[footprint([Placed(e["n"], e["x"], e["y"])])
                                                        for e in dump["entities"] if e.get("t") not in ("tree",)])
        for plan_for in (perimeter.plan_turrets, perimeter.plan_wall):
            plan = plan_for(s, dump)
            self.assertEqual(team_area.misplaced(plan, s), [])
            for t in tiles_of(plan):
                world_tiles = {(x - 32, y - 32) for x, y in t}
                self.assertFalse(world_tiles & taken)
        east = perimeter.plan_turrets(s, dump)
        self.assertTrue(all(e["position"]["x"] - 32 > 150 for e in east["entities"] if e["prototype"] == "gun-turret"))


class RingTests(unittest.TestCase):
    """The wall ring round the factory (the user, 2026-10-26: the plot's whole edge is too long to hold early on,
    though right for later), moved out as the factory grows."""

    FACTORY = [{"purpose": "red-science-automation", "box": [-430, -425, -380, -300], "buildings": 9,
                "machines_box": [-420, -420, -380, -390], "machines": {"lab": 1, "assembling-machine-1": 2}},
               {"purpose": "iron-smelting", "box": [-500, -410, -420, -380], "buildings": 30,
                "machines_box": [-440, -410, -425, -380], "machines": {"stone-furnace": 8, "burner-mining-drill": 8}}]
    OUTPOST = {"purpose": "electric-coal-supply", "box": [-300, -300, -240, -240], "buildings": 40,
               "machines_box": [-260, -260, -240, -240], "machines": {"electric-mining-drill": 12}}
    RING = (-464, -444, -356, -356)

    def test_the_first_ring_goes_round_the_factory_with_room_to_grow(self):
        s = site(base=self.FACTORY + [self.OUTPOST])
        self.assertEqual(perimeter.core(s), (-440, -420, -380, -380))   # its machines, not the belts out; not the outpost
        self.assertEqual(perimeter.ring_due(s), (self.RING, 1))
        edges = perimeter.edges(s)
        self.assertEqual([e.name for e in edges], ["north", "east", "south", "west"])   # closed
        self.assertLess(sum(e.hi - e.lo + 1 for e in edges), sum(e.hi - e.lo + 1 for e in perimeter.plot_edges(s)))
        plan = perimeter.plan_wall(s, world())
        PerimeterTests.check(self, plan, s)
        walls = [e for e in plan["entities"] if e["prototype"] == "stone-wall"]
        self.assertTrue(walls and all(w["position"]["y"] == -441.5 for w in walls))   # the north side: nests north
        self.assertTrue(all(-464 < w["position"]["x"] < -356 for w in walls))
        self.assertEqual({k: plan["keep"][k] for k in ("ring", "ring_gen", "ring_side", "ring_layer")},
                         {"ring": list(self.RING), "ring_gen": 1, "ring_side": "north", "ring_layer": 1})
        # with no factory yet to go round: the plot's outer edges, as the team's ring
        self.assertEqual(perimeter.ring_due(site()), (tuple(PLOT), 1))
        self.assertEqual([e.name for e in perimeter.edges(site())], ["north", "west"])

    def test_a_side_near_the_plots_edge_goes_to_it(self):
        near = [{**self.FACTORY[0], "machines_box": [-540, -540, -500, -500]}]
        ring, _ = perimeter.ring_due(site(base=near))
        self.assertEqual(ring, (-576, -576, -476, -476))   # on the plot's outer edges there: the team's ring

    def test_turrets_spaced_alike_on_every_side_the_nests_side_first(self):
        # (the user's play, 2026-10-30, lesson 10: 12-17.5 apart on every side; its attacks came from sides with no
        # known nests)
        s = site(base=self.FACTORY + [line("north", 100, ring=self.RING)])
        count = {}
        spots = perimeter._spots(s, perimeter.edges(s), [], perimeter._enemy(None, s), GUN)
        for spot in spots:
            count[spot.edge.name] = count.get(spot.edge.name, 0) + 1
        self.assertLessEqual(abs(count["north"] - count["south"]), 1)     # (a square ring: as many each side)
        self.assertEqual(spots[0].edge.name, "north")                      # (north faces the nests, y -700: first)
        self.assertTrue(all(spot.missing == 1 for spot in perimeter._spots(s, perimeter.edges(s), [], [], GUN)))
        # what the ring lacks, less once turrets stand
        before = perimeter.turrets_wanted(s)
        self.assertEqual(before, sum(count.values()))
        plan = perimeter.plan_turrets(s, world())
        guards = [{"x": e["position"]["x"], "y": e["position"]["y"], "name": "gun-turret", "kind": "ammo-turret",
                   "ammo": 10} for e in plan["entities"] if e["prototype"] == "gun-turret"]
        self.assertEqual(perimeter.turrets_wanted(site(base=s["base"], guards=guards)), before - len(guards))

    def test_a_hot_stretch_gets_turrets_side_by_side(self):
        s = site(base=self.FACTORY + [line("north", 100, ring=self.RING)])
        north = [sp for sp in perimeter._spots(s, perimeter.edges(s), [], [], GUN) if sp.edge.name == "north"]
        x = north[len(north) // 2].along
        board = {"attacks": [{"x": x, "y": -446, "tick": 100, "count": 20}, {"x": x + 1, "y": -447, "tick": 200}]}
        hot = site(base=s["base"], threat_board=board)
        plan = perimeter.plan_turrets(hot, world())
        guns = sorted(e["position"]["x"] for e in plan["entities"] if e["prototype"] == "gun-turret")
        there = [g for g in guns if abs(g - x) <= 6]
        self.assertEqual(len(there), 2)                    # two hits there: two side by side
        self.assertLessEqual(there[1] - there[0], 3.5)
        rest = [g for g in guns if g not in there]
        self.assertTrue(rest and all(abs(a - b) > 6 for a in rest for b in guns if a != b))   # elsewhere spread out

    def test_the_ring_moves_out_as_the_factory_grows_and_the_old_sides_come_down_once_the_new_one_stands(self):
        west = [{"purpose": "red-science-automation", "box": [-540, -420, -500, -380], "buildings": 9,
                 "machines_box": [-540, -420, -500, -380], "machines": {"lab": 1, "assembling-machine-1": 2}}]
        ring1, _ = perimeter.ring_due(site(base=west))
        self.assertEqual(ring1, (-576, -444, -476, -356))          # (its west side on the plot's edge)
        old = {e.name: e.hi - e.lo + 1 for e in perimeter.ring_edges(site(), ring1)}
        held = [line(side, n, ring=ring1) for side, n in old.items()]
        self.assertEqual(perimeter.ring_due(site(base=west + held)), (ring1, 1))   # nothing outgrown: the ring it holds
        grown = west + [{"purpose": "green-science-automation", "box": [-540, -452, -520, -440], "buildings": 3,
                         "machines_box": [-540, -452, -520, -440], "machines": {"assembling-machine-1": 2}}]
        s = site(base=grown + held)
        ring2, gen = perimeter.ring_due(s)
        self.assertEqual(gen, 2)
        self.assertLess(ring2[1], ring1[1])                          # farther out where it grew
        self.assertTrue(ring2[0] <= ring1[0] and ring2[2] >= ring1[2] and ring2[3] >= ring1[3])   # never smaller
        self.assertEqual(ring2[0], -576)                             # the side on the plot's edge stays
        self.assertEqual(sorted(perimeter.superseded(s)), ["ring:1:east", "ring:1:north", "ring:1:south"])
        plan = perimeter.plan_wall(s, world())
        self.assertEqual(plan["keep"]["ring_gen"], 2)
        self.assertNotIn("retire", plan)                             # (the new ring is not walled yet)
        # the new ring walled (the west side's old walls on its line count): the old sides come down, not before
        new = [line(e.name, e.hi - e.lo + 1 - (old["west"] if e.name == "west" else 0), ring=ring2, gen=2)
               for e in perimeter.ring_edges(s, ring2)]
        done = site(base=grown + held + new)
        self.assertEqual(perimeter.ring_due(done), (ring2, 2))
        self.assertTrue(perimeter.walled(done))
        after = {}
        perimeter._retire_moved(done, after)
        self.assertEqual(sorted(after["retire"]), ["ring:1:east", "ring:1:north", "ring:1:south"])
        half = site(base=grown + held + new[:2])
        perimeter._retire_moved(half, after := {})
        self.assertEqual(after, {})

    def test_a_heavier_group_grown_up_far_off_does_not_drag_the_ring_out_to_it(self):
        ring1, _ = perimeter.ring_due(site(base=self.FACTORY))
        held = [line(e.name, e.hi - e.lo + 1, ring=ring1) for e in perimeter.ring_edges(site(), ring1)]
        far = {"purpose": "bus-base", "box": [-300, -300, -240, -240], "buildings": 300,
               "machines_box": [-300, -300, -240, -240], "machines": {"assembling-machine-2": 30, "lab": 10}}
        s = site(base=self.FACTORY + held + [far])
        self.assertEqual(perimeter.core(s), (-300, -300, -240, -240))            # (more at stake out there now)
        self.assertEqual(perimeter.ring_due(s), (ring1, 1))                       # an outpost: the ring stays
        near = {**far, "box": [-370, -420, -330, -380], "machines_box": [-370, -420, -330, -380]}
        ring2, gen = perimeter.ring_due(site(base=self.FACTORY + held + [near]))   # grown up next to the factory
        self.assertEqual(gen, 2)
        self.assertGreater(ring2[2], ring1[2])                                    # out east, round it too
        # nothing left standing in the ring it holds (its plans gone from the export): it holds as it is (review)
        self.assertEqual(perimeter.ring_due(site(base=held + [far])), (ring1, 1))
        self.assertEqual(perimeter.superseded(site(base=held + [far])), [])


def built(plan, entities, base):
    """The plan put up on the map and in the base the game exports (local = world here)."""
    kinds = {"stone-wall": ("wall", 1), "gate": ("gate", 1), "gun-turret": ("ammo-turret", 2),
             "laser-turret": ("electric-turret", 2), "small-electric-pole": ("electric-pole", 1)}
    for e in plan["entities"]:
        kind, size = kinds[e["prototype"]]
        entities.append({"n": e["prototype"], "t": kind, "x": e["position"]["x"], "y": e["position"]["y"], "w": size,
                         "h": size, "d": e.get("direction", 0), "f": "npc-faction-1"})
    base.append({"purpose": plan["purpose"], "buildings": len(plan["entities"]), "keep": plan.get("keep"),
                 "id": plan["plan_id"]})


def guards_of(entities):
    return [{"x": e["x"], "y": e["y"], "name": e["n"], "kind": e["t"], "ammo": 10} for e in entities
            if e["t"] == "ammo-turret"]


class RingReviewTests(unittest.TestCase):
    """What the review found (Codex, thread wall-ring, 2026-10-26), played out plan by plan."""

    LAB = {"purpose": "research-lab", "box": [-400, -400, -397, -397], "machines_box": [-400, -400, -397, -397],
           "machines": {"lab": 1}, "buildings": 1}

    def test_a_small_ring_with_no_gate_to_make_yet_is_walled_all_round(self):
        gateless = {**DEFENSE, "gates": []}
        entities, base = [], [self.LAB]
        for _ in range(8):
            s = site(base=base, defense=gateless)
            try:
                plan = perimeter.plan_wall(s, world(entities))
            except perimeter.Done:
                break
            built(plan, entities, base)
        sides = {b["keep"]["ring_side"] for b in base if b["purpose"] == "plot-wall"}
        self.assertEqual(sides, {"north", "east", "south", "west"})
        self.assertTrue(perimeter.walled(site(base=base, defense=gateless)))

    def test_layers_on_a_side_that_stayed_do_not_stand_in_for_a_new_side(self):
        west = [{"purpose": "red-science-automation", "box": [-540, -420, -500, -380], "buildings": 9,
                 "machines_box": [-540, -420, -500, -380], "machines": {"lab": 1, "assembling-machine-1": 2}}]
        ring1, _ = perimeter.ring_due(site(base=west))
        old = {e.name: e.hi - e.lo + 1 for e in perimeter.ring_edges(site(), ring1)}
        held = [{"purpose": "plot-wall", "buildings": n, "keep": {"ring": list(ring1), "ring_gen": 1, "ring_side": side,
                                                                  "ring_layer": layer}}
                for side, n in old.items() for layer in (1, 2, 3, 4)]
        grown = west + [{"purpose": "green-science-automation", "box": [-540, -452, -520, -440], "buildings": 3,
                         "machines_box": [-540, -452, -520, -440], "machines": {"assembling-machine-1": 2}}]
        ring2, _ = perimeter.ring_due(site(base=grown + held))
        some = [line(e.name, e.hi - e.lo + 1, ring=ring2, gen=2) for e in perimeter.ring_edges(site(), ring2)
                if e.name in ("north", "east")]
        partly = site(base=grown + held + some)
        self.assertFalse(perimeter.walled(partly))                    # south and the west's new stretch still to come
        perimeter._retire_moved(partly, after := {"plan_id": "x:y", "entities": [], "stages": []})
        self.assertNotIn("retire", after)

    def test_the_old_sides_that_moved_come_down_once_the_new_ring_stands_and_the_side_that_stayed_does_not(self):
        west = [{"purpose": "red-science-automation", "box": [-540, -420, -500, -380], "buildings": 9,
                 "machines_box": [-540, -420, -500, -380], "machines": {"lab": 1, "assembling-machine-1": 2}}]
        entities, base = [], list(west)
        for _ in range(8):                                             # the first ring, its north side first
            try:
                plan = perimeter.plan_wall(site(base=base), world(entities))
            except perimeter.Done:
                break
            built(plan, entities, base)
        corner = (-574, -442)                                          # where the north side met the plot's west edge
        owner = [b for b in base if b.get("keep") and b["keep"]["ring_side"] == "north"]
        self.assertTrue(owner and any((math.floor(e["x"]), math.floor(e["y"])) == corner for e in entities))
        base.append({"purpose": "green-science-automation", "box": [-540, -452, -520, -440], "buildings": 3,
                     "machines_box": [-540, -452, -520, -440], "machines": {"assembling-machine-1": 2}})
        retire = None
        for _ in range(12):                                            # the next ring, out to the north
            try:
                plan = perimeter.plan_wall(site(base=base), world(entities))
            except perimeter.Done:
                break
            built(plan, entities, base)
            retire = retire or plan.get("retire")
        self.assertIn("ring:1:north", retire or [])                   # (the game leaves the corner standing:
        self.assertNotIn("ring:1:west", retire)                       # test_retire)

    def test_a_hot_stretch_fills_up_and_the_other_sides_get_theirs(self):
        ring = (-464, -444, -356, -356)
        s0 = site(base=RingTests.FACTORY + [line("north", 104, ring=ring)])
        north = [sp for sp in perimeter._spots(s0, perimeter.edges(s0), [], [], GUN) if sp.edge.name == "north"]
        x = north[len(north) // 2].along
        board = {"attacks": [{"x": x + d, "y": -446, "tick": 100 + d} for d in (-2, -1, 0, 1)]}
        entities, base = [], list(s0["base"])
        for _ in range(12):
            s = site(base=base, threat_board=board, guards=guards_of(entities))
            try:
                plan = perimeter.plan_turrets(s, world(entities), shield=False)
            except perimeter.Done:
                break
            built(plan, entities, base)
        there = [e for e in entities if e["t"] == "ammo-turret" and abs(e["x"] - x) <= north[0].room and e["y"] < -420]
        self.assertEqual(len(there), perimeter.MAX_STACK)              # four hits there: four side by side
        sides = {b["keep"]["ring_side"] for b in base if b["purpose"] == "plot-turrets"}
        self.assertEqual(sides, {"north", "east", "south", "west"})
        self.assertEqual(perimeter.turrets_wanted(site(base=base, threat_board=board, guards=guards_of(entities))), 0)


if __name__ == "__main__":
    unittest.main()
