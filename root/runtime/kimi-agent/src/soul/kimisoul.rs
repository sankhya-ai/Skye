use std::collections::HashMap;
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Duration;

use rand::Rng;
use tempfile::tempdir;
use thiserror::Error;
use tokio::sync::mpsc;
use tracing::{debug, error, info, warn};

use kosong::chat_provider::{ChatProvider, ChatProviderError, ChatProviderErrorKind, ThinkingEffort};
use kosong::message::{ContentPart, Message, Role, StreamedMessagePart, TextPart, ToolCall};
use kosong::tooling::{ToolOutput, Toolset};
use kosong::{StepResult, dispatch as kosong_dispatch, generate as kosong_generate, step as kosong_step};

use crate::config::ModelCapability;
use crate::council_budget::{CouncilBudgetExceeded, WALL_CLOCK_BUDGET_EXHAUSTED};
use crate::skill::flow::{Flow, FlowEdge, FlowLabel, FlowNode, FlowNodeKind, parse_choice};
use crate::skill::{Skill, SkillType, read_skill_text};
use crate::soul::repeat_guard::RepeatGuard;
use crate::soul::leaked_calls::LeakedCalls;
use crate::soul::verify_gate::VerifyGate;
use crate::soul::agent::{Agent, Runtime};
use crate::soul::decision::JevClient;
use crate::soul::director::{self, Director, DirectorConfig, DirectorMode, Directive, Phase};
use crate::soul::{
    FlowIncomplete, LLMNotSet, LLMNotSupported, MaxStepsReached, Soul, StatusSnapshot,
    approval::Approval,
    compaction::{Compaction, SimpleCompaction},
    completion_gate::{CompletionGate, CompletionGateSnapshot},
    context::Context,
    message::{
        check_message, sanitize_tool_call_arguments_for_history, system, tool_result_to_message,
    },
    wire_send,
};
use crate::tools::utils::is_tool_rejected;
use crate::utils::{SlashCommandInfo, parse_slash_command_call};
use crate::wire::{
    ApprovalRequest, ApprovalResponse, CompactionBegin, CompactionEnd, StatusUpdate, StepBegin,
    StepInterrupted, StepRetry, TurnBegin, TurnEnd, UserInput, WireMessage,
};

const SKILL_COMMAND_PREFIX: &str = "skill:";
const FLOW_COMMAND_PREFIX: &str = "flow:";
const DEFAULT_MAX_FLOW_MOVES: i64 = 1000;
const FLOW_REASON_BEGIN_WITHOUT_EDGE: &str = "begin_without_outgoing_edge";
const FLOW_REASON_NODE_WITHOUT_EDGE: &str = "node_without_outgoing_edge";
const FLOW_REASON_TOOL_REJECTED: &str = "tool_rejected";
const FLOW_REASON_NO_NEXT_NODE: &str = "no_next_node";
const FLOW_REASON_COMPLETION_GATE_MISSING: &str = "completion_gate_missing";

type StepStopReason = &'static str;

/// Reopenings of a turn that ended in words. Two covers "write, then grade"
/// and one correction after it; more is a loop.
const MAX_FOLLOW_UPS: usize = 2;

pub struct StepOutcome {
    pub stop_reason: StepStopReason,
    pub assistant_message: Message,
}

pub struct TurnOutcome {
    pub stop_reason: StepStopReason,
    pub final_message: Option<Message>,
    pub step_count: i64,
}

#[derive(Clone, Debug, Error)]
#[error("back to the future")]
struct BackToTheFuture {
    checkpoint_id: i64,
    messages: Vec<Message>,
}

pub struct KimiSoul {
    agent: Agent,
    runtime: Runtime,
    context: tokio::sync::Mutex<Context>,
    compaction: SimpleCompaction,
    checkpoint_with_user_message: bool,
    completion_gate: tokio::sync::Mutex<CompletionGate>,
    /// Decides, before each step, whether it replies or which tool it calls.
    /// `None` when there is no key, no OpenRouter-shaped provider, or it is
    /// switched off — in all of which the loop behaves exactly as it did
    /// before the director existed. See [`crate::soul::director`].
    director: Option<Director>,
    /// Tool calls the harness has made this turn. Reset per turn and capped:
    /// past the cap the model steers alone, so a wrong read can cost a few
    /// steps but can never loop a turn into `max_steps_per_turn`.
    directed_calls: AtomicUsize,
    /// Steps taken this turn, for the director's log line and record.
    steps_taken: AtomicUsize,
    /// A decision already made for the next step. Set when a step that ended
    /// in words was read as having a tool call still to come, so the next step
    /// does not ask the same question twice.
    next_directive: std::sync::Mutex<Option<Directive>>,
    /// Turns reopened this turn by [`KimiSoul::follow_up`]. Capped, so a read
    /// that keeps finding one more call can cost seconds, never the turn.
    follow_ups: AtomicUsize,
    /// Identical calls with identical results this turn; see [`RepeatGuard`].
    repeat_guard: std::sync::Mutex<RepeatGuard>,
    /// Edits this turn that nothing has run since; see [`VerifyGate`].
    verify_gate: std::sync::Mutex<VerifyGate>,
    /// Tool calls written as reply text this turn; see [`LeakedCalls`].
    leaked_calls: std::sync::Mutex<LeakedCalls>,
    slash_commands: Vec<SlashCommandInfo>,
    slash_handlers: HashMap<String, SlashHandler>,
}

enum SlashHandler {
    Builtin(BuiltinSlash),
    Skill(Skill),
    Flow(FlowRunner),
}

#[derive(Clone, Copy)]
enum BuiltinSlash {
    Init,
    Compact,
    Clear,
    Yolo,
}

impl KimiSoul {
    pub fn new(agent: Agent, context: Context) -> Self {
        let completion_gate = CompletionGate::new(agent.completion_gate_tools.clone());
        let checkpoint_with_user_message = agent
            .toolset
            .try_lock()
            .map(|guard| guard.tools().iter().any(|tool| tool.name == "SendDMail"))
            .unwrap_or(false);

        let director = DirectorConfig::from_env().and_then(|config| {
            agent
                .runtime
                .llm
                .as_ref()
                .and_then(|llm| llm.provider_config.as_ref())
                .and_then(|provider| JevClient::new(&provider.base_url, &provider.api_key))
                .map(|jev| Director::new(jev, config))
        });
        // At info: whether the director is live is the first thing anyone
        // debugging a turn's tool use needs to know, and it is written once per
        // agent rather than once per step.
        info!(
            "Step director: {}",
            match director.as_ref().map(|d| d.config().mode) {
                Some(DirectorMode::On) => "on (decision model)",
                Some(DirectorMode::Shadow) => "shadow (decides and records, changes nothing)",
                None => "off — the model steers every step",
            }
        );

        let mut soul = KimiSoul {
            runtime: agent.runtime.clone(),
            agent,
            context: tokio::sync::Mutex::new(context),
            compaction: SimpleCompaction::new(2),
            checkpoint_with_user_message,
            completion_gate: tokio::sync::Mutex::new(completion_gate),
            director,
            directed_calls: AtomicUsize::new(0),
            steps_taken: AtomicUsize::new(0),
            next_directive: std::sync::Mutex::new(None),
            follow_ups: AtomicUsize::new(0),
            repeat_guard: std::sync::Mutex::new(RepeatGuard::default()),
            verify_gate: std::sync::Mutex::new(VerifyGate::from_env()),
            leaked_calls: std::sync::Mutex::new(LeakedCalls::default()),
            slash_commands: Vec::new(),
            slash_handlers: HashMap::new(),
        };
        soul.build_slash_commands();
        soul
    }

    /// Replace the director this soul was built with. Tests use it to point
    /// the loop at a scripted decision endpoint; `None` switches it off.
    pub fn with_director(mut self, director: Option<Director>) -> Self {
        self.director = director;
        self
    }

    pub fn agent(&self) -> &Agent {
        &self.agent
    }

    pub fn runtime(&self) -> &Runtime {
        &self.runtime
    }

    pub fn context(&self) -> &tokio::sync::Mutex<Context> {
        &self.context
    }

    pub async fn completion_gate_snapshot(&self) -> CompletionGateSnapshot {
        self.completion_gate.lock().await.snapshot()
    }

    fn build_slash_commands(&mut self) {
        let mut commands = Vec::new();
        let mut handlers = HashMap::new();

        let builtin = vec![
            (
                "init",
                "Analyze the codebase and generate an `AGENTS.md` file",
                BuiltinSlash::Init,
                vec![],
            ),
            (
                "compact",
                "Compact the context",
                BuiltinSlash::Compact,
                vec![],
            ),
            (
                "clear",
                "Clear the context",
                BuiltinSlash::Clear,
                vec!["reset"],
            ),
            (
                "yolo",
                "Toggle YOLO mode (auto-approve all actions)",
                BuiltinSlash::Yolo,
                vec![],
            ),
        ];

        for (name, description, kind, aliases) in builtin {
            commands.push(SlashCommandInfo {
                name: name.to_string(),
                description: description.to_string(),
                aliases: aliases.iter().map(|s| s.to_string()).collect(),
            });
            handlers.insert(name.to_string(), SlashHandler::Builtin(kind));
            for alias in aliases {
                handlers.insert(alias.to_string(), SlashHandler::Builtin(kind));
            }
        }

        let mut seen = handlers
            .keys()
            .cloned()
            .collect::<std::collections::HashSet<_>>();
        let mut skills: Vec<_> = self.runtime.skills.values().cloned().collect();
        skills.sort_by(|a, b| a.name.cmp(&b.name));

        for skill in &skills {
            if skill.skill_type != SkillType::Standard && skill.skill_type != SkillType::Flow {
                continue;
            }
            let name = format!("{SKILL_COMMAND_PREFIX}{}", skill.name);
            if seen.contains(&name) {
                warn!(
                    "Skipping skill slash command /{}: name already registered",
                    name
                );
                continue;
            }
            commands.push(SlashCommandInfo {
                name: name.clone(),
                description: skill.description.clone(),
                aliases: Vec::new(),
            });
            handlers.insert(name.clone(), SlashHandler::Skill(skill.clone()));
            seen.insert(name);
        }

        for skill in &skills {
            if skill.skill_type != SkillType::Flow {
                continue;
            }
            if skill.flow.is_none() {
                warn!("Flow skill {} has no flow; skipping", skill.name);
                continue;
            }
            let name = format!("{FLOW_COMMAND_PREFIX}{}", skill.name);
            if seen.contains(&name) {
                warn!(
                    "Skipping prompt flow slash command /{}: name already registered",
                    name
                );
                continue;
            }
            let runner = FlowRunner::new(
                skill.flow.clone().unwrap(),
                Some(skill.name.clone()),
                DEFAULT_MAX_FLOW_MOVES,
            );
            commands.push(SlashCommandInfo {
                name: name.clone(),
                description: skill.description.clone(),
                aliases: Vec::new(),
            });
            handlers.insert(name.clone(), SlashHandler::Flow(runner));
            seen.insert(name);
        }

        self.slash_commands = commands;
        self.slash_handlers = handlers;
    }

    async fn checkpoint(&self) -> anyhow::Result<()> {
        let mut context = self.context.lock().await;
        context
            .checkpoint(self.checkpoint_with_user_message)
            .await?;
        Ok(())
    }

    async fn handle_slash(&self, name: &str, args: &str) -> anyhow::Result<()> {
        match self.slash_handlers.get(name) {
            Some(SlashHandler::Builtin(kind)) => match kind {
                BuiltinSlash::Init => self.slash_init().await,
                BuiltinSlash::Compact => self.slash_compact().await,
                BuiltinSlash::Clear => self.slash_clear().await,
                BuiltinSlash::Yolo => self.slash_yolo().await,
            },
            Some(SlashHandler::Skill(skill)) => self.run_skill(skill, args).await,
            Some(SlashHandler::Flow(runner)) => runner.run(self, args).await,
            None => {
                wire_send(WireMessage::ContentPart(ContentPart::Text(TextPart::new(
                    format!("Unknown slash command \"/{}\".", name),
                ))));
                Ok(())
            }
        }
    }

    async fn slash_init(&self) -> anyhow::Result<()> {
        let temp_dir = tempdir()?;
        let tmp_path = temp_dir.path().join("context.jsonl");
        let tmp_context = Context::new(tmp_path);
        let tmp_soul = KimiSoul::new(self.agent.clone(), tmp_context);
        tmp_soul
            .run(UserInput::Text(crate::prompts::INIT.to_string()))
            .await?;

        let agents_md =
            crate::soul::agent::load_agents_md(&self.runtime.builtin_args.KIMI_WORK_DIR)
                .await
                .unwrap_or_default();
        let system_message = system(&format!(
            "The user just ran `/init` slash command. The system has analyzed the codebase and generated an `AGENTS.md` file. Latest AGENTS.md file content:\n{}",
            agents_md
        ));
        let mut context = self.context.lock().await;
        context
            .append_messages(Message::new(Role::User, vec![system_message]))
            .await?;
        Ok(())
    }

    async fn slash_compact(&self) -> anyhow::Result<()> {
        info!("Running `/compact`");
        let context = self.context.lock().await;
        if context.n_checkpoints() == 0 {
            wire_send(WireMessage::ContentPart(ContentPart::Text(TextPart::new(
                "The context is empty.",
            ))));
            return Ok(());
        }
        drop(context);
        self.compact_context().await?;
        wire_send(WireMessage::ContentPart(ContentPart::Text(TextPart::new(
            "The context has been compacted.",
        ))));
        self.send_status_update();
        Ok(())
    }

    async fn slash_clear(&self) -> anyhow::Result<()> {
        info!("Running `/clear`");
        {
            let mut context = self.context.lock().await;
            context.clear().await?;
        }
        wire_send(WireMessage::ContentPart(ContentPart::Text(TextPart::new(
            "The context has been cleared.",
        ))));
        self.send_status_update();
        Ok(())
    }

    fn send_status_update(&self) {
        let context_usage = if let Some(llm) = &self.runtime.llm {
            match self.context.try_lock() {
                Ok(context) => context.token_count() as f64 / llm.max_context_size as f64,
                Err(_) => 0.0,
            }
        } else {
            0.0
        };
        let status = StatusUpdate {
            context_usage: Some(context_usage),
            token_usage: None,
            message_id: None,
            retry: None,
        };
        wire_send(WireMessage::StatusUpdate(status));
    }

    async fn slash_yolo(&self) -> anyhow::Result<()> {
        if self.runtime.approval.is_yolo() {
            self.runtime.approval.set_yolo(false);
            wire_send(WireMessage::ContentPart(ContentPart::Text(TextPart::new(
                "You only die once! Actions will require approval.",
            ))));
        } else {
            self.runtime.approval.set_yolo(true);
            wire_send(WireMessage::ContentPart(ContentPart::Text(TextPart::new(
                "You only live once! All actions will be auto-approved.",
            ))));
        }
        Ok(())
    }

    async fn run_skill(&self, skill: &Skill, args: &str) -> anyhow::Result<()> {
        let Some(mut skill_text) = read_skill_text(skill).await else {
            wire_send(WireMessage::ContentPart(ContentPart::Text(TextPart::new(
                format!(
                    "Failed to load skill \"/{}{}\".",
                    SKILL_COMMAND_PREFIX, skill.name
                ),
            ))));
            return Ok(());
        };

        let extra = args.trim();
        if !extra.is_empty() {
            skill_text = format!("{skill_text}\n\nUser request:\n{extra}");
        }
        let message = Message::new(
            Role::User,
            vec![ContentPart::Text(TextPart::new(skill_text))],
        );
        self.turn(message).await?;
        Ok(())
    }

    async fn turn(&self, user_message: Message) -> Result<TurnOutcome, anyhow::Error> {
        let llm = self.runtime.llm.as_ref().ok_or_else(|| LLMNotSet)?;
        let missing = check_message(&user_message, &llm.capabilities);
        if !missing.is_empty() {
            return Err(anyhow::Error::new(LLMNotSupported::new(
                llm.model_name(),
                missing.into_iter().collect(),
            )));
        }

        self.completion_gate.lock().await.reset();
        self.directed_calls.store(0, Ordering::Relaxed);
        self.steps_taken.store(0, Ordering::Relaxed);
        self.take_next_directive();
        self.follow_ups.store(0, Ordering::Relaxed);
        if let Ok(mut guard) = self.repeat_guard.lock() {
            guard.reset();
        }
        if let Ok(mut gate) = self.verify_gate.lock() {
            gate.reset();
        }
        if let Ok(mut leaked) = self.leaked_calls.lock() {
            leaked.reset();
        }
        self.checkpoint().await?;
        {
            let mut context = self.context.lock().await;
            context.append_messages(user_message).await?;
        }
        debug!("Appended user message to context");
        self.agent_loop().await
    }

    async fn agent_loop(&self) -> Result<TurnOutcome, anyhow::Error> {
        let mcp_task = {
            let mut toolset = self.agent.toolset.lock().await;
            toolset.take_mcp_loading_task()
        };
        if let Some(task) = mcp_task {
            let _ = task.await;
        }

        let mut step_no = 0;
        loop {
            step_no += 1;
            if step_no > self.runtime.config.loop_control.max_steps_per_turn {
                return Err(anyhow::Error::new(MaxStepsReached::new(
                    self.runtime.config.loop_control.max_steps_per_turn,
                )));
            }

            wire_send(WireMessage::StepBegin(StepBegin { n: step_no }));
            let approval_task = spawn_approval_task(Arc::clone(&self.runtime.approval));

            self.runtime
                .council_budget
                .check_token_budget()
                .map_err(|reason| anyhow::Error::new(CouncilBudgetExceeded::new(reason)))?;
            // The cost cap used to gate only subagent tasks and compaction; the main loop never stopped on it.
            self.runtime
                .council_budget
                .check_cost_budget()
                .map_err(|reason| anyhow::Error::new(CouncilBudgetExceeded::new(reason)))?;
            self.runtime
                .council_budget
                .check_wall_clock()
                .map_err(|reason| anyhow::Error::new(CouncilBudgetExceeded::new(reason)))?;
            let step_future = async {
                if let Some(llm) = &self.runtime.llm {
                    let context = self.context.lock().await;
                    if context.token_count()
                        + self.runtime.config.loop_control.reserved_context_size
                        >= llm.max_context_size
                    {
                        drop(context);
                        info!("Context too long, compacting...");
                        self.compact_context().await?;
                    }
                }

                debug!("Beginning step {}", step_no);
                self.checkpoint().await?;
                {
                    let checkpoints = self.context.lock().await.n_checkpoints();
                    let mut denwa = self.runtime.denwa_renji.lock().await;
                    denwa.set_n_checkpoints(checkpoints);
                }

                self.step().await
            };
            let step_result =
                match self.runtime.council_budget.remaining_wall_time() {
                    Ok(Some(remaining)) => tokio::time::timeout(remaining, step_future)
                        .await
                        .map_err(|_| {
                            anyhow::Error::new(CouncilBudgetExceeded::new(
                                WALL_CLOCK_BUDGET_EXHAUSTED,
                            ))
                        })?,
                    Ok(None) => step_future.await,
                    Err(reason) => {
                        return Err(anyhow::Error::new(CouncilBudgetExceeded::new(reason)));
                    }
                };

            let mut back_to_future: Option<BackToTheFuture> = None;
            let mut step_error: Option<anyhow::Error> = None;
            let step_outcome = match step_result {
                Ok(outcome) => outcome,
                Err(err) => {
                    if let Some(back) = err.downcast_ref::<BackToTheFuture>() {
                        back_to_future = Some(back.clone());
                        None
                    } else {
                        wire_send(WireMessage::StepInterrupted(StepInterrupted {}));
                        step_error = Some(err);
                        None
                    }
                }
            };

            approval_task.stop().await;

            if let Some(err) = step_error {
                return Err(err);
            }

            if let Some(outcome) = step_outcome {
                let final_message = if outcome.stop_reason == "no_tool_calls" {
                    Some(outcome.assistant_message)
                } else {
                    None
                };
                return Ok(TurnOutcome {
                    stop_reason: outcome.stop_reason,
                    final_message,
                    step_count: step_no,
                });
            }

            if let Some(back_to_future) = back_to_future {
                {
                    let mut context = self.context.lock().await;
                    context.revert_to(back_to_future.checkpoint_id).await?;
                }
                self.checkpoint().await?;
                {
                    let mut context = self.context.lock().await;
                    context.append_messages(back_to_future.messages).await?;
                }
            }
        }
    }

    async fn step(&self) -> Result<Option<StepOutcome>, anyhow::Error> {
        let llm = self.runtime.llm.as_ref().ok_or_else(|| LLMNotSet)?;
        let step_no = self.steps_taken.fetch_add(1, Ordering::Relaxed) + 1;

        let directive = match self.take_next_directive() {
            Some(decided) => Some(decided),
            None => self.direct(step_no, Phase::BeforeStep).await,
        };
        if let Some(Directive::Call { tool, .. }) = &directive {
            if let Some(outcome) = self.directed_step(tool).await? {
                return Ok(outcome);
            }
            // The arguments could not be written. The model takes the step
            // itself, exactly as it would have without the director.
        }
        let steered = directive
            .as_ref()
            .and_then(Directive::overrides)
            .and_then(|overrides| llm.chat_provider.with_generation_overrides(&overrides));
        let provider = steered
            .as_deref()
            .unwrap_or_else(|| llm.chat_provider.as_ref());

        let mut attempts = 0usize;
        let mut retry_hint: Option<Message> = None;
        // (root; see VENDORED.md) A step that came back empty spent its whole
        // output on reasoning. Measured on OpenRouter, effort and budget caps
        // do not bound that for some models — only switching reasoning off
        // does — so the retry acts without it instead of reasoning out again.
        let mut exhausted = false;
        let mut unthinking: Option<Box<dyn ChatProvider>> = None;
        let (result, forward_task) = loop {
            attempts += 1;
            if exhausted && unthinking.is_none() {
                info!("The last attempt reasoned to the output limit; retrying with reasoning off");
                unthinking = Some(provider.with_thinking(ThinkingEffort::Off));
            }
            let provider: &dyn ChatProvider = unthinking.as_deref().unwrap_or(provider);
            let (message_tx, mut message_rx) = mpsc::unbounded_channel();
            let (tool_tx, mut tool_rx) = mpsc::unbounded_channel();

            let mut history = { self.context.lock().await.history().to_vec() };
            if let Some(hint) = retry_hint.clone() {
                history.push(hint);
            }
            let toolset = self.agent.toolset.lock().await;
            let step_future = kosong_step(
                provider,
                &self.agent.system_prompt,
                &*toolset,
                &history,
                Some(message_tx),
                Some(tool_tx),
            );
            tokio::pin!(step_future);

            let mut owner_visible = false;
            let mut message_stream_open = true;
            let step_result = loop {
                tokio::select! {
                    biased;
                    part = message_rx.recv(), if message_stream_open => {
                        match part {
                            Some(part) => {
                                owner_visible |= streamed_part_is_owner_visible(&part);
                                forward_streamed_message_part(part);
                            }
                            None => message_stream_open = false,
                        }
                    }
                    result = &mut step_future => break result,
                }
            };

            // A provider can enqueue a final delta and fail or finish in the
            // same poll. Preserve that ordering before deciding whether the
            // attempt is still safe to retry.
            while let Ok(part) = message_rx.try_recv() {
                owner_visible |= streamed_part_is_owner_visible(&part);
                forward_streamed_message_part(part);
            }

            match step_result {
                Ok(res) => {
                    let handle = crate::soul::spawn_with_current_wire(async move {
                        while let Some(tool_result) = tool_rx.recv().await {
                            wire_send(WireMessage::ToolResult(tool_result));
                        }
                    });
                    break (res, handle);
                }
                Err(err) => {
                    drop(tool_rx);
                    let Some(class) = retry_class(&err) else {
                        return Err(anyhow::Error::new(err));
                    };
                    let max_attempts = match class {
                        RetryClass::Provider => {
                            self.runtime.config.loop_control.max_retries_per_step as usize
                        }
                        RetryClass::Model => MODEL_RETRY_ATTEMPTS
                            .min(self.runtime.config.loop_control.max_retries_per_step as usize),
                    };
                    if owner_visible || attempts >= max_attempts {
                        return Err(anyhow::Error::new(err));
                    }
                    retry_hint = retry_hint_for_error(&err);
                    exhausted |= matches!(err.kind, ChatProviderErrorKind::EmptyResponse);
                    let delay = retry_delay(class, attempts);
                    info!(
                        "Retrying step for the {} time. Waiting {} seconds.",
                        attempts,
                        delay.as_secs_f64()
                    );
                    wire_send(WireMessage::StatusUpdate(StatusUpdate {
                        context_usage: None,
                        token_usage: None,
                        message_id: None,
                        retry: Some(StepRetry {
                            attempt: attempts,
                            max_attempts,
                            delay_seconds: delay.as_secs_f64(),
                            error: err.to_string().chars().take(300).collect(),
                        }),
                    }));
                    tokio::time::sleep(delay).await;
                }
            }
        };

        let outcome = self.handle_step_result(result).await;
        let _ = forward_task.await;
        outcome
    }

    async fn handle_step_result(
        &self,
        mut result: StepResult,
    ) -> Result<Option<StepOutcome>, anyhow::Error> {
        debug!(
            "Got step result: id={}, tool_calls={}",
            result.id.as_deref().unwrap_or(""),
            result.tool_calls.len()
        );
        let usage = result.usage.clone();
        if let Some(usage) = &usage {
            // This provider call has already completed and been paid for. Record
            // its receipt, then enforce the cap before another model step; do
            // not discard a valid final answer merely because its input context
            // crossed the turn estimate.
            self.runtime
                .council_budget
                .record_tokens(usage.total().max(0) as u64);
            if let Some(cost_usd) = usage.cost_usd {
                self.runtime.council_budget.record_cost_usd(cost_usd);
            }
            let mut context = self.context.lock().await;
            context.update_token_count(usage.input()).await?;
        }

        let mut status = StatusUpdate {
            context_usage: None,
            token_usage: usage.clone(),
            message_id: result.id.clone(),
            retry: None,
        };
        if usage.is_some() {
            status.context_usage = Some(self.status().context_usage);
        }
        wire_send(WireMessage::StatusUpdate(status));

        let tool_results = result.tool_results().await?;
        debug!("Got tool results: {}", tool_results.len());
        self.completion_gate
            .lock()
            .await
            .observe(&result.tool_calls, &tool_results);
        if let Ok(mut gate) = self.verify_gate.lock() {
            gate.observe(&result.tool_calls, &tool_results);
        }
        let tool_results = match self.repeat_guard.lock() {
            Ok(mut guard) => guard.annotate(&result.tool_calls, &tool_results),
            Err(_) => tool_results,
        };
        self.grow_context(&result, &tool_results).await?;

        let rejected = tool_results
            .iter()
            .any(|tr| is_tool_rejected(&tr.return_value));
        if rejected {
            let _ = self.runtime.denwa_renji.lock().await.fetch_pending_dmail();
            return Ok(Some(StepOutcome {
                stop_reason: "tool_rejected",
                assistant_message: result.message,
            }));
        }

        if let Some(dmail) = self.runtime.denwa_renji.lock().await.fetch_pending_dmail() {
            let message = Message::new(
                Role::User,
                vec![system(&format!(
                    "You just got a D-Mail from your future self. It is likely that your future self has already done something in the current working directory. Please read the D-Mail and decide what to do next. You MUST NEVER mention to the user about this information. D-Mail content:\n\n{}",
                    dmail.message.trim()
                ))],
            );
            return Err(anyhow::Error::new(BackToTheFuture {
                checkpoint_id: dmail.checkpoint_id,
                messages: vec![message],
            }));
        }

        if !result.tool_calls.is_empty() {
            return Ok(None);
        }
        let missing_completion_gates = self.completion_gate.lock().await.missing();
        if !missing_completion_gates.is_empty() {
            let message = Message::new(
                Role::User,
                vec![system(&format!(
                    "The native completion contract rejected that final answer. Before finishing this turn, call these completion-gate tools successfully: {}. A failed gate means continue working and repair the blocked proof; do not merely repeat the answer.",
                    missing_completion_gates.join(", ")
                ))],
            );
            self.context.lock().await.append_messages(message).await?;
            return Ok(None);
        }
        let written = director::message_text(&result.message);
        let leaked = self.leaked_calls.lock().ok().and_then(|mut calls| calls.hold(&written));
        if let Some(note) = leaked {
            info!("A tool call was written as reply text; holding the turn open");
            let message = Message::new(Role::User, vec![system(note)]);
            self.context.lock().await.append_messages(message).await?;
            return Ok(None);
        }
        let hold = self.verify_gate.lock().ok().and_then(|mut gate| gate.hold());
        if let Some(note) = hold {
            info!("Verify gate: the turn edited files and ran nothing since; holding it open");
            let message = Message::new(Role::User, vec![system(&note)]);
            self.context.lock().await.append_messages(message).await?;
            return Ok(None);
        }
        if let Some(follow_up) = self.follow_up().await {
            // The words stay where they are; the harness makes the call that
            // was still to come as the next step, and the turn goes on.
            *self
                .next_directive
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(follow_up);
            return Ok(None);
        }
        Ok(Some(StepOutcome {
            stop_reason: "no_tool_calls",
            assistant_message: result.message,
        }))
    }

    fn take_next_directive(&self) -> Option<Directive> {
        self.next_directive
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .take()
    }

    /// Whether a step that ended in words has a tool call still to come.
    ///
    /// A turn ends when a step makes no tool call, and that is the model's
    /// decision to stop, made as a side effect of writing. Briefs routinely ask
    /// for words THEN a call — "say your piece, then grade" — and a step that
    /// was told to write can only write. So the director reads the turn once
    /// more with the words in it. Only a confident, named tool reopens it;
    /// "reply" or an unsure read ends the turn exactly as before.
    ///
    /// This is the question the old completion gate asked, grounded: the
    /// options are the real catalogue plus the reply, so "work remains" can
    /// only ever mean a tool that exists — never a `file_edit` a tutor does
    /// not have.
    async fn follow_up(&self) -> Option<Directive> {
        if self.follow_ups.load(Ordering::Relaxed) >= MAX_FOLLOW_UPS {
            return None;
        }
        let step_no = self.steps_taken.load(Ordering::Relaxed);
        match self.direct(step_no, Phase::TurnEnd).await? {
            call @ Directive::Call { .. } => {
                self.follow_ups.fetch_add(1, Ordering::Relaxed);
                Some(call)
            }
            _ => None,
        }
    }

    /// The director's read of the next step, or `None` to leave it alone.
    ///
    /// Shadow mode decides and records but returns `None`, so a rollout can
    /// measure the director against the model's own choices on real turns
    /// before it is allowed to change one.
    async fn direct(&self, step_no: usize, phase: Phase) -> Option<Directive> {
        let director = self.director.as_ref()?;
        let tools = { self.agent.toolset.lock().await.tools() };
        let history = { self.context.lock().await.history().to_vec() };
        let (facts, unchecked) = match self.verify_gate.lock() {
            Ok(gate) => (Some(serde_json::json!({ "turn_facts": gate.facts() })), gate.unchecked()),
            Err(_) => (None, false),
        };
        let started = std::time::Instant::now();
        let mut decision = director
            .decide(&self.agent.system_prompt, &history, &tools, facts.as_ref(), phase)
            .await?;
        let mode = director.config().mode;

        // Reading is not a check. With edits nothing has exercised, the harness
        // does not force another look; the model (and the gate) move it on.
        if unchecked
            && matches!(&decision.directive, Directive::Call { tool, .. } if matches!(tool.as_str(), "ReadFile" | "Glob" | "Grep"))
        {
            decision.directive = Directive::Free {
                why: "edits not yet exercised",
            };
        }

        // Past the cap the model steers alone: a wrong read may cost a few
        // steps, never the whole turn.
        if matches!(decision.directive, Directive::Call { .. })
            && self.directed_calls.load(Ordering::Relaxed) >= director.config().max_directed_calls
        {
            decision.directive = Directive::Free {
                why: "directed-call cap reached",
            };
        }

        director::log_decision(step_no, &decision, mode, started.elapsed());
        let mut record = decision.record.clone();
        record["step"] = serde_json::json!(step_no);
        record["phase"] = serde_json::json!(phase.as_str());
        record["mode"] = serde_json::json!(if mode == DirectorMode::Shadow { "shadow" } else { "on" });
        record["directive"] = serde_json::json!(decision.directive.label());
        director::record(&record);

        match mode {
            DirectorMode::Shadow => None,
            DirectorMode::On => Some(decision.directive),
        }
    }

    /// Make the call the director chose: the model writes the arguments, the
    /// harness builds the call and dispatches it.
    ///
    /// `Ok(None)` means the arguments could not be written — the provider
    /// cannot take per-call fields, the call failed, or the reply was not a
    /// JSON object — and the caller lets the model take the step itself. A
    /// synthesis failure must never cost the turn.
    async fn directed_step(
        &self,
        tool_name: &str,
    ) -> Result<Option<Option<StepOutcome>>, anyhow::Error> {
        let llm = self.runtime.llm.as_ref().ok_or_else(|| LLMNotSet)?;
        let toolset = self.agent.toolset.lock().await;
        let tools = toolset.tools();
        let Some(tool) = tools.iter().find(|tool| tool.name == tool_name).cloned() else {
            return Ok(None);
        };
        let Some(provider) =
            llm.chat_provider
                .with_generation_overrides(&director::argument_overrides(&tool))
        else {
            debug!("Provider cannot take per-call fields; the model takes this step");
            return Ok(None);
        };
        // Writing the arguments of a call already decided needs no reasoning;
        // with it, a reasoning model can think to the output limit and return
        // nothing (root; see VENDORED.md).
        let provider = provider.with_thinking(ThinkingEffort::Off);
        let mut history = { self.context.lock().await.history().to_vec() };
        history.push(director::argument_request(&tool));

        let generated = match kosong_generate(
            provider.as_ref(),
            &self.agent.system_prompt,
            tools,
            &history,
            None,
            None,
        )
        .await
        {
            Ok(generated) => generated,
            Err(error) => {
                info!("Director could not get arguments for {tool_name} ({error}); the model takes this step");
                return Ok(None);
            }
        };
        let text = director::message_text(&generated.message);
        let Some(arguments) = director::parse_arguments(&text) else {
            info!("Director got no argument object for {tool_name}; the model takes this step");
            return Ok(None);
        };
        if !director::fits(&arguments, &tool) {
            info!("Director's arguments do not fit {tool_name} (the model meant another step); the model takes this step");
            return Ok(None);
        }

        let mut call = ToolCall::new(
            format!("directed_{}", uuid::Uuid::new_v4().simple()),
            tool_name,
        );
        call.function.arguments = Some(arguments);
        // Reasoning is kept so a provider that needs it round-tripped sees the
        // same shape as a call the model made; the argument text is not, since
        // it now lives in the call itself.
        let mut message = Message::new(
            Role::Assistant,
            generated
                .message
                .content
                .into_iter()
                .filter(|part| matches!(part, ContentPart::Think(_)))
                .collect(),
        );
        message.tool_calls = Some(vec![call.clone()]);
        forward_streamed_message_part(StreamedMessagePart::ToolCall(call));
        self.directed_calls.fetch_add(1, Ordering::Relaxed);

        let (tool_tx, mut tool_rx) = mpsc::unbounded_channel();
        let result = kosong_dispatch(
            &*toolset,
            generated.id,
            message,
            generated.usage,
            Some(tool_tx),
        );
        drop(toolset);
        let forward = crate::soul::spawn_with_current_wire(async move {
            while let Some(tool_result) = tool_rx.recv().await {
                wire_send(WireMessage::ToolResult(tool_result));
            }
        });
        let outcome = self.handle_step_result(result).await;
        let _ = forward.await;
        outcome.map(Some)
    }

    async fn grow_context(
        &self,
        result: &StepResult,
        tool_results: &[crate::wire::ToolResult],
    ) -> Result<(), anyhow::Error> {
        debug!(
            "Growing context with result: tool_calls={}, usage={}",
            result.tool_calls.len(),
            result.usage.is_some()
        );
        let llm = self.runtime.llm.as_ref().ok_or_else(|| LLMNotSet)?;
        let tool_messages: Vec<Message> = tool_results.iter().map(tool_result_to_message).collect();
        for message in &tool_messages {
            let missing = check_message(message, &llm.capabilities);
            if !missing.is_empty() {
                warn!(
                    "Tool result message requires unsupported capabilities: {:?}",
                    missing
                );
                return Err(anyhow::Error::new(LLMNotSupported::new(
                    llm.model_name(),
                    missing.into_iter().collect(),
                )));
            }
        }

        let mut context = self.context.lock().await;
        let assistant_message = sanitize_tool_call_arguments_for_history(&result.message);
        context.append_messages(assistant_message).await?;
        if let Some(usage) = &result.usage {
            context.update_token_count(usage.total()).await?;
        }
        debug!(
            "Appending tool messages to context: {}",
            tool_messages.len()
        );
        context.append_messages(tool_messages).await?;
        Ok(())
    }

    async fn compact_context(&self) -> Result<(), anyhow::Error> {
        wire_send(WireMessage::CompactionBegin(CompactionBegin {}));
        let mut attempts = 0usize;
        let compacted = loop {
            attempts += 1;
            let llm = self.runtime.llm.as_ref().ok_or_else(|| LLMNotSet)?;
            let history = { self.context.lock().await.history().to_vec() };
            match self.compaction.compact(&history, llm).await {
                Ok(compacted) => break compacted,
                Err(err) => {
                    let Some(class) = retry_class(&err) else {
                        return Err(anyhow::Error::new(err));
                    };
                    if attempts >= self.runtime.config.loop_control.max_retries_per_step as usize {
                        return Err(anyhow::Error::new(err));
                    }
                    let delay = retry_delay(class, attempts);
                    info!(
                        "Retrying compaction for the {} time. Waiting {} seconds.",
                        attempts,
                        delay.as_secs_f64()
                    );
                    tokio::time::sleep(delay).await;
                }
            }
        };
        if let Some(usage) = &compacted.token_usage {
            self.runtime
                .council_budget
                .charge_tokens(usage.total().max(0) as u64)
                .map_err(|reason| anyhow::Error::new(CouncilBudgetExceeded::new(reason)))?;
            if let Some(cost_usd) = usage.cost_usd {
                self.runtime
                    .council_budget
                    .charge_cost_usd(cost_usd)
                    .map_err(|reason| anyhow::Error::new(CouncilBudgetExceeded::new(reason)))?;
            }
        }
        let mut compacted_messages = compacted.messages;
        if let Some(tool_name) = self.agent.context_continuity_tool.as_deref() {
            let summary = compacted_messages
                .first()
                .map(|message| message.extract_text("\n"))
                .unwrap_or_default();
            let restored = self
                .run_context_continuity_hook(tool_name, &summary)
                .await?;
            let first = compacted_messages.first_mut().ok_or_else(|| {
                anyhow::anyhow!("Context continuity hook requires a compacted summary")
            })?;
            first.content.push(system(
                "Dhee restored the following durable continuity after native context compaction:",
            ));
            first
                .content
                .push(ContentPart::Text(TextPart::new(restored)));
        }
        {
            let mut context = self.context.lock().await;
            context.clear().await?;
            context
                .checkpoint(self.checkpoint_with_user_message)
                .await?;
            context.append_messages(compacted_messages).await?;
        }
        wire_send(WireMessage::CompactionEnd(CompactionEnd {}));
        Ok(())
    }

    async fn run_context_continuity_hook(
        &self,
        tool_name: &str,
        summary: &str,
    ) -> Result<String, anyhow::Error> {
        let mut call = ToolCall::new(
            format!("context-continuity-{}", uuid::Uuid::new_v4()),
            tool_name,
        );
        call.function.arguments = Some(
            serde_json::json!({
                "summary": summary,
                "session_id": self.runtime.session.id,
                "workspace_root": self.runtime.session.work_dir.to_string_lossy(),
            })
            .to_string(),
        );
        let pending = { self.agent.toolset.lock().await.handle(call) };
        let result = pending.resolve().await?;
        if result.return_value.is_error {
            return Err(anyhow::anyhow!(
                "Dhee context continuity hook failed: {}",
                result.return_value.message
            ));
        }
        let restored = match result.return_value.output {
            ToolOutput::Text(text) => text,
            ToolOutput::Parts(parts) => serde_json::to_string(&parts)?,
        };
        if restored.trim().is_empty() {
            return Err(anyhow::anyhow!(
                "Dhee context continuity hook returned no restoration packet"
            ));
        }
        Ok(restored)
    }
}

#[async_trait::async_trait(?Send)]
impl Soul for KimiSoul {
    fn name(&self) -> &str {
        &self.agent.name
    }

    fn model_name(&self) -> &str {
        self.runtime
            .llm
            .as_ref()
            .map(|llm| llm.model_name())
            .unwrap_or("")
    }

    fn model_capabilities(&self) -> Option<&std::collections::HashSet<ModelCapability>> {
        self.runtime.llm.as_ref().map(|llm| &llm.capabilities)
    }

    fn thinking(&self) -> Option<bool> {
        self.runtime
            .llm
            .as_ref()
            .and_then(|llm| llm.chat_provider.thinking_effort())
            .map(|effort| effort != kosong::chat_provider::ThinkingEffort::Off)
    }

    fn status(&self) -> StatusSnapshot {
        let context_usage = if let Some(llm) = &self.runtime.llm {
            match self.context.try_lock() {
                Ok(context) => context.token_count() as f64 / llm.max_context_size as f64,
                Err(_) => 0.0,
            }
        } else {
            0.0
        };
        StatusSnapshot {
            context_usage,
            yolo_enabled: self.runtime.approval.is_yolo(),
        }
    }

    fn available_slash_commands(&self) -> Vec<SlashCommandInfo> {
        self.slash_commands.clone()
    }

    async fn run(&self, user_input: UserInput) -> anyhow::Result<()> {
        let user_message = match user_input.clone() {
            UserInput::Text(text) => {
                Message::new(Role::User, vec![ContentPart::Text(TextPart::new(text))])
            }
            UserInput::Parts(parts) => Message::new(Role::User, parts),
        };
        let text_input = user_message.extract_text(" ").trim().to_string();

        wire_send(WireMessage::TurnBegin(TurnBegin { user_input }));

        if let Some(command_call) = parse_slash_command_call(&text_input) {
            self.handle_slash(&command_call.name, &command_call.args)
                .await?;
        } else if self.runtime.config.loop_control.max_ralph_iterations != 0 {
            let runner = FlowRunner::ralph_loop(
                user_message.clone(),
                self.runtime.config.loop_control.max_ralph_iterations,
            );
            runner.run(self, "").await?;
        } else {
            let _ = self.turn(user_message).await?;
        }
        wire_send(WireMessage::TurnEnd(TurnEnd::default()));
        Ok(())
    }
}

pub struct FlowRunner {
    flow: Flow,
    name: Option<String>,
    max_moves: i64,
}

#[derive(Clone)]
struct FlowPrompt {
    user_input: UserInput,
    text: String,
}

impl FlowRunner {
    pub fn new(flow: Flow, name: Option<String>, max_moves: i64) -> Self {
        Self {
            flow,
            name,
            max_moves,
        }
    }

    pub fn ralph_loop(user_message: Message, max_ralph_iterations: i64) -> FlowRunner {
        let prompt_content = user_message.content.clone();
        let prompt_text = Message::new(Role::User, prompt_content.clone())
            .extract_text(" ")
            .trim()
            .to_string();
        let total_runs = if max_ralph_iterations < 0 {
            1_000_000_000_000_000i64
        } else {
            max_ralph_iterations + 1
        };

        let mut nodes: HashMap<String, FlowNode> = HashMap::new();
        let mut outgoing: HashMap<String, Vec<FlowEdge>> = HashMap::new();

        nodes.insert(
            "BEGIN".to_string(),
            FlowNode::new("BEGIN", "BEGIN", FlowNodeKind::Begin),
        );
        nodes.insert(
            "END".to_string(),
            FlowNode::new("END", "END", FlowNodeKind::End),
        );
        nodes.insert(
            "R1".to_string(),
            FlowNode::new("R1", prompt_content.clone(), FlowNodeKind::Task),
        );
        nodes.insert(
            "R2".to_string(),
            FlowNode::new(
                "R2",
                format!(
                    "{}. (You are running in an automated loop where the same prompt is fed repeatedly. Only choose STOP when the task is fully complete. Including it will stop further iterations. If you are not 100% sure, choose CONTINUE.)",
                    prompt_text
                ),
                FlowNodeKind::Decision,
            ),
        );

        outgoing.insert(
            "BEGIN".to_string(),
            vec![FlowEdge::new("BEGIN", "R1", None)],
        );
        outgoing.insert("R1".to_string(), vec![FlowEdge::new("R1", "R2", None)]);
        outgoing.insert(
            "R2".to_string(),
            vec![
                FlowEdge::new("R2", "R2", Some("CONTINUE".to_string())),
                FlowEdge::new("R2", "END", Some("STOP".to_string())),
            ],
        );
        outgoing.insert("END".to_string(), Vec::new());

        let flow = Flow::new(nodes, outgoing, "BEGIN", "END");
        FlowRunner::new(flow, None, total_runs)
    }

    pub async fn run(&self, soul: &KimiSoul, args: &str) -> anyhow::Result<()> {
        if !args.trim().is_empty() {
            let name = self.name.as_deref().unwrap_or("anonymous");
            debug!("Agent flow {name} received owner arguments");
        }

        let mut current_id = self.flow.begin_id.clone();
        let mut moves = 0i64;
        let mut total_steps = 0i64;
        let mut args_injected = false;

        loop {
            let node = self
                .flow
                .nodes
                .get(&current_id)
                .expect("flow node not found");
            let edges = self
                .flow
                .outgoing
                .get(&current_id)
                .cloned()
                .unwrap_or_default();

            if node.kind == FlowNodeKind::End {
                let gate = soul.completion_gate_snapshot().await;
                if !gate.satisfied {
                    return Err(anyhow::Error::new(FlowIncomplete::new(
                        current_id,
                        FLOW_REASON_COMPLETION_GATE_MISSING,
                        gate.missing,
                    )));
                }
                info!("Agent flow reached END node {}", current_id);
                return Ok(());
            }
            if node.kind == FlowNodeKind::Begin {
                if edges.is_empty() {
                    error!(
                        "Agent flow BEGIN node \"{}\" has no outgoing edges; stopping.",
                        node.id
                    );
                    return Err(anyhow::Error::new(FlowIncomplete::new(
                        node.id.clone(),
                        FLOW_REASON_BEGIN_WITHOUT_EDGE,
                        soul.completion_gate_snapshot().await.missing,
                    )));
                }
                current_id = edges[0].dst.clone();
                continue;
            }

            if moves >= self.max_moves {
                return Err(anyhow::Error::new(MaxStepsReached::new(total_steps)));
            }

            let flow_args = if !args_injected && !args.trim().is_empty() {
                args_injected = true;
                Some(args.trim())
            } else {
                None
            };
            let (next_id, steps_used) = self
                .execute_flow_node(soul, node, &edges, flow_args)
                .await?;
            total_steps += steps_used;
            if let Some(next_id) = next_id {
                moves += 1;
                current_id = next_id;
                continue;
            }
            return Err(anyhow::Error::new(FlowIncomplete::new(
                current_id,
                FLOW_REASON_NO_NEXT_NODE,
                soul.completion_gate_snapshot().await.missing,
            )));
        }
    }

    async fn execute_flow_node(
        &self,
        soul: &KimiSoul,
        node: &FlowNode,
        edges: &[FlowEdge],
        flow_args: Option<&str>,
    ) -> anyhow::Result<(Option<String>, i64)> {
        if edges.is_empty() {
            error!(
                "Agent flow node \"{}\" has no outgoing edges; stopping.",
                node.id
            );
            return Err(anyhow::Error::new(FlowIncomplete::new(
                node.id.clone(),
                FLOW_REASON_NODE_WITHOUT_EDGE,
                soul.completion_gate_snapshot().await.missing,
            )));
        }

        let base_prompt = self.build_flow_prompt(node, edges, flow_args);
        let mut prompt = base_prompt.user_input.clone();
        let mut steps_used = 0;
        loop {
            let outcome = self.flow_turn(soul, prompt.clone()).await?;
            steps_used += outcome.step_count;
            if outcome.stop_reason == "tool_rejected" {
                error!("Agent flow stopped after tool rejection.");
                return Err(anyhow::Error::new(FlowIncomplete::new(
                    node.id.clone(),
                    FLOW_REASON_TOOL_REJECTED,
                    soul.completion_gate_snapshot().await.missing,
                )));
            }
            if node.kind != FlowNodeKind::Decision {
                return Ok((Some(edges[0].dst.clone()), steps_used));
            }
            let choice = outcome
                .final_message
                .as_ref()
                .and_then(|msg| parse_choice(&msg.extract_text(" ")));
            if let Some(choice_value) = choice.as_ref() {
                if let Some(next_id) = edges
                    .iter()
                    .find(|edge| edge.label.as_deref() == Some(choice_value.as_str()))
                    .map(|edge| edge.dst.clone())
                {
                    return Ok((Some(next_id), steps_used));
                }
            }
            let options = edges
                .iter()
                .filter_map(|edge| edge.label.as_deref())
                .collect::<Vec<_>>()
                .join(", ");
            warn!(
                "Agent flow invalid choice. Got: {}. Available: {}.",
                choice.clone().unwrap_or_else(|| "<missing>".to_string()),
                options
            );
            prompt = UserInput::Text(format!(
                "{}\n\nYour last response did not include a valid choice. Reply with one of the choices using <choice>...</choice>.",
                base_prompt.text
            ));
        }
    }

    fn build_flow_prompt(
        &self,
        node: &FlowNode,
        edges: &[FlowEdge],
        flow_args: Option<&str>,
    ) -> FlowPrompt {
        if node.kind != FlowNodeKind::Decision {
            return match &node.label {
                FlowLabel::Parts(parts) => FlowPrompt {
                    user_input: UserInput::Parts(with_flow_args(parts.clone(), flow_args)),
                    text: append_flow_args(node.label_as_string(), flow_args),
                },
                FlowLabel::Text(text) => {
                    let text = append_flow_args(text.clone(), flow_args);
                    FlowPrompt {
                        user_input: UserInput::Text(text.clone()),
                        text,
                    }
                }
            };
        }
        let label_text = append_flow_args(node.label_as_string(), flow_args);
        let choices: Vec<String> = edges.iter().filter_map(|edge| edge.label.clone()).collect();
        let mut lines = Vec::new();
        lines.push(label_text);
        lines.push(String::new());
        lines.push("Available branches:".to_string());
        for choice in choices {
            lines.push(format!("- {choice}"));
        }
        lines.push(String::new());
        lines.push("Reply with a choice using <choice>...</choice>.".to_string());
        let text = lines.join("\n");
        FlowPrompt {
            user_input: UserInput::Text(text.clone()),
            text,
        }
    }

    async fn flow_turn(&self, soul: &KimiSoul, prompt: UserInput) -> anyhow::Result<TurnOutcome> {
        wire_send(WireMessage::TurnBegin(TurnBegin {
            user_input: prompt.clone(),
        }));
        let message = match prompt {
            UserInput::Text(text) => {
                Message::new(Role::User, vec![ContentPart::Text(TextPart::new(text))])
            }
            UserInput::Parts(parts) => Message::new(Role::User, parts),
        };
        let outcome = soul.turn(message).await?;
        wire_send(WireMessage::TurnEnd(TurnEnd::default()));
        Ok(outcome)
    }
}

fn append_flow_args(text: String, flow_args: Option<&str>) -> String {
    match flow_args {
        Some(args) => format!("{text}\n\nUser request:\n{args}"),
        None => text,
    }
}

fn with_flow_args(mut parts: Vec<ContentPart>, flow_args: Option<&str>) -> Vec<ContentPart> {
    if let Some(args) = flow_args {
        parts.push(ContentPart::Text(TextPart::new(format!(
            "User request:\n{args}"
        ))));
    }
    parts
}

/// The step's approval piping task, aborted when the step ends by any path.
/// A cancelled turn drops the step mid-flight; a task left running would keep
/// taking requests from the shared approval queue into that turn's dead wire,
/// so a later turn's tool call waits forever on an approval nobody sees.
struct ApprovalTask(tokio::task::JoinHandle<()>);

impl ApprovalTask {
    async fn stop(mut self) {
        self.0.abort();
        if let Err(err) = (&mut self.0).await {
            if !err.is_cancelled() {
                error!("Approval piping task failed: {:?}", err);
            }
        }
    }
}

impl Drop for ApprovalTask {
    fn drop(&mut self) {
        self.0.abort();
    }
}

fn spawn_approval_task(approval: Arc<Approval>) -> ApprovalTask {
    ApprovalTask(crate::soul::spawn_with_current_wire(async move {
        loop {
            let request = match approval.fetch_request().await {
                Ok(req) => req,
                Err(_) => return,
            };
            let wire_request = ApprovalRequest::new(
                request.id.clone(),
                request.tool_call_id.clone(),
                request.sender.clone(),
                request.action.clone(),
                request.description.clone(),
                request.display.clone(),
            );
            wire_send(WireMessage::ApprovalRequest(wire_request.clone()));
            let resp = wire_request.wait().await;
            let _ = approval.resolve_request(&request.id, resp.clone());
            wire_send(WireMessage::ApprovalResponse(ApprovalResponse {
                request_id: request.id,
                response: resp,
            }));
        }
    }))
}

fn forward_streamed_message_part(part: StreamedMessagePart) {
    match part {
        StreamedMessagePart::Content(content) => {
            wire_send(WireMessage::ContentPart(content));
        }
        StreamedMessagePart::ToolCall(call) => {
            wire_send(WireMessage::ToolCall(call));
        }
        StreamedMessagePart::ToolCallPart(part) => {
            wire_send(WireMessage::ToolCallPart(part));
        }
    }
}

fn streamed_part_is_owner_visible(part: &StreamedMessagePart) -> bool {
    match part {
        StreamedMessagePart::Content(ContentPart::Text(text)) => !text.text.trim().is_empty(),
        StreamedMessagePart::Content(
            ContentPart::ImageUrl(_) | ContentPart::AudioUrl(_) | ContentPart::VideoUrl(_),
        ) => true,
        StreamedMessagePart::Content(ContentPart::Think(_))
        | StreamedMessagePart::ToolCall(_)
        | StreamedMessagePart::ToolCallPart(_) => false,
    }
}

/// (root) Why a failed model call may be tried again.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum RetryClass {
    /// The provider failed (connection, timeout, overload, 5xx): it usually
    /// recovers, so wait it out with backoff up to `max_retries_per_step`.
    Provider,
    /// The model's reply was unusable (empty, malformed tool call): retry
    /// quickly with a hint, but only a few times — more burns tokens.
    Model,
}

/// Attempts for a `RetryClass::Model` failure (the first try included).
const MODEL_RETRY_ATTEMPTS: usize = 3;

fn retry_class(err: &ChatProviderError) -> Option<RetryClass> {
    match err.kind {
        ChatProviderErrorKind::Connection | ChatProviderErrorKind::Timeout => {
            Some(RetryClass::Provider)
        }
        ChatProviderErrorKind::Status(code) => {
            matches!(code, 408 | 429 | 500 | 502 | 503 | 504 | 529).then_some(RetryClass::Provider)
        }
        ChatProviderErrorKind::EmptyResponse | ChatProviderErrorKind::MalformedToolCall => {
            Some(RetryClass::Model)
        }
        ChatProviderErrorKind::Other => None,
    }
}

fn retry_delay(class: RetryClass, attempt: usize) -> Duration {
    let (base, cap) = match class {
        RetryClass::Provider => (2.0, 30.0),
        RetryClass::Model => (0.3, 5.0),
    };
    let delay = (base * 2f64.powi((attempt as i32).saturating_sub(1))).min(cap);
    let jitter: f64 = rand::rng().random_range(0.0..0.25);
    Duration::from_secs_f64(delay * (1.0 + jitter))
}

fn retry_hint_for_error(err: &ChatProviderError) -> Option<Message> {
    let hint = match err.kind {
        ChatProviderErrorKind::MalformedToolCall => {
            "Your previous generation emitted invalid JSON for a tool call before the tool could run. Retry the same step with a complete JSON object for every tool call. Keep Shell arguments compact and focused; if the code change is already complete, stop calling tools and provide the final answer."
        }
        ChatProviderErrorKind::EmptyResponse => {
            "Your previous generation ended after private reasoning without a visible answer or a complete tool call. Retry the same step and emit exactly one actionable next result: either a complete tool call with valid JSON arguments, or the final visible answer. Do not end with private reasoning only."
        }
        _ => return None,
    };
    Some(Message::new(Role::User, vec![system(hint)]))
}


#[cfg(test)]
mod tests {
    use super::*;
    use crate::wire::Wire;

    #[tokio::test]
    async fn a_cancelled_step_leaves_no_task_taking_the_next_turns_approvals() {
        let approval = Arc::new(Approval::new(false));

        // A turn cancelled mid-step drops the step, approval task handle and all.
        let dead = Arc::new(Wire::new(None));
        let task = crate::soul::with_current_wire(Arc::clone(&dead), async {
            spawn_approval_task(Arc::clone(&approval))
        })
        .await;
        tokio::task::yield_now().await;
        drop(task);
        dead.shutdown();

        // The next turn's approval request reaches that turn's own wire.
        let live = Arc::new(Wire::new(None));
        let ui = live.ui_side(false);
        let _task = crate::soul::with_current_wire(Arc::clone(&live), async {
            spawn_approval_task(Arc::clone(&approval))
        })
        .await;
        let requester = Arc::clone(&approval);
        tokio::spawn(crate::soul::toolset::with_current_tool_call(
            kosong::message::ToolCall::new("call_1", "Shell"),
            async move { requester.request("Shell", "run command", "git push", None).await },
        ));

        let seen = tokio::time::timeout(Duration::from_secs(2), async {
            loop {
                if let WireMessage::ApprovalRequest(request) = ui.receive().await.unwrap() {
                    break request;
                }
            }
        })
        .await
        .expect("the approval request reached the live turn");
        assert_eq!(seen.tool_call_id, "call_1");
    }


    #[test]
    fn malformed_tool_call_retry_hint_is_actionable_and_prompt_safe() {
        let err = ChatProviderError::new(ChatProviderErrorKind::MalformedToolCall, "bad tool json");

        let hint = retry_hint_for_error(&err).expect("malformed tool call gets a hint");
        assert_eq!(hint.role, Role::User);
        let text = hint.extract_text(" ");
        assert!(text.contains("invalid JSON"));
        assert!(text.contains("complete JSON object"));
        assert!(text.contains("Keep Shell arguments compact"));
        assert!(!text.contains("bad tool json"));
    }

    #[test]
    fn empty_response_retry_hint_requires_visible_progress() {
        let err = ChatProviderError::new(
            ChatProviderErrorKind::EmptyResponse,
            "reasoning only",
        );

        let hint = retry_hint_for_error(&err).expect("empty response gets a hint");
        assert_eq!(hint.role, Role::User);
        let text = hint.extract_text(" ");
        assert!(text.contains("complete tool call"));
        assert!(text.contains("final visible answer"));
        assert!(text.contains("Do not end with private reasoning only"));
        assert!(!text.contains("Your previous generation returned reasoning only"));
    }

    #[test]
    fn provider_failures_wait_out_an_outage_and_model_failures_stay_quick() {
        let status = |code| ChatProviderError::new(ChatProviderErrorKind::Status(code), "x");
        for code in [408, 429, 500, 502, 503, 504, 529] {
            assert_eq!(retry_class(&status(code)), Some(RetryClass::Provider), "{code}");
        }
        for code in [400, 401, 402, 403, 404] {
            assert_eq!(retry_class(&status(code)), None, "{code}");
        }
        let empty = ChatProviderError::new(ChatProviderErrorKind::EmptyResponse, "x");
        assert_eq!(retry_class(&empty), Some(RetryClass::Model));

        let secs = |class, attempt| retry_delay(class, attempt).as_secs_f64();
        assert!((2.0..2.5).contains(&secs(RetryClass::Provider, 1)));
        assert!((16.0..20.0).contains(&secs(RetryClass::Provider, 4)));
        assert!((30.0..37.5).contains(&secs(RetryClass::Provider, 9)));
        assert!(secs(RetryClass::Model, 2) < 1.0);
    }

    #[test]
    fn transient_retry_errors_do_not_change_model_context() {
        let err = ChatProviderError::new(ChatProviderErrorKind::Timeout, "timeout");
        assert!(retry_hint_for_error(&err).is_none());
    }

    #[test]
    fn private_reasoning_does_not_block_a_safe_step_retry() {
        assert!(!streamed_part_is_owner_visible(
            &StreamedMessagePart::Content(ContentPart::Think(
                kosong::message::ThinkPart::new("I should call study_observe next."),
            )),
        ));
        assert!(streamed_part_is_owner_visible(
            &StreamedMessagePart::Content(ContentPart::Text(TextPart::new(
                "I can explain this diagram.",
            ))),
        ));
        assert!(!streamed_part_is_owner_visible(
            &StreamedMessagePart::ToolCallPart(kosong::message::ToolCallPart {
                arguments_part: Some("{\"scope\":\"visible_page\"}".to_string()),
            }),
        ));
    }
}
