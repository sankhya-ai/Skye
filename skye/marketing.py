"""Marketing team preset: a business profile in, a week of content briefs in the backlog.

This is data on top of the existing engine, no new architecture: it registers three marketing
roles, saves one team (strategist leads, copywriter/designer/video-editor are members) and turns
a business profile into pending backlog features the owner approves. Nothing here calls a model,
posts anywhere, or runs a mission. Publishing is deliberately out of scope for this step.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import backlog, roles, teams

TEAM_NAME = "Marketing"
PROJECT = "marketing"
REQUIRED = ("name", "what_you_sell", "audience", "tone")
MAX_FIELD = 1500

# The lead plans (nonwriting); copywriter writes text files only (no Bash).
_ROLES = (
    {"name": "content-strategist",
     "description": "Plans the week: which reels and posts, in what order, for which goal.",
     "catalogue": {"tools": ["Read", "Glob", "Grep"], "writable": False},
     "instructions": "You plan marketing for ONE business using its profile file. Propose a "
                     "mix of reels and posts, each with a hook, a goal and the format. Never "
                     "publish or contact anyone; everything goes to the owner for approval."},
    {"name": "copywriter",
     "description": "Writes captions, hooks and scripts in the business's own tone.",
     "catalogue": {"tools": ["Read", "Glob", "Grep", "Write", "Edit"], "writable": True},
     "instructions": "Write in the tone the profile states. Use only facts, prices and offers "
                     "present in the profile. If a fact is missing, leave a [bracketed] gap."},
)
_MEMBERS = ("copywriter", "designer", "video-editor")


class MarketingError(ValueError):
    pass


def load_profile(path: str | Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError) as error:
        raise MarketingError(f"cannot read business profile: {error}") from error
    if not isinstance(data, dict):
        raise MarketingError("a business profile is a JSON object")
    missing = [k for k in REQUIRED if not str(data.get(k) or "").strip()]
    if missing:
        raise MarketingError("profile is missing: " + ", ".join(missing))
    profile: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, list):
            profile[key] = [str(v).strip()[:MAX_FIELD] for v in value if str(v).strip()]
        else:
            profile[key] = str(value).strip()[:MAX_FIELD]
    return profile


def install(*, roles_path=None, teams_path=None) -> dict[str, Any]:
    """Register the marketing roles and the saved team. Idempotent."""
    for entry in _ROLES:
        roles.add(dict(entry), roles_path)
    existing = teams.get(TEAM_NAME, teams_path)
    entry = {"name": TEAM_NAME, "lead": "content-strategist", "members": list(_MEMBERS),
             "description": "Plans and drafts a week of reels and posts for one business.",
             "handoff": "Strategist plans, copywriter writes, designer and video-editor "
                        "produce files. Nothing is published; the owner approves each item."}
    if existing:
        entry["id"] = existing["id"]
    return teams.add(entry, teams_path)


_WEEK = (
    ("Reel", "reel", "Show the product or service in use in 15 to 30 seconds."),
    ("Post", "post", "One benefit, one image, one clear call to action."),
    ("Reel", "reel", "Behind the scenes or how it is made."),
    ("Post", "post", "Customer question answered in the business's voice."),
    ("Reel", "reel", "Before and after, or a quick tip."),
    ("Post", "post", "Offer or reason to visit this week."),
    ("Post", "post", "Story-style personal note from the owner."),
)


def plan_week(profile: dict[str, Any], *, backlog_path=None) -> list[dict[str, Any]]:
    """Turn a profile into seven PENDING backlog items (briefs, not finished content)."""
    sells = profile["what_you_sell"]
    context = (f"Business: {profile['name']}. Sells: {sells}. Audience: {profile['audience']}. "
               f"Tone: {profile['tone']}.")
    if profile.get("style_references"):
        context += " Style references: " + "; ".join(profile["style_references"]) + "."
    entries = []
    for day, (kind, label, idea) in enumerate(_WEEK, start=1):
        entries.append({
            "title": f"Day {day} {kind}: {profile['name']}",
            "brief": f"{context}\nIdea: {idea}\nDeliver a draft file only. Do not publish.",
            "kind": "content",
            "labels": ["type:content", f"format:{label}"]})
    return backlog.propose(entries, project=PROJECT, mission="marketing-preset",
                           path=backlog_path)
