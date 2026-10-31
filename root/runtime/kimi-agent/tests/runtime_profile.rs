use kimi_agent::runtime_profile::RuntimeIdentity;

#[test]
fn chotu_profile_has_native_identity_and_capability_manifest() {
    let identity = RuntimeIdentity::from_profile(Some("chotu"));
    let capabilities = identity.capability_manifest();

    assert_eq!(identity.name, "chotu-agent-rs");
    assert_eq!(identity.upstream_engine, "kimi-agent-rs");
    assert_eq!(capabilities["profile"], "chotu");
    assert_eq!(capabilities["chotu_owned"], true);
    assert_eq!(capabilities["native"]["parallel_tasks"], true);
    assert_eq!(capabilities["native"]["council_budget"], true);
    assert_eq!(capabilities["native"]["turn_token_budget"], true);
    assert_eq!(capabilities["native"]["turn_wall_clock_budget"], true);
    assert_eq!(capabilities["native"]["budget_receipts"], true);
    assert_eq!(capabilities["native"]["per_subagent_models"], true);
    assert_eq!(capabilities["native"]["per_subagent_output_budgets"], true);
    assert_eq!(capabilities["native"]["typed_specialist_outputs"], true);
    assert_eq!(capabilities["native"]["verifier_controlled_stop"], true);
    assert_eq!(capabilities["native"]["verifier_controlled_flows"], true);
    assert_eq!(capabilities["native"]["completion_gate_receipts"], true);
    assert_eq!(capabilities["native"]["checkpoint_rewind"], true);
    assert_eq!(capabilities["native"]["dhee_aware_compaction"], true);
    assert_eq!(
        capabilities["host_integrations"]["dhee_memory"],
        "typed_external_wire_memory_and_cognition"
    );
    assert_eq!(
        capabilities["host_integrations"]["dhee_mutation_readiness"],
        "host_verified_not_model_supplied"
    );
    assert_eq!(
        capabilities["host_integrations"]["dhee_action_lifecycle"],
        "pre_execution_guard_scene_receipts_and_update_capsules"
    );
    assert_eq!(
        capabilities["host_integrations"]["cognition_learning"],
        "dhee_verified_marginal_quality_token_latency_outcomes"
    );
}

#[test]
fn default_profile_preserves_upstream_wire_identity() {
    let identity = RuntimeIdentity::from_profile(None);

    assert_eq!(identity.profile, "kimi");
    assert_eq!(identity.name, "Kimi Code CLI");
}
