use kaos::{KaosPath, get_current_kaos};
use schemars::JsonSchema;
use serde::Deserialize;

use kosong::tooling::{CallableTool2, ToolReturnValue, tool_error, tool_ok};

use crate::soul::agent::Runtime;
use crate::tools::utils::load_desc;
use crate::utils::{is_within_directory, list_directory};

use super::walk;
use super::{GLOB_DESC, MAX_MATCHES, resolve_tool_path};

#[derive(Debug, Deserialize, JsonSchema)]
pub struct GlobParams {
    #[schemars(description = "Glob pattern to match files/directories.")]
    pub pattern: String,
    #[serde(default)]
    #[schemars(
        description = "Directory to search in. Defaults to the working directory. Relative paths resolve against the working directory."
    )]
    pub directory: Option<String>,
    #[serde(default = "default_include_dirs")]
    #[schemars(
        description = "Whether to include directories in results.",
        default = "default_include_dirs"
    )]
    pub include_dirs: bool,
}

fn default_include_dirs() -> bool {
    true
}

pub struct Glob {
    description: String,
    work_dir: KaosPath,
}

impl Glob {
    pub fn new(runtime: &Runtime) -> Self {
        let desc = load_desc(GLOB_DESC, &[("MAX_MATCHES", MAX_MATCHES.to_string())]);
        Self {
            description: desc,
            work_dir: runtime.builtin_args.KIMI_WORK_DIR.clone(),
        }
    }
}

#[async_trait::async_trait]
impl CallableTool2 for Glob {
    type Params = GlobParams;

    fn name(&self) -> &str {
        "Glob"
    }

    fn description(&self) -> &str {
        &self.description
    }

    async fn call_typed(&self, params: Self::Params) -> ToolReturnValue {
        let requested_dir = if let Some(directory) = params.directory.as_deref() {
            KaosPath::new(directory).expanduser()
        } else {
            self.work_dir.clone()
        };
        let dir = resolve_tool_path(&requested_dir, &self.work_dir);

        let resolved = dir.canonical();
        if !is_within_directory(&resolved, &self.work_dir) {
            return tool_error(
                "",
                format!(
                    "`{}` is outside the working directory. You can only search within the working directory.",
                    dir
                ),
                "Directory outside working directory",
            );
        }

        if !dir.exists(true).await {
            return tool_error(
                "",
                format!("`{}` does not exist.", params.directory.unwrap_or_default()),
                "Directory not found",
            );
        }
        if !dir.is_dir(true).await {
            return tool_error(
                "",
                format!(
                    "`{}` is not a directory.",
                    params.directory.unwrap_or_default()
                ),
                "Invalid directory",
            );
        }

        let recursive = params.pattern.starts_with("**");
        let local = get_current_kaos().name() == "local";
        if recursive && !local {
            let listing = list_directory(&self.work_dir).await;
            return tool_error(
                listing,
                format!(
                    "Pattern `{}` starts with '**', which is only supported on a local workspace. Use a more specific pattern; the top level of the working directory is listed below.",
                    params.pattern
                ),
                "Unsupported pattern",
            );
        }
        let mut walk_truncated = false;
        let matches = if recursive {
            // Pruned and bounded: never node_modules, .git, build output.
            let root = dir.as_path().to_path_buf();
            let pattern = params.pattern.clone();
            let include_dirs = params.include_dirs;
            match tokio::task::spawn_blocking(move || walk::recursive_glob(&root, &pattern, include_dirs)).await {
                Ok(Ok(walked)) => {
                    walk_truncated = walked.truncated;
                    walked.matches.iter().map(|p| KaosPath::unsafe_from_local_path(p)).collect::<Vec<_>>()
                }
                Ok(Err(err)) => {
                    return tool_error("", format!("Invalid pattern {}: {err}", params.pattern), "Glob failed");
                }
                Err(err) => {
                    return tool_error("", format!("Search failed: {err}"), "Glob failed");
                }
            }
        } else {
            match dir.glob(&params.pattern, true).await {
                Ok(matches) => matches,
                Err(err) => {
                    return tool_error(
                        "",
                        format!(
                            "Failed to search for pattern {}. Error: {err}",
                            params.pattern
                        ),
                        "Glob failed",
                    );
                }
            }
        };

        let mut filtered = Vec::new();
        for entry in matches {
            if params.include_dirs || entry.is_file(true).await {
                filtered.push(entry);
            }
        }
        filtered.sort_by(|a, b| a.to_string_lossy().cmp(&b.to_string_lossy()));

        let mut message = if filtered.is_empty() {
            format!("No matches found for pattern `{}`.", params.pattern)
        } else {
            format!(
                "Found {} matches for pattern `{}`.",
                filtered.len(),
                params.pattern
            )
        };

        if walk_truncated {
            message.push_str(" The search stopped early in a very large tree; narrow the pattern or set `directory`.");
        }
        if filtered.len() > MAX_MATCHES {
            filtered.truncate(MAX_MATCHES);
            message.push_str(&format!(
                " Only the first {MAX_MATCHES} matches are returned. You may want to use a more specific pattern."
            ));
        }

        let mut output_lines = Vec::new();
        for entry in filtered {
            if let Ok(relative) = entry.relative_to(&self.work_dir) {
                output_lines.push(relative.to_string_lossy());
            } else {
                output_lines.push(entry.to_string_lossy());
            }
        }

        tool_ok(output_lines.join("\n"), message, "")
    }
}
