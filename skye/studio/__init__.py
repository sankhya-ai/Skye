"""Isolated, lazy local editor. No model calls on launch; no provider credentials."""
from __future__ import annotations

import atexit
import json
import os
import select
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path

from ..media.model import MediaError
from ..media.process import binary

_LOCK = threading.RLock()
_RUNNING: dict[str, 'Studio'] = {}
VENDOR = Path(__file__).parent / 'vendor' / 'openfilm'


class Studio:
    def __init__(self, state: Path):
        self.state = state.resolve()
        build = VENDOR / 'studio/ui/dist/index.html'
        if not build.exists() or not (VENDOR / 'node_modules/playwright-core').is_dir():
            raise MediaError('Install the editor once: python -m skye.studio.setup')
        home = self.state / 'studio'
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.log = (home / 'server.log').open('ab')
        self.process = subprocess.Popen(
            [binary('node'), str(Path(__file__).with_name('runtime.mjs')), str(home)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
            start_new_session=True)
        try:
            ready, _, _ = select.select([self.process.stdout], [], [], 30)
            if not ready:
                raise MediaError('Editor did not start in 30 seconds. Check studio/server.log')
            line = self.process.stdout.readline()
            if not line:
                raise MediaError('Editor stopped at launch. Check studio/server.log')
            self.session = json.loads(line)
            self.origin = self.session['origin']
            if not self.origin.startswith('http://127.0.0.1:'):
                raise MediaError('Editor returned a non-local address')
            # Drain stdout so diagnostics cannot block the editor later.
            threading.Thread(target=self._drain, daemon=True).start()
        except BaseException:
            self.close()
            raise

    def _drain(self):
        for line in self.process.stdout:
            self.log.write(line)
            self.log.flush()

    def request(self, route: str, body=None, method=None):
        if not route.startswith('/api/'):
            raise MediaError('Invalid editor route')
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.origin + route, data=data,
            headers={'x-studio-key': self.session['key'], 'Content-Type': 'application/json'},
            method=method or ('GET' if data is None else 'POST'))
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                return json.load(res)
        except urllib.error.HTTPError as exc:
            try:
                error = json.load(exc).get('error', str(exc))
            except (ValueError, AttributeError):
                error = str(exc)
            raise MediaError(str(error)) from exc
        except (OSError, ValueError) as exc:
            raise MediaError(f'Editor unavailable: {exc}') from exc

    def close(self):
        process = getattr(self, 'process', None)
        if process and process.poll() is None:
            if process.stdin:
                process.stdin.close()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                import signal
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
        if getattr(self, 'log', None):
            self.log.close()


def start(state: Path) -> Studio:
    with _LOCK:
        key = str(state.resolve())
        studio = _RUNNING.get(key)
        if studio and studio.process.poll() is None:
            return studio
        if studio:
            studio.close()
        studio = Studio(state)
        _RUNNING[key] = studio
        return studio


def stop_all():
    with _LOCK:
        for studio in _RUNNING.values():
            studio.close()
        _RUNNING.clear()


atexit.register(stop_all)
