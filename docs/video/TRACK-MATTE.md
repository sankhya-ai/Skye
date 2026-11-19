# Prepared track matte

Video clips can declare a prepared, moving alpha matte:

```html
<video id="person" src="assets/clip.mp4#t=2,4" at="0"
       matte="assets/masks/manifest.json"
       style="object-fit:cover"></video>
```

The same field is `matte: "assets/masks/manifest.json"` in the film model.
A first-class clip property, not a baked foreground video. It round-trips
through the native parser/serializer. The regular preview and export frame
path reads it, and the straight-footage optimization cannot bypass it.
No VP9 alpha codec is needed. The original video remains editable.

Manifest:

```json
{"fps":30,"start":2,"frames":["f000.png","f001.png","f002.png"]}
```

`start` is source time, not timeline time. Thus trimmed or sped clips select
the corresponding source frame. Supply enough frames for the entire selected
source window. The example lists only three frames for illustration.
Frames are RGBA PNGs with the mask in alpha: opaque white retains footage,
transparent removes it. They are relative to the manifest. Pixel geometry
must already match the clip's displayed box, including any cover crop. The
mask stretches across that box. Arbitrary crop/object-position combinations
are not automatically reconstructed by this primitive.

Missing manifests/images or out-of-range source time stop export with an
error. They do not silently show unmasked footage. Do not change manifest
content during an open browser session; reload after preparing a new version.

The property can be edited via film.html/API. The Masks panel provides prepared-alpha edge correction (see below). Native project dependency collection follows declared matte manifests and
all their relative frames for versioning and linked-folder packaging. Missing or
invalid manifests and missing frames stop a portable-project export. The project
ZIP copies the sequence as real files, including when its source folder is linked. Alpha PNGs require
more disk space than an alpha video. The primitive consumes a prepared mask;
it is not a person segmentation model.

Verification: a two-second native render used a moving alpha strip over a
colored video and text. Inspected exported pixels and images at 0.2s/1.8s
confirmed the mask moved and revealed/occluded the expected background.
Parser round-trip, unsafe paths, source-time indexing, and export shortcut
protection have regression tests.

## Existing local cutout path

Skye already has `--of-cutout` in the preview and exact export path. It is
not an absent renderer hook. Mode 0 uses the bundled lightweight person
model; modes 1/2/4 use other models. A short mode-0 render completed on the
small test machine and visibly removed the background, but the shoulder
edge was soft and ragged. Model choice and footage affect quality. A failure
of a larger model does not prove all cutout modes are unusable.

## Prepared-mask edge correction UI

**Masks** in the storyboard lists prepared alpha layers, including layers hidden from the shot cards. The first source-time alpha is displayed. Grow/shrink (-20..20 mask pixels) uses a max/min filter, followed by an optional additional Gaussian feather (0..20 mask pixels). The correction applies to every frame, writes a new sequence, and changes only that layer's native `matte` field. Native Undo restores the previous manifest. Locked tracks and stale revisions are rejected. Originals are never rewritten. New versions consume disk space.

This is a bounded edge-correction panel, not brush/roto editing or per-frame shape repair. It does not edit live `--of-cutout` effects, add segmentation, select a subject, or repair wrongly classified body parts. The alpha preview is the current first source frame, not a live correction preview. Inspect the full composed export. A 60-frame moving-strip workflow verified shrink/feather, native Undo, rerun, and exported motion. This synthetic test does not demonstrate hair/hand/people segmentation quality.
