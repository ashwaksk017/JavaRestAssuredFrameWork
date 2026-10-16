"""Tests for paste.py: images pasted for Cursor are kept only until the run
takes them.  python tools/agent/test_paste.py"""

from __future__ import annotations

import base64
import importlib.util
import os
import shutil
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("paste_under_test", os.path.join(HERE, "paste.py"))
paste = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(paste)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


class Pasting(unittest.TestCase):
    def setUp(self):
        self.job = tempfile.mkdtemp(prefix="paste-")
        self.addCleanup(shutil.rmtree, self.job, True)

    def test_what_is_saved_is_taken_once_and_its_files_are_gone(self):
        self.assertEqual(paste.save(self.job, [{"data": "data:image/png;base64," + b64(PNG)},
                                               {"data": b64(JPG)}]), 2)
        got = paste.take(self.job)
        self.assertEqual([g["mimeType"] for g in got], ["image/png", "image/jpeg"])
        self.assertEqual(base64.b64decode(got[0]["data"]), PNG)
        self.assertFalse(os.path.exists(os.path.join(self.job, paste.DIR)), "deleted once read")
        self.assertEqual(paste.take(self.job), [], "taken once")

    def test_the_type_is_read_from_the_bytes_not_from_the_page(self):
        with self.assertRaises(ValueError):
            paste.save(self.job, [{"data": "data:image/png;base64," + b64(b"<script>alert(1)</script>")}])
        with self.assertRaises(ValueError):
            paste.save(self.job, [{"data": "not base64 !!"}])

    def test_limits(self):
        with self.assertRaises(ValueError):
            paste.save(self.job, [{"data": b64(PNG)}] * (paste.MAX_IMAGES + 1))
        with self.assertRaises(ValueError):
            paste.save(self.job, [{"data": b64(PNG + b"\x00" * paste.MAX_EACH)}])

    def test_a_new_request_replaces_what_an_earlier_one_left(self):
        paste.save(self.job, [{"data": b64(PNG)}, {"data": b64(PNG)}])
        paste.save(self.job, None)
        self.assertEqual(paste.take(self.job), [])

    def test_images_of_a_request_that_never_ran_are_not_taken(self):
        paste.save(self.job, [{"data": b64(PNG)}])
        old = time.time() - paste.MAX_AGE - 5
        os.utime(os.path.join(self.job, paste.DIR), (old, old))
        self.assertEqual(paste.take(self.job), [])
        self.assertFalse(os.path.exists(os.path.join(self.job, paste.DIR)))


if __name__ == "__main__":
    unittest.main()
