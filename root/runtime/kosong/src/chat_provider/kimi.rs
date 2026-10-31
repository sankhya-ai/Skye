use std::any::Any;
use std::collections::{BTreeMap, VecDeque};
use std::env;
use std::pin::Pin;
use std::time::Duration;

use async_trait::async_trait;
use bytes::Bytes;
use futures::StreamExt;
use reqwest::header::{AUTHORIZATION, CONTENT_TYPE, HeaderMap, HeaderValue, USER_AGENT};
use reqwest::{Client, Url};
use serde_json::{Map, Value, json};
use tokio::time::Instant;
use uuid::Uuid;

use crate::chat_provider::{
    ChatProvider, ChatProviderError, ChatProviderErrorKind, StreamedMessage, ThinkingEffort,
    TokenUsage,
};
use crate::message::{
    ContentPart, Message, StreamedMessagePart, TextPart, ThinkPart, ToolCall, ToolCallFunction,
    VideoURL, VideoURLPart,
};
use crate::tooling::Tool;

const PROVIDER_RESPONSE_HEADERS_TIMEOUT: Duration = Duration::from_secs(20);
const PROVIDER_STREAM_IDLE_TIMEOUT: Duration = Duration::from_secs(30);
const PROVIDER_FIRST_SEMANTIC_OUTPUT_TIMEOUT: Duration = Duration::from_secs(30);
const PROVIDER_RESPONSE_HEADERS_TIMEOUT_ENV: &str =
    "KIMI_PROVIDER_RESPONSE_HEADERS_TIMEOUT_SECONDS";
const MAX_PROVIDER_RESPONSE_HEADERS_TIMEOUT_SECONDS: u64 = 900;
// A reasoning model emits its thinking before any semantic part, and 30s is not
// enough for a long one on a large prompt — the tutor's lesson turn hits this
// with deepseek-v4-pro and a whole chapter in context. The deadline still exists
// so a dead stream is caught; the host raises it for the models it runs.
const PROVIDER_FIRST_SEMANTIC_OUTPUT_TIMEOUT_ENV: &str =
    "KIMI_PROVIDER_FIRST_SEMANTIC_OUTPUT_TIMEOUT_SECONDS";
const MAX_PROVIDER_FIRST_SEMANTIC_OUTPUT_TIMEOUT_SECONDS: u64 = 900;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum ChatCompletionsProtocol {
    #[default]
    Kimi,
    OpenRouter,
}

#[derive(Clone)]
pub struct Kimi {
    model: String,
    api_key: String,
    base_url: Url,
    stream: bool,
    client: Client,
    generation_kwargs: Map<String, Value>,
    protocol: ChatCompletionsProtocol,
    response_headers_timeout: Duration,
}

impl Kimi {
    pub fn new(
        model: impl Into<String>,
        api_key: Option<String>,
        base_url: Option<String>,
        default_headers: Option<HeaderMap>,
    ) -> Result<Self, ChatProviderError> {
        let api_key = api_key
            .or_else(|| env::var("KIMI_API_KEY").ok())
            .ok_or_else(|| {
                ChatProviderError::new(
                    ChatProviderErrorKind::Other,
                    "The api_key client option or the KIMI_API_KEY environment variable is not set",
                )
            })?;
        let mut base_url = base_url
            .or_else(|| env::var("KIMI_BASE_URL").ok())
            .unwrap_or_else(|| "https://api.moonshot.ai/v1".to_string());
        if !base_url.ends_with('/') {
            base_url.push('/');
        }
        let base_url = Url::parse(&base_url).map_err(|err| {
            ChatProviderError::new(
                ChatProviderErrorKind::Other,
                format!("Invalid base URL: {err}"),
            )
        })?;
        let response_headers_timeout = provider_response_headers_timeout()?;

        let mut headers = HeaderMap::new();
        headers.insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
        headers.insert(USER_AGENT, HeaderValue::from_static("KimiCLI"));
        if let Some(extra) = default_headers {
            for (k, v) in extra.iter() {
                if let Some(value) = v.to_str().ok() {
                    headers.insert(
                        k,
                        HeaderValue::from_str(value).unwrap_or_else(|_| v.clone()),
                    );
                } else {
                    headers.insert(k, v.clone());
                }
            }
        }

        let client = Client::builder()
            .default_headers(headers)
            .build()
            .map_err(|err| ChatProviderError::new(ChatProviderErrorKind::Other, err.to_string()))?;

        Ok(Self {
            model: model.into(),
            api_key,
            base_url,
            stream: true,
            client,
            generation_kwargs: Map::new(),
            protocol: ChatCompletionsProtocol::Kimi,
            response_headers_timeout,
        })
    }

    pub fn with_protocol(mut self, protocol: ChatCompletionsProtocol) -> Self {
        self.protocol = protocol;
        self
    }

    pub fn with_stream(mut self, stream: bool) -> Self {
        self.stream = stream;
        self
    }

    pub fn is_streaming(&self) -> bool {
        self.stream
    }

    pub fn with_generation_kwargs(mut self, kwargs: Map<String, Value>) -> Self {
        for (k, v) in kwargs {
            self.generation_kwargs.insert(k, v);
        }
        self
    }

    pub fn with_extra_body(mut self, extra_body: Value) -> Self {
        let mut merged = Map::new();
        if let Some(Value::Object(existing)) = self.generation_kwargs.get("extra_body") {
            for (k, v) in existing {
                merged.insert(k.clone(), v.clone());
            }
        }
        if let Value::Object(extra) = extra_body {
            for (k, v) in extra {
                merged.insert(k, v);
            }
        }
        self.generation_kwargs
            .insert("extra_body".to_string(), Value::Object(merged));
        self
    }

    pub fn model_parameters(&self) -> Map<String, Value> {
        let mut params = Map::new();
        params.insert(
            "base_url".to_string(),
            Value::String(self.base_url.to_string()),
        );
        for (k, v) in &self.generation_kwargs {
            params.insert(k.clone(), v.clone());
        }
        params
    }

    fn request_body(
        &self,
        system_prompt: &str,
        tools: &[Tool],
        history: &[Message],
    ) -> Result<Map<String, Value>, ChatProviderError> {
        let mut messages = Vec::new();
        if !system_prompt.is_empty() {
            messages.push(json!({"role": "system", "content": system_prompt}));
        }
        for message in history {
            messages.push(convert_message(message, self.protocol)?);
        }

        let tool_defs = tools.iter().map(convert_tool).collect();
        let mut body = Map::new();
        body.insert("model".to_string(), Value::String(self.model.clone()));
        body.insert("messages".to_string(), Value::Array(messages));
        body.insert("tools".to_string(), Value::Array(tool_defs));
        body.insert("stream".to_string(), Value::Bool(self.stream));
        if self.stream {
            body.insert("stream_options".to_string(), json!({"include_usage": true}));
        }

        let mut generation_kwargs = Map::new();
        generation_kwargs.insert("max_tokens".to_string(), Value::from(32000));
        for (key, value) in &self.generation_kwargs {
            generation_kwargs.insert(key.clone(), value.clone());
        }
        let extra_body = match generation_kwargs.remove("extra_body") {
            Some(Value::Object(map)) => Some(map),
            _ => None,
        };
        if self.protocol == ChatCompletionsProtocol::OpenRouter {
            if let Some(effort) = generation_kwargs.remove("reasoning_effort") {
                generation_kwargs.insert("reasoning".to_string(), json!({"effort": effort}));
            }
            // (root; see VENDORED.md) Which of a model's providers to prefer:
            // `throughput`, `price` or `latency`. Unset keeps OpenRouter's own
            // balancing. Measured for one model, the fastest provider streamed
            // ~3x the tokens per second of the default one at ~2x the price.
            if let Ok(sort) = std::env::var("ROOT_OPENROUTER_SORT") {
                let sort = sort.trim().to_ascii_lowercase();
                if matches!(sort.as_str(), "throughput" | "price" | "latency") && !generation_kwargs.contains_key("provider") {
                    generation_kwargs.insert("provider".to_string(), json!({"sort": sort}));
                }
            }
        }
        for (key, value) in generation_kwargs {
            body.insert(key, value);
        }
        if let Some(extra_body) = extra_body {
            for (key, value) in extra_body {
                body.insert(key, value);
            }
        }
        Ok(body)
    }

    pub fn files(&self) -> KimiFiles {
        KimiFiles {
            client: self.client.clone(),
            api_key: self.api_key.clone(),
            base_url: self.base_url.clone(),
        }
    }
}

#[async_trait]
impl ChatProvider for Kimi {
    fn name(&self) -> &str {
        "kimi"
    }

    fn model_name(&self) -> &str {
        &self.model
    }

    fn thinking_effort(&self) -> Option<ThinkingEffort> {
        match self.generation_kwargs.get("reasoning_effort") {
            Some(Value::String(value)) => match value.as_str() {
                "low" => Some(ThinkingEffort::Low),
                "medium" => Some(ThinkingEffort::Medium),
                "high" => Some(ThinkingEffort::High),
                "xhigh" => Some(ThinkingEffort::XHigh),
                _ => Some(ThinkingEffort::Off),
            },
            _ => None,
        }
    }

    async fn generate(
        &self,
        system_prompt: &str,
        tools: &[Tool],
        history: &[Message],
    ) -> Result<Box<dyn StreamedMessage>, ChatProviderError> {
        let body = self.request_body(system_prompt, tools, history)?;

        let url = self
            .base_url
            .join("chat/completions")
            .map_err(|err| ChatProviderError::new(ChatProviderErrorKind::Other, err.to_string()))?;

        let request = self
            .client
            .post(url)
            .header(AUTHORIZATION, format!("Bearer {}", self.api_key))
            .json(&body)
            .send();
        let resp = tokio::time::timeout(self.response_headers_timeout, request)
            .await
            .map_err(|_| provider_timeout("waiting for response headers"))?
            .map_err(map_reqwest_error)?;

        if !resp.status().is_success() {
            let status = resp.status();
            let text = tokio::time::timeout(PROVIDER_STREAM_IDLE_TIMEOUT, resp.text())
                .await
                .map_err(|_| provider_timeout("reading provider error response"))?
                .map_err(map_reqwest_error)?;
            return Err(ChatProviderError::new(
                ChatProviderErrorKind::Status(status.as_u16()),
                format!("Model provider API error ({status}): {text}"),
            ));
        }

        if self.stream {
            Ok(Box::new(KimiStreamedMessage::new_stream(resp)))
        } else {
            let value: Value = tokio::time::timeout(PROVIDER_STREAM_IDLE_TIMEOUT, resp.json())
                .await
                .map_err(|_| provider_timeout("reading provider response"))?
                .map_err(map_reqwest_error)?;
            let (parts, message_id, usage) = parse_non_stream_response(&value)?;
            Ok(Box::new(KimiStreamedMessage::new_parts(
                parts, message_id, usage,
            )))
        }
    }

    fn with_generation_overrides(
        &self,
        overrides: &Map<String, Value>,
    ) -> Option<Box<dyn ChatProvider>> {
        Some(Box::new(self.clone().with_generation_kwargs(overrides.clone())))
    }

    fn with_thinking(&self, effort: ThinkingEffort) -> Box<dyn ChatProvider> {
        let mut kwargs = Map::new();
        if self.protocol == ChatCompletionsProtocol::OpenRouter {
            let reasoning_effort = match effort {
                ThinkingEffort::Off => "none",
                ThinkingEffort::Low => "low",
                ThinkingEffort::Medium => "medium",
                ThinkingEffort::High => "high",
                ThinkingEffort::XHigh => "xhigh",
            };
            kwargs.insert(
                "reasoning_effort".to_string(),
                Value::String(reasoning_effort.to_string()),
            );
            return Box::new(self.clone().with_generation_kwargs(kwargs));
        }

        let reasoning_effort = match effort {
            ThinkingEffort::Off => None,
            ThinkingEffort::Low => Some("low"),
            ThinkingEffort::Medium => Some("medium"),
            ThinkingEffort::High => Some("high"),
            ThinkingEffort::XHigh => Some("xhigh"),
        };
        if let Some(value) = reasoning_effort {
            kwargs.insert(
                "reasoning_effort".to_string(),
                Value::String(value.to_string()),
            );
        } else {
            kwargs.insert("reasoning_effort".to_string(), Value::Null);
        }

        let mut extra_body = Map::new();
        extra_body.insert(
            "thinking".to_string(),
            json!({"type": if matches!(effort, ThinkingEffort::Off) {"disabled"} else {"enabled"}}),
        );

        let provider = self
            .clone()
            .with_generation_kwargs(kwargs)
            .with_extra_body(Value::Object(extra_body));
        Box::new(provider)
    }

    fn as_any(&self) -> &dyn Any {
        self
    }
}

pub struct KimiFiles {
    client: Client,
    api_key: String,
    base_url: Url,
}

impl KimiFiles {
    pub async fn upload_video(
        &self,
        data: Vec<u8>,
        mime_type: &str,
    ) -> Result<VideoURLPart, ChatProviderError> {
        if !mime_type.starts_with("video/") {
            return Err(ChatProviderError::new(
                ChatProviderErrorKind::Other,
                format!("Expected a video mime type, got {mime_type}"),
            ));
        }
        let filename = guess_filename(mime_type);
        let form = reqwest::multipart::Form::new()
            .text("purpose", "video")
            .part(
                "file",
                reqwest::multipart::Part::bytes(data)
                    .file_name(filename)
                    .mime_str(mime_type)
                    .map_err(|err| {
                        ChatProviderError::new(ChatProviderErrorKind::Other, err.to_string())
                    })?,
            );

        let url = self
            .base_url
            .join("files")
            .map_err(|err| ChatProviderError::new(ChatProviderErrorKind::Other, err.to_string()))?;

        let resp = self
            .client
            .post(url)
            .header(AUTHORIZATION, format!("Bearer {}", self.api_key))
            .multipart(form)
            .send()
            .await
            .map_err(map_reqwest_error)?;

        if !resp.status().is_success() {
            let status = resp.status();
            let text = resp.text().await.unwrap_or_default();
            return Err(ChatProviderError::new(
                ChatProviderErrorKind::Status(status.as_u16()),
                format!("Model provider file upload error ({status}): {text}"),
            ));
        }

        let value: Value = resp.json().await.map_err(map_reqwest_error)?;
        let file_id = value.get("id").and_then(|v| v.as_str()).ok_or_else(|| {
            ChatProviderError::new(ChatProviderErrorKind::Other, "Missing file id")
        })?;
        Ok(VideoURLPart {
            kind: "video_url".to_string(),
            video_url: VideoURL {
                url: format!("ms://{file_id}"),
                id: None,
            },
        })
    }
}

pub struct KimiStreamedMessage {
    stream: Option<Pin<Box<dyn futures::Stream<Item = Result<Bytes, reqwest::Error>> + Send>>>,
    buffer: String,
    parts: VecDeque<StreamedMessagePart>,
    tool_calls: BTreeMap<usize, PendingToolCall>,
    id: Option<String>,
    usage: Option<TokenUsage>,
    first_semantic_output_deadline: Option<Instant>,
}

#[derive(Default)]
struct PendingToolCall {
    id: Option<String>,
    name: Option<String>,
    arguments: String,
}

impl KimiStreamedMessage {
    pub fn new_stream(resp: reqwest::Response) -> Self {
        let stream = resp.bytes_stream();
        Self {
            stream: Some(Box::pin(stream)),
            buffer: String::new(),
            parts: VecDeque::new(),
            tool_calls: BTreeMap::new(),
            id: None,
            usage: None,
            first_semantic_output_deadline: Some(
                Instant::now() + provider_first_semantic_output_timeout(),
            ),
        }
    }

    pub fn new_parts(
        parts: Vec<StreamedMessagePart>,
        id: Option<String>,
        usage: Option<TokenUsage>,
    ) -> Self {
        Self {
            stream: None,
            buffer: String::new(),
            parts: parts.into(),
            tool_calls: BTreeMap::new(),
            id,
            usage,
            first_semantic_output_deadline: None,
        }
    }

    fn ingest_chunk(&mut self, value: &Value) -> Result<(), ChatProviderError> {
        if let Some(id) = value.get("id").and_then(|v| v.as_str()) {
            self.id = Some(id.to_string());
        }
        let usage_value = value.get("usage").or_else(|| {
            value
                .get("choices")
                .and_then(|v| v.as_array())
                .and_then(|choices| choices.first())
                .and_then(|choice| choice.get("usage"))
        });
        if let Some(usage) = usage_value {
            if let Some(parsed) = parse_usage(usage) {
                self.usage = Some(parsed);
            }
        }
        if let Some(choices) = value.get("choices").and_then(|v| v.as_array()) {
            for choice in choices {
                if let Some(delta) = choice.get("delta") {
                    ingest_delta(delta, &mut self.parts, &mut self.tool_calls)?;
                }
            }
        }
        Ok(())
    }

    fn finish_tool_calls(&mut self) -> Result<(), ChatProviderError> {
        for (index, pending) in std::mem::take(&mut self.tool_calls) {
            let id = pending
                .id
                .ok_or_else(|| malformed_streamed_tool_call(index, "did not include an id"))?;
            let name = pending.name.ok_or_else(|| {
                malformed_streamed_tool_call(index, "did not include a function name")
            })?;
            self.parts
                .push_back(StreamedMessagePart::ToolCall(ToolCall {
                    kind: "function".to_string(),
                    id,
                    function: ToolCallFunction {
                        name,
                        arguments: if pending.arguments.is_empty() {
                            None
                        } else {
                            Some(pending.arguments)
                        },
                    },
                    extras: None,
                }));
        }
        Ok(())
    }
}

#[async_trait]
impl StreamedMessage for KimiStreamedMessage {
    async fn next_part(&mut self) -> Result<Option<StreamedMessagePart>, ChatProviderError> {
        loop {
            if let Some(part) = self.parts.pop_front() {
                self.first_semantic_output_deadline = None;
                return Ok(Some(part));
            }
            let stream = match &mut self.stream {
                Some(stream) => stream,
                None => return Ok(None),
            };
            let waiting_for_first_semantic_output = self.first_semantic_output_deadline.is_some();
            let stream_wait_timeout = if let Some(deadline) = self.first_semantic_output_deadline {
                if Instant::now() >= deadline {
                    return Err(provider_timeout("waiting for first semantic model output"));
                }
                PROVIDER_STREAM_IDLE_TIMEOUT.min(deadline.saturating_duration_since(Instant::now()))
            } else {
                PROVIDER_STREAM_IDLE_TIMEOUT
            };
            let next = tokio::time::timeout(stream_wait_timeout, stream.next())
                .await
                .map_err(|_| {
                    provider_timeout(if waiting_for_first_semantic_output {
                        "waiting for first semantic model output"
                    } else {
                        "waiting for streamed model output"
                    })
                })?;
            match next {
                Some(Ok(bytes)) => {
                    let chunk = String::from_utf8_lossy(&bytes);
                    self.buffer.push_str(&chunk);
                    while let Some(pos) = self.buffer.find('\n') {
                        let line = self.buffer[..pos].trim().to_string();
                        self.buffer = self.buffer[pos + 1..].to_string();
                        if line.is_empty() {
                            continue;
                        }
                        if let Some(data) = line.strip_prefix("data:") {
                            let data = data.strip_prefix(' ').unwrap_or(data);
                            if data.trim() == "[DONE]" {
                                self.finish_tool_calls()?;
                                self.stream = None;
                                if !self.buffer.trim().is_empty() {
                                    return Err(incomplete_provider_stream(
                                        "received data after the [DONE] marker",
                                    ));
                                }
                                break;
                            }
                            let value: Value = serde_json::from_str(data).map_err(|err| {
                                ChatProviderError::new(
                                    ChatProviderErrorKind::Other,
                                    err.to_string(),
                                )
                            })?;
                            self.ingest_chunk(&value)?;
                            if !self.parts.is_empty() || !self.tool_calls.is_empty() {
                                self.first_semantic_output_deadline = None;
                            }
                        }
                    }
                }
                Some(Err(err)) => return Err(map_reqwest_error(err)),
                None => {
                    self.stream = None;
                    return Err(incomplete_provider_stream("ended before the [DONE] marker"));
                }
            }
        }
    }

    fn id(&self) -> Option<String> {
        self.id.clone()
    }

    fn usage(&self) -> Option<TokenUsage> {
        self.usage.clone()
    }
}

fn convert_message(
    message: &Message,
    protocol: ChatCompletionsProtocol,
) -> Result<Value, ChatProviderError> {
    let mut reasoning_content = String::new();
    let mut reasoning_details = Vec::new();
    let mut content_parts = Vec::new();
    for part in &message.content {
        match part {
            ContentPart::Think(think) => {
                reasoning_content.push_str(&think.think);
                if let Some(encoded) = &think.encrypted {
                    let value: Value = serde_json::from_str(encoded).map_err(|err| {
                        ChatProviderError::new(
                            ChatProviderErrorKind::Other,
                            format!("Invalid preserved reasoning details: {err}"),
                        )
                    })?;
                    match value {
                        Value::Array(items) => reasoning_details.extend(items),
                        item => reasoning_details.push(item),
                    }
                }
            }
            _ => content_parts.push(part.clone()),
        }
    }

    let payload = serde_json::to_value(Message {
        role: message.role.clone(),
        content: content_parts,
        name: message.name.clone(),
        tool_calls: message.tool_calls.clone(),
        tool_call_id: message.tool_call_id.clone(),
        partial: message.partial,
    })
    .map_err(|err| ChatProviderError::new(ChatProviderErrorKind::Other, err.to_string()))?;

    let mut payload = strip_nulls(payload);
    if !reasoning_details.is_empty() {
        if let Value::Object(map) = &mut payload {
            map.insert(
                "reasoning_details".to_string(),
                Value::Array(reasoning_details),
            );
        }
    } else if !reasoning_content.is_empty() {
        if let Value::Object(map) = &mut payload {
            map.insert(
                if protocol == ChatCompletionsProtocol::OpenRouter {
                    "reasoning".to_string()
                } else {
                    "reasoning_content".to_string()
                },
                Value::String(reasoning_content),
            );
        }
    }
    Ok(payload)
}

fn strip_nulls(value: Value) -> Value {
    match value {
        Value::Object(map) => {
            let mut cleaned = serde_json::Map::new();
            for (key, val) in map {
                if val.is_null() {
                    continue;
                }
                cleaned.insert(key, strip_nulls(val));
            }
            Value::Object(cleaned)
        }
        Value::Array(items) => Value::Array(items.into_iter().map(strip_nulls).collect()),
        other => other,
    }
}

fn convert_tool(tool: &Tool) -> Value {
    if tool.name.starts_with('$') {
        json!({
            "type": "builtin_function",
            "function": {"name": tool.name},
        })
    } else {
        json!({
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            }
        })
    }
}

fn parse_non_stream_response(
    value: &Value,
) -> Result<(Vec<StreamedMessagePart>, Option<String>, Option<TokenUsage>), ChatProviderError> {
    let message_id = value
        .get("id")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string());
    let usage = value.get("usage").and_then(parse_usage);

    let choices = value
        .get("choices")
        .and_then(|v| v.as_array())
        .ok_or_else(|| {
            ChatProviderError::new(ChatProviderErrorKind::Other, "Missing choices in response")
        })?;
    if choices.is_empty() {
        return Err(ChatProviderError::new(
            ChatProviderErrorKind::EmptyResponse,
            "The API returned an empty response.",
        ));
    }
    let message = choices[0].get("message").ok_or_else(|| {
        ChatProviderError::new(ChatProviderErrorKind::Other, "Missing message in response")
    })?;

    let mut parts = Vec::new();
    let reasoning_details = message
        .get("reasoning_details")
        .filter(|value| !value.is_null());
    if let Some(reasoning) = message
        .get("reasoning")
        .or_else(|| message.get("reasoning_content"))
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
    {
        parts.push(StreamedMessagePart::Content(ContentPart::Think(
            ThinkPart {
                kind: "think".to_string(),
                think: reasoning.to_string(),
                encrypted: reasoning_details.map(Value::to_string),
            },
        )));
    } else if let Some(details) = reasoning_details {
        parts.push(StreamedMessagePart::Content(ContentPart::Think(
            ThinkPart {
                kind: "think".to_string(),
                think: String::new(),
                encrypted: Some(details.to_string()),
            },
        )));
    }
    if let Some(content) = message.get("content").and_then(|v| v.as_str()) {
        if !content.is_empty() {
            parts.push(StreamedMessagePart::Content(ContentPart::Text(
                TextPart::new(content),
            )));
        }
    }
    if let Some(tool_calls) = message.get("tool_calls").and_then(|v| v.as_array()) {
        for tool_call in tool_calls {
            if let Some(call) = parse_tool_call(tool_call) {
                parts.push(StreamedMessagePart::ToolCall(call));
            }
        }
    }

    Ok((parts, message_id, usage))
}

fn ingest_delta(
    delta: &Value,
    parts: &mut VecDeque<StreamedMessagePart>,
    pending_tool_calls: &mut BTreeMap<usize, PendingToolCall>,
) -> Result<(), ChatProviderError> {
    let reasoning_details = delta
        .get("reasoning_details")
        .filter(|value| !value.is_null());
    if let Some(reasoning) = delta
        .get("reasoning")
        .or_else(|| delta.get("reasoning_content"))
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
    {
        parts.push_back(StreamedMessagePart::Content(ContentPart::Think(
            ThinkPart {
                kind: "think".to_string(),
                think: reasoning.to_string(),
                encrypted: reasoning_details.map(Value::to_string),
            },
        )));
    } else if let Some(details) = reasoning_details {
        parts.push_back(StreamedMessagePart::Content(ContentPart::Think(
            ThinkPart {
                kind: "think".to_string(),
                think: String::new(),
                encrypted: Some(details.to_string()),
            },
        )));
    }
    if let Some(content) = delta
        .get("content")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
    {
        parts.push_back(StreamedMessagePart::Content(ContentPart::Text(
            TextPart::new(content),
        )));
    }
    if let Some(tool_calls) = delta.get("tool_calls").and_then(|v| v.as_array()) {
        for tool_call in tool_calls {
            ingest_tool_call_delta(tool_call, pending_tool_calls)?;
        }
    }
    Ok(())
}

fn parse_tool_call(tool_call: &Value) -> Option<ToolCall> {
    let function = tool_call.get("function")?;
    let name = function.get("name")?.as_str()?.to_string();
    let arguments = function
        .get("arguments")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string());
    let id = tool_call
        .get("id")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .unwrap_or_else(|| Uuid::new_v4().to_string());
    Some(ToolCall {
        kind: "function".to_string(),
        id,
        function: ToolCallFunction { name, arguments },
        extras: None,
    })
}

fn ingest_tool_call_delta(
    tool_call: &Value,
    pending_tool_calls: &mut BTreeMap<usize, PendingToolCall>,
) -> Result<(), ChatProviderError> {
    let index = tool_call
        .get("index")
        .and_then(Value::as_u64)
        .and_then(|value| usize::try_from(value).ok())
        .ok_or_else(|| {
            ChatProviderError::new(
                ChatProviderErrorKind::MalformedToolCall,
                "Streamed tool call did not include a valid index",
            )
        })?;
    let pending = pending_tool_calls.entry(index).or_default();

    if let Some(id) = tool_call.get("id").and_then(Value::as_str) {
        match pending.id.as_deref() {
            Some(existing) if existing != id => {
                return Err(malformed_streamed_tool_call(
                    index,
                    "changed id while streaming",
                ));
            }
            None => pending.id = Some(id.to_string()),
            _ => {}
        }
    }

    if let Some(function) = tool_call.get("function") {
        if let Some(name) = function
            .get("name")
            .and_then(Value::as_str)
            .filter(|name| !name.is_empty())
        {
            match pending.name.as_deref() {
                Some(existing) if existing != name => {
                    return Err(malformed_streamed_tool_call(
                        index,
                        "changed function name while streaming",
                    ));
                }
                None => pending.name = Some(name.to_string()),
                _ => {}
            }
        }
        if let Some(arguments) = function.get("arguments").and_then(Value::as_str) {
            pending.arguments.push_str(arguments);
        }
    }
    Ok(())
}

fn malformed_streamed_tool_call(index: usize, reason: &str) -> ChatProviderError {
    ChatProviderError::new(
        ChatProviderErrorKind::MalformedToolCall,
        format!("Streamed tool call at index {index} {reason}"),
    )
}

fn parse_usage(value: &Value) -> Option<TokenUsage> {
    let prompt_tokens = value.get("prompt_tokens")?.as_i64()?;
    let completion_tokens = value.get("completion_tokens")?.as_i64()?;
    let mut cached = 0i64;
    if let Some(cached_tokens) = value.get("cached_tokens").and_then(|v| v.as_i64()) {
        cached = cached_tokens;
    } else if let Some(details) = value.get("prompt_tokens_details") {
        if let Some(cached_tokens) = details.get("cached_tokens").and_then(|v| v.as_i64()) {
            cached = cached_tokens;
        }
    }
    let input_other = if prompt_tokens >= cached {
        prompt_tokens - cached
    } else {
        0
    };
    Some(TokenUsage {
        input_other,
        output: completion_tokens,
        input_cache_read: cached,
        input_cache_creation: 0,
        cost_usd: value.get("cost").and_then(|cost| cost.as_f64()),
    })
}

/// How long to wait for the first semantic part before declaring the provider
/// stalled. Reads `KIMI_PROVIDER_FIRST_SEMANTIC_OUTPUT_TIMEOUT_SECONDS`; an
/// unset, malformed, or out-of-range value keeps the built-in default, because a
/// bad env var must not be the reason a turn hangs forever.
fn provider_first_semantic_output_timeout() -> Duration {
    let Ok(value) = env::var(PROVIDER_FIRST_SEMANTIC_OUTPUT_TIMEOUT_ENV) else {
        return PROVIDER_FIRST_SEMANTIC_OUTPUT_TIMEOUT;
    };
    match value.trim().parse::<u64>() {
        Ok(seconds)
            if (1..=MAX_PROVIDER_FIRST_SEMANTIC_OUTPUT_TIMEOUT_SECONDS).contains(&seconds) =>
        {
            Duration::from_secs(seconds)
        }
        _ => PROVIDER_FIRST_SEMANTIC_OUTPUT_TIMEOUT,
    }
}

fn provider_response_headers_timeout() -> Result<Duration, ChatProviderError> {
    let configured = match env::var(PROVIDER_RESPONSE_HEADERS_TIMEOUT_ENV) {
        Ok(value) => Some(value),
        Err(env::VarError::NotPresent) => None,
        Err(env::VarError::NotUnicode(_)) => {
            return Err(ChatProviderError::new(
                ChatProviderErrorKind::Other,
                format!("{PROVIDER_RESPONSE_HEADERS_TIMEOUT_ENV} must be valid UTF-8"),
            ));
        }
    };
    parse_provider_response_headers_timeout(configured.as_deref())
}

fn parse_provider_response_headers_timeout(
    configured: Option<&str>,
) -> Result<Duration, ChatProviderError> {
    let Some(value) = configured else {
        return Ok(PROVIDER_RESPONSE_HEADERS_TIMEOUT);
    };
    let seconds = value.trim().parse::<u64>().map_err(|_| {
        ChatProviderError::new(
            ChatProviderErrorKind::Other,
            format!(
                "{PROVIDER_RESPONSE_HEADERS_TIMEOUT_ENV} must be an integer from 1 to {MAX_PROVIDER_RESPONSE_HEADERS_TIMEOUT_SECONDS}"
            ),
        )
    })?;
    if !(1..=MAX_PROVIDER_RESPONSE_HEADERS_TIMEOUT_SECONDS).contains(&seconds) {
        return Err(ChatProviderError::new(
            ChatProviderErrorKind::Other,
            format!(
                "{PROVIDER_RESPONSE_HEADERS_TIMEOUT_ENV} must be an integer from 1 to {MAX_PROVIDER_RESPONSE_HEADERS_TIMEOUT_SECONDS}"
            ),
        ));
    }
    Ok(Duration::from_secs(seconds))
}

fn provider_timeout(stage: &str) -> ChatProviderError {
    ChatProviderError::new(
        ChatProviderErrorKind::Timeout,
        format!("Model provider timed out while {stage}"),
    )
}

fn incomplete_provider_stream(reason: &str) -> ChatProviderError {
    ChatProviderError::new(
        ChatProviderErrorKind::Connection,
        format!("Model provider returned an incomplete stream: {reason}"),
    )
}

fn guess_filename(mime_type: &str) -> String {
    let extension = match mime_type {
        "video/mp4" => ".mp4",
        "video/quicktime" => ".mov",
        "video/webm" => ".webm",
        _ => ".bin",
    };
    format!("upload{}", extension)
}

fn map_reqwest_error(err: reqwest::Error) -> ChatProviderError {
    if err.is_timeout() {
        ChatProviderError::new(ChatProviderErrorKind::Timeout, err.to_string())
    } else if err.is_connect() {
        ChatProviderError::new(ChatProviderErrorKind::Connection, err.to_string())
    } else {
        ChatProviderError::new(ChatProviderErrorKind::Other, err.to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::message::Role;

    fn streamed_message(chunks: Vec<String>) -> KimiStreamedMessage {
        let stream = futures::stream::iter(
            chunks
                .into_iter()
                .map(|chunk| Ok::<Bytes, reqwest::Error>(Bytes::from(chunk))),
        );
        KimiStreamedMessage {
            stream: Some(Box::pin(stream)),
            buffer: String::new(),
            parts: VecDeque::new(),
            tool_calls: BTreeMap::new(),
            id: None,
            usage: None,
            first_semantic_output_deadline: Some(
                Instant::now() + provider_first_semantic_output_timeout(),
            ),
        }
    }

    fn openrouter_provider(kwargs: Map<String, Value>) -> Kimi {
        Kimi::new(
            "openrouter/free",
            Some("test-key".to_string()),
            Some("https://openrouter.ai/api/v1".to_string()),
            None,
        )
        .expect("provider")
        .with_protocol(ChatCompletionsProtocol::OpenRouter)
        .with_generation_kwargs(kwargs)
    }

    #[test]
    fn openrouter_reasoning_off_uses_gateway_contract() {
        let mut kwargs = Map::new();
        kwargs.insert("reasoning_effort".to_string(), json!("none"));
        let provider = openrouter_provider(kwargs);

        let body = provider.request_body("", &[], &[]).expect("request body");

        assert_eq!(body.get("reasoning"), Some(&json!({"effort": "none"})));
        assert!(!body.contains_key("reasoning_effort"));
        assert!(!body.contains_key("thinking"));
    }

    #[test]
    fn openrouter_provider_sort_comes_from_the_environment() {
        // One test owns the variable, so parallel tests never see it half-set.
        // SAFETY: no other test reads or writes ROOT_OPENROUTER_SORT.
        unsafe { std::env::set_var("ROOT_OPENROUTER_SORT", "Throughput") };
        let sorted = openrouter_provider(Map::new()).request_body("", &[], &[]).expect("body");
        unsafe { std::env::set_var("ROOT_OPENROUTER_SORT", "fastest-please") };
        let unknown = openrouter_provider(Map::new()).request_body("", &[], &[]).expect("body");
        unsafe { std::env::remove_var("ROOT_OPENROUTER_SORT") };
        let unset = openrouter_provider(Map::new()).request_body("", &[], &[]).expect("body");
        assert_eq!(sorted.get("provider"), Some(&json!({"sort": "throughput"})));
        assert!(!unknown.contains_key("provider"));
        assert!(!unset.contains_key("provider"));
    }

    #[test]
    fn openrouter_reasoning_details_round_trip_without_rewriting() {
        let details = json!([
            {"type": "reasoning.encrypted", "id": "r1", "data": "opaque"},
            {"type": "reasoning.summary", "id": "r2", "summary": "plan"}
        ]);
        let response = json!({
            "id": "generation-1",
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": null,
                    "reasoning": "private reasoning",
                    "reasoning_details": details,
                    "tool_calls": [{
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "page", "arguments": "{\"action\":\"observe\"}"}
                    }]
                }
            }]
        });
        let (parts, _, _) = parse_non_stream_response(&response).expect("response");
        let mut message = Message::new(Role::Assistant, Vec::new());
        for part in parts {
            match part {
                StreamedMessagePart::Content(content) => message.content.push(content),
                StreamedMessagePart::ToolCall(call) => {
                    message.tool_calls.get_or_insert_with(Vec::new).push(call);
                }
                StreamedMessagePart::ToolCallPart(_) => {}
            }
        }

        let encoded = convert_message(&message, ChatCompletionsProtocol::OpenRouter)
            .expect("continued message");

        assert_eq!(encoded.get("reasoning_details"), Some(&details));
        assert!(encoded.get("reasoning").is_none());
        assert_eq!(
            encoded.get("tool_calls"),
            response.pointer("/choices/0/message/tool_calls")
        );
    }

    #[test]
    fn provider_timeout_is_retryable_timeout_kind() {
        let error = provider_timeout("waiting for output");
        assert!(matches!(error.kind, ChatProviderErrorKind::Timeout));
    }

    #[test]
    fn provider_response_headers_timeout_defaults_and_validates_override() {
        assert_eq!(
            parse_provider_response_headers_timeout(None).expect("default timeout"),
            Duration::from_secs(20)
        );
        assert_eq!(
            parse_provider_response_headers_timeout(Some("120")).expect("test timeout"),
            Duration::from_secs(120)
        );
        assert!(parse_provider_response_headers_timeout(Some("0")).is_err());
        assert!(parse_provider_response_headers_timeout(Some("901")).is_err());
        assert!(parse_provider_response_headers_timeout(Some("slow")).is_err());
    }

    #[tokio::test]
    async fn streamed_response_requires_done_marker_before_clean_eof() {
        let chunk = format!(
            "data: {}\n\n",
            json!({"choices": [{"delta": {"content": "partial"}}]})
        );
        let mut message = streamed_message(vec![chunk]);

        let first = message
            .next_part()
            .await
            .expect("first part")
            .expect("text part");
        assert!(matches!(
            first,
            StreamedMessagePart::Content(ContentPart::Text(ref text)) if text.text == "partial"
        ));

        let error = message
            .next_part()
            .await
            .expect_err("clean EOF without [DONE] must fail");
        assert!(matches!(error.kind, ChatProviderErrorKind::Connection));
    }

    #[tokio::test]
    async fn provider_heartbeats_cannot_extend_first_semantic_output_deadline() {
        let heartbeats = futures::stream::unfold((), |_| async {
            tokio::time::sleep(Duration::from_millis(1)).await;
            Some((
                Ok::<Bytes, reqwest::Error>(Bytes::from(": provider heartbeat\n\n")),
                (),
            ))
        });
        let mut message = KimiStreamedMessage {
            stream: Some(Box::pin(heartbeats)),
            buffer: String::new(),
            parts: VecDeque::new(),
            tool_calls: BTreeMap::new(),
            id: None,
            usage: None,
            first_semantic_output_deadline: Some(Instant::now() + Duration::from_millis(10)),
        };

        let error = tokio::time::timeout(Duration::from_millis(100), message.next_part())
            .await
            .expect("the absolute first-output deadline must fire")
            .expect_err("heartbeats are not semantic model output");

        assert!(matches!(error.kind, ChatProviderErrorKind::Timeout));
        assert!(error.message.contains("first semantic model output"));
    }

    #[tokio::test]
    async fn streamed_response_accepts_data_lines_with_or_without_one_space() {
        let first = json!({"choices": [{"delta": {"content": "with"}}]});
        let second = json!({"choices": [{"delta": {"content": "out"}}]});
        let chunk = format!("data: {first}\n\ndata:{second}\n\ndata:[DONE]\n\n");
        let mut message = streamed_message(vec![chunk]);

        let with_space = message
            .next_part()
            .await
            .expect("with-space line")
            .expect("with-space content");
        let without_space = message
            .next_part()
            .await
            .expect("without-space line")
            .expect("without-space content");

        assert!(matches!(
            with_space,
            StreamedMessagePart::Content(ContentPart::Text(ref text)) if text.text == "with"
        ));
        assert!(matches!(
            without_space,
            StreamedMessagePart::Content(ContentPart::Text(ref text)) if text.text == "out"
        ));
        assert!(
            message
                .next_part()
                .await
                .expect("complete stream")
                .is_none()
        );
    }

    #[tokio::test]
    async fn streamed_parallel_tool_calls_are_assembled_by_index() {
        let start = json!({
            "choices": [{
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "tool_1",
                            "function": {"name": "WriteFile", "arguments": "{\"path\":"}
                        },
                        {
                            "index": 1,
                            "id": "tool_2",
                            "function": {"name": "ReadFile", "arguments": "{\"path\":"}
                        }
                    ]
                }
            }]
        });
        let interleaved_arguments = json!({
            "choices": [{
                "delta": {
                    "tool_calls": [
                        {"index": 1, "function": {"arguments": "\"b.txt\"}"}},
                        {"index": 0, "function": {"arguments": "\"a.txt\"}"}}
                    ]
                }
            }]
        });
        let chunk = format!("data: {start}\n\ndata: {interleaved_arguments}\n\ndata: [DONE]\n\n");
        let mut message = streamed_message(vec![chunk]);

        let first = message
            .next_part()
            .await
            .expect("first tool call")
            .expect("first tool call part");
        let second = message
            .next_part()
            .await
            .expect("second tool call")
            .expect("second tool call part");

        let StreamedMessagePart::ToolCall(first) = first else {
            panic!("expected complete first tool call");
        };
        let StreamedMessagePart::ToolCall(second) = second else {
            panic!("expected complete second tool call");
        };
        assert_eq!(first.id, "tool_1");
        assert_eq!(first.function.name, "WriteFile");
        assert_eq!(
            first.function.arguments.as_deref(),
            Some("{\"path\":\"a.txt\"}")
        );
        assert_eq!(second.id, "tool_2");
        assert_eq!(second.function.name, "ReadFile");
        assert_eq!(
            second.function.arguments.as_deref(),
            Some("{\"path\":\"b.txt\"}")
        );
        assert!(
            message
                .next_part()
                .await
                .expect("complete stream")
                .is_none()
        );
    }
}
