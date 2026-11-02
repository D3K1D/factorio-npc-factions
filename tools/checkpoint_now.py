"""Take a checkpoint of a running test by hand (as long_test.py does at a milestone): save the game, and keep
it with the engineer's library and experience in tests/runs/checkpoints/<name>.

    python tools/checkpoint_now.py RUN_DIR NAME [ACHIEVED,...]
"""
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]   # (the repo)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import biter_world as bw  # noqa: E402
from companion.rcon import RconClient  # noqa: E402

run, name = Path(sys.argv[1]), sys.argv[2]
achieved = [a for a in (sys.argv[3].split(",") if len(sys.argv) > 3 else []) if a]


def rcon_port(run):
    """The port this run's server bound (proving runs use each chat's own ports), else the live test's."""
    log = run / "server.log"
    found = re.search(r"rcon-bind\W+127\.0\.0\.1:(\d+)", log.read_text(encoding="utf-8", errors="replace")) if log.exists() else None
    return int(found.group(1)) if found else bw.RCON_PORT


client = RconClient("127.0.0.1", rcon_port(run), (run / "rcon-password.txt").read_text().strip(), timeout=60)
try:
    tick = int(client.text("/sc rcon.print(game.tick)").strip())
    client.text(f"/save cp-{name}")
finally:
    client.close()
saved = run / "profile" / "saves" / f"cp-{name}.zip"
size, deadline = -1, time.monotonic() + 120
while time.monotonic() < deadline:
    time.sleep(3)
    now = saved.stat().st_size if saved.exists() else -1
    if now > 0 and now == size:
        break
    size = now
target = ROOT / "tests" / "runs" / "checkpoints" / name
if target.exists():
    shutil.rmtree(target)
(target / "data").mkdir(parents=True)
shutil.copy2(saved, target / "save.zip")
for part in ("blueprints", "experience"):
    if (run / "data" / part).exists():
        shutil.copytree(run / "data" / part, target / "data" / part)
commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
meta = {"milestone": name, "tick": tick, "game_minutes": round(tick / 3600, 1), "run": run.name, "commit": commit,
        "achieved": sorted(set(achieved) | {name}), "resumed_from": None}
(target / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
print(f"checkpoint {name} at game minute {tick / 3600:.1f}: {target} ({(target / 'save.zip').stat().st_size // 1024} KB)")
