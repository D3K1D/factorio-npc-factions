"""A run ends as soon as an engineer is stuck, with a report of why (tools/stall_watch.py, OP-9): the game's watchdog
line, or no piece built and no plan finished for ten game minutes."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import stall_watch  # noqa: E402


class Line:
    """An RCON line answering the state query with the engineers as a test sets them."""
    def __init__(self):
        self.tick, self.npcs = 0, []

    def text(self, command):
        return json.dumps({"tick": self.tick, "npcs": self.npcs})


def npc(plans=3, built=40, alive=True, **extra):
    return {"id": 1, "name": "Ada", "alive": alive, "phase": "designed-start:producing:research", "goal": "research-lab",
            "status": "building", "reason": None, "plans": plans, "built": built, **extra}


class StallWatchTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.log = self.dir / "server.log"
        self.log.write_text("", encoding="utf-8")
        (self.dir / "runner.log").write_text("12:00:00 1:9: next goal research-lab\n", encoding="utf-8")
        (self.dir / "data" / "decisions").mkdir(parents=True)
        (self.dir / "data" / "decisions" / "goals.jsonl").write_text(
            "\n".join(json.dumps({"npc": 1, "tick": t, "goal": "research-lab"}) for t in range(30)), encoding="utf-8")
        self.line = Line()

    def test_no_progress_for_ten_game_minutes_stops_the_run_with_a_report(self):
        watch = stall_watch.StallWatch(self.dir, self.log, mode="stop")
        self.line.npcs = [npc()]
        self.assertEqual(watch.look(self.line), [])
        self.line.tick = 9 * 3600
        self.assertEqual(watch.look(self.line), [])
        self.assertFalse(watch.stop)
        self.line.tick = 10 * 3600
        stalls = watch.look(self.line)
        self.assertEqual(len(stalls), 1)
        self.assertTrue(watch.stop)
        report = json.loads((self.dir / "stuck.json").read_text(encoding="utf-8"))[0]
        self.assertEqual((report["goal"], report["phase"]), ("research-lab", "designed-start:producing:research"))
        self.assertEqual(len(report["decisions"]), 20)
        self.assertIn("next goal", report["trace"]["runner"][-1])

    def test_progress_resets_the_clock(self):
        watch = stall_watch.StallWatch(self.dir, self.log, mode="stop")
        self.line.npcs = [npc()]
        watch.look(self.line)
        self.line.tick, self.line.npcs = 8 * 3600, [npc(built=41)]
        watch.look(self.line)
        self.line.tick = 15 * 3600
        self.assertEqual(watch.look(self.line), [])

    def test_the_games_watchdog_line_is_a_stall_at_once(self):
        watch = stall_watch.StallWatch(self.dir, self.log, mode="stop")
        self.line.npcs = [npc()]
        watch.look(self.line)
        with self.log.open("a", encoding="utf-8") as f:
            f.write("  81.5 Script @__npc-factions__/x.lua:1: [npc-factions] npc=1 STALL waiting on research 5 min\n")
        stalls = watch.look(self.line)
        self.assertEqual(len(stalls), 1)
        self.assertTrue(stalls[0]["why"].startswith("watchdog: waiting on research"))
        self.assertEqual(watch.look(self.line), [])   # (the line is read once)

    def test_research_or_a_new_plan_at_the_history_cap_is_progress(self):
        watch = stall_watch.StallWatch(self.dir, self.log, mode="stop")
        self.line.npcs = [npc(plans=64, newest="a", research="automation:10")]
        watch.look(self.line)
        self.line.tick, self.line.npcs = 6 * 3600, [npc(plans=64, newest="a", research="automation:40")]
        watch.look(self.line)
        self.line.tick, self.line.npcs = 12 * 3600, [npc(plans=64, newest="b", research="automation:40")]
        self.assertEqual(watch.look(self.line), [])
        self.line.tick = 23 * 3600
        self.assertEqual(len(watch.look(self.line)), 1)

    def test_a_watchdog_line_counts_when_the_state_cannot_be_had_and_a_new_log_is_read_from_its_start(self):
        watch = stall_watch.StallWatch(self.dir, self.log, mode="record")
        self.log.write_text("x" * 500, encoding="utf-8")
        watch.look(self.line)
        self.log.write_text("[npc-factions] npc=1 STALL stuck" + chr(10), encoding="utf-8")   # (shorter: a new log)
        self.line.text = lambda command: (_ for _ in ()).throw(TimeoutError())
        self.assertEqual(len(watch.look(self.line)), 1)

    def test_record_mode_goes_on_and_a_dead_engineer_is_no_stall(self):
        watch = stall_watch.StallWatch(self.dir, self.log, mode="record")
        self.line.npcs = [npc(), {**npc(alive=False), "id": 2, "name": "Bo"}]
        watch.look(self.line)
        self.line.tick = 11 * 3600
        stalls = watch.look(self.line)
        self.assertEqual([s["npc"] for s in stalls], [1])
        self.assertFalse(watch.stop)


if __name__ == "__main__":
    unittest.main()
