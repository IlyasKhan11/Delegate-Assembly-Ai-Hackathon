# Delegate / CallBridge

**Start here:** [Test with your friend and share the app](PILOT_GUIDE.md). The app now includes guided setup, private-pilot access, expiring/revocable invitations, and saved-conversation deletion. Docker deployment and CI checks are included. This is a private browser pilot, not a public multi-tenant release.

## Delegate frontend

The repository includes **Delegate**, a browser conversation workspace with general business/task setup, five user languages, participant invitations, bilingual reply approval, and saved conversation summaries.

```bash
python -m uvicorn app:app --reload --port 8000
```

Open **http://localhost:8000/app**. Start with your own task or choose **Explore a sample conversation**, which uses no API credits. Browser conversations connect your task using `AIML_API_KEY` and the low-cost GPT-4o mini model. Microphone transcription needs a separate AssemblyAI or Groq key; typed responses work with AI/ML alone. A real teammate can reply through the separate participant browser screen; it does **not** dial real phone numbers.

See [frontend/README.md](frontend/README.md) for usage, validation commands, and limitations.

> **Autonomous Outbound Negotiation & Phone Agent with Human-in-the-Loop Safeguards**

Delegate helps a user negotiate with a human business representative through a browser conversation. It translates replies, checks constraints, and asks the user to approve commitments. The current transport is a two-person browser room with typed or dictated turns and synthesized speech. Phone-number dialing remains a future integration.

---

## 🌟 Key Features

* **Natural Goal Parsing**: Takes natural language instructions (e.g., *"Book a dental checkup for Tuesday before 4 PM, max $30, never share credit card"*) and extracts structured limits and opening statements.
* **Human receptionist room**: Shares approved conversation with a separate participant screen and receives real human replies.
* **Dealbreaker Interception**: Immediately catches constraint violations and forbidden disclosures.
* **Natural Agent Voice (free)**: Replies are read aloud in a Microsoft neural voice via `edge-tts` — no API key, no credits, 322 voices. Pick **Female** or **Male** in the app, or any other voice with `TTS_VOICE`. The browser's own robotic speech remains only as a fallback.
* **Live Voice Calls**: Runs a real spoken call through the AssemblyAI Voice Agent API. AssemblyAI listens, detects turns, handles interruptions, and speaks in a natural voice, while CallBridge's own engine remains the agent's brain and the approval gate is untouched. See *Live voice calls* below.
* **Zero-Drop Stalling Phrase**: Speaks an immediate natural stalling line (*"Hold on a moment while I confirm that with my client..."*) to keep the receptionist waiting.
* **Human-in-the-Loop Escalation**: Provides smart 1-click counter-options and custom instruction input.
* **Bilingual Staging**: Generates verified English audio to speak while previewing translations in the client's preferred language (e.g. Urdu, Spanish).
* **Dynamic Negotiation Memory**: Tracks approved client overrides throughout the conversation so agreed terms are recognized as successes.
* **Post-Call Audit & Summary Report**: Produces a structured report card with confirmed points, unresolved items, and outcome headlines.

---

## 🚀 Quickstart

### 1. Installation

Clone the repository and install dependencies:
```bash
git clone https://github.com/IlyasKhan11/Delegate-Assembly-Ai-Hackathon.git
cd Delegate-Assembly-Ai-Hackathon
pip install -r requirements.txt # or install fastapi, uvicorn, openai, assemblyai, edge-tts, sounddevice, scipy
```

### 2. Configure Environment Variables

Create a `.env` file based on `.env.example`:
```ini
AIML_API_KEY=your_aiml_api_key
AI_PROVIDER=aimlapi
AIML_MODEL=openai/gpt-4o-mini
LLM_MAX_OUTPUT_TOKENS=700
ASSEMBLYAI_API_KEY=your_assemblyai_api_key
```

### 3. Start the Backend Server

```bash
uvicorn app:app --reload --port 8000
```
Interactive Swagger API documentation will be available at `http://127.0.0.1:8000/docs`.

### 4. Run the Interactive Roleplay Test Client

Simulate the business receptionist and talk to CallBridge in real-time using your PC microphone and speakers:
```bash
python test_mic_caller.py
```

---

## 🔊 How the agent sounds

There are three tiers, and the app picks the best one available:

| Tier | Cost | Needs | Use it for |
|---|---|---|---|
| **edge-tts neural voice** (default) | Free | Nothing | Normal typed conversations. Sounds natural. |
| **AssemblyAI Voice Agent** | $4.50/hour | Key + public https tunnel | A real spoken call with interruptions and turn-taking. |
| Browser `speechSynthesis` | Free | Nothing | Fallback only. Sounds robotic — this is what you hear if the other two are unavailable. |

The **Guided demo** uses the same free neural voice as a real call, so what you rehearse
is what the judges hear. It still spends no API credits. If the backend is unreachable,
it falls back to the browser voice rather than going silent.

### Choosing the voice

The call setup screen has an **Agent voice** dropdown with two options:

| Option | Voice | Live-call equivalent |
|---|---|---|
| Female (default) | `en-US-AvaNeural` | `eve` |
| Male | `en-US-AndrewNeural` | `michael` |

The choice is stored on the call, not in your browser, so the business
participant hears whichever voice you picked — and a live AssemblyAI call uses the
matching voice, so the agent does not change identity between the two tiers.

To use one of the other 320 voices as the default instead:

```bash
python preview_voices.py                 # hear the shortlist
python preview_voices.py --all-english   # hear every English voice
```

Put your pick in `.env` as `TTS_VOICE`, e.g. `TTS_VOICE=en-GB-SoniaNeural`, then restart.
`python -m edge_tts --list-voices` lists all 322 across 74 languages.

Generating a line takes 1–2 seconds, so the app starts generating a reply's audio **while
you are still reading it**. By the time you press Approve it plays instantly. Audio is
cached, so replays are immediate.

The speech routes will only read out words **already in the conversation**, so the voice
can never be used to make the agent say something you did not approve.

---

## 🎧 Live listening — the other side just talks

The business participant can press **Start talking** and simply speak. AssemblyAI's
streaming API transcribes continuously, and each sentence is sent the moment they pause —
no pressing record, no waiting for an upload. The presenter has the same button
(**Listen live**) for holding the call on their own microphone.

Every spoken sentence goes through the identical path as a typed one: the same spend
ceiling, the same personal-data rules, the same approval gate. It also arrives in the
transcript **translated into the user's chosen language**, so a Portuguese speaker reads
what the business said in Portuguese.

This needs only `ASSEMBLYAI_API_KEY` — **no tunnel and no public address**, because the
browser opens the socket to AssemblyAI directly and only the short-lived token comes from
this server. `GET /api/health` reports `live_listening_configured`.

Set `STREAMING_SPEECH_MODEL=universal-streaming-multilingual` in `.env` if the other side
speaks a language other than English.

---

## 🎙️ Live voice calls (AssemblyAI Voice Agent API)

By default approved replies use the server’s neural speech service, with browser speech as a fallback. Turning on a live voice call replaces that with AssemblyAI: the business
participant simply talks, and the agent answers out loud in a natural voice.

**Nothing about who is in control changes.** AssemblyAI runs the ears and the mouth only.
It is configured to use *your own LLM*, and that LLM is CallBridge — so every word still
comes from `engine.py`, through the same guardrails, ledger, and approval gate.

**When a reply needs your approval**, the agent immediately speaks its stalling phrase and
then holds the line open. The receptionist hears a person thinking, not dead air. The
moment you press Approve, those exact words stream out on that same open connection.
Nothing you have not approved is ever spoken.

### Setup

1. Add `ASSEMBLYAI_API_KEY` to `.env`. A new account includes $50 of free credit, which is
   about 11 hours of call time at $4.50/hour.
2. AssemblyAI calls this server back, so it needs a public `https` address. Plain `http`,
   `localhost`, and private addresses are rejected. For local development:
   ```bash
   cloudflared tunnel --url http://localhost:8000
   ```
   Put the address it prints into `PUBLIC_BASE_URL` in `.env`, then restart the server.
3. Check that it took: `curl localhost:8000/api/health` reports `voice_call_configured`.
   If it is `false`, `voice_call_hint` says exactly what is missing.

### Running a call

1. Start a call as usual, then press **Live voice call** in the call bar.
2. Open the participant link and press **Start live voice call** there, and allow the
   microphone. The agent speaks its opening line and the conversation begins.
3. Approve and reject exactly as before. The controller's transcript records every spoken
   line, including the holding phrases.

The voice is set by `VOICE_AGENT_VOICE` and **cannot be changed once a call has started**.
US English: `alba` `eve` `george` `jane` `jean` `mary` `michael`. UK English: `anna`
`charles` `paul` `vera`. Other languages: `giovanni` (IT), `lola` (ES), `juergen` (DE),
`rafael` (PT), `estelle` (FR). Spoken output covers 6 languages; speech recognition
covers 18, so a language CallBridge can translate is not always one it can speak.

---

## 🧪 Testing

[TESTING_GUIDE.md](TESTING_GUIDE.md) is a step-by-step script for someone testing the app,
including three realistic customer-support scenarios in Portuguese and a checklist of what
should and should not happen. The scripted lines in it are covered by
`tests/test_currency.py`, so the guide and the code cannot drift apart.

```bash
./venv/bin/python -m unittest discover -s tests   # backend
npm test                                          # frontend
```

---

## 📚 Documentation for Frontend Developers

See [FRONTEND_INTEGRATION_GUIDE.md](FRONTEND_INTEGRATION_GUIDE.md) and [API_CONTRACT.md](API_CONTRACT.md) for full REST & WebSocket endpoint specifications, state machine transitions, and React/Next.js integration blueprints.

---

## 📄 License
MIT License
