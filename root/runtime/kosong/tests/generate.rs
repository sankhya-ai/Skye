use std::any::Any;
use std::collections::VecDeque;
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};

use async_trait::async_trait;
use kosong::chat_provider::{
    ChatProvider, ChatProviderError, ChatProviderErrorKind, StreamedMessage, ThinkingEffort,
    TokenUsage,
};
use kosong::message::{
    ContentPart, Message, StreamedMessagePart, TextPart, ThinkPart, ToolCall, ToolCallFunction,
    ToolCallPart,
};
use kosong::tooling::{Tool, ToolResult, ToolResultFuture, Toolset, tool_ok};

struct FakeProvider {
    parts: Vec<StreamedMessagePart>,
}

#[async_trait]
impl ChatProvider for FakeProvider {
    fn name(&self) -> &str {
        "fake"
    }

    fn model_name(&self) -> &str {
        "fake"
    }

    fn thinking_effort(&self) -> Option<ThinkingEffort> {
        None
    }

    async fn generate(
        &self,
        _system_prompt: &str,
        _tools: &[Tool],
        _history: &[Message],
    ) -> Result<Box<dyn StreamedMessage>, ChatProviderError> {
        Ok(Box::new(FakeStream {
            parts: self.parts.clone().into(),
        }))
    }

    fn with_thinking(&self, _effort: ThinkingEffort) -> Box<dyn ChatProvider> {
        Box::new(FakeProvider {
            parts: self.parts.clone(),
        })
    }

    fn as_any(&self) -> &dyn Any {
        self
    }
}

struct FakeStream {
    parts: VecDeque<StreamedMessagePart>,
}

#[async_trait]
impl StreamedMessage for FakeStream {
    async fn next_part(&mut self) -> Result<Option<StreamedMessagePart>, ChatProviderError> {
        Ok(self.parts.pop_front())
    }

    fn id(&self) -> Option<String> {
        None
    }

    fn usage(&self) -> Option<TokenUsage> {
        None
    }
}

fn tool_call(arguments: Option<&str>) -> StreamedMessagePart {
    StreamedMessagePart::ToolCall(ToolCall {
        kind: "function".to_string(),
        id: "tool_1".to_string(),
        function: ToolCallFunction {
            name: "WriteFile".to_string(),
            arguments: arguments.map(str::to_string),
        },
        extras: None,
    })
}

#[tokio::test]
async fn generate_merges_tool_call_parts_before_validating_arguments() {
    let provider = FakeProvider {
        parts: vec![
            tool_call(Some("{\"path\":\"a.txt\",")),
            StreamedMessagePart::ToolCallPart(ToolCallPart {
                arguments_part: Some("\"content\":\"hello\"}".to_string()),
            }),
        ],
    };

    let result = kosong::generate(&provider, "", vec![], &[], None, None)
        .await
        .expect("valid merged tool call");

    let tool_calls = result.message.tool_calls.expect("tool call");
    assert_eq!(
        tool_calls[0].function.arguments.as_deref(),
        Some("{\"path\":\"a.txt\",\"content\":\"hello\"}")
    );
}

#[tokio::test]
async fn generate_rejects_incomplete_tool_call_json_as_retryable_error() {
    let provider = FakeProvider {
        parts: vec![
            tool_call(Some("{\"path\":\"a.txt\",")),
            StreamedMessagePart::ToolCallPart(ToolCallPart {
                arguments_part: Some("\"content\":\"unterminated".to_string()),
            }),
        ],
    };

    let err = match kosong::generate(&provider, "", vec![], &[], None, None).await {
        Ok(_) => panic!("malformed tool call should not execute"),
        Err(err) => err,
    };

    assert!(matches!(err.kind, ChatProviderErrorKind::MalformedToolCall));
}

#[tokio::test]
async fn generate_rejects_orphaned_tool_call_continuation() {
    let provider = FakeProvider {
        parts: vec![
            StreamedMessagePart::Content(ContentPart::Text(TextPart::new("partial"))),
            StreamedMessagePart::ToolCallPart(ToolCallPart {
                arguments_part: Some("{\"path\":\"a.txt\"}".to_string()),
            }),
        ],
    };

    let error = match kosong::generate(&provider, "", vec![], &[], None, None).await {
        Ok(_) => panic!("orphaned tool continuation must fail"),
        Err(error) => error,
    };

    assert!(matches!(
        error.kind,
        ChatProviderErrorKind::MalformedToolCall
    ));
}

#[tokio::test]
async fn generate_rejects_reasoning_only_response_as_empty() {
    let provider = FakeProvider {
        parts: vec![StreamedMessagePart::Content(ContentPart::Think(
            ThinkPart::new("Need to call tool_describe for page."),
        ))],
    };

    let error = match kosong::generate(&provider, "", vec![], &[], None, None).await {
        Ok(_) => panic!("reasoning-only response must not end the turn"),
        Err(error) => error,
    };

    assert!(matches!(error.kind, ChatProviderErrorKind::EmptyResponse));
    assert_eq!(
        error.message,
        "The API returned no visible answer or tool call."
    );
}

struct FailingAfterToolCallProvider;

#[async_trait]
impl ChatProvider for FailingAfterToolCallProvider {
    fn name(&self) -> &str {
        "failing"
    }

    fn model_name(&self) -> &str {
        "failing"
    }

    fn thinking_effort(&self) -> Option<ThinkingEffort> {
        None
    }

    async fn generate(
        &self,
        _system_prompt: &str,
        _tools: &[Tool],
        _history: &[Message],
    ) -> Result<Box<dyn StreamedMessage>, ChatProviderError> {
        Ok(Box::new(FailingAfterToolCallStream { index: 0 }))
    }

    fn with_thinking(&self, _effort: ThinkingEffort) -> Box<dyn ChatProvider> {
        Box::new(Self)
    }

    fn as_any(&self) -> &dyn Any {
        self
    }
}

struct FailingAfterToolCallStream {
    index: usize,
}

#[async_trait]
impl StreamedMessage for FailingAfterToolCallStream {
    async fn next_part(&mut self) -> Result<Option<StreamedMessagePart>, ChatProviderError> {
        let result = match self.index {
            0 => Ok(Some(tool_call(Some("{}")))),
            1 => Ok(Some(StreamedMessagePart::Content(ContentPart::Text(
                TextPart::new("partial"),
            )))),
            _ => Err(ChatProviderError::new(
                ChatProviderErrorKind::Timeout,
                "provider stalled",
            )),
        };
        self.index += 1;
        result
    }

    fn id(&self) -> Option<String> {
        None
    }

    fn usage(&self) -> Option<TokenUsage> {
        None
    }
}

struct RecordingToolset {
    calls: Arc<AtomicUsize>,
}

impl Toolset for RecordingToolset {
    fn tools(&self) -> Vec<Tool> {
        vec![]
    }

    fn handle(&self, tool_call: ToolCall) -> ToolResultFuture {
        self.calls.fetch_add(1, Ordering::SeqCst);
        ToolResultFuture::Immediate(ToolResult {
            tool_call_id: tool_call.id,
            return_value: tool_ok("", "ok", ""),
        })
    }
}

#[tokio::test]
async fn step_never_dispatches_tools_from_an_incomplete_provider_response() {
    let calls = Arc::new(AtomicUsize::new(0));
    let toolset = RecordingToolset {
        calls: Arc::clone(&calls),
    };

    let error =
        match kosong::step(&FailingAfterToolCallProvider, "", &toolset, &[], None, None).await {
            Ok(_) => panic!("incomplete provider response must fail"),
            Err(error) => error,
        };

    assert!(matches!(error.kind, ChatProviderErrorKind::Timeout));
    assert_eq!(calls.load(Ordering::SeqCst), 0);
}
