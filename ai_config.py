"""Backend-only provider settings. Never expose keys in API responses or logs."""
import os
from dotenv import load_dotenv

load_dotenv()


def configured_key(name):
    value = os.getenv(name, "").strip()
    if not value or value.lower().startswith(("your_", "<your", "not-configured")):
        return ""
    return value


def get_provider():
    provider = os.getenv("AI_PROVIDER", "").strip().lower()
    if not provider:
        provider = "aimlapi" if configured_key("AIML_API_KEY") or not configured_key("GROQ_API_KEY") else "groq"
    if provider not in {"aimlapi", "groq"}:
        raise ValueError("AI_PROVIDER must be aimlapi or groq.")
    return provider


def provider_key():
    return configured_key("AIML_API_KEY" if get_provider() == "aimlapi" else "GROQ_API_KEY")


def provider_url():
    # Fixed URLs prevent accidentally sending credentials to another service.
    return "https://api.aimlapi.com/v1" if get_provider() == "aimlapi" else "https://api.groq.com/openai/v1"


def get_model():
    if get_provider() == "aimlapi":
        return os.getenv("AIML_MODEL", "openai/gpt-4o-mini").strip() or "openai/gpt-4o-mini"
    return os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip() or "openai/gpt-oss-20b"


def output_limit():
    return max(128, min(1000, int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "700"))))


def input_limit():
    return max(4000, min(40000, int(os.getenv("LLM_MAX_INPUT_CHARS", "24000"))))


def public_ai_settings():
    return {"ai_provider": get_provider(), "ai_model": get_model(), "ai_configured": bool(provider_key()),
            "max_output_tokens": output_limit(),
            "audio_configured": bool(configured_key("ASSEMBLYAI_API_KEY") or configured_key("GROQ_API_KEY"))}
