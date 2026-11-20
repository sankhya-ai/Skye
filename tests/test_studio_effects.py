import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skye" / "studio"))
import effects  # noqa: E402


def test_shake_is_keyframes():
    css = effects.shake(amp=20, dur=0.4, name="s1")
    assert "@keyframes s1" in css and "-20px" in css and "100%" in css


def test_ramp_clips_split_and_timing():
    clips = effects.ramp_clips("assets/a.mp4", 10, 13, [0.5, 1, 3], at=5)
    assert len(clips) == 3
    assert clips[0]["time"] == [10, 11] and clips[0]["speed"] == 0.5 and clips[0]["at"] == 5
    assert clips[1]["time"] == [11, 12] and "speed" not in clips[1]
    # first piece lasts 1/0.5=2s on the timeline, so the second starts at 7
    assert clips[1]["at"] == 7
    assert clips[2]["speed"] == 3 and clips[2]["at"] == 8


def test_ramp_clips_rejects_bad_input():
    with pytest.raises(ValueError):
        effects.ramp_clips("a.mp4", 0, 1, [0.05], at=0)
    with pytest.raises(ValueError):
        effects.ramp_clips("a.mp4", 2, 1, [1], at=0)


def test_glitch_three_tracks():
    clips = effects.glitch_clips("a.mp4", 0, 2, at=4, px=12)
    assert [c["track"] for c in clips] == [0, 1, 2]
    assert "mix-blend-mode:screen" in clips[1]["style"]
    assert "steps(6, end)" in clips[1]["style"]


def test_swipe_directions():
    assert "inset(0 100% 0 0)" in effects.swipe_in("left")
    with pytest.raises(ValueError):
        effects.swipe_in("sideways")


def test_mirror_halves():
    clips = effects.mirror_clips("a.mp4", 0, 2, at=0)
    assert "inset(0 50% 0 0)" in clips[0]["style"]
    assert "scale: -1 1" in clips[1]["style"]


def test_pages_written(tmp_path):
    for name in effects.EFFECTS:
        params = {"image": "../assets/x.png"} if name in ("freeze-pop", "outline-glow") else {}
        if name == "caption-pop":
            params["words"] = ["hello", "*world*"]
        if name == "ktype":
            params["lines"] = ["one", "*two*"]
        if name == "sticker":
            params["emoji"] = "🔥"
        out = effects.render_effect(name, tmp_path, f"p-{name}", **params)
        page = (tmp_path / "pages" / f"p-{name}.html").read_text()
        assert "window.film" in page and out["src"].startswith("pages/")


def test_caption_pop_word_timing(tmp_path):
    effects.caption_pop(tmp_path, "cap", ["a", "b", "*c*"], per_word=0.3)
    page = (tmp_path / "pages" / "cap.html").read_text()
    assert "animation-delay:0.30s" in page and "animation-delay:0.60s" in page


def test_page_name_safety(tmp_path):
    with pytest.raises(ValueError):
        effects.flash(tmp_path, "../evil")
