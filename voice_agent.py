"""AssemblyAI Voice Agent API integration.

The Voice Agent API runs the parts of a phone call that CallBridge does not
want to own: listening, turn detection, interruptions, and speaking in a
natural voice. It does NOT take over the negotiation. AssemblyAI lets an agent
use "your own LLM" -- any endpoint that answers POST {base_url}/chat/completions
in the OpenAI schema -- so CallBridge's own engine is plugged in as that model.

Every word the receptionist hears therefore still comes from engine.py, through
the same guardrails, ledger, and human approval gate as the typed flow.

Keys never reach the browser. The receptionist page receives a one-time,
short-lived token minted here instead.
"""
import os
import json
import time
import uuid

import requests

from ai_config import configured_key

# Fixed hosts. Hard-coding them keeps the AssemblyAI key from being sent
# somewhere else if an environment variable is ever mistyped.
AGENTS_API = "https://agents.assemblyai.com/v1"
AGENTS_WEBSOCKET = "wss://agents.assemblyai.com/v1/ws"

# The Voice Agent API speaks and listens in 16-bit PCM at this rate.
AUDIO_ENCODING = "audio/pcm"
AUDIO_SAMPLE_RATE = 24000

DEFAULT_VOICE = "eve"

# The closest AssemblyAI voice to each edge-tts option, so a call sounds the
# same whether it runs on the free voice or the live Voice Agent API.
MATCHING_AGENT_VOICE = {"female": "eve", "male": "michael"}
# Voice IDs published by AssemblyAI. The voice cannot be changed once a call
# has started, so an unknown value is rejected up front rather than mid-demo.
AVAILABLE_VOICES = {
    "alba", "eve", "george", "jane", "jean", "mary", "michael",
    "anna", "charles", "paul", "vera",
    "giovanni", "lola", "juergen", "rafael", "estelle",
}

# Spoken while the human decides. The receptionist hears a person thinking,
# not silence, and none of these lines commit CallBridge to anything.
HOLDING_PHRASES = [
    "Thanks for your patience, I am still confirming that.",
    "Sorry to keep you, I should have an answer in just a moment.",
]
FALLBACK_AFTER_TIMEOUT = (
    "I am still waiting on a confirmation here. Could I call you back shortly about this?"
)


def voice_key():
    return configured_key("ASSEMBLYAI_API_KEY")


def public_base_url():
    """Public HTTPS address AssemblyAI can reach this server on.

    AssemblyAI rejects http, localhost, and private hosts, so local runs need a
    tunnel (for example: cloudflared tunnel --url http://localhost:8000).
    """
    return os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")


def configured_voice():
    voice = os.getenv("VOICE_AGENT_VOICE", DEFAULT_VOICE).strip().lower() or DEFAULT_VOICE
    return voice if voice in AVAILABLE_VOICES else DEFAULT_VOICE


def voice_for(session):
    """The agent voice for one call, following the choice made in the app."""
    choice = str(session.get("voice_choice") or "").strip().lower()
    return MATCHING_AGENT_VOICE.get(choice) or configured_voice()


def approval_timeout():
    """How long the agent holds the line waiting for a human approval."""
    return max(15, min(600, int(os.getenv("VOICE_APPROVAL_TIMEOUT_SECONDS", "120"))))


_generated_bridge_token = uuid.uuid4().hex


def bridge_token():
    """Shared secret AssemblyAI must present when calling our model endpoint.

    Auto-generated per process when unset, so the endpoint is never unguarded
    just because an environment variable was forgotten.
    """
    return os.getenv("VOICE_BRIDGE_TOKEN", "").strip() or _generated_bridge_token


def unavailable_reason():
    """Plain-language explanation of what is missing, or '' when usable."""
    if not voice_key():
        return "Add ASSEMBLYAI_API_KEY to .env to place live voice calls."
    base = public_base_url()
    if not base:
        return ("Add PUBLIC_BASE_URL to .env. AssemblyAI calls this server back, so it needs a "
                "public https address (for example a cloudflared tunnel).")
    if not base.startswith("https://"):
        return "PUBLIC_BASE_URL must start with https:// — AssemblyAI rejects plain http and local addresses."
    return ""


def public_voice_settings():
    """Safe to return over the API: never includes a key or the bridge token."""
    reason = unavailable_reason()
    return {
        "voice_call_configured": not reason,
        "voice_call_hint": reason,
        "voice": configured_voice(),
        "sample_rate": AUDIO_SAMPLE_RATE,
    }


def _call_agents_api(method, path, payload=None, params=None, timeout=15):
    key = voice_key()
    if not key:
        raise ValueError("Add ASSEMBLYAI_API_KEY to .env to place live voice calls.")
    response = requests.request(
        method,
        f"{AGENTS_API}{path}",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload,
        params=params,
        timeout=timeout,
    )
    if response.status_code >= 400:
        # Surface AssemblyAI's own message; it names the offending field.
        raise ValueError(f"AssemblyAI Voice Agent API returned {response.status_code}: {response.text[:300]}")
    return response.json() if response.content else {}


def greeting_for(session):
    """Opening line. AssemblyAI speaks this straight to the voice on connect."""
    rules = session.get("rules") or {}
    business = rules.get("target_business") or "the business"
    return (rules.get("opening_phrase") or "").strip() or f"Hello, I am calling {business} on behalf of my client."


def agent_payload(session):
    """Agent definition for one CallBridge call.

    The system prompt is deliberately thin: this agent's 'model' is CallBridge,
    and CallBridge carries its own instructions, memory, and limits. The prompt
    only matters if the custom endpoint is ever unreachable.
    """
    session_id = session["session_id"]
    return {
        "name": f"CallBridge {session_id[:8]}",
        "system_prompt": (
            "You are CallBridge, calling a business on behalf of a client. Speak as the caller, "
            "never as the receptionist. Keep replies to one or two short spoken sentences. "
            "Never agree to a price, booking, cancellation, or share personal details on your own."
        ),
        "greeting": greeting_for(session),
        "voice": {"voice_id": voice_for(session)},
        "input": {
            "format": {"encoding": AUDIO_ENCODING},
            # Interruption handling stays on: a receptionist can talk over the
            # agent exactly as they would over a person.
            "turn_detection": {"interrupt_response": True},
        },
        "output": {"voice": voice_for(session), "format": {"encoding": AUDIO_ENCODING}},
        "llm": [{
            # AssemblyAI appends /chat/completions to this base URL.
            "base_url": f"{public_base_url()}/v1/voice/{session_id}",
            "model": "callbridge",
            "api_key": bridge_token(),
        }],
    }


def create_agent(session):
    """Registers this call's agent with AssemblyAI and returns its id."""
    reason = unavailable_reason()
    if reason:
        raise ValueError(reason)
    agent = _call_agents_api("POST", "/agents", agent_payload(session))
    agent_id = agent.get("id")
    if not agent_id:
        raise ValueError("AssemblyAI did not return an agent id.")
    return agent_id


def delete_agent(agent_id):
    """Best-effort cleanup; a stale agent costs nothing but should not linger."""
    if not agent_id or not voice_key():
        return
    try:
        _call_agents_api("DELETE", f"/agents/{agent_id}")
    except Exception:
        pass


def session_token(max_session_duration_seconds=1800):
    """One-time token so the receptionist's browser can open the socket.

    The AssemblyAI key stays on the server; this token is single use and
    expires quickly, and it caps how long a single call can run.
    """
    reason = unavailable_reason()
    if reason:
        raise ValueError(reason)
    result = _call_agents_api("GET", "/token", params={
        "expires_in_seconds": 120,
        "max_session_duration_seconds": max(60, min(10800, int(max_session_duration_seconds))),
    })
    if not result.get("token"):
        raise ValueError("AssemblyAI did not return a voice session token.")
    return result["token"]


# --- OpenAI-compatible streaming, as the Voice Agent API expects it ----------

def _chunk(content=None, role=None, finish_reason=None, completion_id="", model="callbridge"):
    delta = {}
    if role:
        delta["role"] = role
    if content is not None:
        delta["content"] = content
    return {
        "id": completion_id, "object": "chat.completion.chunk",
        "created": int(time.time()), "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def sse(payload):
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def speech_events(text, completion_id, model="callbridge", first=False):
    """Yields one sentence as OpenAI-style deltas.

    Words are sent individually because the agent starts speaking as soon as
    text arrives -- streaming is what keeps the reply from sounding delayed.
    Pass first=False for a sentence appended to a stream that already spoke,
    so its opening word does not run into the previous one.
    """
    if first:
        yield sse(_chunk(role="assistant", content="", completion_id=completion_id, model=model))
    for index, word in enumerate((text or "").split()):
        piece = word if (first and index == 0) else f" {word}"
        yield sse(_chunk(content=piece, completion_id=completion_id, model=model))


def finish_events(completion_id, model="callbridge"):
    yield sse(_chunk(finish_reason="stop", completion_id=completion_id, model=model))
    yield "data: [DONE]\n\n"


def whole_completion(text, completion_id, model="callbridge"):
    """Non-streaming reply, used only by tests and manual endpoint checks."""
    return {
        "id": completion_id, "object": "chat.completion", "created": int(time.time()), "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def last_caller_text(messages):
    """The receptionist's most recent words, as transcribed by AssemblyAI."""
    for message in reversed(messages or []):
        if message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, list):
                # Some clients send content as typed parts rather than a string.
                content = " ".join(part.get("text", "") for part in content if isinstance(part, dict))
            if isinstance(content, str) and content.strip():
                return content.strip()
    return ""
