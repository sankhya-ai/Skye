# Multi-clip same-source regression

Seven clips all pointing at ONE source file with different `#t=in,out` ranges, mixed
with an iframe card and a filtered clip: the "selects from one long take" pattern.
A render must draw each clip's own range, not a neighbor's.

## Build the source and render

    # 36s of testsrc2 with a burned-in source timecode (S h:mm:ss.cs, centered so the
    # vertical cover crop keeps it on stage)
    ffmpeg -f lavfi -i "testsrc2=size=1920x1080:rate=30:duration=36" \
      -vf "drawtext=text='S%{pts\:hms}':x=(w-text_w)/2:y=(h-text_h)/2:fontsize=64:fontcolor=white:box=1:boxcolor=black" \
      -c:v libx264 -preset fast -crf 18 -pix_fmt yuv420p src.mp4
    node ../interleave-test/render-range.mjs . ../../skye/studio/vendor/openfilm 0 19 out.mp4

## Verify

Timeline 1,4,7,11,13,15,17s must show source 2,6,10,14,18,22,26s respectively
(clips start at source 1,5,9,13,17,21,25 and each check is 1s into its clip).
`verify.py` compares a render against the source at those times with SSIM
(max at the expected source time versus its neighbors) and writes `verdict.jpg`
with the cropped timecodes for a human check.

Proven 2026-11-19: all seven frames read the expected source second (30fps software
render, 2 workers), including the filtered clip. Known noise: the render logs
"copy: src.mp4: could not be made, drawn as it is" (seekable proxy fails silently;
frames are still correct).
