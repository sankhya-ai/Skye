"""Mission artifacts: real changed-files derived from recorded repo manifests."""
import json

from skye.journey import diff_manifests, repo_manifest
from skye.ui.server import mission_artifacts


def _write(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_repo_manifest_hashes_and_skips_git(tmp_path):
    _write(tmp_path, "app.py", "print('v1')\n")
    _write(tmp_path, ".git/HEAD", "ref: refs/heads/main\n")
    manifest, truncated = repo_manifest(str(tmp_path))
    assert not truncated
    assert set(manifest) == {"app.py"}
    _write(tmp_path, "app.py", "print('v2')\n")
    again, _ = repo_manifest(str(tmp_path))
    assert again["app.py"] != manifest["app.py"]


def test_repo_manifest_limit_truncates(tmp_path):
    for i in range(5):
        _write(tmp_path, f"f{i}.txt", str(i))
    manifest, truncated = repo_manifest(str(tmp_path), limit=3)
    assert truncated and len(manifest) == 3


def test_diff_manifests():
    start = {"a.py": "1", "b.py": "2", "c.py": "3"}
    end = {"a.py": "1", "b.py": "9", "d.py": "4"}
    assert diff_manifests(start, end) == {"added": ["d.py"], "modified": ["b.py"],
                                          "deleted": ["c.py"]}


def test_mission_artifacts_unrecorded(tmp_path):
    assert mission_artifacts(tmp_path) == {"recorded": False}


def test_mission_artifacts_baseline_only(tmp_path):
    (tmp_path / "artifacts.json").write_text(json.dumps(
        {"repo": "x", "start": {"a.py": "1"}, "start_truncated": False}))
    out = mission_artifacts(tmp_path)
    assert out["recorded"] and out["complete"] is False


def test_mission_artifacts_full_diff(tmp_path):
    (tmp_path / "artifacts.json").write_text(json.dumps({
        "repo": "x", "start": {"a.py": "1", "b.py": "2"}, "start_truncated": False,
        "end": {"a.py": "1", "b.py": "7", "c.py": "3"}, "end_truncated": False}))
    out = mission_artifacts(tmp_path)
    assert out["complete"] is True
    assert out["added"] == ["c.py"] and out["modified"] == ["b.py"] and out["deleted"] == []
