//! Recovering tool-call arguments a model wrote as almost-JSON.
//!
//! Weaker models get the shape right and the syntax wrong: a code fence around
//! the object, a raw newline inside a `WriteFile` string, a trailing comma, an
//! object encoded twice as a string, or output cut off mid-argument. Rejecting
//! those costs a whole step, and a weak model often repeats the same mistake.
//!
//! Two kinds of repair, deliberately separated:
//!
//! * **Lossless** — fences, raw control characters in strings, trailing
//!   commas, double encoding. The recovered value is exactly what the model
//!   meant, so it is used for every tool.
//! * **Truncation** — closing an unterminated string or bracket. That can drop
//!   content (a half-written file would be written as if complete), so it is
//!   only used for read-only tools, whose worst case is reading the wrong thing.

use serde_json::{Value, json};

/// Tools whose arguments may be completed when the model's output was cut off.
const TRUNCATION_SAFE: &[&str] = &["ReadFile", "ReadMediaFile", "Glob", "Grep", "SearchWeb", "FetchURL"];

/// Parse tool arguments, repairing what can be repaired safely for this tool.
pub fn parse_tool_arguments(tool_name: &str, arguments: &str) -> Result<Value, String> {
    parse_value(tool_name, arguments).map(|value| reshape(tool_name, value))
}

/// Right values in the wrong place, moved where the schema wants them. Also
/// lossless: `StrReplaceFile` called as `{path, old, new}` (the flat shape most
/// edit tools use) means exactly one edit, and `edits` is the plural of `edit`.
fn reshape(tool_name: &str, value: Value) -> Value {
    let Value::Object(mut map) = value else {
        return value;
    };
    if tool_name == "StrReplaceFile" && !map.contains_key("edit") {
        if let Some(edits) = map.remove("edits") {
            map.insert("edit".to_string(), edits);
        } else if map.contains_key("old") && map.contains_key("new") {
            let mut edit = serde_json::Map::new();
            for key in ["old", "new", "replace_all"] {
                if let Some(field) = map.remove(key) {
                    edit.insert(key.to_string(), field);
                }
            }
            map.insert("edit".to_string(), Value::Object(edit));
        }
    }
    Value::Object(map)
}

fn parse_value(tool_name: &str, arguments: &str) -> Result<Value, String> {
    let error = match serde_json::from_str::<Value>(arguments) {
        Ok(Value::String(inner)) => match serde_json::from_str::<Value>(&inner) {
            Ok(value @ Value::Object(_)) => return Ok(value),
            _ => return Ok(Value::String(inner)),
        },
        Ok(value) => return Ok(value),
        Err(err) => err.to_string(),
    };
    let cleaned = lossless(arguments);
    if let Some(value) = parse_object(&cleaned) {
        return Ok(value);
    }
    if TRUNCATION_SAFE.contains(&tool_name) {
        if let Some(value) = parse_object(&close_truncated(&cleaned)) {
            return Ok(value);
        }
        if let Some(value) = builtin_fields(tool_name, arguments) {
            return Ok(value);
        }
    }
    if tool_name == "Shell" {
        // Only a complete command string: `extract_string` stops at the closing quote.
        if let Some(value) = builtin_fields(tool_name, arguments) {
            return Ok(value);
        }
    }
    Err(error)
}

fn parse_object(text: &str) -> Option<Value> {
    match serde_json::from_str::<Value>(text).ok()? {
        value @ Value::Object(_) => Some(value),
        Value::String(inner) => serde_json::from_str::<Value>(&inner).ok().filter(Value::is_object),
        _ => None,
    }
}

/// Fences off, control characters escaped inside strings, trailing commas dropped.
fn lossless(raw: &str) -> String {
    let mut text = raw.trim();
    if let Some(rest) = text.strip_prefix("```") {
        text = rest.trim_start_matches(|c: char| c.is_ascii_alphanumeric()).trim_start();
        text = text.strip_suffix("```").unwrap_or(text).trim_end();
    }
    let mut out = String::with_capacity(text.len() + 16);
    let mut in_string = false;
    let mut escaped = false;
    let chars: Vec<char> = text.chars().collect();
    let mut index = 0;
    while index < chars.len() {
        let ch = chars[index];
        if in_string {
            if escaped {
                escaped = false;
                out.push(ch);
            } else if ch == '\\' {
                escaped = true;
                out.push(ch);
            } else if ch == '"' {
                in_string = false;
                out.push(ch);
            } else if ch == '\n' {
                out.push_str("\\n");
            } else if ch == '\r' {
                out.push_str("\\r");
            } else if ch == '\t' {
                out.push_str("\\t");
            } else if (ch as u32) < 0x20 {
                out.push_str(&format!("\\u{:04x}", ch as u32));
            } else {
                out.push(ch);
            }
        } else if ch == '"' {
            in_string = true;
            out.push(ch);
        } else if ch == ',' {
            // Drop a comma whose next non-space character closes a container.
            let next = chars[index + 1..].iter().find(|c| !c.is_whitespace());
            if !matches!(next, Some('}') | Some(']')) {
                out.push(ch);
            }
        } else {
            out.push(ch);
        }
        index += 1;
    }
    out
}

/// Close an unterminated string and any open brackets, innermost first.
fn close_truncated(text: &str) -> String {
    let mut stack: Vec<char> = Vec::new();
    let mut in_string = false;
    let mut escaped = false;
    for ch in text.chars() {
        if in_string {
            if escaped {
                escaped = false;
            } else if ch == '\\' {
                escaped = true;
            } else if ch == '"' {
                in_string = false;
            }
            continue;
        }
        match ch {
            '"' => in_string = true,
            '{' => stack.push('}'),
            '[' => stack.push(']'),
            '}' | ']' => {
                stack.pop();
            }
            _ => {}
        }
    }
    let mut out = text.trim_end().trim_end_matches(',').to_string();
    if escaped {
        out.pop();
    }
    if in_string {
        out.push('"');
    }
    while let Some(close) = stack.pop() {
        out.push(close);
    }
    out
}

/// The one or two string fields a builtin tool needs, read straight from text.
/// `Shell` only takes a command whose string was closed: a cut-off command
/// is a different command.
fn builtin_fields(tool_name: &str, arguments: &str) -> Option<Value> {
    let closed_only = tool_name == "Shell";
    let field = |name: &str| extract_string(arguments, name).filter(|(_, closed)| *closed || !closed_only).map(|(text, _)| text);
    match tool_name {
        "ReadFile" | "ReadMediaFile" => Some(json!({ "path": field("path")? })),
        "Glob" => {
            let mut value = json!({ "pattern": field("pattern")? });
            if let Some(directory) = field("directory") {
                value["directory"] = json!(directory);
            }
            Some(value)
        }
        "Grep" => {
            let mut value = json!({ "pattern": field("pattern")? });
            for extra in ["path", "glob", "output_mode"] {
                if let Some(found) = field(extra) {
                    value[extra] = json!(found);
                }
            }
            Some(value)
        }
        "Shell" => Some(json!({ "command": field("command")? })),
        _ => None,
    }
}

/// The value of `"field": "…"`, unescaped, and whether its closing quote was seen.
fn extract_string(arguments: &str, field: &str) -> Option<(String, bool)> {
    let needle = format!("\"{field}\"");
    let after = &arguments[arguments.find(&needle)? + needle.len()..];
    let mut chars = after[after.find(':')? + 1..].trim_start().chars();
    if chars.next()? != '"' {
        return None;
    }
    let mut out = String::new();
    let mut escaped = false;
    let mut closed = false;
    for ch in chars {
        if escaped {
            out.push(match ch {
                'n' => '\n',
                'r' => '\r',
                't' => '\t',
                other => other,
            });
            escaped = false;
        } else if ch == '\\' {
            escaped = true;
        } else if ch == '"' {
            closed = true;
            break;
        } else {
            out.push(ch);
        }
    }
    let trimmed = out.trim();
    (!trimmed.is_empty()).then(|| (trimmed.to_string(), closed))
}

#[cfg(test)]
mod tests {
    use super::parse_tool_arguments;
    use serde_json::json;

    #[test]
    fn valid_json_is_untouched() {
        let value = parse_tool_arguments("WriteFile", r#"{"path": "a.py", "content": "x = 1\n"}"#).unwrap();
        assert_eq!(value, json!({"path": "a.py", "content": "x = 1\n"}));
    }

    #[test]
    fn raw_newlines_and_tabs_in_strings_are_escaped() {
        let raw = "{\"path\": \"a.py\", \"content\": \"def f():\n\treturn 1\n\"}";
        let value = parse_tool_arguments("WriteFile", raw).unwrap();
        assert_eq!(value["content"], "def f():\n\treturn 1\n");
    }

    #[test]
    fn fences_trailing_commas_and_double_encoding() {
        let fenced = "```json\n{\"path\": \"a.py\", \"edit\": [{\"old\": \"a\", \"new\": \"b\"},],}\n```";
        let value = parse_tool_arguments("StrReplaceFile", fenced).unwrap();
        assert_eq!(value["edit"][0]["new"], "b");
        let twice = serde_json::to_string(&json!({"path": "b.py"}).to_string()).unwrap();
        assert_eq!(parse_tool_arguments("ReadFile", &twice).unwrap(), json!({"path": "b.py"}));
        // A comma inside a string is content, not syntax.
        let value = parse_tool_arguments("WriteFile", "{\"path\": \"c\", \"content\": \"a,]\",}").unwrap();
        assert_eq!(value["content"], "a,]");
    }

    #[test]
    fn truncation_is_completed_only_for_read_only_tools() {
        let cut = r#"{"path": "packages/shared/lib/keys/setupHelper.ts"#;
        assert_eq!(parse_tool_arguments("ReadFile", cut).unwrap(), json!({"path": "packages/shared/lib/keys/setupHelper.ts"}));
        let grep = r#"{"pattern": "fn main", "path": "src"#;
        assert_eq!(parse_tool_arguments("Grep", grep).unwrap(), json!({"pattern": "fn main", "path": "src"}));
        // A half-written file must never be written as if it were whole.
        let write = r#"{"path": "a.py", "content": "def f():\n    retu"#;
        assert!(parse_tool_arguments("WriteFile", write).is_err());
        // Nor a half-written command run.
        assert!(parse_tool_arguments("Shell", r#"{"command": "rm -rf build/tm"#).is_err());
        assert_eq!(parse_tool_arguments("Shell", r#"{"command": "ls -la" "#).unwrap(), json!({"command": "ls -la"}));
    }

    #[test]
    fn a_flat_edit_is_moved_into_the_edit_list() {
        let value = parse_tool_arguments("StrReplaceFile", r#"{"path": "a.py", "old": "x", "new": "y"}"#).unwrap();
        assert_eq!(value, json!({"path": "a.py", "edit": {"old": "x", "new": "y"}}));
        let plural = parse_tool_arguments("StrReplaceFile", r#"{"path": "a.py", "edits": [{"old": "x", "new": "y"}]}"#).unwrap();
        assert_eq!(plural, json!({"path": "a.py", "edit": [{"old": "x", "new": "y"}]}));
        // Other tools, and a correct call, are untouched.
        let shell = parse_tool_arguments("Shell", r#"{"old": "x", "new": "y"}"#).unwrap();
        assert_eq!(shell, json!({"old": "x", "new": "y"}));
        let right = parse_tool_arguments("StrReplaceFile", r#"{"path": "a", "edit": {"old": "x", "new": "y"}}"#).unwrap();
        assert_eq!(right, json!({"path": "a", "edit": {"old": "x", "new": "y"}}));
    }
}
