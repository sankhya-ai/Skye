//! Moonshot's search service (the upstream kimi-cli default when configured).

use std::collections::HashMap;

use serde::Deserialize;

use super::{Found, Hit, SearchParams, http_error, user_agent_headers};
use crate::config::MoonshotSearchConfig;
use crate::soul::toolset::get_current_tool_call_or_none;

pub struct Moonshot {
    base_url: String,
    api_key: String,
    custom_headers: HashMap<String, String>,
}

#[derive(Deserialize)]
struct MoonshotResult {
    title: String,
    url: String,
    snippet: String,
    #[serde(default)]
    content: String,
    #[serde(default)]
    date: String,
}

#[derive(Deserialize)]
struct MoonshotResponse {
    search_results: Vec<MoonshotResult>,
}

impl Moonshot {
    pub fn new(service: MoonshotSearchConfig) -> Self {
        Self {
            base_url: service.base_url,
            api_key: service.api_key,
            custom_headers: service.custom_headers.unwrap_or_default(),
        }
    }

    pub async fn search(&self, client: &reqwest::Client, params: &SearchParams) -> Result<Found, String> {
        if self.base_url.is_empty() || self.api_key.is_empty() {
            return Err("not configured".to_string());
        }
        let call = get_current_tool_call_or_none().ok_or("no tool call context")?;
        let bearer = format!("Bearer {}", self.api_key);
        let mut extra: Vec<(&str, &str)> = vec![("Authorization", bearer.as_str()), ("X-Msh-Tool-Call-Id", call.id.as_str())];
        extra.extend(self.custom_headers.iter().map(|(k, v)| (k.as_str(), v.as_str())));
        let resp = client
            .post(&self.base_url)
            .headers(user_agent_headers(&extra))
            .json(&serde_json::json!({
                "text_query": params.query,
                "limit": params.limit,
                "enable_page_crawling": params.include_content,
                "timeout_seconds": 30,
            }))
            .send()
            .await
            .map_err(|err| err.to_string())?;
        if !resp.status().is_success() {
            return Err(http_error(resp).await);
        }
        let payload: MoonshotResponse = resp.json().await.map_err(|err| err.to_string())?;
        Ok(Found {
            summary: String::new(),
            hits: payload
                .search_results
                .into_iter()
                .map(|r| Hit {
                    title: r.title,
                    url: r.url,
                    date: r.date,
                    snippet: r.snippet,
                    content: r.content,
                })
                .collect(),
        })
    }
}
