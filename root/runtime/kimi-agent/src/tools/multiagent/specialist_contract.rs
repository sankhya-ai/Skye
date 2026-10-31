use serde_json::Value;

use crate::agentspec::SpecialistOutputContract;

pub const SPECIALIST_OUTPUT_SCHEMA: &str = "chotu.specialist_output.v1";

pub fn validate_specialist_output(
    contract: SpecialistOutputContract,
    text: &str,
) -> Result<Value, String> {
    let value: Value = serde_json::from_str(text.trim())
        .map_err(|error| format!("response must be one JSON object: {error}"))?;
    let object = value
        .as_object()
        .ok_or_else(|| "response must be one JSON object".to_string())?;
    require_exact_string(object, "schema_version", SPECIALIST_OUTPUT_SCHEMA)?;
    require_exact_string(object, "role", contract.as_str())?;
    require_non_empty_string(object, "summary")?;

    match contract {
        SpecialistOutputContract::Researcher => validate_researcher(object)?,
        SpecialistOutputContract::Reasoner => validate_reasoner(object)?,
        SpecialistOutputContract::Coder => validate_coder(object)?,
        SpecialistOutputContract::Critic => validate_critic(object)?,
        SpecialistOutputContract::Verifier => validate_verifier(object)?,
    }
    Ok(value)
}

pub fn repair_prompt(contract: SpecialistOutputContract, error: &str) -> String {
    format!(
        "Your output was rejected by Chotu's native {} contract: {}\n\nReturn ONLY one JSON object matching this exact contract:\n{}\nDo not use Markdown fences or add prose outside the JSON object.",
        contract.as_str(),
        error,
        contract_description(contract),
    )
}

fn validate_researcher(object: &serde_json::Map<String, Value>) -> Result<(), String> {
    require_only_fields(
        object,
        &[
            "schema_version",
            "role",
            "summary",
            "claims",
            "unresolved_uncertainties",
        ],
    )?;
    let claims = require_non_empty_object_array(object, "claims")?;
    for (index, claim) in claims.iter().enumerate() {
        require_only_fields(claim, &["id", "claim", "evidence_refs"])
            .map_err(|error| format!("claims[{index}]: {error}"))?;
        require_non_empty_string(claim, "id")
            .map_err(|error| format!("claims[{index}]: {error}"))?;
        require_non_empty_string(claim, "claim")
            .map_err(|error| format!("claims[{index}]: {error}"))?;
        validate_evidence_refs(claim, index, "claims")?;
    }
    require_string_array(object, "unresolved_uncertainties")?;
    Ok(())
}

fn validate_reasoner(object: &serde_json::Map<String, Value>) -> Result<(), String> {
    require_only_fields(
        object,
        &[
            "schema_version",
            "role",
            "summary",
            "answer",
            "claims",
            "assumptions",
            "remaining_risks",
        ],
    )?;
    require_non_empty_string(object, "answer")?;
    let claims = require_non_empty_object_array(object, "claims")?;
    for (index, claim) in claims.iter().enumerate() {
        require_only_fields(claim, &["id", "claim", "evidence_refs"])
            .map_err(|error| format!("claims[{index}]: {error}"))?;
        require_non_empty_string(claim, "id")
            .map_err(|error| format!("claims[{index}]: {error}"))?;
        require_non_empty_string(claim, "claim")
            .map_err(|error| format!("claims[{index}]: {error}"))?;
        validate_evidence_refs(claim, index, "claims")?;
    }
    require_string_array(object, "assumptions")?;
    require_string_array(object, "remaining_risks")?;
    Ok(())
}

fn validate_coder(object: &serde_json::Map<String, Value>) -> Result<(), String> {
    require_only_fields(
        object,
        &[
            "schema_version",
            "role",
            "summary",
            "changed_paths",
            "verification",
            "remaining_risks",
        ],
    )?;
    require_string_array(object, "changed_paths")?;
    require_string_array(object, "remaining_risks")?;
    let verification = require_non_empty_object_array(object, "verification")?;
    for (index, check) in verification.iter().enumerate() {
        require_only_fields(check, &["command", "status", "evidence_refs"])
            .map_err(|error| format!("verification[{index}]: {error}"))?;
        require_non_empty_string(check, "command")
            .map_err(|error| format!("verification[{index}]: {error}"))?;
        let status = require_non_empty_string(check, "status")
            .map_err(|error| format!("verification[{index}]: {error}"))?;
        if !matches!(status, "passed" | "failed") {
            return Err(format!(
                "verification[{index}].status must be passed or failed"
            ));
        }
        validate_evidence_refs(check, index, "verification")?;
    }
    Ok(())
}

fn validate_critic(object: &serde_json::Map<String, Value>) -> Result<(), String> {
    require_only_fields(
        object,
        &[
            "schema_version",
            "role",
            "summary",
            "verdict",
            "checked_obligations",
            "evidence_refs",
            "counterexamples",
        ],
    )?;
    let verdict = validate_verdict(object)?;
    require_non_empty_string_array(object, "checked_obligations")?;
    validate_top_level_evidence_refs(object)?;
    let counterexamples = require_object_array(object, "counterexamples")?;
    for (index, counterexample) in counterexamples.iter().enumerate() {
        require_only_fields(
            counterexample,
            &[
                "claim_id",
                "failure_condition",
                "severity",
                "required_proof",
            ],
        )
        .map_err(|error| format!("counterexamples[{index}]: {error}"))?;
        for field in [
            "claim_id",
            "failure_condition",
            "severity",
            "required_proof",
        ] {
            require_non_empty_string(counterexample, field)
                .map_err(|error| format!("counterexamples[{index}]: {error}"))?;
        }
    }
    if verdict == "fail" && counterexamples.is_empty() {
        return Err("critic fail verdict requires at least one counterexample".to_string());
    }
    Ok(())
}

fn validate_verifier(object: &serde_json::Map<String, Value>) -> Result<(), String> {
    require_only_fields(
        object,
        &[
            "schema_version",
            "role",
            "summary",
            "verdict",
            "checked_obligations",
            "evidence_refs",
            "unmet_obligations",
        ],
    )?;
    let verdict = validate_verdict(object)?;
    require_non_empty_string_array(object, "checked_obligations")?;
    validate_top_level_evidence_refs(object)?;
    let unmet = require_string_array(object, "unmet_obligations")?;
    if verdict == "pass" && !unmet.is_empty() {
        return Err("verifier pass verdict cannot contain unmet obligations".to_string());
    }
    if verdict == "fail" && unmet.is_empty() {
        return Err("verifier fail verdict requires at least one unmet obligation".to_string());
    }
    Ok(())
}

fn validate_verdict(object: &serde_json::Map<String, Value>) -> Result<&str, String> {
    let verdict = require_non_empty_string(object, "verdict")?;
    if !matches!(verdict, "pass" | "fail") {
        return Err("verdict must be pass or fail".to_string());
    }
    Ok(verdict)
}

fn validate_top_level_evidence_refs(object: &serde_json::Map<String, Value>) -> Result<(), String> {
    let refs = require_non_empty_object_array(object, "evidence_refs")?;
    for (index, evidence) in refs.iter().enumerate() {
        validate_evidence_ref(evidence)
            .map_err(|error| format!("evidence_refs[{index}]: {error}"))?;
    }
    Ok(())
}

fn validate_evidence_refs(
    object: &serde_json::Map<String, Value>,
    parent_index: usize,
    parent_name: &str,
) -> Result<(), String> {
    let refs = require_non_empty_object_array(object, "evidence_refs")
        .map_err(|error| format!("{parent_name}[{parent_index}]: {error}"))?;
    for (index, evidence) in refs.iter().enumerate() {
        validate_evidence_ref(evidence).map_err(|error| {
            format!("{parent_name}[{parent_index}].evidence_refs[{index}]: {error}")
        })?;
    }
    Ok(())
}

fn validate_evidence_ref(object: &serde_json::Map<String, Value>) -> Result<(), String> {
    require_only_fields(object, &["kind", "source", "locator", "claim_id", "status"])?;
    require_non_empty_string(object, "kind")?;
    require_non_empty_string(object, "source")?;
    for optional in ["locator", "claim_id", "status"] {
        if object.contains_key(optional) {
            require_non_empty_string(object, optional)?;
        }
    }
    Ok(())
}

fn require_only_fields(
    object: &serde_json::Map<String, Value>,
    allowed: &[&str],
) -> Result<(), String> {
    if let Some(field) = object
        .keys()
        .find(|field| !allowed.contains(&field.as_str()))
    {
        return Err(format!("unexpected field {field}"));
    }
    Ok(())
}

fn require_exact_string(
    object: &serde_json::Map<String, Value>,
    field: &str,
    expected: &str,
) -> Result<(), String> {
    let value = require_non_empty_string(object, field)?;
    if value != expected {
        return Err(format!("{field} must be {expected}"));
    }
    Ok(())
}

fn require_non_empty_string<'a>(
    object: &'a serde_json::Map<String, Value>,
    field: &str,
) -> Result<&'a str, String> {
    object
        .get(field)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| format!("{field} must be a non-empty string"))
}

fn require_string_array<'a>(
    object: &'a serde_json::Map<String, Value>,
    field: &str,
) -> Result<&'a Vec<Value>, String> {
    let values = object
        .get(field)
        .and_then(Value::as_array)
        .ok_or_else(|| format!("{field} must be an array of strings"))?;
    if values
        .iter()
        .any(|value| value.as_str().is_none_or(|text| text.trim().is_empty()))
    {
        return Err(format!("{field} must contain only non-empty strings"));
    }
    Ok(values)
}

fn require_non_empty_string_array<'a>(
    object: &'a serde_json::Map<String, Value>,
    field: &str,
) -> Result<&'a Vec<Value>, String> {
    let values = require_string_array(object, field)?;
    if values.is_empty() {
        return Err(format!("{field} must contain at least one item"));
    }
    Ok(values)
}

fn require_object_array<'a>(
    object: &'a serde_json::Map<String, Value>,
    field: &str,
) -> Result<Vec<&'a serde_json::Map<String, Value>>, String> {
    object
        .get(field)
        .and_then(Value::as_array)
        .ok_or_else(|| format!("{field} must be an array of objects"))?
        .iter()
        .map(|value| {
            value
                .as_object()
                .ok_or_else(|| format!("{field} must contain only objects"))
        })
        .collect()
}

fn require_non_empty_object_array<'a>(
    object: &'a serde_json::Map<String, Value>,
    field: &str,
) -> Result<Vec<&'a serde_json::Map<String, Value>>, String> {
    let values = require_object_array(object, field)?;
    if values.is_empty() {
        return Err(format!("{field} must contain at least one item"));
    }
    Ok(values)
}

fn contract_description(contract: SpecialistOutputContract) -> &'static str {
    match contract {
        SpecialistOutputContract::Researcher => {
            r#"{"schema_version":"chotu.specialist_output.v1","role":"researcher","summary":"...","claims":[{"id":"...","claim":"...","evidence_refs":[{"kind":"...","source":"...","locator":"..."}]}],"unresolved_uncertainties":["..."]}"#
        }
        SpecialistOutputContract::Reasoner => {
            r#"{"schema_version":"chotu.specialist_output.v1","role":"reasoner","summary":"...","answer":"...","claims":[{"id":"...","claim":"...","evidence_refs":[{"kind":"...","source":"...","locator":"..."}]}],"assumptions":["..."],"remaining_risks":[]}"#
        }
        SpecialistOutputContract::Coder => {
            r#"{"schema_version":"chotu.specialist_output.v1","role":"coder","summary":"...","changed_paths":["..."],"verification":[{"command":"...","status":"passed","evidence_refs":[{"kind":"test_output","source":"...","locator":"..."}]}],"remaining_risks":[]}"#
        }
        SpecialistOutputContract::Critic => {
            r#"{"schema_version":"chotu.specialist_output.v1","role":"critic","summary":"...","verdict":"pass","checked_obligations":["..."],"evidence_refs":[{"kind":"...","source":"...","claim_id":"..."}],"counterexamples":[]}"#
        }
        SpecialistOutputContract::Verifier => {
            r#"{"schema_version":"chotu.specialist_output.v1","role":"verifier","summary":"...","verdict":"pass","checked_obligations":["..."],"evidence_refs":[{"kind":"...","source":"...","claim_id":"..."}],"unmet_obligations":[]}"#
        }
    }
}
