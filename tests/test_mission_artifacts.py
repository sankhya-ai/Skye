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


def _hash(text):
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _mission_dir(tmp_path, repo, end=None):
    import json as _json
    if end is None:
        end = {"poster.svg": _hash("<svg xmlns='http://www.w3.org/2000/svg'/>"),
               "app.py": _hash("x = 1\n"), "notes.txt": "3"}
    mdir = tmp_path / "state" / "missions" / "m1"
    mdir.mkdir(parents=True)
    (mdir / "artifacts.json").write_text(_json.dumps({
        "repo": str(repo), "start": {}, "start_truncated": False,
        "end": end, "end_truncated": False}))
    return tmp_path / "state"


def test_artifact_file_serves_recorded_svg(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "poster.svg", "<svg xmlns='http://www.w3.org/2000/svg'/>")
    state = _mission_dir(tmp_path, repo)
    from skye.ui.server import artifact_file
    data, content_type = artifact_file(state, "m1", "poster.svg")
    assert content_type == "image/svg+xml" and data.startswith(b"<svg")


def test_artifact_file_refuses_unrecorded_and_unpreviewable(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "app.py", "x = 1\n")       # recorded but not previewable
    _write(repo, "secret.env", "TOKEN=x\n")  # previewable-looking target outside the diff
    state = _mission_dir(tmp_path, repo)
    from skye.ui.server import artifact_file
    import pytest
    with pytest.raises(KeyError):
        artifact_file(state, "m1", "app.py")          # not a preview type
    with pytest.raises(KeyError):
        artifact_file(state, "m1", "secret.env")      # not in the recorded diff
    with pytest.raises(KeyError):
        artifact_file(state, "m1", "../outside.svg")  # traversal never reaches the repo check


def test_artifact_file_refuses_mutation_after_record(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "poster.svg", "<svg xmlns='http://www.w3.org/2000/svg'>EDITED</svg>")
    state = _mission_dir(tmp_path, repo)  # record holds the pre-edit hash
    from skye.ui.server import FileChangedError, artifact_file
    import pytest
    with pytest.raises(FileChangedError):
        artifact_file(state, "m1", "poster.svg")


def test_artifact_file_refuses_symlink_escape(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.svg"
    outside.write_text("<svg/>")
    (repo / "poster.svg").symlink_to(outside)
    state = _mission_dir(tmp_path, repo, end={"poster.svg": _hash("<svg/>")})
    from skye.ui.server import artifact_file
    import pytest
    with pytest.raises(KeyError):
        artifact_file(state, "m1", "poster.svg")


def test_role_instructions_reach_worker_without_touching_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYE_ROLES", str(tmp_path / "roles.json"))
    (tmp_path / "roles.json").write_text(json.dumps({"roles": [
        {"name": "designer", "instructions": "Diverge first. No generic hooks."}]}))
    from skye.roles import instructions_for
    assert instructions_for("designer") == "Diverge first. No generic hooks."
    assert instructions_for("developer") == ""

    import skye.dispatcher as dispatcher
    captured = {}

    def fake_run(spec, **kwargs):
        captured["brief"] = spec.get("brief")
        from skye.adapters.base import Result
        return Result(ok=True, output="done")

    (tmp_path / "repo").mkdir(exist_ok=True)
    contract = {"goal": "make a poster", "brief": "Poster for the launch.", "repo": str(tmp_path / "repo"),
                "role": "designer", "adapter": "root", "gates": [], "budget": {}}
    from skye.contract import validate
    spec = validate(contract)
    spec["id"] = "s1"
    from skye.policy import Policy
    policy = Policy(data={"allow_unattended": [{"adapter": "root", "role": "*"}]})
    outcome = dispatcher.dispatch(spec, policy=policy, runner=fake_run)
    assert outcome.status == "done"
    assert captured["brief"].startswith("Diverge first. No generic hooks.\n\nPoster for the launch.")
    assert spec.get("brief") == "Poster for the launch."  # the contract itself is untouched
