# Alpha video declaration and local fallback

```html
<video id="cutout" src="assets/cutout.webm#t=0,2" at="0" alpha
       style="object-fit:cover"></video>
```

`alpha` (model: `alpha: true`) declares transparency and protects the clip
from the opaque straight-footage export shortcut. It does not create an
alpha channel in an opaque video. The ordinary media probe also recognizes
VP9 alpha_mode metadata, including when ffprobe reports yuv420p; such sources
are excluded from the straight path even without the declaration.

The existing browser-copy path is the codec fallback: a format Chromium
cannot decode (e.g. QuickTime Animation RGBA) is copied to VP9 WebM while
retaining alpha. This change makes the copy path work with older FFmpeg
using `-vsync 0`, rather than the newer unsupported `-fps_mode` option.
When an alpha VP9 input is re-encoded, it selects the libvpx VP9 decoder;
FFmpeg's native VP9 decoder can drop the auxiliary alpha plane. Cache version
was bumped so old incompatible copies are not reused.

No transparency support on the installed encoder/decoder is a real error,
not permission to flatten the clip. Prepared alpha PNG mattes with the native
`matte` property are a codec-independent alternative, described in
TRACK-MATTE.md. They are selected explicitly, not automatically generated
on a codec error. There is no new fallback configuration panel in the UI.

## Verified

- Explicit alpha declaration round-trips through film.html/model.
- VP9 alpha metadata is excluded from opaque straight-footage handling.
- A generated RGBA QuickTime Animation source was automatically converted to
  WebM by the normal render fallback. A two-second render over colored text
  preserved transparent regions and the moving opaque strip. Actual exported
  frames were inspected.
- An automated local regression creates a tiny RGBA source, requests the
  codec fallback, decodes its output and checks both transparent and opaque
  alpha pixels. The test does not require a paid service or a model.
- The installed FFmpeg 4.4 build successfully encoded a VP9-alpha test. A
  previous assumption that version 5 was required was incorrect. Encoder
  capabilities and actual decoded alpha pixels, not pix_fmt alone, decide.

Not verified here: Apple's HEVC-alpha playback on macOS, hardware alpha
export, or a full matrix of video formats/browser versions. Those remain
separate platform checks.
