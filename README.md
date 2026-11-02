# NPC Factions

A Factorio 2.1 + Space Age mod where NPC engineers play the game with other players.
Each engineer is apart of a faction and has unique personality traits, and plays the game similar to a player (with a few exceptions). 
Their goal is the first orbital platform above Nauvis (for now).

Engineers play the way above average players do:

- **Opening:** burner drills and a coal loop, stone from rocks, steam power,
  the first lab, then electric mining and red science by machine.
- **Main bus:** planned whole from the start and built lane by lane, with
  smelting columns at its head and science blocks fed from it by splitter taps.
  Science comes from proven blueprint books; any book block's inputs and outputs
  are read from its own belts.
- **Research:** picked by what the next builds need, before they wait on it.
- **Defense:** a closed wall ring with turrets spaced along it, an ammo belt
  loop, walls built after the first losses, more turrets where attacks keep
  landing, rebuilds after every loss and repairs when there is time.
- **Offense:** nests in or near their pollution cloud cleared by turret creep,
  one loop round the base, restocking only when the next group needs it.
- **Later game:** robots, oil, a nuclear plant stamped from a tileable book,
  the rocket silo and a space platform that mines asteroids and makes space
  science.

**Team play:** several engineers and the players share one team: shared
research (players' queue items are never moved), can communicate in game chat, help when a base is
attacked. 
**Faction play:** engineers start at peace with you, trade, offer
alliances, and go to war if you attack them or their factory.

An LLM strategist decides what each engineer does when something happens and
answers chat in character. It runs through the Codex CLI on a ChatGPT plan by
default, or the OpenAI API. Layout planning, building and fighting run in the
mod and the Python companion.

**Status:** early development, not ready for regular play. The opening through
red science runs on its own; the main bus with green science is being finished,
then blue.

## Layout

| Folder | What it is |
|---|---|
| `mod/` | The Factorio mod (Lua). Owns all game state and physical actions. |
| `companion/` | Python companion. Connects to the game over RCON: layout planners, blueprint library, the strategist. |
| `designer/` | LLM layout designer the companion calls for new layouts. |
| `tools/` | Live-play launcher, native test runners, sandbox module tests, base renderer. |
| `tests/` | Unit tests and test map settings. |

## Requirements

- Factorio 2.1 with Space Age
- Python 3.12+ with `lupa` (Lua unit tests) and `Pillow` (the designer and `tools/render_base.py`)
- For the AI: the Codex CLI signed in to a ChatGPT plan (the default, `--ai codex`), or an OpenAI API key in the
  `OPENAI_API_KEY` environment variable (`--ai api`)

## Playing along

```
python tools/run_world_smoke.py --live --bots 4 --strategist gpt-6-luna --run-dir tests/runs/live1 --catalog tests/runs/library-live
```

This starts a private server on a random map with native starting areas, writes
`join.cmd` in the run folder (your Factorio, with only this game's mods), and
runs the companion. The game autosaves every 10 minutes; touch `stop-now` in the
run folder to save and stop. Commands written to `rcon-in.txt` are sent to the
server (answers in `rcon-out.txt`). `--warlord N` makes engineer N silent, at war
with everyone from the start, and military first. `--strategist rules` uses a
scripted stand-in instead of the paid model.

## Talking to engineers

Chat `Name: message` (or `#id: message`); `everyone: message` reaches every
engineer. With the strategist connected they answer freely, in character; they
can agree to help, trade, join your team, offer peace, or refuse.

- `/npc help` lists the commands: `/npc status`, `/npc peace|ally|war <id>`,
  `/npc pact <id> <id>`, `/npc trades`, `/npc accept|reject <deal>`, `/npc teach`.
- The **Engineers** button on the shortcut bar opens a window with where every
  engineer stands with you and with each other, an engineer's open question
  (answer a "where?" with Mark location), Show an area (they copy part of your
  base), and a slot for teaching a blueprint or book.

## A team game

```
python tools/make_biter_world_save.py --engines 7 --out dist/team-7.zip
python tools/rehearse_biter_world.py --minutes 60 --llm --save dist/team-7.zip --run-dir tests/runs/team-7
```

The first makes a normal-world team map with every engineer's plot (add
`--death-world` for a death world); the second runs it headless with the
companion and writes `status.json`, the logs and a stuck report to the run
folder.

## Running the tests

```
python tests/run_parallel.py --jobs 4
cd designer && python -m unittest discover -s tests
```

`run_parallel.py` runs every test file in its own process; give it file names
to run only those.

Native tests start a private headless Factorio server:

```
python tools/run_designed_start_test.py --planner catalog --catalog tests/runs/library-smoke --no-designer --no-chooser --variant g
```

`--variant` picks the test map: a and b are the plain start (b with moved ore
and obstacles); c to h and j to n each add a stage, from more resources up to a
main bus (l), blue science on the bus (m) and the rocket section with a launch
to orbit (n); i has no room at home, so the engineer must scout and build at a
far patch; k has a far oil field to scout for and power. o puts biter nests near
a map-k base and sends waves (defense, rebuilding, an ally helping); p has an
armed, aggressive engineer raid the base. n, o and p resume a map-k save
(`--resume tests/runs/k-blue.zip`). Without `--no-designer`/`--no-chooser` the
test calls the paid OpenAI APIs. Test maps have no cliffs. `--strategist rules`
adds the scripted strategist stand-in.

Late stages take a long time to reach. `--save-when REGEX --save-name NAME`
saves the game the first time a log line matches; `--resume SAVE.zip` continues
from such a save with the current mod code. Touch `render-now` in the run folder
for a picture of the base, `save-now` to save, `stop-now` to end the run.

The proving ground sets up one situation on a flat practice map with the
Biter World mod set and scores it in a few real minutes, the game as fast as
the CPU allows and the rules AI answering (no LLM calls):

```
python tools/proving.py list
python tools/proving.py green-from-zero --port 34318 --rcon 27118
python tools/proving.py wall-under-attack --port 34318 --rcon 27118 --set size=30 --set evolution=0.4
python tools/proving.py batch all --parallel 2
```

`green-from-zero` starts one engineer at minute 0 with nothing given and scores
the opening, the bus lanes, the science block and the first green pack;
`blue-from-green` continues from the save it makes.

Each run writes `tests/runs/proving/<scenario>/score.json` (pass/fail per
check, the numbers, timings). The cached base save is remade when the mod set
or map settings change (`--fresh-base` forces it). A scenario is a setup and
score in `mod/scripts/proving.lua` plus its checks in `tools/proving.py`.

Module layouts are checked in a sandbox with endless inputs:

```
python tools/run_module_test.py processing_units 0.15 --space-age
python tools/run_module_test.py oil_complex --space-age
```

## License

MIT — see [LICENSE](LICENSE).
