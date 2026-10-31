use std::collections::{BTreeSet, HashMap};

use kosong::message::ToolCall;
use kosong::tooling::ToolResult;
use serde::Serialize;

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct CompletionGateSnapshot {
    pub required: Vec<String>,
    pub passed: Vec<String>,
    pub missing: Vec<String>,
    pub satisfied: bool,
}

#[derive(Debug, Default)]
pub struct CompletionGate {
    required: BTreeSet<String>,
    passed: BTreeSet<String>,
}

impl CompletionGate {
    pub fn new(required: impl IntoIterator<Item = String>) -> Self {
        Self {
            required: required
                .into_iter()
                .filter(|name| !name.trim().is_empty())
                .collect(),
            passed: BTreeSet::new(),
        }
    }

    pub fn reset(&mut self) {
        self.passed.clear();
    }

    pub fn observe(&mut self, tool_calls: &[ToolCall], tool_results: &[ToolResult]) {
        let results = tool_results
            .iter()
            .map(|result| (result.tool_call_id.as_str(), result))
            .collect::<HashMap<_, _>>();
        for call in tool_calls {
            let name = call.function.name.trim();
            let Some(result) = results.get(call.id.as_str()) else {
                continue;
            };
            if self.required.contains(name) && !result.return_value.is_error {
                self.passed.insert(name.to_string());
            }
        }
    }

    pub fn missing(&self) -> Vec<String> {
        self.required.difference(&self.passed).cloned().collect()
    }

    pub fn snapshot(&self) -> CompletionGateSnapshot {
        let required = self.required.iter().cloned().collect::<Vec<_>>();
        let passed = self.passed.iter().cloned().collect::<Vec<_>>();
        let missing = self.missing();
        CompletionGateSnapshot {
            required,
            passed,
            satisfied: missing.is_empty(),
            missing,
        }
    }
}
