"""The lane ledger (companion/lane_ledger.py): every belt line tracked on its own, what it carries and what is claimed
of it, its signals, and claims granted only where the headroom covers them (the user, 2026-10-28)."""
import json
import os
import tempfile
import unittest

from companion import belt_lines, lane_ledger
from companion.modules import Placed, SOUTH
from test_belt_lines import gears, measured, two_belt_bus


def lanes():
    ops = {op["id"]: op for op in belt_lines.bus_lanes(two_belt_bus(), two_belt_bus().entities())}
    return ops["iron-plate@0,0"], ops["iron-plate@1,0"]


def trunk(supply=1.0, claims=None):
    tiles = [(0, y, SOUTH) for y in range(30)]
    built = [Placed("transport-belt", x + 0.5, y + 0.5, d) for x, y, d in tiles]
    belt_lines.record_line(built, "coal", supply, 0.5, (0, 30))
    op = dict(built[0].line_op)
    op.pop("key"), op.pop("rate")
    return {**op, "claims": claims if claims is not None else {"a": 0.5, "b": 0.7}}


def book(*lines, site=None):
    return lane_ledger.LaneBook.from_site(site or {}, {"belt_lines": list(lines)})


def signals(*lines, site=None):
    return [(s["signal"], s.get("what")) for s in book(*lines, site=site).signals()]


class LaneTests(unittest.TestCase):

    def test_a_lane_s_carry_claims_and_headroom(self):
        # 0.9 a second measurably going on (6% of a yellow belt), nowhere backed up, 1.2 claimed
        lane = book(measured(trunk(), {0: 0.06, 1: 0.06, 2: 0.05, 5: 0.02})).lanes()[0]
        self.assertEqual((lane.carry_from, lane.capacity), ("measured", 15.0))
        self.assertAlmostEqual(lane.carry, 0.9)
        self.assertAlmostEqual(lane.headroom, 0.9 - 1.2)
        # backed up somewhere: it gives at least its design (less is taken than comes)
        backed = book(measured(trunk(supply=3.0), {0: 0.06, 5: 0.95})).lanes()[0]
        self.assertEqual((backed.carry_from, backed.carry), ("at least", 3.0))
        # not sampled long enough: its design
        young = book({**trunk(supply=2.0), "ok": True}).lanes()[0]
        self.assertEqual((young.carry_from, young.carry), ("design", 2.0))

    def test_short_while_under_capacity_is_short_and_wants_supply_not_a_belt(self):
        self.assertEqual(signals(measured(trunk(), {0: 0.06, 5: 0.02})), [("short", None), ("expand", "supply")])

    def test_a_backed_up_lane_is_never_short_and_a_roomy_one_is_plenty(self):
        self.assertEqual(signals(measured(trunk(supply=3.0), {0: 0.06, 5: 0.95})), [("plenty", None)])
        # coal on one lane only, backed up: half full both lanes, its fuller lane full
        self.assertEqual(signals(measured(trunk(supply=3.0), {0: 0.5, 5: 0.5}, one_lane=True)), [("plenty", None)])

    def test_claims_near_the_belt_s_capacity_want_the_next_belt(self):
        heavy = {**trunk(supply=15.0, claims={"a": 13.0}), "ok": True}
        self.assertEqual(signals(heavy), [("expand", "belt")])
        # short of supply too: supply first, not the next belt as well (Codex, L-2)
        starved = measured(trunk(supply=15.0, claims={"a": 13.0}), {0: 0.01, 5: 0.0})
        self.assertEqual(signals(starved), [("short", None), ("expand", "supply")])

    def test_a_slow_queue_is_not_read_as_flow(self):
        # (a 1/s coal trunk read 49% full in a short run, 2026-10-29: a queue creeping on, not 7.4/s moving)
        lane = book(measured(trunk(supply=1.0), {0: 0.49, 5: 0.49})).lanes()[0]
        self.assertEqual((lane.carry_from, lane.carry), ("at least", 1.0))

    def test_backed_up_with_no_design_its_carry_is_unknown_and_nothing_is_granted_from_it(self):
        side = measured(trunk(supply=0.0, claims={"a": 0.5}), {0: 0.95, 5: 0.95})
        b = book(side)
        self.assertEqual(b.lanes()[0].carry_from, "unknown")
        self.assertEqual(b.signals(), [])
        self.assertFalse(b.grant(0.1, "x", line=b.lanes()[0].id).ok)
        self.assertFalse(b.grant(0.1, "x", item="coal").ok)


class GroupTests(unittest.TestCase):

    def test_a_belt_running_dry_beside_a_full_one_asks_for_a_balancer_where_it_runs_dry(self):
        west, east = lanes()
        out = book(measured(west, {1: 0.6, 2: 0.6, 6: 0.55, 10: 0.7, 15: 0.9, 39: 1.0}),
                   measured(east, {1: 0.6, 2: 0.6, 6: 0.3, 10: 0.04, 15: 0.02, 39: 0.0})).signals()
        need = out[0]
        self.assertEqual((need["signal"], need["group"], need["dry"]), ("balance", "iron-plate@0,0", "iron-plate@1,0"))
        self.assertEqual((need["fed_row"], need["dry_row"]), (6, 10))      # gears-3 (row 11) goes without
        self.assertIn("runs nearly empty by row 10", need["why"])

    def test_the_starved_module_after_it_is_named_as_evidence(self):
        west, east = lanes()
        site = {"base": [{"stage": "bus-gears-3", "measured": {"minutes": 40, "shares": {"starved": 0.6}}}]}
        need = book(measured(west, {1: 0.6, 10: 0.7, 39: 1.0}), measured(east, {1: 0.6, 10: 0.04, 39: 0.0}),
                    site=site).signals()[0]
        self.assertIn("gears-3 (60% of the time)", need["why"])

    def test_no_balancer_when_the_dry_belt_has_no_taps_after_it(self):
        west, east = lanes()   # (the west belt's only tap is at row 7)
        out = book(measured(west, {1: 0.6, 10: 0.05, 39: 0.0}), measured(east, {1: 0.6, 10: 0.7, 39: 1.0})).signals()
        self.assertNotIn("balance", [s["signal"] for s in out])

    def test_a_balancer_that_refills_it_before_its_next_tap_leaves_nothing_to_do(self):
        # (the re-balancer at row 16 refills the east belt by row 39, the probe just above its next tap, at row 40)
        west, east = lanes()
        west["claims"], east["claims"] = {}, {"late@1,40": 2.0}
        out = book(measured(west, {1: 0.6, 10: 0.7, 15: 0.8, 39: 0.6}),
                   measured(east, {1: 0.6, 10: 0.04, 15: 0.02, 39: 0.5})).signals()
        self.assertNotIn("balance", [s["signal"] for s in out])

    def test_a_belt_dry_again_after_a_stretch_that_starved_nothing_is_still_found(self):
        # (Codex, L-2: dry at row 10 with no tap there, refilled by 15, dry again by 39 with a tap after it)
        west, east = lanes()
        west["claims"], east["claims"] = {}, {"late@1,40": 2.0}
        need = book(measured(west, {1: 0.6, 10: 0.7, 15: 0.8, 39: 0.6}),
                    measured(east, {1: 0.6, 10: 0.04, 15: 0.3, 39: 0.02})).signals()[0]
        self.assertEqual((need["signal"], need["dry_row"], need["fed_row"]), ("balance", 39, 15))

    def test_an_untapped_dry_belt_does_not_hide_a_tapped_one(self):
        # (Codex, L-1: a four-belt group, two inner belts empty and untapped, the outer one dry with taps after it)
        rows = {1: 0.0, 10: 0.0, 39: 0.0}
        base = dict(lanes()[1], claims={})
        belts = [measured({**base, "id": f"iron-plate@{x},0", "probes": [[x, r, SOUTH] for r in rows]},
                          {1: 0.7, 10: f, 39: f}) for x, f in ((0, 0.7), (1, 0.0), (2, 0.0))]
        belts.append(measured({**base, "id": "iron-plate@3,0", "claims": {"m@3,20": 2.0},
                               "probes": [[3, r, SOUTH] for r in rows]}, {1: 0.7, 10: 0.05, 39: 0.0}))
        need = book(*belts).signals()[0]
        self.assertEqual((need["signal"], need["dry"]), ("balance", "iron-plate@3,0"))

    def test_a_group_carries_what_its_belts_carry_together_at_one_row(self):
        # (each belt's own busiest row would count what a balancer moved twice: Codex, L-1)
        west, east = lanes()
        g = book(measured(west, {1: 0.4, 39: 0.2}), measured(east, {1: 0.0, 39: 0.2})).groups()[0]
        self.assertEqual(g.carry_from, "measured")
        self.assertAlmostEqual(g.carry, 6.0)                                # not 0.4 x 15 + 0.2 x 15 = 9

    def test_a_group_short_of_its_claims_is_short_as_a_whole(self):
        west, east = lanes()
        out = book(measured(west, {1: 0.12, 10: 0.05, 39: 0.0}), measured(east, {1: 0.12, 10: 0.04, 39: 0.0}))
        short = next(s for s in out.signals() if s["signal"] == "short")
        self.assertEqual((short["group"], short["claims"]), ("iron-plate@0,0", 6.0))
        self.assertAlmostEqual(short["carry"], 3.6)


class GrantTests(unittest.TestCase):

    def test_a_claim_is_granted_only_where_the_headroom_covers_it_and_held_for_the_plan(self):
        b = book(measured(trunk(supply=3.0, claims={"a": 0.5}), {0: 0.1, 5: 0.05}))   # 1.5 going on, 0.5 claimed
        first = b.grant(0.8, "furnaces", item="coal")
        self.assertTrue(first.ok)
        self.assertAlmostEqual(first.headroom, 1.0)
        again = b.grant(0.8, "boilers", item="coal")                        # the first one's grant is held
        self.assertFalse(again.ok)
        self.assertIn("refused", again.why)
        self.assertIn("0.20/s left, 0.80/s asked", again.why)
        self.assertEqual([g["ok"] for g in b.grants], [True, False])

    def test_a_group_grants_from_all_its_belts_and_a_planned_lane_counts_too(self):
        west, east = lanes()                                  # design: 6 each, 2 and 4 claimed: 4 and 2 left
        b = book({**west, "ok": True}, {**east, "ok": True})
        # (the group has 6 left, but no belt 5 alone: one belt is tapped, so a balancer first; Codex, L-2)
        five = b.grant(5.0, "new", group="iron-plate@0,0")
        self.assertFalse(five.ok)
        self.assertIn("a balancer first", five.why)
        first = b.grant(4.0, "new", group="iron-plate@0,0")
        self.assertEqual((first.ok, first.line), (True, "iron-plate@0,0"))   # the belt that takes it, named
        self.assertEqual(b.grant(2.0, "next", group="iron-plate@0,0").line, "iron-plate@1,0")
        self.assertFalse(b.grant(0.5, "more", group="iron-plate@0,0").ok)
        b.plan_lane("copper-plate@4,0", "copper-plate", 7.5)
        self.assertTrue(b.grant(7.0, "cable", item="copper-plate").ok)
        self.assertFalse(b.grant(1.0, "more cable", line="copper-plate@4,0").ok)
        self.assertFalse(b.grant(1.0, "x", item="sulfur").ok)

    def test_a_group_s_claim_goes_on_a_belt_the_consumer_can_reach(self):
        # (Codex, L-2: an east module taps the east belt, however much the west one has spare)
        west, east = lanes()                                   # design: 4 left on the west belt, 2 on the east
        b = book({**west, "ok": True}, {**east, "ok": True})
        near = b.grant(1.0, "east module", group="iron-plate@0,0", reach=["iron-plate@1,0"])
        self.assertEqual((near.ok, near.line), (True, "iron-plate@1,0"))
        far = b.grant(2.0, "east module 2", group="iron-plate@0,0", reach=["iron-plate@1,0"])
        self.assertFalse(far.ok)
        self.assertIn("a balancer first", far.why)

    def test_a_floor_under_heavy_claims_asks_for_supply_not_a_belt(self):
        # (Codex, L-2: 1/s by design, 13/s claimed, queued: no sign the supply for a second belt is there)
        heavy = measured(trunk(supply=1.0, claims={"a": 13.0}), {0: 0.49, 5: 0.49})
        self.assertEqual(signals(heavy), [("expand", "supply")])

    def test_a_grant_under_a_key_already_claimed_adds_to_it(self):
        # (Codex, L-2: the new grant replaced the built 2/s, and the line was booked past what goes on)
        b = book({**trunk(supply=3.0, claims={"a": 2.0}), "ok": True})
        self.assertTrue(b.grant(1.0, "a", item="coal").ok)
        self.assertAlmostEqual(b.lanes()[0].claimed, 3.0)
        self.assertFalse(b.grant(2.0, "b", item="coal").ok)

    def test_taps_with_no_probe_before_them_are_said_not_hidden(self):
        west, east = lanes()
        b = book(measured({**west, "unread": 3}, {1: 0.6, 39: 0.6}), measured({**east, "unread": 3}, {1: 0.6, 39: 0.6}))
        unseen = [s for s in b.signals() if s.get("what") == "unseen"]
        self.assertEqual((len(unseen), unseen[0]["unread"]), (1, 6))
        self.assertEqual(b.lanes()[0].row()["unread"], 3)

    def test_a_grant_on_one_belt_of_a_group_is_held_to_what_the_group_carries_together(self):
        # (Codex, L-2: two belts each reading 6 a second may be one 6 a balancer moved across)
        west, east = lanes()
        west["claims"], east["claims"] = {}, {}
        b = book(measured(west, {1: 0.4, 39: 0.0}), measured(east, {1: 0.0, 39: 0.4}))   # each 6, 6 together
        self.assertTrue(b.grant(5.0, "a", line="iron-plate@0,0").ok)
        refused = b.grant(5.0, "b", line="iron-plate@1,0")
        self.assertFalse(refused.ok)
        self.assertIn("together", refused.why)

    def test_a_queued_line_short_by_design_is_short_once_a_consumer_goes_without(self):
        west, east = lanes()
        b = book(measured(west, {1: 0.49, 39: 0.49}), measured(east, {1: 0.49, 39: 0.49}))
        self.assertNotIn("short", [s["signal"] for s in b.signals()])        # 12 a second on, 6 claimed: fine
        heavy = {**west, "claims": {"gears-2@0,7": 9.0}}
        site = {"base": [{"stage": "bus-gears-2", "measured": {"minutes": 40, "shares": {"starved": 0.5}}}]}
        out = book(measured(heavy, {1: 0.49, 39: 0.49}), measured(east, {1: 0.49, 39: 0.49}), site=site).signals()
        self.assertIn("short", [s["signal"] for s in out])

    def test_no_design_means_no_flow_however_it_reads(self):
        side = measured(trunk(supply=0.0, claims={"a": 0.5}), {0: 0.49, 5: 0.49})   # (a queue, or 7 a second?)
        b = book(side)
        self.assertEqual(b.lanes()[0].carry_from, "unknown")
        self.assertFalse(b.grant(0.1, "x", line=b.lanes()[0].id).ok)

    def test_the_site_export_s_lines_are_read_alike(self):
        op = trunk(supply=2.0)
        site_line = {k: v for k, v in op.items() if k not in ("tiles", "probes")}
        site_line.update(head=op["tiles"][0], tail=op["tiles"][-1])
        lane = lane_ledger.LaneBook.from_site({"belt_lines": [site_line]}).lanes()[0]
        self.assertEqual((lane.id, lane.carry, lane.line.tiles[-1][:2]), (op["id"], 2.0, (0, 29)))


class LogTests(unittest.TestCase):

    def test_signals_are_logged_when_they_change_and_the_strategist_hears_what_presses(self):
        lane_ledger.forget()
        with tempfile.TemporaryDirectory() as folder:
            old = os.environ.get("NPC_FACTIONS_DATA")
            os.environ["NPC_FACTIONS_DATA"] = folder
            try:
                short = {"npc_id": 3, "tick": 10, "belt_lines": [measured(trunk(), {0: 0.06, 5: 0.02})]}
                lane_ledger.remember(short)
                lane_ledger.remember({**short, "tick": 20})                 # the same signals: not again
                lane_ledger.remember({"npc_id": 3, "tick": 30,
                                      "belt_lines": [measured(trunk(supply=3.0), {0: 0.06, 5: 0.95})]})
                with open(os.path.join(folder, "decisions", "lanes.jsonl"), encoding="utf-8") as fh:
                    rows = [json.loads(r) for r in fh]
            finally:
                if old is None:
                    os.environ.pop("NPC_FACTIONS_DATA", None)
                else:
                    os.environ["NPC_FACTIONS_DATA"] = old
        self.assertEqual([r["tick"] for r in rows], [10, 30])
        self.assertEqual(rows[0]["signals"][0]["signal"], "short")
        self.assertIsNone(lane_ledger.note_for(3))                          # plenty is not news
        lane_ledger.remember(short)
        self.assertIn("supply first", lane_ledger.note_for(3))
        lane_ledger.remember({"npc_id": 3, "tick": 40, "belt_lines": []})  # (no lines now: nothing to say)
        self.assertIsNone(lane_ledger.note_for(3))
        lane_ledger.forget()

    def test_a_balance_need_that_moved_is_logged_again(self):
        lane_ledger.forget()
        west, east = lanes()
        with tempfile.TemporaryDirectory() as folder:
            old = os.environ.get("NPC_FACTIONS_DATA")
            os.environ["NPC_FACTIONS_DATA"] = folder
            try:
                for tick, rows in ((10, {1: 0.6, 2: 0.04, 10: 0.04, 39: 0.0}),
                                   (20, {1: 0.6, 2: 0.6, 10: 0.04, 39: 0.0})):
                    lane_ledger.remember({"npc_id": 5, "tick": tick, "belt_lines": [
                        measured(west, {1: 0.6, 2: 0.6, 10: 0.7, 39: 1.0}), measured(east, rows)]})
                with open(os.path.join(folder, "decisions", "lanes.jsonl"), encoding="utf-8") as fh:
                    ticks = [json.loads(r)["tick"] for r in fh]
            finally:
                if old is None:
                    os.environ.pop("NPC_FACTIONS_DATA", None)
                else:
                    os.environ["NPC_FACTIONS_DATA"] = old
        self.assertEqual(ticks, [10, 20])
        lane_ledger.forget()

    def test_a_signal_whose_numbers_change_is_logged_again(self):
        lane_ledger.forget()
        with tempfile.TemporaryDirectory() as folder:
            old = os.environ.get("NPC_FACTIONS_DATA")
            os.environ["NPC_FACTIONS_DATA"] = folder
            try:
                for tick, f in ((10, 0.06), (20, 0.03)):   # short both times, carrying 0.9 then 0.45
                    lane_ledger.remember({"npc_id": 4, "tick": tick, "belt_lines": [measured(trunk(), {0: f, 5: f})]})
                with open(os.path.join(folder, "decisions", "lanes.jsonl"), encoding="utf-8") as fh:
                    ticks = [json.loads(r)["tick"] for r in fh]
            finally:
                if old is None:
                    os.environ.pop("NPC_FACTIONS_DATA", None)
                else:
                    os.environ["NPC_FACTIONS_DATA"] = old
        self.assertEqual(ticks, [10, 20])
        lane_ledger.forget()



class SideTests(unittest.TestCase):
    """A belt with an item a side (the user, 2026-10-29): each side its own lane, judged and granted on its own."""

    def two(self, claims=None):
        tiles = [(0, y, SOUTH) for y in range(30)]
        built = [Placed("transport-belt", x + 0.5, y + 0.5, d) for x, y, d in tiles]
        belt_lines.record_line(built, "iron-plate", 4.0, 0.5, (0, 30), sides={"left": "iron-plate",
                               "right": "iron-gear-wheel"}, side_supply={"left": 3.0, "right": 1.0})
        op = dict(built[0].line_op)
        op.pop("key"), op.pop("rate")
        return {**op, "claims": claims if claims is not None else {"red@0,10#left": 1.0, "red@0,10#right": 0.5}}

    def test_each_side_is_a_lane_with_its_own_item_claims_and_half_the_belt(self):
        b = book({**self.two(), "ok": True})
        left, right = (b.lane(f"{self.two()['id']}#{s}") for s in ("left", "right"))
        self.assertEqual((left.item, left.carry, left.claimed, left.capacity), ("iron-plate", 3.0, 1.0, 7.5))
        self.assertEqual((right.item, right.carry, right.claimed), ("iron-gear-wheel", 1.0, 0.5))

    def test_a_grant_on_one_side_is_held_to_that_side(self):
        op = self.two()
        b = book({**op, "ok": True})
        self.assertTrue(b.grant(0.5, "green", line=op["id"], side="right").ok)
        refused = b.grant(0.5, "more", line=op["id"], side="right")      # (1.0 on it, 1.0 claimed now)
        self.assertFalse(refused.ok)
        self.assertTrue(b.grant(1.5, "more", line=op["id"], side="left").ok)
        self.assertIn("green#right", b.lane(op["id"] + "#right").claims)

    def test_each_side_is_measured_on_its_own_lane(self):
        op = self.two(claims={"red@0,10#right": 2.0})
        probes = [{"x": x, "y": y, "f": 0.3, "m": 0.55, "l": 0.55, "r": 0.05, "f5": 0.3, "m5": 0.55}
                  for x, y, *_ in op["probes"]]
        b = book({**op, "ok": True, "measured": {"minutes": 30, "rate": 15.0, "probes": probes}})
        right = b.lane(op["id"] + "#right")
        self.assertEqual(right.carry_from, "measured")
        self.assertAlmostEqual(right.carry, 0.05 * 7.5)
        self.assertIn(("short", None), [(s["signal"], s.get("what")) for s in b.signals() if s.get("line") == right.id])

    def test_a_tap_for_the_right_side_s_item_finds_the_belt_and_claims_that_side(self):
        op = self.two(claims={})
        dump = {"area": [-20, -20, 20, 40], "entities": [], "belt_lines": [{**op, "ok": True}]}
        blocked = {(0, y) for y in range(30)}
        with belt_lines.taps(True):   # (a splitter sends both items down the branch: only for a consumer taking both)
            self.assertIsNone(belt_lines.tap(dump, "iron-gear-wheel", (8, 15), 0.5, blocked))
            branch = belt_lines.tap(dump, "iron-gear-wheel", (8, 15), 0.5, blocked, mixed={"iron-plate": 1.0})
        self.assertIsNotNone(branch)
        ops = belt_lines.ops(branch)                                      # (both items it takes, each on its side)
        self.assertEqual(sorted((o["key"].rsplit("#", 1)[1], o["rate"]) for o in ops), [("left", 1.0), ("right", 0.5)])
        with belt_lines.taps(True):
            self.assertIsNone(belt_lines.tap(dump, "iron-gear-wheel", (8, 15), 1.5, blocked, mixed={"iron-plate": 1.0}))
            self.assertIsNone(belt_lines.tap(dump, "iron-gear-wheel", (8, 15), 0.5, blocked, mixed={}))
            # (the other side, 3 a second, has no room for 4 more)
            self.assertIsNone(belt_lines.tap(dump, "iron-gear-wheel", (8, 15), 0.5, blocked, mixed={"iron-plate": 4.0}))

    def test_a_planned_belt_with_an_item_a_side(self):
        b = lane_ledger.LaneBook([])
        b.plan_lane("mix@0,0", "iron-plate", 0, sides={"left": "iron-plate", "right": "copper-plate"},
                    side_supply={"left": 7.5, "right": 7.5})
        self.assertEqual(sorted(l.id for l in b.lanes()), ["mix@0,0#left", "mix@0,0#right"])
        self.assertTrue(b.grant(7.0, "circuits", line="mix@0,0", side="right").ok)
        self.assertFalse(b.grant(1.0, "more", line="mix@0,0", side="right").ok)

if __name__ == "__main__":
    unittest.main()
