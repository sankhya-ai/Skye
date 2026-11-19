"""Safety checks for the effect command's boundaries and native film validation."""
import json
from pathlib import Path
import pytest
from skye.studio import edit, effects
from test_studio_edit import FILM

@pytest.fixture
def project(tmp_path):
    (tmp_path/'film.html').write_text(FILM)
    (tmp_path/'pages').mkdir()
    return tmp_path

def test_multi_track_requires_explicit_choice(project):
    f=project/'film.html'
    f.write_text(FILM.replace('</body>', '<section><video id="b" src="b.mp4#t=0,2" at="0"></video></section></body>'))
    with pytest.raises(edit.EditError, match='multiple video tracks'):
        edit.read_shots(project)
    assert len(edit.read_shots(project, track=2)) == 3

def test_overlay_copies_do_not_become_the_footage_track(project):
    edit.apply_effect(project, 1, 'glitch')
    assert len(edit.read_shots(project)) == 3
    body=(project/'film.html').read_text()
    assert body.count('volume="0"') == 2

def test_duration_changing_ramp_refused_without_write(project):
    with pytest.raises(edit.EditError, match='preserve shot duration'):
        edit.apply_effect(project, 1, 'ramp', speeds='0.5,2')
    assert (project/'film.html').read_text() == FILM

@pytest.mark.parametrize('args', [dict(dur=-1), dict(at=9), dict(dur=float('nan')), dict(dur=10)])
def test_invalid_timing_does_not_change_film(project,args):
    with pytest.raises(edit.EditError):
        edit.apply_effect(project,1,'flash',**args)
    assert (project/'film.html').read_text() == FILM

def test_recipe_rolls_back_on_incompatible_motion(project):
    with pytest.raises(edit.EditError, match='already animated'):
        edit.apply_recipe(project,1,[{'effect':'caption-pop','text':'a|b'}, {'effect':'shake'}, {'effect':'punch'}])
    assert (project/'film.html').read_text() == FILM
    assert not list((project/'pages').iterdir())

def test_recipe_keeps_overlays_and_one_motion(project):
    result=edit.apply_recipe(project,1,[{'effect':'punch'}, {'effect':'caption-pop','text':'a|b'}])
    assert len(result)==2
    assert 'fx-punch' in (project/'film.html').read_text()
    assert (project/'film.html.bak').read_text()==FILM

def test_native_validator_preserves_film_on_invalid_markup(project):
    with pytest.raises(edit.EditError,match='validation failed'):
        edit._write_film(project, FILM.replace('</video>', '</video></video>',1))
    assert (project/'film.html').read_text()==FILM

def test_scene_defaults_are_transparent(project):
    effects.ktype(project,'title',['ONE'])
    assert 'background:transparent' in (project/'pages/title.html').read_text()

def test_sticker_legacy_keyword_and_svg(project):
    effects.sticker(project,'legacy',emoji='x')
    effects.sticker(project,'star',shape='star')
    assert '<svg' in (project/'pages/star.html').read_text()

def test_duplicate_caption_edits_do_not_overwrite_pages(project):
    edit.apply_effect(project,1,'caption-pop',text='one')
    edit.apply_effect(project,1,'caption-pop',text='two')
    assert len(list((project/'pages').glob('fx-cap-*.html')))==2
