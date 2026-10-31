//! The director: the harness, not the model, decides what each step does.
//!
//! A step in this loop is a model call that may end in tool calls. Left to the
//! model, that one call carries two decisions — which tool, and whether any —
//! and a small reasoning model is unreliable at both. Measured on OpenRouter on
//! 2026-09-23 with three tutor tools and a plain question ("What is Newton's
//! first law?"): `deepseek-v4-flash` on `tool_choice: "auto"` called lookup
//! tools on 5 of 5 tries; and on 2026-09-22 `gpt-oss-120b` answered "draw a
//! free body diagram" by describing arrows. Neither is a failure to write; both
//! are failures to steer.
//!
//! So before every step the director asks Jev one closed question — *what
//! should happen next: reply, or which tool?* — over the real catalogue, and
//! gets a probability for every option. Then:
//!
//! * **Reply** — the step runs with `tool_choice: "none"`. Every provider we
//!   measured honours `none`, so the model can only write.
//! * **Call a tool** — the harness makes the call itself. The model is asked for
//!   the tool's ARGUMENTS only, as structured output, and the harness builds the
//!   tool call and dispatches it. This is deliberate: OpenRouter's gpt-oss-120b
//!   providers advertise `tool_choice` and ignored `"required"` and a named
//!   function on 20 of 20 tries, while the same models wrote correct arguments
//!   under `response_format` 10 of 10. A forced call that depends on the
//!   provider is not a harness property; one the harness builds is.
//! * **Act, tool unclear** — `tool_choice: "required"`. Honoured by some
//!   providers and ignored by others, and harmless when ignored.
//! * **Unsure** — the step runs exactly as it did before the director existed.
//!
//! The tool block is never narrowed. It stays byte-identical on every step so
//! the provider's prefix cache survives; only per-call fields change.
//!
//! **What this does not do:** it never writes. The words and the arguments are
//! the model's — Jev cannot write a lesson or a patch, and the measured full
//! `open_block` turn showed no win from trying. Route with it; generate with the
//! model.
//!
//! **Scale.** A Choice takes up to 255 options. A catalogue up to
//! [`DirectorConfig::detail_limit`] is described in full in one call; a larger
//! one (a coding agent with MCP servers) is skimmed with one line per tool and,
//! when the skim is split, the top few are re-read in detail — the two-request
//! shape TypeSafe measured on a 182-skill roster. Past 254 tools the director
//! stands aside rather than guess.
//!
//! **Context.** The state is rendered mechanically from the turn: who the
//! assistant is, the request, the steps taken and what they returned. A
//! summariser here would be a model call on the critical path. The `context`
//! slot is where a compiled packet from Dhee belongs — built once per turn, not
//! per step, because Dhee's writes take tens of seconds and a step's decision
//! has about one.

use std::collections::HashSet;
use std::io::Write;
use std::time::Duration;

use kosong::message::{ContentPart, Message, Role};
use kosong::tooling::Tool;
use serde_json::{Map, Value, json};
use tracing::{debug, info};

use crate::soul::decision::JevClient;

/// `on` (default), `shadow` (decide and record, change nothing) or `off`.
pub const DIRECTOR_ENV: &str = "CHOTU_DIRECTOR";
pub const CALL_FLOOR_ENV: &str = "CHOTU_DIRECTOR_CALL_FLOOR";
pub const RESPOND_FLOOR_ENV: &str = "CHOTU_DIRECTOR_RESPOND_FLOOR";
pub const MAX_CALLS_ENV: &str = "CHOTU_DIRECTOR_MAX_CALLS";

/// The option that means "no tool: write the answer".
pub const REPLY: &str = "reply_to_user";

const QUESTION: &str = "next";
/// A Choice holds 255 options; one is the reply.
const MAX_TOOLS: usize = 254;

const ASSISTANT_CHARS: usize = 500;
const REQUEST_HEAD_CHARS: usize = 3_000;
const REQUEST_TAIL_CHARS: usize = 1_200;
const STEP_ARGS_CHARS: usize = 200;
const STEP_RESULT_CHARS: usize = 400;
const RECENT_STEPS: usize = 12;
const REPLY_CHARS: usize = 800;
const SUMMARY_CHARS: usize = 160;
const DETAIL_CHARS: usize = 600;
const SCHEMA_HINT_CHARS: usize = 4_000;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DirectorMode {
    On,
    Shadow,
}

#[derive(Debug, Clone)]
pub struct DirectorConfig {
    pub mode: DirectorMode,
    /// How sure Jev must be of one tool before the harness calls it.
    pub call_floor: f64,
    /// How sure Jev must be that no tool is needed before tools are withheld.
    pub respond_floor: f64,
    /// Directed calls per turn. Past it, the model steers alone.
    pub max_directed_calls: usize,
    /// Catalogues up to this size are described in full in the first read.
    pub detail_limit: usize,
    /// How many candidates a split skim carries into the detailed re-read.
    pub shortlist: usize,
}

impl DirectorConfig {
    pub fn from_env() -> Option<Self> {
        let mode = match std::env::var(DIRECTOR_ENV)
            .unwrap_or_default()
            .trim()
            .to_ascii_lowercase()
            .as_str()
        {
            "0" | "false" | "no" | "off" => return None,
            "shadow" => DirectorMode::Shadow,
            _ => DirectorMode::On,
        };
        Some(Self {
            mode,
            call_floor: env_f64(CALL_FLOOR_ENV, 0.80),
            respond_floor: env_f64(RESPOND_FLOOR_ENV, 0.80),
            max_directed_calls: env_usize(MAX_CALLS_ENV, 8),
            detail_limit: 32,
            shortlist: 3,
        })
    }
}

impl Default for DirectorConfig {
    fn default() -> Self {
        Self {
            mode: DirectorMode::On,
            call_floor: 0.80,
            respond_floor: 0.80,
            max_directed_calls: 8,
            detail_limit: 32,
            shortlist: 3,
        }
    }
}

/// What the harness will do with the next step.
#[derive(Debug, Clone, PartialEq)]
pub enum Directive {
    /// Write only: the step runs with `tool_choice: "none"`.
    Reply { p: f64 },
    /// The harness calls this tool; the model writes only its arguments.
    Call { tool: String, p: f64 },
    /// A tool is needed and which one is unclear: `tool_choice: "required"`.
    Act { p: f64 },
    /// No confident read: the step runs as it did before the director.
    Free { why: &'static str },
}

impl Directive {
    pub fn label(&self) -> String {
        match self {
            Directive::Reply { p } => format!("reply (p={p:.2})"),
            Directive::Call { tool, p } => format!("call {tool} (p={p:.2})"),
            Directive::Act { p } => format!("act, tool unclear (p={p:.2})"),
            Directive::Free { why } => format!("free ({why})"),
        }
    }

    /// The per-call request fields that carry this directive to the provider.
    pub fn overrides(&self) -> Option<Map<String, Value>> {
        let choice = match self {
            Directive::Reply { .. } => "none",
            Directive::Act { .. } => "required",
            Directive::Call { .. } | Directive::Free { .. } => return None,
        };
        let mut map = Map::new();
        map.insert("tool_choice".to_string(), Value::String(choice.to_string()));
        Some(map)
    }
}

/// When in the turn a decision is asked for. The question differs.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Phase {
    /// Before a step: what should this step do?
    BeforeStep,
    /// A step just ended in words, which would end the turn: is a tool action
    /// still owed AFTER those words?
    TurnEnd,
}

impl Phase {
    pub fn as_str(self) -> &'static str {
        match self {
            Phase::BeforeStep => "before_step",
            Phase::TurnEnd => "turn_end",
        }
    }
}

/// One decision, and the record of how it was reached.
#[derive(Debug, Clone)]
pub struct Decision {
    pub directive: Directive,
    pub record: Value,
}

pub struct Director {
    jev: JevClient,
    config: DirectorConfig,
}

impl Director {
    pub fn new(jev: JevClient, config: DirectorConfig) -> Self {
        Self { jev, config }
    }

    pub fn config(&self) -> &DirectorConfig {
        &self.config
    }

    /// Decide the next step, or `None` when there is nothing to decide.
    ///
    /// `None` and [`Directive::Free`] both leave the step as it was; `None` is
    /// for the cases that are not worth a record (no tools, no request).
    pub async fn decide(
        &self,
        system_prompt: &str,
        history: &[Message],
        tools: &[Tool],
        context: Option<&Value>,
        phase: Phase,
    ) -> Option<Decision> {
        if tools.is_empty() {
            return None;
        }
        let state = render_state(system_prompt, history, context)?;
        let reply_key = reply_key(tools);

        if tools.len() > MAX_TOOLS {
            return Some(Decision {
                directive: Directive::Free {
                    why: "catalogue larger than one choice holds",
                },
                record: json!({"tools": tools.len()}),
            });
        }

        let detailed = tools.len() <= self.config.detail_limit;
        let first = self
            .jev
            .decide(&state, &question(tools, &reply_key, detailed, phase))
            .await;
        let Some(first) = first else {
            return Some(Decision {
                directive: Directive::Free {
                    why: "decision model unavailable",
                },
                record: json!({"state": state}),
            });
        };
        let ranked = first.probabilities(QUESTION).unwrap_or_default();
        let mut directive = policy(&ranked, &reply_key, &self.config);
        let mut record = json!({
            "state": state,
            "tools": tools.len(),
            "detailed": detailed,
            "ranked": top(&ranked, 5),
            "ms": first.elapsed.as_millis() as u64,
        });

        // A skim that split between a few tools is re-read with their full
        // descriptions. A catalogue small enough to have been read in detail
        // the first time has nothing more to show, so it is not asked twice.
        if !detailed && matches!(directive, Directive::Act { .. }) {
            let shortlist: HashSet<&str> = ranked
                .iter()
                .filter(|(name, _)| name != &reply_key)
                .take(self.config.shortlist)
                .map(|(name, _)| name.as_str())
                .collect();
            let narrowed: Vec<Tool> = tools
                .iter()
                .filter(|tool| shortlist.contains(tool.name.as_str()))
                .cloned()
                .collect();
            if let Some(second) = self
                .jev
                .decide(&state, &question(&narrowed, &reply_key, true, phase))
                .await
            {
                let reread = second.probabilities(QUESTION).unwrap_or_default();
                let refined = policy(&reread, &reply_key, &self.config);
                // The re-read may name the tool; it may not overturn the skim's
                // judgement that a tool is needed on a narrower view.
                if matches!(refined, Directive::Call { .. }) {
                    directive = refined;
                }
                record["reread"] = json!({
                    "ranked": top(&reread, 5),
                    "ms": second.elapsed.as_millis() as u64,
                });
            }
        }

        record["directive"] = Value::String(directive.label());
        Some(Decision { directive, record })
    }
}

/// The directive a distribution supports, under these floors.
///
/// Pure, so the thresholds can be argued about in tests rather than in logs.
pub fn policy(ranked: &[(String, f64)], reply_key: &str, config: &DirectorConfig) -> Directive {
    let Some((best, p)) = ranked.first() else {
        return Directive::Free {
            why: "no distribution",
        };
    };
    if best == reply_key {
        if *p >= config.respond_floor {
            return Directive::Reply { p: *p };
        }
        return Directive::Free {
            why: "reply not certain enough",
        };
    }
    if *p >= config.call_floor {
        return Directive::Call {
            tool: best.clone(),
            p: *p,
        };
    }
    let reply = ranked
        .iter()
        .find(|(name, _)| name == reply_key)
        .map(|(_, p)| *p)
        .unwrap_or(0.0);
    let act = 1.0 - reply;
    if act >= config.call_floor {
        return Directive::Act { p: act };
    }
    Directive::Free {
        why: "no option clear enough",
    }
}

fn question(tools: &[Tool], reply_key: &str, detailed: bool, phase: Phase) -> Value {
    let mut criteria = Map::new();
    let (reply, instructions) = match phase {
        Phase::BeforeStep => (
            json!({
                "what": "Write the reply to the user now, with no further tool call.",
                "choose_when": [
                    "the steps already taken this turn have what the reply needs",
                    "the user is talking, greeting, or asking something the assistant can answer from what it knows",
                    "the request says the reply itself is the work, or says to stop after the steps already taken",
                    "the assistant has to ask the user something only the user can answer"
                ],
                "not_when": "the request asks for an action or lookup that no step this turn has done yet and that should come before the reply, or the turn changed files and context.turn_facts says edits_not_yet_exercised"
            }),
            json!({
                "question": "What should the assistant do next?",
                "rules": [
                    "Judge the NEXT single action from the request and the steps already taken this turn.",
                    "The request is the authority. When it names the next tool, says NOT to use a kind of tool (for example 'do not draw'), or says to stop, that wins over any tool's own description of when it is useful.",
                    "A step already done successfully does not need doing again unless the request asks for more of it.",
                    "A file listed in context.turn_facts.files_read_this_turn has been read: do not choose to read it again unless it was edited since or a part not yet seen is needed.",
                    "When context.turn_facts says edits_not_yet_exercised, the next action runs something that exercises the change (the project's tests, a script that reproduces the problem, the build) — not more reading and not the reply.",
                    "A step the request marks optional ('if it would help') is chosen only when it clearly would."
                ]
            }),
        ),
        Phase::TurnEnd => (
            json!({
                "what": "Nothing more: the reply the assistant just wrote completes the turn.",
                "choose_when": [
                    "everything the request asks for is done",
                    "a lookup or read the request mentioned was skipped — it existed to inform the reply, and the reply is already written, so it is too late to matter"
                ],
                "not_when": "the request requires a tool action AFTER the reply (recording a verdict or grade, saving, sending, drawing that was asked for), or the reply only describes or promises work the request asked the assistant to actually do, or the turn changed files and context.turn_facts says edits_not_yet_exercised"
            }),
            json!({
                "question": "The assistant has just written its reply to the user, which would end the turn. Does the request still require a tool call after that reply, and if so which one?",
                "rules": [
                    "The request is the authority. When it says a tool is called LAST or after the reply, that call is still owed until a step shows it done.",
                    "Tools that only gather information for the reply are not owed once the reply is written.",
                    "Changed code is not done until something has run it: when context.turn_facts says edits_not_yet_exercised, a command that exercises the change is still owed.",
                    "Never choose a tool the request says not to use."
                ]
            }),
        ),
    };
    criteria.insert(reply_key.to_string(), reply);
    for tool in tools {
        let text = if detailed {
            clipped(&tool.description, DETAIL_CHARS)
        } else {
            first_sentence(&tool.description, SUMMARY_CHARS)
        };
        let mut option = Map::new();
        option.insert("what".to_string(), Value::String(text));
        if detailed {
            if let Some(needs) = required_arguments(&tool.parameters) {
                option.insert("takes".to_string(), needs);
            }
        }
        criteria.insert(tool.name.clone(), Value::Object(option));
    }
    json!({
        QUESTION: {
            "type": "choice",
            "instructions": instructions,
            "criteria": criteria,
        }
    })
}

fn reply_key(tools: &[Tool]) -> String {
    let mut key = REPLY.to_string();
    while tools.iter().any(|tool| tool.name == key) {
        key.push('_');
    }
    key
}

fn required_arguments(parameters: &Value) -> Option<Value> {
    let required = parameters.get("required")?.as_array()?;
    if required.is_empty() {
        return None;
    }
    Some(Value::Array(required.iter().take(8).cloned().collect()))
}

fn top(ranked: &[(String, f64)], n: usize) -> Value {
    Value::Array(
        ranked
            .iter()
            .take(n)
            .map(|(name, p)| json!([name, (p * 1000.0).round() / 1000.0]))
            .collect(),
    )
}

/// The state Jev reads: who is asking, what was asked, what has been done.
///
/// Mechanical on purpose — see the module docs. `None` when there is no
/// request to judge against.
pub fn render_state(
    system_prompt: &str,
    history: &[Message],
    context: Option<&Value>,
) -> Option<Value> {
    let start = history
        .iter()
        .rposition(|message| message.role == Role::User && !is_injected(message))?;
    let (preamble, request) = split_envelope(&message_text(&history[start]));
    if request.trim().is_empty() {
        return None;
    }
    let assistant = if preamble.trim().is_empty() {
        clipped(system_prompt, ASSISTANT_CHARS)
    } else {
        clipped(&preamble, ASSISTANT_CHARS)
    };

    let turn = &history[start + 1..];
    let mut steps = Vec::new();
    for message in turn {
        for call in message.tool_calls.iter().flatten() {
            let result = turn
                .iter()
                .find(|m| m.role == Role::Tool && m.tool_call_id.as_deref() == Some(&call.id))
                .map(message_text);
            let ok = result
                .as_deref()
                .map(|text| !text.contains("<system>ERROR"))
                .unwrap_or(false);
            steps.push(json!({
                "tool": call.function.name,
                "arguments": clipped(call.function.arguments.as_deref().unwrap_or(""), STEP_ARGS_CHARS),
                "result": match &result {
                    Some(text) => clipped(text, STEP_RESULT_CHARS),
                    None => "(no result yet)".to_string(),
                },
                "ok": ok,
            }));
        }
    }
    let overflow = steps.len().saturating_sub(RECENT_STEPS);
    let steps: Vec<Value> = steps.into_iter().skip(overflow).collect();

    let written = turn
        .iter()
        .rev()
        .find(|message| message.role == Role::Assistant)
        .map(message_text)
        .unwrap_or_default();

    let mut state = Map::new();
    state.insert("assistant".to_string(), Value::String(assistant));
    if let Some(context) = context {
        state.insert("context".to_string(), context.clone());
    }
    state.insert(
        "request".to_string(),
        Value::String(head_and_tail(&request, REQUEST_HEAD_CHARS, REQUEST_TAIL_CHARS)),
    );
    state.insert(
        "steps_this_turn".to_string(),
        if steps.is_empty() {
            Value::String("none yet".to_string())
        } else {
            Value::Array(steps)
        },
    );
    if !written.trim().is_empty() {
        state.insert(
            "assistant_wrote_last".to_string(),
            Value::String(clipped(&written, REPLY_CHARS)),
        );
    }
    Some(Value::Object(state))
}

/// Chotu sends one user message shaped `## System\n…\n\n## User\n…`: the
/// product's identity and policy, then the actual turn. Read as one block, the
/// "request" would be the identity paragraph and the decision would judge a
/// turn against a job description.
fn split_envelope(text: &str) -> (String, String) {
    let trimmed = text.trim_start();
    if trimmed.starts_with("## System") {
        if let Some(at) = trimmed.find("\n## User\n") {
            let preamble = trimmed["## System".len()..at].trim().to_string();
            let request = trimmed[at + "\n## User\n".len()..].to_string();
            return (preamble, request);
        }
    }
    (String::new(), text.to_string())
}

// ----- argument synthesis -------------------------------------------------

/// The message that asks the model for one tool's arguments, and nothing else.
///
/// Not persisted: the context records only the tool call the harness built from
/// the answer, exactly as if the model had made it.
pub fn argument_request(tool: &Tool) -> Message {
    let schema = clipped(
        &serde_json::to_string(&tool.parameters).unwrap_or_default(),
        SCHEMA_HINT_CHARS,
    );
    Message::new(
        Role::User,
        vec![crate::soul::message::system(&format!(
            "The next action is decided: call the tool `{}`. Reply with ONLY the JSON object \
             of its arguments — no prose, no code fence — matching this JSON Schema:\n{}",
            tool.name, schema
        ))],
    )
}

/// Request fields for the argument call: structured output, no native tools.
///
/// The tool block itself is left alone so the prefix cache still holds;
/// `tool_choice: "none"` is what stops the model calling something else, and
/// it is the one setting every measured provider honours.
pub fn argument_overrides(tool: &Tool) -> Map<String, Value> {
    let mut map = Map::new();
    map.insert("tool_choice".to_string(), Value::String("none".to_string()));
    map.insert(
        "response_format".to_string(),
        json!({
            "type": "json_schema",
            "json_schema": {
                "name": schema_name(&tool.name),
                "strict": false,
                "schema": tool.parameters,
            }
        }),
    );
    map
}

/// The arguments in a reply to [`argument_request`], compact, or `None`.
///
/// Tolerates a code fence or a sentence around the object — models add them
/// under `json_schema` often enough — but never a non-object: a tool's
/// arguments are an object, and anything else is a failed synthesis that must
/// fall back to the model's own step.
pub fn parse_arguments(text: &str) -> Option<String> {
    let start = text.find('{')?;
    let end = text.rfind('}')?;
    if end < start {
        return None;
    }
    let value: Value = serde_json::from_str(&text[start..=end]).ok()?;
    if !value.is_object() {
        return None;
    }
    serde_json::to_string(&value).ok()
}

/// Whether directed arguments are for this tool at all: every required field
/// is present. When the harness chose `Shell` and the model answers with an
/// edit (`{"old", "new"}`), the model meant another step; dispatching the call
/// would only fail, so the caller lets the model take the step instead.
pub fn fits(arguments: &str, tool: &Tool) -> bool {
    let Ok(Value::Object(map)) = serde_json::from_str::<Value>(arguments) else {
        return false;
    };
    let required = tool.parameters.get("required").and_then(Value::as_array);
    required.map_or(true, |fields| {
        fields
            .iter()
            .filter_map(Value::as_str)
            .all(|field| map.get(field).is_some_and(|v| !v.is_null()))
    })
}

fn schema_name(name: &str) -> String {
    let cleaned: String = name
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() || c == '_' || c == '-' { c } else { '_' })
        .take(64)
        .collect();
    if cleaned.is_empty() {
        "arguments".to_string()
    } else {
        cleaned
    }
}

// ----- the record -------------------------------------------------------

/// Append one decision to `<KIMI_SHARE_DIR>/director.jsonl`.
///
/// Every decision point is a labelled example waiting to happen: the state, the
/// distribution, what the harness did, and — added by the caller — what came
/// of it. This file is the evaluation set; it costs one small append per step.
pub fn record(decision: &Value) {
    let path = crate::share::get_share_dir().join("director.jsonl");
    let line = match serde_json::to_string(decision) {
        Ok(line) => line,
        Err(_) => return,
    };
    let written = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .and_then(|mut file| writeln!(file, "{line}"));
    if let Err(error) = written {
        debug!("could not record a director decision at {}: {error}", path.display());
    }
}

pub fn log_decision(step: usize, decision: &Decision, mode: DirectorMode, elapsed: Duration) {
    info!(
        "Director step {step}: {}{} in {}ms",
        decision.directive.label(),
        if mode == DirectorMode::Shadow { " [shadow]" } else { "" },
        elapsed.as_millis()
    );
}

// ----- text helpers -----------------------------------------------------

/// Whether this user message was put there by the loop rather than the person.
///
/// Completion-gate reminders, D-Mail and retry hints all arrive as user
/// messages wrapped in `<system>`. Reading one as the request would have the
/// decision judge the turn against the loop's own last instruction.
pub fn is_injected(message: &Message) -> bool {
    message_text(message).trim_start().starts_with("<system>")
}

pub fn message_text(message: &Message) -> String {
    message
        .content
        .iter()
        .filter_map(|part| match part {
            ContentPart::Text(text) => Some(text.text.as_str()),
            _ => None,
        })
        .collect::<Vec<_>>()
        .join("\n")
}

/// `text` cut to `limit` characters on a char boundary, never mid-codepoint.
pub fn clipped(text: &str, limit: usize) -> String {
    let trimmed = text.trim();
    if trimmed.chars().count() <= limit {
        return trimmed.to_string();
    }
    trimmed.chars().take(limit).collect::<String>() + "…"
}

/// The start and the end of a long text. Instructions sit at the top of a
/// request and the "stop here" at the bottom; the middle is usually a source.
fn head_and_tail(text: &str, head: usize, tail: usize) -> String {
    let trimmed = text.trim();
    let count = trimmed.chars().count();
    if count <= head + tail {
        return trimmed.to_string();
    }
    let start: String = trimmed.chars().take(head).collect();
    let end: String = trimmed.chars().skip(count - tail).collect();
    format!("{start}\n…\n{end}")
}

fn first_sentence(text: &str, limit: usize) -> String {
    let trimmed = text.trim();
    let cut = trimmed
        .char_indices()
        .find(|(i, c)| {
            matches!(c, '.' | '\n')
                && trimmed[*i + c.len_utf8()..]
                    .chars()
                    .next()
                    .map(char::is_whitespace)
                    .unwrap_or(true)
        })
        .map(|(i, c)| &trimmed[..i + c.len_utf8()])
        .unwrap_or(trimmed);
    clipped(cut, limit)
}

fn env_f64(key: &str, default: f64) -> f64 {
    std::env::var(key)
        .ok()
        .and_then(|raw| raw.trim().parse::<f64>().ok())
        .filter(|value| (0.0..=1.0).contains(value))
        .unwrap_or(default)
}

fn env_usize(key: &str, default: usize) -> usize {
    std::env::var(key)
        .ok()
        .and_then(|raw| raw.trim().parse::<usize>().ok())
        .unwrap_or(default)
}

#[cfg(test)]
mod tests {
    use super::*;
    use kosong::message::{TextPart, ToolCall};

    fn text(role: Role, body: &str) -> Message {
        Message::new(role, vec![ContentPart::Text(TextPart::new(body))])
    }

    fn calling(name: &str, id: &str) -> Message {
        let mut message = Message::new(Role::Assistant, Vec::new());
        let mut call = ToolCall::new(id, name);
        call.function.arguments = Some("{\"q\":\"x\"}".to_string());
        message.tool_calls = Some(vec![call]);
        message
    }

    fn result(id: &str, body: &str) -> Message {
        let mut message = text(Role::Tool, body);
        message.tool_call_id = Some(id.to_string());
        message
    }

    fn tool(name: &str, description: &str) -> Tool {
        Tool::new(
            name,
            description,
            json!({"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}),
        )
    }

    fn ranked(pairs: &[(&str, f64)]) -> Vec<(String, f64)> {
        pairs.iter().map(|(n, p)| (n.to_string(), *p)).collect()
    }

    #[test]
    fn a_clear_tool_is_called_by_the_harness() {
        let d = policy(&ranked(&[("find", 0.93), (REPLY, 0.05)]), REPLY, &DirectorConfig::default());
        assert_eq!(d, Directive::Call { tool: "find".into(), p: 0.93 });
    }

    #[test]
    fn a_clear_reply_withholds_the_tools() {
        let d = policy(&ranked(&[(REPLY, 0.91), ("find", 0.09)]), REPLY, &DirectorConfig::default());
        assert_eq!(d, Directive::Reply { p: 0.91 });
        assert_eq!(d.overrides().unwrap()["tool_choice"], "none");
    }

    #[test]
    fn a_split_between_tools_still_means_act() {
        let d = policy(
            &ranked(&[("find", 0.5), ("read", 0.42), (REPLY, 0.08)]),
            REPLY,
            &DirectorConfig::default(),
        );
        assert!(matches!(d, Directive::Act { p } if (p - 0.92).abs() < 1e-9));
        assert_eq!(d.overrides().unwrap()["tool_choice"], "required");
    }

    #[test]
    fn a_hedged_read_changes_nothing() {
        let d = policy(
            &ranked(&[(REPLY, 0.55), ("find", 0.45)]),
            REPLY,
            &DirectorConfig::default(),
        );
        assert!(matches!(d, Directive::Free { .. }));
        assert!(d.overrides().is_none());
    }

    #[test]
    fn the_reply_option_never_shadows_a_tool_of_the_same_name() {
        let tools = vec![tool(REPLY, "a tool that happens to be called this")];
        assert_ne!(reply_key(&tools), REPLY);
    }

    #[test]
    fn the_state_reads_the_request_inside_chotus_envelope() {
        let history = vec![text(
            Role::User,
            "## System\nYou are Tura, a study companion.\n\n## User\nCall read_learner_record, then write the scene.",
        )];
        let state = render_state("coding system prompt", &history, None).unwrap();
        assert_eq!(state["assistant"], "You are Tura, a study companion.");
        assert_eq!(state["request"], "Call read_learner_record, then write the scene.");
        assert_eq!(state["steps_this_turn"], "none yet");
    }

    #[test]
    fn the_state_carries_this_turns_steps_and_their_results() {
        let history = vec![
            text(Role::User, "an earlier turn"),
            calling("old_tool", "c0"),
            result("c0", "old"),
            text(Role::User, "look it up"),
            calling("find", "c1"),
            result("c1", "found page 11"),
            calling("read", "c2"),
            result("c2", "<system>ERROR: no such record</system>"),
        ];
        let state = render_state("sys", &history, None).unwrap();
        let steps = state["steps_this_turn"].as_array().unwrap();
        assert_eq!(steps.len(), 2, "only this turn's steps");
        assert_eq!(steps[0]["tool"], "find");
        assert_eq!(steps[0]["ok"], true);
        assert_eq!(steps[1]["ok"], false);
    }

    #[test]
    fn the_loops_own_messages_are_never_the_request() {
        let history = vec![
            text(Role::User, "draw the diagram"),
            text(Role::Assistant, "Here it is."),
            text(Role::User, "<system>Retry the same step.</system>"),
        ];
        let state = render_state("sys", &history, None).unwrap();
        assert_eq!(state["request"], "draw the diagram");
        assert_eq!(state["assistant_wrote_last"], "Here it is.");
    }

    #[test]
    fn a_dhee_packet_rides_in_the_context_slot() {
        let history = vec![text(Role::User, "hi")];
        let packet = json!({"scene": "revising Newton's laws for a test on Friday"});
        let state = render_state("sys", &history, Some(&packet)).unwrap();
        assert_eq!(state["context"], packet);
    }

    #[test]
    fn a_long_request_keeps_its_instructions_and_its_ending() {
        let body = format!("DO THIS FIRST {} STOP THERE", "source ".repeat(2_000));
        let clipped = head_and_tail(&body, 100, 50);
        assert!(clipped.starts_with("DO THIS FIRST"));
        assert!(clipped.ends_with("STOP THERE"));
    }

    #[test]
    fn a_large_catalogue_is_skimmed_one_line_per_tool() {
        let tools = vec![tool("find", "Search the books. Returns pages with scores and more detail here.")];
        let skim = question(&tools, REPLY, false, Phase::BeforeStep);
        assert_eq!(skim[QUESTION]["criteria"]["find"]["what"], "Search the books.");
        assert!(skim[QUESTION]["criteria"]["find"].get("takes").is_none());
        let full = question(&tools, REPLY, true, Phase::BeforeStep);
        assert_eq!(full[QUESTION]["criteria"]["find"]["takes"], json!(["q"]));
    }

    #[test]
    fn arguments_survive_a_fence_and_nothing_else_does() {
        assert_eq!(
            parse_arguments("```json\n{\"q\": \"x\"}\n```").as_deref(),
            Some("{\"q\":\"x\"}")
        );
        assert!(parse_arguments("I will call the tool now.").is_none());
        assert!(parse_arguments("[1, 2]").is_none());
        assert!(parse_arguments("{broken").is_none());
    }

    #[test]
    fn the_argument_call_keeps_tools_but_cannot_call_one() {
        let overrides = argument_overrides(&tool("read learner/record", "x"));
        assert_eq!(overrides["tool_choice"], "none");
        assert_eq!(
            overrides["response_format"]["json_schema"]["name"],
            "read_learner_record"
        );
    }

    #[test]
    fn unexercised_edits_are_part_of_the_state_and_both_questions() {
        let history = vec![text(Role::User, "fix the bug")];
        let facts = json!({"turn_facts": {"edits_not_yet_exercised": true, "files_read_this_turn": ["a.py"]}});
        let state = render_state("You are root.", &history, Some(&facts)).unwrap();
        assert_eq!(state["context"]["turn_facts"]["edits_not_yet_exercised"], true);
        let tools = [tool("Shell", "Run a command.")];
        for phase in [Phase::BeforeStep, Phase::TurnEnd] {
            let asked = question(&tools, REPLY, true, phase).to_string();
            assert!(asked.contains("edits_not_yet_exercised"), "{phase:?}");
        }
        let before = question(&tools, REPLY, true, Phase::BeforeStep).to_string();
        assert!(before.contains("files_read_this_turn"));
    }

    #[test]
    fn arguments_for_a_different_tool_do_not_fit() {
        let shell = Tool::new(
            "Shell",
            "Run a command.",
            json!({"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}),
        );
        assert!(fits(r#"{"command": "pytest -q"}"#, &shell));
        assert!(!fits(r#"{"old": "a", "new": "b"}"#, &shell));
        assert!(!fits(r#"{"command": null}"#, &shell));
        assert!(fits(r#"{"q": "x"}"#, &tool("find", "x")));
    }
}
