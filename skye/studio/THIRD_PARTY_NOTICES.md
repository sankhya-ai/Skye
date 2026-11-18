# Third-party notices: Skye Studio

Skye Studio contains a modified snapshot of OpenFilm, MIT License, copyright (c) 2026 OpenFilm contributors. The complete original license is retained at `vendor/openfilm/LICENSE`.

- Repository: https://github.com/openfilm/openfilm
- Commit: `715c3a6e46b11a0b3ee038e8f7b3e3b4e19c5c36`
- Source directory: `packages/openfilm`
- Local changes and file hashes: `UPSTREAM.json`, `SOURCE_MANIFEST.json`

The retained OpenFilm library pages/assets are dedicated under CC0 1.0, except the Fluent Emoji assets, MIT License, copyright Microsoft Corporation, and the bundled Smiley Sans and ZCOOL KuaiLe fonts, SIL Open Font License 1.1. The fonts retain their OFL notices under `library/text/text/files/fonts/`. The complete library notice and Microsoft license are retained at `vendor/openfilm/library/LICENSE.md`. This snapshot omits the optional model weights and remote audio catalog assets. Library templates, effects, filters, transitions, text and other retained local content keep their original notices.

Dependencies installed by npm retain their own licenses. No node_modules or browser/media binaries are committed or bundled in the wheel. The build output includes dependency code and fonts; their license texts are collected in `DEPENDENCY_LICENSES.txt`. This is not a claim that every installed FFmpeg is non-GPL. The local Linux test FFmpeg is not redistributed. See `docs/video/LICENSES.md` for that boundary.

No mcut, GPL/AGPL, Twick, Remotion or Storyboarder source was copied for this adoption. Skye's own license remains Apache-2.0.
