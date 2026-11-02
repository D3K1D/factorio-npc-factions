"""How a person drove in the drive lab: what to copy into the engineers' driving.

    python tools/analyze_drive_lab.py tests/runs/lab1

Reads drive_lab.jsonl (fifteen samples a second: position, heading, speed, keys, health, fuel, what is
ahead, biters about) and drive_lab_world.jsonl (twice a second: every tree, rock and cliff about the
vehicle, once each, and water cells) and prints, per vehicle:
  - time, distance, speed, keys (how often it accelerates, coasts, brakes, reverses, turns);
  - what hit it and how hard, and what it broke and at what speed;
  - rocks and trees passed: how close (edge to the vehicle's middle), how many hit;
  - avoiding a rock or water ahead: how far ahead (and how many ticks from it) the steering began;
  - speed with trees ahead and in turns; with biters about; reversing.
"""

from __future__ import annotations

import collections
import json
import math
import statistics
import sys
from pathlib import Path


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def pct(values, q):
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))]


def fmt(values, scale=1.0, digits=1):
    if not values:
        return "-"
    return (f"median {statistics.median(values) * scale:.{digits}f}, 10% {pct(values, 0.1) * scale:.{digits}f}, "
            f"90% {pct(values, 0.9) * scale:.{digits}f} (n={len(values)})")


def main() -> int:
    run = Path(sys.argv[1] if len(sys.argv) > 1 else "tests/runs/lab1")
    rows = load(run / "drive_lab.jsonl")
    world = load(run / "drive_lab_world.jsonl")
    samples = [r for r in rows if "v" in r and "s" in r]
    hits = [r for r in rows if "hit" in r]
    broke = [r for r in rows if "broke" in r]

    # the land, put back together: obstacles (kind, x, y, half-size) and water cells
    obstacles = []
    water = set()
    for w in world:
        for kind, x, y, sw, sh in w.get("new") or []:
            obstacles.append((kind, x, y, max(sw, sh) / 2))
        for cx, cy, n in w.get("wet") or []:
            if n >= 4:
                water.add((int(cx) // 4, int(cy) // 4))
    grid = collections.defaultdict(list)
    for o in obstacles:
        grid[(int(o[1] // 16), int(o[2] // 16))].append(o)

    def near(x, y, r):
        out = []
        for gx in range(int((x - r) // 16), int((x + r) // 16) + 1):
            for gy in range(int((y - r) // 16), int((y + r) // 16) + 1):
                for o in grid.get((gx, gy), ()):
                    if (o[1] - x) ** 2 + (o[2] - y) ** 2 <= r * r:
                        out.append(o)
        return out

    def is_rock(kind):
        return kind != "tree" and kind != "cliff"

    world_from = min((w["t"] for w in world), default=None)
    print(f"{run}: {len(samples)} samples, {len(hits)} hits, {len(broke)} broken; "
          f"{len(obstacles)} obstacles and {len(water)} water cells mapped (from tick {world_from})")

    for vehicle in sorted({s["v"] for s in samples}):
        vs = [s for s in samples if s["v"] == vehicle]
        print(f"\n=== {vehicle}: {len(vs) * 4 / 60:.0f} s driven")
        dist = 0.0
        for a, b in zip(vs, vs[1:]):
            if b["t"] - a["t"] <= 8:
                dist += math.hypot(b["x"] - a["x"], b["y"] - a["y"])
        speeds = [abs(s["s"]) for s in vs]
        print(f"distance {dist:.0f} tiles; speed {fmt(speeds, 1, 3)}; max {max(speeds):.3f}")
        keys = collections.Counter(s.get("a") for s in vs)
        turns = collections.Counter(s.get("d") for s in vs)
        n = len(vs)
        print("keys: " + ", ".join(f"{k} {100 * c / n:.0f}%" for k, c in keys.most_common()))
        print("steer: " + ", ".join(f"{k} {100 * c / n:.0f}%" for k, c in turns.most_common()))
        turning = [s for s in vs if s.get("d") in ("left", "right")]
        if turning:
            tk = collections.Counter(s.get("a") for s in turning)
            print("while turning: " + ", ".join(f"{k} {100 * c / len(turning):.0f}%" for k, c in tk.most_common())
                  + f"; speed {fmt([abs(s['s']) for s in turning], 1, 3)}")
        fuel = collections.Counter(s.get("f") for s in vs)
        print(f"fuel: {dict(fuel)}")

        # trees ahead (from the samples' own look ahead) and speed
        def trees_ahead(s, within=12):
            a = s.get("ahead") or []
            return sum(1 for o in a if isinstance(o, list) and o[0] == "tree" and o[1] < within and abs(o[2]) < 2)
        clear = [abs(s["s"]) for s in vs if not trees_ahead(s, 26)]
        some = [abs(s["s"]) for s in vs if 1 <= trees_ahead(s) <= 2]
        many = [abs(s["s"]) for s in vs if trees_ahead(s) >= 3]
        print(f"speed, nothing ahead: {fmt(clear, 1, 3)}")
        print(f"speed, 1-2 trees in the way within 12: {fmt(some, 1, 3)}")
        print(f"speed, 3+ trees in the way within 12: {fmt(many, 1, 3)}")

        # rocks and trees passed: closest approach of the vehicle's middle to their edge, moving
        times = [s["t"] for s in vs]
        t0, t1 = min(times), max(times)
        mapped = [s for s in vs if world_from is not None and s["t"] >= world_from and abs(s["s"]) > 0.1]
        closest = {}
        for s in mapped:
            for o in near(s["x"], s["y"], 10):
                d = math.hypot(o[1] - s["x"], o[2] - s["y"]) - o[3]
                key = (o[1], o[2])
                if key not in closest or d < closest[key][0]:
                    closest[key] = (d, o[0], abs(s["s"]))
        rocks = [d for d, kind, sp in closest.values() if is_rock(kind)]
        trees = [d for d, kind, sp in closest.values() if kind == "tree"]
        cliffs = [d for d, kind, sp in closest.values() if kind == "cliff"]
        print(f"rocks within 10 tiles: {len(rocks)}; closest edge {fmt(rocks)}; within 2 tiles {sum(1 for d in rocks if d < 2)}")
        print(f"trees within 10 tiles: {len(trees)}; closest edge {fmt(trees)}; within 1 tile {sum(1 for d in trees if d < 1)}")
        if cliffs:
            print(f"cliffs within 10 tiles: {len(cliffs)}; closest edge {fmt(cliffs)}")

        # avoiding a rock ahead: from when it is first in the way (within 2.5 tiles of the line ahead,
        # within 26 tiles) to the first steer away, how far ahead it was and how many ticks off
        react_dist, react_ticks, no_steer = [], [], 0
        seen = {}
        for i, s in enumerate(vs):
            for o in s.get("ahead") or []:
                if not isinstance(o, list) or o[0] == "tree" or o[0] == "cliff":
                    continue
                along, side = o[1], o[2]
                if abs(side) > 2.5 or abs(s["s"]) < 0.1:
                    continue
                key = (round(s["x"] + 0), o[0])   # one per rock per approach: keyed below by position
                rx = s["x"] + math.sin(s["o"] * 2 * math.pi) * along + math.cos(s["o"] * 2 * math.pi) * side
                ry = s["y"] - math.cos(s["o"] * 2 * math.pi) * along + math.sin(s["o"] * 2 * math.pi) * side
                rkey = (round(rx), round(ry))
                if rkey in seen and s["t"] - seen[rkey] < 300:
                    continue
                seen[rkey] = s["t"]
                steered = None
                for later in vs[i:i + 60]:
                    if later["t"] - s["t"] > 240:
                        break
                    if later.get("d") in ("left", "right"):
                        steered = later
                        break
                if steered is None:
                    no_steer += 1
                    continue
                gone = math.hypot(steered["x"] - s["x"], steered["y"] - s["y"])
                react_dist.append(max(0.0, along - gone))
                react_ticks.append(max(0.0, (along - gone) / max(0.05, abs(steered["s"]))))
        print(f"rock in the way ahead: steering began {fmt(react_dist)} tiles before it, "
              f"{fmt(react_ticks, 1, 0)} ticks before reaching it; {no_steer} not steered for")

        # water ahead
        wet = [s for s in vs if s.get("water", -1) >= 0 and abs(s["s"]) > 0.1]
        if wet:
            print(f"water ahead seen at {fmt([s['water'] for s in wet])} tiles; speed then {fmt([abs(s['s']) for s in wet], 1, 3)}; "
                  "keys " + str(dict(collections.Counter(s.get('a') for s in wet))))

        # biters about
        bit = [s for s in vs if s.get("enemies")]
        if bit:
            near_bit = [s for s in bit if s.get("nearest") and s["nearest"][1] < 15]
            print(f"biters within 40: {len(bit) * 4 / 60:.0f} s; speed {fmt([abs(s['s']) for s in bit], 1, 3)}; "
                  f"shooting {100 * sum(1 for s in bit if s.get('shoot')) / len(bit):.0f}% of it; within 15 tiles "
                  f"{len(near_bit) * 4 / 60:.0f} s, speed {fmt([abs(s['s']) for s in near_bit], 1, 3)}")

        # reversing
        episodes, cur = [], None
        for s in vs:
            if s.get("a") == "reversing":
                if cur is None:
                    cur = {"start": s, "n": 0}
                cur["n"] += 1
            elif cur is not None:
                episodes.append(cur)
                cur = None
        if episodes:
            print(f"reversing: {len(episodes)} times, {fmt([e['n'] * 4 for e in episodes], 1, 0)} ticks each; "
                  f"speed when it began {fmt([abs(e['start']['s']) for e in episodes], 1, 3)}; "
                  "steering then " + str(dict(collections.Counter(e['start'].get('d') for e in episodes))))

    # what hit it, what it broke
    print("\n=== hits (damage over 0.5)")
    by = collections.defaultdict(list)
    for h in hits:
        if h["damage"] > 0.5:
            by[(h["hit"], h.get("kind"))].append(h)
    for (cause, kind), hs in sorted(by.items(), key=lambda kv: -sum(h["damage"] for h in kv[1])):
        print(f"  {cause} ({kind}): {len(hs)} times, {sum(h['damage'] for h in hs):.0f} damage, "
              f"speed {fmt([abs(h['s']) for h in hs], 1, 3)}")
    print("=== broken")
    bb = collections.defaultdict(list)
    for b in broke:
        bb["tree" if "tree" in b["broke"] or "trunk" in b["broke"] else b["broke"]].append(b)
    for kind, bs in sorted(bb.items(), key=lambda kv: -len(kv[1])):
        print(f"  {kind}: {len(bs)}, speed {fmt([abs(b['s']) for b in bs], 1, 3)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
