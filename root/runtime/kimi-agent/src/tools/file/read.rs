use std::collections::HashMap;
use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};
use std::sync::Mutex;

use futures::StreamExt;
use kaos::KaosPath;
use schemars::JsonSchema;
use serde::Deserialize;

use kosong::tooling::error::tool_validate_error;
use kosong::tooling::{CallableTool2, ToolReturnValue, tool_error, tool_ok};

use crate::soul::agent::Runtime;
use crate::tools::utils::{load_desc, truncate_line};
use crate::utils::list_directory_relative_to;

use super::{
    not_found, approve_outside_read,
    FileKind, MAX_BYTES, MAX_LINE_LENGTH, MAX_LINES, MEDIA_SNIFF_BYTES, READ_DESC,
    detect_file_type, resolve_tool_path, validate_absolute_path,
};

#[derive(Debug, Deserialize, JsonSchema)]
pub struct ReadParams {
    #[schemars(
        description = "The path to the file or directory to read. Relative paths resolve against the working directory; workspace-scoped runtimes reject paths outside that workspace."
    )]
    pub path: String,
    #[serde(default = "default_line_offset")]
    #[schemars(
        description = "The line number to start reading from. By default read from the beginning of the file. Set this when the file is too large to read at once.",
        range(min = 1),
        default = "default_line_offset"
    )]
    pub line_offset: i64,
    #[serde(default = "default_n_lines")]
    #[schemars(
        description = "The number of lines to read. By default read up to 1000 lines, which is the max allowed value. Set this value when the file is too large to read at once.",
        range(min = 1),
        default = "default_n_lines"
    )]
    pub n_lines: i64,
}

fn default_line_offset() -> i64 {
    1
}

fn default_n_lines() -> i64 {
    MAX_LINES as i64
}

const DIRECTORY_READ_GUIDANCE: &str = concat!(
    "is a directory. Directory entries are listed below. ",
    "Use ReadFile with an exact file path, or Glob/Grep to search within the working directory."
);

const REPEATED_READ_GUIDANCE: &str = concat!(
    "This exact path/range was already read and is unchanged. ",
    "Use the prior content, edit the file, or read a different path/range."
);

#[derive(Clone, Debug, Hash, PartialEq, Eq)]
struct ReadHistoryKey {
    path: String,
    line_offset: i64,
    n_lines: i64,
}

#[derive(Clone, Debug)]
struct ReadHistoryValue {
    output_hash: u64,
    repeat_count: usize,
}

pub struct ReadFile {
    description: String,
    work_dir: KaosPath,
    approval: std::sync::Arc<crate::soul::approval::Approval>,
    read_history: Mutex<HashMap<ReadHistoryKey, ReadHistoryValue>>,
}

impl ReadFile {
    pub fn new(runtime: &Runtime) -> Self {
        let desc = load_desc(
            READ_DESC,
            &[
                ("MAX_LINES", MAX_LINES.to_string()),
                ("MAX_LINE_LENGTH", MAX_LINE_LENGTH.to_string()),
                ("MAX_BYTES", MAX_BYTES.to_string()),
            ],
        );
        Self {
            description: desc,
            work_dir: runtime.builtin_args.KIMI_WORK_DIR.clone(),
            approval: runtime.approval.clone(),
            read_history: Mutex::new(HashMap::new()),
        }
    }

    fn repeated_read_response(
        &self,
        path: &KaosPath,
        line_offset: i64,
        n_lines: i64,
        output: &str,
    ) -> Option<ToolReturnValue> {
        let key = ReadHistoryKey {
            path: path.to_string_lossy(),
            line_offset,
            n_lines,
        };
        let output_hash = stable_hash(output);
        let mut history = self.read_history.lock().expect("read history lock");
        match history.get_mut(&key) {
            Some(value) if value.output_hash == output_hash => {
                value.repeat_count += 1;
                Some(tool_ok(
                    "",
                    format!("{REPEATED_READ_GUIDANCE} Repeat #{}.", value.repeat_count),
                    "Repeated read suppressed",
                ))
            }
            Some(value) => {
                value.output_hash = output_hash;
                value.repeat_count = 0;
                None
            }
            None => {
                history.insert(
                    key,
                    ReadHistoryValue {
                        output_hash,
                        repeat_count: 0,
                    },
                );
                None
            }
        }
    }
}

fn stable_hash(value: &str) -> u64 {
    let mut hasher = DefaultHasher::new();
    value.hash(&mut hasher);
    hasher.finish()
}

#[async_trait::async_trait]
impl CallableTool2 for ReadFile {
    type Params = ReadParams;

    fn name(&self) -> &str {
        "ReadFile"
    }

    fn description(&self) -> &str {
        &self.description
    }

    async fn call_typed(&self, params: Self::Params) -> ToolReturnValue {
        if params.line_offset < 1 {
            return tool_validate_error("line_offset must be >= 1");
        }
        if params.n_lines < 1 {
            return tool_validate_error("n_lines must be >= 1");
        }
        if params.path.is_empty() {
            return tool_error("", "File path cannot be empty.", "Empty file path");
        }

        let mut path = KaosPath::new(params.path.as_str()).expanduser();
        if let Some(err) = validate_absolute_path(&path, &self.work_dir, "read") {
            return err;
        }
        path = resolve_tool_path(&path, &self.work_dir);
        if let Some(refused) = approve_outside_read(&self.approval, "ReadFile", &path, &self.work_dir).await {
            return refused;
        }

        if !path.exists(true).await {
            return tool_error(
                "",
                not_found(&params.path, path.as_path(), self.work_dir.as_path()),
                "File not found",
            );
        }
        if path.is_dir(true).await {
            let output = list_directory_relative_to(&path, &self.work_dir).await;
            if let Some(repeated) =
                self.repeated_read_response(&path, params.line_offset, params.n_lines, &output)
            {
                return repeated;
            }
            return tool_ok(
                output,
                format!("`{}` {DIRECTORY_READ_GUIDANCE}", params.path),
                "Directory listing",
            );
        }
        if !path.is_file(true).await {
            return tool_error(
                "",
                format!("`{}` is not a file.", params.path),
                "Invalid path",
            );
        }

        let header = match path.read_bytes(Some(MEDIA_SNIFF_BYTES)).await {
            Ok(bytes) => bytes,
            Err(err) => {
                return tool_error(
                    "",
                    format!("Failed to read {}. Error: {err}", params.path),
                    "Failed to read file",
                );
            }
        };
        let file_type = detect_file_type(&path.to_string_lossy(), Some(&header));
        match file_type.kind {
            FileKind::Image => {
                return tool_error(
                    "",
                    format!(
                        "`{}` is a image file. Use other appropriate tools to read image or video files.",
                        params.path
                    ),
                    "Unsupported file type",
                );
            }
            FileKind::Video => {
                return tool_error(
                    "",
                    format!(
                        "`{}` is a video file. Use other appropriate tools to read image or video files.",
                        params.path
                    ),
                    "Unsupported file type",
                );
            }
            FileKind::Unknown => {
                return tool_error(
                    "",
                    format!(
                        "`{}` seems not readable. You may need to read it with proper shell commands, Python tools or MCP tools if available. If you read/operate it with Python, you MUST ensure that any third-party packages are installed in a virtual environment (venv).",
                        params.path
                    ),
                    "File not readable",
                );
            }
            FileKind::Text => {}
        }

        let mut lines = Vec::new();
        let mut truncated_lines = Vec::new();
        let mut n_bytes = 0usize;
        let mut max_lines_reached = false;
        let mut max_bytes_reached = false;
        let mut current_line = 0usize;

        let mut stream = match path.read_lines_stream().await {
            Ok(stream) => stream,
            Err(err) => {
                return tool_error(
                    "",
                    format!("Failed to read {}. Error: {err}", params.path),
                    "Failed to read file",
                );
            }
        };

        while let Some(line) = stream.next().await {
            let line = match line {
                Ok(line) => line,
                Err(err) => {
                    return tool_error(
                        "",
                        format!("Failed to read {}. Error: {err}", params.path),
                        "Failed to read file",
                    );
                }
            };
            if line.is_empty() {
                continue;
            }
            current_line += 1;
            if current_line < params.line_offset as usize {
                continue;
            }
            let truncated = truncate_line(&line, MAX_LINE_LENGTH, "...");
            if truncated != line {
                truncated_lines.push(current_line);
            }
            lines.push(truncated.clone());
            n_bytes += truncated.as_bytes().len();
            if lines.len() >= params.n_lines as usize {
                break;
            }
            if lines.len() >= MAX_LINES {
                max_lines_reached = true;
                break;
            }
            if n_bytes >= MAX_BYTES {
                max_bytes_reached = true;
                break;
            }
        }

        let mut numbered = Vec::new();
        for (idx, line) in lines.iter().enumerate() {
            let line_no = params.line_offset as usize + idx;
            numbered.push(format!("{line_no:6}\t{line}"));
        }

        let mut message = if lines.is_empty() {
            "No lines read from file.".to_string()
        } else {
            format!(
                "{} lines read from file starting from line {}.",
                lines.len(),
                params.line_offset
            )
        };

        if max_lines_reached {
            message.push_str(&format!(" Max {MAX_LINES} lines reached."));
        } else if max_bytes_reached {
            message.push_str(&format!(" Max {MAX_BYTES} bytes reached."));
        } else if lines.len() < params.n_lines as usize {
            message.push_str(" End of file reached.");
        }
        if !truncated_lines.is_empty() {
            message.push_str(&format!(" Lines {:?} were truncated.", truncated_lines));
        }

        let output = numbered.join("");
        if let Some(repeated) =
            self.repeated_read_response(&path, params.line_offset, params.n_lines, &output)
        {
            return repeated;
        }

        tool_ok(output, message, "")
    }
}
