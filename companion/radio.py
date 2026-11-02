"""How engineers sound in chat: like people on a game radio. The strategist's lines pass through
`line` before they reach the game (and the game checks them again): no em or en dashes, nor a spaced
hyphen standing in for one, plain quotes, at most two short sentences."""

from __future__ import annotations

import re

MAX = 140          # characters, the length of a quick radio call
SENTENCES = 2

_DASH = re.compile(r"\s*[—–―−]\s*")
_SPACED_HYPHEN = re.compile(r"(\S)\s+--?\s+(\S)")
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "…": "..."})


def line(text: str) -> str:
    s = _DASH.sub(", ", str(text))
    s = _SPACED_HYPHEN.sub(r"\1, \2", s)
    s = s.translate(_QUOTES)
    s = re.sub(r";\s*", ", ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r",\s*,", ",", s)
    s = re.sub(r",\s*([.!?])", r"\1", s)
    s = re.sub(r"^,\s*", "", s)
    ends = [m.start() for m in re.finditer(r"[.!?]+(?=\s)", s)]
    if len(ends) >= SENTENCES:
        cut = re.match(r"(.*?[.!?]+)\s", s[ends[SENTENCES - 1]:])
        s = s[:ends[SENTENCES - 1]] + (cut.group(1) if cut else "")
    if len(s) > MAX:
        cut = s[:MAX]
        space = cut.rfind(" ")
        s = (cut[:space] if space > MAX // 2 else cut).rstrip(", ")
    return s
