"""Live speech-to-text through AssemblyAI's streaming API.

This is the "just talk" mode. The existing microphone button records a clip,
uploads it, and waits; this listens continuously and produces a sentence the
moment the speaker finishes it.

Unlike the Voice Agent API (voice_agent.py) this needs no public address and no
tunnel: the browser opens the socket to AssemblyAI directly. Only the
short-lived token is minted here, so the API key never reaches the page.
"""
import os

import requests

from ai_config import configured_key

STREAMING_TOKEN_URL = "https://streaming.assemblyai.com/v3/token"
STREAMING_WEBSOCKET = "wss://streaming.assemblyai.com/v3/ws"

# The streaming API listens to 16-bit PCM at this rate.
SAMPLE_RATE = 16000

# universal-3-5-pro is the most accurate for English, which is what the agent
# and the business speak. Set STREAMING_SPEECH_MODEL to
# universal-streaming-multilingual if the other side speaks another language.
DEFAULT_SPEECH_MODEL = "universal-3-5-pro"


def speech_model():
    return os.getenv("STREAMING_SPEECH_MODEL", "").strip() or DEFAULT_SPEECH_MODEL


def unavailable_reason():
    """Plain-language explanation of what is missing, or '' when usable."""
    if not configured_key("ASSEMBLYAI_API_KEY"):
        return "Add ASSEMBLYAI_API_KEY to .env to listen to live speech."
    return ""


def public_listening_settings():
    """Safe to return over the API: never includes the key."""
    reason = unavailable_reason()
    return {
        "live_listening_configured": not reason,
        "live_listening_hint": reason,
        "listen_sample_rate": SAMPLE_RATE,
        "listen_model": speech_model(),
    }


def listening_token(expires_in_seconds=600):
    """A short-lived token so a browser can open the streaming socket itself.

    The key stays on the server. The token expires quickly and is scoped to
    streaming only.
    """
    reason = unavailable_reason()
    if reason:
        raise ValueError(reason)
    response = requests.get(
        STREAMING_TOKEN_URL,
        # The streaming API takes the key raw, with no "Bearer" prefix.
        headers={"Authorization": configured_key("ASSEMBLYAI_API_KEY")},
        params={"expires_in_seconds": max(60, min(3600, int(expires_in_seconds)))},
        timeout=15,
    )
    if response.status_code >= 400:
        raise ValueError(f"AssemblyAI refused the listening token ({response.status_code}): {response.text[:200]}")
    token = response.json().get("token")
    if not token:
        raise ValueError("AssemblyAI did not return a listening token.")
    return token


def websocket_url():
    """Everything the browser needs except the token, which it appends."""
    return (f"{STREAMING_WEBSOCKET}?sample_rate={SAMPLE_RATE}"
            f"&speech_model={speech_model()}&format_turns=true")
