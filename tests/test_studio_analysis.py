"""Local video analysis tests, optional OpenCV. No models or network."""
import json
from pathlib import Path
import pytest
cv2=pytest.importorskip('cv2')
np=pytest.importorskip('numpy')
from skye.studio.reference import analyze
from skye.studio.tracking import track_text


def make_video(root,frames):
    (root/'assets').mkdir(parents=True)
    out=cv2.VideoWriter(str(root/'assets/source.mp4'),cv2.VideoWriter_fourcc(*'mp4v'),30,(320,180))
    if not out.isOpened():pytest.skip('mp4v unavailable')
    for frame in frames:out.write(frame)
    out.release()
    dur=len(frames)/30
    (root/'film.html').write_text('<!doctype html><html><head><meta name="viewport" content="width=320,height=180"><style></style></head><body><section><video id="v1" src="assets/source.mp4#t=0,'+str(dur)+'" at="0" style="object-fit:cover"></video></section></body></html>')


def test_cuts_do_not_count_flash(tmp_path):
    frames=[]
    for i in range(90):
        color=(150,40,20) if i<60 else (20,130,25)
        if 20<=i<=22:color=(250,250,250)
        frames.append(np.full((180,320,3),color,np.uint8))
    make_video(tmp_path,frames)
    r=analyze(tmp_path/'assets/source.mp4',ocr=False)
    assert r['cuts']==[2.0]
    assert r['flash_candidates']==[.667]
    assert r['shots'][0]['recipe'][0]['effect']=='flash'


def test_tracking_measures_known_pan(tmp_path):
    rng=np.random.default_rng(8)
    base=rng.integers(0,255,(240,400,3),dtype=np.uint8)
    frames=[base[i//3:i//3+180,i//2:i//2+320].copy() for i in range(30)]
    make_video(tmp_path,frames)
    result=track_text(tmp_path,1,'PLANTED',fps=10,roi=[.1,.3,.9,.95])
    r=json.loads((tmp_path/result['tracking']).read_text())
    first=np.array(r['samples'][0]['quad']);last=np.array(r['samples'][-1]['quad'])
    dx=(last[:,0]-first[:,0])*320;dy=(last[:,1]-first[:,1])*180
    assert abs(float(np.median(dx))+13)<1.5
    assert abs(float(np.median(dy))+9)<1.5
    assert result['min_inliers']>=10
    assert 'matrix3d' in (tmp_path/result['page']).read_text()


def test_untextured_plane_fails_without_edit(tmp_path):
    make_video(tmp_path,[np.full((180,320,3),100,np.uint8)]*30)
    before=(tmp_path/'film.html').read_bytes()
    with pytest.raises(Exception,match='features'):track_text(tmp_path,1,'NO')
    assert (tmp_path/'film.html').read_bytes()==before


def test_comparison_does_not_invent_visual_match():
    from skye.studio.reference import compare_analyses
    ref={'source':'reference.mp4','duration':4,'median_shot_seconds':1,'cut_resolution_seconds':1/6,'cuts':[1,2,3],'flash_candidates':[.5],'caption_candidates':[]}
    out={**ref,'source':'output.mp4','duration':5,'cuts':[1.1,1.2,3.05,4],'flash_candidates':[]}
    r=compare_analyses(ref,out,tolerance=.15)
    assert r['duration']['delta']==1
    assert len(r['cuts']['pairs'])==2
    assert r['cuts']['reference_unpaired']==[2]
    assert r['cuts']['output_unpaired']==[1.2,4]
    assert r['flash_candidates']['reference_unpaired']==[.5]
    assert r['caption_geometry']['vertical_center']=={'reference':None,'output':None}
    assert any('No overall style-match score' in s for s in r['limits'])
    with pytest.raises(ValueError):compare_analyses(ref,out,tolerance=float('nan'))
