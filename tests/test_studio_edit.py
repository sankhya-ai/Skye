"""Tests for per-shot effect editing (skye.studio.edit)."""
import tempfile
import unittest
from pathlib import Path

from skye.studio import edit

FILM = '''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=1080, height=1920">
<style>
@keyframes old { 0% { scale: 1 } 100% { scale: 1 } }
</style></head>
<body>
<section>
  <iframe id="hook" src="pages/hook.html#t=0,2" at="0.2"></iframe>
</section>
<section>
  <video id="v1" src="assets/walk.mp4#t=5,8.4" at="0" style="object-fit:cover"></video>
  <video id="v2" src="assets/walk.mp4#t=15,17.4" at="3.4" style="object-fit:cover"></video>
  <video id="v3" src="assets/walk.mp4#t=30,34" at="5.8" speed="2" style="object-fit:cover"></video>
</section>
</body></html>'''


class TestShots(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        (root / 'film.html').write_text(FILM)
        (root / 'pages').mkdir()
        (root / 'assets').mkdir()

    def tearDown(self):
        self.dir.cleanup()

    def test_read_shots(self):
        shots = edit.read_shots(self.dir.name)
        self.assertEqual(len(shots), 3)
        self.assertEqual(shots[0].clip_id, 'v1')
        self.assertAlmostEqual(shots[1].at, 3.4)
        self.assertAlmostEqual(shots[1].dur, 2.4)
        # speed halves timeline duration
        self.assertAlmostEqual(shots[2].dur, 2.0)

    def test_shot_out_of_range(self):
        with self.assertRaises(edit.EditError):
            edit.apply_effect(self.dir.name, 9, 'flash')

    def test_overlay_effect_inserts_iframe_on_top_track(self):
        change = edit.apply_effect(self.dir.name, 2, 'caption-pop', text='code.|games.')
        text = (Path(self.dir.name) / 'film.html').read_text()
        self.assertIn('fx-cap-2-', text)
        self.assertTrue(list((Path(self.dir.name) / 'pages').glob('fx-cap-2-*.html')))
        # overlay clip sits at the shot's start + default offset
        self.assertIn('at="3.6"', text)
        # footage track untouched
        self.assertIn('id="v1"', text)
        self.assertEqual(change['effect'], 'caption-pop')

    def test_overlay_never_overlaps_an_existing_overlay(self):
        edit.apply_effect(self.dir.name, 1, 'flash', at=0.2)
        text = (Path(self.dir.name) / 'film.html').read_text()
        # 0.2..0.32 collides with the hook iframe (0.2..2.2): a new section opened
        self.assertEqual(text.count('<section'), 3)

    def test_shake_edits_shot_style_and_adds_keyframes_once(self):
        edit.apply_effect(self.dir.name, 1, 'shake', dur=0.4)
        edit.apply_effect(self.dir.name, 2, 'shake', dur=0.4)
        text = (Path(self.dir.name) / 'film.html').read_text()
        self.assertIn('animation:fx-shake-1 0.4s linear both', text)
        self.assertIn('@keyframes fx-shake-1', text)
        self.assertEqual(text.count('@keyframes fx-shake-1'), 1)
        self.assertIn('animation-delay:0.2s', text)

    def test_whip_out_delays_to_the_shot_end(self):
        edit.apply_effect(self.dir.name, 2, 'whip-out', dur=0.4)
        text = (Path(self.dir.name) / 'film.html').read_text()
        # shot 2 is 2.4s long: the 0.4s whip starts 2.0s in, shot-relative
        self.assertIn('animation-delay:2.0s', text)

    def test_ramp_replaces_the_shot_with_sequential_speed_clips(self):
        edit.apply_effect(self.dir.name, 1, 'ramp', speeds='0.75,1.5')
        text = (Path(self.dir.name) / 'film.html').read_text()
        self.assertIn('speed="0.75"', text)
        self.assertIn('speed="1.5"', text)
        # Balanced speeds preserve the shot and subsequent timeline.
        self.assertIn('at="2.267"', text)

    def test_mirror_splits_halves_and_flips_via_keyframes(self):
        edit.apply_effect(self.dir.name, 1, 'mirror')
        text = (Path(self.dir.name) / 'film.html').read_text()
        self.assertIn('clip-path:inset(0 50% 0 0)', text)
        self.assertIn('clip-path:inset(0 0 0 50%)', text)
        self.assertIn('@keyframes fx-mirror-flip-1', text)

    def test_text_effect_requires_text(self):
        with self.assertRaises(edit.EditError):
            edit.apply_effect(self.dir.name, 1, 'meme')

    def test_glitch_adds_two_blended_copies(self):
        edit.apply_effect(self.dir.name, 1, 'glitch', dur=0.7)
        text = (Path(self.dir.name) / 'film.html').read_text()
        self.assertIn('mix-blend-mode:screen', text)
        self.assertIn('@keyframes fx-glitch', text)


if __name__ == '__main__':
    unittest.main()
