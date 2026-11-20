# Local voice

Video > Compose > Voice uses **Kitten TTS 2 Mini int4**. It is optional. The normal Skye install does not import or download it.

## Use it

1. Install the voice extra in the environment that runs Skye: `python -m pip install '.[voice]'`.
2. Start Skye from that environment. Open Video, make a project and import footage if wanted.
3. In Compose, write one scene per line. Separate spoken phrase groups with `|`. These words are the voice script too. Choose the language and a stock voice under **Voice**, then **Generate**.
4. First use downloads model/front-end files. Later runs reuse the cache. Preview the WAV. Generation sets Timing to **Generated voice** and selects the new asset.
5. **Build** creates phrase-timed native text clips and adds the generated voice as a separate native audio clip at the Compose start. Words edits the picture text only, it does not regenerate speech. If the script changes, regenerate first; mismatched groups stop Build rather than drifting away from the sound.
6. Edit/Undo/export as usual. Voice generation itself creates a reusable asset and sidecar, not a timeline mutation; timeline Undo does not delete that asset.

UI labels stay short. The full caveats live here.

## What "48 MB" means

The selected graph is `kitten_tts_2_mini_int4.onnx`: **47,468,831 bytes** in the verified revision, roughly 47.5 decimal MB. It is not the 82.7 MB int8 graph or the larger flagship model. ONNX Runtime uses its CPU provider. Skye asserts `weights == "int4"` and refuses an SDK that ignores the request. No silent int8, GPU, hosted API or paid fallback.

The voice packs, English G2P files, spaCy language data and Python packages add to download size, disk use and RAM. This is not a 48 MB total installation. The published kittenml package declares dependencies used by its other model families too, including PyTorch. The Mini acoustic graph is ONNX, but its English fallback front end imports PyTorch and transformers. Do not describe this verified installation as torch-free. On Linux, installing a CPU PyTorch build before the extra can avoid pulling a GPU build unnecessarily. A clean minimal dependency installer is not part of this milestone.

The PyPI `kittenml==0.9.8` source inspected during this integration accepts a top-level weights argument but does not pass it to the Mini backend. The extra therefore pins an upstream revision that implements int4 selection:

- SDK: `5802f6b956844919ecf210f4e55f4750a6ad7c6f`
- Model: `KittenML/kitten-tts-2-mini`, revision `8e89ae2a718ee4bb893c67d67657e520e6112faf`

Only the int4 graph, selected voice pack, vocabulary/config and, for English, G2P files are requested from that model revision. Its English front end can also download spaCy's small English language data on first use. Model download is on explicit Generate, not opening the picker. Once all assets are cached, inference is local. Offline first-run setup is not automatic; set `SKYE_KITTEN_MODEL` to a fully populated local Mini int4 model directory if pre-provisioning. A local override is labeled separately in provenance. Network-free cached inference has not been independently tested on a disconnected machine.

## Timing, honestly

The synthesis API returns waveform audio, not a word-alignment result. Skye generates each `|` / newline group separately and joins groups with 150 ms of silence. It retains the script, language, voice, engine, graph/SDK revisions, sample rate, elapsed time and the exact sample-derived segment bounds in `assets/voice-….voice.json` beside the WAV.

Those are **generated segment bounds**, not exact word/syllable times or phonetic onsets. A segment can contain model-leading silence. Separate synthesis can change pacing and expression compared with reading a sentence in one pass. It can sound more chopped. It is useful for editorial phrase timing, not forced alignment or lip sync.

Generated voice feeds the existing Compose speech-timing path using its segment report. Every scene phrase group must match the ordered generated groups, including all of them. Word spacing and Hindi vowel marks are preserved by the match. Compose maps each group to its audio segment and adds the voice track at the same timeline start, including an append. A group shorter than the editor's minimum scene length should share a scene with another group.

If word alignment is needed, select the generated WAV in **Speech**, run **Analyze** with the existing local Vosk setup and review the word/In/Out fields. Recognition can be wrong, and the configured model must match the language. English Vosk is not Hindi alignment. Merely knowing the script does not make word times known. No word-timing guarantee is made here.

## Engine / CLI

Only stock English and Hindi voices are exposed in this first Skye control, despite the upstream model having more languages. No cloning or reference-speaker upload. The source/model list is fixed, not an arbitrary model loader from an incoming request.

```sh
python -m skye.media.tts \
  --engine kitten-mini-int4 \
  --text-file script.txt \
  --language en --voice Danny \
  --output /path/to/project/assets/narration.wav
```

For Hindi, use `--language hi --voice Arjun` (also Priya, Ananya, Vikram). The engine flag deliberately selects int4. A speed parameter is bounded to 0.7–1.3. The UI uses normal speed. Inputs are limited to 1,600 characters, 60 groups, 240 characters per group and 180 seconds of output. A worker runs separately with a four-minute limit. Invalid, non-finite or empty audio fails without leaving an output. Overwriting an existing output is refused. Unknown/unpronounceable front-end text fails rather than being silently skipped.

Use the resulting file with a recipe's `timing: {"mode": "voice", "source": "assets/narration.wav"}`. Keep the `.voice.json` beside it. Skye validates the sidecar's bounds against the decoded WAV. Voice generation does not need an open editor for the CLI, but offline Compose apply still requires the editor to be closed.

Live controls use Skye's authenticated local surface: `voice-list` reads the fixed choices without loading the engine; `voice-generate` starts a local media job and creates a WAV/sidecar, not a timeline edit. The UI previews it and passes its source to Compose. Native operations and revision checks remain responsible for the final Build/Undo.

## Verified and unproven

Test host: Linux x86-64 CPU, ONNX Runtime CPU provider, two inference threads. Pinned-wrapper samples:

- Danny, English, "Your shots. Your story. Make your next cut.": 3.000 s audio in 6.09 s including worker startup, warm file cache.
- Arjun, Hindi, "अपनी कहानी को एक नई आवाज़ दो।": 2.725 s audio in 3.45 s including worker startup, warm file cache.

Both are 24 kHz mono PCM WAV with finite samples. Earlier equivalent samples peaked at -3.2 and -1.3 dBFS. These are sent for the listener to judge, not a listening-reviewed quality ranking. No claim of superiority to Verba, no M1/Mac speed or RAM claim, and no broad Hindi/English pronunciation benchmark. First downloads and the cold English front end take additional time.

The Video UI generated four English phrase groups, previewed the actual WAV, built two scene anchors with four separate phrase layers and one native voice track. The native export is 5.875 s, 854×480, 30 fps H.264/AAC; it is a functionality proof, not a new art-direction milestone.

## License / sources

The publisher lists Apache-2.0 for this Mini model, voice packs and bundled G2P, separately from its differently licensed flagship. The SDK is Apache-2.0. No model weights, SDK source or voice packs are committed to Skye. Other installed dependencies retain their licenses.

- https://huggingface.co/KittenML/kitten-tts-2-mini
- https://github.com/KittenML/KittenTTS/blob/main/docs/kitten-tts-2-tiny.md
- https://pypi.org/project/kittenml/

The native video proof uses the same Pexels source material as the earlier motion demos, with no reference footage or implied endorsement. The standalone voice samples contain only original test lines.
