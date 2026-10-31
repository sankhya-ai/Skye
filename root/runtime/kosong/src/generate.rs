use crate::chat_provider::{ChatProvider, ChatProviderError, ChatProviderErrorKind, TokenUsage};
use crate::message::{Message, Role, StreamedMessagePart, ToolCall};
use crate::tooling::Tool;
use serde_json::Value;
use tokio::sync::mpsc;
use tracing::trace;

pub struct GenerateResult {
    pub id: Option<String>,
    pub message: Message,
    pub usage: Option<TokenUsage>,
}

pub async fn generate(
    chat_provider: &dyn ChatProvider,
    system_prompt: &str,
    tools: Vec<Tool>,
    history: &[Message],
    message_part_tx: Option<mpsc::UnboundedSender<StreamedMessagePart>>,
    mut on_tool_call: Option<&mut dyn FnMut(ToolCall)>,
) -> Result<GenerateResult, ChatProviderError> {
    let mut message = Message::new(Role::Assistant, Vec::new());
    let mut pending: Option<StreamedMessagePart> = None;

    trace!("Generating with history: {:?}", history);
    let mut stream = chat_provider
        .generate(system_prompt, &tools, history)
        .await?;

    loop {
        let part = stream.next_part().await?;
        if part.is_none() {
            break;
        }
        let part = part.unwrap();
        trace!("Received part: {:?}", part);
        if let Some(tx) = message_part_tx.as_ref() {
            let _ = tx.send(part.clone());
        }

        if pending.is_none() {
            pending = Some(part);
            continue;
        }
        let mut current = pending.take().unwrap();
        if current.merge_in_place(&part) {
            pending = Some(current);
        } else {
            append_part(&mut message, current, &mut on_tool_call)?;
            pending = Some(part);
        }
    }

    if let Some(final_part) = pending {
        append_part(&mut message, final_part, &mut on_tool_call)?;
    }

    if !message_has_visible_content(&message)
        && message
            .tool_calls
            .as_ref()
            .map(|v| v.is_empty())
            .unwrap_or(true)
    {
        return Err(ChatProviderError::new(
            ChatProviderErrorKind::EmptyResponse,
            "The API returned no visible answer or tool call.",
        ));
    }

    Ok(GenerateResult {
        id: stream.id(),
        message,
        usage: stream.usage(),
    })
}

fn message_has_visible_content(message: &Message) -> bool {
    message.content.iter().any(|part| match part {
        crate::message::ContentPart::Text(text) => !text.text.trim().is_empty(),
        crate::message::ContentPart::ImageUrl(_)
        | crate::message::ContentPart::AudioUrl(_)
        | crate::message::ContentPart::VideoUrl(_) => true,
        crate::message::ContentPart::Think(_) => false,
    })
}

fn append_part(
    message: &mut Message,
    part: StreamedMessagePart,
    on_tool_call: &mut Option<&mut dyn FnMut(ToolCall)>,
) -> Result<(), ChatProviderError> {
    match part {
        StreamedMessagePart::Content(content) => {
            message.content.push(content);
        }
        StreamedMessagePart::ToolCall(tool_call) => {
            validate_tool_call_arguments(&tool_call)?;
            if message.tool_calls.is_none() {
                message.tool_calls = Some(Vec::new());
            }
            if let Some(list) = &mut message.tool_calls {
                list.push(tool_call.clone());
            }
            if let Some(cb) = on_tool_call.as_deref_mut() {
                cb(tool_call);
            }
        }
        StreamedMessagePart::ToolCallPart(_) => {
            return Err(ChatProviderError::new(
                ChatProviderErrorKind::MalformedToolCall,
                "Model produced a tool-call continuation without a complete tool call. Retrying the generation step before executing any tool.",
            ));
        }
    }
    Ok(())
}

fn validate_tool_call_arguments(tool_call: &ToolCall) -> Result<(), ChatProviderError> {
    let Some(arguments) = tool_call.function.arguments.as_ref() else {
        return Ok(());
    };
    if serde_json::from_str::<Value>(arguments).is_ok() {
        return Ok(());
    }
    Err(ChatProviderError::new(
        ChatProviderErrorKind::MalformedToolCall,
        format!(
            "Model produced malformed JSON arguments for tool `{}`. Retrying the generation step before executing the tool.",
            tool_call.function.name
        ),
    ))
}
