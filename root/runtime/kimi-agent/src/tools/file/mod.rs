use std::path::Path;

use anyhow::Result;
use kaos::KaosPath;
use kosong::tooling::{ToolReturnValue, tool_error};

use crate::utils::is_within_directory;

mod glob;
mod grep;
mod read;
mod read_media;
mod replace;
mod tolerant;
mod walk;
mod write;

pub use glob::{Glob, GlobParams};
pub use grep::{Grep, GrepParams};
pub use read::{ReadFile, ReadParams};
pub use read_media::{ReadMediaFile, ReadMediaParams};
pub use replace::{EditParams, StrReplaceFile, StrReplaceParams};
pub use write::{WriteFile, WriteMode, WriteParams};

pub const MAX_LINES: usize = 1000;
pub const MAX_LINE_LENGTH: usize = 2000;
pub const MAX_BYTES: usize = 100 << 10;
pub const MAX_MEDIA_MEGABYTES: usize = 100;
pub const MAX_MATCHES: usize = 1000;
pub(super) const MEDIA_SNIFF_BYTES: usize = 512;

pub(super) const FILE_ACTION_EDIT: &str = "edit file";
pub(super) const FILE_ACTION_EDIT_OUTSIDE: &str = "edit file outside of working directory";

pub(super) const READ_DESC: &str = include_str!("../desc/file/read.md");
pub(super) const READ_MEDIA_DESC: &str = include_str!("../desc/file/read_media.md");
pub(super) const GLOB_DESC: &str = include_str!("../desc/file/glob.md");
pub(super) const GREP_DESC: &str = include_str!("../desc/file/grep.md");
pub(super) const WRITE_DESC: &str = include_str!("../desc/file/write.md");
pub(super) const REPLACE_DESC: &str = include_str!("../desc/file/replace.md");

#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) enum FileKind {
    Text,
    Image,
    Video,
    Unknown,
}

#[cfg(test)]
mod tests {
    use super::{FileKind, detect_file_type, not_found, repair_path};

    #[test]
    fn a_missing_file_names_where_files_of_that_name_are() {
        let ws = tempfile::tempdir().unwrap();
        let ws = ws.path().canonicalize().unwrap();
        std::fs::create_dir_all(ws.join("app/docs")).unwrap();
        std::fs::create_dir_all(ws.join("node_modules/x")).unwrap();
        std::fs::write(ws.join("app/DESIGN.md"), "x").unwrap();
        std::fs::write(ws.join("node_modules/x/DESIGN.md"), "x").unwrap();
        let message = not_found("DESIGN.md", &ws.join("DESIGN.md"), &ws);
        assert_eq!(message, "`DESIGN.md` does not exist. Did you mean `app/DESIGN.md`? (relative to the working directory)");
        assert_eq!(not_found("nope.md", &ws.join("nope.md"), &ws), "`nope.md` does not exist.");
    }

    #[test]
    fn a_doubled_workspace_or_a_url_suffix_is_put_right_only_when_it_helps() {
        let ws = tempfile::tempdir().unwrap();
        let ws = ws.path().canonicalize().unwrap();
        std::fs::create_dir_all(ws.join("app")).unwrap();
        std::fs::write(ws.join("app/x.py"), "x").unwrap();
        let bare = ws.to_string_lossy().trim_start_matches('/').to_string();

        let doubled = ws.join(&bare).join("app/x.py");
        assert_eq!(repair_path(&doubled, &ws), Some(ws.join("app/x.py")));
        // A new file in a real directory, written through a doubled path.
        let new_file = ws.join(&bare).join("app/new.py");
        assert_eq!(repair_path(&new_file, &ws), Some(ws.join("app/new.py")));
        assert_eq!(repair_path(&ws.join("app/x.py?after"), &ws), Some(ws.join("app/x.py")));
        assert_eq!(repair_path(&ws.join("app/x.py#L3"), &ws), Some(ws.join("app/x.py")));

        // Real paths, and misses with nothing to land on, are left alone.
        assert_eq!(repair_path(&ws.join("app/x.py"), &ws), None);
        assert_eq!(repair_path(&ws.join("app/missing.py"), &ws), None);
        assert_eq!(repair_path(&ws.join("app/missing.py?x"), &ws), None);
    }

    fn kind_name(kind: &FileKind) -> &'static str {
        match kind {
            FileKind::Text => "text",
            FileKind::Image => "image",
            FileKind::Video => "video",
            FileKind::Unknown => "unknown",
        }
    }

    #[test]
    fn test_detect_file_type_suffixes() {
        assert_eq!(
            kind_name(&detect_file_type("image.PNG", None).kind),
            "image"
        );
        assert_eq!(kind_name(&detect_file_type("clip.mp4", None).kind), "video");
        assert_eq!(kind_name(&detect_file_type("notes.txt", None).kind), "text");
        assert_eq!(kind_name(&detect_file_type("Makefile", None).kind), "text");
        assert_eq!(kind_name(&detect_file_type(".env", None).kind), "text");
        assert_eq!(kind_name(&detect_file_type("icon.svg", None).kind), "text");
        assert_eq!(
            kind_name(&detect_file_type("archive.tar.gz", None).kind),
            "unknown"
        );
        assert_eq!(
            kind_name(&detect_file_type("my file.pdf", None).kind),
            "unknown"
        );
        assert_eq!(kind_name(&detect_file_type("app.ts", None).kind), "text");
        assert_eq!(
            kind_name(&detect_file_type("component.tsx", None).kind),
            "text"
        );
        assert_eq!(
            kind_name(&detect_file_type("module.mts", None).kind),
            "text"
        );
        assert_eq!(
            kind_name(&detect_file_type("common.cts", None).kind),
            "text"
        );
    }

    #[test]
    fn test_detect_file_type_header_overrides() {
        let png_header = b"\x89PNG\r\n\x1a\npngdata";
        let mp4_header = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom";
        let iso5_header = b"\x00\x00\x00\x18ftypiso5\x00\x00\x00\x00iso5isom";
        let binary_header = b"\x00\x00binary";

        assert_eq!(
            kind_name(&detect_file_type("sample", Some(png_header)).kind),
            "image"
        );
        assert_eq!(
            detect_file_type("sample.bin", Some(png_header)).mime_type,
            "image/png"
        );
        assert_eq!(
            kind_name(&detect_file_type("sample", Some(mp4_header)).kind),
            "video"
        );
        assert_eq!(
            kind_name(&detect_file_type("sample", Some(iso5_header)).kind),
            "video"
        );
        assert_eq!(
            kind_name(&detect_file_type("sample.png", Some(mp4_header)).kind),
            "image"
        );
        assert_eq!(
            kind_name(&detect_file_type("notes.txt", Some(binary_header)).kind),
            "unknown"
        );
    }
}

#[derive(Clone, Debug)]
pub(super) struct FileType {
    kind: FileKind,
    mime_type: String,
}

pub(super) fn detect_file_type(path: &str, header: Option<&[u8]>) -> FileType {
    let suffix = Path::new(path)
        .extension()
        .map(|s| format!(".{}", s.to_string_lossy().to_lowercase()));

    let mut media_hint: Option<FileType> = None;
    if let Some(ext) = suffix.as_deref() {
        if let Some(mime) = text_mime_by_suffix(ext) {
            media_hint = Some(FileType {
                kind: FileKind::Text,
                mime_type: mime.to_string(),
            });
        } else if let Some(mime) = image_mime_by_suffix(ext) {
            media_hint = Some(FileType {
                kind: FileKind::Image,
                mime_type: mime.to_string(),
            });
        } else if let Some(mime) = video_mime_by_suffix(ext) {
            media_hint = Some(FileType {
                kind: FileKind::Video,
                mime_type: mime.to_string(),
            });
        } else if let Some(mime) = extra_mime_type(ext) {
            if mime.starts_with("image/") {
                media_hint = Some(FileType {
                    kind: FileKind::Image,
                    mime_type: mime.to_string(),
                });
            } else if mime.starts_with("video/") {
                media_hint = Some(FileType {
                    kind: FileKind::Video,
                    mime_type: mime.to_string(),
                });
            }
        }
    }

    if let Some(hint) = &media_hint {
        if matches!(hint.kind, FileKind::Image | FileKind::Video) {
            return hint.clone();
        }
    }

    if let Some(bytes) = header {
        if let Some(sniffed) = sniff_media_from_magic(bytes) {
            if let Some(hint) = &media_hint {
                if hint.kind != sniffed.kind {
                    return FileType {
                        kind: FileKind::Unknown,
                        mime_type: String::new(),
                    };
                }
            }
            return sniffed;
        }
        if bytes.iter().any(|b| *b == 0) {
            return FileType {
                kind: FileKind::Unknown,
                mime_type: String::new(),
            };
        }
    }

    if let Some(hint) = media_hint {
        return hint;
    }

    if suffix.as_deref().map(is_non_text_suffix).unwrap_or(false) {
        return FileType {
            kind: FileKind::Unknown,
            mime_type: String::new(),
        };
    }

    FileType {
        kind: FileKind::Text,
        mime_type: "text/plain".to_string(),
    }
}

fn sniff_media_from_magic(data: &[u8]) -> Option<FileType> {
    let header = &data[..data.len().min(MEDIA_SNIFF_BYTES)];

    if header.starts_with(b"\x89PNG\r\n\x1a\n") {
        return Some(FileType {
            kind: FileKind::Image,
            mime_type: "image/png".to_string(),
        });
    }
    if header.starts_with(b"\xff\xd8\xff") {
        return Some(FileType {
            kind: FileKind::Image,
            mime_type: "image/jpeg".to_string(),
        });
    }
    if header.starts_with(b"GIF87a") || header.starts_with(b"GIF89a") {
        return Some(FileType {
            kind: FileKind::Image,
            mime_type: "image/gif".to_string(),
        });
    }
    if header.starts_with(b"BM") {
        return Some(FileType {
            kind: FileKind::Image,
            mime_type: "image/bmp".to_string(),
        });
    }
    if header.starts_with(b"II*\x00") || header.starts_with(b"MM\x00*") {
        return Some(FileType {
            kind: FileKind::Image,
            mime_type: "image/tiff".to_string(),
        });
    }
    if header.starts_with(b"\x00\x00\x01\x00") {
        return Some(FileType {
            kind: FileKind::Image,
            mime_type: "image/x-icon".to_string(),
        });
    }
    if header.starts_with(b"RIFF") && header.len() >= 12 {
        let chunk = &header[8..12];
        if chunk == b"WEBP" {
            return Some(FileType {
                kind: FileKind::Image,
                mime_type: "image/webp".to_string(),
            });
        }
        if chunk == b"AVI " {
            return Some(FileType {
                kind: FileKind::Video,
                mime_type: "video/x-msvideo".to_string(),
            });
        }
    }
    if header.starts_with(b"FLV") {
        return Some(FileType {
            kind: FileKind::Video,
            mime_type: "video/x-flv".to_string(),
        });
    }
    if header.starts_with(b"\x30\x26\xb2\x75\x8e\x66\xcf\x11\xa6\xd9\x00\xaa\x00\x62\xce\x6c") {
        return Some(FileType {
            kind: FileKind::Video,
            mime_type: "video/x-ms-wmv".to_string(),
        });
    }
    if header.starts_with(b"\x1a\x45\xdf\xa3") {
        let lowered = header.to_ascii_lowercase();
        if lowered.windows(4).any(|w| w == b"webm") {
            return Some(FileType {
                kind: FileKind::Video,
                mime_type: "video/webm".to_string(),
            });
        }
        if lowered.windows(8).any(|w| w == b"matroska") {
            return Some(FileType {
                kind: FileKind::Video,
                mime_type: "video/x-matroska".to_string(),
            });
        }
    }
    if let Some(brand) = sniff_ftyp_brand(header) {
        if let Some(mime) = ftyp_image_brand(&brand) {
            return Some(FileType {
                kind: FileKind::Image,
                mime_type: mime.to_string(),
            });
        }
        if let Some(mime) = ftyp_video_brand(&brand) {
            return Some(FileType {
                kind: FileKind::Video,
                mime_type: mime.to_string(),
            });
        }
    }

    None
}

fn sniff_ftyp_brand(header: &[u8]) -> Option<String> {
    if header.len() < 12 || &header[4..8] != b"ftyp" {
        return None;
    }
    let brand = String::from_utf8_lossy(&header[8..12]).to_lowercase();
    Some(brand.trim().to_string())
}

fn text_mime_by_suffix(suffix: &str) -> Option<&'static str> {
    match suffix {
        ".svg" => Some("image/svg+xml"),
        _ => None,
    }
}

fn image_mime_by_suffix(suffix: &str) -> Option<&'static str> {
    match suffix {
        ".png" => Some("image/png"),
        ".jpg" => Some("image/jpeg"),
        ".jpeg" => Some("image/jpeg"),
        ".gif" => Some("image/gif"),
        ".bmp" => Some("image/bmp"),
        ".tif" => Some("image/tiff"),
        ".tiff" => Some("image/tiff"),
        ".webp" => Some("image/webp"),
        ".ico" => Some("image/x-icon"),
        ".heic" => Some("image/heic"),
        ".heif" => Some("image/heif"),
        ".avif" => Some("image/avif"),
        ".svgz" => Some("image/svg+xml"),
        _ => None,
    }
}

fn video_mime_by_suffix(suffix: &str) -> Option<&'static str> {
    match suffix {
        ".mp4" => Some("video/mp4"),
        ".mkv" => Some("video/x-matroska"),
        ".avi" => Some("video/x-msvideo"),
        ".mov" => Some("video/quicktime"),
        ".wmv" => Some("video/x-ms-wmv"),
        ".webm" => Some("video/webm"),
        ".m4v" => Some("video/x-m4v"),
        ".flv" => Some("video/x-flv"),
        ".3gp" => Some("video/3gpp"),
        ".3g2" => Some("video/3gpp2"),
        _ => None,
    }
}

fn extra_mime_type(suffix: &str) -> Option<&'static str> {
    match suffix {
        ".avif" => Some("image/avif"),
        ".heic" => Some("image/heic"),
        ".heif" => Some("image/heif"),
        ".mkv" => Some("video/x-matroska"),
        ".m4v" => Some("video/x-m4v"),
        ".3gp" => Some("video/3gpp"),
        ".3g2" => Some("video/3gpp2"),
        ".ts" => Some("text/typescript"),
        ".tsx" => Some("text/typescript"),
        ".mts" => Some("text/typescript"),
        ".cts" => Some("text/typescript"),
        _ => None,
    }
}

fn ftyp_image_brand(brand: &str) -> Option<&'static str> {
    match brand {
        "avif" | "avis" => Some("image/avif"),
        "heic" | "hevc" => Some("image/heic"),
        "heif" | "heix" | "mif1" | "msf1" => Some("image/heif"),
        _ => None,
    }
}

fn ftyp_video_brand(brand: &str) -> Option<&'static str> {
    match brand {
        "isom" | "iso2" | "iso5" | "mp41" | "mp42" | "avc1" | "mp4v" => Some("video/mp4"),
        "m4v" => Some("video/x-m4v"),
        "qt" => Some("video/quicktime"),
        "3gp4" | "3gp5" | "3gp6" | "3gp7" => Some("video/3gpp"),
        "3g2" => Some("video/3gpp2"),
        _ => None,
    }
}

fn is_non_text_suffix(suffix: &str) -> bool {
    matches!(
        suffix,
        ".icns"
            | ".psd"
            | ".ai"
            | ".eps"
            | ".pdf"
            | ".doc"
            | ".docx"
            | ".dot"
            | ".dotx"
            | ".rtf"
            | ".odt"
            | ".xls"
            | ".xlsx"
            | ".xlsm"
            | ".xlt"
            | ".xltx"
            | ".xltm"
            | ".ods"
            | ".ppt"
            | ".pptx"
            | ".pptm"
            | ".pps"
            | ".ppsx"
            | ".odp"
            | ".pages"
            | ".numbers"
            | ".key"
            | ".zip"
            | ".rar"
            | ".7z"
            | ".tar"
            | ".gz"
            | ".tgz"
            | ".bz2"
            | ".xz"
            | ".zst"
            | ".lz"
            | ".lz4"
            | ".br"
            | ".cab"
            | ".ar"
            | ".deb"
            | ".rpm"
            | ".mp3"
            | ".wav"
            | ".flac"
            | ".ogg"
            | ".oga"
            | ".opus"
            | ".aac"
            | ".m4a"
            | ".wma"
            | ".ttf"
            | ".otf"
            | ".woff"
            | ".woff2"
            | ".exe"
            | ".dll"
            | ".so"
            | ".dylib"
            | ".bin"
            | ".apk"
            | ".ipa"
            | ".jar"
            | ".class"
            | ".pyc"
            | ".pyo"
            | ".wasm"
            | ".dmg"
            | ".iso"
            | ".img"
            | ".sqlite"
            | ".sqlite3"
            | ".db"
            | ".db3"
    )
}

pub(super) fn validate_absolute_path(
    path: &KaosPath,
    work_dir: &KaosPath,
    action: &str,
) -> Option<ToolReturnValue> {
    let resolved = resolve_tool_path(path, work_dir);
    if workspace_scoped_file_tools() && !is_within_directory(&resolved, work_dir) {
        return Some(tool_error(
            "",
            format!(
                "`{}` resolves outside the runtime workspace. Workspace-scoped file tools are restricted to `{}`; use a path inside the workspace.",
                path, work_dir
            ),
            "Path outside runtime workspace",
        ));
    }
    if !is_within_directory(&resolved, work_dir) && !path.is_absolute() {
        return Some(tool_error(
            "",
            format!(
                "`{}` is not an absolute path. You must provide an absolute path to {action} a file outside the working directory.",
                path
            ),
            "Invalid path",
        ));
    }
    None
}

pub(super) const FILE_ACTION_READ_OUTSIDE: &str = "read file outside of working directory";

/// With `ROOT_READ_BOUNDARY` set (root; see VENDORED.md), reading outside the
/// working directory asks, the way editing there does, and the host decides —
/// in every permission mode. `None` means go ahead; `Some` is the refusal.
pub(super) async fn approve_outside_read(
    approval: &crate::soul::approval::Approval,
    tool: &str,
    path: &KaosPath,
    work_dir: &KaosPath,
) -> Option<ToolReturnValue> {
    if !env_truthy("ROOT_READ_BOUNDARY") || is_within_directory(&path.canonical(), work_dir) {
        return None;
    }
    let approved = approval
        .request(tool, FILE_ACTION_READ_OUTSIDE, &format!("Read `{}`", path), None)
        .await
        .unwrap_or(false);
    if approved { None } else { Some(crate::tools::utils::tool_rejected_error()) }
}

pub(super) fn workspace_scoped_file_tools() -> bool {
    env_truthy("CHOTU_WORKSPACE_SCOPED_FILE_TOOLS")
}

fn env_truthy(name: &str) -> bool {
    std::env::var(name)
        .map(|value| {
            matches!(
                value.trim().to_ascii_lowercase().as_str(),
                "1" | "true" | "yes" | "on"
            )
        })
        .unwrap_or(false)
}

pub(super) fn resolve_tool_path(path: &KaosPath, work_dir: &KaosPath) -> KaosPath {
    let resolved = if path.is_absolute() {
        path.canonical()
    } else {
        KaosPath::from(work_dir.as_path().join(path.as_path())).canonical()
    };
    match repair_path(resolved.as_path(), work_dir.as_path()) {
        Some(repaired) => KaosPath::from(repaired),
        None => resolved,
    }
}

/// "`x` does not exist." — and, when files of that name exist elsewhere in the
/// workspace, where they are, so a wrong directory costs one step, not a hunt.
pub(super) fn not_found(requested: &str, resolved: &std::path::Path, work_dir: &std::path::Path) -> String {
    let mut message = format!("`{requested}` does not exist.");
    let Some(name) = resolved.file_name().and_then(|n| n.to_str()) else {
        return message;
    };
    let found = walk::find_by_name(work_dir, name, 5);
    if !found.is_empty() {
        let listed = found
            .iter()
            .map(|p| format!("`{}`", p.strip_prefix(work_dir).unwrap_or(p).display()))
            .collect::<Vec<_>>()
            .join(", ");
        message.push_str(&format!(" Did you mean {listed}? (relative to the working directory)"));
    }
    message
}

/// A path a weaker model mangled, put right — only when the path as written
/// does not exist and the repair lands on something that does (or, for a new
/// file, in a directory that does). Two shapes seen in practice (root; see
/// VENDORED.md): the workspace path written twice (`<ws>/<ws>/app/x.py`, from
/// joining an absolute path onto the workspace), and a stray URL-ish suffix
/// (`DESIGN.md?after`, `x.py#L10`).
pub(super) fn repair_path(resolved: &std::path::Path, work_dir: &std::path::Path) -> Option<std::path::PathBuf> {
    if resolved.exists() {
        return None;
    }
    let usable = |candidate: &std::path::Path| {
        candidate != resolved
            && (candidate.exists()
                || (!resolved.parent().is_some_and(|p| p.exists())
                    && candidate.parent().is_some_and(|p| p.is_dir())))
    };
    let text = resolved.to_string_lossy();
    let root = work_dir.to_string_lossy();
    let bare = root.trim_start_matches('/');
    if !bare.is_empty() {
        if let Some(at) = text.rfind(bare) {
            if at > 1 {
                let tail = text[at + bare.len()..].trim_start_matches('/');
                let candidate = work_dir.join(tail);
                if usable(&candidate) {
                    return Some(candidate);
                }
            }
        }
    }
    let name = resolved.file_name()?.to_string_lossy();
    if let Some(cut) = name.find(['?', '#']).filter(|&i| i > 0) {
        let candidate = resolved.with_file_name(&name[..cut]);
        if candidate.exists() {
            return Some(candidate);
        }
    }
    None
}

pub(super) async fn read_text_lossy(path: &KaosPath) -> Result<String> {
    let bytes = path.read_bytes(None).await?;
    Ok(String::from_utf8_lossy(&bytes).to_string())
}
