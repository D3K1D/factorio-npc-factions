"""Package the mod for players: dist/npc-factions_<version>.zip, the way Factorio loads a released mod.

    python tools/package_mod.py [output folder]

The zip holds one top folder, npc-factions_<version>/, with the mod's files (no caches, no run folders).
It is deterministic: the same files make the same bytes (sorted, fixed timestamps), so a friend's copy
can be compared with the host's.
"""

from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / "mod"
SKIP_DIRS = {"__pycache__", "profile", "runs", ".git"}
SKIP_SUFFIXES = {".pyc", ".log", ".zip"}
STAMP = (1980, 1, 1, 0, 0, 0)


def mod_info(mod: Path = MOD) -> tuple[str, str]:
    info = json.loads((mod / "info.json").read_text(encoding="utf-8"))
    name, version = info["name"], info["version"]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){2}", version):
        raise ValueError(f"unexpected mod name or version in info.json: {name!r} {version!r}")
    return name, version


def files(mod: Path = MOD) -> list[Path]:
    out = []
    for path in sorted(mod.rglob("*")):
        rel = path.relative_to(mod)
        if path.is_file() and not (set(rel.parts) & SKIP_DIRS) and path.suffix not in SKIP_SUFFIXES:
            out.append(path)
    return out


def build(out_dir: Path = ROOT / "dist", mod: Path = MOD) -> Path:
    """Write the zip and return its path."""
    name, version = mod_info(mod)
    top = f"{name}_{version}"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{top}.zip"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for path in files(mod):
            info = zipfile.ZipInfo(f"{top}/{path.relative_to(mod).as_posix()}", date_time=STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, path.read_bytes())
    return target


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist"
    target = build(out)
    print(f"{target} ({target.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
