"""A run's milestones against the user's pace targets (experienced players; the aim is the faster half): the first
game minute each shows in the companion's site snapshots.

    python tools/pace_report.py long-36
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from companion import tier_path  # noqa: E402

RUN = Path(__file__).resolve().parents[1] / "tests" / "runs" / (sys.argv[1] if len(sys.argv) > 1 else "long-36")
# (milestone, experienced range in minutes, the aim)
TARGETS = [("steam power", (5, 15), 10), ("mostly electric mining", (10, 30), 22), ("red science automated", (20, 50), 25),
           ("green science automated", (45, 120), 50), ("main bus begun", (60, 180), 90),
           ("steel", (60, 180), 90), ("military science", (90, 240), 120)]


def machines(site, names):
    return sum(int((b.get("machines") or {}).get(n, 0)) for b in site.get("base") or [] for n in names)


def reached(site):
    done = set(site.get("completed_purposes") or [])
    out = set()
    if done & {"steam-power", "power-plant"}:
        out.add("steam power")
    electric, burner = machines(site, ["electric-mining-drill"]), machines(site, ["burner-mining-drill"])
    if electric >= 6 and electric >= 2 * burner:
        out.add("mostly electric mining")
    # (a pack line fed from chests by hand is not automated, the user 2026-10-29: its milestone waits on its belts)
    if (("red-science-automation" in done or (site.get("base_plan") or {}).get("stages"))
            and not tier_path.pack_hand_fed(site, "automation-science-pack")):
        out.add("red science automated")
    if "green-science-automation" in done and not tier_path.pack_hand_fed(site, "logistic-science-pack"):
        out.add("green science automated")
    if (site.get("base_plan") or {}).get("stages"):
        out.add("main bus begun")
    if "steel-smelting" in done or machines(site, ["steel-furnace"]) or "steel-plate" in str(site.get("base")):
        out.add("steel")
    if "military-science-automation" in done and not tier_path.pack_hand_fed(site, "military-science-pack"):
        out.add("military science")
    return out


first = {}
files = sorted((RUN / "sites").glob("*.json"), key=lambda p: p.stat().st_mtime)
last = 0.0
for f in files:
    try:
        s = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        continue
    minute = float(s.get("tick") or 0) / 3600
    last = max(last, minute)
    for m in reached(s):
        first.setdefault(m, minute)
print(f"{RUN.name}: snapshots to game minute {last:.0f}")
for name, (lo, hi), aim in TARGETS:
    at = first.get(name)
    if at is None:
        verdict = "not yet" + (f" (behind: past {hi}m)" if last > hi else "")
        print(f"  {name:26s}   --    experienced {lo}-{hi}m, aim {aim}m: {verdict}")
        continue
    verdict = "at the aim" if at <= aim else "experienced" if at <= hi else "slower than experienced"
    print(f"  {name:26s} {at:5.0f}m  experienced {lo}-{hi}m, aim {aim}m: {verdict}")
