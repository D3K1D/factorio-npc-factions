"""A practice world for one stage: a running mid-game base the engineer treats as its own, made once and kept, so a
scenario goes straight into the stage (tools/proving.py oil-practice).

    python tools/make_practice_world.py --stage oil [--from bus] [--port 34318 --rcon 27118]

The base is a long run's checkpoint (bus: run 60, minute 146; power, the main bus, red and green science automated,
its plot wall and turrets, all the engineer's own plans), on the user's map and mods. The stage's setup (/npc-proving
setup stage <stage>_practice) adds what the stage needs: its research done, an oil field and a pond on open ground, an
energy interface so power is no bottleneck, and creative supply (whatever it sets out to fetch is put in its pockets).
The world must run: in its first game minute each red and green line makes packs or stands full (this base's
labs are fed by hand, by the engineer), or no save is written. The result is
tests/runs/practice/<stage> (save.zip, data/, meta.json).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import biter_world as bw  # noqa: E402
import package_mod  # noqa: E402
import proving  # noqa: E402

# packs made so far by its red and green assemblers (their own counts: what a minute adds is what the base makes)
MADE = ("/sc __npc-factions__ local npc=storage.npcs[1] local s=npc.character.surface local out={tick=game.tick} "
        "for _,e in ipairs(s.find_entities_filtered{force=npc.force,type='assembling-machine'}) do "
        "local r=e.get_recipe() if r then out[r.name]=(out[r.name] or 0)+e.products_finished "
        "out['n:'..r.name]=(out['n:'..r.name] or 0)+1 "
        "if e.status==defines.entity_status.full_output then out['full:'..r.name]=(out['full:'..r.name] or 0)+1 end end end "
        "rcon.print(helpers.table_to_json(out))")
# the labs at work: the cheapest research still open that wants red and green packs, both and nothing else
RESEARCH = ("/sc __npc-factions__ local f=storage.npcs[1].force local best,cost "
            "for name,t in pairs(f.technologies) do if t.enabled and not t.researched then local ok=true "
            "for _,p in pairs(t.prerequisites) do if not p.researched then ok=false end end "
            "local has={} for _,i in ipairs(t.research_unit_ingredients) do has[i.name]=true if i.name~='automation-science-pack' "
            "and i.name~='logistic-science-pack' then ok=false end end "
            "ok=ok and has['automation-science-pack'] and has['logistic-science-pack'] "
            "local c=t.research_unit_count if ok and (not cost or c<cost or (c==cost and name<best)) then best,cost=name,c end end end "
            "if best then f.add_research(best) end rcon.print(tostring(best))")


def make(stage: str, source: str | Path, ports: tuple[int, int], log=print, name: str | None = None) -> dict:
    start = source if isinstance(source, Path) else proving.source_of(source)
    if not (start / "save.zip").exists():
        raise FileNotFoundError(f"no checkpoint save at {start}")
    out = proving.PRACTICE / (name or stage)
    run = proving.RUNS / f"make-practice-{stage}"
    shutil.rmtree(run, ignore_errors=True)
    (run / "data").mkdir(parents=True)
    for part in ("blueprints", "experience"):
        if (start / "data" / part).exists():
            shutil.copytree(start / "data" / part, run / "data" / part)
    os.environ["NPC_FACTIONS_DATA"] = str(run / "data")
    config, mods = bw.prepare_run(run, package_mod.build(run / "dist"), proving.MODS_FROM)
    (run / "saves").mkdir(exist_ok=True)
    served = run / "saves" / "base.zip"
    shutil.copy2(start / "save.zip", served)
    saved = run / "profile" / "saves" / f"practice-{stage}.zip"
    with bw.Server(run, config, mods, served, game_port=ports[0], rcon_port=ports[1]) as server:
        rcon = server.connect()
        try:
            rcon.text("/sc rcon.print(1)")
            setup = bw.parse(rcon.text("/npc-proving setup " + json.dumps(
                {"scenario": "stage", "stage": f"{stage}_practice", "power": False})), proving.MARK)
            if not setup.get("ok"):
                raise RuntimeError(f"the practice setup failed: {setup.get('reason')}")
            log(f"set up: {json.dumps(setup.get('setup'))[:400]}")
            log(f"research at work: {rcon.text(RESEARCH).strip()}")
            # the first game minute, fast, with no AI: does the base run?
            before = json.loads(rcon.text(MADE).strip())
            first = before["tick"]
            rcon.text("/sc game.speed=16 rcon.print(game.speed)")
            deadline = time.monotonic() + 120
            made = {}
            while time.monotonic() < deadline:
                made = json.loads(rcon.text(MADE).strip())
                if made["tick"] - first >= 3600:
                    break
                time.sleep(1)
            rcon.text("/sc game.speed=1 rcon.print(game.speed)")
            red = made.get("automation-science-pack", 0) - before.get("automation-science-pack", 0)
            green = made.get("logistic-science-pack", 0) - before.get("logistic-science-pack", 0)
            # (each line runs: it made packs this minute, or stands full with nothing taking its packs; this base's labs
            # are fed by hand, by the engineer)
            if made.get("tick", first) - first < 3600:
                raise RuntimeError(f"the first game minute did not pass in time ({made.get('tick', first) - first} ticks)")
            full = {n: bool(made.get(f"n:{n}")) and made.get(f"full:{n}", 0) == made.get(f"n:{n}")
                    for n in ("automation-science-pack", "logistic-science-pack")}
            log(f"first game minute: red {red:.0f}, green {green:.0f} made; full: {full}")
            stuck = [n for n, d in (("automation-science-pack", red), ("logistic-science-pack", green))
                     if d <= 0 and not full[n]]
            if stuck:
                raise RuntimeError(f"the base does not run: {stuck} made nothing and are not full, in the first minute")
            since = saved.stat().st_mtime_ns if saved.exists() else 0
            rcon.text(f"/save practice-{stage}")
            bw.wait_file(saved, since)
        finally:
            rcon.close()
        errors = server.errors()
    if errors:
        raise RuntimeError(f"script errors: {errors[:3]}")
    shutil.rmtree(out, ignore_errors=True)
    (out / "data").mkdir(parents=True)
    shutil.copy2(saved, out / "save.zip")
    for part in ("blueprints", "experience"):
        if (run / "data" / part).exists():
            shutil.copytree(run / "data" / part, out / "data" / part)
    meta = {"stage": stage, "from": str(start), "red_per_minute": red, "green_per_minute": green, "lines_full": full,
            "setup": setup.get("setup"), "made": time.strftime("%Y-%m-%d %H:%M")}
    (out / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    log(f"practice world: {out}")
    return meta


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stage", default="oil")
    ap.add_argument("--from", dest="source", default="practice:bus",
                    help="a checkpoint's name (tests/runs/checkpoints), or practice:<name>; the default is the last grow "
                         "that passed (a normal world: the user stopped the death world, 2026-10-30)")
    ap.add_argument("--port", type=int, default=34318)
    ap.add_argument("--rcon", type=int, default=27118)
    ap.add_argument("--real-cap", type=float, default=1500, help="oil-bus: real seconds the base may take to grow")
    args = ap.parse_args()
    if args.stage == "oil-bus":
        # (a stamped mid-game base: the engineer's own, grown in creative mode with each plan put down at once, then the
        # oil practice on top of it)
        grown = proving.PRACTICE / "grown-bus"
        # (and the world at the red-science mark: green-pace's start, practice:red-science; reaching the mark is what
        # makes it one, whatever the grow does after)
        red = proving.PRACTICE / "grown-red-science"
        result = proving.run_scenario("grow-bus", (args.port, args.rcon), real_cap=args.real_cap, save_as=grown,
                                      save_at={"red_minute": red})
        score = result.get("score") or {}
        print(f"grown: bus at {score.get('bus_minute')}, science at {score.get('science_minute')} game min; "
              f"red {score.get('red_per_minute')}/min, green {score.get('green_per_minute')}/min, "
              f"lab belt inserters {score.get('lab_belt_inserters')}, {score.get('stamped')} pieces stamped")
        if red.exists() and (result.get("score") or {}).get("red_minute") is not None:
            shutil.rmtree(proving.PRACTICE / "red-science", ignore_errors=True)
            shutil.copytree(red, proving.PRACTICE / "red-science")
            print(f"red science at {score.get('red_minute')} game min: {proving.PRACTICE / 'red-science'}")
        if not result.get("pass"):   # (a bus, science at its rate by belt-fed labs, alive, no script errors)
            print(f"the grow did not pass ({[c['metric'] for c in result.get('checks', []) if not c['ok']]}): "
                  f"not a practice world (the grown save is kept in {grown})")
            return 1
        # (a grow that passed is the start of the later stages too: practice:bus, kept apart from grown-bus, which a
        # grow that fails overwrites)
        shutil.rmtree(proving.PRACTICE / "bus", ignore_errors=True)
        shutil.copytree(grown, proving.PRACTICE / "bus")
        make("oil", grown, (args.port, args.rcon), name="oil-bus")
        return 0
    make(args.stage, args.source, (args.port, args.rcon))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
