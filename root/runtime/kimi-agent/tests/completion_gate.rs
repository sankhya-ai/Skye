use kimi_agent::soul::completion_gate::CompletionGate;
use kosong::message::ToolCall;
use kosong::tooling::{ToolResult, tool_error, tool_ok};

fn result(id: &str, passed: bool) -> ToolResult {
    ToolResult {
        tool_call_id: id.to_string(),
        return_value: if passed {
            tool_ok("{}", "gate passed", "")
        } else {
            tool_error("{}", "gate blocked", "")
        },
    }
}

#[test]
fn completion_gate_accepts_only_successful_required_tool_results() {
    let mut gate = CompletionGate::new(["runtime_completion_gate".to_string()]);
    let call = ToolCall::new("call-1", "runtime_completion_gate");

    gate.observe(std::slice::from_ref(&call), &[result("call-1", false)]);
    assert_eq!(gate.missing(), vec!["runtime_completion_gate"]);

    gate.observe(&[call], &[result("call-1", true)]);
    assert!(gate.missing().is_empty());
}

#[test]
fn completion_gate_resets_for_each_owner_turn() {
    let mut gate = CompletionGate::new(["runtime_completion_gate".to_string()]);
    let call = ToolCall::new("call-1", "runtime_completion_gate");
    gate.observe(&[call], &[result("call-1", true)]);
    assert!(gate.missing().is_empty());

    gate.reset();
    assert_eq!(gate.missing(), vec!["runtime_completion_gate"]);
}

#[test]
fn completion_gate_snapshot_is_a_complete_sorted_receipt() {
    let mut gate = CompletionGate::new([
        "runtime_completion_gate".to_string(),
        "secondary_gate".to_string(),
    ]);
    let call = ToolCall::new("call-1", "runtime_completion_gate");
    gate.observe(&[call], &[result("call-1", true)]);

    let snapshot = gate.snapshot();

    assert_eq!(
        snapshot.required,
        vec!["runtime_completion_gate", "secondary_gate"]
    );
    assert_eq!(snapshot.passed, vec!["runtime_completion_gate"]);
    assert_eq!(snapshot.missing, vec!["secondary_gate"]);
    assert!(!snapshot.satisfied);
}
