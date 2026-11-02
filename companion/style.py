"""Each engineer's building style: why two engineers' bases never look alike.

The style comes from the engineer's traits and its personality seed, both fixed for life, so
one engineer builds the same way every time (its half-built bus never shifts) while two
engineers differ: where the base grows from home and how spread out it is, which side of the
bus its modules go on first, how much spare capacity it builds, whether the mall comes early,
how many lanes it keeps free, whether it pays for better machines, and whether it makes gears on
site (as the guides advise) or puts them on the bus.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class Style:
    heading: float = 0.0       # radians: the direction from home the base prefers to grow in
    spread: float = 0.0        # 0 builds as close to home as fits; 1 prefers room further out
    first_side: str = "east"   # bus side its modules take first
    spare: float = 1.1         # smelting and mining built this much above the goal
    mall_early: bool = False   # the mall right after the circuits, before the science modules
    reserve: int = 0           # bus lanes kept free beyond the planned ones
    cheap_machines: bool = False   # assembling machine 1 even when 2 is available
    gears_on_site: bool = False    # green science and engines make their own gears (the mall still takes the bus's)

    def code(self) -> str:
        """The style as a short string, kept with a bus plan so every later stage uses it."""
        return (f"{self.heading:.4f},{self.spread:.4f},{self.first_side},{self.spare:.3f},"
                f"{int(self.mall_early)},{self.reserve},{int(self.cheap_machines)},{int(self.gears_on_site)}")

    @classmethod
    def decode(cls, text: str | None) -> "Style":
        """A kept style; a bus planned before styles existed keeps the old default."""
        if not text:
            return cls()
        parts = str(text).split(",")
        heading, spread, side, spare, mall, reserve, cheap = parts[:7]
        on_site = len(parts) > 7 and parts[7] == "1"   # a bus planned before this choice put gears on the bus
        return cls(float(heading), float(spread), side, float(spare), mall == "1", int(reserve), cheap == "1",
                   on_site)

    def describe(self) -> str:
        compass = ["east", "south-east", "south", "south-west", "west", "north-west", "north", "north-east"]
        where = compass[round(self.heading / (math.pi / 4)) % 8]
        return (f"grows {where} ({'spread out' if self.spread > 0.5 else 'compact'}), modules {self.first_side} "
                f"first, {round((self.spare - 1) * 100)}% spare, mall {'early' if self.mall_early else 'late'}"
                + (f", {self.reserve} spare lanes" if self.reserve else "")
                + (", cheap machines" if self.cheap_machines else "")
                + (", gears made on site" if self.gears_on_site else ""))


def _unit(seed: str, salt: str) -> float:
    """A stable number in [0, 1) from the engineer's seed."""
    digest = hashlib.sha256(f"{seed}:{salt}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2 ** 64


def style_for(site: Mapping[str, Any]) -> Style:
    personality = site.get("personality") or {}
    traits = {str(t) for t in personality.get("traits") or []}
    seed = f"{personality.get('seed', '')}:{site.get('npc_id', '')}"
    tidy = bool(traits & {"methodical", "perfectionist", "traditionalist"})
    spare = 1.1
    if traits & {"cautious", "perfectionist", "hoarder"}:
        spare = 1.25
    elif traits & {"bold", "wasteful", "improviser"}:
        spare = 1.05
    spread = _unit(seed, "spread")
    if tidy:
        spread *= 0.5          # tidy engineers keep the base together
    elif traits & {"curious", "bold", "independent"}:
        spread = 0.5 + spread / 2
    return Style(heading=_unit(seed, "heading") * 2 * math.pi, spread=spread,
                 first_side="west" if _unit(seed, "side") < 0.5 else "east",
                 spare=spare, mall_early=bool(traits & {"merchant", "smart", "ambitious"}) or _unit(seed, "mall") < 0.25,
                 reserve=2 if traits & {"cautious", "perfectionist", "methodical"} else 0,
                 cheap_machines=bool(traits & {"frugal", "content"}),
                 gears_on_site=tidy or bool(traits & {"smart", "efficient"}) or _unit(seed, "gears") < 0.35)
