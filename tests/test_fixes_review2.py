"""Second Codex review round and the first rehearsal (2026-10-22): a patch by the plot's edge is still
reached (the planning window slides inward), and one beyond it makes the engineer wait, not scout for
ever (npc 2 scouted 24 times in 20 minutes for copper it already knew)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from companion import blueprints as bp, team_area  # noqa: E402
from companion.catalog_planner import CatalogPlanner, Defer, MoveSite, Scout  # noqa: E402
from test_blueprints import OPENED, site  # noqa: E402

# its plot ends 88 tiles east of home (local tiles; home at 32,32)
TEAM = {"plot": [-300, -300, 120, 300], "area": [-1000, -1000, 1000, 1000], "no_build": []}


class WindowTests(unittest.TestCase):
    def test_a_window_slides_inside_the_plot_while_the_patch_stays_in_it(self):
        team = team_area.team_of({"team": TEAM})
        self.assertEqual(team_area.window_for(team, 102, 32, half=33), (87, 32))      # slid 15 tiles west
        self.assertIsNone(team_area.window_for(team, 142, 32, half=33))               # 55 tiles: not in it
        self.assertEqual(team_area.window_for(team, 40, 32, half=33), (40, 32))       # fits as it is
        # an outpost patch just past the wall: the site goes wholly outside the team area (Codex's case)
        walled = team_area.team_of({"team": {"plot": [-576, -576, -192, -192], "area": [-576, -576, 576, 576],
                                             "no_build": []}})
        self.assertEqual(team_area.window_for(walled, -590, -384, half=33), (-609, -384))


class SiteMoveTests(unittest.TestCase):
    def ask(self, dx, planner=None):
        s = site(request="1:5", purpose="iron-smelting-expansion", completed_purposes=OPENED["completed_purposes"],
                 base=OPENED["base"], resources=[{"resource": "iron-ore", "rows": [[29, 42, 43], [30, 42, 43]]}],
                 built=[{"name": "burner-mining-drill", "position": {"x": 43, "y": 30}}],
                 known_patches=[{"resource": "iron-ore", "dx": dx, "dy": 0, "tiles": 64, "amount": 256000}],
                 team=TEAM)
        with tempfile.TemporaryDirectory() as folder:
            (planner or CatalogPlanner(bp.Catalog(folder), log=lambda _: None))(s)

    def test_a_patch_by_the_edge_is_reached_from_a_site_slid_inward(self):
        with self.assertRaises(MoveSite) as moved:
            self.ask(70)
        self.assertEqual((moved.exception.dx, moved.exception.dy), (55, 0))

    def test_a_first_plan_with_no_iron_in_its_site_moves_to_iron_it_knows_else_scouts(self):
        # (Factorio 2.1 test map, 2026-10-23: the first plan found no iron in its window, failed, and the
        # engineer never asked again)
        bare = dict(site(), resources=[{"resource": "coal", "rows": [[29, 10, 15]]}])
        with tempfile.TemporaryDirectory() as folder:
            planner = CatalogPlanner(bp.Catalog(folder), log=lambda _: None)
            with self.assertRaises(MoveSite) as moved:
                planner(dict(bare, known_patches=[{"resource": "iron-ore", "dx": 48, "dy": 10, "tiles": 120,
                                                   "amount": 400000}]))
            self.assertEqual((moved.exception.purpose, moved.exception.dx, moved.exception.dy),
                             ("iron-smelting-start", 48, 10))
            with self.assertRaises(Scout):
                planner(dict(bare, request_id="1:2", known_patches=[]))
            plan = planner(site(request="1:3"))                       # iron in its site: built there, as before
        self.assertTrue(plan["entities"])

    def test_a_patch_only_past_the_edge_sends_it_round_home_twice_then_it_waits(self):
        # (it knows only what it has been near: the field beside home may be unseen, rehearsal 2026-10-22;
        # a trip ends only at a patch on its own ground, so it is no loop)
        with tempfile.TemporaryDirectory() as folder:
            planner = CatalogPlanner(bp.Catalog(folder), log=lambda _: None)
            for _ in range(2):
                with self.assertRaises(Scout):
                    self.ask(110, planner)
            with self.assertRaises(Defer) as waited:
                self.ask(110, planner)
        self.assertIn("outside_its_ground", waited.exception.reason)



class StrategistResendTests(unittest.TestCase):
    def test_a_decision_whose_sending_fails_goes_again_next_time(self):
        import time
        from companion.strategist import AgentBridge
        sent, fail = [], [True]

        def call(command):
            if command.startswith("/npc-agent observe"):
                return {"tick": 100, "requests": [{"request_id": "1:s1", "npc_id": 1, "reason": "events",
                                                   "situation": {"name": "Ada"}}]}
            if fail[0]:
                fail[0] = False
                raise ConnectionError("the line dropped")
            sent.append(command)
            return {"accepted": 1}

        class Brain:
            source = "rules"

            def decide(self, request):
                return [{"tool": "say", "args": {"text": "On it."}}]

        bridge = AgentBridge(call, Brain(), log=lambda _: None)
        for _ in range(50):
            try:
                bridge.poll_once()
            except ConnectionError:
                pass
            if sent:
                break
            time.sleep(0.01)
        self.assertEqual(len(sent), 1)
        self.assertIn('"request_id":"1:s1"', sent[0])
        bridge.poll_once()
        self.assertEqual(len(sent), 1)          # sent once, not again


class ReconnectTests(unittest.TestCase):
    def test_after_a_reconnect_nothing_asked_before_is_answered(self):
        import sys as _sys
        _sys.path.insert(0, str(ROOT / "tools"))
        import attach_runner as ar
        forgotten = []

        class Side:
            def __init__(self, *a, **k):
                pass

            def poll_once(self):
                pass

            def forget(self, why):
                forgotten.append(why)

        class Client:
            def text(self, command):
                return "{}"

            def close(self):
                pass

        link = ar.Link({"host": "127.0.0.1", "port": 1, "password": "x"}, connect=lambda e, t: Client())
        with tempfile.TemporaryDirectory() as folder:
            runner = ar.Runner(link, Path(folder), planner=object(), log=lambda _: None,
                               bridge_factory=lambda *a, **k: Side(), agent_factory=lambda *a, **k: Side())
            runner.step()
            self.assertEqual(forgotten, [])                     # the first connection: nothing to forget
            link.close()
            runner.step()                                       # connected again
            self.assertEqual(len(forgotten), 2)                 # the designers and the strategists


if __name__ == "__main__":
    unittest.main()
