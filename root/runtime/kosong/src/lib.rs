//! Kosong: LLM abstraction layer.

pub mod chat_provider;
pub mod message;
pub mod tooling;
pub mod utils;

mod generate;

pub use generate::{GenerateResult, generate};

use std::collections::HashMap;

use chat_provider::{ChatProvider, TokenUsage};
use message::{Message, StreamedMessagePart, ToolCall};
use tokio::sync::{mpsc, oneshot};
use tooling::error::tool_runtime_error;
use tooling::{ToolResult, ToolResultFuture, Toolset};

/// Run one step (generate + tool dispatch).
pub async fn step(
    chat_provider: &dyn ChatProvider,
    system_prompt: &str,
    toolset: &dyn Toolset,
    history: &[Message],
    message_part_tx: Option<mpsc::UnboundedSender<StreamedMessagePart>>,
    tool_result_tx: Option<mpsc::UnboundedSender<ToolResult>>,
) -> Result<StepResult, chat_provider::ChatProviderError> {
    let result = generate::generate(
        chat_provider,
        system_prompt,
        toolset.tools(),
        history,
        message_part_tx,
        None,
    )
    .await?;

    // A streamed provider response can fail after emitting a tool-call prefix.
    // Dispatch only after the complete response has validated so a retry can
    // never duplicate a side effect from an incomplete generation.
    Ok(dispatch(
        toolset,
        result.id,
        result.message,
        result.usage,
        tool_result_tx,
    ))
}

/// Dispatch the tool calls an assistant message carries, as `step` does after
/// generating it.
///
/// Public so a harness can hand the toolset a call it decided on itself: the
/// message it builds goes through exactly the same dispatch, approval and
/// result plumbing as one the model wrote, so nothing downstream can tell the
/// two apart.
pub fn dispatch(
    toolset: &dyn Toolset,
    id: Option<String>,
    message: Message,
    usage: Option<TokenUsage>,
    tool_result_tx: Option<mpsc::UnboundedSender<ToolResult>>,
) -> StepResult {
    let mut tool_calls = Vec::new();
    let mut tool_result_receivers: HashMap<String, oneshot::Receiver<anyhow::Result<ToolResult>>> =
        HashMap::new();

    for tool_call in message.tool_calls.clone().unwrap_or_default() {
        tool_calls.push(tool_call.clone());
        let (result_tx, result_rx) = oneshot::channel();
        tool_result_receivers.insert(tool_call.id.clone(), result_rx);
        let tool_result = toolset.handle(tool_call.clone());
        match tool_result {
            ToolResultFuture::Immediate(result) => {
                if let Some(tx) = tool_result_tx.as_ref() {
                    let _ = tx.send(result.clone());
                }
                let _ = result_tx.send(Ok(result));
            }
            ToolResultFuture::Pending(future) => {
                let tool_result_tx = tool_result_tx.clone();
                let tool_call_id = tool_call.id.clone();
                tokio::spawn(async move {
                    let result = match future.await {
                        Ok(result) => result,
                        Err(error) => ToolResult {
                            tool_call_id,
                            return_value: tool_runtime_error(&error.to_string()),
                        },
                    };
                    if let Some(tx) = tool_result_tx {
                        let _ = tx.send(result.clone());
                    }
                    let _ = result_tx.send(Ok(result));
                });
            }
        }
    }

    StepResult {
        id,
        message,
        usage,
        tool_calls,
        tool_result_receivers,
    }
}

/// Step result returned by `step`.
pub struct StepResult {
    pub id: Option<String>,
    pub message: Message,
    pub usage: Option<TokenUsage>,
    pub tool_calls: Vec<ToolCall>,
    tool_result_receivers: HashMap<String, oneshot::Receiver<anyhow::Result<ToolResult>>>,
}

impl StepResult {
    pub async fn tool_results(&mut self) -> anyhow::Result<Vec<ToolResult>> {
        let mut results = Vec::new();
        for tool_call in &self.tool_calls {
            if let Some(rx) = self.tool_result_receivers.remove(&tool_call.id) {
                let result = rx
                    .await
                    .map_err(|_| anyhow::anyhow!("Tool result channel closed"))??;
                results.push(result);
            }
        }
        Ok(results)
    }
}
