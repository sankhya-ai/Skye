//! Tool calls written as text. (root; see VENDORED.md.)
//!
//! Some models, served through some providers, now and then emit a tool call in
//! their native markup inside the reply text — DeepSeek's `<｜DSML｜tool_calls>`,
//! a Hermes/Qwen `<tool_call>` block — instead of through the tool-calling
//! interface. The runtime sees words and no call, so the turn ends mid-task
//! with the markup shown to the user. When a step ends that way the harness
//! keeps the turn open with a note to make the call properly, at most
//! [`MAX_NOTES`] times a turn.

/// Markers of a tool call written into text, across common native formats.
const MARKERS: [&str; 7] = [
    "｜DSML｜tool_calls",
    "|DSML|tool_calls",
    "｜DSML｜invoke",
    "<｜tool▁calls▁begin｜>",
    "<｜tool▁call▁begin｜>",
    "<tool_call>",
    "<function_call>",
];

const MAX_NOTES: u8 = 2;

/// Whether reply text carries a tool call in markup rather than as a call.
pub fn leaked(text: &str) -> bool {
    MARKERS.iter().any(|marker| text.contains(marker))
}

#[derive(Debug, Default)]
pub struct LeakedCalls {
    notes: u8,
}

impl LeakedCalls {
    pub fn reset(&mut self) {
        self.notes = 0;
    }

    /// The note that keeps the turn open, when `text` ended a step with a
    /// call written as markup.
    pub fn hold(&mut self, text: &str) -> Option<&'static str> {
        if !leaked(text) || self.notes >= MAX_NOTES {
            return None;
        }
        self.notes += 1;
        Some(
            "Your last message wrote a tool call as text (tool-call markup in the reply) instead of calling the tool, \
             so nothing ran and the turn would have ended there. Tools only run when called through the tool-calling \
             interface: make that call now, as a real tool call, and continue the task.",
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn markup_in_the_reply_holds_the_turn_twice_at_most() {
        let text = "Let me check:\n\n<｜DSML｜tool_calls>\n<｜DSML｜invoke name=\"ReadFile\">\n</｜DSML｜invoke>\n</｜DSML｜tool_calls>";
        let mut calls = LeakedCalls::default();
        assert!(calls.hold(text).is_some());
        assert!(calls.hold(text).is_some());
        assert!(calls.hold(text).is_none());
        calls.reset();
        assert!(calls.hold("<tool_call>{\"name\": \"Shell\"}</tool_call>").is_some());
    }

    #[test]
    fn ordinary_replies_end_the_turn() {
        let mut calls = LeakedCalls::default();
        assert!(calls.hold("Fixed it and ran the tests: 12 passed.").is_none());
        assert!(calls.hold("Use the `tool_call` field in the config.").is_none());
    }
}
