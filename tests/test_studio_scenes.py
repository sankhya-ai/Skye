"""Tests for the storyboard scene presets."""
import html
import tempfile
import unittest
from pathlib import Path

from skye.studio import scenes


class TestScenes(unittest.TestCase):
    def test_every_template_renders_a_full_page(self):
        cases = {
            "hook": dict(line1="You studied all night.", line2="It didn't study you."),
            "meme": dict(top="pov", bottom="tura"),
            "caption": dict(kicker="reads past papers", main="teaches what carries the marks"),
            "endcard": dict(title="TURA", subtitle="tutor for university exams", url="turalearn.com"),
            "shot": dict(image="../assets/overview.png", caption_text="your course, mapped"),
        }
        for name, kw in cases.items():
            page = scenes.render_scene(name, **kw)
            self.assertTrue(page.startswith("<!doctype html>"))
            self.assertIn("</html>", page)
            self.assertNotIn("@BODY@", page)
            self.assertIn("window.film={duration:", page)
            self.assertIn("frame(t)", page)
            for value in kw.values():
                want = value.upper() if name == "meme" else value
                self.assertIn(html.escape(want), page)

    def test_text_is_html_escaped(self):
        page = scenes.render_scene("hook", line1="<script>alert(1)</script>", line2="b & c")
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("b &amp; c", page)

    def test_unknown_template_raises(self):
        with self.assertRaises(KeyError):
            scenes.render_scene("nope")

    def test_write_scene_places_the_page_and_returns_clip_fields(self):
        with tempfile.TemporaryDirectory() as d:
            out = scenes.write_scene(d, "endcard", "end", title="TURA", subtitle="s", url="u")
            self.assertEqual(out["src"], "pages/end.html")
            self.assertEqual(out["len"], 4.0)
            written = Path(d, "pages", "end.html")
            self.assertTrue(written.is_file())
            self.assertIn("TURA", written.read_text())

    def test_write_scene_rejects_unsafe_names(self):
        with tempfile.TemporaryDirectory() as d:
            for bad in ("../x", "a/b", "a.html", "-lead"):
                with self.assertRaises(ValueError, msg=bad):
                    scenes.write_scene(d, "meme", bad, top="a", bottom="b")


if __name__ == "__main__":
    unittest.main()
