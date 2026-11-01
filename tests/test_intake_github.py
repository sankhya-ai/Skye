"""GitHub intake connector: read-only pull -> PENDING proposals, honest about limits."""
from __future__ import annotations

import io
import json
import urllib.error

import pytest

from skye import intake_github, issues, backlog


class FakeResponse:
    def __init__(self, payload, headers=None):
        self.payload = payload
        self.headers = headers or {"x-ratelimit-limit": "60",
                                   "x-ratelimit-remaining": "57",
                                   "x-ratelimit-reset": "1793523406"}

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


ISSUES = [
    {"number": 12, "title": "Login 500s on Safari", "body": "stacktrace...",
     "html_url": "https://github.com/acme/app/issues/12",
     "labels": [{"name": "bug"}]},
    {"number": 13, "title": "Dark mode", "body": "", 
     "html_url": "https://github.com/acme/app/issues/13", "labels": []},
    {"number": 14, "title": "a PR, not an issue", "pull_request": {},
     "html_url": "https://github.com/acme/app/pull/14", "labels": []},
]


def fake_opener(request, timeout=0):
    return FakeResponse(ISSUES)


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYE_ISSUES", str(tmp_path / "issues.json"))
    monkeypatch.setenv("SKYE_BACKLOG", str(tmp_path / "backlog.json"))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    return tmp_path


def test_import_files_pending_proposals_only(store):
    result = intake_github.import_issues("acme/app", project="app", opener=fake_opener)
    assert result["fetched"] == 2  # the PR is not intake
    assert len(result["created"]) == 2
    features = backlog.list_features()
    assert len(features) == 2
    assert all(f["status"] == "pending" for f in features)  # owner decides, nothing started
    assert "Safari" in features[0]["title"] or "Safari" in features[1]["title"]
    assert result["rate"]["remaining"] == "57" and result["rate"]["authenticated"] is False


def test_reimport_dedupes_by_external_id(store):
    intake_github.import_issues("acme/app", opener=fake_opener)
    again = intake_github.import_issues("acme/app", opener=fake_opener)
    assert again["created"] == [] and len(again["already_known"]) == 2
    assert len(backlog.list_features()) == 2  # not filed twice
    rows = issues.list_issues()
    assert all(i["occurrences"] == 2 for i in rows)  # re-seen is recorded, not re-filed


def test_rate_limit_is_a_visible_fact(store):
    headers = {"x-ratelimit-limit": "60", "x-ratelimit-remaining": "0",
               "x-ratelimit-reset": "1793523406"}
    def denied(request, timeout=0):
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", headers, None)
    with pytest.raises(intake_github.IntakeError, match="rate limit exhausted.*1793523406"):
        intake_github.fetch("acme/app", opener=denied)


def test_private_or_missing_repo_says_so(store):
    def missing(request, timeout=0):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)
    with pytest.raises(intake_github.IntakeError, match="private repository needs GITHUB_TOKEN"):
        intake_github.fetch("acme/secret", opener=missing)


def test_token_used_only_when_provided(store, monkeypatch):
    seen = []
    def observing(request, timeout=0):
        seen.append(request.headers.get("Authorization"))
        return FakeResponse([])
    intake_github.fetch("acme/app", opener=observing)
    assert seen == [None]  # unauthenticated by default
    monkeypatch.setenv("GITHUB_TOKEN", "tok123")
    intake_github.fetch("acme/app", opener=observing)
    assert seen[1] == "Bearer tok123"
