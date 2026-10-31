//! The director in a real loop: a scripted decision endpoint, a scripted model,
//! a real toolset. What is pinned here is the contract the harness makes —
//! a confident call is made by the harness from arguments the model wrote, a
//! confident reply withholds the tools, and a failed argument write hands the
//! step back to the model instead of costing the turn.

mod tool_test_utils;

use std::collections::{HashSet, VecDeque};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use serde::Deserialize;
use serde_json::{Map, Value, json};
use schemars::JsonSchema;
use tempfile::TempDir;
use tokio_util::sync::CancellationToken;
use wiremock::matchers::{method, path};
use wiremock::{Mock, MockServer, Request, Respond, ResponseTemplate};

use kimi_agent::llm::LLM;
use kimi_agent::soul::agent::Agent;
use kimi_agent::soul::context::Context;
use kimi_agent::soul::decision::JevClient;
use kimi_agent::soul::director::{Director, DirectorConfig, REPLY};
use kimi_agent::soul::kimisoul::KimiSoul;
use kimi_agent::soul::run_soul;
use kimi_agent::soul::toolset::KimiToolset;
use kimi_agent::utils::QueueShutDown;
use kimi_agent::wire::{UserInput, Wire};
use kosong::chat_provider::{
    ChatProvider, ChatProviderError, StreamedMessage, ThinkingEffort, TokenUsage,
};
use kosong::message::{ContentPart, Message, Role, StreamedMessagePart, TextPart, ToolCall};
use kosong::tooling::{CallableTool2, Tool, ToolReturnValue, tool_ok};

use tool_test_utils::RuntimeFixture;

// ----- a model that records how each call was shaped ----------------------

struct Script {
    replies: Mutex<VecDeque<Vec<StreamedMessagePart>>>,
    /// The `tool_choice` / `response_format` each call carried, in order.
    shapes: Mutex<Vec<Map<String, Value>>>,
}

struct ScriptedModel {
    script: Arc<Script>,
    overrides: Map<String, Value>,
}

struct Parts(VecDeque<StreamedMessagePart>);

#[async_trait]
impl StreamedMessage for Parts {
    async fn next_part(&mut self) -> Result<Option<StreamedMessagePart>, ChatProviderError> {
        Ok(self.0.pop_front())
    }
    fn id(&self) -> Option<String> {
        Some("scripted".to_string())
    }
    fn usage(&self) -> Option<TokenUsage> {
        None
    }
}

#[async_trait]
impl ChatProvider for ScriptedModel {
    fn name(&self) -> &str {
        "scripted"
    }
    fn model_name(&self) -> &str {
        "scripted"
    }
    fn thinking_effort(&self) -> Option<ThinkingEffort> {
        None
    }
    async fn generate(
        &self,
        _system_prompt: &str,
        tools: &[Tool],
        _history: &[Message],
    ) -> Result<Box<dyn StreamedMessage>, ChatProviderError> {
        // The tool block must never be narrowed: that is what keeps the
        // provider's prefix cache alive across steps.
        assert_eq!(tools.len(), 1, "the catalogue reaches every call intact");
        self.script.shapes.lock().unwrap().push(self.overrides.clone());
        let parts = self
            .script
            .replies
            .lock()
            .unwrap()
            .pop_front()
            .expect("the model was called more times than scripted");
        Ok(Box::new(Parts(parts.into())))
    }
    fn with_thinking(&self, _effort: ThinkingEffort) -> Box<dyn ChatProvider> {
        Box::new(ScriptedModel {
            script: Arc::clone(&self.script),
            overrides: self.overrides.clone(),
        })
    }
    fn with_generation_overrides(
        &self,
        overrides: &Map<String, Value>,
    ) -> Option<Box<dyn ChatProvider>> {
        Some(Box::new(ScriptedModel {
            script: Arc::clone(&self.script),
            overrides: overrides.clone(),
        }))
    }
    fn as_any(&self) -> &dyn std::any::Any {
        self
    }
}

fn text(body: &str) -> Vec<StreamedMessagePart> {
    vec![ContentPart::Text(TextPart::new(body)).into()]
}

// ----- a decision endpoint that answers from a script ---------------------

struct Decisions {
    answers: Mutex<VecDeque<Vec<(&'static str, f64)>>>,
    asked: AtomicUsize,
}

impl Respond for Decisions {
    fn respond(&self, _request: &Request) -> ResponseTemplate {
        self.asked.fetch_add(1, Ordering::SeqCst);
        let answer = self
            .answers
            .lock()
            .unwrap()
            .pop_front()
            .unwrap_or_else(|| vec![(REPLY, 0.99)]);
        let probabilities: Map<String, Value> = answer
            .iter()
            .map(|(option, p)| (option.to_string(), json!(p)))
            .collect();
        ResponseTemplate::new(200).set_body_json(json!({
            "answers": {"next": {
                "type": "choice",
                "choice": answer[0].0,
                "confidence": answer[0].1,
                "probabilities": probabilities,
            }}
        }))
    }
}

// ----- one real tool ------------------------------------------------------

#[derive(Debug, Deserialize, JsonSchema)]
struct LookupParams {
    q: String,
}

struct Lookup;

#[async_trait]
impl CallableTool2 for Lookup {
    type Params = LookupParams;
    fn name(&self) -> &str {
        "lookup"
    }
    fn description(&self) -> &str {
        "Look a topic up in the student's books."
    }
    async fn call_typed(&self, params: LookupParams) -> ToolReturnValue {
        tool_ok(format!("found: {}", params.q), "looked it up", "")
    }
}

// ----- harness ------------------------------------------------------------

async fn turn(
    replies: Vec<Vec<StreamedMessagePart>>,
    decisions: Vec<Vec<(&'static str, f64)>>,
) -> (Vec<Message>, Vec<Map<String, Value>>, usize) {
    let server = MockServer::start().await;
    let decisions = Arc::new(Decisions {
        answers: Mutex::new(decisions.into()),
        asked: AtomicUsize::new(0),
    });
    Mock::given(method("POST"))
        .and(path("/alpha/decisions"))
        .respond_with(SharedDecisions(Arc::clone(&decisions)))
        .mount(&server)
        .await;

    let script = Arc::new(Script {
        replies: Mutex::new(replies.into()),
        shapes: Mutex::new(Vec::new()),
    });
    let llm = LLM {
        chat_provider: Box::new(ScriptedModel {
            script: Arc::clone(&script),
            overrides: Map::new(),
        }),
        max_context_size: 100_000,
        capabilities: HashSet::new(),
        model_config: None,
        provider_config: None,
    };
    let fixture = RuntimeFixture::new();
    let mut runtime = fixture.runtime.clone();
    runtime.llm = Some(Arc::new(llm));
    let mut toolset = KimiToolset::new();
    toolset.add(Arc::new(Lookup));
    let tmp = TempDir::new().expect("temp dir");
    // SAFETY: the record file goes to this test's own temp dir.
    unsafe { std::env::set_var("KIMI_SHARE_DIR", tmp.path()) };
    let agent = Agent {
        name: "Test Agent".to_string(),
        system_prompt: "Test system prompt.".to_string(),
        toolset: Arc::new(tokio::sync::Mutex::new(toolset)),
        runtime,
        completion_gate_tools: Vec::new(),
        context_continuity_tool: None,
        output_contract: None,
    };
    let jev = JevClient::at(&format!("{}/alpha/decisions", server.uri()), "test-key")
        .expect("decisions are on");
    let soul = KimiSoul::new(agent, Context::new(tmp.path().join("history.jsonl")))
        .with_director(Some(Director::new(jev, DirectorConfig::default())));

    let ui_loop = |wire: Arc<Wire>| async move {
        let ui = wire.ui_side(true);
        loop {
            if let Err(QueueShutDown) = ui.receive().await {
                return Ok(());
            }
        }
    };
    run_soul(
        &soul,
        UserInput::Text("Why does a ball keep rolling?".to_string()),
        ui_loop,
        CancellationToken::new(),
        None,
    )
    .await
    .expect("run soul");

    let history = soul.context().lock().await.history().to_vec();
    let shapes = script.shapes.lock().unwrap().clone();
    (history, shapes, decisions.asked.load(Ordering::SeqCst))
}

struct SharedDecisions(Arc<Decisions>);

impl Respond for SharedDecisions {
    fn respond(&self, request: &Request) -> ResponseTemplate {
        self.0.respond(request)
    }
}

fn calls(history: &[Message]) -> Vec<ToolCall> {
    history
        .iter()
        .filter_map(|m| m.tool_calls.clone())
        .flatten()
        .collect()
}

#[tokio::test]
async fn the_harness_makes_the_call_and_the_model_only_writes() {
    let (history, shapes, asked) = turn(
        vec![
            // Asked for the lookup's arguments.
            text("```json\n{\"q\": \"inertia\"}\n```"),
            // Asked to reply, with the tools withheld.
            text("It keeps rolling because nothing stops it."),
        ],
        vec![
            vec![("lookup", 0.95), (REPLY, 0.05)],
            vec![(REPLY, 0.92), ("lookup", 0.08)],
            // Turn end: nothing is owed after the reply.
            vec![(REPLY, 0.97), ("lookup", 0.03)],
        ],
    )
    .await;

    let made = calls(&history);
    assert_eq!(made.len(), 1);
    assert_eq!(made[0].function.name, "lookup");
    assert!(made[0].id.starts_with("directed_"));
    assert_eq!(made[0].function.arguments.as_deref(), Some("{\"q\":\"inertia\"}"));
    assert!(
        history
            .iter()
            .any(|m| m.role == Role::Tool && m.extract_text("").contains("found: inertia")),
        "the harness-made call really ran"
    );
    assert_eq!(
        history.last().map(|m| m.extract_text("")).as_deref(),
        Some("It keeps rolling because nothing stops it.")
    );
    // The argument request never reaches the transcript.
    assert!(!history.iter().any(|m| m.extract_text("").contains("The next action is decided")));

    assert_eq!(shapes.len(), 2);
    assert_eq!(shapes[0]["tool_choice"], "none");
    assert_eq!(shapes[0]["response_format"]["type"], "json_schema");
    assert_eq!(shapes[1]["tool_choice"], "none");
    assert!(shapes[1].get("response_format").is_none());
    assert_eq!(asked, 3, "before each step, and once at the turn's end");
}

#[tokio::test]
async fn words_then_a_call_still_owed_reopens_the_turn_once() {
    let (history, shapes, _) = turn(
        vec![
            text("Here is my explanation."),
            text("{\"q\": \"friction\"}"),
            text("Done."),
        ],
        vec![
            vec![(REPLY, 0.9), ("lookup", 0.1)],
            // The reply was written, and the request still owes a call.
            vec![("lookup", 0.91), (REPLY, 0.09)],
            vec![(REPLY, 0.95), ("lookup", 0.05)],
            vec![(REPLY, 0.99), ("lookup", 0.01)],
        ],
    )
    .await;

    let made = calls(&history);
    assert_eq!(made.len(), 1);
    assert_eq!(made[0].function.arguments.as_deref(), Some("{\"q\":\"friction\"}"));
    assert_eq!(shapes.len(), 3);
    assert!(history.iter().any(|m| m.extract_text("") == "Here is my explanation."));
    assert_eq!(history.last().map(|m| m.extract_text("")).as_deref(), Some("Done."));
}

#[tokio::test]
async fn arguments_that_are_not_an_object_hand_the_step_back_to_the_model() {
    let mut native = ToolCall::new("native-1", "lookup");
    native.function.arguments = Some("{\"q\": \"momentum\"}".to_string());
    let (history, shapes, _) = turn(
        vec![
            // The argument write fails: prose, not an object.
            text("I will look that up for you now."),
            // The model takes the step itself, unsteered.
            vec![StreamedMessagePart::from(native)],
            text("Momentum explains it."),
        ],
        vec![
            vec![("lookup", 0.95), (REPLY, 0.05)],
            vec![(REPLY, 0.95), ("lookup", 0.05)],
            vec![(REPLY, 0.99), ("lookup", 0.01)],
        ],
    )
    .await;

    let made = calls(&history);
    assert_eq!(made.len(), 1);
    assert_eq!(made[0].id, "native-1", "the model's own call, not a directed one");
    assert!(shapes[1].is_empty(), "the fallback step is the model's, unshaped");
    assert_eq!(history.last().map(|m| m.extract_text("")).as_deref(), Some("Momentum explains it."));
}

#[tokio::test]
async fn an_unsure_read_leaves_the_step_to_the_model() {
    let (history, shapes, _) = turn(
        vec![text("Plain answer.")],
        vec![
            vec![(REPLY, 0.55), ("lookup", 0.45)],
            vec![(REPLY, 0.6), ("lookup", 0.4)],
        ],
    )
    .await;
    assert!(calls(&history).is_empty());
    assert_eq!(shapes, vec![Map::new()], "no field was set on an unsure step");
}
