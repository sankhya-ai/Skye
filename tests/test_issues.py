from skye import backlog, issues


def test_report_files_a_pending_backlog_proposal(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYE_ISSUES", str(tmp_path / "skye.issues.json"))
    monkeypatch.setenv("SKYE_BACKLOG", str(tmp_path / "skye.backlog.json"))
    issue, created = issues.report("frontend-error", "Checkout button does nothing",
                                   detail="TypeError: total is undefined", project="shop",
                                   source="browser-hook")
    assert created
    assert issue["occurrences"] == 1
    features = backlog.list_features()
    assert len(features) == 1
    assert features[0]["status"] == "pending"
    assert features[0]["project"] == "shop"
    assert features[0]["proposed_by"] == issue["id"]
    assert "TypeError" in features[0]["brief"]
    assert issues.list_issues()[0]["feature_id"] == features[0]["id"]


def test_duplicate_signal_bumps_count_not_files_again(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYE_ISSUES", str(tmp_path / "skye.issues.json"))
    monkeypatch.setenv("SKYE_BACKLOG", str(tmp_path / "skye.backlog.json"))
    first, created1 = issues.report("production-log", "OOM in worker", project="shop",
                                    source="deploy-feed")
    second, created2 = issues.report("production-log", "oom in worker", project="shop",
                                     source="deploy-feed")
    assert created1 and not created2
    assert second["id"] == first["id"]
    assert second["occurrences"] == 2
    assert len(backlog.list_features()) == 1
    assert len(issues.list_issues()) == 1


def test_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYE_ISSUES", str(tmp_path / "skye.issues.json"))
    monkeypatch.setenv("SKYE_BACKLOG", str(tmp_path / "skye.backlog.json"))
    for kind, summary in (("nonsense", "x"), ("frontend-error", "")):
        try:
            issues.report(kind, summary)
            raise AssertionError("expected IssueError")
        except issues.IssueError:
            pass


def test_intake_is_bounded_untrusted_data(tmp_path, monkeypatch):
    """Signal content is untrusted: oversized detail is truncated, unknown kinds rejected,
    and a report can never create anything but a PENDING proposal (no approvals, no tools)."""
    monkeypatch.setenv("SKYE_ISSUES", str(tmp_path / "skye.issues.json"))
    monkeypatch.setenv("SKYE_BACKLOG", str(tmp_path / "skye.backlog.json"))
    issue, created = issues.report("production-log", "boom", detail="x" * 9000,
                                   source="feed")
    assert created
    assert len(issue["detail"]) == 4000
    # the filed proposal is pending with no mission attached - nothing can start from intake
    feature = backlog.list_features()[0]
    assert feature["status"] == "pending"
    assert feature["mission_id"] == ""
    # approvals registry untouched by intake
    from skye import approvals
    assert approvals.pending(tmp_path / "skye.approvals.json") == []
