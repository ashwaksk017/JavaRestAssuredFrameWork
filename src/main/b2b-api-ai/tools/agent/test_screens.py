"""Pictures of the page for a workbench change's review (screens.py).

    python tools/agent/test_screens.py

No browser is started here: what is decided before one would be is.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("screens_under_test", os.path.join(HERE, "screens.py"))
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)


class WhatIsPictured(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="screens-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.out = os.path.join(self.root, ".workbench", "wb1")
        os.makedirs(os.path.join(self.out, "screens"))

    def review(self, *paths):
        with io.open(os.path.join(self.out, "review.json"), "w", encoding="utf-8") as fh:
            json.dump({"files": {"modified": list(paths), "created": []}}, fh)

    def test_the_tabs_are_the_top_bars_as_the_page_has_them(self):
        got = sc.tabs(os.path.dirname(os.path.dirname(HERE)))
        ids = [t for t, _n in got]
        self.assertIn("new", ids)
        self.assertIn("settings", ids)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(n for _t, n in got))

    def test_only_a_change_to_the_page_is_pictured_and_old_pictures_go(self):
        self.review("tools/agent/jobs.py")
        self.assertFalse(sc.touched_page(self.out))
        with io.open(os.path.join(self.out, "screens", "new.png"), "wb") as fh:
            fh.write(b"old")
        r = sc.capture("wb1", self.root, browser="never-started")
        self.assertIn("did not touch the page", r["skipped"])
        self.assertFalse(os.path.exists(os.path.join(self.out, "screens", "new.png")))
        self.review("tools/agent/ui/app.css")
        self.assertTrue(sc.touched_page(self.out))

    def test_a_picture_is_read_by_tab_name_only(self):
        with io.open(os.path.join(self.out, "screens", "run.png"), "wb") as fh:
            fh.write(b"\x89PNG")
        self.assertEqual(sc.picture(self.out, "run"), b"\x89PNG")
        for bad in ("../review", "run.png", "RUN", "", "a/b"):
            self.assertEqual(sc.picture(self.out, bad), b"", bad)


if __name__ == "__main__":
    unittest.main(verbosity=1)
