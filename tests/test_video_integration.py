"""Real optional media stack, no model calls. SKYE_TEST_MEDIA=1 pytest tests/test_video*"""
import json
import os
from pathlib import Path
import pytest
from skye.media.importer import import_video
from skye.media.model import MediaError, files_for, revision, update, file_hash
from skye.media.renderer import render
from skye.media.process import ffmpeg, probe, run, binary
from skye.media.structure import split, merge
from skye.media.history import step, summary

pytestmark = pytest.mark.skipif(os.environ.get("SKYE_TEST_MEDIA") != "1", reason="optional FFmpeg / Chromium / scene-detection stack")


@pytest.fixture(scope="module")
def imported(tmp_path_factory):
    state=tmp_path_factory.mktemp("media")
    source=state / "footage नमस्ते.webm"
    ffmpeg("-f","lavfi","-i","color=c=0x193047:s=480x270:r=30:d=1", "-f","lavfi","-i","color=c=0x347252:s=480x270:r=30:d=1",
           "-f","lavfi","-i","sine=frequency=440:duration=2", "-filter_complex","[0:v][1:v]concat=n=2:v=1:a=0[v]",
           "-map","[v]","-map","2:a","-c:v","libvpx-vp9","-deadline","realtime","-cpu-used","6","-c:a","libopus",str(source))
    p=import_video(state,source,scene_detection=True)
    return state / "media" / p["id"]


def test_real_import_detection_and_incremental_render(imported):
    p=json.loads((imported/"project.json").read_text())
    assert p["frames"]==60
    assert len(p["units"])==2
    original_hash=file_hash(imported/"original.bin")
    initial=render(imported)
    assert initial["rendered"]==["shot-0001","shot-0002"]
    assert render(imported)["reused"]==["shot-0001","shot-0002"]
    f=files_for(imported,"shot-0002")
    f["visual.html"]=f["visual.html"].replace("title.textContent = '';", "title.textContent = 'A local change';")
    f["captions.json"]='[{"start":0,"end":30,"text":"Only shot two."}]'
    update(imported,"shot-0002",revision(imported),f)
    result=render(imported)
    assert result["rendered"]==["shot-0002"] and result["reused"]==["shot-0001"]
    assert result["audio_rendered"]==[]
    output=imported/"exports"/result["file"]
    assert probe(output)["audio"]
    counts=json.loads(run([binary("ffprobe"),"-v","error","-select_streams","v:0","-count_frames","-show_entries","stream=nb_read_frames","-of","json",str(output)]))
    assert int(counts["streams"][0]["nb_read_frames"])==60
    f["audio.json"]='{"gain":0.5,"mute":false,"fade_frames":2}'
    update(imported,"shot-0002",revision(imported),f)
    audio=render(imported)
    assert audio["rendered"]==[] and audio["audio_rendered"]==["shot-0002"]
    assert file_hash(imported/"original.bin")==original_hash
    assert not list((imported/"cache").glob("*.partial*"))


def test_failed_render_keeps_previous_export(imported):
    latest=json.loads((imported/"latest.json").read_text())
    before=files_for(imported,"shot-0001");bad=dict(before)
    bad["visual.html"]='<script>window.frame = () => {throw new Error("fixture failure")}</script>'
    update(imported,"shot-0001",revision(imported),bad)
    with pytest.raises(MediaError): render(imported)
    assert json.loads((imported/"latest.json").read_text())==latest
    assert (imported/"exports"/latest["file"]).exists()
    assert not list((imported/"cache").glob("*.partial*"))
    update(imported,"shot-0001",revision(imported),before)


def test_portrait_silent_and_manual_cuts(tmp_path):
    source=tmp_path/"portrait.webm"
    ffmpeg("-f","lavfi","-i","color=c=navy:s=180x320:r=30:d=1","-an","-c:v","libvpx-vp9","-deadline","realtime",str(source))
    p=import_video(tmp_path,source,scene_detection=False)
    root=tmp_path/"media"/p["id"]
    assert not p["has_audio"] and p["width"]==180 and p["height"]==320
    split(root,revision(root),"shot-0001",15)
    result=render(root)
    assert probe(root/"exports"/result["file"])["audio"]  # explicit silent track
    f=files_for(root,"shot-0001");f["visual.html"]+='<!-- edited -->'
    update(root,"shot-0001",revision(root),f)
    merge(root,revision(root),"shot-0001")  # editing no longer blocks recutting
    assert len(json.loads((root/"project.json").read_text())["units"])==1


def test_browser_worker_approval_and_trusted_render(tmp_path):
    from skye.media.handoff import prepare
    from skye.media.worker import run_edit
    from skye.adapters.base import Result
    source=tmp_path/"source.webm"
    ffmpeg("-f","lavfi","-i","color=c=navy:s=180x320:r=30:d=0.5","-an","-c:v","libvpx-vp9","-deadline","realtime",str(source))
    p=import_video(tmp_path,source,scene_detection=False);root=tmp_path/"media"/p["id"]
    proposal=prepare(root,"shot-0001","Add a title")
    def fake(spec):
        assert spec["role"]=="artist" and "Write" in spec["tools"] and "Bash" not in spec["tools"]
        file=Path(spec["repo"])/"visual.html"
        file.write_text(file.read_text().replace("title.textContent = '';", "title.textContent = 'Hello';"))
        return Result(ok=True,output="Edited",returncode=0)
    result=run_edit(tmp_path,root,proposal["proposal"],lambda data:None,runner=fake)
    assert result["rendered"]==["shot-0001"]
    assert "Hello" in files_for(root,"shot-0001")["visual.html"]


def test_ask_is_one_step_previews_only_that_shot_and_can_be_undone(tmp_path):
    from skye.media.worker import ask
    from skye.adapters.base import Result
    source=tmp_path/"source.webm"
    ffmpeg("-f","lavfi","-i","color=c=navy:s=180x320:r=30:d=2","-an","-c:v","libvpx-vp9","-deadline","realtime",str(source))
    p=import_video(tmp_path,source,scene_detection=False);root=tmp_path/"media"/p["id"]
    split(root,revision(root),"shot-0001",30)
    seen={}
    def fake(spec):
        seen["brief"]=spec["goal"]; seen["tools"]=spec["tools"]
        folder=Path(spec["repo"])
        assert (folder/"reference.jpg").exists()
        f=folder/"captions.json"; f.write_text('[{"start":0,"end":20,"text":"Hello"}]')
        return Result(ok=True,output="Added a caption",returncode=0)
    stages=[]
    result=ask(tmp_path,root,"shot-0002","Add a hello caption","claude-code",lambda d:stages.append(d.get("stage")),runner=fake)
    assert result["shot"]=="shot-0002" and result["rendered"]==["shot-0002"] and result["reused"]==[]
    assert result["file"].endswith("-shot-0002-preview.webm") and (root/"exports"/result["file"]).exists()
    assert not (root/"latest.json").exists()  # a one-shot preview is not the full render
    assert json.loads(files_for(root,"shot-0002")["captions.json"])[0]["text"]=="Hello"
    assert files_for(root,"shot-0001")["captions.json"].strip()=="[]"
    assert "Bash" not in seen["tools"] and "Write" in seen["tools"]
    assert summary(root)["undo"]=="Agent: Add a hello caption"
    assert json.loads((root/"agent-log.json").read_text())[-1]["status"]=="done"
    step(root,revision(root),-1)
    assert files_for(root,"shot-0002")["captions.json"].strip()=="[]"
    def sloppy(spec):
        (Path(spec["repo"])/"extra.txt").write_text("x"); return Result(ok=True,output="ok",returncode=0)
    with pytest.raises(MediaError,match="outside"):
        ask(tmp_path,root,"shot-0002","Add something","codex",lambda d:None,runner=sloppy)
    assert json.loads((root/"agent-log.json").read_text())[-1]["status"]=="failed"
    assert files_for(root,"shot-0002")["captions.json"].strip()=="[]"
