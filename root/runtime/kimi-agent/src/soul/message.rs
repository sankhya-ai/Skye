use std::collections::HashSet;

use kosong::message::{
    ContentPart, ImageURLPart, Message, Role, TextPart, ThinkPart, VideoURLPart,
};
use kosong::tooling::{DisplayBlock, ToolOutput, ToolResult};

use crate::config::ModelCapability;

pub fn system(message: &str) -> ContentPart {
    ContentPart::Text(TextPart::new(format!("<system>{}</system>", message)))
}

pub fn tool_result_to_message(tool_result: &ToolResult) -> Message {
    let return_value = &tool_result.return_value;
    let mut content = Vec::new();
    if return_value.is_error {
        let mut message = return_value.message.clone();
        if is_tool_runtime_error(return_value.display.as_slice()) {
            if !message.is_empty() {
                message.push_str(
                    "\nThis is an unexpected error and the tool is probably not working.",
                );
            }
        }
        if !message.is_empty() {
            content.push(system(&format!("ERROR: {message}")));
        }
        append_output(&mut content, &return_value.output);
    } else {
        if !return_value.message.is_empty() {
            content.push(system(&return_value.message));
        }
        append_output(&mut content, &return_value.output);
        if content.is_empty() {
            content.push(system("Tool output is empty."));
        }
    }

    Message {
        role: Role::Tool,
        content,
        name: None,
        tool_calls: None,
        tool_call_id: Some(tool_result.tool_call_id.clone()),
        partial: None,
    }
}

pub fn sanitize_tool_call_arguments_for_history(message: &Message) -> Message {
    let mut sanitized = message.clone();
    let Some(tool_calls) = sanitized.tool_calls.as_mut() else {
        return sanitized;
    };

    for tool_call in tool_calls {
        let valid_json = tool_call
            .function
            .arguments
            .as_deref()
            .map(|args| {
                !args.trim().is_empty() && serde_json::from_str::<serde_json::Value>(args).is_ok()
            })
            .unwrap_or(false);
        if !valid_json {
            tool_call.function.arguments = Some("{}".to_string());
        }
    }
    sanitized
}

fn append_output(content: &mut Vec<ContentPart>, output: &ToolOutput) {
    match output {
        ToolOutput::Text(text) => {
            if !text.is_empty() {
                content.push(ContentPart::Text(TextPart::new(text)));
            }
        }
        ToolOutput::Parts(parts) => {
            content.extend(parts.clone());
        }
    }
}

fn is_tool_runtime_error(display: &[DisplayBlock]) -> bool {
    display.iter().any(|block| match block {
        DisplayBlock::Brief(brief) => brief.text == "Tool runtime error",
        _ => false,
    })
}

pub fn check_message(
    message: &Message,
    model_capabilities: &HashSet<ModelCapability>,
) -> HashSet<ModelCapability> {
    let mut needed = HashSet::new();
    for part in &message.content {
        match part {
            ContentPart::ImageUrl(ImageURLPart { .. }) => {
                needed.insert(ModelCapability::ImageIn);
            }
            ContentPart::VideoUrl(VideoURLPart { .. }) => {
                needed.insert(ModelCapability::VideoIn);
            }
            ContentPart::Think(ThinkPart { .. }) => {
                needed.insert(ModelCapability::Thinking);
            }
            _ => {}
        }
    }
    needed
        .difference(model_capabilities)
        .cloned()
        .collect::<HashSet<ModelCapability>>()
}
