"""Whether each engineer gets anywhere (scripts/watchdog.lua, in Factorio's Lua 5.2; SO-5): stalled when nothing of its
own advances for a while, told and logged once, its plan or wait set aside after longer; a running factory is no
progress of its own, nor a goal asked and deferred again and again."""
import sys
import unittest
from pathlib import Path

from lupa.lua52 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SETUP = """
    logged = {}
    function log(text) logged[#logged + 1] = text end
    events, unstuck = {}, {}
    stubs = {['scripts.agent'] = {event=function(npc, e) events[#events + 1] = e; return true end},
             ['scripts.designed_start'] = {unstick=function(npc, tick, why) unstuck[#unstuck + 1] = why; return true end}}
    function require(name)
        if stubs[name] then return stubs[name] end
        error('no module ' .. name)
    end
    Watchdog = assert(load(source, '@watchdog', 't'))()
    placed = 0
    stats = {input_counts={['stone-furnace']=0}}
    lost = 0
    kills = {output_counts={['stone-wall']=0}}
    force = {research_progress=0, current_research={name='automation'},
             get_kill_count_statistics=function(surface) kills.output_counts['stone-wall'] = lost; return kills end,
             get_entity_build_count_statistics=function(surface) stats.input_counts['stone-furnace'] = placed; return stats end}
    npc = {id=1, force=force, phase='designed:building:smelting', character={valid=true, surface={}},
           design={purpose='power-plant', status='building', reason='-', stage_index=2}, design_history={}, receipts={},
           receipt_order={}}
    cancelled = {}
    stubs['scripts.worker'] = {cancel_job=function(npc, id, why) cancelled[#cancelled + 1] = id; return {} end}
    function done(id, kind, count)
        npc.receipts[id] = {status='complete', kind=kind, count=count}
        npc.receipt_order[#npc.receipt_order + 1] = id
    end
    function run(from, to) for t = from, to, 600 do Watchdog.tick(npc, t) end end
"""
MIN = 3600


def runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().source = (ROOT / "mod" / "scripts" / "watchdog.lua").read_text(encoding="utf-8")
    lua.execute(SETUP)
    return lua


class StallTests(unittest.TestCase):
    def test_nothing_of_its_own_for_minutes_is_a_stall_told_once_and_logged(self):
        lua = runtime()
        start = 10 * MIN
        lua.execute(f"run({start}, {start + 5 * MIN - 600})")
        self.assertEqual(lua.eval("#events"), 0)
        lua.execute(f"run({start + 5 * MIN}, {start + 8 * MIN})")
        self.assertEqual(lua.eval("#events"), 1)                              # (once, not every look)
        self.assertEqual(lua.eval("events[1].kind"), "stalled")
        self.assertEqual(lua.eval("events[1].goal"), "power-plant")
        line = lua.eval("logged[1]")
        self.assertIn("[npc-factions] npc=1 STALL minutes=5", line)
        self.assertIn("goal=power-plant status=building", line)
        self.assertEqual(lua.eval("npc.stalled.goal"), "power-plant")

    def test_longer_still_its_plan_is_set_aside(self):
        lua = runtime()
        start = 10 * MIN
        lua.execute(f"run({start}, {start + 15 * MIN})")
        self.assertEqual(lua.eval("#unstuck"), 1)
        self.assertIn("UNSTICK", lua.eval("logged[#logged]"))

    def test_buildings_placed_research_or_a_finished_plan_are_progress(self):
        for change in ("placed = placed + 1", "force.research_progress = 0.5",
                       "npc.design_history[#npc.design_history + 1] = {}",
                       "force.current_research = {name='logistics'}"):
            lua = runtime()
            start = 10 * MIN
            lua.execute(f"run({start}, {start + 4 * MIN})")
            lua.execute(change)
            lua.execute(f"run({start + 4 * MIN + 600}, {start + 8 * MIN})")
            self.assertEqual(lua.eval("#events"), 0, change)

    def test_its_hands_work_counts_but_walking_or_a_goal_asked_and_deferred_does_not(self):
        lua = runtime()
        start = 10 * MIN
        # jobs finished with something mined, several between two looks: progress
        lua.execute(f"run({start}, {start + 4 * MIN}); done(1, 'mine', 10); done(2, 'craft', 1)")
        lua.execute(f"run({start + 4 * MIN + 600}, {start + 8 * MIN}); done(3, 'mine', 5)")
        lua.execute(f"run({start + 8 * MIN + 600}, {start + 12 * MIN})")
        self.assertEqual(lua.eval("#events"), 0)
        lua = runtime()
        for i in range(12):   # (walks done, and the design flipping between waiting and deferred)
            lua.execute(f"done({i}, 'move', 0); npc.design.status = '{'waiting' if i % 2 else 'deferred'}'; "
                        f"Watchdog.tick(npc, {start + i * MIN})")
        self.assertEqual(lua.eval("#events"), 1)

    def test_a_partial_plan_put_away_is_no_progress_and_a_lost_job_is_cancelled(self):
        lua = runtime()
        start = 10 * MIN
        lua.execute(f"npc.pending_job = 7; run({start}, {start + 6 * MIN})")
        lua.execute("npc.design_history[1] = {partial=true}")
        lua.execute(f"run({start + 6 * MIN + 600}, {start + 15 * MIN})")
        self.assertEqual(lua.eval("#unstuck"), 1)                            # (still stalled: set aside)
        self.assertEqual(lua.eval("cancelled[1]"), 7)                         # (the job that never came back)

    def test_a_job_the_worker_still_has_is_not_cancelled_and_a_capped_history_is_no_progress(self):
        lua = runtime()
        start = 10 * MIN
        lua.execute("npc.pending_job = 7; npc.current_job = {id=7}")         # (a long craft in hand)
        lua.execute("for i = 1, 64 do npc.design_history[i] = {plan_id='p' .. i, accepted_tick=i} end")
        lua.execute(f"run({start}, {start + 6 * MIN})")
        lua.execute("table.remove(npc.design_history, 1); npc.design_history[64] = {plan_id='x', partial=true}")
        lua.execute(f"run({start + 6 * MIN + 600}, {start + 15 * MIN})")
        self.assertEqual(lua.eval("#unstuck"), 1)
        self.assertEqual(lua.eval("#cancelled"), 0)

    def test_trips_scouting_and_fights_are_given_longer_and_the_start_its_grace(self):
        lua = runtime()
        lua.execute("npc.phase = 'designed:scouting:coal'")
        start = 10 * MIN
        lua.execute(f"run({start}, {start + 10 * MIN})")
        self.assertEqual(lua.eval("#events"), 0)
        lua.execute(f"run({start + 10 * MIN + 600}, {start + 16 * MIN})")
        self.assertEqual(lua.eval("#events"), 1)
        lua = runtime()
        lua.execute(f"run(0, {2 * MIN})")                                       # (its first minutes: not watched)
        self.assertEqual(lua.eval("#events"), 0)

    def test_progress_after_a_stall_clears_it(self):
        lua = runtime()
        start = 10 * MIN
        lua.execute(f"run({start}, {start + 6 * MIN}); placed = 3; run({start + 6 * MIN + 600}, {start + 7 * MIN})")
        self.assertIsNone(lua.eval("npc.stalled"))
        self.assertIn("UNSTALLED", lua.eval("logged[#logged]"))


class HookTests(unittest.TestCase):
    def test_the_game_watches_every_engineer_and_the_planner_holds_its_stalled_goal(self):
        control = (ROOT / "mod" / "control.lua").read_text(encoding="utf-8")
        start = (ROOT / "mod" / "scripts" / "designed_start.lua").read_text(encoding="utf-8")
        self.assertIn('timed("watchdog",Watchdog.tick,npc,event.tick)', control)
        # (before the drive and combat branch, whose early returns would skip it)
        self.assertLess(control.index("Watchdog.tick"), control.index('if timed("drive",Drive.tick'))
        self.assertIn("stalled=npc.stalled", start)
        self.assertIn("function DesignedStart.unstick", start)
        from companion import failures
        failures.forget()
        site = {"world_id": "w", "npc_id": 1, "tick": 50000,
                "stalled": {"goal": "power-plant", "phase": "designed:building", "reason": "-", "tick": 49000}}
        self.assertEqual(failures.stalled(site), "power-plant")
        self.assertIsNone(failures.stalled(site))                              # (once a stall)
        self.assertIsNotNone(failures.held(site, "power-plant"))
        self.assertIsNone(failures.stalled({**site, "stalled": {"goal": "choose", "tick": 1}}))
        failures.chose(site, "bus-base")                                        # (on a goal its planner chose)
        self.assertEqual(failures.stalled({**site, "stalled": {"goal": "choose", "tick": 2}}), "bus-base")
        failures.chose(site, "steel-smelting")                                  # (the same stall, a later request)
        self.assertIsNone(failures.stalled({**site, "stalled": {"goal": "choose", "tick": 2}}))
        self.assertIn('d.reason~="designer_timeout"', start)   # (a wait, and a late plan, are left)


if __name__ == "__main__":
    unittest.main()


class RepeatTests(unittest.TestCase):
    """Progress that repeats is none (SO-7: a breach repaired every 3 minutes all run, a wall plan blocked
    "entity_failed:r1-north-4:target_exhausted" between)."""

    def cycle(self, lua, n, minute, blocker="entity_failed:r1-north-4:target_exhausted", x=40, walls=4, destroyed=0):
        t = minute * MIN
        if blocker:   # (the wall plan blocked, seen at a look)
            lua.execute(f"npc.design = {{purpose='plot-wall', status='blocked', reason='{blocker}'}}; "
                        f"Watchdog.tick(npc, {t})")
        # the repair planned, built and finished: buildings placed, a whole plan more
        order = ", ".join(f"'e{i}'" for i in range(walls))
        ents = ", ".join(f"e{i}={{name='stone-wall', world={{x={x}, y=-300}}}}" for i in range(walls))
        lua.execute(f"lost = lost + {destroyed}; placed = placed + {walls}; "
                    f"npc.design = {{purpose='breach-repair', status='complete'}}; "
                    f"npc.design_history[#npc.design_history + 1] = {{purpose='breach-repair', plan_id='r{n}', "
                    f"accepted_tick={t + 600}, order={{{order}}}, entities={{{ents}}}}}; "
                    f"Watchdog.tick(npc, {t + 1200})")

    def test_the_same_plan_finished_again_and_again_with_the_same_blocker_is_a_stall(self):
        lua = runtime()
        lua.execute(f"Watchdog.tick(npc, {10 * MIN})")
        for n in range(3):
            self.cycle(lua, n, 11 + 3 * n)
        self.assertEqual(lua.eval("#events"), 1)
        line = lua.eval("logged[1]")
        self.assertIn("STALL", line)
        self.assertIn("reason=repeat key=breach-repair@", line)
        self.assertIn("entity_failed:r1-north-4:target_exhausted", line)
        self.assertEqual(lua.eval("events[1].goal"), "breach-repair")       # (the repeating goal is the one held)
        self.assertEqual(lua.eval("npc.stalled.goal"), "breach-repair")
        for n in range(3, 12):   # (on and on: set aside after as long again)
            self.cycle(lua, n, 11 + 3 * n)
        self.assertEqual(lua.eval("#unstuck"), 1)

    def test_plans_finished_with_nothing_blocked_or_on_new_ground_are_progress(self):
        lua = runtime()
        lua.execute(f"Watchdog.tick(npc, {10 * MIN})")
        for n in range(5):   # (supply-like: the same plan again, nothing blocking it)
            self.cycle(lua, n, 11 + 3 * n, blocker=None)
        self.assertEqual(lua.eval("#events"), 0)
        lua = runtime()
        lua.execute(f"Watchdog.tick(npc, {10 * MIN})")
        for n in range(5):   # (a blocker stands, but each repair on a new stretch)
            self.cycle(lua, n, 11 + 3 * n, x=40 + 64 * n)
        self.assertEqual(lua.eval("#events"), 0)

    def test_a_blocker_seen_once_long_ago_or_gone_since_counts_for_nothing(self):
        lua = runtime()
        lua.execute(f"Watchdog.tick(npc, {10 * MIN})")
        self.cycle(lua, 0, 11)                                    # (seen blocked once)
        for n in range(1, 5):
            self.cycle(lua, n, 11 + 3 * n, blocker=None)         # (never again since)
        self.assertEqual(lua.eval("#events"), 0)
        lua = runtime()
        lua.execute(f"Watchdog.tick(npc, {10 * MIN})")
        self.cycle(lua, 0, 11)
        self.cycle(lua, 1, 14)
        lua.execute(f"npc.design = {{purpose='plot-wall', status='building'}}; Watchdog.tick(npc, {16 * MIN})")
        self.cycle(lua, 2, 17, blocker=None)                      # (the wall going again: the loop is over)
        self.assertEqual(lua.eval("#events"), 0)

    def test_a_breach_under_attack_repaired_again_and_again_is_work_not_a_loop(self):
        # (Optimization's stage-nuclear: walls destroyed between repairs of 17, 15, 6, 8, 12 and 11 pieces)
        lua = runtime()
        lua.execute(f"Watchdog.tick(npc, {10 * MIN})")
        for n, (walls, destroyed) in enumerate(((17, 0), (15, 9), (6, 4), (8, 0), (12, 7), (11, 3))):
            self.cycle(lua, n, 11 + 2 * n, walls=walls, destroyed=destroyed)
        self.assertEqual(lua.eval("#events"), 0)
        lua = runtime()   # (the same repair each time, but walls lost again between: still the work of an attack)
        lua.execute(f"Watchdog.tick(npc, {10 * MIN})")
        for n in range(5):
            self.cycle(lua, n, 11 + 2 * n, destroyed=4)
        self.assertEqual(lua.eval("#events"), 0)

    def test_an_attack_after_a_repeat_stall_ends_it(self):
        lua = runtime()
        lua.execute(f"Watchdog.tick(npc, {10 * MIN})")
        for n in range(3):
            self.cycle(lua, n, 11 + 3 * n)
        self.assertEqual(lua.eval("#events"), 1)
        self.cycle(lua, 3, 20, destroyed=5)                      # (walls broken again: work, the repeat over)
        self.assertIn("UNSTALLED", lua.eval("logged[#logged]"))
        self.assertIsNone(lua.eval("npc.stalled"))

    def test_a_new_blocker_starts_a_new_story(self):
        lua = runtime()
        lua.execute(f"Watchdog.tick(npc, {10 * MIN})")
        self.cycle(lua, 0, 11)
        self.cycle(lua, 1, 14)
        self.cycle(lua, 2, 17, blocker="no_room")
        self.assertEqual(lua.eval("#events"), 0)

    def test_two_repeats_then_other_work_is_no_stall(self):
        lua = runtime()
        lua.execute(f"Watchdog.tick(npc, {10 * MIN})")
        self.cycle(lua, 0, 11)
        self.cycle(lua, 1, 14)
        self.cycle(lua, 2, 17, x=500)   # (a different target: the streak is over)
        self.cycle(lua, 3, 20)
        self.assertEqual(lua.eval("#events"), 0)
