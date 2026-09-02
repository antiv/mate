#!/usr/bin/env python3
"""
Tests for marking AI-generated images (EU AI Act Art. 50(2), from 2 Dec 2026).

Generated content must be machine-readably marked as artificially generated. The
interoperable marker is the IPTC digital source type `trainedAlgorithmicMedia`
carried in an XMP packet — what the major image generators write.

The risk in writing PNG chunks by hand is producing a file that carries the
marker and no longer decodes. So these tests do not stop at "the marker is
present": they walk every chunk and verify every CRC, which is what makes a PNG
readable at all.
"""

import os
import struct
import sys
import unittest
import zlib
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.content_marking import (
    DIGITAL_SOURCE_TYPE,
    PNG_SIGNATURE,
    is_marked_as_ai_generated,
    mark_png_as_ai_generated,
    read_xmp,
)


def _chunk(kind: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))


def tiny_png() -> bytes:
    """A 1x1 PNG built by hand, so the tests need no imaging library."""
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    idat = zlib.compress(b"\x00\x00\x00\x00\x00")
    return PNG_SIGNATURE + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


def walk_chunks(png: bytes):
    """Every chunk as (type, crc_ok). Raises if the structure does not parse."""
    assert png.startswith(PNG_SIGNATURE), "missing PNG signature"
    out, offset = [], 8
    while offset < len(png):
        length = struct.unpack(">I", png[offset:offset + 4])[0]
        kind = png[offset + 4:offset + 8]
        data = png[offset + 8:offset + 8 + length]
        stored_crc = struct.unpack(">I", png[offset + 8 + length:offset + 12 + length])[0]
        out.append((kind, stored_crc == (zlib.crc32(kind + data) & 0xFFFFFFFF)))
        offset += 12 + length
        if kind == b"IEND":
            break
    return out


class TestMarking(unittest.TestCase):

    def test_an_unmarked_image_is_reported_as_unmarked(self):
        self.assertFalse(is_marked_as_ai_generated(tiny_png()))

    def test_marking_adds_the_iptc_digital_source_type(self):
        marked = mark_png_as_ai_generated(tiny_png())
        self.assertTrue(is_marked_as_ai_generated(marked))
        self.assertIn(DIGITAL_SOURCE_TYPE, read_xmp(marked))

    def test_the_marked_file_is_still_a_structurally_valid_png(self):
        # The failure that matters: a marker in a file nothing can open.
        chunks = walk_chunks(mark_png_as_ai_generated(tiny_png()))
        kinds = [kind for kind, _ in chunks]
        self.assertEqual(kinds[0], b"IHDR", "IHDR must remain the first chunk")
        self.assertEqual(kinds[-1], b"IEND")
        self.assertIn(b"iTXt", kinds)
        self.assertIn(b"IDAT", kinds)
        for kind, crc_ok in chunks:
            self.assertTrue(crc_ok, f"bad CRC on {kind!r}")

    def test_the_original_image_data_is_untouched(self):
        original = tiny_png()
        marked = mark_png_as_ai_generated(original)
        idat = [c for c in walk_chunks(marked) if c[0] == b"IDAT"]
        self.assertEqual(len(idat), 1)
        # Every original chunk still appears, byte for byte.
        for kind in (b"IHDR", b"IDAT", b"IEND"):
            start = original.index(kind) - 4
            length = struct.unpack(">I", original[start:start + 4])[0]
            self.assertIn(original[start:start + 12 + length], marked)

    def test_the_description_reaches_the_metadata(self):
        marked = mark_png_as_ai_generated(tiny_png(), description="a cat on a bicycle")
        self.assertIn("a cat on a bicycle", read_xmp(marked))

    def test_a_prompt_with_xml_characters_does_not_break_the_packet(self):
        # Prompts are user text and go straight into an XML document.
        marked = mark_png_as_ai_generated(
            tiny_png(), description='<script>&"bad" prompt</script>')
        self.assertTrue(is_marked_as_ai_generated(marked))
        for kind, crc_ok in walk_chunks(marked):
            self.assertTrue(crc_ok)
        self.assertNotIn("<script>", read_xmp(marked))

    def test_marking_is_idempotent_enough_to_be_safe(self):
        once = mark_png_as_ai_generated(tiny_png())
        twice = mark_png_as_ai_generated(once)
        self.assertTrue(is_marked_as_ai_generated(twice))
        for kind, crc_ok in walk_chunks(twice):
            self.assertTrue(crc_ok)


class TestFailureBehaviour(unittest.TestCase):
    """An unmarked image is a compliance gap; a corrupted one is a broken product."""

    def test_a_non_png_is_passed_through_unchanged(self):
        for payload in (b"", b"not an image", b"\xff\xd8\xff\xe0 jpeg-ish"):
            with self.subTest(payload=payload[:12]):
                self.assertEqual(mark_png_as_ai_generated(payload), payload)

    def test_a_png_whose_first_chunk_is_not_ihdr_is_passed_through(self):
        malformed = PNG_SIGNATURE + _chunk(b"IDAT", b"x") + _chunk(b"IEND", b"")
        self.assertEqual(mark_png_as_ai_generated(malformed), malformed)

    def test_an_internal_failure_returns_the_original_rather_than_corruption(self):
        original = tiny_png()
        with patch("shared.utils.content_marking._itxt_chunk",
                   side_effect=ValueError("boom")):
            self.assertEqual(mark_png_as_ai_generated(original), original)

    def test_reading_a_non_png_yields_nothing_rather_than_raising(self):
        self.assertIsNone(read_xmp(b"not an image"))
        self.assertFalse(is_marked_as_ai_generated(b""))


if __name__ == "__main__":
    unittest.main()
