//! Score the director on labelled decision points, with the code that ships.
//!
//! ```text
//! DIRECTOR_EVAL_KEY=sk-or-... cargo run --release --example director_eval -- cases.jsonl
//! ```
//!
//! Each line of `cases.jsonl` is one decision point taken from a real turn:
//!
//! ```json
//! {"id": "...", "kind": "opening", "system_prompt": "...",
//!  "history": [<kosong messages up to the decision>],
//!  "tools": [{"name": "...", "description": "...", "parameters": {...}}],
//!  "expect": ["read_learner_record"]}
//! ```
//!
//! `expect` lists every acceptable next action; `reply_to_user` means "no tool".
//! A case scores as *right* when the directive acts on an expected option, as
//! *wrong* when it acts on anything else, and as *deferred* when the director
//! stands aside (`free`) — which is never wrong, only unhelpful, because the
//! model then steers exactly as it did before. The thing to watch is wrong.

use std::collections::BTreeMap;

use kimi_agent::soul::decision::JevClient;
use kimi_agent::soul::director::{Director, DirectorConfig, Directive, Phase, REPLY};
use kosong::message::Message;
use kosong::tooling::Tool;
use serde::Deserialize;

#[derive(Deserialize)]
struct Case {
    id: String,
    kind: String,
    system_prompt: String,
    history: Vec<Message>,
    tools: Vec<Tool>,
    expect: Vec<String>,
}

#[tokio::main]
async fn main() {
    let path = std::env::args().nth(1).expect("usage: director_eval <cases.jsonl>");
    let key = std::env::var("DIRECTOR_EVAL_KEY").expect("DIRECTOR_EVAL_KEY is not set");
    let jev = JevClient::new("https://openrouter.ai/api/v1", &key).expect("decisions are switched off");
    let director = Director::new(jev, DirectorConfig::from_env().unwrap_or_default());

    let text = std::fs::read_to_string(&path).expect("cannot read cases");
    let cases: Vec<Case> = text
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| serde_json::from_str(line).expect("bad case"))
        .collect();

    // kind -> (right, wrong, deferred)
    let mut tally: BTreeMap<String, (usize, usize, usize)> = BTreeMap::new();
    let mut elapsed = Vec::new();
    for case in &cases {
        let started = std::time::Instant::now();
        let decision = director
            .decide(
                &case.system_prompt,
                &case.history,
                &case.tools,
                None,
                if case.id.ends_with("turn_end") { Phase::TurnEnd } else { Phase::BeforeStep },
            )
            .await;
        elapsed.push(started.elapsed().as_millis());
        let (acted, label, ranked) = match &decision {
            Some(decision) => {
                let acted = match &decision.directive {
                    Directive::Reply { .. } => Some(REPLY.to_string()),
                    Directive::Call { tool, .. } => Some(tool.clone()),
                    // At the end of a turn only a named call reopens it; the
                    // loop treats anything else as "stop", so score it so.
                    Directive::Act { .. } if case.id.ends_with("turn_end") => None,
                    Directive::Act { .. } => Some("<any tool>".to_string()),
                    Directive::Free { .. } => None,
                };
                (
                    acted,
                    decision.directive.label(),
                    decision.record.get("ranked").cloned().unwrap_or_default(),
                )
            }
            None => (None, "none".to_string(), serde_json::Value::Null),
        };
        let entry = tally.entry(case.kind.clone()).or_default();
        let verdict = match &acted {
            None => {
                entry.2 += 1;
                "defer"
            }
            Some(action) if action == "<any tool>" => {
                if case.expect.iter().any(|e| e != REPLY) {
                    entry.0 += 1;
                    "right"
                } else {
                    entry.1 += 1;
                    "WRONG"
                }
            }
            Some(action) if case.expect.contains(action) => {
                entry.0 += 1;
                "right"
            }
            Some(_) => {
                entry.1 += 1;
                "WRONG"
            }
        };
        println!(
            "{verdict:5} {:<14} {:<48} expect {:?} | {label} | {ranked}",
            case.kind,
            case.id,
            case.expect
        );
    }

    println!();
    let (mut right, mut wrong, mut deferred) = (0, 0, 0);
    for (kind, (r, w, d)) in &tally {
        println!("{kind:<14} right {r:>3}  wrong {w:>3}  deferred {d:>3}");
        right += r;
        wrong += w;
        deferred += d;
    }
    elapsed.sort();
    let p50 = elapsed.get(elapsed.len() / 2).copied().unwrap_or(0);
    let p90 = elapsed.get(elapsed.len() * 9 / 10).copied().unwrap_or(0);
    println!(
        "{:<14} right {right:>3}  wrong {wrong:>3}  deferred {deferred:>3}   decision p50 {p50}ms p90 {p90}ms",
        "ALL"
    );
}
