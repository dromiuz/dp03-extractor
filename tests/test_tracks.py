from __future__ import annotations

import unittest

from dp03extract.tracks import TRACK_SLOT_START, parse_track_table


class TrackTableTests(unittest.TestCase):
    def test_parse_track_table_ignores_out_of_range_leaf_offsets(self):
        table = bytearray(b"\x00" * 0x100000)
        slot_offset = TRACK_SLOT_START

        table[slot_offset + 0x28:slot_offset + 0x2C] = bytes.fromhex("09000000")
        leaf_offset = len(table) - 2
        table[slot_offset + 0x60:slot_offset + 0x64] = leaf_offset.to_bytes(4, "big")

        tracks = parse_track_table(bytes(table))

        self.assertEqual(len(tracks), 8)
        self.assertTrue(all(track.master_offset is None for track in tracks))


if __name__ == "__main__":
    unittest.main()
