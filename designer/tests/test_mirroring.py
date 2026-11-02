"""Mirrored machines (2.x): their connections flip left to right in their own frame."""

import unittest

from designer_llm._reference_contract import Entity
from designer_llm.functional_validation import mirrored_direction, port_point


class MirrorTests(unittest.TestCase):
    def test_a_mirrored_machine_has_its_connections_flipped_left_to_right(self):
        plain = Entity("r", "oil-refinery", 10.5, 10.5, 0, None)
        flipped = Entity("r", "oil-refinery", 10.5, 10.5, 0, None, True)
        self.assertEqual(port_point(plain, (-1, 2)), (9.5, 12.5))      # (the west inlet)
        self.assertEqual(port_point(flipped, (-1, 2)), (11.5, 12.5))   # (now the east one)
        turned = Entity("r", "oil-refinery", 10.5, 10.5, 4, None, True)
        self.assertEqual(port_point(turned, (-1, 2)), (8.5, 11.5))
        self.assertEqual(mirrored_direction(4, flipped), 12)
        self.assertEqual(mirrored_direction(8, flipped), 8)
        self.assertEqual(mirrored_direction(4, plain), 4)


if __name__ == "__main__":
    unittest.main()
