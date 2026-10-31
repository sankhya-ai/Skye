// KIMI_EXTRA_SKILLS_DIRS: host-supplied skill roots (root's promoted skills).
// Its own test binary, because it sets a process-wide environment variable.

use std::path::Path;

use tempfile::TempDir;

use kaos::KaosPath;
use kimi_agent::skill::{discover_skills_from_roots, get_builtin_skills_dir, resolve_skills_roots};

fn write_skill(skill_dir: &Path, name: &str, description: &str) {
    std::fs::create_dir_all(skill_dir).expect("create skill dir");
    std::fs::write(
        skill_dir.join("SKILL.md"),
        format!("---\nname: {name}\ndescription: {description}\n---\n\nBody.\n"),
    )
    .expect("write skill");
}

#[tokio::test]
async fn test_extra_skills_dirs_load_before_project_skills() {
    let tmp = TempDir::new().expect("temp dir");
    let learned = tmp.path().join("learned");
    let missing = tmp.path().join("missing");
    let work_dir = tmp.path().join("project");
    let project = work_dir.join(".agents/skills");
    write_skill(&learned.join("verify-first"), "verify-first", "learned");
    write_skill(&learned.join("shared"), "shared", "learned copy");
    write_skill(&project.join("shared"), "shared", "project copy");

    let joined = std::env::join_paths([&learned, &missing]).expect("join paths");
    // SAFETY: this binary has one test, so nothing reads the environment concurrently.
    unsafe { std::env::set_var("KIMI_EXTRA_SKILLS_DIRS", joined) };

    let roots = resolve_skills_roots(&KaosPath::unsafe_from_local_path(&work_dir), None).await;
    let learned_root = KaosPath::unsafe_from_local_path(&learned);
    let project_root = KaosPath::unsafe_from_local_path(&project);
    assert_eq!(roots.first(), Some(&KaosPath::unsafe_from_local_path(&get_builtin_skills_dir())));
    let learned_at = roots.iter().position(|r| *r == learned_root).expect("learned root");
    let project_at = roots.iter().position(|r| *r == project_root).expect("project root");
    assert!(learned_at < project_at, "learned skills must load before project skills");
    assert!(!roots.contains(&KaosPath::unsafe_from_local_path(&missing)), "missing dirs are skipped");

    let skills = discover_skills_from_roots(&roots).await;
    let verify = skills.iter().find(|s| s.name == "verify-first").expect("learned skill");
    assert_eq!(verify.description, "learned");
    let shared = skills.iter().find(|s| s.name == "shared").expect("shared skill");
    assert_eq!(shared.description, "project copy", "the project's own skill wins");

    unsafe { std::env::remove_var("KIMI_EXTRA_SKILLS_DIRS") };
}
