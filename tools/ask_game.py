"""Run one Lua snippet in the mod's context of a live run and print what it prints (rcon.print).

    python tools/ask_game.py RUN "lua code"

The RCON password is read from the run folder, never printed."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]   # (the repo)
sys.path.insert(0, str(ROOT))
from companion.rcon import RconClient  # noqa: E402

run = ROOT / "tests" / "runs" / sys.argv[1]
secret = (run / "rcon-password.txt").read_text().strip()
client = RconClient("127.0.0.1", 27131, secret, timeout=60)   # (tools/biter_world.py RCON_PORT)
print(client.text("/silent-command __npc-factions__ " + sys.argv[2]))
