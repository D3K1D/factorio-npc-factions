"""Whether a vehicle of its own pays an engineer (companion/vehicle_brain.py), and which it asks for."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion import vehicle_brain as vehicle  # noqa: E402

TANK = {"item": "tank", "name": "tank", "parts": [{"name": "engine-unit", "amount": 32, "have": 0, "by": "machine"},
                                                  {"name": "steel-plate", "amount": 50, "have": 80, "by": "furnace"}]}
CAR = {"item": "car", "name": "car", "parts": [{"name": "engine-unit", "amount": 8, "have": 10, "by": "machine"},
                                               {"name": "steel-plate", "amount": 5, "have": 80, "by": "furnace"}]}


def request(walk=20.0, can_make=(TANK, CAR), vehicle_now=None, pockets=None, spawners=0, tick=1000,
            reason="routine", agenda=(), npc=1, speed=None):
    travel = {"walk_minutes_per_hour": walk, "per_hour": 6, "average_tiles": 240, "can_make": list(can_make)}
    if speed:
        travel["drive_speed"] = speed
    if vehicle_now:
        travel["vehicle"] = vehicle_now
    if pockets:
        travel["in_pockets"] = pockets
    return {"request_id": "1:s1", "npc_id": npc, "reason": reason,
            "situation": {"tick": tick, "travel": travel, "threats": {"unit-spawner": spawners},
                          "agenda": list(agenda)}}


class VehicleTests(unittest.TestCase):
    def setUp(self):
        vehicle._asked.clear()

    def test_long_walks_with_the_parts_at_hand_ask_for_the_sturdiest_it_can_make(self):
        out = vehicle.intentions(request())
        self.assertEqual([(i["tool"], i["args"]["focus"], i["args"]["vehicle"]) for i in out],
                         [("arm_up", "vehicle", "car")])     # the tank is short of engines: the car
        j = vehicle.judge(request()["situation"])
        self.assertEqual(j["saved_minutes_per_hour"], 8.0)   # 20 minutes on foot, 0.15 against 0.25
        both = dict(TANK, parts=[dict(TANK["parts"][0], have=40), TANK["parts"][1]])
        out = vehicle.intentions(request(can_make=(both, CAR), npc=2))
        self.assertEqual(out[0]["args"]["vehicle"], "tank")  # with its engines at hand: the tank, listed first

    def test_short_walks_one_it_has_already_or_missing_parts_ask_for_nothing(self):
        self.assertEqual(vehicle.intentions(request(walk=5)), [])                 # 2 minutes saved: not worth it
        self.assertEqual(vehicle.intentions(request(vehicle_now={"name": "car"})), [])
        self.assertEqual(vehicle.intentions(request(pockets="car")), [])
        no_engines = dict(CAR, parts=[dict(CAR["parts"][0], have=2), CAR["parts"][1]])
        self.assertEqual(vehicle.intentions(request(can_make=(TANK, no_engines))), [])
        j = vehicle.judge(request(can_make=(TANK, no_engines))["situation"])
        self.assertEqual(j["blocked_on"], ["engine-unit"])                         # what is short, said
        self.assertIn("engine-unit", vehicle.describe(request(can_make=(TANK, no_engines))["situation"]))

    def test_nests_near_home_tip_a_closer_call(self):
        self.assertEqual(vehicle.intentions(request(walk=8)), [])                 # 3.2 saved
        self.assertEqual(len(vehicle.intentions(request(walk=8, spawners=4))), 1)  # + 1 for the nests

    def test_its_own_slower_drives_count_against_it(self):
        self.assertTrue(vehicle.judge(request(walk=12)["situation"])["pays"])        # 4.8 at 0.25
        self.assertFalse(vehicle.judge(request(walk=12, speed=0.18)["situation"])["pays"])   # 2.0 at 0.18

    def test_it_asks_once_in_a_while_not_every_wake(self):
        self.assertEqual(len(vehicle.intentions(request(tick=1000))), 1)
        self.assertEqual(vehicle.intentions(request(tick=2000)), [])
        self.assertEqual(len(vehicle.intentions(request(tick=1000 + vehicle.ASK_EVERY))), 1)

    def test_the_reflex_adds_it_on_routine_wakes_unless_already_asked(self):
        decided = [{"tool": "remember", "args": {"note": "x"}}]
        self.assertEqual([i["tool"] for i in vehicle.with_reflex(request(), decided)], ["remember", "arm_up"])
        vehicle._asked.clear()
        asked = [{"tool": "arm_up", "args": {"focus": "vehicle", "vehicle": "car"}}]
        self.assertEqual(vehicle.with_reflex(request(), asked), asked)
        self.assertEqual(vehicle.with_reflex(request(agenda=asked), decided), decided)
        self.assertEqual(vehicle.with_reflex(request(reason="attacked"), decided), decided)

    def test_each_ask_is_written_down_with_what_it_saw(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {"NPC_FACTIONS_DATA": folder}):
            vehicle.intentions(request())
            rows = (Path(folder) / "decisions" / vehicle.LOG_NAME).read_text(encoding="utf-8").splitlines()
        row = json.loads(rows[0])
        self.assertEqual(row["intentions"][0]["args"]["vehicle"], "car")
        self.assertEqual(row["travel"]["walk_minutes_per_hour"], 20.0)
        self.assertTrue(row["judged"]["pays"])


if __name__ == "__main__":
    unittest.main()
