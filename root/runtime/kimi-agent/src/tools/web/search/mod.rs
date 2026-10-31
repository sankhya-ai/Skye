//! `SearchWeb`: one tool, several search backends tried in order.
//!
//! * **Parallel** (`search.parallel.ai/mcp`) and **Firecrawl Keyless**
//!   (`api.firecrawl.dev`) answer without an API key. They are the default, so
//!   a local install searches the web for free. Both meter keyless use per IP,
//!   so a hosted install should set their keys.
//! * **OpenRouter**'s `web` plugin is paid; it is the last resort when enabled.
//! * **Moonshot**'s search service, when configured, is used alone.
//!
//! A backend that fails (rate limit, outage, empty answer) hands the query to
//! the next one; the tool fails only when all of them do.

mod firecrawl;
mod moonshot;
mod openrouter;
mod parallel;

use std::time::Duration;

use schemars::JsonSchema;
use serde::Deserialize;

use crate::config::WebSearchConfig;
use crate::tools::SkipThisTool;
use crate::tools::utils::{DEFAULT_MAX_CHARS, ToolResultBuilder, load_desc};
use kosong::tooling::{CallableTool2, ToolReturnValue};


const SEARCH_DESC: &str = include_str!("../../desc/web/search.md");
const SNIPPET_CHARS: usize = 600;
const BACKEND_TIMEOUT: Duration = Duration::from_secs(45);

#[derive(Debug, Deserialize, JsonSchema)]
pub struct SearchParams {
    #[schemars(description = "The query text to search for.")]
    pub query: String,
    #[serde(default = "default_search_limit")]
    #[schemars(
        description = "The number of results to return. Typically you do not need to set this value. When the results do not contain what you need, you probably want to give a more concrete query.",
        range(min = 1, max = 20),
        default = "default_search_limit"
    )]
    pub limit: i64,
    #[serde(default)]
    #[schemars(
        description = "Whether to include the content of the web pages in the results. It can consume a large amount of tokens when this is set to True. You should avoid enabling this when `limit` is set to a large value.",
        default
    )]
    pub include_content: bool,
}

fn default_search_limit() -> i64 {
    5
}

/// One search result, whatever backend produced it.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Hit {
    pub title: String,
    pub url: String,
    pub date: String,
    pub snippet: String,
    pub content: String,
}

/// What a backend found: an optional summary and the results.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Found {
    pub summary: String,
    pub hits: Vec<Hit>,
}

pub(crate) enum Backend {
    Parallel(parallel::Parallel),
    Firecrawl(firecrawl::Firecrawl),
    OpenRouter(openrouter::OpenRouter),
    Moonshot(moonshot::Moonshot),
}

impl Backend {
    fn name(&self) -> &'static str {
        match self {
            Backend::Parallel(_) => "Parallel",
            Backend::Firecrawl(_) => "Firecrawl",
            Backend::OpenRouter(_) => "OpenRouter",
            Backend::Moonshot(_) => "Moonshot",
        }
    }

    async fn search(&self, client: &reqwest::Client, params: &SearchParams) -> Result<Found, String> {
        match self {
            Backend::Parallel(b) => b.search(client, params).await,
            Backend::Firecrawl(b) => b.search(client, params).await,
            Backend::OpenRouter(b) => b.search(client, params).await,
            Backend::Moonshot(b) => b.search(client, params).await,
        }
    }
}

/// The backends from config, in the order they are tried.
pub(crate) fn backends_from_config(runtime: &crate::soul::agent::Runtime) -> Vec<Backend> {
    let services = &runtime.config.services;
    if let Some(service) = services.moonshot_search.clone() {
        return vec![Backend::Moonshot(moonshot::Moonshot::new(service))];
    }
    let mut backends = keyless_backends(services.web_search.as_ref());
    if let Some(service) = services.openrouter_search.clone() {
        let provider = runtime.llm.as_ref().and_then(|llm| llm.provider_config.as_ref());
        backends.push(Backend::OpenRouter(openrouter::OpenRouter::new(service, provider)));
    }
    backends
}

pub(crate) fn keyless_backends(config: Option<&WebSearchConfig>) -> Vec<Backend> {
    let Some(config) = config else {
        return Vec::new();
    };
    config
        .order
        .iter()
        .filter_map(|name| match name.trim().to_ascii_lowercase().as_str() {
            "parallel" => Some(Backend::Parallel(parallel::Parallel::new(
                &config.parallel_url,
                &config.parallel_api_key,
            ))),
            "firecrawl" => Some(Backend::Firecrawl(firecrawl::Firecrawl::new(
                &config.firecrawl_url,
                &config.firecrawl_api_key,
            ))),
            _ => None,
        })
        .collect()
}

pub struct SearchWeb {
    description: String,
    backends: Vec<Backend>,
    client: reqwest::Client,
}

impl SearchWeb {
    pub fn new(runtime: &crate::soul::agent::Runtime) -> Result<Self, SkipThisTool> {
        let backends = backends_from_config(runtime);
        if backends.is_empty() {
            return Err(SkipThisTool);
        }
        Ok(Self::with_backends(backends))
    }

    pub(crate) fn with_backends(backends: Vec<Backend>) -> Self {
        let client = reqwest::Client::builder()
            .timeout(BACKEND_TIMEOUT)
            .build()
            .unwrap_or_default();
        Self {
            description: load_desc(SEARCH_DESC, &[]),
            backends,
            client,
        }
    }

    pub(crate) async fn run(&self, params: &SearchParams) -> ToolReturnValue {
        let mut failures: Vec<String> = Vec::new();
        for backend in &self.backends {
            match backend.search(&self.client, params).await {
                Ok(found) if !found.hits.is_empty() || !found.summary.is_empty() => {
                    return render(&found, params, backend.name());
                }
                Ok(_) => failures.push(format!("{}: no results", backend.name())),
                Err(err) => failures.push(format!("{}: {err}", backend.name())),
            }
        }
        ToolResultBuilder::new(DEFAULT_MAX_CHARS, None).error(
            &format!(
                "Web search failed on every backend ({}). Try a different query, or fetch a known URL with FetchURL.",
                failures.join("; ")
            ),
            "Search failed",
        )
    }
}

fn render(found: &Found, params: &SearchParams, source: &str) -> ToolReturnValue {
    let mut builder = ToolResultBuilder::new(DEFAULT_MAX_CHARS, None);
    if !found.summary.is_empty() {
        builder.write(&format!("Summary: {}\n\n", found.summary));
    }
    let limit = params.limit.clamp(1, 20) as usize;
    for (idx, hit) in found.hits.iter().take(limit).enumerate() {
        if idx > 0 || !found.summary.is_empty() {
            builder.write("---\n\n");
        }
        builder.write(&format!("Title: {}\n", hit.title));
        if !hit.date.is_empty() {
            builder.write(&format!("Date: {}\n", hit.date));
        }
        builder.write(&format!("URL: {}\n", hit.url));
        if !hit.snippet.is_empty() {
            builder.write(&format!("Summary: {}\n", truncate_chars(&hit.snippet, SNIPPET_CHARS)));
        }
        if params.include_content && !hit.content.is_empty() {
            builder.write(&format!("\n{}\n", hit.content));
        }
        builder.write("\n");
    }
    builder.ok("", &format!("Searched via {source}"))
}

pub(crate) fn truncate_chars(text: &str, max: usize) -> String {
    if text.chars().count() <= max {
        return text.to_string();
    }
    let mut out: String = text.chars().take(max).collect();
    out.push('…');
    out
}

pub(crate) fn user_agent_headers(extra: &[(&str, &str)]) -> reqwest::header::HeaderMap {
    let mut headers = reqwest::header::HeaderMap::new();
    if let Ok(value) = crate::constant::user_agent().parse() {
        headers.insert(reqwest::header::USER_AGENT, value);
    }
    for (name, value) in extra {
        if value.is_empty() {
            continue;
        }
        if let (Ok(name), Ok(value)) = (
            reqwest::header::HeaderName::from_bytes(name.as_bytes()),
            value.parse(),
        ) {
            headers.insert(name, value);
        }
    }
    headers
}

/// A non-2xx reply as a short error: status plus the start of the body.
pub(crate) async fn http_error(resp: reqwest::Response) -> String {
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    format!("HTTP {status}: {}", truncate_chars(text.trim(), 200))
}

#[async_trait::async_trait]
impl CallableTool2 for SearchWeb {
    type Params = SearchParams;

    fn name(&self) -> &str {
        "SearchWeb"
    }

    fn description(&self) -> &str {
        &self.description
    }

    async fn call_typed(&self, params: Self::Params) -> ToolReturnValue {
        self.run(&params).await
    }
}

#[cfg(test)]
mod tests;
