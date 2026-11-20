# Motion direction

Compose uses this guide by default. Humans and agents see the same rules in **Design**, the API and `skye studio compose PROJECT --lint`. It is a starting director, not an aesthetic judge.

## Read the sentence first

Write the scene's idea before choosing its effect. Use `|` to separate phrase entrances. One group carries the point; the rest supports it. Keep linking words quieter. Compose's current focal choice is a rule (first phrase for the opening/ending, last phrase otherwise), not semantic understanding. Change the grouping or edit the native layers when the meaning needs a different stress.

## A small visual vocabulary

- Keep at least 80% of phrase layers upright. The few emphasis angles alternate +6° / -6°. Do not tilt the support words too.
- One focal text box is roughly twice the support height. Long words still auto-fit, so inspect actual glyph size, not only box dimensions.
- Keep the rotated text rectangle within an 8% title-safe inset. The pass accounts for the real stage aspect ratio. During entrances/exits, motion may briefly cross that inset by design.
- Alternate hero, quiet, impact and contrast scenes. End with upright, quiet type. A physical picture flip can carry the motion while the words stay still.
- Use one main palette and a contrasting ending. Guided footage scenes use mint/cream over darker picture treatment; the ending uses paper/red. One accent phrase per scene.
- Use block transitions on impact beats, not every cut. In the seven-scene default, six block transitions become two.
- Do not repeat the same layout three times in a row. Vary hierarchy and negative space rather than filling every gap.

## What the design check actually checks

It returns machine-readable warning codes and clip IDs for tilt budget/direction, hierarchy of box heights, rotated title-safe bounds, concurrent text-box collisions, strongly one-sided overall text placement, palette count, repeated layouts and too many block transitions. Scene roles/layouts are returned as short choices.

It does not see faces, infer the subject's gaze, measure perceived contrast over changing footage, understand semantic emphasis, score beauty, or guarantee that an intentional overlap is bad. The UI says "Checks passed", never "beautiful". Review the real export. Use the Words panel or native timeline to revise. `design: "free"` in a recipe preserves the older per-layout direction and reports warnings rather than applying the guide. Guided mode intentionally chooses palette, emphasis, text boxes and transition frequency; it is not a fidelity-preserving pass over arbitrary authored art.

## Timing

### Manual

Set BPM and scene beats, or an explicit duration. No audio is inspected.

### Beats

Choose imported audio/video, select **Beats**, then **Analyze**. A local ffmpeg/stdlib energy-onset analysis estimates pulse tempo and phase. Compose snaps scene ends to that estimated grid, keeping phrase staggering within scenes. The score is a pulse-fit score, not a probability. The first picture still starts at zero. Rejecting silence, sparse onsets and an uncertain pulse is intentional. Tap/manual BPM remains the fallback. Half/double tempo, irregular music, syncopation, speech and dense mixes can fool this simple detector. It does not identify bar one, genres or a musical downbeat. It has been verified on synthetic pulse fixtures and the original proof bed, not benchmarked as a general music tracker.

### Speech

Install the optional local recognizer once:

```sh
python -m pip install '.[speech]'
# Unpack a model from https://alphacephei.com/vosk/models
export SKYE_VOSK_MODEL=/absolute/path/to/unpacked-language-model
skye ui
```

The small US English model used in development is `vosk-model-small-en-us-0.15`, listed by its publisher as Apache-2.0, about 40 MB. Vosk itself is Apache-2.0. Neither model nor recognizer is bundled in the Skye source ZIP. Model setup depends on the target computer; no download, subscription or cloud fallback happens inside Compose. Choose a model for the actual spoken language. Model families have different licenses, check the selected model's row.

Choose **Speech**, pick the narration, **Analyze**. Review the word, In and Out fields. Correct wrong words and times, then check **Reviewed words and times**. The transcript below follows the edited words. The phrase groups must match the ordered words. Missing and low-confidence matches stop composition rather than inventing timestamps. Reviewed/imported timings are labeled as such, not presented as unmodified recognition. ASR confidence is not reliable proof: the clean synthetic test misheard "never" with high confidence.

Scene cuts fall just before the next scene's first phrase; each phrase entrance starts at its matched word time. This is exact token matching against word timestamps, not acoustic forced alignment, syllable timing, emotion detection or stress detection. Timestamps remain estimates. Do not copy a transcript until you have reviewed it. The last spoken scene gets a short tail where the source allows it. Add the narration as a native sound bed in Audio mixer; analysis does not silently add or stretch audio. When appending, audio source time zero maps to the Compose start, not film zero. Move the sound clip to that same timeline offset; the whole-film mixer bed otherwise starts at zero. Music timing and speech timing are separate modes in this version, not a joint score optimizer.

Audio analysis is local, at most 180 seconds. Split long material first. Scene limits stay 0.8–12 seconds. Asset paths must be imported files within the project.

## Agent entry points

```sh
skye studio timing PROJECT --source assets/music.wav --mode beat
skye studio timing PROJECT --source assets/narration.wav --mode speech
skye studio compose PROJECT --recipe recipe.json
skye studio compose PROJECT --recipe recipe.json --apply
skye studio compose PROJECT --lint
```

Close the project in the editor before offline apply. The CLI backs up `film.html`; use editor Undo for live changes. Compose returns `design`, `decisions` and `timing` in addition to the native operations. Offline apply returns the same reports.

Recipe extensions:

```json
{
  "design": "guided",
  "timing": {"mode": "beat", "source": "assets/music.wav"},
  "scenes": [
    {"words": ["YOUR SHOTS", "YOUR STORY"], "layout": "split", "source": "assets/shot.mp4", "beats": 4}
  ]
}
```

For reviewed speech, `timing` can instead contain `mode: "speech"`, `source`, `reviewed: true` and a `words` array of `{word, start, end}`. These are user-reviewed/imported timings. Do not set that flag for uninspected machine output. The server checks ordered words and source bounds; authorization to use or publish the narration remains the caller's responsibility.

Live endpoints: `/api/studio/motion-timing` analyzes a selected source; `/api/studio/motion-design` inspects the current film; `/api/studio/compose` prepares revision-checked native operations. All go through Skye's normal authenticated local control surface.

## The proof

Same words, same stock footage, same original music bed, same 14-second duration. The prior default had 10 of 15 phrase layers angled and six block transitions. The guided remake has 2 of 15 phrases angled in opposite directions, two block transitions, an upright focal/support hierarchy and a still ending. That is the directly tested design change, not a claim of Framey parity or an objective beauty improvement.

A separate speech test used locally synthesized original narration. Vosk produced 24 timed words; three words were corrected through the UI, then the reviewed words generated 15 separate phrase clips at the measured starts (including 0.06, 0.54, 1.50, 2.10 and 4.26 seconds). The narration was added as a native audio bed. Synthetic speech is a functionality test, not the art-directed soundtrack of the before/after remake.

Sources: https://alphacephei.com/vosk/models ; https://github.com/alphacep/vosk-api/ ; https://pypi.org/project/vosk/ . Stock source and rights ledger is included with the proof projects. Reference footage remains study-only.

Final checks for this revision: 656 Python tests passed, 14 skipped. TypeScript and the production build passed (existing bundle-size warning). The 14-second native H.264/AAC remake is 854×480 at 30 fps, 1,650,193 bytes; its native export job took 64.4 seconds on the test host. All seven exported composition frames and the before/after comparison were visually inspected. Audio peaks at -1.7 dBFS, measured, not listening-reviewed. The editable ZIP was extracted to a new directory; all seven scene text bounds, repeated clocks and page-error checks passed. This is not a second full ZIP rerender.
