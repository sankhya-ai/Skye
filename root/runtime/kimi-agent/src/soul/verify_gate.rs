//! Edits must be exercised before a turn ends. (root; see VENDORED.md.)
//!
//! A weaker model will read, edit, and report "fixed" without running
//! anything; the claim is then only as good as its reading of the code. The
//! system prompt already says to verify, but a prompt is advice. This is the
//! harness's version: it watches the turn, and when a step would end it with
//! an edit that no later command has exercised, it keeps the turn open with a
//! note saying so.
//!
//! "Exercised" is deliberately loose — any `Shell` command that is not plainly
//! read-only (`ls`, `cat`, `grep`, read-only `git`, …) counts, pass or fail. The
//! gate asks for evidence, not for green: a failing run the model then reports
//! honestly is a verified turn.
//!
//! It also keeps the facts the director is given (see [`VerifyGate::facts`]):
//! which files this turn read and edited, and whether a check ran since the
//! last edit — so Jev judges "read again?" and "done?" from the record rather
//! than from twelve clipped steps.
//!
//! Off unless `ROOT_VERIFY_GATE` is set (the host sets it), so the runtime
//! behaves as upstream elsewhere.

use std::collections::BTreeSet;
use std::collections::HashMap;

use kosong::message::ToolCall;
use kosong::tooling::ToolResult;
use serde_json::{Value, json};

pub const VERIFY_GATE_ENV: &str = "ROOT_VERIFY_GATE";

/// Times one turn may be held open. A model that edits again after the note
/// and stops again gets one more reminder; past that it ends as it chooses.
const MAX_NUDGES: u8 = 2;
const FACT_PATHS: usize = 24;

const EDIT_TOOLS: [&str; 2] = ["WriteFile", "StrReplaceFile"];
const READ_TOOLS: [&str; 2] = ["ReadFile", "ReadMediaFile"];

/// Programs that only look. Everything else run through `Shell` counts as
/// exercising the code.
const READ_ONLY: [&str; 30] = [
    "ls", "cat", "head", "tail", "less", "more", "grep", "rg", "ag", "find", "fd", "wc", "echo",
    "printf", "pwd", "cd", "tree", "stat", "file", "which", "whereis", "type", "du", "df", "date",
    "env", "printenv", "diff", "cmp", "true",
];
const READ_ONLY_GIT: [&str; 9] = [
    "status", "diff", "log", "show", "blame", "branch", "ls-files", "rev-parse", "grep",
];

#[derive(Debug, Default)]
pub struct VerifyGate {
    enabled: bool,
    read: BTreeSet<String>,
    edited: Vec<String>,
    checks: usize,
    unchecked: bool,
    nudges: u8,
}

impl VerifyGate {
    pub fn from_env() -> Self {
        let enabled = matches!(
            std::env::var(VERIFY_GATE_ENV)
                .unwrap_or_default()
                .trim()
                .to_ascii_lowercase()
                .as_str(),
            "1" | "on" | "true" | "yes"
        );
        Self::new(enabled)
    }

    pub fn new(enabled: bool) -> Self {
        Self {
            enabled,
            ..Self::default()
        }
    }

    pub fn reset(&mut self) {
        *self = Self::new(self.enabled);
    }

    pub fn observe(&mut self, calls: &[ToolCall], results: &[ToolResult]) {
        let results: HashMap<&str, &ToolResult> = results
            .iter()
            .map(|result| (result.tool_call_id.as_str(), result))
            .collect();
        for call in calls {
            let Some(result) = results.get(call.id.as_str()) else {
                continue;
            };
            let name = call.function.name.trim();
            let args: Value = call
                .function
                .arguments
                .as_deref()
                .and_then(|raw| serde_json::from_str(raw).ok())
                .unwrap_or(Value::Null);
            let path = args.get("path").and_then(Value::as_str).unwrap_or("").to_string();
            if READ_TOOLS.contains(&name) && !result.return_value.is_error && !path.is_empty() {
                self.read.insert(path);
            } else if EDIT_TOOLS.contains(&name) && !result.return_value.is_error {
                self.unchecked = true;
                if !path.is_empty() && !self.edited.contains(&path) {
                    self.edited.push(path);
                }
            } else if name == "Shell" {
                let command = args.get("command").and_then(Value::as_str).unwrap_or("");
                if exercises(command) {
                    self.checks += 1;
                    self.unchecked = false;
                }
            }
        }
    }

    /// Edits no command has exercised since.
    pub fn unchecked(&self) -> bool {
        self.unchecked
    }

    /// The note that keeps the turn open, or `None` to let it end.
    pub fn hold(&mut self) -> Option<String> {
        if !self.enabled || !self.unchecked || self.nudges >= MAX_NUDGES {
            return None;
        }
        self.nudges += 1;
        let files = if self.edited.is_empty() {
            "files".to_string()
        } else {
            self.edited
                .iter()
                .take(8)
                .map(|p| format!("`{p}`"))
                .collect::<Vec<_>>()
                .join(", ")
        };
        Some(format!(
            "Not finished: this turn changed {files}, and nothing has run since to show the change works. \
             Run the project's tests if it has any. If it has none, write a short script that reproduces \
             each problem the request describes, run it with Shell, and confirm it now behaves as intended \
             (delete the script afterwards unless the request wants it kept). If a check fails, fix the \
             cause and run it again. Only if running code is impossible here, finish by saying exactly \
             what is unverified."
        ))
    }

    /// What the director should know about this turn so far.
    pub fn facts(&self) -> Value {
        json!({
            "files_read_this_turn": self.read.iter().take(FACT_PATHS).collect::<Vec<_>>(),
            "files_edited_this_turn": self.edited.iter().take(FACT_PATHS).collect::<Vec<_>>(),
            "commands_run_that_exercise_code": self.checks,
            "edits_not_yet_exercised": self.unchecked,
        })
    }
}

/// Whether a shell command runs code rather than only looking at it.
pub fn exercises(command: &str) -> bool {
    for segment in command.split(['&', '|', ';', '\n']) {
        let words: Vec<&str> = segment
            .split_whitespace()
            .skip_while(|w| w.contains('=') && !w.starts_with('-'))
            .collect();
        let Some(program) = words.first() else {
            continue;
        };
        let program = program.rsplit('/').next().unwrap_or(program);
        if program == "git" {
            let sub = words.iter().skip(1).find(|w| !w.starts_with('-')).copied().unwrap_or("");
            if READ_ONLY_GIT.contains(&sub) {
                continue;
            }
            return true;
        }
        if program == "sed" && !words.iter().any(|w| w.starts_with("-i")) {
            continue;
        }
        if !READ_ONLY.contains(&program) {
            return true;
        }
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;

    fn call(id: &str, name: &str, args: Value) -> ToolCall {
        serde_json::from_value(json!({
            "type": "function",
            "id": id,
            "function": {"name": name, "arguments": args.to_string()},
            "extras": null,
        }))
        .unwrap()
    }

    fn result(id: &str, is_error: bool) -> ToolResult {
        ToolResult {
            tool_call_id: id.to_string(),
            return_value: serde_json::from_value(json!({"is_error": is_error})).unwrap(),
        }
    }

    #[test]
    fn read_only_commands_do_not_count_as_checks() {
        for command in ["git diff HEAD~1", "cd app && git log --oneline", "ls -la", "cat a.py | head", "grep -rn x .", "sed -n 1,20p a.py", "FOO=1 git status"] {
            assert!(!exercises(command), "{command}");
        }
        for command in ["python3 repro.py", "pytest -q", "cd app && make test", "npm test", "cargo test", "sed -i s/a/b/ f", "./run.sh", "git commit -m x"] {
            assert!(exercises(command), "{command}");
        }
    }

    #[test]
    fn an_unexercised_edit_holds_the_turn_open_at_most_twice() {
        let mut gate = VerifyGate::new(true);
        gate.observe(&[call("1", "ReadFile", json!({"path": "a.py"}))], &[result("1", false)]);
        assert!(gate.hold().is_none(), "reading alone never holds a turn");
        gate.observe(&[call("2", "StrReplaceFile", json!({"path": "a.py"}))], &[result("2", false)]);
        let note = gate.hold().expect("held");
        assert!(note.contains("`a.py`"));
        gate.observe(&[call("3", "Shell", json!({"command": "git diff"}))], &[result("3", false)]);
        assert!(gate.hold().is_some(), "git diff is not a check");
        assert!(gate.hold().is_none(), "two reminders, then the model decides");
    }

    #[test]
    fn a_failing_run_still_counts_and_a_later_edit_reopens() {
        let mut gate = VerifyGate::new(true);
        gate.observe(&[call("1", "WriteFile", json!({"path": "b.py"}))], &[result("1", false)]);
        gate.observe(&[call("2", "Shell", json!({"command": "python3 b.py"}))], &[result("2", true)]);
        assert!(!gate.unchecked());
        assert!(gate.hold().is_none());
        gate.observe(&[call("3", "StrReplaceFile", json!({"path": "b.py"}))], &[result("3", false)]);
        assert!(gate.unchecked());
        assert_eq!(gate.facts()["files_edited_this_turn"], json!(["b.py"]));
        assert_eq!(gate.facts()["commands_run_that_exercise_code"], json!(1));
    }

    #[test]
    fn a_failed_edit_or_a_disabled_gate_changes_nothing() {
        let mut gate = VerifyGate::new(true);
        gate.observe(&[call("1", "StrReplaceFile", json!({"path": "c.py"}))], &[result("1", true)]);
        assert!(gate.hold().is_none());
        let mut off = VerifyGate::new(false);
        off.observe(&[call("1", "WriteFile", json!({"path": "c.py"}))], &[result("1", false)]);
        assert!(off.unchecked() && off.hold().is_none());
    }
}
