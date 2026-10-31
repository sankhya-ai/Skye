use serde::Serialize;
use serde_json::{Value, json};

use crate::constant::VERSION;

pub const CHOTU_RUNTIME_PROFILE_ENV: &str = "CHOTU_AGENT_RUNTIME_PROFILE";

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct RuntimeIdentity {
    pub profile: String,
    pub name: String,
    pub version: String,
    pub upstream_engine: String,
}

impl RuntimeIdentity {
    pub fn from_environment() -> Self {
        Self::from_profile(std::env::var(CHOTU_RUNTIME_PROFILE_ENV).ok().as_deref())
    }

    pub fn from_profile(profile: Option<&str>) -> Self {
        if profile.is_some_and(|value| value.eq_ignore_ascii_case("chotu")) {
            return Self {
                profile: "chotu".to_string(),
                name: "chotu-agent-rs".to_string(),
                version: VERSION.to_string(),
                upstream_engine: "kimi-agent-rs".to_string(),
            };
        }
        Self {
            profile: "kimi".to_string(),
            name: crate::constant::NAME.to_string(),
            version: VERSION.to_string(),
            upstream_engine: "kimi-agent-rs".to_string(),
        }
    }

    pub fn capability_manifest(&self) -> Value {
        json!({
            "schema_version": "chotu.agent_runtime_capabilities.v1",
            "profile": self.profile,
            "native": {
                "soul_loop": true,
                "context_compaction": true,
                "dhee_aware_compaction": true,
                "checkpoint_rewind": true,
                "fixed_subagents": true,
                "per_subagent_models": true,
                "per_subagent_output_budgets": true,
                "typed_specialist_outputs": true,
                "verifier_controlled_stop": true,
                "verifier_controlled_flows": true,
                "completion_gate_receipts": true,
                "dynamic_subagents": true,
                "parallel_tasks": true,
                "council_budget": true,
                "turn_token_budget": true,
                "turn_wall_clock_budget": true,
                "budget_receipts": true,
                "skills": true,
                "flows": true,
                "mcp": true,
                "external_tools": true,
                "approvals": true,
                "cancellation": true,
                "session_restore": true,
                "web_research": true
            },
            "chotu_owned": self.profile == "chotu",
            "host_integrations": {
                "dhee_memory": "typed_external_wire_memory_and_cognition",
                "dhee_mutation_readiness": "host_verified_not_model_supplied",
                "dhee_action_lifecycle": "pre_execution_guard_scene_receipts_and_update_capsules",
                "cognition_learning": "dhee_verified_marginal_quality_token_latency_outcomes",
                "owner_policy": "external_wire_approval",
                "product_tools": "external_wire_catalog"
            },
        })
    }
}
