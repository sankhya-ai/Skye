# Shot storyboard

Video opens on the shot canvas. The editor remains the same live project beneath
it. Click a shot frame to open the timeline at that clip; Back to storyboard
returns. Pan/zoom, minimap, source frames, timings, text/effect metadata and saved
notes are real. Thumbnails are source frames, not a promise of the final
composited crop/effect appearance. Standalone page cards get a rendered frame.

Notes are currently saved in Skye's local state by project/clip ID. The embedded
video-editor receives the same current storyboard and notes in its next brief.
Notes are not yet a portable, project-owned collaboration format. A shot description
is the owner's editable note, not automatically generated scene understanding.

The canvas uses the same free @xyflow/react library already used by the vendor,
under its MIT license. No vendor generation canvas workflow or provider calls
are enabled by this feature. Film structure remains a linear edit, not a branching
execution graph. Drag a node onto another to place it before that shot; connecting
handles requests the same linear reorder. Arbitrary graph branches are not film
semantics. Position changes are not yet persisted as freeform layout.

Start/End, a selected card's After action, or an edge's Insert shot creates a
planned placeholder or inserts an already-imported local video/image. A placeholder
is a real neutral page clip, not invisible metadata. Delete and reorder ripple
all wholly-contained shot elements. Spanning clips (including long music or overlays)
and locked tracks are refused instead of silently retimed. Split them in the editor
first. This is a current limitation, not a finished continuous-audio workflow.

Click an edge for cut, flash, film burn, glitch, whip-out or fade-black. These use
native edit operations and normal Undo. Fade-black is a dip through black, not a
true overlapping dissolve. Existing incompatible motion/adjustments are refused.
The edge stores restoration data so returning to Cut removes its own changes.
Unused generated page assets/keyframes can remain after replacement or Undo.

All structural changes carry the film revision and apply through the same editor
Undo path. The embedded worker can return an explicit storyboard plan for insertion,
reorder, deletion or an edge effect. This expands beyond selected-clip edits only
for an explicit owner-requested structural task. No real Claude/Codex run has been
verified on this host, because neither CLI is installed.

Known roughness: large boards fit small; initial project switches can show prior
cards while the new board loads; no media browser/uploader in the insertion dialog,
only an already-imported project path; no multi-user note merge; no true dissolve;
source frame cards do not show every applied effect. The audio mixer is a separate
pending slice.

## Audited workflow repairs

The storyboard header now has its own project picker, New project and Import media.
New projects choose vertical (default), landscape or square before shots are added.
Insertion uses an imported-asset picker and explicit source in/out seconds rather
than a typed project path. New footage fills that project frame. Source preview
is available in the insertion dialog; source-range and actual export checks still
matter, and changing the export ratio is not automatic subject-aware reframing.

Spanning-layer policy is explicit. `Keep existing time and length` leaves music
and overlays that cross shot boundaries unchanged on the absolute timeline,
including locked spanning layers. Shot-contained layers follow their shot. Thus
reordering footage does not cut up a music bed. Insert/delete does not stretch,
loop, trim or regenerate that bed: check its end after changing film length.
`Stop and ask me to split them` retains the strict refusal policy. The API defaults
to strict refusal when no policy is supplied. No automatic continuity/beat matching
or bed length repair is implied.

These changes do not yet implement the queued mixer, persistent layout,
project-owned notes, or composed final-frame thumbnails.
