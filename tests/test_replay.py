"""Watching a test run afterwards: the in-game recorder (recorder.lua, in Lua 5.2 with the game
stubbed), the LLM cost ledger, and the replay built from both (tools/replay.py)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from lupa.lua52 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from companion import usage  # noqa: E402
import replay  # noqa: E402


def to_python(value):
    """A Lua table as JSON would see it: 1..n keys make a list."""
    if hasattr(value, "items"):
        items = dict(value.items())
        if items and all(isinstance(k, int) for k in items) and sorted(items) == list(range(1, len(items) + 1)):
            return [to_python(items[k]) for k in range(1, len(items) + 1)]
        return {str(k): to_python(v) for k, v in items.items()}
    return value


def game():
    """recorder.lua over a stubbed game: one engineer, a few buildings, a nest, some biters."""
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().sources = lua.table_from({
        name: (ROOT / "mod" / "scripts" / (name + ".lua")).read_text(encoding="utf-8") for name in ("recorder", "chronicle")})
    lua.globals().encode = lambda t: json.dumps(to_python(t))
    lua.execute("""
        written = {}
        local loaded = {}
        function require(name)
            local short = name:gsub('^scripts%.', '')
            if not loaded[short] then loaded[short] = assert(load(sources[short], '@'..short, 't'))() end
            return loaded[short]
        end
        function log() end
        helpers = {write_file=function(name, data, append, player) written[#written+1] = data end,
                   table_to_json=function(t) return encode(t) end}
        settings = {global={['npc-factions-chat']={value='quiet'}, ['other-mod']={value=1}},
                    startup={['npc-factions-test-mode']={value='none'}}}
        script = {active_mods={base='2.0.77', ['npc-factions']='0.8.0'}}
        local function entity(name, type, x, y, unit, force, w, h)
            return {valid=true, name=name, type=type, position={x=x, y=y}, unit_number=unit, direction=0,
                    force=force, prototype={tile_width=w or 1, tile_height=h or 1}}
        end
        stats = {get_input_count=function(item) return item == 'iron-plate' and 120 or 0 end}
        ada_force = {name='npc-1', valid=true, color={r=1, g=0.5, b=0, a=1}, technologies={},
                     get_item_production_statistics=function() return stats end}
        enemy = {name='enemy', valid=true, color={r=1, g=0, b=0, a=1}}
        player_force = {name='player', valid=true, technologies={}}
        buildings = {entity('stone-furnace', 'furnace', 10, 10, 101, ada_force, 2, 2),
                     entity('burner-mining-drill', 'mining-drill', 12, 10, 102, ada_force, 2, 2),
                     entity('character', 'character', 1, 1, 103, ada_force)}
        nest = entity('biter-spawner', 'unit-spawner', 60, 60, 900, enemy, 5, 5)
        biter = entity('small-biter', 'unit', 20, 20, 901, enemy)
        surface = {index=1, name='nauvis', map_gen_settings={seed=42, starting_points={{x=0,y=0}}, no_enemies_mode=false},
            is_chunk_generated=function() return true end,
            find_tiles_filtered=function(f) return {{position={x=3, y=3}}} end,
            find_entities_filtered=function(f)
                if f.type == 'resource' then return {entity('iron-ore', 'resource', 5, 5)} end
                if f.type == 'tree' then return {entity('tree-01', 'tree', 9, 9)} end
                if f.type == 'unit' then return {biter} end
                if f.force == enemy then return {nest} end
                if f.force == ada_force then return buildings end
                return {}
            end}
        character = {valid=true, position={x=1, y=1}, surface=surface, get_health_ratio=function() return 0.5 end}
        ada = {id=1, name='Ada', force=ada_force, color={r=1, g=0.5, b=0}, character=character, home={x=0, y=0}, phase='mining iron',
               personality={traits={'curious', 'brave'}}, design={purpose='iron-smelting', status='building'}}
        storage = {npcs={ada}}
        game = {tick=0, speed=8, is_multiplayer=function() return true end, players={}, connected_players={},
                forces={enemy=enemy, player=player_force, ['npc-1']=ada_force}, surfaces={surface},
                map_settings={enemy_expansion={enabled=false}, enemy_evolution={enabled=true}, pollution={enabled=true}},
                difficulty_settings={technology_price_multiplier=1}}
        enemy.get_evolution_factor = function() return 0.12 end
        Recorder = require('scripts.recorder')
        Chronicle = require('scripts.chronicle')
    """)
    return lua


def lines(lua) -> list[dict]:
    return [json.loads(text) for text in lua.eval("written").values() if text.strip()]


class RecorderTest(unittest.TestCase):
    def run_game(self):
        lua = game()
        lua.execute("""
            assert(Recorder.start({name='t', every=60, scan=600}).ok)
            for tick = 1, 1300 do
                game.tick = tick
                if tick == 700 then table.remove(buildings, 2); ada.phase = 'walking home' end
                if tick == 800 then
                    Recorder.say(ada, nil, 'Hello')
                    Recorder.task(ada, {tool='attack', reason='they shot me'}, 'failed', 'walled')
                    Recorder.on_entity_died({entity=nest, force=ada_force})
                    Chronicle.record{kind='war_declared', actor='npc:1', target='player:1', tick=tick, source='native'}
                end
                Recorder.tick(tick)
            end
            stopped = Recorder.stop()
        """)
        return lua, lines(lua)

    def test_it_writes_samples_buildings_terrain_and_events(self):
        lua, out = self.run_game()
        kinds = [line["k"] for line in out]
        self.assertEqual(kinds[0], "start")
        self.assertEqual(kinds[-1], "stop")
        self.assertNotIn("error", kinds)
        start = out[0]
        self.assertEqual(start["settings"]["global"], {"npc-factions-chat": "quiet"})   # its own settings only
        self.assertEqual(start["world"]["seed"], 42)
        samples = [line for line in out if line["k"] == "a"]
        self.assertEqual(len(samples), 21)
        self.assertEqual(samples[0]["a"][0][:4], [1, 1, 1, 0.5])
        self.assertEqual(samples[0]["e"], [20, 20])
        added = [a for line in out if line["k"] == "b" for a in line.get("add", [])]
        self.assertEqual(sorted(a[0] for a in added), [101, 102, 900])   # bodies are not buildings
        removed = [d for line in out if line["k"] == "b" for d in line.get("del", [])]
        self.assertEqual(removed, [102])
        terrain = [line for line in out if line["k"] == "terrain"]
        self.assertTrue(terrain)
        block = next(line for line in terrain if line["bx"] == 0 and line["by"] == 0)
        self.assertEqual(block["g"][1 * 32 + 1], "w")   # water at tile 3,3
        self.assertEqual(block["g"][2 * 32 + 2], "i")   # iron ore at 5,5
        self.assertEqual({"say", "task", "nest", "chron", "stats", "state", "npc"} - set(kinds), set())
        self.assertTrue(lua.eval("stopped.ok"))
        self.assertIsNone(lua.eval("storage.recorder"))

    def test_a_bug_stops_the_recording_not_the_game(self):
        lua = game()
        lua.execute("""
            Recorder.start({name='t'})
            surface.find_entities_filtered = function() error('boom') end
            game.tick = 60
            Recorder.tick(60)
            Recorder.tick(120)
        """)
        out = lines(lua)
        self.assertEqual(out[-1]["k"], "error")
        self.assertIn("boom", out[-1]["error"])
        self.assertFalse(lua.eval("storage.recorder.active"))

    def test_hooks_do_nothing_when_not_recording(self):
        lua = game()
        lua.execute("Recorder.say(ada, nil, 'x'); Recorder.on_entity_died({entity=nest}); Recorder.tick(60)")
        self.assertEqual(lines(lua), [])

    def test_the_replay_reads_the_recording(self):
        _, out = self.run_game()
        data = replay.build_data(out, {"purpose": "test"})
        self.assertEqual([n["name"] for n in data["npcs"]], ["Ada"])
        self.assertEqual(data["npcs"][0]["color"], [1, 0.5, 0])   # its chat colour
        b = data["buildings"]
        furnace = b["p"].index(next(i + 1 for i, p in enumerate(data["protos"]) if p[0] == "stone-furnace"))
        self.assertEqual((b["t0"][furnace], b["t1"][furnace]), (0, -1))   # there from the start, still standing
        drill = next(i for i in range(len(b["p"])) if data["protos"][b["p"][i] - 1][0] == "burner-mining-drill")
        self.assertGreater(b["t1"][drill], 700)
        texts = [e["text"] for e in data["events"]]
        self.assertIn("Ada: Hello", texts)
        self.assertIn("Ada: attack failed (walled)", texts)
        self.assertIn("Ada destroyed a biter-spawner", texts)
        self.assertTrue(any(t.startswith("war declared: Ada → player:1") for t in texts))
        self.assertEqual(data["states"]["1"][-1][1], "walking home")
        self.assertEqual(data["stats"][-1][1][0]["made"], {"iron-plate": 120})


class LedgerTest(unittest.TestCase):
    def test_prices(self):
        self.assertAlmostEqual(usage.price("gpt-6-luna", 100_000, 100_000), 0.06)
        self.assertAlmostEqual(usage.price("gpt-6.1-sol", 200_000, 0, cached_tokens=100_000), 0.21)
        self.assertIsNone(usage.price("gpt-6-luna", 300_000, 10))   # long context: not priced here
        self.assertAlmostEqual(usage.price("gpt-6-luna", 300, api="decisions"), 0.00003)
        self.assertIsNone(usage.price("some-new-model", 10, 10))

    def test_summary_by_system(self):
        start = usage.mark()
        usage.record("strategist", "gpt-6-luna", input_tokens=1000, output_tokens=200, latency_ms=2800, npc=1)
        usage.record("system one", "gpt-6-luna", api="decisions", input_tokens="300", purpose="goal")
        usage.record("designer", "mystery", input_tokens=None, ok=False)
        s = usage.summary(usage.entries(start))
        self.assertEqual(s["total"]["calls"], 3)
        self.assertEqual(s["total"]["failed"], 1)
        self.assertEqual(s["total"]["unpriced"], 1)
        self.assertAlmostEqual(s["systems"]["strategist"]["cost"], 0.0002)
        self.assertEqual(s["systems"]["system one"]["input"], 300)
        self.assertEqual(s["systems"]["strategist"]["median_latency_ms"], 2800)


class ReplayTest(unittest.TestCase):
    def test_log_lines_and_llm_calls_land_on_the_timeline(self):
        to_tick = replay.wall_to_tick([[100.0, 600, 1], [110.0, 1200, 1]])
        self.assertEqual(to_tick(105.0), 900)
        self.assertEqual(to_tick(90.0), 600)
        self.assertEqual(to_tick(112.0), 1200 + 120)   # past the last sample: at the last speed

    def test_build_writes_one_page_with_the_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            recording = [{"t": 0, "k": "start", "every": 60},
                         {"t": 60, "k": "npc", "id": 1, "name": "Ada", "force": "npc-1"},
                         {"t": 60, "k": "a", "a": [[1, 0, 0, 1, 0]]},
                         {"t": 120, "k": "say", "id": 1, "text": "</script><b>hi</b>"}]
            (run / "recording.jsonl").write_text("\n".join(json.dumps(r) for r in recording) + "\n{broken",
                                                 encoding="utf-8")
            (run / "run.json").write_text(json.dumps({
                "purpose": "check", "clock": [[1.0, 0, 1], [2.0, 60, 1]], "log": [[1.5, "tick 30: Ada accepted"]],
                "llm": {"calls": [{"at": 2.0, "system": "strategist", "cost": 0.001}],
                        "total": {"calls": 1, "cost": 0.001}}}), encoding="utf-8")
            page = replay.build(run).read_text(encoding="utf-8")
        self.assertNotIn(replay.SLOT, page)
        self.assertEqual(page.count("</script>"), 2)   # the page's own two; the chat line is escaped
        start = page.index('type="application/json">') + len('type="application/json">')
        data = json.loads(page[start:page.index("</script>", start)])
        self.assertEqual(data["log"], [[30, "tick 30: Ada accepted"]])
        self.assertEqual(data["llm"][0]["t"], 60)
        self.assertIn("Ada: </script><b>hi</b>", [e["text"] for e in data["events"]])


if __name__ == "__main__":
    unittest.main()
