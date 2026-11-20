"""Insert-with-ripple planner for Studio timelines.

Given a film (the Studio/OpenFilm document: tracks of clips that each carry
``at`` seconds on the track, ``src``, and either a ``time`` [in, out] window
for footage or a ``len`` for pages and stills), produce the op batch that
inserts one clip at a point in time, splitting whatever it lands on and
moving everything at or after that point on every track by the new clip's
length. The ops feed straight into the Studio applyOps layer, the same
shared edit language the editor, the agent bridge, and scripts use.

This is a pure planner: it never touches the film itself, so callers can
review or adjust the batch before applying it.
"""
from __future__ import annotations

from typing import Any

_EPS = 1e-6


def _clip_start(clip: dict[str, Any]) -> float:
    return float(clip.get("at") or 0.0)


def _clip_len(clip: dict[str, Any]) -> float:
    time = clip.get("time")
    if isinstance(time, (list, tuple)) and len(time) == 2:
        return (float(time[1]) - float(time[0])) / float(clip.get("speed") or 1)
    if "len" in clip:
        return float(clip["len"])
    raise ValueError(f"clip {clip.get('id')!r} has no length: needs time [in, out] or len")


def plan_insert(film: dict[str, Any], track: int, at: float, clip: dict[str, Any], insert_len: float) -> list[dict[str, Any]]:
    """Plan the ops that insert ``clip`` at ``at`` seconds on ``track``.

    - Every clip on every track that starts at or after ``at`` moves right by
      ``insert_len`` seconds.
    - A clip spanning ``at`` splits there; its right half moves right too.
    - The new clip inserts on ``track`` at ``at``.

    Returns the op batch in apply order. Raises ValueError on bad input.
    """
    tracks = film.get("tracks") or []
    if not (0 <= track < len(tracks)):
        raise ValueError(f"no track {track}")
    if at < 0:
        raise ValueError("at must be seconds from 0")
    if insert_len <= 0:
        raise ValueError("insert length must be positive")
    if not isinstance(clip.get("src"), str) or not clip["src"]:
        raise ValueError("the inserted clip needs a src")

    ops: list[dict[str, Any]] = []
    for ti, tr in enumerate(tracks):
        for c in list(tr.get("clips") or []):
            cid = c.get("id")
            start, length = _clip_start(c), _clip_len(c)
            end = start + length
            if start >= at - _EPS:
                ops.append({"op": "move", "clip": cid, "track": ti, "at": round(start + insert_len, 3)})
            elif start + _EPS < at < end - _EPS:
                base = float(c.get("time")[0]) if isinstance(c.get("time"), (list, tuple)) else 0.0
                off = at - start
                rest = {
                    "op": "split", "clip": cid, "at": round(at, 3),
                    "time": [round(base, 3), round(base + off, 3)],
                    "restTime": [round(base + off, 3), round(base + length, 3)],
                    "restId": f"{cid}-rest",
                }
                ops.append(rest)
                ops.append({"op": "move", "clip": f"{cid}-rest", "track": ti, "at": round(at + insert_len, 3)})
    ops.append({"op": "insert", "track": track, "at": round(at, 3), "clip": {**clip, "at": round(at, 3)}})
    return ops
