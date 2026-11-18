"""The media contract and trust boundary do not require media dependencies."""
import json
from pathlib import Path
import pytest
from skye.media import handoff
from skye.media.importer import TEMPLATE
from skye.media.model import (MediaError, files_for, load, project_dir, revision, safe_file,
                              update, validate_edits, write_json)
from skye.media.renderer import cache_key


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "media" / ("a" * 16)
    root.mkdir(parents=True)
    p = {"version": 1, "id": "a"*16, "name": "Test", "fps": 30, "frames": 60,
         "width": 480, "height": 270, "source_hash": "abc", "has_audio": True,
         "units": [{"id": "shot-0001", "start": 0, "end": 30}, {"id": "shot-0002", "start": 30, "end": 60}]}
    write_json(root / "project.json", p)
    for u in p["units"]:
        folder = root / "units" / u["id"]
        folder.mkdir(parents=True)
        (folder / "visual.html").write_text(TEMPLATE)
        write_json(folder / "captions.json", [])
        write_json(folder / "audio.json", {"gain":1,"mute":False,"fade_frames":0})
    return root


def test_single_shot_transaction_and_stale_guard(project):
    old = revision(project)
    neighbour = files_for(project, "shot-0002")
    f = files_for(project, "shot-0001")
    f["visual.html"] += "<!-- revised -->"
    new = update(project, "shot-0001", old, f)
    assert new != old
    assert files_for(project, "shot-0002") == neighbour
    with pytest.raises(MediaError, match="changed"):
        update(project, "shot-0001", old, f)


@pytest.mark.parametrize("caption", [
    [{"start":-1,"end":10,"text":"no"}], [{"start":0,"end":31,"text":"no"}],
    [{"start":0.0,"end":1,"text":"no"}], [{"start":True,"end":1,"text":"no"}],
    [{"start":1,"end":1,"text":"no"}], [{"start":0,"end":1,"text":42}], {},
])
def test_reject_caption_escape(project, caption):
    f = files_for(project, "shot-0001"); f["captions.json"] = json.dumps(caption)
    with pytest.raises(MediaError): validate_edits(f, 30)


@pytest.mark.parametrize("patch", [{"gain":float("nan")}, {"gain":5}, {"mute":1}, {"fade_frames":16}, {"path":"/private"}])
def test_reject_audio_escape(project, patch):
    f = files_for(project, "shot-0001"); a = json.loads(f["audio.json"]); a.update(patch)
    f["audio.json"] = json.dumps(a)
    with pytest.raises(MediaError): validate_edits(f, 30)


def test_scope_extra_files_and_symlink(project):
    f = files_for(project, "shot-0001"); f["other.html"] = "x"
    with pytest.raises(MediaError): validate_edits(f, 30)
    (project / "escape").symlink_to(project / "project.json")
    with pytest.raises(MediaError): safe_file(project, "escape")
    with pytest.raises(MediaError): safe_file(project, "../outside")
    with pytest.raises(MediaError): project_dir(project.parent.parent, "../x")


def test_fixed_boundaries(project):
    p=load(project);p["units"][1]["start"]=31;write_json(project/"project.json",p)
    with pytest.raises(MediaError): load(project)


def test_cache_separates_picture_and_audio(project):
    p=load(project);u=p["units"][0];f=files_for(project,u["id"])
    picture=cache_key(p,u,f,"preview","picture");audio=cache_key(p,u,f,"audio","audio")
    f["audio.json"]='{"gain":0,"mute":true,"fade_frames":0}'
    assert cache_key(p,u,f,"preview","picture")==picture
    assert cache_key(p,u,f,"audio","audio")!=audio
    assert cache_key(p,u,f,"final","picture")!=picture


def test_scoped_handoff_has_real_goal_and_rejects_extra_files(project):
    result=handoff.prepare(project,"shot-0001","Make the title smaller", "codex")
    job=json.loads(Path(result["job"]).read_text())
    assert "Make the title smaller" in job["brief"]
    assert job["adapter"]=="codex"
    folder=Path(result["path"])
    (folder/"neighbour.html").write_text("bad")
    with pytest.raises(MediaError,match="outside"): handoff.apply(project,result["proposal"])
    (folder/"neighbour.html").unlink()
    (folder/"visual.html").write_text(TEMPLATE+"<!-- new -->")
    before=files_for(project,"shot-0002")
    handoff.apply(project,result["proposal"])
    assert files_for(project,"shot-0002")==before
    with pytest.raises(MediaError,match="changed"): handoff.apply(project,result["proposal"])


def test_handoff_retarget_rejected(project):
    result=handoff.prepare(project,"shot-0001")
    folder=Path(result["path"]);meta=json.loads((folder/"proposal.json").read_text())
    meta["shot"]="shot-0002";write_json(folder/"proposal.json",meta)
    with pytest.raises(MediaError,match="metadata"): handoff.apply(project,result["proposal"])


def test_canonical_commit_survives_incomplete_mirror_writes(project):
    before=files_for(project,"shot-0001")
    update(project,"shot-0001",revision(project),before)
    (project/"units"/"shot-0001"/"visual.html").write_text("partial interrupted file")
    assert files_for(project,"shot-0001")==before
