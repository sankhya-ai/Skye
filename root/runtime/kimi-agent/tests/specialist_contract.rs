use kimi_agent::agentspec::SpecialistOutputContract;
use kimi_agent::tools::multiagent::validate_specialist_output;

#[test]
fn researcher_requires_evidence_for_every_claim() {
    let valid = r#"{
        "schema_version":"chotu.specialist_output.v1",
        "role":"researcher",
        "summary":"Found the governing source.",
        "claims":[{
            "id":"claim-1",
            "claim":"The setting is enabled.",
            "evidence_refs":[{"kind":"api_docs","source":"https://example.test/docs","locator":"section-2"}]
        }],
        "unresolved_uncertainties":[]
    }"#;
    assert!(validate_specialist_output(SpecialistOutputContract::Researcher, valid).is_ok());

    let invalid = valid.replace(
        r#""evidence_refs":[{"kind":"api_docs","source":"https://example.test/docs","locator":"section-2"}]"#,
        r#""evidence_refs":[]"#,
    );
    assert!(
        validate_specialist_output(SpecialistOutputContract::Researcher, &invalid)
            .expect_err("claim without evidence must fail")
            .contains("evidence_refs must contain at least one item")
    );
}

#[test]
fn reasoner_requires_an_answer_and_evidence_bearing_claims() {
    let valid = r#"{
        "schema_version":"chotu.specialist_output.v1",
        "role":"reasoner",
        "summary":"Compared the supplied evidence and constraints.",
        "answer":"Choose the reversible staged approach.",
        "claims":[{
            "id":"claim-1",
            "claim":"The staged approach satisfies the rollback requirement.",
            "evidence_refs":[{"kind":"owner_instruction","source":"task","locator":"rollback requirement"}]
        }],
        "assumptions":["The supplied constraints are current."],
        "remaining_risks":[]
    }"#;
    assert!(validate_specialist_output(SpecialistOutputContract::Reasoner, valid).is_ok());

    let invalid = valid.replace(
        r#""evidence_refs":[{"kind":"owner_instruction","source":"task","locator":"rollback requirement"}]"#,
        r#""evidence_refs":[]"#,
    );
    assert!(
        validate_specialist_output(SpecialistOutputContract::Reasoner, &invalid)
            .expect_err("reasoner claims without evidence must fail")
            .contains("evidence_refs must contain at least one item")
    );
}

#[test]
fn coder_records_actual_verification_status() {
    let valid = r#"{
        "schema_version":"chotu.specialist_output.v1",
        "role":"coder",
        "summary":"Implemented and checked the change.",
        "changed_paths":["src/lib.rs"],
        "verification":[{
            "command":"cargo test -p kimi-agent",
            "status":"passed",
            "evidence_refs":[{"kind":"test_output","source":"cargo test","locator":"exit=0"}]
        }],
        "remaining_risks":[]
    }"#;
    assert!(validate_specialist_output(SpecialistOutputContract::Coder, valid).is_ok());

    let invalid = valid.replace(r#""status":"passed""#, r#""status":"unknown""#);
    assert!(
        validate_specialist_output(SpecialistOutputContract::Coder, &invalid)
            .expect_err("unknown check status must fail")
            .contains("must be passed or failed")
    );
}

#[test]
fn critic_fail_requires_a_concrete_counterexample() {
    let invalid = r#"{
        "schema_version":"chotu.specialist_output.v1",
        "role":"critic",
        "summary":"The proof is incomplete.",
        "verdict":"fail",
        "checked_obligations":["tests pass"],
        "evidence_refs":[{"kind":"test_output","source":"cargo test","claim_id":"claim-1"}],
        "counterexamples":[]
    }"#;
    assert!(
        validate_specialist_output(SpecialistOutputContract::Critic, invalid)
            .expect_err("fail without counterexample must fail")
            .contains("requires at least one counterexample")
    );
}

#[test]
fn verifier_verdict_must_agree_with_unmet_obligations() {
    let valid = r#"{
        "schema_version":"chotu.specialist_output.v1",
        "role":"verifier",
        "summary":"All obligations passed.",
        "verdict":"pass",
        "checked_obligations":["tests pass"],
        "evidence_refs":[{"kind":"test_output","source":"cargo test","claim_id":"claim-1"}],
        "unmet_obligations":[]
    }"#;
    assert!(validate_specialist_output(SpecialistOutputContract::Verifier, valid).is_ok());

    let invalid = valid.replace(
        r#""unmet_obligations":[]"#,
        r#""unmet_obligations":["release build failed"]"#,
    );
    assert!(
        validate_specialist_output(SpecialistOutputContract::Verifier, &invalid)
            .expect_err("pass with unmet obligation must fail")
            .contains("cannot contain unmet obligations")
    );
}

#[test]
fn contract_rejects_markdown_and_role_substitution() {
    let verifier = r#"{"schema_version":"chotu.specialist_output.v1","role":"verifier","summary":"ok","verdict":"pass","checked_obligations":["one"],"evidence_refs":[{"kind":"tool_observation","source":"tool"}],"unmet_obligations":[]}"#;
    assert!(
        validate_specialist_output(
            SpecialistOutputContract::Verifier,
            &format!("```json\n{verifier}\n```")
        )
        .is_err()
    );
    assert!(
        validate_specialist_output(SpecialistOutputContract::Critic, verifier)
            .expect_err("role substitution must fail")
            .contains("role must be critic")
    );
}
