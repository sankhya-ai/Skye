"""Import a media file from a URL into a Studio project's assets.

The editor process itself keeps its loopback-only fetch; pages and the
renderer still cannot reach the network. This route is the single, narrow
exception: the Skye-authenticated Python surface downloads one file the user
asked for, into one project's assets. Media only - never HTML or other code,
because imported pages would run in the rendering browser.

Usage rights are the caller's: import your own material or permissively
licensed stock, not someone else's copyrighted clips.
"""
from __future__ import annotations

import re
import urllib.error
import urllib.request
import urllib.parse
from pathlib import Path

from ..media.model import MediaError

MAX_BYTES = 512 * 1024 * 1024  # same ceiling as legacy media imports
ALLOWED_EXT = {
    'mp4', 'mov', 'webm', 'm4v', 'mkv', 'avi',
    'mp3', 'wav', 'ogg', 'm4a', 'flac',
    'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg',
    'vtt', 'srt', 'cube',
}
_TIMEOUT = 60


def _clean_name(name: str) -> str:
    name = name.strip().replace('\\', '/').rsplit('/', 1)[-1]
    name = re.sub(r'[^\w.() -]', '_', name).strip('. ').replace(' ', '-')
    return name or 'download'


def _check_url(url: str) -> urllib.parse.SplitResult:
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError as exc:
        raise MediaError(f'Bad URL: {exc}') from exc
    if parts.scheme not in ('https', 'http') or not parts.hostname:
        raise MediaError('Only http(s) URLs can be imported')
    return parts


def fetch_into(project_root: Path, url: str, rel: str | None = None) -> dict:
    """Download url into project_root/assets and return its project-relative path."""
    parts = _check_url(url)
    name = _clean_name(rel or urllib.parse.unquote(Path(parts.path).name))
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    if ext not in ALLOWED_EXT:
        raise MediaError(f'".{ext}" is not importable media (no code or pages, only media files)')
    root = project_root.resolve()
    assets = (root / 'assets')
    assets.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={'User-Agent': 'skye-studio-import/1'})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as res:
            length = res.headers.get('Content-Length')
            if length is not None and int(length) > MAX_BYTES:
                raise MediaError(f'File is larger than {MAX_BYTES // (1024 * 1024)} MB')
            target = assets / name
            stem, dot = name.rsplit('.', 1)
            n = 1
            while target.exists():
                n += 1
                target = assets / f'{stem}-{n}.{dot}'
            tmp = target.with_name(f'.{target.name}.importing')
            total = 0
            try:
                with tmp.open('xb') as out:
                    while True:
                        chunk = res.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_BYTES:
                            raise MediaError(f'File is larger than {MAX_BYTES // (1024 * 1024)} MB')
                        out.write(chunk)
                if total == 0:
                    raise MediaError('The URL returned an empty file')
                tmp.rename(target)
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
    except urllib.error.URLError as exc:
        raise MediaError(f'Download failed: {exc.reason}') from exc
    return {'path': f'assets/{target.name}', 'bytes': total, 'from': url}


def import_url(studio, project: str, url: str, rel: str | None = None) -> dict:
    if not isinstance(project, str) or not project:
        raise MediaError('import-url needs a project id')
    if not isinstance(url, str) or not url:
        raise MediaError('import-url needs a url')
    listing = studio.request('/api/projects')
    projects = listing.get('projects', listing) if isinstance(listing, dict) else listing
    match = next((p for p in projects if isinstance(p, dict) and p.get('id') == project), None)
    if not match or not isinstance(match.get('path'), str):
        raise MediaError(f'Unknown Studio project: {project}')
    return fetch_into(Path(match['path']), url, rel)
