//! OpenRouter's `web` plugin on a chat completion: a summary with
//! `url_citation` annotations. Paid per request, so it comes last.

use std::collections::HashMap;

use serde_json::Value;

use super::{Found, Hit, SearchParams, http_error, user_agent_headers};
use crate::config::{LLMProvider, OpenRouterSearchConfig};

pub struct OpenRouter {
    base_url: String,
    api_key: String,
    model: String,
    custom_headers: HashMap<String, String>,
}

impl OpenRouter {
    /// An empty `base_url` or `api_key` falls back to the main provider's.
    pub fn new(service: OpenRouterSearchConfig, provider: Option<&LLMProvider>) -> Self {
        let base_url = if service.base_url.trim().is_empty() {
            provider.map(|p| p.base_url.clone()).unwrap_or_default()
        } else {
            service.base_url
        };
        let api_key = if service.api_key.trim().is_empty() {
            provider.map(|p| p.api_key.clone()).unwrap_or_default()
        } else {
            service.api_key
        };
        let mut custom_headers = provider
            .and_then(|p| p.custom_headers.clone())
            .unwrap_or_default();
        custom_headers.extend(service.custom_headers.unwrap_or_default());
        Self {
            base_url,
            api_key,
            model: service.model,
            custom_headers,
        }
    }

    pub async fn search(&self, client: &reqwest::Client, params: &SearchParams) -> Result<Found, String> {
        if self.base_url.trim().is_empty() || self.api_key.trim().is_empty() {
            return Err("not configured".to_string());
        }
        let bearer = format!("Bearer {}", self.api_key);
        let mut extra: Vec<(&str, &str)> = vec![("Authorization", bearer.as_str())];
        extra.extend(self.custom_headers.iter().map(|(k, v)| (k.as_str(), v.as_str())));
        let body = serde_json::json!({
            "model": self.model,
            "plugins": [{"id": "web", "max_results": params.limit.clamp(1, 20)}],
            "messages": [
                {
                    "role": "system",
                    "content": "You are a web search engine. Answer the query with a short factual summary of what the search results say, citing sources. Do not speculate beyond the results."
                },
                {"role": "user", "content": params.query},
            ],
            "max_tokens": 1500,
        });
        let resp = client
            .post(format!("{}/chat/completions", self.base_url.trim_end_matches('/')))
            .headers(user_agent_headers(&extra))
            .json(&body)
            .send()
            .await
            .map_err(|err| err.to_string())?;
        if !resp.status().is_success() {
            return Err(http_error(resp).await);
        }
        let payload: Value = resp.json().await.map_err(|err| err.to_string())?;
        let (summary, sources) = parse_openrouter_search(&payload);
        Ok(Found {
            summary,
            hits: sources
                .into_iter()
                .map(|s| Hit {
                    title: s.title,
                    url: s.url,
                    snippet: s.content.clone(),
                    content: s.content,
                    date: String::new(),
                })
                .collect(),
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WebSource {
    pub title: String,
    pub url: String,
    pub content: String,
}

/// The summary text and the de-duplicated `url_citation` sources of an
/// OpenRouter chat completion made with the `web` plugin.
pub fn parse_openrouter_search(payload: &Value) -> (String, Vec<WebSource>) {
    let message = payload.pointer("/choices/0/message");
    let summary = message
        .and_then(|m| m.get("content"))
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim()
        .to_string();
    let mut sources: Vec<WebSource> = Vec::new();
    let annotations = message
        .and_then(|m| m.get("annotations"))
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    for annotation in annotations {
        if annotation.get("type").and_then(Value::as_str) != Some("url_citation") {
            continue;
        }
        let citation = annotation.get("url_citation").unwrap_or(&annotation);
        let field = |name: &str| citation.get(name).and_then(Value::as_str).unwrap_or_default().to_string();
        let url = field("url");
        if url.is_empty() || sources.iter().any(|s| s.url == url) {
            continue;
        }
        sources.push(WebSource {
            title: field("title"),
            url,
            content: field("content"),
        });
    }
    (summary, sources)
}
