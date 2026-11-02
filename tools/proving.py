"""The proving ground: short practice worlds, each setting up one situation in seconds and scoring it in a few
real minutes, the game as fast as the CPU allows and the rules AI answering (catalog planner, rules strategist:
no LLM calls). A chat tests a fix in minutes; long runs only check that everything works together.

    python tools/proving.py SCENARIO [--port 34318 --rcon 27118] [--set key=value ...] [--limit MIN] [--keep]
    python tools/proving.py batch [all | NAME ...] [--parallel 2]
    python tools/proving.py list

Each scenario starts from a cached base save: a flat, cliff-free, empty map with the user's mods (NPC_MODS_FROM,
default tests/runs/mods-user-21), the team world on for one engineer, its plot laid out (starter ore and ponds)
and the engineer spawned. The base is remade when the mod set, the map settings or BASE_VERSION change (or with
--fresh-base). The worktree's mod is packaged for every run, so a scenario always runs the current code.

`/npc-proving setup {json}` (mod/scripts/proving.lua) builds the situation; `/npc-proving score` returns its
numbers. The result, with pass/fail per check, is tests/runs/proving/<scenario>/score.json, and a line on stdout.
The game runs at --speed (default 64) while nothing waits on the AI, at 1x while a plan is made (as in a live game,
so a request does not expire). Use your chat's own ports (Chat-Roster.md).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import attach_runner as ar  # noqa: E402
from stall_watch import StallWatch  # noqa: E402
import biter_world as bw  # noqa: E402
import package_mod  # noqa: E402

RUNS = ROOT / "tests" / "runs" / "proving"
MAP_GEN = ROOT / "tests" / "proving-map-gen-settings.json"
MODS_FROM = Path(os.environ.get("NPC_MODS_FROM") or ROOT.parent / "npc-biterworld" / "tests" / "runs" / "mods-user-21")
BASE_VERSION = 2   # bump when the base save must be made again (what it holds changed)
MARK = "NPC_PROVING_RESULT "
CHECKPOINTS = Path(os.environ.get("NPC_CHECKPOINTS") or ROOT.parent / "npc-biterworld" / "tests" / "runs" / "checkpoints")

# name: owners, params for /npc-proving setup, game-minute limit, checks (metric, op, value). A metric that is
# missing (never reached) fails its check.
SCENARIOS = {
    "wall-under-attack": {
        "owners": "Defense, Offense", "limit": 15,
        "params": {"waves": 3, "size": 20, "evolution": 0.2, "every": 3600, "first": 1800,
                   "kinds": {"small-biter": 3, "medium-biter": 1}, "wall_length": 64, "turrets": 4},
        "checks": [("defense_owned", "==", True), ("units_short", "<=", 0), ("done", "==", True),
                   ("engineer_alive", "==", True), ("breaches", "<=", 0), ("turrets_lost", "<=", 0),
                   ("turret_empty_seconds", "<=", 30)]},
    "build-through-obstacles": {
        "owners": "Worker, Factory", "limit": 20, "params": {"length": 40},
        "checks": [("obstacles_not_placed", "<=", 0), ("silently_missing", "<=", 0), ("belt_connected", "==", True), ("pipe_connected", "==", True),
                   ("engineer_alive", "==", True), ("done", "==", True)]},
    "opening-pace": {
        "owners": "Worker, System One", "limit": 50, "params": {},
        # (the user's pace aims are steam ~10, red ~25: the checks are the experienced range's far end)
        "checks": [("steam_minute", "<=", 15), ("lab_minute", "<=", 25), ("red_minute", "<=", 50),
                   ("engineer_alive", "==", True)]},
    # (F-21: from the red-science checkpoint, game minute 35 of long-65, through the bus's first lane to green by
    # machine; the user's pace aim for green is about 50: its minutes count from the checkpoint)
    "green-pace": {
        "owners": "Factory, System One", "limit": 25, "scenario": "opening-pace", "from": "practice:red-science",
        "params": {"run_to": 25, "rocks": 0, "trees": 0}, "checks": [("green_minute", "<=", 25), ("engineer_alive", "==", True)]},
    # (U-2, the user: green science, then blue. One engineer from minute 0 on the flat practice map (no nests, no
    # cliffs), nothing given: no creative supply, no energy interface, no instant builds; the stall watch and the
    # game's watchdog stop it. Scored on the bus's lanes standing, the Science Book's block 3 (params.block: a piece of
    # its plan's purpose, id or bus stage), its first green pack (by its machines: the block's packs share one bus
    # lane, an item a lane is named for says nothing of green), red and green a minute over the last five, and every wait of 3
    # minutes or more. The world is saved when green first runs: practice:green, blue-from-green's start)
    "green-from-zero": {
        "owners": "Factory (the bus and block 3), Worker, System One", "limit": 70, "scenario": "opening-pace",
        "params": {"run_to": 70, "block": "science-block"},   # (Factory's bus stage bus-science-block)
        "save_at": {"green_minute": "green"},
        "checks": [("green_minute", "<=", 70), ("engineer_alive", "==", True)]},
    # (and blue from there: oil processing researched, the rest of its chain due, still nothing given; the block is the
    # Science Book's block 5; a crude field is put near, the flat map having none)
    "blue-from-green": {
        "owners": "Factory (block 5), Designer (the oil plant), Research", "limit": 70, "scenario": "stage",
        "from": "practice:green",
        "params": {"stage": "blue_from_green", "power": False, "boost_mw": 0, "block": "science-book-5"},
        "checks": [("first_blue_minute", "<=", 70), ("engineer_alive", "==", True)]},
    "power-fault": {"owners": "Factory", "stub": True},
    # (O-5: a nest by its plot, what it has chosen; rich: it can afford to lose turrets, so it goes and creeps; poor:
    # the same nest, two turrets and a few magazines, no upgrades: it waits and stocks up)
    "nest-rich": {
        "owners": "Offense", "limit": 20, "scenario": "fight-odds",
        "params": {"minutes": 20, "spawners": 1, "turrets": 8, "magazines": 300, "pollute": 200, "evolution": 0.05,
                   "research": ["military", "gun-turret", "physical-projectile-damage-1", "weapon-shooting-speed-1"]},
        "checks": [("attacked", "==", True), ("nests_left", "<=", 0), ("engineer_alive", "==", True)]},
    "nest-poor": {
        "owners": "Offense", "limit": 20, "scenario": "fight-odds",
        "params": {"minutes": 20, "spawners": 3, "turrets": 2, "magazines": 20, "pollute": 200, "evolution": 0.3,
                   "research": ["military", "gun-turret"]},
        "checks": [("attacked", "==", False), ("engineer_alive", "==", True)]},
    "vehicle-doors": {"owners": "Movement", "stub": True},
    "death-world-waves": {
        "death_world": True, "owners": "Team (Defense, Offense read how it holds)", "limit": 30, "params": {"minutes": 30, "nests": 3},
        # (a softer start, then the ramp: a grace of about 9 minutes, a first wave its starting gun can beat, every
        # wave sized to its defense and the ramp growing)
        "checks": [("first_wave_minute", ">=", 8), ("first_wave_units", "<=", 4), ("waves", ">=", 3),
                   ("sized", "==", True), ("ramp_grows", "==", True), ("engineer_alive", "==", True)]},
    # (D-12: help weighed, "some, not all": a small wave its own turret holds wants no helper; a big one one or two,
    # who come back once it is over)
    "help-weighed-small": {
        "owners": "Defense", "limit": 8, "scenario": "help-weighed", "params": {"size": 4},
        "checks": [("helpers", "==", 0), ("engineer_alive", "==", True), ("helper_alive", "==", True)]},
    "help-weighed-big": {
        "owners": "Defense", "limit": 10, "scenario": "help-weighed", "params": {"size": 20},
        "checks": [("helpers", ">=", 1), ("helpers", "<=", 2), ("engineer_alive", "==", True),
                   ("helper_alive", "==", True)]},
    "help-weighed-unarmed": {   # (only its pistol: it brings nothing to the fight, it stays)
        "owners": "Defense", "limit": 6, "scenario": "help-weighed", "params": {"size": 20, "helper_armed": False},
        "checks": [("helpers", "==", 0), ("helper_alive", "==", True)]},
    "help-weighed-home-hit": {  # (its own plot hit two minutes before: it stays)
        "owners": "Defense", "limit": 6, "scenario": "help-weighed", "params": {"size": 20, "helper_hit_ago": 7200},
        "checks": [("helpers", "==", 0), ("helper_alive", "==", True)]},
    "death-world-waves-turrets": {
        "death_world": True, "owners": "Team (Defense, Offense read how it holds)", "limit": 30,
        "params": {"minutes": 30, "nests": 3, "turrets": 4},
        # (four loaded gun turrets round its home from the start: they fight, and the waves still come and grow)
        "checks": [("first_wave_minute", ">=", 8), ("waves", ">=", 3), ("sized", "==", True),
                   ("ramp_grows", "==", True), ("biters_killed", ">=", 5), ("engineer_alive", "==", True)]},
    # (F-11/F-12: steam power standing and handed to it as done, assemblers and poles carried, and what a turret row
    # needs known (electronics: inserters and poles; automation; turrets and walls): its defense supply by machine
    # measured alone, not behind the opening's power)
    # (OP-7: a later stage from its start, the research, power and materials given, so the stage itself is tested;
    # prototypes from base and Space Age in the user's set)
    # (OP-8, the user's oil practice world: a running mid-game base, creative supply, straight into oil and blue science;
    # made by tools/make_practice_world.py --stage oil)
    # (OP-8b: the engineer grows its own mid-game base in creative mode, each plan put down the moment it is accepted;
    # tools/make_practice_world.py --stage oil-bus saves it and puts the oil practice on top)
    "grow-bus": {
        "owners": "Optimization (the practice world's base)", "limit": 240, "scenario": "grow",
        # (rate: packs a minute of red and of green, belt-fed into a lab: a first bus is planned for 5 (factory_plan
        # bus_targets' least), so 50 measured nothing the engineer aims for)
        "params": {"minutes": 240, "rate": 5},
        "checks": [("bus_minute", "<=", 240), ("science_minute", "<=", 240), ("engineer_alive", "==", True)]},
    "oil-practice-bus": {
        "owners": "Designer (fluid layouts), Research (the chain), Factory (bus feeding), Worker (supply)", "limit": 40,
        "scenario": "stage", "from": "practice:oil-bus", "params": {"stage": "oil_practice", "power": False, "minutes": 40},
        "checks": [("first_blue_minute", "<=", 40), ("cracking_minute", "<=", 40), ("engineer_alive", "==", True)]},
    "oil-practice": {
        "owners": "Designer (fluid layouts), Research (the chain), Factory (bus feeding), Worker (supply)", "limit": 40,
        "scenario": "stage", "from": "practice:oil", "params": {"stage": "oil_practice", "power": False, "minutes": 40},
        "checks": [("first_blue_minute", "<=", 40), ("cracking_minute", "<=", 40), ("engineer_alive", "==", True)]},
    # (O-7, the user: "defend and attack correctly even late game": a running base, late research and kit, evolved
    # biters, nest clusters at three distances; made by tools/make_practice_world.py --stage late_combat)
    "late-combat": {
        "owners": "Offense (nests), Defense (waves: params waves)", "limit": 20,
        "scenario": "stage", "from": "practice:late_combat", "params": {"stage": "late_combat_practice", "power": False,
                                                                       "minutes": 20},
        "checks": [("nests_cleared", ">=", 1), ("engineer_alive", "==", True)]},
    # (late-combat from the grown bus, its wall ring taken away at the start and built again as its planners now
    # would: a change of the ring's geometry measured on a ring of its own, D-25)
    "late-combat-bus": {
        "owners": "Defense", "limit": 20,
        "scenario": "stage", "from": "practice:bus", "params": {"stage": "late_combat_practice", "power": False,
                                                                 "minutes": 20, "fresh_ring": True},
        "checks": [("engineer_alive", "==", True)]},
    "stage-oil": {
        "owners": "Factory, Worker, Research", "limit": 30, "scenario": "stage", "from": "practice:bus",
        "params": {"stage": "oil", "power": False},
        "checks": [("plastic_minute", "<=", 30), ("engineer_alive", "==", True)]},
    "stage-blue": {
        "owners": "Factory, Worker, Research", "limit": 30, "scenario": "stage", "from": "practice:bus",
        "params": {"stage": "blue", "power": False},
        "checks": [("blue_science_minute", "<=", 30), ("engineer_alive", "==", True)]},
    # (L-3/W-26b, NPC Ledger and NPC Worker: the robot network: roboports over the base from its own plans, robots in;
    # a ghost row the stage puts in its reach is built by robots from the network's stock, not from its pockets)
    "stage-robots": {
        "owners": "Ledger, Worker", "limit": 30, "scenario": "stage", "from": "practice:bus",
        "params": {"stage": "robots", "power": False},
        "checks": [("roboports_placed", ">=", 1), ("robots_in_network", ">=", 10), ("coverage_share", ">=", 0.5),
                   ("ghosts_built_by_bots", ">=", 8), ("engineer_belts_used", "<=", 0), ("engineer_alive", "==", True),
                   ("plan_pieces_by_network", ">=", 20), ("passed", "==", True)]},   # (20: W-26b's plan)
    # (DS-4, NPC Designer: nuclear power from a running base, the cells only what it makes; the game's facts logged)
    "stage-nuclear": {
        "owners": "Designer (the block and its sizing), Worker (fuel cells in, spent out)", "limit": 40, "scenario": "stage",
        "from": "practice:bus", "params": {"stage": "nuclear", "power": False, "reactors": 4, "boost_mw": 0,
                                    "goals": ["nuclear-power", "nuclear-fuel"]},
        "checks": [("turbines_minute", "<=", 40), ("cells_out_after_fuelled", "<=", 0),
                   ("idle_while_hot_samples", "<=", 0), ("engineer_alive", "==", True)]},
    "stage-silo": {
        "owners": "Factory, Worker, Research", "limit": 20, "scenario": "stage", "from": "practice:bus",
        "params": {"stage": "silo", "power": False},
        "checks": [("rocket_parts_minute", "<=", 20), ("engineer_alive", "==", True)]},
    # (R-8: the starter pack up on the silo's rocket and the platform's hub over Nauvis; later slices build it out)
    "stage-platform": {
        "owners": "Research (the road to space), Worker (the launch jobs)", "limit": 30, "scenario": "stage",
        "from": "practice:bus", "params": {"stage": "platform", "power": False},
        "checks": [("orbit_minute", "<=", 30), ("platform_built_minute", "<=", 30), ("materials_minute", "<=", 30), ("space_science_minute", "<=", 30), ("packs_in_lab_minute", "<=", 30), ("thrust_ready_minute", "<=", 30), ("defended_minute", "<=", 30), ("engineer_alive", "==", True)]},
    # (F-16b: a blueprint from the user's packs handed as an imported plan, about 5 game minutes: its hologram placed
    # and seen by the player's force (drawn when not allied), every piece built over its ghost from the inventory,
    # underground sides right, no ghost left at the end, on a forced hand-back, or on a move; no script error)
    "imported-block": {
        "owners": "Factory (Worker: the build loop)", "limit": 8, "prepare": "imported_plan",
        "params": {"label": "Automation + Logistic Science (60", "research": [
            "automation", "electronics", "logistics", "logistic-science-pack"], "allied": False},
        "checks": [("ghosts_placed", "==", True), ("drawn_for_players", "==", True), ("sides_right", "==", True),
                   ("built_over_ghosts", "==", True), ("from_inventory", "==", True), ("ghosts_left", "==", 0),
                   ("engineer_alive", "==", True)]},
    "imported-block-handback": {
        "owners": "Factory", "limit": 6, "scenario": "imported-block", "prepare": "imported_plan",
        "params": {"label": "Automation + Logistic Science (60", "research": [
            "automation", "electronics", "logistics", "logistic-science-pack"], "allied": False, "end": "handback"},
        "checks": [("ghosts_placed", "==", True), ("ended_by", "==", "handback"), ("ghosts_left", "==", 0),
                   ("drawings_left", "==", 0)]},
    "imported-block-relocate": {
        "owners": "Factory", "limit": 6, "scenario": "imported-block", "prepare": "imported_plan",
        "params": {"label": "Automation + Logistic Science (60", "research": [
            "automation", "electronics", "logistics", "logistic-science-pack"], "allied": False, "end": "relocate"},
        "checks": [("ghosts_placed", "==", True), ("ended_by", "==", "relocate"), ("ghosts_left", "==", 0),
                   ("drawings_left", "==", 0)]},
    # (D-19: its turrets in a row with power, inserters and belts known: the line behind them, measured by the share of
    # its time loading turrets by hand and the turrets empty at the waves)
    "death-world-waves-line": {
        "death_world": True, "owners": "Defense", "limit": 30, "scenario": "death-world-waves",
        "params": {"minutes": 30, "nests": 3, "power": True, "turrets": 4, "row": 20,
                   "give": {"transport-belt": 60, "inserter": 10, "small-electric-pole": 20},
                   "research": ["electronics", "automation", "military", "gun-turret", "stone-wall", "logistics"]},
        "checks": [("first_wave_minute", ">=", 8), ("waves", ">=", 3), ("engineer_alive", "==", True),
                   ("ammo_hand_share", "<=", 0.05)]},   # (its lines: the perimeter loop's scenario, D-19 step 2)
    # (W-26: early robots given; a plan of 12 furnaces and 4 chests, trees on four spots, put up by its robots)
    "robot-build": {
        "owners": "Worker", "limit": 8,
        "params": {"pieces": 12, "chests": 4, "trees": 4, "assemblers": 2, "underground": True},
        "checks": [("given", "==", True), ("built", "==", 20), ("robots_built", "==", 20), ("trees_left", "==", 0),
                   ("engineer_alive", "==", True)]},
    # (D-19 step 2: a wall ring standing round home with turrets on its north line, power, belts and inserters in
    # hand: the perimeter ammo loop laid, its arms feeding the ring's turrets, the hands off their ammunition)
    "death-world-waves-ring": {
        "death_world": True, "owners": "Defense", "limit": 40, "scenario": "death-world-waves",
        "params": {"minutes": 40, "nests": 3, "power": True, "ring": 50, "ring_turrets": 4,
                   "research": ["electronics", "automation", "military", "gun-turret", "stone-wall", "logistics"],
                   "give": {"transport-belt": 400, "underground-belt": 20, "inserter": 12,
                            "small-electric-pole": 80, "firearm-magazine": 1000, "iron-chest": 2}},
        "checks": [("waves", ">=", 3), ("engineer_alive", "==", True), ("turrets_fed", ">=", 4),
                   ("line_ammo_minute", "<=", 40), ("ammo_hand_share", "<=", 0.05), ("empty_most", "==", 0)]},
    "death-world-waves-power": {
        "death_world": True, "owners": "Factory (Defense reads how it holds)", "limit": 30, "scenario": "death-world-waves",
        "params": {"minutes": 30, "nests": 3, "power": True,
                   "research": ["electronics", "automation", "military", "gun-turret", "stone-wall"]},
        "checks": [("first_wave_minute", ">=", 8), ("waves", ">=", 3), ("sized", "==", True),
                   ("ramp_grows", "==", True), ("engineer_alive", "==", True), ("power", "==", True)]},
}
OPS = {"<=": lambda a, b: a <= b, ">=": lambda a, b: a >= b, "==": lambda a, b: a == b, "<": lambda a, b: a < b,
       ">": lambda a, b: a > b}


def imported_plan(probe, params: dict) -> dict:
    """A blueprint from the user's packs (params["label"]: a part of its label), converted as the importer converts it
    (companion/blueprint_packs.py: optional parts left out, settings kept) with this game's facts, as plan entities in
    its own tiles; and the items it takes, for the engineer's pockets."""
    from companion import blueprint_packs, designer_bridge
    folder = Path(params.get("folder") or "") if params.get("folder") else blueprint_packs.user_folder()
    if folder is None:
        return {**params, "prepare_error": "no Blueprints folder"}
    facts = designer_bridge.parse_result(probe.text("/npc-plan facts " + json.dumps({"npc_id": 1})))
    site = {"game_facts": facts, "prototypes": {}}
    prototypes = blueprint_packs.facts_prototypes(site)
    want = str(params["label"]).lower()
    for path in sorted(folder.glob("*.txt")):
        for _, text in blueprint_packs.strings(path.read_text(encoding="utf-8", errors="replace")):
            for books, blueprint in blueprint_packs.blueprints(blueprint_packs.decode(text)):
                if want in str(blueprint.get("label") or "").lower():
                    plan, skipped, missing = blueprint_packs.convert(blueprint, prototypes)
                    if missing:
                        return {**params, "prepare_error": f"{blueprint.get('label')}: {missing[:4]}"}
                    items: dict[str, int] = {}
                    for e in plan["entities"]:
                        item = str((prototypes.get(e["prototype"]) or {}).get("placement_item") or e["prototype"])
                        items[item] = items.get(item, 0) + 1
                    return {**params, "entities": plan["entities"], "items": items, "skipped": skipped,
                            "source": "catalog:import:" + "/".join((path.stem, *books, str(blueprint.get("label"))))}
    return {**params, "prepare_error": f"no blueprint labelled like {params['label']!r}"}


PREPARE = {"imported_plan": imported_plan}


def say(line: str) -> None:
    print(time.strftime("%H:%M:%S ") + line, flush=True)


def check(score: dict, checks) -> tuple[bool, list[dict]]:
    out = []
    for metric, op, want in checks:
        got = score.get(metric)
        try:
            ok = got is not None and OPS[op](got, want)
        except TypeError:
            ok = False
        out.append({"metric": metric, "op": op, "want": want, "got": got, "ok": ok})
    return all(c["ok"] for c in out), out


def base_key(mods_from: Path, mod_dir: Path | None = None) -> str:
    """Everything the base save is made from: the map settings, the mod set (names, sizes, settings) and our own
    mod's code (its engineer is spawned by it)."""
    h = hashlib.sha256(f"v{BASE_VERSION}".encode())
    for f in (MAP_GEN, mods_from / "mod-list.json", mods_from / "mod-settings.dat"):
        if f.exists():
            h.update(f.read_bytes())
    for z in sorted(mods_from.glob("*.zip")):
        if not z.name.startswith("npc-factions"):
            h.update(f"{z.name}:{z.stat().st_size}".encode())
    if mod_dir is not None:
        for f in sorted(mod_dir.rglob("*")):
            if f.is_file():
                h.update(f.relative_to(mod_dir).as_posix().encode())
                h.update(f.read_bytes())
    return h.hexdigest()[:12]


PRACTICE = ROOT / "tests" / "runs" / "practice"


def save_world(run: Path, probe, dest: Path) -> None:
    """The game as it stands saved to `dest` (save.zip and the engineer's library and experience): a practice start."""
    saved = run / "profile" / "saves" / "proving-end.zip"
    since = saved.stat().st_mtime_ns if saved.exists() else 0
    probe.text("/sc game.speed=1 rcon.print(game.speed)")
    probe.text("/save proving-end")
    bw.wait_file(saved, since)
    shutil.rmtree(dest, ignore_errors=True)
    (dest / "data").mkdir(parents=True)
    shutil.copy2(saved, dest / "save.zip")
    for part in ("blueprints", "experience"):
        if (run / "data" / part).exists():
            shutil.copytree(run / "data" / part, dest / "data" / part)


def with_goals(build, goals: list[str]):
    """Each engineer's planner made to choose `goals` in turn (params["goals"]): each the first time it is free to choose
    and that goal can be built, then the next; its own choice otherwise. A stage that tests a block itself hands its
    goal over, as stage-platform hands over its silo, instead of waiting on the chooser to offer it (NPC Designer)."""
    from companion.catalog_planner import PURPOSES, available

    def make(npc_id: int):
        planner = build(npc_id)
        # (a goal this code does not know is left out, said once: its chooser would fail on it every request)
        unknown = [g for g in goals if g not in PURPOSES]
        if unknown:
            planner.log(f"the stage's goals {', '.join(unknown)} are not known here: left to its own choice")
        choose, left = planner.choose_purpose, [g for g in goals if g in PURPOSES]

        def choose_purpose(site):
            if left and available(left[0], site, designer=planner.designer is not None):
                goal = left.pop(0)
                planner.log(f"{site.get('request_id')}: next goal {goal} (handed over by the stage)")
                return goal
            return choose(site)
        planner.choose_purpose = choose_purpose
        return planner
    return make


def source_of(name: str) -> Path:
    """Where a scenario starts: "practice:<stage>" a practice world (tools/make_practice_world.py), else a long run's
    checkpoint by name."""
    return PRACTICE / name.split(":", 1)[1] if name.startswith("practice:") else CHECKPOINTS / name


def make_base(mods_from: Path, ports: tuple[int, int], fresh: bool = False) -> Path:
    """The cached base save: flat map, team world on for one engineer, its plot laid out, the engineer spawned."""
    key = base_key(mods_from, ROOT / "mod")
    target = RUNS / "base" / f"base-{key}.zip"
    if target.exists() and not fresh:
        return target
    run = RUNS / "base" / f"build-{ports[0]}"
    shutil.rmtree(run, ignore_errors=True)
    run.mkdir(parents=True)
    say(f"making the base save ({key}): a flat map with {len(list(mods_from.glob('*.zip')))} mods")
    started = time.monotonic()
    config, mods = bw.prepare_run(run, package_mod.build(run / "dist"), mods_from)
    (run / "saves").mkdir(exist_ok=True)
    created = run / "saves" / "flat.zip"
    # (the game's own map settings: evolution and expansion as a normal world has them; the death world's are not
    # used, the user 2026-10-30: "stop doing the death world")
    args = [*bw.common_args(config, mods), "--create", str(created), "--map-gen-settings", str(MAP_GEN)]
    with (run / "create.log").open("w", encoding="utf-8") as out:
        subprocess.run(args, stdout=out, stderr=subprocess.STDOUT, timeout=600, creationflags=bw.CREATION_FLAGS,
                       check=True)
    saved = run / "profile" / "saves" / "proving-base.zip"
    with bw.Server(run, config, mods, created, game_port=ports[0], rcon_port=ports[1]) as server:
        rcon = server.connect()
        try:
            rcon.text("/sc rcon.print(1)")
            on = bw.parse(rcon.text('/npc-team on {"count":1,"death_world":false}'), bw.TEAM_MARK)
            if not on.get("active"):
                raise RuntimeError(f"the team world did not switch on: {on}")
            rcon.text("/sc game.speed=16 rcon.print(game.speed)")
            if bw.wait_ready(rcon).get("status") != "ready":
                raise RuntimeError("the team area was not ready in time")
            spawned = bw.parse(rcon.text("/npc-team spawn"), bw.TEAM_MARK)
            if not spawned.get("ok"):
                raise RuntimeError(f"no engineer spawned: {spawned}")
            rcon.text("/sc game.speed=1 rcon.print(game.speed)")
            time.sleep(2)
            since = saved.stat().st_mtime_ns if saved.exists() else 0
            rcon.text("/save proving-base")
            bw.wait_file(saved, since)
        finally:
            rcon.close()
        errors = server.errors()
    if errors:
        raise RuntimeError(f"script errors while making the base: {errors[:3]}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(saved, target)
    say(f"base save ready in {time.monotonic() - started:.0f} s: {target}")
    return target


def parse_set(pairs) -> dict:
    out = {}
    for pair in pairs or []:
        key, _, value = pair.partition("=")
        try:
            out[key] = json.loads(value)
        except json.JSONDecodeError:
            out[key] = value
    return out


def run_scenario(name: str, ports: tuple[int, int], *, overrides: dict | None = None, limit: float | None = None,
                 speed: float = 64, real_cap: float = 600, fresh_base: bool = False, mods_from: Path = MODS_FROM,
                 save_as: Path | None = None, keep: bool = False, stall: str = "stop",
                 save_at: dict[str, Path] | None = None, start_from: str | None = None) -> dict:
    spec = SCENARIOS[name]
    if start_from:   # (another start than its own: a checkpoint's name or practice:<name>, --from)
        spec = {**spec, "from": start_from}
    if spec.get("stub"):
        return {"scenario": name, "pass": None, "stub": True, "owners": spec["owners"]}
    t0 = time.monotonic()
    # (a later stage starts from a long run's checkpoint: a real, working base on the user's map; read, never written)
    start = source_of(spec["from"]) if spec.get("from") else None
    if start and not (start / "save.zip").exists():
        raise FileNotFoundError(f"no checkpoint save at {start}")
    if start and (RUNS / name).resolve() in [start.resolve(), *start.resolve().parents]:
        raise ValueError(f"the checkpoint {start} lies in the run folder, which is cleared first")
    base = start / "save.zip" if start else make_base(mods_from, ports, fresh_base)
    run = RUNS / name
    shutil.rmtree(run, ignore_errors=True)
    (run / "data").mkdir(parents=True)
    library = Path(os.environ.get("LOCALAPPDATA", "")) / "npc-factions" / "blueprints"
    if start:
        for part in ("blueprints", "experience"):
            if (start / "data" / part).exists():
                shutil.copytree(start / "data" / part, run / "data" / part)
    elif library.exists():
        shutil.copytree(library, run / "data" / "blueprints")
    os.environ["NPC_FACTIONS_DATA"] = str(run / "data")
    config, mods = bw.prepare_run(run, package_mod.build(run / "dist"), mods_from)
    (run / "saves").mkdir(exist_ok=True)
    served = run / "saves" / "base.zip"
    shutil.copy2(base, served)
    params = {**spec["params"], **(overrides or {}), "scenario": spec.get("scenario", name)}   # (variants share one)
    # (a scenario's own save points, {score metric: practice name}, with any the caller asks for)
    save_at = {**{k: PRACTICE / v for k, v in (spec.get("save_at") or {}).items()}, **(save_at or {})}
    if start and not spec["from"].startswith("practice:"):
        # (a long run's checkpoint was made on the death world: made a normal world on load, the user 2026-10-30)
        params.setdefault("normal_world", True)
    limit = limit or spec["limit"]
    if isinstance(params.get("run_to"), (int, float)):   # (a scenario asked to run on to a minute: no cap before it)
        limit = max(limit, params["run_to"] + 1)   # (and a sampling past it: its last counts are taken)
    result: dict = {"scenario": name, "owners": spec["owners"], "params": params, "limit_minutes": limit}
    with bw.Server(run, config, mods, served, game_port=ports[0], rcon_port=ports[1]) as server:
        probe = server.connect()
        try:
            probe.text("/sc rcon.print(1)")
            if spec.get("prepare"):   # (what the scenario needs from the game first: e.g. its facts)
                params = PREPARE[spec["prepare"]](probe, params)
                if params.get("prepare_error"):
                    result.update({"pass": False, "reason": f"prepare failed: {params['prepare_error']}"})
                    return finish(run, result, t0)
            setup = bw.parse(probe.text("/npc-proving setup " + json.dumps(params)), MARK)
            result["setup"] = setup
            if not setup.get("ok"):
                result.update({"pass": False, "reason": f"setup failed: {setup.get('reason')}"})
                return finish(run, result, t0)
            setup_s = time.monotonic() - t0
            from companion.designer_bridge import locked_log, make_planners
            log = locked_log(ar.make_log(run / "runner.log", echo=None))
            planners = make_planners("catalog", log=log, designer=False, chooser=False, ai="codex",
                                     catalog=run / "data" / "blueprints")
            if params.get("goals"):
                planners = with_goals(planners, list(params["goals"]))
            runner = ar.Runner(ar.Link(server.endpoint), run, planners=planners, strategist="rules", ai="codex",
                               log=log, experience=run / "data" / "experience")
            stop_at = time.monotonic() + real_cap
            threading.Thread(target=runner.run, kwargs={"until": stop_at + 5}, daemon=True).start()
            current, score, last_score = None, {}, 0.0
            slow_s, calm_from = 0.0, 0.0
            watch, last_watch = StallWatch(run, run / "server.log", mode=stall), 0.0
            while time.monotonic() < stop_at:
                agent = getattr(runner, "agent", None)
                busy = runner.bridge.busy() or bool(agent and getattr(agent, "running", None))
                if busy:   # (a finished plan is sent on the runner's next poll: 1x a moment longer)
                    calm_from = time.monotonic() + 2
                want = 1 if busy or time.monotonic() < calm_from else speed
                if want != current:
                    probe.text(f"/sc game.speed={want} rcon.print(game.speed)")
                    current = want
                if busy:
                    slow_s += 0.5
                if time.monotonic() - last_score >= 2:
                    last_score = time.monotonic()
                    score = bw.parse(probe.text("/npc-proving score"), MARK)
                    # (the world saved as each mark is first reached: save_at {score metric: where}, a practice start
                    # at that point; the game is held at 1x meanwhile)
                    for metric, dest in list((save_at or {}).items()):
                        if score.get(metric) is not None:
                            save_world(run, probe, dest)
                            say(f"saved at {metric} {score[metric]}: {dest}")
                            current = None
                            del save_at[metric]
                    if score.get("done") or (score.get("game_minutes") or 0) >= limit:
                        break
                if time.monotonic() - last_watch >= 10:   # (stuck: the run ends there, stuck.json says why)
                    last_watch = time.monotonic()
                    for stall in watch.look(probe):
                        say(f"[stuck] {stall['name']} {stall['why']} ({stall['phase']}, goal {stall['goal']})")
                    if watch.stop:
                        break
                time.sleep(0.5)
            (run / "stop-now").write_text("done", encoding="utf-8")
            score = bw.parse(probe.text("/npc-proving score"), MARK)
            try:   # (its plans as they stand: a refused or blocked one is read from here)
                (run / "plans.json").write_text(json.dumps(bw.parse(probe.text("/npc-proving plans"), MARK), indent=1),
                                                encoding="utf-8")
            except Exception as exc:   # (a dump is never worth the result)
                say(f"[proving] plans not written: {exc}")
            if keep:   # (the game left up, at 1x and its AI stopped, to look at by hand; ends with a file named keep-done)
                probe.text("/sc game.speed=1 rcon.print(game.speed)")
                say(f"kept: {run} (create keep-done there to stop)")
                while not (run / "keep-done").exists():
                    time.sleep(1)
            if save_as is not None:   # (the world as it ends, with the engineer's library: a practice world's base)
                save_world(run, probe, save_as)
        finally:
            probe.close()
        errors = server.errors()
    passed, checks = check(score, spec["checks"])
    result.update({"pass": passed and not errors and not watch.stop, "checks": checks, "score": score, "script_errors": errors[:10],
                   "timing": {"setup_s": round(setup_s, 1), "real_s": round(time.monotonic() - t0, 1),
                              "at_1x_s": round(slow_s, 1), "game_minutes": score.get("game_minutes")},
                   "ended": "stuck" if watch.stop else "done" if score.get("done") else "limit",
                   "stuck": watch.stalls})
    return finish(run, result, t0)


def finish(run: Path, result: dict, t0: float) -> dict:
    result.setdefault("timing", {"real_s": round(time.monotonic() - t0, 1)})
    (run / "score.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    failed = [f"{c['metric']}={c['got']}" for c in result.get("checks", []) if not c["ok"]]
    given = (result.get("score") or {}).get("given_power_mw")
    if given:   # (said with the result: power the base did not make, nobody reads it as its own)
        say(f"[proving] {result['scenario']}: {given} MW of its power is given by script (energy interfaces)")
    say(f"[proving] {result['scenario']}: {'PASS' if result.get('pass') else 'FAIL'} "
        f"in {result['timing'].get('real_s')} s real, {result['timing'].get('game_minutes')} game min"
        + (f"; failed {', '.join(failed)}" if failed else "") + (f"; {result['reason']}" if result.get("reason") else ""))
    return result


def batch(names: list[str], parallel: int, ports: tuple[int, int], extra: list[str]) -> int:
    """Each scenario in its own process (one after another, or `parallel` at once on ports 100 apart)."""
    # (a death-world scenario only when named: the user stopped the death world for now, 2026-10-30)
    names = [n for n in (names if names and names != ["all"] else
                         [k for k, v in SCENARIOS.items() if not v.get("death_world")])
             if not SCENARIOS[n].get("stub")]
    make_base(MODS_FROM, ports)   # (made once, before the processes share it)
    queue, results, lock = list(names), [], threading.Lock()

    def worker(slot: int) -> None:
        while True:
            with lock:
                if not queue:
                    return
                name = queue.pop(0)
            game, rcon = ports[0] + 100 * slot, ports[1] + 100 * slot
            path = RUNS / name / "score.json"
            path.unlink(missing_ok=True)   # (never an earlier run's score)
            done = subprocess.run([sys.executable, __file__, name, "--port", str(game), "--rcon", str(rcon), *extra])
            if path.exists():
                result = json.loads(path.read_text(encoding="utf-8"))
            else:
                result = {"scenario": name, "pass": False, "reason": f"no score (exit {done.returncode})"}
            if done.returncode not in (0, 1):
                result["pass"], result["reason"] = False, f"crashed (exit {done.returncode})"
            with lock:
                results.append(result)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(max(1, parallel))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for r in sorted(results, key=lambda r: r["scenario"]):
        say(f"  {r['scenario']:26s} {'PASS' if r.get('pass') else 'FAIL'}  {r.get('timing', {}).get('real_s')} s real")
    return 0 if all(r.get("pass") for r in results) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("scenario", help="a scenario, 'batch' or 'list'")
    parser.add_argument("names", nargs="*", help="batch: scenarios (default all)")
    parser.add_argument("--port", type=int, default=34318)
    parser.add_argument("--rcon", type=int, default=27118)
    parser.add_argument("--set", action="append", help="key=value (JSON) for the scenario's setup")
    parser.add_argument("--limit", type=float, help="game minutes at most")
    parser.add_argument("--speed", type=float, default=64)
    parser.add_argument("--real-cap", type=float, default=600, help="real seconds at most")
    parser.add_argument("--parallel", type=int, default=1)
    parser.add_argument("--fresh-base", action="store_true")
    parser.add_argument("--keep", action="store_true", help="leave the game up at the end (until a keep-done file)")
    parser.add_argument("--from", dest="start_from", help="start from this instead: a checkpoint or practice:<name>")
    parser.add_argument("--stall", choices=["stop", "record"], default="stop",
                        help="a stuck engineer ends the run (stop), or is only written to stuck.json (record)")
    args = parser.parse_args()
    if args.scenario == "list":
        for name, spec in SCENARIOS.items():
            tags = [t for t, on in (("stub", spec.get("stub")), ("death world", spec.get("death_world"))) if on]
            print(f"{name:26s} {spec['owners']:20s} {', '.join(tags)}")
        return 0
    if args.scenario == "batch":
        extra = [f"--speed={args.speed}", f"--real-cap={args.real_cap}"] + [f"--set={s}" for s in args.set or []]
        if args.limit:
            extra.append(f"--limit={args.limit}")
        if args.fresh_base:
            make_base(MODS_FROM, (args.port, args.rcon), fresh=True)
        return batch(args.names, args.parallel, (args.port, args.rcon), extra)
    if args.scenario not in SCENARIOS:
        parser.error(f"unknown scenario {args.scenario}; see 'list'")
    result = run_scenario(args.scenario, (args.port, args.rcon), overrides=parse_set(args.set), limit=args.limit,
                          speed=args.speed, real_cap=args.real_cap, fresh_base=args.fresh_base, keep=args.keep,
                          stall=args.stall, start_from=args.start_from)
    return 0 if result.get("pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
