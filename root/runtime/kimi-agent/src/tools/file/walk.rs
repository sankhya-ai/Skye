//! Recursive globbing (`**/*.py`) that skips the directories nobody means.
//!
//! Models reach for `**/…` first — weaker ones almost always. A plain
//! recursive glob would descend into `node_modules`, `.git` or a build tree
//! and take seconds or return noise, which is why the tool used to refuse the
//! pattern outright and cost the model a step. This walk prunes those
//! directories, stops after a bounded number of entries, and matches the
//! pattern against paths relative to the search root.

use std::path::{Path, PathBuf};

/// Directory names never descended into: dependencies, VCS data, build output, caches.
pub const PRUNED: &[&str] = &[
    ".git", ".hg", ".svn", "node_modules", "bower_components", "target", "dist", "build", ".next", ".nuxt",
    ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".cache", ".gradle",
    ".idea", "coverage", ".turbo", ".parcel-cache", ".terraform",
];

/// How many directory entries one search may look at.
pub const MAX_VISITED: usize = 200_000;

pub struct Walked {
    pub matches: Vec<PathBuf>,
    /// The walk stopped at [`MAX_VISITED`] entries: there may be more matches.
    pub truncated: bool,
}

/// Entries a "did you mean" search may look at: it runs on an error path.
const NAME_SEARCH_VISITED: usize = 20_000;

/// Files under `root` whose file name is exactly `name`, nearest first, at
/// most `limit` — the suggestion when a path does not exist.
pub fn find_by_name(root: &Path, name: &str, limit: usize) -> Vec<PathBuf> {
    let mut found = Vec::new();
    let mut visited = 0usize;
    let mut queue = std::collections::VecDeque::from([root.to_path_buf()]);
    while let Some(dir) = queue.pop_front() {
        let Ok(entries) = std::fs::read_dir(&dir) else {
            continue;
        };
        for entry in entries.flatten() {
            visited += 1;
            if visited > NAME_SEARCH_VISITED || found.len() >= limit {
                return found;
            }
            let Ok(kind) = entry.file_type() else {
                continue;
            };
            if kind.is_dir() {
                if !PRUNED.iter().any(|pruned| entry.file_name() == *pruned) {
                    queue.push_back(entry.path());
                }
            } else if entry.file_name() == name {
                found.push(entry.path());
            }
        }
    }
    found
}

/// Paths under `root` matching `pattern` (relative to `root`), pruned and bounded.
pub fn recursive_glob(root: &Path, pattern: &str, include_dirs: bool) -> Result<Walked, String> {
    let matcher = glob::Pattern::new(pattern).map_err(|err| err.to_string())?;
    let options = glob::MatchOptions {
        case_sensitive: true,
        require_literal_separator: true,
        require_literal_leading_dot: true,
    };
    let mut matches = Vec::new();
    let mut visited = 0usize;
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let Ok(entries) = std::fs::read_dir(&dir) else {
            continue;
        };
        for entry in entries.flatten() {
            visited += 1;
            if visited > MAX_VISITED {
                return Ok(Walked { matches, truncated: true });
            }
            let path = entry.path();
            let Ok(kind) = entry.file_type() else {
                continue;
            };
            let is_dir = kind.is_dir();
            if is_dir && PRUNED.iter().any(|name| entry.file_name() == *name) {
                continue;
            }
            if let Ok(relative) = path.strip_prefix(root) {
                if (include_dirs || !is_dir) && matcher.matches_path_with(relative, options) {
                    matches.push(path.clone());
                }
            }
            if is_dir && !kind.is_symlink() {
                stack.push(path);
            }
        }
    }
    Ok(Walked { matches, truncated: false })
}

#[cfg(test)]
mod tests {
    use super::recursive_glob;

    fn touch(root: &std::path::Path, relative: &str) {
        let path = root.join(relative);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, "x").unwrap();
    }

    #[test]
    fn finds_files_at_every_depth_but_not_in_pruned_directories() {
        let tmp = tempfile::TempDir::new().unwrap();
        for file in ["top.py", "src/a.py", "src/pkg/b.py", "node_modules/dep/c.py", ".git/hooks/d.py", "src/__pycache__/e.py", "src/readme.md", ".hidden/f.py"] {
            touch(tmp.path(), file);
        }
        let walked = recursive_glob(tmp.path(), "**/*.py", false).unwrap();
        let mut found: Vec<String> = walked
            .matches
            .iter()
            .map(|p| p.strip_prefix(tmp.path()).unwrap().to_string_lossy().into_owned())
            .collect();
        found.sort();
        assert_eq!(found, ["src/a.py", "src/pkg/b.py", "top.py"]);
        assert!(!walked.truncated);
    }

    #[test]
    fn directories_only_when_asked() {
        let tmp = tempfile::TempDir::new().unwrap();
        touch(tmp.path(), "tests/unit/x.rs");
        let files = recursive_glob(tmp.path(), "**/unit", false).unwrap();
        assert!(files.matches.is_empty());
        let dirs = recursive_glob(tmp.path(), "**/unit", true).unwrap();
        assert_eq!(dirs.matches.len(), 1);
    }
}
