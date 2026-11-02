"""A building planned mirrored is built mirrored (plan "mirror": true; DS-3, in Factorio's Lua 5.2): its plan keeps it,
every build of it, by hand, in a run, as a rebuild or as its robots' ghost, places it so, and only a mirrored one of
it is taken as the piece. The optimizer's oil block mirrors its refinery and chemical plants: built unmirrored, a
refinery's crude and water inlets swap, and crude stood at its water inlet."""
import unittest

import test_opening_pace
import test_robots

ROOT = test_robots.test_character_layer.ROOT / "mod" / "scripts"


class MirrorTests(unittest.TestCase):
    def test_a_building_planned_so_is_mirrored_if_its_placing_left_it_not(self):
        lua = test_opening_pace.crafting_worker()
        lua.execute("e = {mirroring=false}; W.mirrored(e, true); f = {mirroring=false}; W.mirrored(f, nil)")
        self.assertTrue(lua.eval("e.mirroring"))
        self.assertFalse(lua.eval("f.mirroring"))

    def test_its_robots_ghost_must_be_mirrored_too(self):
        lua = test_robots.runtime()
        lua.execute("""
            piece.mirror = true
            surface.create_entity = function(spec)
                local g = {valid=true, direction=spec.direction, mirroring=spec.mirror == true}
                function g.destroy() g.valid = false end
                ghosts[#ghosts + 1] = g
                return g
            end""")
        self.assertIsNone(lua.eval("(Robots.instead(npc, hand, 100, piece))"))   # (placed mirrored: its robots')
        self.assertTrue(lua.eval("ghosts[1].mirroring"))
        lua.execute("ghosts[1].mirroring = false")
        self.assertFalse(lua.eval("Robots.matches(ghosts[1], piece)"))

    def test_an_unmirrored_piece_takes_no_mirrored_ghost(self):
        # (Codex, DS-3: either way round)
        lua = test_robots.runtime()
        self.assertFalse(lua.eval("Robots.matches({direction=0, mirroring=true}, piece)"))
        self.assertTrue(lua.eval("Robots.matches({direction=0, mirroring=false}, piece)"))

    def test_kept_by_its_plan_and_passed_to_every_build(self):
        ds = (ROOT / "designed_start.lua").read_text(encoding="utf-8")
        self.assertIn("mirror=e.mirror==true or nil", ds)                           # (validate keeps it)
        self.assertEqual(ds.count("mirror=e.mirror}") + ds.count("mirror=entity.mirror}")
                         + ds.count("mirror=entity.mirror,avoid=avoid}"), 6)         # (each build of a piece)
        self.assertIn("(try(function() return e.mirroring end)==true)==(entity.mirror==true)", ds)   # (adopt)
        worker = (ROOT / "worker.lua").read_text(encoding="utf-8")
        self.assertIn('"belt_to_ground_type", "mirror",', worker)
        self.assertEqual(worker.count("placement.mirror = "), 2)                     # (a build, a run's placement)
        self.assertIn("mirror=e.mirror==true or nil", (ROOT / "holograms.lua").read_text(encoding="utf-8"))
        self.assertIn("and (o.mirror==true)==(e.mirror==true) then", ds)             # (a duplicate, mirrored or not)
        self.assertIn("if back and (try(function() return back.mirroring end)==true)~=(entity.mirror==true) then back=nil end", ds)
        self.assertIn("mirror = job.mirror == true or nil}", worker)                 # (fast replacement)


if __name__ == "__main__":
    unittest.main()
