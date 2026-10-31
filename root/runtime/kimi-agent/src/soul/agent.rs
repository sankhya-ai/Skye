use std::collections::HashMap;
use std::path::Path;
use std::sync::Arc;

use chrono::Local;
use kaos::KaosPath;
use regex::Regex;
use tracing::{debug, info};

use crate::agentspec::{SpecialistOutputContract, load_agent_spec};
use crate::config::Config;
use crate::council_budget::CouncilBudget;
use crate::exception::{AgentSpecError, SystemPromptTemplateError};
use crate::llm::{LLM, augment_provider_with_env_vars, create_llm_with_max_tokens};
use crate::session::Session;
use crate::skill::{Skill, discover_skills_from_roots, index_skills, resolve_skills_roots};
use crate::soul::approval::Approval;
use crate::soul::denwarenji::DenwaRenji;
use crate::soul::toolset::KimiToolset;
use crate::utils::{Environment, list_directory};

#[derive(Clone, Debug)]
#[allow(non_snake_case)]
pub struct BuiltinSystemPromptArgs {
    pub KIMI_NOW: String,
    pub KIMI_WORK_DIR: KaosPath,
    pub KIMI_WORK_DIR_LS: String,
    pub KIMI_AGENTS_MD: String,
    pub KIMI_SKILLS: String,
}

impl BuiltinSystemPromptArgs {
    fn as_map(&self) -> HashMap<String, String> {
        let mut map = HashMap::new();
        map.insert("KIMI_NOW".to_string(), self.KIMI_NOW.clone());
        map.insert(
            "KIMI_WORK_DIR".to_string(),
            self.KIMI_WORK_DIR.to_string_lossy(),
        );
        map.insert(
            "KIMI_WORK_DIR_LS".to_string(),
            self.KIMI_WORK_DIR_LS.clone(),
        );
        map.insert("KIMI_AGENTS_MD".to_string(), self.KIMI_AGENTS_MD.clone());
        map.insert("KIMI_SKILLS".to_string(), self.KIMI_SKILLS.clone());
        map
    }
}

pub async fn load_agents_md(work_dir: &KaosPath) -> Option<String> {
    // `CLAUDE.md` is read last so a repo already set up for Claude Code gets
    // its project instructions without a second copy under another name.
    let candidates = [
        work_dir.clone() / "AGENTS.md",
        work_dir.clone() / "agents.md",
        work_dir.clone() / "CLAUDE.md",
    ];
    for path in candidates {
        if path.is_file(true).await {
            if let Ok(text) = path.read_text().await {
                info!("Loaded agents.md: {}", path.to_string_lossy());
                return Some(text.trim().to_string());
            }
        }
    }
    info!("No AGENTS.md found in {}", work_dir.to_string_lossy());
    None
}

#[derive(Clone)]
pub struct Runtime {
    pub config: Config,
    pub llm: Option<Arc<LLM>>,
    pub session: Session,
    pub builtin_args: BuiltinSystemPromptArgs,
    pub denwa_renji: Arc<tokio::sync::Mutex<DenwaRenji>>,
    pub approval: Arc<Approval>,
    pub labor_market: Arc<tokio::sync::Mutex<LaborMarket>>,
    pub environment: Environment,
    pub skills: HashMap<String, Skill>,
    pub council_budget: Arc<CouncilBudget>,
}

impl Runtime {
    pub async fn create(
        config: Config,
        llm: Option<Arc<LLM>>,
        session: Session,
        yolo: bool,
        skills_dir: Option<KaosPath>,
    ) -> Runtime {
        let work_dir = session.work_dir.clone();
        let (ls_output, agents_md, environment) = tokio::join!(
            list_directory(&work_dir),
            load_agents_md(&work_dir),
            Environment::detect()
        );

        let skills_roots = resolve_skills_roots(&work_dir, skills_dir).await;
        let skills = discover_skills_from_roots(&skills_roots).await;
        info!("Discovered {} skill(s)", skills.len());
        let skills_by_name = index_skills(&skills);
        let skills_formatted = if skills.is_empty() {
            "No skills found.".to_string()
        } else {
            skills
                .iter()
                .map(|skill| {
                    format!(
                        "- {}\n  - Path: {}\n  - Description: {}",
                        skill.name,
                        skill.skill_md_file().to_string_lossy(),
                        skill.description
                    )
                })
                .collect::<Vec<_>>()
                .join("\n")
        };

        Runtime {
            config,
            llm,
            session,
            builtin_args: BuiltinSystemPromptArgs {
                KIMI_NOW: Local::now().to_rfc3339(),
                KIMI_WORK_DIR: work_dir,
                KIMI_WORK_DIR_LS: ls_output,
                KIMI_AGENTS_MD: agents_md.unwrap_or_default(),
                KIMI_SKILLS: skills_formatted,
            },
            denwa_renji: Arc::new(tokio::sync::Mutex::new(DenwaRenji::new())),
            approval: Arc::new(Approval::new(yolo)),
            labor_market: Arc::new(tokio::sync::Mutex::new(LaborMarket::new())),
            environment,
            skills: skills_by_name,
            council_budget: CouncilBudget::from_environment(),
        }
    }

    pub fn copy_for_fixed_subagent(&self) -> Runtime {
        Runtime {
            config: self.config.clone(),
            llm: self.llm.clone(),
            session: self.session.clone(),
            builtin_args: self.builtin_args.clone(),
            denwa_renji: Arc::new(tokio::sync::Mutex::new(DenwaRenji::new())),
            approval: Arc::new(self.approval.share()),
            labor_market: Arc::new(tokio::sync::Mutex::new(LaborMarket::new())),
            environment: self.environment.clone(),
            skills: self.skills.clone(),
            council_budget: Arc::clone(&self.council_budget),
        }
    }

    pub async fn copy_for_fixed_subagent_model(
        &self,
        model_key: Option<&str>,
    ) -> Result<Runtime, anyhow::Error> {
        self.copy_for_fixed_subagent_model_with_max_tokens(model_key, None)
            .await
    }

    pub async fn copy_for_fixed_subagent_model_with_max_tokens(
        &self,
        model_key: Option<&str>,
        max_tokens: Option<u64>,
    ) -> Result<Runtime, anyhow::Error> {
        let mut runtime = self.copy_for_fixed_subagent();
        if model_key.filter(|value| !value.is_empty()).is_none() && max_tokens.is_none() {
            return Ok(runtime);
        }
        let model_key = model_key
            .filter(|value| !value.is_empty())
            .unwrap_or(self.config.default_model.as_str());
        if model_key.is_empty() {
            return Err(anyhow::anyhow!(
                "Subagent output budget requires a configured default model"
            ));
        }
        let mut model = self
            .config
            .models
            .get(model_key)
            .cloned()
            .ok_or_else(|| anyhow::anyhow!("Subagent model is not configured: {model_key}"))?;
        let configured_model_name = model.model.clone();
        let mut provider = self
            .config
            .providers
            .get(&model.provider)
            .cloned()
            .ok_or_else(|| {
                anyhow::anyhow!("Subagent provider is not configured: {}", model.provider)
            })?;
        augment_provider_with_env_vars(&mut provider, &mut model).map_err(anyhow::Error::new)?;
        model.model = configured_model_name;
        runtime.llm = create_llm_with_max_tokens(
            &provider,
            &model,
            Some(self.config.default_thinking),
            Some(&self.session.id),
            max_tokens,
        )
        .await
        .map_err(anyhow::Error::new)?
        .map(Arc::new);
        if runtime.llm.is_none() {
            return Err(anyhow::anyhow!(
                "Subagent model could not be created: {model_key}"
            ));
        }
        Ok(runtime)
    }

    /// Rescope this runtime to a different working directory. The session
    /// work dir, the file-tool scope, and the work-dir-derived system prompt
    /// args all move together so an agent loaded from this runtime perceives
    /// and mutates only the new directory. Fails when the directory does not
    /// exist: a missing solver workspace must abort the turn, not silently
    /// fall back to the shared checkout.
    pub async fn rescope_to_work_dir(&mut self, work_dir: KaosPath) -> Result<(), anyhow::Error> {
        let work_dir = work_dir.canonical();
        if !work_dir.is_dir(true).await {
            return Err(anyhow::anyhow!(
                "Subagent cwd does not exist: {}",
                work_dir.to_string_lossy()
            ));
        }
        let (ls_output, agents_md) =
            tokio::join!(list_directory(&work_dir), load_agents_md(&work_dir));
        self.session.work_dir = work_dir.clone();
        self.builtin_args.KIMI_WORK_DIR = work_dir;
        self.builtin_args.KIMI_WORK_DIR_LS = ls_output;
        self.builtin_args.KIMI_AGENTS_MD = agents_md.unwrap_or_default();
        Ok(())
    }

    pub fn copy_for_dynamic_subagent(&self) -> Runtime {
        Runtime {
            config: self.config.clone(),
            llm: self.llm.clone(),
            session: self.session.clone(),
            builtin_args: self.builtin_args.clone(),
            denwa_renji: Arc::new(tokio::sync::Mutex::new(DenwaRenji::new())),
            approval: Arc::new(self.approval.share()),
            labor_market: Arc::clone(&self.labor_market),
            environment: self.environment.clone(),
            skills: self.skills.clone(),
            council_budget: Arc::clone(&self.council_budget),
        }
    }
}

#[derive(Clone)]
pub struct Agent {
    pub name: String,
    pub system_prompt: String,
    pub toolset: Arc<tokio::sync::Mutex<KimiToolset>>,
    pub runtime: Runtime,
    pub completion_gate_tools: Vec<String>,
    pub context_continuity_tool: Option<String>,
    pub output_contract: Option<SpecialistOutputContract>,
}

#[derive(Default)]
pub struct LaborMarket {
    fixed_subagents: HashMap<String, Agent>,
    fixed_subagent_descs: HashMap<String, String>,
    dynamic_subagents: HashMap<String, Agent>,
}

impl LaborMarket {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn add_fixed_subagent(&mut self, name: String, agent: Agent, description: String) {
        self.fixed_subagents.insert(name.clone(), agent);
        self.fixed_subagent_descs.insert(name, description);
    }

    pub fn add_dynamic_subagent(&mut self, name: String, agent: Agent) {
        self.dynamic_subagents.insert(name, agent);
    }

    pub fn fixed_subagents(&self) -> &HashMap<String, Agent> {
        &self.fixed_subagents
    }

    pub fn dynamic_subagents(&self) -> &HashMap<String, Agent> {
        &self.dynamic_subagents
    }

    pub fn all_subagents(&self) -> HashMap<String, Agent> {
        let mut combined = self.fixed_subagents.clone();
        combined.extend(self.dynamic_subagents.clone());
        combined
    }

    pub fn fixed_subagent_descs(&self) -> &HashMap<String, String> {
        &self.fixed_subagent_descs
    }
}

pub fn load_agent<'a>(
    agent_file: &'a Path,
    runtime: Runtime,
    mcp_configs: &'a [serde_json::Value],
) -> futures::future::BoxFuture<'a, Result<Agent, anyhow::Error>> {
    Box::pin(async move {
        info!("Loading agent: {}", agent_file.display());
        let agent_spec = load_agent_spec(agent_file).await?;
        let system_prompt = load_system_prompt(
            &agent_spec.system_prompt_path,
            &agent_spec.system_prompt_args,
            &runtime.builtin_args,
        )
        .await?;

        for (subagent_name, subagent_spec) in agent_spec.subagents.iter() {
            debug!("Loading subagent: {}", subagent_name);
            let mut subagent_runtime = runtime
                .copy_for_fixed_subagent_model_with_max_tokens(
                    subagent_spec.model.as_deref(),
                    subagent_spec.max_tokens,
                )
                .await?;
            if let Some(cwd) = subagent_spec.cwd.as_ref() {
                subagent_runtime
                    .rescope_to_work_dir(KaosPath::from(cwd.clone()))
                    .await?;
            }
            let mut subagent =
                load_agent(&subagent_spec.path, subagent_runtime, mcp_configs).await?;
            subagent.output_contract = subagent_spec.output_contract;
            runtime.labor_market.lock().await.add_fixed_subagent(
                subagent_name.clone(),
                subagent,
                subagent_spec.description.clone(),
            );
        }

        let toolset = Arc::new(tokio::sync::Mutex::new(KimiToolset::new()));
        {
            let mut guard = toolset.lock().await;
            let mut tools = agent_spec.tools.clone();
            if !agent_spec.exclude_tools.is_empty() {
                debug!("Excluding tools: {:?}", agent_spec.exclude_tools);
                tools.retain(|tool| !agent_spec.exclude_tools.contains(tool));
            }
            guard
                .load_tools(&tools, &runtime, Arc::clone(&toolset))
                .map_err(anyhow::Error::from)?;

            if !mcp_configs.is_empty() {
                guard
                    .load_mcp_tools(mcp_configs, &runtime, Arc::clone(&toolset))
                    .await?;
            }
        }

        Ok(Agent {
            name: agent_spec.name,
            system_prompt,
            toolset,
            runtime,
            completion_gate_tools: agent_spec.completion_gate_tools,
            context_continuity_tool: agent_spec.context_continuity_tool,
            output_contract: None,
        })
    })
}

async fn load_system_prompt(
    path: &Path,
    args: &HashMap<String, String>,
    builtin_args: &BuiltinSystemPromptArgs,
) -> Result<String, anyhow::Error> {
    info!("Loading system prompt: {}", path.display());
    let system_prompt = tokio::fs::read_to_string(path).await.map_err(|err| {
        AgentSpecError::new(format!(
            "Failed to read system prompt {}: {err}",
            path.display()
        ))
    })?;

    let mut values = builtin_args.as_map();
    for (key, value) in args {
        values.insert(key.clone(), value.clone());
    }
    debug!(
        "Substituting system prompt with builtin args: {:?}, spec args: {:?}",
        builtin_args.as_map(),
        args
    );

    let rendered = substitute_template(system_prompt.trim(), &values).map_err(|missing| {
        SystemPromptTemplateError::new(format!(
            "Missing system prompt arg in {}: {}",
            path.display(),
            missing.join(", ")
        ))
    })?;
    Ok(rendered)
}

fn substitute_template(
    template: &str,
    values: &HashMap<String, String>,
) -> Result<String, Vec<String>> {
    let re = Regex::new(r"\$\{([A-Za-z0-9_]+)\}").expect("valid system prompt placeholder regex");

    let mut missing: Vec<String> = Vec::new();
    let result = re
        .replace_all(template, |caps: &regex::Captures<'_>| {
            let key = caps.get(1).map(|m| m.as_str()).unwrap_or("");
            match values.get(key) {
                Some(value) => value.clone(),
                None => {
                    missing.push(key.to_string());
                    caps.get(0).map(|m| m.as_str()).unwrap_or("").to_string()
                }
            }
        })
        .to_string();

    if !missing.is_empty() {
        missing.sort();
        missing.dedup();
        return Err(missing);
    }

    Ok(result)
}
