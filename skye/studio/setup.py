"""One-time local editor dependency/build setup. No provider keys or paid services."""
from __future__ import annotations
import subprocess
from . import VENDOR
from ..media.process import binary


def main():
    subprocess.run([binary('npm'), 'ci', '--ignore-scripts', '--no-audit', '--no-fund'], cwd=VENDOR, check=True)
    subprocess.run([binary('npm'), 'run', 'build:ui'], cwd=VENDOR, check=True)
    subprocess.run([binary('node'), 'node_modules/playwright-core/cli.js', 'install', 'chromium'], cwd=VENDOR, check=True)
    print('Skye Studio ready. No provider keys are needed for local editing or rendering.')


if __name__ == '__main__':
    main()
