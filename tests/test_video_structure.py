"""Split, merge and undo keep user work. No media stack needed beyond FFmpeg for thumbnails."""
import json
import os
import pytest
from skye.media.history import step, summary
from skye.media.importer import TEMPLATE, import_video
from skye.media.model import MediaError, files_for, load, revision, update
from skye.media.process import ffmpeg
from skye.media.structure import merge, split

pytestmark = pytest.mark.skipif(os.environ.get("SKYE_TEST_MEDIA") != "1", reason="needs FFmpeg")


@pytest.fixture
def root(tmp_path):
    src = tmp_path / "a.webm"
    ffmpeg("-f", "lavfi", "-i", "color=c=0x193047:s=160x90:r=30:d=2", "-f", "lavfi", "-i", "sine=d=2",
           "-c:v", "libvpx-vp9", "-deadline", "realtime", "-c:a", "libopus", str(src))
    p = import_video(tmp_path, src, scene_detection=False)
    return tmp_path / "media" / p["id"]


def edit(root, shot, **changes):
    f = files_for(root, shot)
    f.update(changes)
    return update(root, shot, revision(root), f)


def ids(root):
    return [(u["id"], u["start"], u["end"]) for u in load(root)["units"]]


def test_split_keeps_code_and_cuts_captions_at_the_playhead(root):
    edit(root, "shot-0001", **{"visual.html": TEMPLATE + "<!-- mine -->",
         "captions.json": json.dumps([{"start": 0, "end": 20, "text": "a"}, {"start": 40, "end": 55, "text": "b"}]),
         "audio.json": json.dumps({"gain": 0.5, "mute": False, "fade_frames": 8})})
    result = split(root, revision(root), "shot-0001", 30)
    assert ids(root) == [("shot-0001", 0, 30), ("shot-0002", 30, 60)]
    first, second = files_for(root, "shot-0001"), files_for(root, result["shot"])
    assert "<!-- mine -->" in first["visual.html"] and first["visual.html"] == second["visual.html"]
    assert json.loads(first["captions.json"]) == [{"start": 0, "end": 20, "text": "a"}]
    assert json.loads(second["captions.json"]) == [{"start": 10, "end": 25, "text": "b"}]
    assert json.loads(second["audio.json"])["gain"] == 0.5
    assert (root / "units" / "shot-0002" / "thumb.jpg").stat().st_size > 0


def test_split_across_a_caption_keeps_both_halves(root):
    edit(root, "shot-0001", **{"captions.json": json.dumps([{"start": 10, "end": 50, "text": "long"}])})
    split(root, revision(root), "shot-0001", 30)
    assert json.loads(files_for(root, "shot-0001")["captions.json"]) == [{"start": 10, "end": 30, "text": "long"}]
    assert json.loads(files_for(root, "shot-0002")["captions.json"]) == [{"start": 0, "end": 20, "text": "long"}]


def test_merge_keeps_work_and_says_what_it_dropped(root):
    split(root, revision(root), "shot-0001", 30)
    edit(root, "shot-0002", **{"visual.html": TEMPLATE + "<!-- right -->",
         "captions.json": json.dumps([{"start": 0, "end": 5, "text": "hi"}])})
    result = merge(root, revision(root), "shot-0001")
    assert ids(root) == [("shot-0001", 0, 60)]
    merged = files_for(root, "shot-0001")
    assert "<!-- right -->" in merged["visual.html"] and result["notes"] == []
    assert json.loads(merged["captions.json"]) == [{"start": 30, "end": 35, "text": "hi"}]
    step(root, revision(root), -1)
    edit(root, "shot-0001", **{"visual.html": TEMPLATE + "<!-- left -->"})
    note = merge(root, revision(root), "shot-0001")["notes"]
    assert note and "code was dropped" in note[0]


def test_undo_and_redo_restore_cuts_files_and_hide_nothing(root):
    start = revision(root)
    edit(root, "shot-0001", **{"visual.html": TEMPLATE + "<!-- one -->"})
    split(root, revision(root), "shot-0001", 20)
    assert summary(root) == {"undo": "Split shot 1", "redo": None}
    step(root, revision(root), -1)
    assert ids(root) == [("shot-0001", 0, 60)] and "<!-- one -->" in files_for(root, "shot-0001")["visual.html"]
    step(root, revision(root), -1)
    assert revision(root) == start
    with pytest.raises(MediaError, match="Nothing to undo"):
        step(root, revision(root), -1)
    step(root, revision(root), 1)
    step(root, revision(root), 1)
    assert ids(root) == [("shot-0001", 0, 20), ("shot-0002", 20, 60)]
    # Undone thumbnails match the restored cut again.
    step(root, revision(root), -1)
    assert json.loads((root / "units" / "shot-0001" / "unit.json").read_text())["end"] == 60


def test_new_change_after_undo_drops_redo_and_stale_ui_is_refused(root):
    old = revision(root)
    edit(root, "shot-0001", **{"visual.html": TEMPLATE + "<!-- a -->"})
    step(root, revision(root), -1)
    assert summary(root)["redo"] == "Edit shot 1"
    edit(root, "shot-0001", **{"visual.html": TEMPLATE + "<!-- b -->"})
    assert summary(root)["redo"] is None
    with pytest.raises(MediaError, match="changed"):
        split(root, old, "shot-0001", 10)
    with pytest.raises(MediaError, match="inside"):
        split(root, revision(root), "shot-0001", 0)
    with pytest.raises(MediaError, match="next shot"):
        merge(root, revision(root), "shot-0001")


def test_autosave_bursts_fold_into_one_undo_step(root):
    base = revision(root)
    for text in ("a", "ab", "abc"):
        f = files_for(root, "shot-0001")
        f["captions.json"] = json.dumps([{"start": 0, "end": 5, "text": text}])
        update(root, "shot-0001", revision(root), f, coalesce=True)
    split(root, revision(root), "shot-0001", 30)
    step(root, revision(root), -1)
    step(root, revision(root), -1)
    assert revision(root) == base and summary(root)["redo"] == "Edit shot 1"
    step(root, revision(root), 1)
    assert json.loads(files_for(root, "shot-0001")["captions.json"])[0]["text"] == "abc"


def test_srt_and_vtt_import_follow_the_cuts_and_undo_as_one_step(root):
    from skye.media.captions import import_captions, parse
    split(root, revision(root), "shot-0001", 30)
    srt = "1\n00:00:00,500 --> 00:00:01,500\nAcross the cut\n\n2\n00:00:01,600 --> 00:00:02,000\n<i>Second</i> shot\n"
    result = import_captions(root, revision(root), srt)
    assert result == {"revision": revision(root), "added": 3, "shots": 2}
    assert json.loads(files_for(root, "shot-0001")["captions.json"]) == [{"start": 15, "end": 30, "text": "Across the cut"}]
    assert json.loads(files_for(root, "shot-0002")["captions.json"]) == [
        {"start": 0, "end": 15, "text": "Across the cut"}, {"start": 18, "end": 30, "text": "Second shot"}]
    step(root, revision(root), -1)
    assert files_for(root, "shot-0001")["captions.json"].strip() == "[]"
    assert parse("WEBVTT\n\n00:01.000 --> 00:02.000\nHi")[0] == (30, 60, "Hi")
    with pytest.raises(MediaError, match="No captions"):
        parse("hello")
    with pytest.raises(MediaError, match="outside"):
        import_captions(root, revision(root), "1\n00:10:00,000 --> 00:10:01,000\nLate\n")
