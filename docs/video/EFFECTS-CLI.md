# Per-shot effects: one command, any agent

Every reel effect is addressable per shot through one interface. Claude Code,
Codex, Instinct, or a person in a terminal runs:

```bash
python -m skye.studio.edit <project> --shot N --effect NAME [params]
```

A shot is the Nth video clip of the footage track (the last `<section>` of
`film.html` that holds `<video>` clips), counted from 1. List them:

```bash
python -m skye.studio.edit <project> --shot 1 --effect x --list
```

The same operations are a Python API: `skye.studio.edit.apply_effect(root,
shot, effect, **params)`. Everything is deterministic and local; no model
calls. The command edits `film.html` and writes pages into `pages/`, so the
editor, previews and exports all read the change the same way, and git diffs
stay reviewable.

Natural-language asks map straight onto it: "add text the crowd walks through
on shot 3" is `--shot 3 --effect text-behind --text "WALKING IN TOKYO"`;
"punch in on shot 5" is `--shot 5 --effect punch`. An agent that can describe
a reference clip's style ("glitch on the drop, then a whip into a freeze
frame with a label") writes the two or three commands and runs them in order.

## Timing

`--at T` places the effect on the film timeline; the default is 0.2s into the
shot (`--offset`). A clip's animation clock starts at its own `at`, so
shot-relative beats (a shake 1.2s into a shot, a whip on its last 0.4s) are
computed for you.

## Effects

Text and cards (overlay-first: text rides the footage, no tempo-breaking
cutaways; the full-frame card is still there with the scenes API):

| effect | params | what it does |
| --- | --- | --- |
| `caption-pop` | `--text "one|two|three"` `--fg` `--accent` | word-by-word captions, the current word highlighted |
| `ktype` | `--text "BIG|*LINES*"` | kinetic type slamming in over the shot (`*` = accent) |
| `caption` | `--text "KICKER|the main line"` | small kicker, accent bar, main line |
| `meme` | `--text "TOP|BOTTOM"` | meme top/bottom text, white with black stroke |
| `hook` | `--text "setup|the turn"` | two-line hook, the turn in accent italic |
| `freeze-pop` | `--image ../assets/cutout.png --label "THE LEGEND"` | a cutout still pops in with a hard outline |

Transitions and motion (these edit the shot clip itself; keyframes only use
translate/scale/rotate, per the film format):

| effect | params | what it does |
| --- | --- | --- |
| `flash` | `--dur 0.12 --fg #ffffff` | white flash cut |
| `film-burn` | `--dur 0.4` | burn/light-leak over the cut |
| `whip-in` / `whip-out` | `--dur 0.4` | whip pan in (start) or out (last `--dur` of the shot) |
| `shake` | `--amp 7 --dur 0.35 --offset 1.2` | beat-hit jitter |
| `punch` | `--zoom 1.15` | slow push-in across the whole shot |
| `glitch` | `--dur 0.7` | RGB-split glitch (adds two blended copies) |
| `mirror` | | mirror split-screen (left normal, right flipped) |
| `ramp` | `--speeds 0.5,1,2` | speed ramp: the shot split into speed sections |
| `grain` | `--dur --strength 0.45` | film grain + vignette overlay |
| `sticker` | `--shape star|burst|heart` | SVG sticker pop (emoji glyphs are unreliable headless) |

The heavy one:

| effect | params | what it does |
| --- | --- | --- |
| `text-behind` | `--text "LINE ONE|LINE TWO" --at --dur` | text anchored in the scene with people walking in front of it |

`text-behind` bakes the window offline: per-frame person masks (rembg/u2netp,
free and local), temporal smoothing, feathered edges, text composited under
the people, written to `assets/text-behind-N.mp4`; the shot is split and its
window replaced with the baked asset. Cost is real: about 0.75s of compute
per frame on a small box, so keep it to 2-4s beats. It needs `rembg`, `numpy`
and `Pillow` where it runs, and an un-sped shot (ramp first is not
supported).

## Recipes: "make this shot like that reference"

A recipe is a JSON list of the same operations, so a style can be replayed
onto any shot:

```json
[
  {"effect": "shake", "offset": 1.2, "dur": 0.35},
  {"effect": "whip-out", "dur": 0.4},
  {"effect": "caption-pop", "text": "code.|games.|rockets.", "offset": 0.6}
]
```

An agent (or a person) watches or describes the reference, writes the recipe,
and applies it shot by shot. Deterministic: the same recipe on the same film
produces the same edit.

## What is automatic vs still manual

Automatic today: every effect above, per shot, from one command; timing math;
overlay track placement; keyframe injection; the text-behind mask pipeline.
Still manual: picking which shot and which effect (the creative call, by a
person or an agent), choosing words, and the freeze-pop cutout image (auto
cutout from the shot's midpoint frame is the planned addition). Render still
goes through the normal export path after the edit.
