mod tool_test_utils;

use kimi_agent::config::{LLMModel, LLMProvider, ProviderType};
use tool_test_utils::RuntimeFixture;

#[tokio::test]
async fn fixed_subagent_can_bind_a_different_configured_model() {
    let mut runtime = RuntimeFixture::new().runtime;
    runtime.config.providers.insert(
        "specialist_provider".to_string(),
        LLMProvider {
            provider_type: ProviderType::Echo,
            base_url: String::new(),
            api_key: String::new(),
            prompt_cache: None,
            stream: None,
            env: None,
            custom_headers: None,
        },
    );
    runtime.config.models.insert(
        "chotu_verifier".to_string(),
        LLMModel {
            provider: "specialist_provider".to_string(),
            model: "verifier-model".to_string(),
            max_context_size: 32_000,
            capabilities: None,
        },
    );

    let specialist = runtime
        .copy_for_fixed_subagent_model(Some("chotu_verifier"))
        .await
        .expect("specialist runtime");

    let specialist_llm = specialist.llm.as_ref().expect("specialist llm");
    assert_eq!(
        specialist_llm
            .model_config
            .as_ref()
            .expect("specialist model config")
            .model,
        "verifier-model"
    );
    assert_eq!(specialist_llm.max_context_size, 32_000);
    assert!(std::sync::Arc::ptr_eq(
        &specialist.council_budget,
        &runtime.council_budget
    ));
}

#[tokio::test]
async fn unknown_subagent_model_fails_loudly() {
    let runtime = RuntimeFixture::new().runtime;

    let error = match runtime
        .copy_for_fixed_subagent_model(Some("missing_model"))
        .await
    {
        Ok(_) => panic!("missing model must fail"),
        Err(error) => error,
    };

    assert!(
        error
            .to_string()
            .contains("Subagent model is not configured")
    );
}
