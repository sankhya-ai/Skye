"""Prepared mask command graph, without model downloads in unit tests."""
from unittest.mock import patch
from skye.studio import edit
from test_studio_edit_safety import project

def test_native_text_behind_is_a_masked_foreground_over_editable_text(project):
    with patch('skye.studio.matte.prepare_matte',return_value={'matte':'assets/masks/manifest.json','source_start':5.2,'duration':2,'frames':60}):
        change=edit.apply_effect(project,1,'text-behind-native',text='HELLO|WORLD',dur=2)
    film=(project/'film.html').read_text()
    assert 'matte="assets/masks/manifest.json"' in film
    assert 'volume="0"' in film
    assert 'fx-behind-' in film
    assert 'id="v1"' in film
    assert len(edit.read_shots(project))==3
    assert change['frames']==60

def test_lightweight_cutout_uses_existing_model_path(project):
    edit.apply_effect(project,1,'cutout')
    film=(project/'film.html').read_text()
    assert '--of-cutout:1' in film
    assert '--of-cutout-mode:0' in film
