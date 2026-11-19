"""Studio URL import: validation and confined download. Loopback server only."""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

from skye.media.model import MediaError
from skye.studio.importer import fetch_into


@pytest.fixture()
def server(tmp_path):
    (tmp_path / 'clip.mp4').write_bytes(b'\x00' * 2048)
    (tmp_path / 'big.png').write_bytes(b'x')
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    httpd = http.server.HTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{httpd.server_port}'
    httpd.shutdown()


def test_downloads_into_assets(server, tmp_path):
    project = tmp_path / 'proj'
    project.mkdir()
    out = fetch_into(project, f'{server}/clip.mp4')
    assert out['path'] == 'assets/clip.mp4'
    assert out['bytes'] == 2048
    assert (project / 'assets' / 'clip.mp4').read_bytes() == b'\x00' * 2048


def test_dedupes_names(server, tmp_path):
    project = tmp_path / 'proj'
    (project / 'assets').mkdir(parents=True)
    (project / 'assets' / 'clip.mp4').write_bytes(b'old')
    out = fetch_into(project, f'{server}/clip.mp4')
    assert out['path'] == 'assets/clip-2.mp4'
    assert (project / 'assets' / 'clip.mp4').read_bytes() == b'old'


def test_rejects_non_media(server, tmp_path):
    (tmp_path / 'proj').mkdir()
    with pytest.raises(MediaError, match='not importable media'):
        fetch_into(tmp_path / 'proj', f'{server}/page.html')
    with pytest.raises(MediaError, match='not importable media'):
        fetch_into(tmp_path / 'proj', f'{server}/evil.html', rel='scene.html')


def test_rejects_bad_urls(tmp_path):
    with pytest.raises(MediaError, match='http'):
        fetch_into(tmp_path, 'ftp://example.com/clip.mp4')
    with pytest.raises(MediaError, match='http'):
        fetch_into(tmp_path, 'file:///etc/passwd')
    with pytest.raises(MediaError, match='Bad URL|http'):
        fetch_into(tmp_path, 'not a url')


def test_sanitizes_names(server, tmp_path):
    project = tmp_path / 'proj'
    project.mkdir()
    out = fetch_into(project, f'{server}/clip.mp4', rel='../../My Clip (final).mp4')
    assert out['path'] == 'assets/My-Clip-(final).mp4'
    assert (project / 'assets' / 'My-Clip-(final).mp4').exists()
