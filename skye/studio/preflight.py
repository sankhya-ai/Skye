"""Read-only export facts. Deliberately no speculative time or output-size estimate."""
from pathlib import Path
from urllib.parse import unquote, urlsplit
import shutil


def inspect(studio, project):
    snap = studio.request(f'/api/projects/{project}')
    film = snap['value']
    root = Path(snap['project']['path']).resolve()
    clips = [c for t in film['tracks'] for c in t['clips']]
    duration = max((c.get('at', 0) + (c.get('time', [0, c.get('len', 0)])[1] - c.get('time', [0, 0])[0]) / c.get('speed', 1) for c in clips), default=0)
    files = {}
    for clip in clips:
        source = clip.get('src', '')
        url = urlsplit(source)
        if url.scheme or url.netloc:
            continue
        path = (root / unquote(url.path).lstrip('/')).resolve()
        if path.is_relative_to(root) and path.is_file():
            files[str(path)] = path.stat().st_size
    cache = root / '.film/cache'
    # Cache workers can remove transient files during the check.
    cache_bytes = 0
    for path in cache.rglob('*') if cache.exists() else []:
        try:
            if path.is_file():
                cache_bytes += path.stat().st_size
        except FileNotFoundError:
            pass
    return {
        'project': project, 'name': snap['project'].get('name', project),
        'stage': film.get('stage', {'w': 1920, 'h': 1080}), 'duration': round(duration, 3),
        'mediaBytes': sum(files.values()), 'mediaFiles': len(files),
        'cacheBytes': cache_bytes, 'freeBytes': shutil.disk_usage(root).free,
        'warning': 'Direct source files only, not nested page dependencies. Export frame changes do not reframe subjects. Preview the project crop first. Exports may prepare full source videos, not just selected ranges. Extra disk and time may be needed. No reliable time estimate is available before profiling this project.'
    }
