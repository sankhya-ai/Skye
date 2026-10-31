mod tool_test_utils;

use std::collections::{HashMap, HashSet, VecDeque};
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Duration;

use async_trait::async_trait;
use kaos::KaosPath;
use tokio_util::sync::CancellationToken;

use kimi_agent::config::ModelCapability;
use kimi_agent::council_budget::{
    COST_BUDGET_EXHAUSTED, CouncilBudget, TOKEN_BUDGET_EXHAUSTED, WALL_CLOCK_BUDGET_EXHAUSTED,
};
use kimi_agent::llm::LLM;
use kimi_agent::skill::flow::{Flow, FlowEdge, FlowNode, FlowNodeKind};
use kimi_agent::skill::{Skill, SkillType};
use kimi_agent::soul::agent::{Agent, Runtime};
use kimi_agent::soul::context::Context;
use kimi_agent::soul::kimisoul::{FlowRunner, KimiSoul};
use kimi_agent::soul::message::tool_result_to_message;
use kimi_agent::soul::toolset::KimiToolset;
use kimi_agent::soul::{FlowIncomplete, run_soul};
use kimi_agent::tools::utils::tool_rejected_error;
use kimi_agent::utils::QueueShutDown;
use kimi_agent::wire::{TurnBegin, UserInput, Wire, WireMessage};
use kosong::chat_provider::{
    ChatProvider, ChatProviderError, ChatProviderErrorKind, StreamedMessage, ThinkingEffort,
    TokenUsage,
};
use kosong::message::{
    ContentPart, ImageURLPart, Message, Role, StreamedMessagePart, TextPart, ToolCall,
};
use kosong::tooling::{CallableTool2, Tool, ToolResult, ToolReturnValue, tool_error, tool_ok};
use schemars::JsonSchema;
use serde::Deserialize;
use tempfile::TempDir;

use tool_test_utils::RuntimeFixture;

const RALPH_IMAGE_URL: &str = "https://example.com/test.png";
const STREAM_TEST_TIMEOUT: Duration = Duration::from_secs(2);
const RETRY_TEST_TIMEOUT: Duration = Duration::from_secs(3);

fn ralph_prompt(prompt_text: &str) -> String {
    format!(
        "{}. (You are running in an automated loop where the same prompt is fed repeatedly. Only choose STOP when the task is fully complete. Including it will stop further iterations. If you are not 100% sure, choose CONTINUE.)\n\nAvailable branches:\n- CONTINUE\n- STOP\n\nReply with a choice using <choice>...</choice>.",
        prompt_text
    )
}

struct SequenceStreamedMessage {
    parts: VecDeque<StreamedMessagePart>,
    usage: Option<TokenUsage>,
}

impl SequenceStreamedMessage {
    fn new(parts: Vec<StreamedMessagePart>, usage: Option<TokenUsage>) -> Self {
        Self {
            parts: parts.into(),
            usage,
        }
    }
}

#[async_trait]
impl StreamedMessage for SequenceStreamedMessage {
    async fn next_part(&mut self) -> Result<Option<StreamedMessagePart>, ChatProviderError> {
        Ok(self.parts.pop_front())
    }

    fn id(&self) -> Option<String> {
        Some("sequence".to_string())
    }

    fn usage(&self) -> Option<TokenUsage> {
        self.usage.clone()
    }
}

struct SequenceChatProvider {
    sequences: Vec<Vec<StreamedMessagePart>>,
    index: AtomicUsize,
    usage: Option<TokenUsage>,
    delay: std::time::Duration,
}

impl SequenceChatProvider {
    fn new(sequences: Vec<Vec<StreamedMessagePart>>) -> Self {
        Self {
            sequences,
            index: AtomicUsize::new(0),
            usage: None,
            delay: std::time::Duration::ZERO,
        }
    }

    fn with_usage(mut self, usage: TokenUsage) -> Self {
        self.usage = Some(usage);
        self
    }

    fn with_delay(mut self, delay: std::time::Duration) -> Self {
        self.delay = delay;
        self
    }
}

#[async_trait]
impl ChatProvider for SequenceChatProvider {
    fn name(&self) -> &str {
        "sequence"
    }

    fn model_name(&self) -> &str {
        "sequence"
    }

    fn thinking_effort(&self) -> Option<ThinkingEffort> {
        None
    }

    async fn generate(
        &self,
        _system_prompt: &str,
        _tools: &[Tool],
        _history: &[Message],
    ) -> Result<Box<dyn StreamedMessage>, ChatProviderError> {
        if !self.delay.is_zero() {
            tokio::time::sleep(self.delay).await;
        }
        let index = self.index.fetch_add(1, Ordering::SeqCst);
        let sequence = if self.sequences.is_empty() {
            Vec::new()
        } else {
            let selected = std::cmp::min(index, self.sequences.len() - 1);
            self.sequences[selected].clone()
        };
        Ok(Box::new(SequenceStreamedMessage::new(
            sequence,
            self.usage.clone(),
        )))
    }

    fn with_thinking(&self, _effort: ThinkingEffort) -> Box<dyn ChatProvider> {
        Box::new(SequenceChatProvider {
            sequences: self.sequences.clone(),
            index: AtomicUsize::new(self.index.load(Ordering::SeqCst)),
            usage: self.usage.clone(),
            delay: self.delay,
        })
    }

    fn as_any(&self) -> &dyn std::any::Any {
        self
    }
}

struct PartialThenTimeoutStream {
    sent_partial: bool,
}

#[async_trait]
impl StreamedMessage for PartialThenTimeoutStream {
    async fn next_part(&mut self) -> Result<Option<StreamedMessagePart>, ChatProviderError> {
        if !self.sent_partial {
            self.sent_partial = true;
            return Ok(Some(StreamedMessagePart::Content(ContentPart::Text(
                TextPart::new("failed attempt partial"),
            ))));
        }
        Err(ChatProviderError::new(
            ChatProviderErrorKind::Timeout,
            "provider timed out after partial output",
        ))
    }

    fn id(&self) -> Option<String> {
        None
    }

    fn usage(&self) -> Option<TokenUsage> {
        None
    }
}

struct PartialThenFinalProvider {
    attempts: Arc<AtomicUsize>,
}

#[async_trait]
impl ChatProvider for PartialThenFinalProvider {
    fn name(&self) -> &str {
        "partial-then-final"
    }

    fn model_name(&self) -> &str {
        "partial-then-final"
    }

    fn thinking_effort(&self) -> Option<ThinkingEffort> {
        None
    }

    async fn generate(
        &self,
        _system_prompt: &str,
        _tools: &[Tool],
        _history: &[Message],
    ) -> Result<Box<dyn StreamedMessage>, ChatProviderError> {
        if self.attempts.fetch_add(1, Ordering::SeqCst) == 0 {
            return Ok(Box::new(PartialThenTimeoutStream {
                sent_partial: false,
            }));
        }
        Ok(Box::new(SequenceStreamedMessage::new(
            vec![StreamedMessagePart::Content(ContentPart::Text(
                TextPart::new("final answer"),
            ))],
            None,
        )))
    }

    fn with_thinking(&self, _effort: ThinkingEffort) -> Box<dyn ChatProvider> {
        Box::new(Self {
            attempts: Arc::clone(&self.attempts),
        })
    }

    fn as_any(&self) -> &dyn std::any::Any {
        self
    }
}

struct PreVisibleFailureThenFinalProvider {
    attempts: Arc<AtomicUsize>,
}

#[async_trait]
impl ChatProvider for PreVisibleFailureThenFinalProvider {
    fn name(&self) -> &str {
        "pre-visible-failure-then-final"
    }

    fn model_name(&self) -> &str {
        "pre-visible-failure-then-final"
    }

    fn thinking_effort(&self) -> Option<ThinkingEffort> {
        None
    }

    async fn generate(
        &self,
        _system_prompt: &str,
        _tools: &[Tool],
        _history: &[Message],
    ) -> Result<Box<dyn StreamedMessage>, ChatProviderError> {
        if self.attempts.fetch_add(1, Ordering::SeqCst) == 0 {
            return Err(ChatProviderError::new(
                ChatProviderErrorKind::Timeout,
                "provider timed out before producing output",
            ));
        }
        Ok(Box::new(SequenceStreamedMessage::new(
            vec![StreamedMessagePart::Content(ContentPart::Text(
                TextPart::new("final answer"),
            ))],
            None,
        )))
    }

    fn with_thinking(&self, _effort: ThinkingEffort) -> Box<dyn ChatProvider> {
        Box::new(Self {
            attempts: Arc::clone(&self.attempts),
        })
    }

    fn as_any(&self) -> &dyn std::any::Any {
        self
    }
}

struct DeltaThenCompletionGateStream {
    sent_delta: bool,
    completion_gate: Arc<tokio::sync::Notify>,
}

#[async_trait]
impl StreamedMessage for DeltaThenCompletionGateStream {
    async fn next_part(&mut self) -> Result<Option<StreamedMessagePart>, ChatProviderError> {
        if !self.sent_delta {
            self.sent_delta = true;
            return Ok(Some(StreamedMessagePart::Content(ContentPart::Text(
                TextPart::new("first delta"),
            ))));
        }
        self.completion_gate.notified().await;
        Ok(None)
    }

    fn id(&self) -> Option<String> {
        Some("delta-before-completion".to_string())
    }

    fn usage(&self) -> Option<TokenUsage> {
        None
    }
}

struct DeltaThenCompletionGateProvider {
    attempts: Arc<AtomicUsize>,
    completion_gate: Arc<tokio::sync::Notify>,
}

#[async_trait]
impl ChatProvider for DeltaThenCompletionGateProvider {
    fn name(&self) -> &str {
        "delta-before-completion"
    }

    fn model_name(&self) -> &str {
        "delta-before-completion"
    }

    fn thinking_effort(&self) -> Option<ThinkingEffort> {
        None
    }

    async fn generate(
        &self,
        _system_prompt: &str,
        _tools: &[Tool],
        _history: &[Message],
    ) -> Result<Box<dyn StreamedMessage>, ChatProviderError> {
        self.attempts.fetch_add(1, Ordering::SeqCst);
        Ok(Box::new(DeltaThenCompletionGateStream {
            sent_delta: false,
            completion_gate: Arc::clone(&self.completion_gate),
        }))
    }

    fn with_thinking(&self, _effort: ThinkingEffort) -> Box<dyn ChatProvider> {
        Box::new(Self {
            attempts: Arc::clone(&self.attempts),
            completion_gate: Arc::clone(&self.completion_gate),
        })
    }

    fn as_any(&self) -> &dyn std::any::Any {
        self
    }
}

#[derive(Debug, Deserialize, JsonSchema)]
struct RejectParams {}

struct RejectTool;

#[async_trait]
impl CallableTool2 for RejectTool {
    type Params = RejectParams;

    fn name(&self) -> &str {
        "reject_tool"
    }

    fn description(&self) -> &str {
        "Always reject tool calls."
    }

    async fn call_typed(&self, _params: RejectParams) -> ToolReturnValue {
        tool_rejected_error()
    }
}

#[derive(Debug, Deserialize, JsonSchema)]
struct GateParams {}

struct PassingCompletionGate;

#[async_trait]
impl CallableTool2 for PassingCompletionGate {
    type Params = GateParams;

    fn name(&self) -> &str {
        "runtime_completion_gate"
    }

    fn description(&self) -> &str {
        "Pass the deterministic completion contract."
    }

    async fn call_typed(&self, _params: GateParams) -> ToolReturnValue {
        tool_ok("{}", "completion proof passed", "")
    }
}

#[derive(Debug, Deserialize, JsonSchema)]
struct ContinuityParams {
    summary: String,
    session_id: String,
    workspace_root: String,
}

struct PassingContinuityHook;

#[async_trait]
impl CallableTool2 for PassingContinuityHook {
    type Params = ContinuityParams;

    fn name(&self) -> &str {
        "dhee_context_checkpoint"
    }

    fn description(&self) -> &str {
        "Persist and restore Dhee continuity."
    }

    async fn call_typed(&self, params: ContinuityParams) -> ToolReturnValue {
        assert!(params.summary.contains("durable compacted summary"));
        assert!(!params.session_id.is_empty());
        assert!(!params.workspace_root.is_empty());
        tool_ok(
            r#"{"schema_version":"chotu.context_continuity.v1","status":"ready","restored_context":[{"content":"preserve owner approval boundary"}]}"#,
            "Dhee continuity restored",
            "",
        )
    }
}

struct FailingContinuityHook;

#[async_trait]
impl CallableTool2 for FailingContinuityHook {
    type Params = ContinuityParams;

    fn name(&self) -> &str {
        "dhee_context_checkpoint"
    }

    fn description(&self) -> &str {
        "Reject context rotation when Dhee cannot checkpoint."
    }

    async fn call_typed(&self, _params: ContinuityParams) -> ToolReturnValue {
        tool_error("", "Dhee durable write unavailable", "")
    }
}

fn make_llm(
    sequences: Vec<Vec<StreamedMessagePart>>,
    capabilities: HashSet<ModelCapability>,
) -> LLM {
    make_llm_with_provider(SequenceChatProvider::new(sequences), capabilities)
}

fn make_llm_with_provider(
    provider: impl ChatProvider + 'static,
    capabilities: HashSet<ModelCapability>,
) -> LLM {
    LLM {
        chat_provider: Box::new(provider),
        max_context_size: 100_000,
        capabilities,
        model_config: None,
        provider_config: None,
    }
}

fn runtime_with_llm(mut runtime: Runtime, llm: LLM) -> Runtime {
    runtime.llm = Some(Arc::new(llm));
    runtime
}

fn make_soul(
    runtime: Runtime,
    llm: LLM,
    toolset: KimiToolset,
    tmp_path: &std::path::Path,
) -> KimiSoul {
    let agent = Agent {
        name: "Test Agent".to_string(),
        system_prompt: "Test system prompt.".to_string(),
        toolset: Arc::new(tokio::sync::Mutex::new(toolset)),
        runtime: runtime_with_llm(runtime, llm),
        completion_gate_tools: Vec::new(),
        context_continuity_tool: None,
        output_contract: None,
    };

    KimiSoul::new(agent, Context::new(tmp_path.join("history.jsonl")))
}

async fn run_and_collect_turns(soul: &KimiSoul, user_input: UserInput) -> Vec<UserInput> {
    let turns = Arc::new(tokio::sync::Mutex::new(Vec::new()));
    let turns_clone = Arc::clone(&turns);

    let ui_loop = move |wire: Arc<Wire>| {
        let turns = Arc::clone(&turns_clone);
        async move {
            let ui = wire.ui_side(true);
            loop {
                let msg = match ui.receive().await {
                    Ok(msg) => msg,
                    Err(QueueShutDown) => return Ok(()),
                };
                if let WireMessage::TurnBegin(TurnBegin { user_input }) = msg {
                    turns.lock().await.push(user_input);
                }
            }
        }
    };

    run_soul(soul, user_input, ui_loop, CancellationToken::new(), None)
        .await
        .expect("run soul");

    turns.lock().await.clone()
}

#[tokio::test]
async fn first_delta_is_visible_before_provider_completion() {
    let fixture = RuntimeFixture::new();
    let runtime = fixture.runtime.clone();
    let attempts = Arc::new(AtomicUsize::new(0));
    let completion_gate = Arc::new(tokio::sync::Notify::new());
    let llm = make_llm_with_provider(
        DeltaThenCompletionGateProvider {
            attempts: Arc::clone(&attempts),
            completion_gate: Arc::clone(&completion_gate),
        },
        HashSet::new(),
    );
    let tmp = TempDir::new().expect("temp dir");
    let soul = make_soul(runtime, llm, KimiToolset::new(), tmp.path());
    let (content_tx, mut content_rx) = tokio::sync::mpsc::unbounded_channel();

    let ui_loop = move |wire: Arc<Wire>| {
        let content_tx = content_tx.clone();
        async move {
            let ui = wire.ui_side(false);
            loop {
                match ui.receive().await {
                    Ok(WireMessage::ContentPart(ContentPart::Text(part))) => {
                        content_tx.send(part.text).expect("content receiver open");
                    }
                    Ok(_) => {}
                    Err(QueueShutDown) => return Ok(()),
                }
            }
        }
    };
    let run_future = run_soul(
        &soul,
        UserInput::Text("stream now".to_string()),
        ui_loop,
        CancellationToken::new(),
        None,
    );
    tokio::pin!(run_future);

    let first_delta = tokio::time::timeout(STREAM_TEST_TIMEOUT, async {
        tokio::select! {
            delta = content_rx.recv() => delta.expect("content channel remains open"),
            result = &mut run_future => panic!("provider completed before first delta: {result:?}"),
        }
    })
    .await
    .expect("first delta arrives while provider is running");
    assert_eq!(first_delta, "first delta");

    completion_gate.notify_one();
    tokio::time::timeout(STREAM_TEST_TIMEOUT, &mut run_future)
        .await
        .expect("provider completes after gate release")
        .expect("turn succeeds");
    assert_eq!(attempts.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn pre_visible_failure_retries_without_leaking_failed_attempt() {
    let fixture = RuntimeFixture::new();
    let mut runtime = fixture.runtime.clone();
    runtime.config.loop_control.max_retries_per_step = 2;
    let attempts = Arc::new(AtomicUsize::new(0));
    let llm = make_llm_with_provider(
        PreVisibleFailureThenFinalProvider {
            attempts: Arc::clone(&attempts),
        },
        HashSet::new(),
    );
    let tmp = TempDir::new().expect("temp dir");
    let soul = make_soul(runtime, llm, KimiToolset::new(), tmp.path());
    let wire_content = Arc::new(tokio::sync::Mutex::new(Vec::new()));
    let collected = Arc::clone(&wire_content);

    let ui_loop = move |wire: Arc<Wire>| {
        let collected = Arc::clone(&collected);
        async move {
            let ui = wire.ui_side(false);
            loop {
                match ui.receive().await {
                    Ok(WireMessage::ContentPart(ContentPart::Text(part))) => {
                        collected.lock().await.push(part.text);
                    }
                    Ok(_) => {}
                    Err(QueueShutDown) => return Ok(()),
                }
            }
        }
    };

    tokio::time::timeout(
        RETRY_TEST_TIMEOUT,
        run_soul(
            &soul,
            UserInput::Text("answer once".to_string()),
            ui_loop,
            CancellationToken::new(),
            None,
        ),
    )
    .await
    .expect("retry terminates")
    .expect("retry succeeds");

    assert_eq!(attempts.load(Ordering::SeqCst), 2);
    assert_eq!(wire_content.lock().await.as_slice(), ["final answer"]);
    assert_eq!(
        soul.context()
            .lock()
            .await
            .history()
            .last()
            .map(|message| message.extract_text("")),
        Some("final answer".to_string())
    );
}

#[tokio::test]
async fn post_visible_failure_does_not_retry_duplicate_or_stall() {
    let fixture = RuntimeFixture::new();
    let mut runtime = fixture.runtime.clone();
    runtime.config.loop_control.max_retries_per_step = 2;
    let attempts = Arc::new(AtomicUsize::new(0));
    let llm = make_llm_with_provider(
        PartialThenFinalProvider {
            attempts: Arc::clone(&attempts),
        },
        HashSet::new(),
    );
    let tmp = TempDir::new().expect("temp dir");
    let soul = make_soul(runtime, llm, KimiToolset::new(), tmp.path());
    let wire_content = Arc::new(tokio::sync::Mutex::new(Vec::new()));
    let collected = Arc::clone(&wire_content);
    let interruptions = Arc::new(AtomicUsize::new(0));
    let interruption_count = Arc::clone(&interruptions);

    let ui_loop = move |wire: Arc<Wire>| {
        let collected = Arc::clone(&collected);
        let interruption_count = Arc::clone(&interruption_count);
        async move {
            let ui = wire.ui_side(false);
            loop {
                match ui.receive().await {
                    Ok(WireMessage::ContentPart(ContentPart::Text(part))) => {
                        collected.lock().await.push(part.text);
                    }
                    Ok(WireMessage::StepInterrupted(_)) => {
                        interruption_count.fetch_add(1, Ordering::SeqCst);
                    }
                    Ok(_) => {}
                    Err(QueueShutDown) => return Ok(()),
                }
            }
        }
    };

    let error = tokio::time::timeout(
        STREAM_TEST_TIMEOUT,
        run_soul(
            &soul,
            UserInput::Text("answer once".to_string()),
            ui_loop,
            CancellationToken::new(),
            None,
        ),
    )
    .await
    .expect("failed turn reaches a terminal state")
    .expect_err("post-visible failure is not retried");

    assert!(error.to_string().contains("timed out after partial output"));
    assert_eq!(attempts.load(Ordering::SeqCst), 1);
    assert_eq!(
        wire_content.lock().await.as_slice(),
        ["failed attempt partial"]
    );
    assert_eq!(interruptions.load(Ordering::SeqCst), 1);
    assert!(
        soul.context()
            .lock()
            .await
            .history()
            .iter()
            .all(|message| message.role != Role::Assistant)
    );
}

#[tokio::test]
async fn native_loop_rejects_final_answer_until_completion_gate_passes() {
    let fixture = RuntimeFixture::new();
    let runtime = fixture.runtime.clone();
    let mut gate_call = ToolCall::new("gate-1", "runtime_completion_gate");
    gate_call.function.arguments = Some("{}".to_string());
    let llm = make_llm(
        vec![
            vec![ContentPart::Text(TextPart::new("premature answer")).into()],
            vec![StreamedMessagePart::from(gate_call)],
            vec![ContentPart::Text(TextPart::new("verified answer")).into()],
        ],
        HashSet::new(),
    );
    let mut toolset = KimiToolset::new();
    toolset.add(Arc::new(PassingCompletionGate));
    let tmp = TempDir::new().expect("temp dir");
    let agent = Agent {
        name: "Test Agent".to_string(),
        system_prompt: "Test system prompt.".to_string(),
        toolset: Arc::new(tokio::sync::Mutex::new(toolset)),
        runtime: runtime_with_llm(runtime, llm),
        completion_gate_tools: vec!["runtime_completion_gate".to_string()],
        context_continuity_tool: None,
        output_contract: None,
    };
    let soul = KimiSoul::new(agent, Context::new(tmp.path().join("history.jsonl")));

    run_and_collect_turns(&soul, UserInput::Text("do it".to_string())).await;

    let history = soul.context().lock().await.history().to_vec();
    assert_eq!(
        history.last().and_then(|message| message.content.first()),
        Some(&ContentPart::Text(TextPart::new("verified answer")))
    );
    assert!(history.iter().any(|message| {
        message.role == Role::User
            && message
                .extract_text("")
                .contains("native completion contract rejected")
    }));
}

#[tokio::test]
async fn native_loop_keeps_final_answer_that_crosses_turn_token_budget() {
    let fixture = RuntimeFixture::new();
    let mut runtime = fixture.runtime.clone();
    runtime.council_budget = Arc::new(CouncilBudget::with_limits(
        8,
        4,
        100,
        std::time::Duration::from_secs(1),
    ));
    let provider = SequenceChatProvider::new(vec![vec![
        ContentPart::Text(TextPart::new("over budget answer")).into(),
    ]])
    .with_usage(TokenUsage {
        input_other: 80,
        input_cache_read: 0,
        input_cache_creation: 0,
        output: 30,
        cost_usd: None,
    });
    let llm = make_llm_with_provider(provider, HashSet::new());
    let tmp = TempDir::new().expect("temp dir");
    let soul = make_soul(runtime, llm, KimiToolset::new(), tmp.path());

    let result = run_soul(
        &soul,
        UserInput::Text("do it".to_string()),
        |_wire| async { Ok(()) },
        CancellationToken::new(),
        None,
    )
    .await;

    result.expect("the completed final answer must survive its usage receipt");
    assert_eq!(soul.runtime().council_budget.snapshot().tokens_used, 110);
    assert_eq!(
        soul.context()
            .lock()
            .await
            .history()
            .last()
            .map(|message| message.extract_text("")),
        Some("over budget answer".to_string())
    );
    assert_eq!(
        soul.runtime().council_budget.check_token_budget(),
        Err(TOKEN_BUDGET_EXHAUSTED)
    );
}

#[tokio::test]
async fn native_loop_keeps_final_answer_that_crosses_turn_cost_budget() {
    let fixture = RuntimeFixture::new();
    let mut runtime = fixture.runtime.clone();
    runtime.council_budget = Arc::new(CouncilBudget::with_cost_limit(
        8,
        4,
        1_000,
        std::time::Duration::from_secs(1),
        0.05,
    ));
    let provider = SequenceChatProvider::new(vec![vec![
        ContentPart::Text(TextPart::new("over cost answer")).into(),
    ]])
    .with_usage(TokenUsage {
        input_other: 10,
        input_cache_read: 0,
        input_cache_creation: 0,
        output: 10,
        cost_usd: Some(0.06),
    });
    let llm = make_llm_with_provider(provider, HashSet::new());
    let tmp = TempDir::new().expect("temp dir");
    let soul = make_soul(runtime, llm, KimiToolset::new(), tmp.path());

    let result = run_soul(
        &soul,
        UserInput::Text("do it".to_string()),
        |_wire| async { Ok(()) },
        CancellationToken::new(),
        None,
    )
    .await;

    result.expect("the completed final answer must survive its cost receipt");
    assert!((soul.runtime().council_budget.snapshot().cost_used_usd - 0.06).abs() < 1e-9);
    assert_eq!(
        soul.context()
            .lock()
            .await
            .history()
            .last()
            .map(|message| message.extract_text("")),
        Some("over cost answer".to_string())
    );
    assert_eq!(
        soul.runtime().council_budget.check_cost_budget(),
        Err(COST_BUDGET_EXHAUSTED)
    );
}

#[tokio::test]
async fn native_loop_stops_before_a_step_once_the_cost_budget_is_spent() {
    // Regression: the cost cap was recorded but only gated subagent tasks and compaction, so the main loop kept
    // calling the model after a turn had already spent its dollars.
    let fixture = RuntimeFixture::new();
    let mut runtime = fixture.runtime.clone();
    let budget = CouncilBudget::with_cost_limit(8, 4, 1_000, std::time::Duration::from_secs(5), 0.05);
    budget.record_cost_usd(0.06);
    runtime.council_budget = Arc::new(budget);
    let provider = SequenceChatProvider::new(vec![vec![
        ContentPart::Text(TextPart::new("must never be generated")).into(),
    ]]);
    let llm = make_llm_with_provider(provider, HashSet::new());
    let tmp = TempDir::new().expect("temp dir");
    let soul = make_soul(runtime, llm, KimiToolset::new(), tmp.path());

    let result = run_soul(
        &soul,
        UserInput::Text("do it".to_string()),
        |_wire| async { Ok(()) },
        CancellationToken::new(),
        None,
    )
    .await;

    let error = result.expect_err("a spent cost budget must stop the loop before the model step");
    assert!(format!("{error:#}").contains(COST_BUDGET_EXHAUSTED), "unexpected error: {error:#}");
}

#[tokio::test]
async fn native_loop_interrupts_model_step_at_shared_wall_clock_deadline() {
    let fixture = RuntimeFixture::new();
    let mut runtime = fixture.runtime.clone();
    runtime.council_budget = Arc::new(CouncilBudget::with_limits(
        8,
        4,
        1_000,
        std::time::Duration::from_millis(10),
    ));
    let provider = SequenceChatProvider::new(vec![vec![
        ContentPart::Text(TextPart::new("late answer")).into(),
    ]])
    .with_delay(std::time::Duration::from_millis(50));
    let llm = make_llm_with_provider(provider, HashSet::new());
    let tmp = TempDir::new().expect("temp dir");
    let soul = make_soul(runtime, llm, KimiToolset::new(), tmp.path());

    let result = run_soul(
        &soul,
        UserInput::Text("do it".to_string()),
        |_wire| async { Ok(()) },
        CancellationToken::new(),
        None,
    )
    .await;

    assert!(
        result
            .expect_err("wall-clock budget must stop the turn")
            .to_string()
            .contains(WALL_CLOCK_BUDGET_EXHAUSTED)
    );
    assert_eq!(soul.context().lock().await.history().len(), 1);
}

#[tokio::test]
async fn native_compaction_checkpoints_and_restores_dhee_context_before_rotation() {
    let fixture = RuntimeFixture::new();
    let mut runtime = fixture.runtime.clone();
    runtime.council_budget = Arc::new(CouncilBudget::with_limits(
        8,
        4,
        1_000,
        std::time::Duration::from_secs(1),
    ));
    let provider = SequenceChatProvider::new(vec![
        vec![ContentPart::Text(TextPart::new("first answer")).into()],
        vec![ContentPart::Text(TextPart::new("second answer")).into()],
        vec![ContentPart::Text(TextPart::new("durable compacted summary")).into()],
    ])
    .with_usage(TokenUsage {
        input_other: 5,
        input_cache_read: 0,
        input_cache_creation: 0,
        output: 5,
        cost_usd: None,
    });
    let llm = make_llm_with_provider(provider, HashSet::new());
    let mut toolset = KimiToolset::new();
    toolset.add(Arc::new(PassingContinuityHook));
    let tmp = TempDir::new().expect("temp dir");
    let agent = Agent {
        name: "Test Agent".to_string(),
        system_prompt: "Test system prompt.".to_string(),
        toolset: Arc::new(tokio::sync::Mutex::new(toolset)),
        runtime: runtime_with_llm(runtime, llm),
        completion_gate_tools: Vec::new(),
        context_continuity_tool: Some("dhee_context_checkpoint".to_string()),
        output_contract: None,
    };
    let soul = KimiSoul::new(agent, Context::new(tmp.path().join("history.jsonl")));

    run_and_collect_turns(&soul, UserInput::Text("first request".to_string())).await;
    run_and_collect_turns(&soul, UserInput::Text("second request".to_string())).await;
    run_and_collect_turns(&soul, UserInput::Text("/compact".to_string())).await;

    let history = soul.context().lock().await.history().to_vec();
    let compacted = history.first().expect("compacted context message");
    let text = compacted.extract_text("\n");
    assert!(text.contains("durable compacted summary"));
    assert!(text.contains("Dhee restored the following durable continuity"));
    assert!(text.contains("preserve owner approval boundary"));
    assert!(!text.contains("first request"));
    assert_eq!(soul.runtime().council_budget.snapshot().tokens_used, 30);
}

#[tokio::test]
async fn native_compaction_preserves_original_context_when_dhee_checkpoint_fails() {
    let fixture = RuntimeFixture::new();
    let runtime = fixture.runtime.clone();
    let llm = make_llm(
        vec![
            vec![ContentPart::Text(TextPart::new("first answer")).into()],
            vec![ContentPart::Text(TextPart::new("second answer")).into()],
            vec![ContentPart::Text(TextPart::new("durable compacted summary")).into()],
        ],
        HashSet::new(),
    );
    let mut toolset = KimiToolset::new();
    toolset.add(Arc::new(FailingContinuityHook));
    let tmp = TempDir::new().expect("temp dir");
    let agent = Agent {
        name: "Test Agent".to_string(),
        system_prompt: "Test system prompt.".to_string(),
        toolset: Arc::new(tokio::sync::Mutex::new(toolset)),
        runtime: runtime_with_llm(runtime, llm),
        completion_gate_tools: Vec::new(),
        context_continuity_tool: Some("dhee_context_checkpoint".to_string()),
        output_contract: None,
    };
    let soul = KimiSoul::new(agent, Context::new(tmp.path().join("history.jsonl")));
    run_and_collect_turns(&soul, UserInput::Text("first request".to_string())).await;
    run_and_collect_turns(&soul, UserInput::Text("second request".to_string())).await;
    let before = soul.context().lock().await.history().to_vec();

    let result = run_soul(
        &soul,
        UserInput::Text("/compact".to_string()),
        |_wire| async { Ok(()) },
        CancellationToken::new(),
        None,
    )
    .await;

    assert!(
        result
            .expect_err("Dhee checkpoint must block rotation")
            .to_string()
            .contains("Dhee context continuity hook failed")
    );
    assert_eq!(soul.context().lock().await.history(), before.as_slice());
}

#[tokio::test]
async fn test_ralph_loop_replays_original_prompt() {
    let fixture = RuntimeFixture::new();
    let mut runtime = fixture.runtime.clone();
    runtime.config.loop_control.max_ralph_iterations = 2;

    let user_input = UserInput::Parts(vec![
        ContentPart::Text(TextPart::new("Check this image")),
        ContentPart::ImageUrl(ImageURLPart::new(RALPH_IMAGE_URL)),
    ]);

    let mut capabilities = HashSet::new();
    capabilities.insert(ModelCapability::ImageIn);

    let llm = make_llm(
        vec![
            vec![ContentPart::Text(TextPart::new("first")).into()],
            vec![ContentPart::Text(TextPart::new("second <choice>CONTINUE</choice>")).into()],
            vec![ContentPart::Text(TextPart::new("third <choice>STOP</choice>")).into()],
        ],
        capabilities,
    );

    let toolset = KimiToolset::new();
    let tmp = TempDir::new().expect("temp dir");
    let soul = make_soul(runtime, llm, toolset, tmp.path());

    run_and_collect_turns(&soul, user_input).await;

    let history = soul.context().lock().await.history().to_vec();
    let prompt_text = ralph_prompt("Check this image");

    assert_eq!(
        history,
        vec![
            Message::new(
                Role::User,
                vec![
                    ContentPart::Text(TextPart::new("Check this image")),
                    ContentPart::ImageUrl(ImageURLPart::new(RALPH_IMAGE_URL)),
                ],
            ),
            Message::new(
                Role::Assistant,
                vec![ContentPart::Text(TextPart::new("first"))]
            ),
            Message::new(
                Role::User,
                vec![ContentPart::Text(TextPart::new(prompt_text.clone()))],
            ),
            Message::new(
                Role::Assistant,
                vec![ContentPart::Text(TextPart::new(
                    "second <choice>CONTINUE</choice>"
                ))],
            ),
            Message::new(
                Role::User,
                vec![ContentPart::Text(TextPart::new(prompt_text))],
            ),
            Message::new(
                Role::Assistant,
                vec![ContentPart::Text(TextPart::new(
                    "third <choice>STOP</choice>"
                ))],
            ),
        ]
    );
}

#[tokio::test]
async fn test_ralph_loop_stops_on_choice() {
    let fixture = RuntimeFixture::new();
    let mut runtime = fixture.runtime.clone();
    runtime.config.loop_control.max_ralph_iterations = -1;

    let llm = make_llm(
        vec![
            vec![ContentPart::Text(TextPart::new("first")).into()],
            vec![ContentPart::Text(TextPart::new("done <choice>STOP</choice>")).into()],
        ],
        HashSet::new(),
    );

    let toolset = KimiToolset::new();
    let tmp = TempDir::new().expect("temp dir");
    let soul = make_soul(runtime, llm, toolset, tmp.path());

    run_and_collect_turns(&soul, UserInput::Text("do it".to_string())).await;

    let history = soul.context().lock().await.history().to_vec();
    let prompt_text = ralph_prompt("do it");

    assert_eq!(
        history,
        vec![
            Message::new(Role::User, vec![ContentPart::Text(TextPart::new("do it"))]),
            Message::new(
                Role::Assistant,
                vec![ContentPart::Text(TextPart::new("first"))]
            ),
            Message::new(
                Role::User,
                vec![ContentPart::Text(TextPart::new(prompt_text))],
            ),
            Message::new(
                Role::Assistant,
                vec![ContentPart::Text(TextPart::new(
                    "done <choice>STOP</choice>"
                ))],
            ),
        ]
    );
}

#[tokio::test]
async fn native_ralph_stop_repairs_until_completion_gate_passes() {
    let fixture = RuntimeFixture::new();
    let mut runtime = fixture.runtime.clone();
    runtime.config.loop_control.max_ralph_iterations = -1;
    let mut first_gate = ToolCall::new("gate-1", "runtime_completion_gate");
    first_gate.function.arguments = Some("{}".to_string());
    let mut stop_gate = ToolCall::new("gate-2", "runtime_completion_gate");
    stop_gate.function.arguments = Some("{}".to_string());
    let llm = make_llm(
        vec![
            vec![ContentPart::Text(TextPart::new("premature task answer")).into()],
            vec![StreamedMessagePart::from(first_gate)],
            vec![ContentPart::Text(TextPart::new("task work complete")).into()],
            vec![ContentPart::Text(TextPart::new("<choice>STOP</choice>")).into()],
            vec![StreamedMessagePart::from(stop_gate)],
            vec![ContentPart::Text(TextPart::new("verified <choice>STOP</choice>")).into()],
        ],
        HashSet::new(),
    );
    let mut toolset = KimiToolset::new();
    toolset.add(Arc::new(PassingCompletionGate));
    let tmp = TempDir::new().expect("temp dir");
    let agent = Agent {
        name: "Test Agent".to_string(),
        system_prompt: "Test system prompt.".to_string(),
        toolset: Arc::new(tokio::sync::Mutex::new(toolset)),
        runtime: runtime_with_llm(runtime, llm),
        completion_gate_tools: vec!["runtime_completion_gate".to_string()],
        context_continuity_tool: None,
        output_contract: None,
    };
    let soul = KimiSoul::new(agent, Context::new(tmp.path().join("history.jsonl")));

    run_and_collect_turns(&soul, UserInput::Text("do it".to_string())).await;

    let history = soul.context().lock().await.history().to_vec();
    assert_eq!(
        history
            .iter()
            .filter(|message| message
                .extract_text("")
                .contains("native completion contract rejected"))
            .count(),
        2
    );
    assert!(history.last().is_some_and(|message| {
        message
            .extract_text("")
            .contains("verified <choice>STOP</choice>")
    }));
    let receipt = soul.completion_gate_snapshot().await;
    assert!(receipt.satisfied);
    assert_eq!(receipt.passed, vec!["runtime_completion_gate"]);
}

#[tokio::test]
async fn direct_flow_end_cannot_bypass_required_completion_gate() {
    let fixture = RuntimeFixture::new();
    let runtime = fixture.runtime.clone();
    let llm = make_llm(Vec::new(), HashSet::new());
    let tmp = TempDir::new().expect("temp dir");
    let agent = Agent {
        name: "Test Agent".to_string(),
        system_prompt: "Test system prompt.".to_string(),
        toolset: Arc::new(tokio::sync::Mutex::new(KimiToolset::new())),
        runtime: runtime_with_llm(runtime, llm),
        completion_gate_tools: vec!["runtime_completion_gate".to_string()],
        context_continuity_tool: None,
        output_contract: None,
    };
    let soul = KimiSoul::new(agent, Context::new(tmp.path().join("history.jsonl")));
    let flow = Flow::new(
        HashMap::from([
            (
                "BEGIN".to_string(),
                FlowNode::new("BEGIN", "BEGIN", FlowNodeKind::Begin),
            ),
            (
                "END".to_string(),
                FlowNode::new("END", "END", FlowNodeKind::End),
            ),
        ]),
        HashMap::from([
            (
                "BEGIN".to_string(),
                vec![FlowEdge::new("BEGIN", "END", None)],
            ),
            ("END".to_string(), Vec::new()),
        ]),
        "BEGIN",
        "END",
    );

    let error = FlowRunner::new(flow, Some("direct".to_string()), 1)
        .run(&soul, "")
        .await
        .expect_err("direct END must not bypass the gate");
    let incomplete = error
        .downcast_ref::<FlowIncomplete>()
        .expect("typed flow incomplete error");

    assert_eq!(incomplete.reason, "completion_gate_missing");
    assert_eq!(incomplete.node_id, "END");
    assert_eq!(
        incomplete.missing_completion_gates,
        vec!["runtime_completion_gate"]
    );
}

#[tokio::test]
async fn native_flow_injects_owner_arguments_into_first_actionable_node() {
    let fixture = RuntimeFixture::new();
    let mut runtime = fixture.runtime.clone();
    let llm = make_llm(
        vec![vec![ContentPart::Text(TextPart::new("done")).into()]],
        HashSet::new(),
    );
    let tmp = TempDir::new().expect("temp dir");
    let skill_dir = tmp.path().join("flow-skill");
    std::fs::create_dir_all(&skill_dir).expect("skill dir");
    let flow = Flow::new(
        HashMap::from([
            (
                "BEGIN".to_string(),
                FlowNode::new("BEGIN", "BEGIN", FlowNodeKind::Begin),
            ),
            (
                "TASK".to_string(),
                FlowNode::new("TASK", "Perform bounded work", FlowNodeKind::Task),
            ),
            (
                "END".to_string(),
                FlowNode::new("END", "END", FlowNodeKind::End),
            ),
        ]),
        HashMap::from([
            (
                "BEGIN".to_string(),
                vec![FlowEdge::new("BEGIN", "TASK", None)],
            ),
            ("TASK".to_string(), vec![FlowEdge::new("TASK", "END", None)]),
            ("END".to_string(), Vec::new()),
        ]),
        "BEGIN",
        "END",
    );
    runtime.skills = HashMap::from([(
        "flow-skill".to_string(),
        Skill {
            name: "flow-skill".to_string(),
            description: "Test flow".to_string(),
            skill_type: SkillType::Flow,
            dir: KaosPath::unsafe_from_local_path(&skill_dir),
            flow: Some(flow),
        },
    )]);
    let soul = make_soul(runtime, llm, KimiToolset::new(), tmp.path());

    run_and_collect_turns(
        &soul,
        UserInput::Text("/flow:flow-skill repair authentication".to_string()),
    )
    .await;

    let history = soul.context().lock().await.history().to_vec();
    assert_eq!(
        history.first().map(|message| message.extract_text("\n")),
        Some("Perform bounded work\n\nUser request:\nrepair authentication".to_string())
    );
}

#[tokio::test]
async fn test_ralph_loop_stops_on_tool_rejected() {
    let fixture = RuntimeFixture::new();
    let mut runtime = fixture.runtime.clone();
    runtime.config.loop_control.max_ralph_iterations = 3;

    let mut tool_call = ToolCall::new("call-1", "reject_tool");
    tool_call.function.arguments = Some("{}".to_string());

    let llm = make_llm(
        vec![vec![StreamedMessagePart::from(tool_call.clone())]],
        HashSet::new(),
    );

    let mut toolset = KimiToolset::new();
    toolset.add(Arc::new(RejectTool));

    let tmp = TempDir::new().expect("temp dir");
    let soul = make_soul(runtime, llm, toolset, tmp.path());

    let result = run_soul(
        &soul,
        UserInput::Text("do it".to_string()),
        |_wire| async { Ok(()) },
        CancellationToken::new(),
        None,
    )
    .await;

    let error = result.expect_err("tool rejection must not report flow success");
    let incomplete = error
        .downcast_ref::<FlowIncomplete>()
        .expect("typed flow incomplete error");
    assert_eq!(incomplete.reason, "tool_rejected");

    let history = soul.context().lock().await.history().to_vec();
    let tool_message = tool_result_to_message(&ToolResult {
        tool_call_id: "call-1".to_string(),
        return_value: tool_rejected_error(),
    });

    assert_eq!(
        history,
        vec![
            Message::new(Role::User, vec![ContentPart::Text(TextPart::new("do it"))]),
            Message {
                role: Role::Assistant,
                content: Vec::new(),
                name: None,
                tool_calls: Some(vec![tool_call]),
                tool_call_id: None,
                partial: None,
            },
            tool_message,
        ]
    );
}

#[tokio::test]
async fn test_ralph_loop_disabled_skips_loop_prompt() {
    let fixture = RuntimeFixture::new();
    let mut runtime = fixture.runtime.clone();
    runtime.config.loop_control.max_ralph_iterations = 0;

    let llm = make_llm(
        vec![vec![ContentPart::Text(TextPart::new("done")).into()]],
        HashSet::new(),
    );

    let toolset = KimiToolset::new();
    let tmp = TempDir::new().expect("temp dir");
    let soul = make_soul(runtime, llm, toolset, tmp.path());

    run_and_collect_turns(&soul, UserInput::Text("hello".to_string())).await;

    let history = soul.context().lock().await.history().to_vec();
    assert_eq!(
        history,
        vec![
            Message::new(Role::User, vec![ContentPart::Text(TextPart::new("hello"))]),
            Message::new(
                Role::Assistant,
                vec![ContentPart::Text(TextPart::new("done"))]
            ),
        ]
    );
}
