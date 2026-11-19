import pytest
from skye.studio import edit
from test_studio_edit_safety import project

def test_color_parameter_cannot_write_active_markup(project):
    before=(project/'film.html').read_text()
    with pytest.raises(edit.EditError,match='colors'):
        edit.apply_effect(project,1,'caption-pop',text='ok',fg='red;position:fixed')
    assert (project/'film.html').read_text()==before

def test_multiple_tracks_get_distinct_motion_keyframes(project):
    film=project/'film.html'
    film.write_text(film.read_text().replace('</body>','<section><video id="other" src="b.mp4#t=0,2" at="0"></video></section></body>'))
    edit.apply_effect(project,1,'shake',amp=3,track=2)
    edit.apply_effect(project,1,'shake',amp=20,track=3)
    import re
    names=re.findall(r'@keyframes (fx-shake-1-[a-f0-9]+)',film.read_text())
    assert len(names)==2 and len(set(names))==2
