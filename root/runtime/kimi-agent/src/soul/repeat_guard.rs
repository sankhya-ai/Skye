//! Noticing when the model repeats itself.
//!
//! Weaker models loop: the same `ReadFile`, the same failing `Shell`, the same
//! `StrReplaceFile` that does not match, step after step, until the turn runs
//! out of steps. When a call is identical to an earlier one in the same turn
//! and got the identical result, the result the model sees gains a short note
//! saying so, which is usually enough to make it change approach.
//!
//! Only the model's context is annotated; the wire events and the transcript
//! show the real result.

use std::collections::HashMap;
use std::hash::{DefaultHasher, Hash, Hasher};

use kosong::message::ToolCall;

use crate::wire::ToolResult;

/// Nudge from the second repeat on (the third identical call).
const NUDGE_FROM: usize = 2;
const MAX_TRACKED: usize = 256;

#[derive(Default)]
pub struct RepeatGuard {
    /// (call signature, result signature) -> how many times it was seen.
    seen: HashMap<(u64, u64), usize>,
}

impl RepeatGuard {
    pub fn reset(&mut self) {
        self.seen.clear();
    }

    /// Annotate results whose call and outcome repeat earlier ones this turn.
    pub fn annotate(&mut self, calls: &[ToolCall], results: &[ToolResult]) -> Vec<ToolResult> {
        results
            .iter()
            .map(|result| {
                let Some(call) = calls.iter().find(|c| c.id == result.tool_call_id) else {
                    return result.clone();
                };
                let key = (call_signature(call), result_signature(result));
                if self.seen.len() >= MAX_TRACKED && !self.seen.contains_key(&key) {
                    return result.clone();
                }
                let count = self.seen.entry(key).or_insert(0);
                *count += 1;
                let repeats = *count - 1;
                if repeats < NUDGE_FROM {
                    return result.clone();
                }
                let mut annotated = result.clone();
                let note = format!(
                    "You have now made this exact `{}` call {} times this turn and got the same result each time. \
                     Repeating it will not change anything: read the result, then try a different approach \
                     (different arguments, a different tool, or inspect the state first).",
                    call.function.name,
                    repeats + 1
                );
                let message = &mut annotated.return_value.message;
                if message.is_empty() {
                    *message = note;
                } else {
                    message.push('\n');
                    message.push_str(&note);
                }
                annotated
            })
            .collect()
    }
}

fn call_signature(call: &ToolCall) -> u64 {
    let mut hasher = DefaultHasher::new();
    call.function.name.hash(&mut hasher);
    // Whitespace-insensitive, so reformatted but identical arguments match.
    let args = call.function.arguments.as_deref().unwrap_or_default();
    serde_json::from_str::<serde_json::Value>(args)
        .map(|v| v.to_string())
        .unwrap_or_else(|_| args.split_whitespace().collect::<Vec<_>>().join(" "))
        .hash(&mut hasher);
    hasher.finish()
}

fn result_signature(result: &ToolResult) -> u64 {
    let mut hasher = DefaultHasher::new();
    let value = &result.return_value;
    value.is_error.hash(&mut hasher);
    value.message.hash(&mut hasher);
    serde_json::to_string(&value.output).unwrap_or_default().hash(&mut hasher);
    hasher.finish()
}

#[cfg(test)]
mod tests {
    use super::*;
    use kosong::message::ToolCallFunction;
    use kosong::tooling::{ToolOutput, ToolReturnValue};

    fn call(id: &str, args: &str) -> ToolCall {
        ToolCall {
            kind: "function".into(),
            id: id.into(),
            function: ToolCallFunction { name: "ReadFile".into(), arguments: Some(args.into()) },
            extras: None,
        }
    }

    fn result(id: &str, message: &str) -> ToolResult {
        let value = ToolReturnValue {
            is_error: false,
            output: ToolOutput::Text("contents".into()),
            message: message.into(),
            display: Vec::new(),
            extras: None,
        };
        ToolResult { tool_call_id: id.into(), return_value: value }
    }

    #[test]
    fn the_third_identical_call_with_the_same_result_is_annotated() {
        let mut guard = RepeatGuard::default();
        for (index, expected) in [(1, false), (2, false), (3, true), (4, true)] {
            let id = format!("c{index}");
            let args = if index % 2 == 0 { r#"{"path":"a.py"}"# } else { r#"{ "path": "a.py" }"# };
            let out = guard.annotate(&[call(&id, args)], &[result(&id, "same")]);
            assert_eq!(out[0].return_value.message.contains("exact `ReadFile` call"), expected, "call {index}");
        }
    }

    #[test]
    fn a_different_result_or_argument_is_not_a_repeat() {
        let mut guard = RepeatGuard::default();
        for index in 0..4 {
            let id = format!("c{index}");
            let out = guard.annotate(&[call(&id, r#"{"path":"a.py"}"#)], &[result(&id, &format!("v{index}"))]);
            assert!(!out[0].return_value.message.contains("exact"));
        }
        guard.reset();
        for index in 0..4 {
            let id = format!("d{index}");
            let out = guard.annotate(&[call(&id, &format!(r#"{{"path":"f{index}"}}"#))], &[result(&id, "same")]);
            assert!(!out[0].return_value.message.contains("exact"));
        }
    }
}
