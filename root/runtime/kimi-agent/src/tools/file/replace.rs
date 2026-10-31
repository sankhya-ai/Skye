use schemars::{JsonSchema, Schema, SchemaGenerator};
use serde::{Deserialize, Deserializer};
use serde_json::Value;

use kaos::KaosPath;
use kosong::tooling::{CallableTool2, DisplayBlock, ToolReturnValue, tool_error};

use crate::soul::agent::Runtime;
use crate::tools::utils::tool_rejected_error;
use crate::utils::{build_diff_blocks, is_within_directory};

use super::tolerant;
use super::{
    not_found,
    FILE_ACTION_EDIT, FILE_ACTION_EDIT_OUTSIDE, REPLACE_DESC, read_text_lossy, resolve_tool_path,
    validate_absolute_path,
};

const REPLACE_MISS_CONTEXT_LINES: usize = 6;
const REPLACE_MISS_PREVIEW_MAX_LINES: usize = 80;

#[derive(Debug, Deserialize, JsonSchema)]
pub struct EditParams {
    #[schemars(description = "The old string to replace. Can be multi-line.")]
    pub old: String,
    #[schemars(description = "The new string to replace with. Can be multi-line.")]
    pub new: String,
    #[serde(default)]
    #[schemars(description = "Whether to replace all occurrences.", default)]
    pub replace_all: bool,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct StrReplaceParams {
    #[schemars(
        description = "The path to the file to edit. Relative paths resolve against the working directory; workspace-scoped runtimes reject paths outside that workspace."
    )]
    pub path: String,
    #[serde(deserialize_with = "deserialize_edit_list")]
    #[schemars(schema_with = "edit_schema")]
    pub edit: Vec<EditParams>,
}

fn deserialize_edit_list<'de, D>(deserializer: D) -> Result<Vec<EditParams>, D::Error>
where
    D: Deserializer<'de>,
{
    let value = Value::deserialize(deserializer)?;
    if value.is_array() {
        serde_json::from_value(value).map_err(serde::de::Error::custom)
    } else {
        let single: EditParams = serde_json::from_value(value).map_err(serde::de::Error::custom)?;
        Ok(vec![single])
    }
}

fn edit_schema(schema_gen: &mut SchemaGenerator) -> Schema {
    let edit_schema = EditParams::json_schema(schema_gen);
    let list_schema = Vec::<EditParams>::json_schema(schema_gen);
    let mut map = serde_json::Map::new();
    map.insert(
        "anyOf".to_string(),
        Value::Array(vec![
            serde_json::to_value(&edit_schema).unwrap_or(Value::Null),
            serde_json::to_value(&list_schema).unwrap_or(Value::Null),
        ]),
    );
    map.insert(
        "description".to_string(),
        Value::String(
            "The edit(s) to apply to the file. You can provide a single edit or a list of edits here.".to_string(),
        ),
    );
    Schema::from(map)
}

pub struct StrReplaceFile {
    description: String,
    work_dir: KaosPath,
    approval: std::sync::Arc<crate::soul::approval::Approval>,
}

impl StrReplaceFile {
    pub fn new(runtime: &Runtime) -> Self {
        Self {
            description: REPLACE_DESC.to_string(),
            work_dir: runtime.builtin_args.KIMI_WORK_DIR.clone(),
            approval: runtime.approval.clone(),
        }
    }
}

#[async_trait::async_trait]
impl CallableTool2 for StrReplaceFile {
    type Params = StrReplaceParams;

    fn name(&self) -> &str {
        "StrReplaceFile"
    }

    fn description(&self) -> &str {
        &self.description
    }

    async fn call_typed(&self, params: Self::Params) -> ToolReturnValue {
        if params.path.is_empty() {
            return tool_error("", "File path cannot be empty.", "Empty file path");
        }

        let mut path = KaosPath::new(params.path.as_str()).expanduser();
        if let Some(err) = validate_absolute_path(&path, &self.work_dir, "edit") {
            return err;
        }
        path = resolve_tool_path(&path, &self.work_dir);

        if !path.exists(true).await {
            return tool_error(
                "",
                not_found(&params.path, path.as_path(), self.work_dir.as_path()),
                "File not found",
            );
        }
        if !path.is_file(true).await {
            return tool_error(
                "",
                format!("`{}` is not a file.", params.path),
                "Invalid path",
            );
        }

        let original = match read_text_lossy(&path).await {
            Ok(text) => text,
            Err(err) => {
                return tool_error(
                    "",
                    format!("Failed to edit. Error: {err}"),
                    "Failed to edit file",
                );
            }
        };

        let mut content = original.clone();
        let mut notes: Vec<String> = Vec::new();
        for (index, edit) in params.edit.iter().enumerate() {
            if edit.old.is_empty() {
                return tool_error("", "Edit old string cannot be empty.", "Invalid edit");
            }
            if !content.contains(&edit.old) {
                if let Some(found) = tolerant::replace(&content, &edit.old, &edit.new) {
                    notes.push(format!(
                        "Edit {} did not match exactly; it was applied to lines {}-{}, ignoring whitespace differences{}. Re-read the file before editing those lines again.",
                        index + 1,
                        found.first_line,
                        found.last_line,
                        if found.reindented { ", and the new text was re-indented to the file's indentation" } else { "" }
                    ));
                    content = found.content;
                    continue;
                }
                return tool_error(
                    replacement_miss_preview(&original, &edit.old),
                    format!(
                        "No replacements were made. Edit {} old string was not found in the current file. The returned excerpt is current file evidence for the next edit.",
                        index + 1
                    ),
                    "No replacements made",
                );
            }
            if edit.replace_all {
                content = content.replace(&edit.old, &edit.new);
            } else {
                content = content.replacen(&edit.old, &edit.new, 1);
            }
        }

        if content == original {
            return tool_error(
                "",
                "No replacements were made. The old string was not found in the file.",
                "No replacements made",
            );
        }

        let diff_blocks: Vec<DisplayBlock> =
            build_diff_blocks(&path.to_string_lossy(), &original, &content)
                .into_iter()
                .map(DisplayBlock::Diff)
                .collect();

        let action = if is_within_directory(&path, &self.work_dir) {
            FILE_ACTION_EDIT
        } else {
            FILE_ACTION_EDIT_OUTSIDE
        };

        let approved = match self
            .approval
            .request(
                self.name(),
                action,
                &format!("Edit file `{}`", path),
                Some(diff_blocks.clone()),
            )
            .await
        {
            Ok(value) => value,
            Err(_) => false,
        };
        if !approved {
            return tool_rejected_error();
        }

        if let Err(err) = path.write_text(&content).await {
            return tool_error(
                "",
                format!("Failed to edit {}. Error: {err}", params.path),
                "Failed to edit file",
            );
        }

        ToolReturnValue {
            is_error: false,
            output: Default::default(),
            message: if notes.is_empty() {
                "File successfully edited.".to_string()
            } else {
                format!("File successfully edited. {}", notes.join(" "))
            },
            display: diff_blocks,
            extras: None,
        }
    }
}

fn replacement_miss_preview(original: &str, missing_old: &str) -> String {
    let lines: Vec<&str> = original.lines().collect();
    if lines.is_empty() {
        return String::new();
    }
    let anchor = replacement_miss_anchor_line(&lines, missing_old).unwrap_or(0);
    let start = anchor.saturating_sub(REPLACE_MISS_CONTEXT_LINES);
    let end = (start + REPLACE_MISS_PREVIEW_MAX_LINES).min(lines.len());
    let mut out = Vec::new();
    for (offset, line) in lines[start..end].iter().enumerate() {
        out.push(format!("{:6}\t{}", start + offset + 1, line));
    }
    out.join("\n")
}

fn replacement_miss_anchor_line(lines: &[&str], missing_old: &str) -> Option<usize> {
    for candidate in missing_old
        .lines()
        .map(str::trim)
        .filter(|line| line.len() >= 4)
    {
        if let Some(index) = lines.iter().position(|line| line.contains(candidate)) {
            return Some(index);
        }
    }
    for token in missing_old
        .split(|ch: char| !ch.is_alphanumeric() && ch != '_' && ch != '-')
        .filter(|token| token.len() >= 4)
    {
        if let Some(index) = lines.iter().position(|line| line.contains(token)) {
            return Some(index);
        }
    }
    None
}
