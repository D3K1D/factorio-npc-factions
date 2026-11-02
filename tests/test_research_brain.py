"""The research weighing (companion/research_brain.py): the way to the rocket and what its priorities want, against
what each option costs."""
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

from companion import research_brain as rb  # noqa: E402

LAMP = {"name": "lamp", "units": 10, "packs": ["automation-science-pack"], "unlocks": ["small-lamp"]}
PATH = {"name": "advanced-circuit", "units": 200, "packs": ["automation-science-pack", "logistic-science-pack"],
        "unlocks": ["advanced-circuit"], "rocket_path": True}
CHEAP_PATH = dict(PATH, name="engine", units=100, unlocks=["engine-unit"])
BONUS = {"name": "mining-productivity-1", "units": 250, "packs": ["automation-science-pack", "logistic-science-pack"],
         "unlocks": []}


class WeighingTests(unittest.TestCase):
    def test_the_way_to_the_rocket_beats_a_cheap_side_road_and_the_cheaper_step_on_it_comes_first(self):
        # (run 19, 2026-10-25: System One picked "lamp" at 100%)
        names = [r["name"] for r in rb.weigh([LAMP, PATH, CHEAP_PATH, BONUS])]
        self.assertEqual(names[:2], ["engine", "advanced-circuit"])
        self.assertLess(names.index("advanced-circuit"), names.index("lamp"))

    def test_what_its_priorities_want_raises_what_unlocks_it(self):
        class Prio:
            def of(self, item):
                return type("P", (), {"weight": 0.9 if item == "small-lamp" else 0.1})()
        with mock.patch("companion.priorities.assess", lambda site, hold=False: Prio()):
            names = [r["name"] for r in rb.weigh([LAMP, PATH], site={"tick": 1})]
        self.assertEqual(names[0], "lamp")                           # (lamps wanted: night raids, say)

    def test_each_choice_is_written_down(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {"NPC_FACTIONS_DATA": folder}):
            tech, rows = rb.choose([LAMP, PATH], None, {"request_id": "1:r30", "npc_id": 1})
            row = json.loads((Path(folder) / "decisions" / rb.LOG_NAME).read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual((tech, row["chose"], len(row["options"])), ("advanced-circuit", "advanced-circuit", 2))


if __name__ == "__main__":
    unittest.main()
