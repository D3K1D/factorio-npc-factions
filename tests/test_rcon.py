"""The companion's RCON client (companion/rcon.py) against a fake local server."""

from __future__ import annotations

import socket
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from companion.rcon import RconClient, _read_packet, _write_packet  # noqa: E402


def listener():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    return sock, sock.getsockname()[1]


class RconTests(unittest.TestCase):
    def test_auth_and_command_framing(self):
        server_sock, port = listener()
        commands = []

        def server():
            conn, _ = server_sock.accept()
            with conn:
                rid, kind, payload = _read_packet(conn)
                self.assertEqual((rid, kind, payload), (1, 3, "secret"))
                _write_packet(conn, 1, 2, "")
                rid, kind, payload = _read_packet(conn)
                commands.append(payload)
                _write_packet(conn, rid, 0, "NPC_AGENT_RESULT {}")

        thread = threading.Thread(target=server)
        thread.start()
        with RconClient("127.0.0.1", port, "secret", timeout=0.5) as client:
            self.assertEqual(client.text("/npc-agent observe"), "NPC_AGENT_RESULT {}")
        thread.join(timeout=2)
        server_sock.close()
        self.assertEqual(commands, ["/npc-agent observe"])

    def test_a_command_waits_a_bounded_time(self):
        server_sock, port = listener()
        release = threading.Event()

        def server():
            conn, _ = server_sock.accept()
            with conn:
                rid, _, _ = _read_packet(conn)
                _write_packet(conn, rid, 2, "")
                _read_packet(conn)
                release.wait(1)

        thread = threading.Thread(target=server)
        thread.start()
        try:
            with RconClient("127.0.0.1", port, "secret", timeout=0.1) as client:
                with self.assertRaises(socket.timeout):
                    client.text("/npc-agent observe")
        finally:
            release.set()
            thread.join(timeout=2)
            server_sock.close()

    def test_only_a_local_game(self):
        with self.assertRaises(ValueError):
            RconClient("example.com", 27015, "secret")


if __name__ == "__main__":
    unittest.main()
