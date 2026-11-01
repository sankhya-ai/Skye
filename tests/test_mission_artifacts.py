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


def test_role_instructions_bind_at_validation_into_approval_fingerprint(tmp_path, monkeypatch):
    roles_file = tmp_path / "roles.json"
    roles_file.write_text(json.dumps({"roles": [
        {"name": "designer", "instructions": "Diverge first. No generic hooks."}]}))
    monkeypatch.setenv("SKYE_ROLES", str(roles_file))
    (tmp_path / "repo").mkdir()
    raw = {"goal": "make a poster", "brief": "Poster for the launch.",
           "repo": str(tmp_path / "repo"), "role": "designer", "adapter": "root",
           "gates": [], "budget": {}}
    from skye.approvals import fingerprint
    from skye.contract import validate

    spec = validate(dict(raw))
    # the brief stays the owner's own words; instructions live in their own contract field
    assert spec["brief"] == "Poster for the launch."
    assert spec["role_instructions"] == "Diverge first. No generic hooks."
    approved_fp = fingerprint(spec)

    # re-validation of an already-validated contract is idempotent under an unchanged registry
    assert validate(dict(spec)) == spec
    assert fingerprint(dict(spec)) == approved_fp

    # editing the registry afterwards FAILS CLOSED: fingerprints are computed through
    # validate() under the current registry, so the approved text can no longer match -
    # the old approval is useless and a fresh approval under the new instructions is needed.
    roles_file.write_text(json.dumps({"roles": [
        {"name": "designer", "instructions": "SUBSTITUTED instructions."}]}))
    assert fingerprint(spec) != approved_fp          # old approval cannot be consumed
    substituted = validate(dict(raw))
    assert substituted["role_instructions"] == "SUBSTITUTED instructions."
    assert fingerprint(substituted) != approved_fp

    # and with no registry instructions at all, the field disappears and the fingerprint
    # changes again - never a silent fall-back to the approved text.
    monkeypatch.setenv("SKYE_ROLES", str(tmp_path / "absent.json"))
    plain = validate(dict(raw))
    assert "role_instructions" not in plain
    assert fingerprint(plain) != approved_fp


def test_reference_files_snapshot_at_validation_into_fingerprint(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "DESIGN.md").write_text("# Design\nDark canvas, roman display face, no eyebrow chips.\n")
    (repo / "PRODUCT.md").write_text("# Product\nA shop whose cart keeps its own receipts.\n")
    raw = {"goal": "make a poster", "brief": "Poster for the launch.",
           "repo": str(repo), "role": "developer", "adapter": "root",
           "gates": [], "budget": {}, "reference_files": ["DESIGN.md", "PRODUCT.md"]}
    from skye.approvals import fingerprint
    from skye.contract import ContractError, validate
    import pytest

    spec = validate(dict(raw))
    assert [d["path"] for d in spec["reference_docs"]] == ["DESIGN.md", "PRODUCT.md"]
    assert "no eyebrow chips" in spec["reference_docs"][0]["content"]
    approved_fp = fingerprint(spec)

    # idempotent under unchanged files
    assert validate(dict(spec)) == spec
    assert fingerprint(dict(spec)) == approved_fp

    # editing a referenced file FAILS CLOSED: the old approval's fingerprint no longer matches
    (repo / "DESIGN.md").write_text("# Design\nBeige everything.\n")
    assert fingerprint(spec) != approved_fp
    substituted = validate(dict(raw))
    assert "Beige" in substituted["reference_docs"][0]["content"]
    assert fingerprint(substituted) != approved_fp

    # traversal, symlinks, and missing files are rejected before anything runs
    for bad in ("../outside.md", "absent.md"):
        with pytest.raises(ContractError):
            validate(dict(raw, reference_files=[bad]))
    (tmp_path / "real.md").write_text("x")
    (repo / "link.md").symlink_to(tmp_path / "real.md")
    with pytest.raises(ContractError):
        validate(dict(raw, reference_files=["link.md"]))

    # no references -> the field disappears entirely, never a stale snapshot
    plain = validate(dict(raw, reference_files=[]))
    assert "reference_docs" not in plain
