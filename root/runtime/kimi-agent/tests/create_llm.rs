use std::collections::HashSet;
use std::sync::Mutex;

use serde_json::json;

use kimi_agent::config::{LLMModel, LLMProvider, ModelCapability, ProviderType};
use kimi_agent::llm::{
    augment_provider_with_env_vars, create_llm, create_llm_with_max_tokens,
    derive_model_capabilities,
};
use kosong::chat_provider::echo::echo::EchoChatProvider;
use kosong::chat_provider::kimi::Kimi;

static ENV_LOCK: Mutex<()> = Mutex::new(());

#[test]
fn test_explicit_capabilities_override_model_name_inference() {
    let model = LLMModel {
        provider: "kimi".to_string(),
        model: "sankhya-thinking".to_string(),
        max_context_size: 4096,
        capabilities: Some(HashSet::new()),
    };

    assert!(derive_model_capabilities(&model).is_empty());
}

struct EnvGuard {
    key: &'static str,
    prev: Option<String>,
}

impl EnvGuard {
    fn set(key: &'static str, value: &str) -> Self {
        let prev = std::env::var(key).ok();
        // SAFETY: tests serialize env access via ENV_LOCK to avoid races.
        unsafe {
            std::env::set_var(key, value);
        }
        Self { key, prev }
    }
}

impl Drop for EnvGuard {
    fn drop(&mut self) {
        if let Some(prev) = &self.prev {
            // SAFETY: tests serialize env access via ENV_LOCK to avoid races.
            unsafe {
                std::env::set_var(self.key, prev);
            }
        } else {
            // SAFETY: tests serialize env access via ENV_LOCK to avoid races.
            unsafe {
                std::env::remove_var(self.key);
            }
        }
    }
}

#[test]
fn test_augment_provider_with_env_vars_kimi() {
    let _lock = ENV_LOCK.lock().unwrap();
    let _guards = vec![
        EnvGuard::set("KIMI_BASE_URL", "https://env.test/v1"),
        EnvGuard::set("KIMI_API_KEY", "env-key"),
        EnvGuard::set("KIMI_MODEL_NAME", "kimi-env-model"),
        EnvGuard::set("KIMI_MODEL_MAX_CONTEXT_SIZE", "8192"),
        EnvGuard::set("KIMI_MODEL_CAPABILITIES", "Image_In,THINKING,unknown"),
    ];

    let mut provider = LLMProvider {
        provider_type: ProviderType::Kimi,
        base_url: "https://original.test/v1".to_string(),
        api_key: "orig-key".to_string(),
        prompt_cache: None,
        stream: None,
        env: None,
        custom_headers: None,
    };
    let mut model = LLMModel {
        provider: "kimi".to_string(),
        model: "kimi-base".to_string(),
        max_context_size: 4096,
        capabilities: None,
    };

    augment_provider_with_env_vars(&mut provider, &mut model).expect("env overrides");

    assert_eq!(
        provider,
        LLMProvider {
            provider_type: ProviderType::Kimi,
            base_url: "https://env.test/v1".to_string(),
            api_key: "env-key".to_string(),
            prompt_cache: None,
            stream: None,
            env: None,
            custom_headers: None,
        }
    );
    assert_eq!(
        model,
        LLMModel {
            provider: "kimi".to_string(),
            model: "kimi-env-model".to_string(),
            max_context_size: 8192,
            capabilities: Some(HashSet::from([
                ModelCapability::ImageIn,
                ModelCapability::Thinking,
            ])),
        }
    );
}

#[test]
fn test_augment_provider_with_env_vars_invalid_max_context_size() {
    let _lock = ENV_LOCK.lock().unwrap();
    let _guard = EnvGuard::set("KIMI_MODEL_MAX_CONTEXT_SIZE", "not-a-number");

    let mut provider = LLMProvider {
        provider_type: ProviderType::Kimi,
        base_url: "https://original.test/v1".to_string(),
        api_key: "orig-key".to_string(),
        prompt_cache: None,
        stream: None,
        env: None,
        custom_headers: None,
    };
    let mut model = LLMModel {
        provider: "kimi".to_string(),
        model: "kimi-base".to_string(),
        max_context_size: 4096,
        capabilities: None,
    };

    let err = augment_provider_with_env_vars(&mut provider, &mut model)
        .expect_err("invalid max context size");
    assert!(
        err.to_string()
            .contains("invalid literal for int() with base 10")
    );
}

#[tokio::test]
async fn test_create_llm_kimi_model_parameters() {
    let _lock = ENV_LOCK.lock().unwrap();
    let _guards = vec![
        EnvGuard::set("KIMI_MODEL_TEMPERATURE", "0.2"),
        EnvGuard::set("KIMI_MODEL_TOP_P", "0.8"),
        EnvGuard::set("KIMI_MODEL_MAX_TOKENS", "1234"),
    ];

    let provider = LLMProvider {
        provider_type: ProviderType::Kimi,
        base_url: "https://api.test/v1".to_string(),
        api_key: "test-key".to_string(),
        prompt_cache: None,
        stream: None,
        env: None,
        custom_headers: None,
    };
    let model = LLMModel {
        provider: "kimi".to_string(),
        model: "kimi-base".to_string(),
        max_context_size: 4096,
        capabilities: None,
    };

    let llm = create_llm(&provider, &model, None, None)
        .await
        .expect("create llm")
        .expect("llm");

    let kimi = llm
        .chat_provider
        .as_any()
        .downcast_ref::<Kimi>()
        .expect("kimi provider");

    assert_eq!(
        serde_json::Value::Object(kimi.model_parameters()),
        json!({
            "base_url": "https://api.test/v1/",
            "temperature": 0.2,
            "top_p": 0.8,
            "max_tokens": 1234
        })
    );
}

#[tokio::test]
async fn test_create_llm_honors_non_streaming_provider_transport() {
    // Reads the model env vars, which other tests set: hold the lock too.
    let _lock = ENV_LOCK.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    let provider = LLMProvider {
        provider_type: ProviderType::Kimi,
        base_url: "https://api.test/v1".to_string(),
        api_key: "test-key".to_string(),
        prompt_cache: Some(false),
        stream: Some(false),
        env: None,
        custom_headers: None,
    };
    let model = LLMModel {
        provider: "nvidia_nim".to_string(),
        model: "deepseek-ai/deepseek-v4-flash".to_string(),
        max_context_size: 262_144,
        capabilities: Some(HashSet::new()),
    };

    let llm = create_llm(&provider, &model, None, None)
        .await
        .expect("create llm")
        .expect("llm");
    let kimi = llm
        .chat_provider
        .as_any()
        .downcast_ref::<Kimi>()
        .expect("kimi-compatible provider");

    assert!(!kimi.is_streaming());
}

#[tokio::test]
async fn explicit_subagent_budget_overrides_main_model_environment() {
    let _lock = ENV_LOCK.lock().unwrap();
    let _guard = EnvGuard::set("KIMI_MODEL_MAX_TOKENS", "24576");
    let provider = LLMProvider {
        provider_type: ProviderType::Kimi,
        base_url: "https://api.test/v1".to_string(),
        api_key: "test-key".to_string(),
        prompt_cache: Some(true),
        stream: None,
        env: None,
        custom_headers: None,
    };
    let model = LLMModel {
        provider: "kimi".to_string(),
        model: "kimi-base".to_string(),
        max_context_size: 4096,
        capabilities: None,
    };

    let llm =
        create_llm_with_max_tokens(&provider, &model, None, Some("advisor-session"), Some(1536))
            .await
            .expect("create bounded subagent llm")
            .expect("llm");
    let kimi = llm
        .chat_provider
        .as_any()
        .downcast_ref::<Kimi>()
        .expect("kimi provider");

    assert_eq!(
        kimi.model_parameters().get("max_tokens"),
        Some(&json!(1536))
    );
    assert_eq!(
        kimi.model_parameters().get("prompt_cache_key"),
        Some(&json!("advisor-session"))
    );

    let error = match create_llm_with_max_tokens(&provider, &model, None, None, Some(0)).await {
        Err(error) => error,
        Ok(_) => panic!("zero subagent output budget must fail"),
    };
    assert!(error.to_string().contains("must be greater than zero"));
}

#[tokio::test]
async fn test_create_llm_preserves_explicit_xhigh_reasoning_effort() {
    let _lock = ENV_LOCK.lock().unwrap();
    let _guard = EnvGuard::set("KIMI_MODEL_REASONING_EFFORT", "xhigh");

    let provider = LLMProvider {
        provider_type: ProviderType::Kimi,
        base_url: "https://api.test/v1".to_string(),
        api_key: "test-key".to_string(),
        prompt_cache: Some(false),
        stream: None,
        env: None,
        custom_headers: None,
    };
    let model = LLMModel {
        provider: "sankhya".to_string(),
        model: "sankhya-deep".to_string(),
        max_context_size: 4096,
        capabilities: None,
    };

    let llm = create_llm(&provider, &model, Some(false), None)
        .await
        .expect("create llm")
        .expect("llm");
    let kimi = llm
        .chat_provider
        .as_any()
        .downcast_ref::<Kimi>()
        .expect("kimi provider");

    assert_eq!(
        kimi.model_parameters().get("reasoning_effort"),
        Some(&json!("xhigh"))
    );
}

#[tokio::test]
async fn test_create_llm_does_not_emit_thinking_off_for_plain_kimi_model() {
    let _lock = ENV_LOCK.lock().unwrap();

    let provider = LLMProvider {
        provider_type: ProviderType::Kimi,
        base_url: "https://api.test/v1".to_string(),
        api_key: "test-key".to_string(),
        prompt_cache: None,
        stream: None,
        env: None,
        custom_headers: None,
    };
    let model = LLMModel {
        provider: "kimi".to_string(),
        model: "moonshotai/kimi-k2.6".to_string(),
        max_context_size: 4096,
        capabilities: None,
    };

    let llm = create_llm(&provider, &model, Some(false), None)
        .await
        .expect("create llm")
        .expect("llm");

    let kimi = llm
        .chat_provider
        .as_any()
        .downcast_ref::<Kimi>()
        .expect("kimi provider");
    let params = kimi.model_parameters();

    assert!(!params.contains_key("reasoning_effort"));
    assert!(!params.contains_key("extra_body"));
}

#[tokio::test]
async fn test_create_llm_omits_prompt_cache_key_when_provider_disables_prompt_cache() {
    let _lock = ENV_LOCK.lock().unwrap();

    let provider = LLMProvider {
        provider_type: ProviderType::Kimi,
        base_url: "https://api.test/v1".to_string(),
        api_key: "test-key".to_string(),
        prompt_cache: Some(false),
        stream: None,
        env: None,
        custom_headers: None,
    };
    let model = LLMModel {
        provider: "kimi".to_string(),
        model: "moonshotai/kimi-k2.6".to_string(),
        max_context_size: 4096,
        capabilities: None,
    };

    let llm = create_llm(&provider, &model, Some(false), Some("session-1"))
        .await
        .expect("create llm")
        .expect("llm");

    let kimi = llm
        .chat_provider
        .as_any()
        .downcast_ref::<Kimi>()
        .expect("kimi provider");
    let params = kimi.model_parameters();

    assert!(!params.contains_key("prompt_cache_key"));
}

#[tokio::test]
async fn test_create_llm_emits_thinking_off_for_declared_thinking_model() {
    let _lock = ENV_LOCK.lock().unwrap();

    let provider = LLMProvider {
        provider_type: ProviderType::Kimi,
        base_url: "https://api.test/v1".to_string(),
        api_key: "test-key".to_string(),
        prompt_cache: None,
        stream: None,
        env: None,
        custom_headers: None,
    };
    let model = LLMModel {
        provider: "kimi".to_string(),
        model: "kimi-k2.6".to_string(),
        max_context_size: 4096,
        capabilities: Some(HashSet::from([ModelCapability::Thinking])),
    };

    let llm = create_llm(&provider, &model, Some(false), None)
        .await
        .expect("create llm")
        .expect("llm");

    let kimi = llm
        .chat_provider
        .as_any()
        .downcast_ref::<Kimi>()
        .expect("kimi provider");

    assert_eq!(
        serde_json::Value::Object(kimi.model_parameters()),
        json!({
            "base_url": "https://api.test/v1/",
            "reasoning_effort": null,
            "extra_body": {"thinking": {"type": "disabled"}}
        })
    );
}

#[tokio::test]
async fn test_create_llm_invalid_temperature_env() {
    let _lock = ENV_LOCK.lock().unwrap();
    let _guard = EnvGuard::set("KIMI_MODEL_TEMPERATURE", "not-a-number");

    let provider = LLMProvider {
        provider_type: ProviderType::Kimi,
        base_url: "https://api.test/v1".to_string(),
        api_key: "test-key".to_string(),
        prompt_cache: None,
        stream: None,
        env: None,
        custom_headers: None,
    };
    let model = LLMModel {
        provider: "kimi".to_string(),
        model: "kimi-base".to_string(),
        max_context_size: 4096,
        capabilities: None,
    };

    let err = match create_llm(&provider, &model, None, None).await {
        Ok(_) => panic!("expected temperature parsing error"),
        Err(err) => err,
    };
    assert!(
        err.to_string()
            .contains("could not convert string to float")
    );
}

#[tokio::test]
async fn test_create_llm_echo_provider() {
    let provider = LLMProvider {
        provider_type: ProviderType::Echo,
        base_url: "".to_string(),
        api_key: "".to_string(),
        prompt_cache: None,
        stream: None,
        env: None,
        custom_headers: None,
    };
    let model = LLMModel {
        provider: "_echo".to_string(),
        model: "echo".to_string(),
        max_context_size: 1234,
        capabilities: None,
    };

    let llm = create_llm(&provider, &model, None, None)
        .await
        .expect("create llm")
        .expect("llm");

    assert!(llm.chat_provider.as_any().is::<EchoChatProvider>());
    assert_eq!(llm.max_context_size, 1234);
}

#[tokio::test]
async fn test_create_llm_requires_base_url_for_kimi() {
    let provider = LLMProvider {
        provider_type: ProviderType::Kimi,
        base_url: "".to_string(),
        api_key: "test-key".to_string(),
        prompt_cache: None,
        stream: None,
        env: None,
        custom_headers: None,
    };
    let model = LLMModel {
        provider: "kimi".to_string(),
        model: "kimi-base".to_string(),
        max_context_size: 4096,
        capabilities: None,
    };

    let llm = create_llm(&provider, &model, None, None)
        .await
        .expect("create llm");
    assert!(llm.is_none());
}
