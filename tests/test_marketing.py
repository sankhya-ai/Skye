import json

import pytest

from skye import backlog, marketing, teams


PROFILE = {"name": "Chai Corner", "what_you_sell": "masala chai and snacks",
           "audience": "college students nearby", "tone": "warm, funny",
           "style_references": ["bright handheld shots"]}


def _profile(tmp_path, data=PROFILE):
    p = tmp_path / "profile.json"
    p.write_text(json.dumps(data))
    return p


def test_install_saves_team_idempotently(tmp_path, monkeypatch):
    r, t = tmp_path / "roles.json", tmp_path / "teams.json"
    monkeypatch.setenv("SKYE_ROLES", str(r))  # team validation reads the default registry
    first = marketing.install(roles_path=r, teams_path=t)
    second = marketing.install(roles_path=r, teams_path=t)
    assert first["id"] == second["id"]
    assert len(teams.list_teams(t)) == 1
    assert first["lead"] == "content-strategist"


def test_plan_week_creates_seven_pending_and_no_duplicates(tmp_path):
    b = tmp_path / "backlog.json"
    added = marketing.plan_week(marketing.load_profile(_profile(tmp_path)), backlog_path=b)
    assert len(added) == 7
    assert {f["status"] for f in backlog.list_features(b)} == {"pending"}
    assert marketing.plan_week(marketing.load_profile(_profile(tmp_path)), backlog_path=b) == []


def test_profile_requires_fields(tmp_path):
    with pytest.raises(marketing.MarketingError):
        marketing.load_profile(_profile(tmp_path, {"name": "x"}))
