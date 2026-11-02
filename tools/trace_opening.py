"""Where the engineer's game time goes, job by job: what its site snapshots' time_use (a decaying tally of job time
only) cannot show. A run of tools/long_test.py with NPC_TRACE=1 samples the first engineer's current job every
~2 game seconds into tests/runs/RUN/trace.jsonl; this reads it.

    set NPC_TRACE=1 & set NPC_GAME_PORT=34319 & set NPC_RCON_PORT=27119
    python tools/long_test.py trace-1 20 20 4 rules
    python tools/trace_opening.py tests/runs/trace-1 [UNTIL_MINUTE]

Prints the plans with their game minutes, the time by job (its upkeep or plan tag, kind, item, and for mining what
it mined: a rock, a tree, ore) and the top jobs of each plan. Idle samples are named by the engineer's phase.
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

TRACE_LUA = (
    "/sc __npc-factions__ local npc=storage.npcs[1] if not npc then rcon.print('{}') return end "
    "local c=npc.character local j=npc.current_job local m=j and npc.design_jobs and npc.design_jobs[j.id] "
    "local inv=c and c.valid and c.get_main_inventory() "
    "local function n(i) return inv and prototypes.item[i] and inv.get_item_count({name=i,quality='normal'}) or 0 end "
    "local mt=m and (m.upkeep or (m.build and 'build') or (m.build_run and 'run') or (m.acquire and ('acq:'..m.acquire)) "
    "or (m.supply and 'supply') or (m.clear and 'clear') or (m.clearing and 'clearing') or (m.intention and 'agent') or 'meta') "
    "rcon.print(helpers.table_to_json({t=game.tick,ph=npc.phase,k=j and j.kind,i=j and (j.item or j.recipe or j.entity_name),"
    "c=j and j.count,mo=j and j.mode,tg=j and j.target and j.target.valid and j.target.name or nil,m=mt,"
    "x=c and c.valid and math.floor(c.position.x) or nil,y=c and c.valid and math.floor(c.position.y) or nil,"
    "q=c and c.valid and c.crafting_queue_size or 0,fe=n('iron-plate'),co=n('coal'),st=n('stone'),io=n('iron-ore'),"
    "cu=n('copper-plate'),wo=n('wood'),js=#(npc.jobs or {}),ds=npc.design and npc.design.status,"
    "dp=npc.design and npc.design.purpose,si=npc.design and npc.design.stage_index}))")


def sample(connect, stop, path):
    """Sample the first engineer's job until `stop` is set (a thread: its own RCON connection)."""
    with open(path, "w", encoding="utf-8") as out:
        try:
            link = connect()
        except Exception as exc:   # (no trace then: say so in the file, not silently)
            out.write(json.dumps({"err": f"no connection: {str(exc)[:100]}"}) + "\n")
            return
        while not stop.is_set():
            try:
                out.write(link.text(TRACE_LUA).strip() + "\n")
                out.flush()
            except Exception as exc:   # (a busy line: keep sampling)
                out.write(json.dumps({"err": str(exc)[:100]}) + "\n")
            stop.wait(0.5)
    link.close()


def analyze(run, until=1e12):
    rows = []
    for line in (Path(run) / "trace.jsonl").read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if "t" in r and r["t"] <= until:
            rows.append(r)
    by_tag, by_plan, plans = defaultdict(float), defaultdict(lambda: defaultdict(float)), []
    for a, b in zip(rows, rows[1:]):
        dt = (b["t"] - a["t"]) / 60
        if dt <= 0 or dt > 30:
            continue
        if a.get("k"):
            tag = f"{a.get('m')}:{a.get('k')}:{a.get('i')}" + (f"<{a.get('tg')}" if a.get("k") == "mine" and a.get("tg") else "")
        else:
            tag = "idle:" + ":".join(str(a.get("ph")).split(":")[:3])
        by_tag[tag] += dt
        by_plan[a.get("dp")][tag] += dt
        if not plans or plans[-1][0] != a.get("dp"):
            plans.append([a.get("dp"), a["t"], a["t"]])
        plans[-1][2] = b["t"]
    total = sum(by_tag.values()) or 1
    print(f"{Path(run).name}: {total / 60:.1f} game minutes traced ({len(rows)} samples)")
    print("\n== plans (game minute from - to)")
    for p, t0, t1 in plans:
        print(f"  {t0 / 3600:5.1f} - {t1 / 3600:5.1f}  {p}")
    print("\n== time by job (seconds, share)")
    for tag, s in sorted(by_tag.items(), key=lambda kv: -kv[1])[:40]:
        print(f"  {s:7.0f}s {s / total:5.1%}  {tag}")
    print("\n== by plan, top jobs")
    for p, tags in by_plan.items():
        tot = sum(tags.values()) or 1
        print(f"  -- {p}: {tot:.0f}s")
        for tag, s in sorted(tags.items(), key=lambda kv: -kv[1])[:10]:
            print(f"     {s:6.0f}s {s / tot:5.1%}  {tag}")


if __name__ == "__main__":
    analyze(sys.argv[1], float(sys.argv[2]) * 3600 if len(sys.argv) > 2 else 1e12)
