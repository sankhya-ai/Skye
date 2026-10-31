//! Firecrawl search. Keyless since June 2026: `POST /v2/search` without an
//! `Authorization` header gets 1,000 free credits a month per IP; a key lifts
//! that. With `include_content`, each result carries the page as markdown.

use serde_json::{Value, json};

use super::{Found, Hit, SearchParams, http_error, user_agent_headers};

pub const DEFAULT_URL: &str = "https://api.firecrawl.dev";

pub struct Firecrawl {
    base_url: String,
    api_key: String,
}

impl Firecrawl {
    pub fn new(base_url: &str, api_key: &str) -> Self {
        let base = if base_url.trim().is_empty() { DEFAULT_URL } else { base_url.trim() };
        Self {
            base_url: base.trim_end_matches('/').to_string(),
            api_key: api_key.trim().to_string(),
        }
    }

    pub async fn search(&self, client: &reqwest::Client, params: &SearchParams) -> Result<Found, String> {
        let bearer = if self.api_key.is_empty() {
            String::new()
        } else {
            format!("Bearer {}", self.api_key)
        };
        let headers = user_agent_headers(&[("Authorization", bearer.as_str())]);
        let mut body = json!({"query": params.query, "limit": params.limit.clamp(1, 20)});
        if params.include_content {
            body["scrapeOptions"] = json!({"formats": ["markdown"], "onlyMainContent": true});
        }
        let resp = client
            .post(format!("{}/v2/search", self.base_url))
            .headers(headers)
            .json(&body)
            .send()
            .await
            .map_err(|err| err.to_string())?;
        if !resp.status().is_success() {
            return Err(http_error(resp).await);
        }
        let payload: Value = resp.json().await.map_err(|err| err.to_string())?;
        parse_firecrawl(&payload)
    }
}

pub fn parse_firecrawl(payload: &Value) -> Result<Found, String> {
    if payload.get("success").and_then(Value::as_bool) == Some(false) {
        let error = payload.get("error").and_then(Value::as_str).unwrap_or("search failed");
        return Err(error.lines().next().unwrap_or(error).to_string());
    }
    // v2 nests results by source (`data.web`); v1 returned `data` as a list.
    let data = payload.get("data");
    let items = data
        .and_then(|d| d.get("web"))
        .or(data)
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let hits = items
        .iter()
        .filter_map(|item| {
            let url = item
                .get("url")
                .or_else(|| item.pointer("/metadata/sourceURL"))
                .and_then(Value::as_str)?
                .to_string();
            Some(Hit {
                title: item
                    .get("title")
                    .or_else(|| item.pointer("/metadata/title"))
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string(),
                url,
                date: String::new(),
                snippet: item
                    .get("description")
                    .or_else(|| item.pointer("/metadata/description"))
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string(),
                content: item.get("markdown").and_then(Value::as_str).unwrap_or_default().to_string(),
            })
        })
        .collect();
    Ok(Found {
        summary: String::new(),
        hits,
    })
}
