"""Ordinary films saved by the native editor need no hand-inserted IDs/at=0."""
from skye.studio import edit
from test_studio_edit_safety import project
import pytest

def test_implicit_ids_zero_time_and_single_quotes(project):
    film=project/'film.html'
    film.write_text("<html><head><style></style><!--keep head--></head><body><section><!--keep shot--><video src='a.mp4#t=0,2' style='object-fit:cover'></video></section></body></html>")
    shots=edit.read_shots(project)
    assert shots[0].at==0
    edit.apply_effect(project,1,'punch')
    text=film.read_text()
    assert 'fx-punch' in text
    assert 'keep head' in text and 'keep shot' in text
    assert len(edit.read_shots(project))==1

def test_locked_track_refused_without_changing_file(project):
    film=project/'film.html'
    before=film.read_text().replace('<section>', '<section locked>',2)
    film.write_text(before)
    with pytest.raises(edit.EditError,match='locked'):
        edit.apply_effect(project,1,'punch')
    assert film.read_text()==before
