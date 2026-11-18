# Media dependency and codec audit

Checked 18 November 2026. This is an engineering record, not legal advice.

Skye's new media code is original Apache-2.0 code. No third-party editor code, assets, fonts or binaries are copied into the new module. Existing Skye/Berd notices remain unchanged. The page-per-shot, explicit-time rendering convention is informed by OpenFilm; this is an independent, narrower implementation, not a bundled OpenFilm renderer or project-format adapter.

| Component | Integration | License / distribution boundary |
| --- | --- | --- |
| Playwright 1.58.2 | Optional pinned npm runtime; package lock checked in | Apache-2.0. Keep its license/notice if distributing installed dependencies. |
| Chromium | Installed locally through Playwright or selected by the operator | Chromium and its many third-party notices are separate from Playwright's license. Not included in Skye's source or wheel. Audit the actual browser build if shipping one. |
| PySceneDetect | Optional Python extra, 0.6.x | BSD-3-Clause. Keep its notice if redistributing. |
| OpenCV / NumPy | Optional PySceneDetect dependencies | Audit the exact installed wheels and their bundled third-party libraries before creating an installer. The optional dependency does not make those binaries Skye-authored or Apache-only. |
| FFmpeg / ffprobe | External executables on PATH, never bundled or downloaded by Skye | LGPL-2.1-or-later for a qualifying build. Enabling GPL components makes that FFmpeg build GPL. Inspect the real build, not the package name. |
| libvpx VP9 | Requested external FFmpeg encoder | BSD-style license; does not require FFmpeg's GPL switch. |
| libopus | Requested external FFmpeg encoder | BSD-style license; does not require FFmpeg's GPL switch. |
| OpenFilm | Architectural reference only | Repo is MIT. No copied source or assets in this change. |

The render commands do not request libx264, libx265, GPL filters or a nonfree encoder. They use VP9/Opus, native PCM audio slices and ordinary scale/fps/audio filters. This is why this draft exports WebM instead of quietly adding a GPL H.264 encoder.

**Do not claim the installed runtime is non-GPL just because these commands avoid GPL codecs.** The Linux test machine's system FFmpeg was a GPL-enabled distribution build. It is not redistributed by this PR. `skye video doctor` reports the installed build's license family for review. For a shipped LGPL runtime, build without `--enable-gpl` and `--enable-nonfree`, audit enabled external libraries, and fulfill source, build-configuration, attribution and other obligations. Installing a separate executable is not a way to erase its own license obligations. Codec and patent issues need their own review for a commercial distribution.

Source records:

- FFmpeg legal and build-compliance guidance: https://ffmpeg.org/legal.html
- Playwright license: https://github.com/microsoft/playwright/blob/main/LICENSE
- PySceneDetect license: https://github.com/Breakthrough/PySceneDetect/blob/main/LICENSE
- libvpx license: https://github.com/webmproject/libvpx/blob/main/LICENSE
- OpenFilm architecture and repository: https://github.com/openfilm/openfilm

The excluded storyboard application is not a dependency, reference implementation or source of copied code. Its restrictive application license is not used to authorize any part of this module.
