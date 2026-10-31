//! Parallel's search, through its hosted MCP endpoint. Anonymous calls are
//! free (rate-limited per IP); a key lifts the limits. One stateless JSON-RPC
//! `tools/call` per search: the endpoint does not require `initialize`.

use serde_json::{Value, json};

use super::{Found, Hit, SearchParams, http_error, user_agent_headers};

pub const DEFAULT_URL: &str = "https://search.parallel.ai/mcp";

pub struct Parallel {
    url: String,
    api_key: String,
}

impl Parallel {
    pub fn new(url: &str, api_key: &str) -> Self {
        let url = if url.trim().is_empty() { DEFAULT_URL } else { url.trim() };
        Self {
            url: url.to_string(),
            api_key: api_key.trim().to_string(),
        }
    }

    pub async fn search(&self, client: &reqwest::Client, params: &SearchParams) -> Result<Found, String> {
        let bearer = if self.api_key.is_empty() {
            String::new()
        } else {
            format!("Bearer {}", self.api_key)
        };
        let headers = user_agent_headers(&[
            ("Accept", "application/json, text/event-stream"),
            ("MCP-Protocol-Version", "2025-06-18"),
            ("Authorization", bearer.as_str()),
            ("x-api-key", self.api_key.as_str()),
        ]);
        let body = json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "web_search",
                "arguments": {"objective": params.query, "search_queries": [params.query]},
            },
        });
        let resp = client
            .post(&self.url)
            .headers(headers)
            .json(&body)
            .send()
            .await
            .map_err(|err| err.to_string())?;
        if !resp.status().is_success() {
            return Err(http_error(resp).await);
        }
        let text = resp.text().await.map_err(|err| err.to_string())?;
        parse_parallel(&text)
    }
}

/// The JSON-RPC reply, which may arrive as plain JSON or as one SSE event.
fn rpc_reply(text: &str) -> Result<Value, String> {
    if let Ok(value) = serde_json::from_str::<Value>(text) {
        return Ok(value);
    }
    text.lines()
        .filter_map(|line| line.strip_prefix("data:"))
        .filter_map(|data| serde_json::from_str::<Value>(data.trim()).ok())
        .find(|value| value.get("result").is_some() || value.get("error").is_some())
        .ok_or_else(|| "unreadable reply".to_string())
}

pub fn parse_parallel(text: &str) -> Result<Found, String> {
    let reply = rpc_reply(text)?;
    if let Some(error) = reply.get("error") {
        let message = error.get("message").and_then(Value::as_str).unwrap_or("error");
        return Err(message.to_string());
    }
    let result = reply.get("result").ok_or("no result")?;
    if result.get("isError").and_then(Value::as_bool) == Some(true) {
        let message = result
            .pointer("/content/0/text")
            .and_then(Value::as_str)
            .unwrap_or("search failed");
        return Err(message.chars().take(200).collect());
    }
    // Prefer the structured payload; fall back to the JSON text part.
    let payload = match result.get("structuredContent") {
        Some(value) if value.get("results").is_some() => value.clone(),
        _ => result
            .pointer("/content/0/text")
            .and_then(Value::as_str)
            .and_then(|text| serde_json::from_str::<Value>(text).ok())
            .ok_or("no results payload")?,
    };
    let hits = payload
        .get("results")
        .and_then(Value::as_array)
        .map(|items| items.iter().filter_map(hit).collect())
        .unwrap_or_default();
    Ok(Found {
        summary: String::new(),
        hits,
    })
}

fn hit(item: &Value) -> Option<Hit> {
    let url = item.get("url").and_then(Value::as_str)?.to_string();
    let excerpts: Vec<&str> = item
        .get("excerpts")
        .and_then(Value::as_array)
        .map(|list| list.iter().filter_map(Value::as_str).collect())
        .unwrap_or_default();
    Some(Hit {
        title: item.get("title").and_then(Value::as_str).unwrap_or_default().to_string(),
        url,
        date: item.get("publish_date").and_then(Value::as_str).unwrap_or_default().to_string(),
        snippet: excerpts.first().map(|s| s.replace('\n', " ")).unwrap_or_default(),
        content: excerpts.join("\n\n"),
    })
}
