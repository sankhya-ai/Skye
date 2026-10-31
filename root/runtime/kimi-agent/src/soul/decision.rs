//! The client for typed decisions: the choices the harness makes without the model.
//!
//! In this runtime the model owns exactly two driving decisions — which tool to
//! call, and whether to call one at all — and the second is the whole
//! termination contract (`stop_reason: "no_tool_calls"` in
//! [`crate::soul::kimisoul`]). A small reasoning model is unreliable at both.
//! Measured on OpenRouter on 2026-09-23: `deepseek-v4-flash` with
//! `tool_choice: "auto"` called two lookup tools on five of five plain
//! questions, and `gpt-oss-120b` stopped to describe work it had not done.
//!
//! `typesafe/jev-1.13` answers a closed choice with a probability for every
//! option in well under a second and bills nothing for the answer. This module
//! only talks to it; [`crate::soul::director`] decides what to ask.
//!
//! **The contract is the one the hub's `decision_client.py` keeps**, for the
//! same reasons:
//!
//! * *It never fails a turn.* Every error — no key, a timeout, an HTTP fault, a
//!   shape this code does not recognise — is `None`, and callers are written so
//!   `None` means "do exactly what this loop did before decisions existed".
//! * *It never retries.* The reason to call it is that it answers inside a
//!   second; a retry spends the budget twice and lands after the point where
//!   the caller should have given up.
//! * *It is switchable off.* `CHOTU_DECISIONS_ENABLED=0` makes construction
//!   return `None`, so a bad day at the provider is one environment variable
//!   rather than a rebuild of a binary that ships inside two products.
//!
//! The endpoint is a sibling of `/v1`, not a path beneath it: asking for this
//! model on `/chat/completions` is refused outright.

use std::time::{Duration, Instant};

use serde_json::{Value, json};
use tracing::debug;

/// Turns every decision in this process into an immediate fallback.
pub const DECISIONS_ENABLED_ENV: &str = "CHOTU_DECISIONS_ENABLED";

pub const DECISION_MODEL: &str = "typesafe/jev-1.13";

/// Measured p50 0.57–0.92s with a long tail; 1.97s was seen on a live turn.
/// The deadline caps the wasted wait at roughly the time one ordinary model
/// reply takes, which is what the step pays if this falls back anyway.
const TIMEOUT: Duration = Duration::from_millis(2_500);

/// One reading of a state against named questions.
#[derive(Debug, Clone)]
pub struct Answers {
    pub answers: Value,
    pub elapsed: Duration,
}

impl Answers {
    /// A Choice answer's full distribution, option → probability.
    pub fn probabilities(&self, question: &str) -> Option<Vec<(String, f64)>> {
        let map = self
            .answers
            .get(question)?
            .get("probabilities")?
            .as_object()?;
        let mut out: Vec<(String, f64)> = map
            .iter()
            .filter_map(|(option, p)| Some((option.clone(), p.as_f64()?)))
            .collect();
        if out.is_empty() {
            return None;
        }
        out.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        Some(out)
    }
}

/// A client for the decisions endpoint.
#[derive(Debug, Clone)]
pub struct JevClient {
    http: reqwest::Client,
    url: String,
    api_key: String,
}

impl JevClient {
    /// A client for this provider, or `None` to leave the loop exactly as it was.
    ///
    /// `base_url` is the provider's chat base. The decisions route is built from
    /// the host rather than by appending to it, because it is a sibling of `/v1`.
    /// A provider that is not OpenRouter has no route to this model at all, and
    /// returning `None` is how that stays a non-event.
    pub fn new(base_url: &str, api_key: &str) -> Option<Self> {
        let base = base_url.trim().trim_end_matches('/');
        let host = match base.rsplit_once("/v1") {
            Some((head, _)) => head,
            // Not an OpenRouter-shaped base URL. Guessing a decisions path onto
            // an unknown provider would spend a call to be told 404 on every step.
            None => return None,
        };
        if host.is_empty() {
            return None;
        }
        Self::at(&format!("{host}/alpha/decisions"), api_key)
    }

    /// A client for an explicit decisions URL. Tests point this at a mock.
    pub fn at(url: &str, api_key: &str) -> Option<Self> {
        if !decisions_enabled() {
            return None;
        }
        let key = api_key.trim();
        if key.is_empty() {
            return None;
        }
        let http = reqwest::Client::builder().timeout(TIMEOUT).build().ok()?;
        Some(Self {
            http,
            url: url.to_string(),
            api_key: key.to_string(),
        })
    }

    pub fn url(&self) -> &str {
        &self.url
    }

    /// Ask every question about one state in one call, or `None`.
    ///
    /// Asking together is the point, not only the saving: every answer is scored
    /// against the same reading of the same state, so two answers cannot
    /// disagree about what they were looking at.
    pub async fn decide(&self, state: &Value, questions: &Value) -> Option<Answers> {
        let started = Instant::now();
        let body = json!({
            "model": DECISION_MODEL,
            "state": state,
            "questions": questions,
        });
        let response = match self
            .http
            .post(&self.url)
            .bearer_auth(&self.api_key)
            .json(&body)
            .send()
            .await
        {
            Ok(response) => response,
            Err(error) => {
                // Logged at debug: an expected outcome with a working fallback,
                // not a fault. A burst of these is a reason to switch the
                // feature off, which is why it is visible at all.
                debug!("decision model unreachable ({error}); falling back");
                return None;
            }
        };
        if !response.status().is_success() {
            debug!("decision model answered {}; falling back", response.status());
            return None;
        }
        let payload: Value = match response.json().await {
            Ok(payload) => payload,
            Err(error) => {
                debug!("decision model sent no JSON ({error}); falling back");
                return None;
            }
        };
        let answers = payload.get("answers")?.clone();
        if !answers.is_object() {
            return None;
        }
        Some(Answers {
            answers,
            elapsed: started.elapsed(),
        })
    }
}

/// False turns every decision in this process into an immediate fallback.
pub fn decisions_enabled() -> bool {
    match std::env::var(DECISIONS_ENABLED_ENV) {
        Ok(raw) => !matches!(
            raw.trim().to_ascii_lowercase().as_str(),
            "0" | "false" | "no" | "off"
        ),
        Err(_) => true,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The env var is process-wide, so these run under one lock rather than
    /// racing each other into a wrong reading.
    fn with_env<T>(key: &str, value: Option<&str>, body: impl FnOnce() -> T) -> T {
        static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
        let _guard = LOCK.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        let previous = std::env::var(key).ok();
        match value {
            Some(value) => unsafe { std::env::set_var(key, value) },
            None => unsafe { std::env::remove_var(key) },
        }
        let out = body();
        match previous {
            Some(previous) => unsafe { std::env::set_var(key, previous) },
            None => unsafe { std::env::remove_var(key) },
        }
        out
    }

    #[test]
    fn switched_off_builds_nothing() {
        for off in ["0", "false", "no", "off", "OFF"] {
            with_env(DECISIONS_ENABLED_ENV, Some(off), || {
                assert!(
                    JevClient::new("https://openrouter.ai/api/v1", "sk-test").is_none(),
                    "{off} should switch the decider off"
                );
            });
        }
    }

    #[test]
    fn no_key_builds_nothing() {
        with_env(DECISIONS_ENABLED_ENV, None, || {
            assert!(JevClient::new("https://openrouter.ai/api/v1", "   ").is_none());
        });
    }

    #[test]
    fn an_unknown_provider_builds_nothing() {
        // Guessing a decisions path onto a provider that has none would spend a
        // call to be told 404 on every step of every turn.
        with_env(DECISIONS_ENABLED_ENV, None, || {
            assert!(JevClient::new("https://example.test/openai", "sk-test").is_none());
        });
    }

    #[test]
    fn the_route_is_a_sibling_of_v1_not_a_child() {
        with_env(DECISIONS_ENABLED_ENV, None, || {
            let decider = JevClient::new("https://openrouter.ai/api/v1", "sk-test")
                .expect("a provider with a /v1 base builds");
            assert_eq!(decider.url, "https://openrouter.ai/api/alpha/decisions");
        });
    }

    #[test]
    fn a_trailing_slash_is_not_a_different_provider() {
        with_env(DECISIONS_ENABLED_ENV, None, || {
            let decider = JevClient::new("https://openrouter.ai/api/v1/", "sk-test")
                .expect("a trailing slash still builds");
            assert_eq!(decider.url, "https://openrouter.ai/api/alpha/decisions");
        });
    }

    #[test]
    fn a_distribution_comes_back_most_likely_first() {
        let answers = Answers {
            answers: json!({"next": {"choice": "b", "probabilities": {"a": 0.1, "b": 0.7, "c": 0.2}}}),
            elapsed: Duration::ZERO,
        };
        let ranked = answers.probabilities("next").unwrap();
        assert_eq!(ranked[0], ("b".to_string(), 0.7));
        assert_eq!(ranked[2], ("a".to_string(), 0.1));
        assert!(answers.probabilities("missing").is_none());
    }
}
