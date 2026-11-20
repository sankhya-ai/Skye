"""Native matte declaration, source-time mapping and straight-path protection."""
import json
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
VENDOR=ROOT/'skye/studio/vendor/openfilm/src'

def node(code):
    r=subprocess.run(['node','--input-type=module','-e',code],capture_output=True,text=True,check=True)
    return json.loads(r.stdout)

def test_matte_native_roundtrip():
    result=node(f'''import {{readFilmFile,filmHtml}} from {json.dumps((VENDOR/'film-doc.mjs').as_uri())};
const text='<html><head><style></style></head><body><section><video src="a.mp4#t=2,4" matte="assets/masks/manifest.json"></video></section></body></html>';
const r=readFilmFile(text); if(!r.doc) throw Error(r.problems.join(' '));
const again=readFilmFile(filmHtml(r.doc));
console.log(JSON.stringify(again.doc.tracks[0].clips[0]));''')
    assert result['matte']=='assets/masks/manifest.json'

def test_matte_unsafe_paths_rejected():
    result=node(f'''import {{readFilm}} from {json.dumps((VENDOR/'film-doc.mjs').as_uri())};
console.log(JSON.stringify(['https://bad/m.json','../x.json','/x.json'].map(matte=>readFilm({{stage:{{w:320,h:480}},tracks:[{{clips:[{{id:'a',src:'a.mp4',matte}}]}}]}}).doc===null)));''')
    assert all(result)

def test_source_time_frame_selection_and_missing_range():
    result=node(f'''import {{matteFrame}} from {json.dumps((VENDOR/'track-matte.mjs').as_uri())};
const m={{fps:30,start:2,frames:['a.png','b.png','c.png']}};
let throws=false;try{{matteFrame(m,4)}}catch{{throws=true}}
console.log(JSON.stringify([matteFrame(m,2),matteFrame(m,2+1/30),throws]));''')
    assert result==['a.png','b.png',True]

def test_matte_disables_unmasked_straight_export():
    assert 'clip.matte || clipFade(clip)' in (VENDOR/'direct.mjs').read_text()

def test_matte_module_is_served_to_browser():
    assert "'track-matte.mjs'" in (VENDOR/'vision-assets.mjs').read_text()
    assert 'await applyTrackMatte(c,' in (VENDOR/'timeline.mjs').read_text()


def test_packaged_matte_closure_is_complete_and_missing_frames_fail(tmp_path):
    (tmp_path/'assets/masks').mkdir(parents=True)
    (tmp_path/'assets/clip.mp4').write_bytes(b'video')
    (tmp_path/'assets/masks/a.png').write_bytes(b'alpha')
    (tmp_path/'assets/masks/b.png').write_bytes(b'alpha')
    (tmp_path/'assets/masks/manifest.json').write_text(json.dumps({'fps':2,'start':0,'frames':['a.png','b.png']}))
    (tmp_path/'film.html').write_text('<html><head><meta name="viewport" content="width=360,height=640"></head><body><section><video id="v" src="assets/clip.mp4#t=0,1" matte="assets/masks/manifest.json"></video></section></body></html>')
    module=(VENDOR.parent/'studio/server/closure.mjs').as_uri()
    code=f"import {{filmClosure,folderView}} from {json.dumps(module)};const files=await filmClosure(folderView({json.dumps(str(tmp_path))}));console.log(JSON.stringify([...files.keys()]));"
    files=node(code)
    (tmp_path/'.film').mkdir()
    (tmp_path/'.film/skye-storyboard.json').write_text('{}')
    exported=node(f"import {{projectFiles}} from {json.dumps((VENDOR.parent/'studio/server/exports.mjs').as_uri())};console.log(JSON.stringify((await projectFiles({json.dumps(str(tmp_path))},[])).map(f=>f.name)));")
    assert '.film/skye-storyboard.json' in exported
    assert {'assets/masks/manifest.json','assets/masks/a.png','assets/masks/b.png'}<=set(files)
    (tmp_path/'assets/masks/b.png').unlink()
    code=f"import {{filmClosure,folderView}} from {json.dumps(module)};let error='';try{{await filmClosure(folderView({json.dumps(str(tmp_path))}))}}catch(e){{error=e.message}}console.log(JSON.stringify(error));"
    assert 'Matte frame is missing' in node(code)
