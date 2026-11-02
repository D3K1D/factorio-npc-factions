"""A minimal local RCON client: the companion's line to the game (commands in, printed text out)."""

from __future__ import annotations

import socket
import struct
import threading
from typing import Any

# The game answers in one packet: observing 7 engineers under 60 mods is over a megabyte (each site export
# lists every unlocked recipe), so the cap is only a guard against garbage.
MAX_PACKET = 16 * 1024 * 1024
# A guard, not the game's limit: Factorio's RCON took a 1 MB command in a headless check (2026-10-25), and a bus
# stage of 483 buildings and 442 belts was over the old 64 KB cap (run 27: refused, it was planned again and again).
MAX_COMMAND = 4 * 1024 * 1024


class CommandTooLarge(ValueError):
    """A command bigger than MAX_COMMAND: nothing was sent, and the connection is fine."""


class ProtocolError(ValueError):
    """The game sent something that is not an RCON packet, or a command was too long."""


def _exact(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        part = sock.recv(size - len(data))
        if not part:
            raise ConnectionError("RCON connection closed")
        data.extend(part)
    return bytes(data)


def _read_packet(sock: socket.socket) -> tuple[int, int, str]:
    size = struct.unpack("<i", _exact(sock, 4))[0]
    if not 10 <= size <= MAX_PACKET:
        raise ProtocolError("RCON packet has invalid size")
    body = _exact(sock, size)
    if body[-2:] != b"\x00\x00":
        raise ProtocolError("RCON packet has invalid terminator")
    request_id, kind = struct.unpack("<ii", body[:8])
    return request_id, kind, body[8:-2].decode("utf-8", errors="strict")


def _write_packet(sock: socket.socket, request_id: int, kind: int, payload: str) -> None:
    encoded = payload.encode("utf-8")
    if len(encoded) > MAX_COMMAND:
        raise CommandTooLarge(f"RCON command of {len(encoded)} bytes exceeds the {MAX_COMMAND} limit")
    body = struct.pack("<ii", request_id, kind) + encoded + b"\x00\x00"
    sock.sendall(struct.pack("<i", len(body)) + body)


class RconClient:
    def __init__(self, host: str, port: int, password: str, timeout: float = 3.0):
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("RCON companion requires a local host")
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError("Invalid RCON port")
        if not password:
            raise ValueError("Empty RCON password")
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.next_id = 2
        self._lock = threading.Lock()   # one exchange at a time: designers fetch maps from their own threads
        try:
            _write_packet(self.sock, 1, 3, password)
            while True:
                rid, kind, _ = _read_packet(self.sock)
                if rid == -1:
                    raise PermissionError("RCON authentication failed")
                if rid == 1 and kind == 2:
                    break
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        self.sock.close()

    def __enter__(self) -> "RconClient":
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        self.close()

    def text(self, command: str) -> str:
        with self._lock:
            rid = self.next_id
            self.next_id = 2 if rid >= 2_000_000_000 else rid + 1
            _write_packet(self.sock, rid, 2, command)
            while True:
                response_id, kind, payload = _read_packet(self.sock)
                if response_id == -1:
                    raise PermissionError("RCON session lost authentication")
                if response_id == rid and kind == 0:
                    if not payload.strip():
                        continue
                    return payload
