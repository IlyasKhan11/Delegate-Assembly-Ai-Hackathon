"""Free neural speech for the agent's replies, via edge-tts.

edge-tts uses the same Microsoft neural voices that Azure charges for, but it
needs no API key and costs nothing. That makes it the sensible default for
CallBridge: the browser's own speech sounds robotic, and the AssemblyAI Voice
Agent API (voice_agent.py) is worth its hourly rate only for a real, live,
interruptible phone call.

Nothing here decides what is said. Callers must pass text that is already in
the session transcript, so this cannot widen what the agent is allowed to say.
"""
import asyncio
import os
from collections import OrderedDict

import edge_tts

DEFAULT_VOICE = "en-US-AvaNeural"

# The two voices the app offers as a button. TTS_VOICE can name any other
# edge-tts voice; these are simply the two that are one click away.
SELECTABLE_VOICES = {
    "female": "en-US-AvaNeural",
    "male": "en-US-AndrewNeural",
}

# Synthesis takes a second or two, so finished audio is kept for replays and
# for the common case of pre-generating a draft before it is approved.
CACHE_LIMIT = 64
# The page gives up after 12 seconds, so the server must fail before that.
SYNTHESIS_TIMEOUT_SECONDS = 8
_cache = OrderedDict()
_in_flight = {}


def configured_voice():
    """Voice for the agent's replies. Any edge-tts voice name is accepted.

    Run `python -m edge_tts --list-voices` to see all 300+ of them.
    This is the fallback when no voice has been chosen for a call.
    Natural-sounding English options: en-US-AvaNeural, en-US-AndrewNeural,
    en-US-BrianNeural, en-US-EmmaNeural, en-GB-RyanNeural, en-GB-SoniaNeural.
    """
    return os.getenv("TTS_VOICE", "").strip() or DEFAULT_VOICE


def resolve_voice(choice):
    """Turns a stored 'female' or 'male' choice into an actual voice name.

    Anything unrecognised falls back to the configured default, so a stale or
    hand-edited session can never leave the agent without a voice.
    """
    return SELECTABLE_VOICES.get(str(choice or "").strip().lower()) or configured_voice()


def public_tts_settings(choice=None):
    return {"tts_configured": True,
            "tts_voice": resolve_voice(choice),
            "tts_voice_choice": str(choice or "female").strip().lower(),
            "tts_voice_choices": sorted(SELECTABLE_VOICES)}


def _remember(key, audio):
    _cache[key] = audio
    _cache.move_to_end(key)
    while len(_cache) > CACHE_LIMIT:
        _cache.popitem(last=False)


async def _render(text, voice):
    audio = b""
    async for chunk in edge_tts.Communicate(text, voice).stream():
        if chunk["type"] == "audio":
            audio += chunk["data"]
    if not audio:
        raise ValueError("The speech service returned no audio.")
    return audio


async def _render_within_time_limit(text, voice):
    # Without a limit, a host that silently blocks outbound connections leaves
    # the request hanging until the host's proxy gives up with an unexplained
    # 503. Failing first means the log says what actually went wrong.
    try:
        return await asyncio.wait_for(_render(text, voice), SYNTHESIS_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        raise TimeoutError(
            f"The speech service did not answer within {SYNTHESIS_TIMEOUT_SECONDS} seconds. "
            "Check that this server can reach speech.platform.bing.com.") from None


async def synthesize(text, voice=None):
    """Returns MP3 bytes for one sentence, reusing earlier work where possible.

    Concurrent requests for the same sentence share a single synthesis rather
    than racing each other, which matters because the page often asks for a
    draft's audio at the same moment the approval does.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("There is nothing to speak.")
    chosen_voice = voice or configured_voice()
    key = (chosen_voice, text)
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    if key not in _in_flight:
        _in_flight[key] = asyncio.create_task(_render_within_time_limit(text, chosen_voice))
    try:
        audio = await asyncio.shield(_in_flight[key])
    finally:
        # Whoever finishes last clears the slot, so a failed attempt is retryable.
        if key in _in_flight and _in_flight[key].done():
            _in_flight.pop(key, None)
    _remember(key, audio)
    return audio
