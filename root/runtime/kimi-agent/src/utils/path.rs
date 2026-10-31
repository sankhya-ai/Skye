use std::path::{Path, PathBuf};

use kaos::KaosPath;

pub async fn next_available_rotation(path: &Path) -> Option<PathBuf> {
    let parent = path.parent()?;
    if tokio::fs::metadata(parent).await.is_err() {
        return None;
    }

    let base_name = path.file_stem()?.to_string_lossy().to_string();
    let suffix = path
        .extension()
        .map(|s| format!(".{}", s.to_string_lossy()))
        .unwrap_or_default();

    let mut max_num = 0u64;
    if let Ok(entries) = tokio::fs::read_dir(parent).await {
        let mut entries = entries;
        while let Ok(Some(entry)) = entries.next_entry().await {
            if let Some(name) = entry.file_name().to_str() {
                if let Some(num) = parse_rotation_suffix(name, &base_name, &suffix) {
                    if num > max_num {
                        max_num = num;
                    }
                }
            }
        }
    }

    let mut next_num = max_num + 1;
    loop {
        let candidate = parent.join(format!("{base_name}_{next_num}{suffix}"));
        if reserve_rotation_path(&candidate).await {
            return Some(candidate);
        }
        next_num += 1;
    }
}

async fn reserve_rotation_path(path: &Path) -> bool {
    let mut options = tokio::fs::OpenOptions::new();
    options.create_new(true).write(true);
    #[cfg(unix)]
    {
        options.mode(0o600);
    }
    match options.open(path).await {
        Ok(file) => {
            drop(file);
            true
        }
        Err(err) if err.kind() == std::io::ErrorKind::AlreadyExists => false,
        Err(_) => false,
    }
}

pub async fn list_directory(work_dir: &KaosPath) -> String {
    list_directory_relative_to(work_dir, work_dir).await
}

pub async fn list_directory_relative_to(work_dir: &KaosPath, relative_root: &KaosPath) -> String {
    let entries = match work_dir.iterdir().await {
        Ok(entries) => entries,
        Err(_) => return String::new(),
    };
    let mut lines = Vec::new();
    for entry in entries {
        let name = entry
            .relative_to(relative_root)
            .map(|path| path.to_string_lossy())
            .unwrap_or_else(|_| entry.name());
        match entry.stat(true).await {
            Ok(stat) => {
                let mode = format_mode(stat.st_mode);
                lines.push(format!("{mode} {:>10} {name}", stat.st_size));
            }
            Err(_) => {
                lines.push(format!("?--------- {:>10} {name} [stat failed]", "?"));
            }
        }
    }
    lines.join("\n")
}

pub fn shorten_home(path: &KaosPath) -> KaosPath {
    let home = KaosPath::home();
    if let Ok(relative) = path.relative_to(&home) {
        return KaosPath::new("~") / &relative;
    }
    path.clone()
}

/// Whether `path` is inside `directory`, by name AND by where it really points. A symlink inside the directory that
/// leads out of it is outside (ROOT local change; see VENDORED.md): the name check alone let a file tool read or write
/// through such a link without the host ever being asked. Non-existent tails are resolved through their nearest existing
/// parent; a path that cannot be resolved at all falls back to the name check.
pub fn is_within_directory(path: &KaosPath, directory: &KaosPath) -> bool {
    let path_str = path.to_string_lossy();
    let dir_str = directory.to_string_lossy();
    if Path::new(&path_str).strip_prefix(Path::new(&dir_str)).is_err() {
        return false;
    }
    match (real_path(Path::new(&path_str)), real_path(Path::new(&dir_str))) {
        (Some(real), Some(base)) => real.starts_with(base),
        _ => true,
    }
}

fn real_path(path: &Path) -> Option<std::path::PathBuf> {
    let mut tail = Vec::new();
    let mut current = path.to_path_buf();
    loop {
        match std::fs::canonicalize(&current) {
            Ok(real) => {
                return Some(tail.iter().rev().fold(real, |acc, part| acc.join(part)));
            }
            Err(_) => {
                let name = current.file_name()?.to_os_string();
                tail.push(name);
                current = current.parent()?.to_path_buf();
            }
        }
    }
}

fn parse_rotation_suffix(name: &str, base_name: &str, suffix: &str) -> Option<u64> {
    if !name.starts_with(base_name) || !name.ends_with(suffix) {
        return None;
    }
    let middle = &name[base_name.len()..name.len() - suffix.len()];
    let middle = middle.strip_prefix('_')?;
    middle.parse::<u64>().ok()
}

fn format_mode(mode: u32) -> String {
    let mut out = String::new();
    out.push(if mode & 0o040000 != 0 { 'd' } else { '-' });
    out.push(if mode & 0o400 != 0 { 'r' } else { '-' });
    out.push(if mode & 0o200 != 0 { 'w' } else { '-' });
    out.push(if mode & 0o100 != 0 { 'x' } else { '-' });
    out.push(if mode & 0o040 != 0 { 'r' } else { '-' });
    out.push(if mode & 0o020 != 0 { 'w' } else { '-' });
    out.push(if mode & 0o010 != 0 { 'x' } else { '-' });
    out.push(if mode & 0o004 != 0 { 'r' } else { '-' });
    out.push(if mode & 0o002 != 0 { 'w' } else { '-' });
    out.push(if mode & 0o001 != 0 { 'x' } else { '-' });
    out
}

#[cfg(test)]
mod symlink_boundary_tests {
    use super::*;

    #[test]
    fn a_symlink_leading_out_of_the_directory_is_outside_it() {
        let base = std::env::temp_dir().join(format!("root-symlink-{}", std::process::id()));
        let (ws, out) = (base.join("ws"), base.join("out"));
        std::fs::create_dir_all(&ws).unwrap();
        std::fs::create_dir_all(&out).unwrap();
        std::fs::write(out.join("secret.txt"), "s").unwrap();
        std::fs::write(ws.join("inside.txt"), "i").unwrap();
        std::os::unix::fs::symlink(out.join("secret.txt"), ws.join("link.txt")).unwrap();
        std::os::unix::fs::symlink(&out, ws.join("dirlink")).unwrap();
        let dir = KaosPath::from(ws.clone());
        assert!(is_within_directory(&KaosPath::from(ws.join("inside.txt")), &dir));
        assert!(is_within_directory(&KaosPath::from(ws.join("new/file.txt")), &dir), "a path that does not exist yet stays inside");
        assert!(!is_within_directory(&KaosPath::from(ws.join("link.txt")), &dir));
        assert!(!is_within_directory(&KaosPath::from(ws.join("dirlink/secret.txt")), &dir));
        assert!(!is_within_directory(&KaosPath::from(ws.join("dirlink/new.txt")), &dir), "writing a new file through a linked directory");
        let _ = std::fs::remove_dir_all(&base);
    }
}
