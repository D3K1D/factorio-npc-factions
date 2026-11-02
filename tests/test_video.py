"""Real graphics for a run's replay: the camera (camera.lua, in Lua 5.2 with the game stubbed), the
frame saves a run takes, the pictures joined into MP4s and the replay showing them (tools/video.py)."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lupa.lua52 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import replay  # noqa: E402
import video  # noqa: E402


def to_python(value):
    """A Lua table as JSON would see it: 1..n keys make a list."""
    if hasattr(value, "items"):
        items = dict(value.items())
        if items and all(isinstance(k, int) for k in items) and sorted(items) == list(range(1, len(items) + 1)):
            return [to_python(items[k]) for k in range(1, len(items) + 1)]
        return {str(k): to_python(v) for k, v in items.items()}
    return value


def game():
    """camera.lua over a stubbed game: Ada alive at (5, 7), Kevin dead (his home is still there)."""
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().source = (ROOT / "mod" / "scripts" / "camera.lua").read_text(encoding="utf-8")
    lua.globals().encode = lambda t: json.dumps(to_python(t))
    lua.execute("""
        saved, shots, written, printed, commands_added = {}, {}, {}, {}, {}
        multiplayer, waited = true, false
        function log() end
        helpers = {write_file=function(name, data) written[name] = data end,
                   table_to_json=function(t) return encode(t) end,
                   json_to_table=function(s) return s == '{"name":"bad name/../x"}' and {name='bad name/../x'} or {} end}
        surface = {index=1, name='nauvis'}
        game = {tick=7200, surfaces={surface}, is_multiplayer=function() return multiplayer end,
                server_save=function(name) saved[#saved+1] = name end,
                take_screenshot=function(t)
                    if t.position.x == 999 then error('no such place') end
                    shots[#shots+1] = t
                end,
                set_wait_for_screenshots_to_finish=function() waited = true end,
                get_player=function() return nil end}
        commands = {add_command=function(name, help, f) commands_added[name] = f end}
        rcon = {print=function(s) printed[#printed+1] = s end}
        storage = {npcs={
            {id=1, name='Ada', home={x=0, y=0}, character={valid=true, position={x=5.04, y=7}, surface=surface}},
            {id=2, name='Kevin', home={x=512, y=0}, character={valid=false}}}}
        Camera = assert(load(source, '@camera', 't'))()
    """)
    return lua


def table(lua, expr):
    return {k: lua.eval(f"{expr}.{k}") for k in ("id", "kind", "x", "y", "zoom")}


class CameraTest(unittest.TestCase):
    def test_a_frame_marks_the_game_with_where_to_look_and_saves_it(self):
        lua = game()
        lua.execute("result = Camera.frame({name='video-0001'})")
        self.assertTrue(lua.eval("result.ok"))
        self.assertEqual(lua.eval("saved[1]"), "video-0001")
        self.assertEqual(lua.eval("#storage.camera.views"), 3)   # Ada and her home; Kevin's home only
        self.assertEqual(table(lua, "storage.camera.views[1]"), {"id": 1, "kind": "engineer", "x": 5.0, "y": 7, "zoom": 0.5})
        self.assertEqual(table(lua, "storage.camera.views[2]"), {"id": 1, "kind": "base", "x": 0, "y": 0, "zoom": 0.25})
        self.assertEqual(table(lua, "storage.camera.views[3]"), {"id": 2, "kind": "base", "x": 512, "y": 0, "zoom": 0.25})
        self.assertEqual((lua.eval("storage.camera.resolution.x"), lua.eval("storage.camera.resolution.y")), (1280, 720))
        self.assertEqual(lua.eval("storage.camera.daytime"), 0)
        lua.execute("Camera.done()")
        self.assertIsNone(lua.eval("storage.camera"))

    def test_the_live_server_never_draws(self):
        lua = game()
        lua.execute("Camera.frame({name='f'}); Camera.tick()")
        self.assertEqual(lua.eval("#shots"), 0)
        self.assertIsNotNone(lua.eval("storage.camera"))

    def test_a_single_player_load_draws_every_view_once_and_says_which_is_which(self):
        lua = game()
        lua.execute("""
            Camera.frame({name='f', width=641, height=360, daytime=false})
            multiplayer = false
            Camera.tick(); Camera.tick()""")
        self.assertEqual(lua.eval("#shots"), 3)
        self.assertEqual(lua.eval("shots[1].path"), "npc-video/f/1-engineer.jpg")
        self.assertEqual(lua.eval("shots[3].path"), "npc-video/f/2-base.jpg")
        self.assertEqual((lua.eval("shots[1].resolution.x"), lua.eval("shots[1].zoom")), (640, 0.5))
        self.assertIsNone(lua.eval("shots[1].daytime"))   # the game's own time of day, as asked
        self.assertFalse(lua.eval("shots[1].show_gui"))
        self.assertTrue(lua.eval("waited"))
        self.assertIsNone(lua.eval("storage.camera"))
        frame = json.loads(lua.eval("written['npc-video/f/frame.json']"))
        self.assertEqual(frame["tick"], 7200)
        self.assertEqual([(v["id"], v["kind"], v["file"]) for v in frame["views"]],
                         [(1, "engineer", "1-engineer.jpg"), (1, "base", "1-base.jpg"), (2, "base", "2-base.jpg")])

    def test_a_picture_that_fails_is_noted_and_the_others_are_still_taken(self):
        lua = game()
        lua.execute("""
            storage.npcs[2].home = {x=999, y=0}
            Camera.frame({name='f'}); multiplayer = false; Camera.tick()""")
        self.assertEqual(lua.eval("#shots"), 2)
        frame = json.loads(lua.eval("written['npc-video/f/frame.json']"))
        failed = frame["views"][2]
        self.assertNotIn("file", failed)
        self.assertIn("no such place", failed["error"])

    def test_the_command_answers_rcon_and_keeps_save_names_safe(self):
        lua = game()
        lua.execute("""
            Camera.register_commands()
            commands_added['npc-camera']({parameter='frame {"name":"bad name/../x"}'})
            commands_added['npc-camera']({parameter='done'})""")
        self.assertEqual(lua.eval("saved[1]"), "bad_name____x")
        first = lua.eval("printed[1]")
        self.assertTrue(first.startswith("NPC_CAMERA_RESULT "))
        self.assertEqual(json.loads(first[len("NPC_CAMERA_RESULT "):])["save"], "bad_name____x")
        self.assertIsNone(lua.eval("storage.camera"))


class CaptureTest(unittest.TestCase):
    def test_a_frame_every_interval_and_its_save_waited_for(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            saves = run / "profile" / "saves"
            saves.mkdir(parents=True)
            sent = []

            def call(command):
                sent.append(command)
                if command.startswith("/npc-camera frame"):
                    name = json.loads(command.split(" ", 2)[2])["name"]
                    (saves / f"{name}.zip").write_bytes(b"zip")
                    return 'NPC_CAMERA_RESULT {"ok":true,"tick":7300,"views":2}'
                return 'NPC_CAMERA_RESULT {"ok":true}'

            capture = video.VideoCapture(run, 1, log=lambda m: None)
            self.assertFalse(capture.due(100))     # the first frame a whole minute in
            self.assertFalse(capture.due(3599))
            self.assertTrue(capture.due(3600))
            capture.frame(call)
            self.assertEqual(sent, ['/npc-camera frame {"name": "video-0001"}', "/npc-camera done"])
            self.assertEqual(capture.frames, [{"save": "video-0001", "tick": 7300, "views": 2}])
            self.assertFalse(capture.due(7300 + 60))
            self.assertTrue(capture.due(10800))
            self.assertEqual(capture.summary()["frames_saved"], 1)

    def test_a_frame_the_game_refuses_is_counted_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture = video.VideoCapture(Path(tmp), 2, log=lambda m: None)
            capture.frame(lambda command: "Unknown command")
            self.assertEqual((capture.frames, capture.failed), ([], ["video-0001"]))


def pictures(frame_dir: Path, tick: int, views: list[tuple[int, str, str]], color: str) -> None:
    from PIL import Image
    frame_dir.mkdir(parents=True)
    for npc_id, name, kind in views:
        Image.new("RGB", (64, 36), color).save(frame_dir / f"{npc_id}-{kind}.jpg")
    (frame_dir / "frame.json").write_text(json.dumps({"tick": tick, "views": [
        {"id": npc_id, "name": name, "kind": kind, "file": f"{npc_id}-{kind}.jpg", "zoom": 0.5} for npc_id, name, kind in views]}),
        encoding="utf-8")


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.ffmpeg = video.find_ffmpeg()
        if self.ffmpeg is None:
            self.skipTest("ffmpeg not installed")

    def test_each_save_drawn_then_one_mp4_per_engineer_and_view_in_tick_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            saves = run / "profile" / "saves"
            saves.mkdir(parents=True)
            for name in ("video-0001", "video-0002", "video-0003"):
                (saves / f"{name}.zip").write_bytes(b"zip")
            engine = run / "factorio.exe"
            engine.write_bytes(b"")
            drawn = []

            def shoot(engine, config, mods, save, out, log_path, client=None):
                drawn.append(save.stem)
                if save.stem == "video-0002":
                    log_path.write_text("crashed", encoding="utf-8")
                    return False   # this one did not come out
                tick = 3600 if save.stem == "video-0001" else 7200
                views = [(1, "Ada", "engineer"), (1, "Ada", "base")] + ([(2, "Kevin", "base")] if tick == 7200 else [])
                pictures(out, tick, views, "red" if tick == 3600 else "blue")
                return True

            with mock.patch.object(video, "shoot", shoot), \
                    mock.patch.object(video, "validate_engine", lambda e: run / "data"), \
                    mock.patch.object(video, "CLIENT", run / "client"):
                result = video.render(run, engine, capture={"every_minutes": 1.0}, log=lambda m: None)
            self.assertEqual(drawn, ["video-0001", "video-0002", "video-0003"])
            self.assertEqual(sorted(p.name for p in saves.iterdir()), ["video-0002.zip"])   # kept to try again
            self.assertEqual(result["failed"], ["video-0002"])
            self.assertTrue((run / "video" / "client-video-0002.log").exists())
            got = {(v["id"], v["kind"]): v for v in result["videos"]}
            self.assertEqual(set(got), {(1, "engineer"), (1, "base"), (2, "base")})
            self.assertEqual(got[(1, "base")]["ticks"], [3600, 7200])
            self.assertEqual(got[(2, "base")]["file"], "video/2-kevin-base.mp4")
            mp4 = run / got[(1, "base")]["file"]
            probe = subprocess.run([str(self.ffmpeg), "-i", str(mp4), "-map", "0:v", "-f", "null", "-"],
                                   capture_output=True, text=True)
            self.assertEqual(re.findall(r"frame=\s*(\d+)", probe.stderr)[-1], "2")
            saved = json.loads((run / "video" / "video.json").read_text(encoding="utf-8"))
            self.assertEqual((saved["every_minutes"], saved["frames"]), (1.0, 2))

            page_dir = run / "elsewhere"
            page_dir.mkdir()
            data = replay.video_data(run, page_dir)
            self.assertEqual(data["fps"], video.FPS)
            self.assertEqual({v["src"] for v in data["videos"]},
                             {"../video/1-ada-engineer.mp4", "../video/1-ada-base.mp4", "../video/2-kevin-base.mp4"})


    def test_drawn_later_it_keeps_what_the_run_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "profile" / "saves").mkdir(parents=True)
            pictures(run / "video" / "frames" / "video-0001", 3600, [(1, "Ada", "base")], "green")
            (run / "run.json").write_text(json.dumps({"video": {"every_minutes": 2.0, "frames_saved": 1,
                                                                "rendered": False, "later": "python ..."}}),
                                          encoding="utf-8")
            engine = run / "factorio.exe"
            engine.write_bytes(b"")
            with mock.patch.object(video, "validate_engine", lambda e: run / "data"), \
                    mock.patch.object(video, "CLIENT", run / "client"):
                result = video.render(run, engine, log=lambda m: None)
            self.assertEqual((result["every_minutes"], result["frames_saved"]), (2.0, 1))
            self.assertEqual([v["file"] for v in result["videos"]], ["video/1-ada-base.mp4"])


class ReplayVideoTest(unittest.TestCase):
    def test_no_video_no_tab_data_and_missing_files_are_left_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            self.assertIsNone(replay.video_data(run, run))
            (run / "video").mkdir()
            (run / "video" / "1-ada-base.mp4").write_bytes(b"")
            (run / "video" / "video.json").write_text(json.dumps({"fps": 4, "every_minutes": 2, "videos": [
                {"id": 1, "kind": "base", "file": "video/1-ada-base.mp4", "ticks": [7200]},
                {"id": 1, "kind": "engineer", "file": "video/1-ada-engineer.mp4", "ticks": [7200]}]}), encoding="utf-8")
            data = replay.video_data(run, run)
        self.assertEqual(data["fps"], 4)
        self.assertEqual([v["src"] for v in data["videos"]], ["video/1-ada-base.mp4"])


if __name__ == "__main__":
    unittest.main()
