use serde_json::json;
use wiremock::matchers::{header_exists, method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

use super::firecrawl::{Firecrawl, parse_firecrawl};
use super::openrouter::parse_openrouter_search;
use super::parallel::{Parallel, parse_parallel};
use super::*;

fn params(query: &str) -> SearchParams {
    SearchParams {
        query: query.to_string(),
        limit: 5,
        include_content: false,
    }
}

fn parallel_reply() -> serde_json::Value {
    let inner = json!({"search_id": "s1", "results": [
        {"url": "https://tokio.rs/", "title": "Tokio", "publish_date": null, "excerpts": ["An async runtime.\nFor Rust.", "More."]},
        {"url": "https://docs.rs/tokio", "title": "tokio - Docs.rs", "publish_date": "2026-09-01", "excerpts": []}
    ]});
    json!({"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": inner.to_string()}]}})
}

#[test]
fn parallel_results_become_hits() {
    let found = parse_parallel(&parallel_reply().to_string()).expect("parse");
    assert_eq!(found.hits.len(), 2);
    assert_eq!(found.hits[0].snippet, "An async runtime. For Rust.");
    assert_eq!(found.hits[0].content, "An async runtime.\nFor Rust.\n\nMore.");
    assert_eq!(found.hits[1].date, "2026-09-01");
}

#[test]
fn parallel_reads_sse_and_reports_errors() {
    let sse = format!("event: message\ndata: {}\n\n", parallel_reply());
    assert_eq!(parse_parallel(&sse).expect("sse").hits.len(), 2);
    let error = json!({"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "rate limited"}});
    assert_eq!(parse_parallel(&error.to_string()).unwrap_err(), "rate limited");
    let tool_error = json!({"jsonrpc": "2.0", "id": 1, "result": {"isError": true, "content": [{"type": "text", "text": "quota exceeded"}]}});
    assert_eq!(parse_parallel(&tool_error.to_string()).unwrap_err(), "quota exceeded");
}

#[test]
fn firecrawl_reads_v2_and_v1_shapes_and_keyless_limits() {
    let v2 = json!({"success": true, "data": {"web": [{"url": "https://a.test", "title": "A", "description": "alpha", "markdown": "# A"}]}});
    let found = parse_firecrawl(&v2).expect("v2");
    assert_eq!(found.hits[0].snippet, "alpha");
    assert_eq!(found.hits[0].content, "# A");
    let v1 = json!({"success": true, "data": [{"metadata": {"sourceURL": "https://b.test", "title": "B"}}]});
    assert_eq!(parse_firecrawl(&v1).expect("v1").hits[0].url, "https://b.test");
    let limited = json!({"success": false, "error": "You've hit Firecrawl's keyless free tier rate limit.\nThen authenticate", "reason": "credits"});
    assert_eq!(parse_firecrawl(&limited).unwrap_err(), "You've hit Firecrawl's keyless free tier rate limit.");
}

#[test]
fn keyless_backends_follow_the_configured_order() {
    let config = crate::config::WebSearchConfig {
        order: vec!["firecrawl".into(), "bogus".into(), "Parallel".into()],
        ..Default::default()
    };
    let names: Vec<&str> = keyless_backends(Some(&config)).iter().map(Backend::name).collect();
    assert_eq!(names, ["Firecrawl", "Parallel"]);
    assert!(keyless_backends(None).is_empty());
    let default: Vec<&str> = keyless_backends(Some(&Default::default())).iter().map(Backend::name).collect();
    assert_eq!(default, ["Parallel", "Firecrawl"]);
}

#[tokio::test]
async fn a_rate_limited_backend_falls_through_to_the_next() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/mcp"))
        .respond_with(ResponseTemplate::new(429).set_body_string("slow down"))
        .mount(&server)
        .await;
    Mock::given(method("POST"))
        .and(path("/v2/search"))
        .respond_with(ResponseTemplate::new(200).set_body_json(
            json!({"success": true, "data": {"web": [{"url": "https://c.test", "title": "C", "description": "gamma"}]}}),
        ))
        .mount(&server)
        .await;
    let tool = SearchWeb::with_backends(vec![
        Backend::Parallel(Parallel::new(&format!("{}/mcp", server.uri()), "")),
        Backend::Firecrawl(Firecrawl::new(&server.uri(), "")),
    ]);
    let result = tool.run(&params("gamma")).await;
    assert!(!result.is_error, "{result:?}");
    let output = format!("{:?}", result.output);
    assert!(output.contains("https://c.test"), "{output}");
    assert!(format!("{:?}", result.display).contains("Searched via Firecrawl"));
}

#[tokio::test]
async fn every_backend_failing_is_one_clear_error_and_keys_are_sent() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/v2/search"))
        .and(header_exists("authorization"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"success": false, "error": "no credits"})))
        .mount(&server)
        .await;
    let tool = SearchWeb::with_backends(vec![Backend::Firecrawl(Firecrawl::new(&server.uri(), "fc-key"))]);
    let result = tool.run(&params("x")).await;
    assert!(result.is_error);
    assert!(result.message.contains("Firecrawl: no credits"), "{}", result.message);
}

#[test]
fn openrouter_citations_become_deduplicated_sources() {
    let payload = json!({
        "choices": [{
            "message": {
                "content": "  Rust 1.94 shipped in March.  ",
                "annotations": [
                    {"type": "url_citation", "url_citation": {"url": "https://a.test", "title": "A", "content": "alpha"}},
                    {"type": "url_citation", "url_citation": {"url": "https://a.test", "title": "A again"}},
                    {"type": "file", "file": {}},
                    {"type": "url_citation", "url_citation": {"url": "https://b.test", "title": "B"}}
                ]
            }
        }]
    });
    let (summary, sources) = parse_openrouter_search(&payload);
    assert_eq!(summary, "Rust 1.94 shipped in March.");
    assert_eq!(sources.len(), 2);
    assert_eq!(sources[0].title, "A");
    assert_eq!(sources[0].content, "alpha");
    assert_eq!(sources[1].url, "https://b.test");
}

#[test]
fn an_openrouter_reply_without_choices_is_empty() {
    let (summary, sources) = parse_openrouter_search(&json!({"error": "nope"}));
    assert!(summary.is_empty());
    assert!(sources.is_empty());
}
