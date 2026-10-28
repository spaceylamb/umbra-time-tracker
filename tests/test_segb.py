"""The SEGB container and protobuf wire-format reader.

The format was reverse-engineered from real files on 2026-08-15; Apple does
not document it. The fixture is a slice of a real file from
~/Library/Biome/.../App.InFocus/remote/ — hand-made bytes easily describe a
format that does not exist, and then the tests pass while production breaks.

Layout:
    header, 0x20 bytes:  "SEGB" + record count (u32) + created (double) + ...
    data:  [protobuf][zero padding to 4][CRC, 4 bytes][0a 00 00 00]  × N
    tail index (growing backwards):  [offset u32][state u32][timestamp double] × N
"""

import os
import struct
import unittest

from umbra import segb

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "fixtures", "segb_iphone_sample.bin")


class VarintTest(unittest.TestCase):
    def test_single_byte(self):
        self.assertEqual(segb.varint(b"\x01", 0), (1, 1))

    def test_multi_byte(self):
        # 300 = 0b100101100 → 0xac 0x02
        self.assertEqual(segb.varint(b"\xac\x02", 0), (300, 2))

    def test_reads_from_offset(self):
        self.assertEqual(segb.varint(b"\xff\x05", 1), (5, 2))


class ProtobufTest(unittest.TestCase):
    def test_length_delimited_string(self):
        # field 6, wire 2, len 3, "abc"
        d, end = segb.parse_message(b"\x32\x03abc", 0, 5)
        self.assertEqual(d[6][0], b"abc")
        self.assertEqual(end, 5)

    def test_varint_field(self):
        d, _ = segb.parse_message(b"\x18\x01", 0, 2)
        self.assertEqual(d[3][0], 1)

    def test_double_field(self):
        raw = b"\x21" + struct.pack("<d", 1234.5)
        d, _ = segb.parse_message(raw, 0, len(raw))
        self.assertAlmostEqual(d[4][0], 1234.5)

    def test_stops_at_padding(self):
        """Records are zero-padded to 4 bytes. Field number 0 is invalid; stop here."""
        d, end = segb.parse_message(b"\x18\x01\x00\x00", 0, 4)
        self.assertEqual(end, 2)

    def test_stops_on_absurd_length(self):
        """A length prefix out of range means we are reading CRC-like non-protobuf
        bytes."""
        d, end = segb.parse_message(b"\x32\xff\xff\xff\x7f", 0, 5)
        self.assertEqual(end, 0)

    def test_repeated_fields_are_collected(self):
        d, _ = segb.parse_message(b"\x18\x01\x18\x02", 0, 4)
        self.assertEqual(d[3], [1, 2])


class AppleTimeTest(unittest.TestCase):
    def test_epoch_offset(self):
        """Apple absolute time counts from 2001-01-01, not 1970."""
        self.assertEqual(segb.apple_ms(0.0), 978307200000)

    def test_keeps_sub_second(self):
        self.assertEqual(segb.apple_ms(0.5), 978307200500)


class ReadFileTest(unittest.TestCase):
    def setUp(self):
        self.recs = segb.read(FIX)

    def test_reads_records(self):
        self.assertTrue(self.recs, "nothing decoded at all")

    def test_declared_count_is_honoured(self):
        with open(FIX, "rb") as f:
            declared = struct.unpack_from("<I", f.read(8), 4)[0]
        self.assertLessEqual(len(self.recs), declared)

    def test_every_record_has_bundle_and_time(self):
        for r in self.recs:
            self.assertIn(6, r)
            self.assertIsInstance(r[6][0], bytes)
            self.assertIn(4, r)
            self.assertIsInstance(r[4][0], float)

    def test_timestamps_are_plausible(self):
        """The classic symptom of a wrong offset is timestamps in 1904 or 2400."""
        for r in self.recs:
            ms = segb.apple_ms(r[4][0])
            self.assertGreater(ms, 1_600_000_000_000)   # > 2020
            self.assertLess(ms, 2_000_000_000_000)      # < 2033

    def test_bundles_look_like_bundles(self):
        for r in self.recs:
            s = r[6][0].decode()
            self.assertIn(".", s)
            self.assertTrue(s.isprintable())

    def test_missing_file_returns_empty(self):
        self.assertEqual(segb.read(FIX + ".nope"), [])

    def test_truncated_file_does_not_raise(self):
        """A file may be mid-write during sync. Half a file must not break the
        whole import."""
        import tempfile
        raw = open(FIX, "rb").read()
        p = os.path.join(tempfile.mkdtemp(), "half.bin")
        with open(p, "wb") as f:
            f.write(raw[:len(raw) // 2])
        segb.read(p)          # not raising is the pass




class SchemaAgnosticTest(unittest.TestCase):
    """The reader must not hard-code one stream's field numbers.

    Every Biome stream has its own protobuf schema: App.InFocus uses f4 = time,
    f6 = bundle; Media.NowPlaying f2 = time, f15 = bundle. With App.InFocus's test
    inside read(), other streams would read nothing — silently, looking like
    "no data".
    """

    def test_read_accepts_a_validator(self):
        got = segb.read(FIX, valid=lambda r: False)
        self.assertEqual(got, [])

    def test_default_validator_is_permissive(self):
        """No schema assumed by default; judging the schema is the importer's job."""
        self.assertTrue(segb.read(FIX))

    def test_validator_sees_parsed_records(self):
        seen = []
        segb.read(FIX, valid=lambda r: seen.append(sorted(r)) or True)
        self.assertTrue(seen)
        self.assertIn(6, seen[0])

    def test_read_dir_passes_the_validator_through(self):
        d = os.path.dirname(FIX)
        self.assertEqual(segb.read_dir(d, valid=lambda r: False), [])

if __name__ == "__main__":
    unittest.main()
