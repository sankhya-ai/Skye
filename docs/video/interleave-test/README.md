# Interleave test: inserts between cuts, real render

Question: can storyboard shots (code scenes), images and other clips sit
anywhere between and around cuts of one source clip on the Studio timeline,
not only at the start and end?

Answer (2026-11-19, Linux, software x264): yes. One 12 s vertical
1080x1920 source was cut at 3/6/9 with the editor's own `split` op, later
cuts rippled with `move` ops, and a code scene, a PNG still, a second video
and an endcard code scene were placed in the gaps with `insert` ops — all
through `applyOps`, the same operation layer Studio's timeline and the
dispatcher bridge use. The result rendered through the real renderer to one
21 s H.264 + AAC MP4. Frame checks at 1.5 / 4 / 6.5 / 9 / 11.5 / 14.5 /
17.5 / 20 s each showed the expected segment (source frame numbers confirm
each cut kept its own source range).

Timeline produced:

    0-3    tura.mp4 #t=0,3
    3-5    scenes/act1.html      (code scene, animated)
    5-8    tura.mp4 #t=3,6
    8-10   assets/meme.png       (still image)
    10-13  tura.mp4 #t=6,9
    13-16  assets/webclip.mp4    (stand-in for a clip pulled from the web)
    16-19  tura.mp4 #t=9,12
    19-21  scenes/endcard.html   (code scene)

Cost on a small Linux box: about 44 s per 7 s of 1080x1920 at 30 fps with
two software workers. A killed render's chunks were reused on rerun
(render-resume works). M1, proxies, 4K and long sessions remain unproven,
and this test drove ops and the renderer directly, not Studio's UI clicks.

Synthetic fixtures only: `testsrc2`/`rgbtestsrc` clips with a frame-number
overlay, a drawn PNG, no real footage. The web-clip stand-in was generated
locally; nothing was downloaded.

Rerun: make `assets/` (any 12 s 1080x1920 `tura.mp4`, 3 s `webclip.mp4`,
1080x1920 `meme.png`), keep `scenes/` and the baseline `film.html` from this
folder's git history, then:

    node interleave.mjs /path/to/project /path/to/skye/studio/vendor/openfilm
    node render-range.mjs /path/to/project /path/to/skye/studio/vendor/openfilm 0 7 seg1.mp4
