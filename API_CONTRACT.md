# CallBridge Backend API & WebSocket Specification

## Delegate frontend integration update

The app now serves the frontend at `/` and `/demo`. All existing REST routes remain available. The following behavior supersedes older examples below:

- `GET /api/health` returns `status`, `ai_configured`, and `telephony_configured` booleans.
- In Assist mode, routine turns return `AWAITING_APPROVAL`, with the reply in `decision.draft_response`. They are not appended as spoken until `/approve` succeeds.
- `decision.translated_caller_text` and `decision.translated_response` provide bilingual display text.
- `/user-action` stages a draft; the approved decision ledger is updated only by `/approve`.
- `/approve` returns `status: APPROVED`, `speak_text`, and `session_status` (`IN_PROGRESS` or `COMPLETED`).
- `POST /api/session/{id}/reject` clears the pending draft. `POST /api/session/{id}/interrupt` clears it and pauses the call for a decision.
- Pending decisions block new caller turns with HTTP 409. Completed sessions block further actions. Retry a failed `/complete` request; successful summaries are cached.
- Status responses additionally include `transcript_history`, `rules`, `pending_translation`, and `summary` when present.
- WebSocket `STAGING_UPDATE` is emitted for Assist replies. `STATUS_UPDATE` reports transitions. `REJECT_DRAFT` and `INTERRUPT` events use the same controls as REST.
- Session IDs are opaque. Credentials are optional for serving the frontend but required for AI sessions. No telephone provider is connected.

---

**Base URL**: `http://127.0.0.1:8000`  
**WebSocket URL**: `ws://127.0.0.1:8000/ws/call/{session_id}`  
**Interactive Docs**: `http://127.0.0.1:8000/docs`  
**CORS**: Same-origin frontend; localhost:8000 and 127.0.0.1:8000 are allowed by default. Set `CORS_ORIGINS` to a comma-separated list for other origins.

---

## 1. Lifecycle Overview

```
[1. POST /api/session/create]
         │ (Goal prompt + language + mode)
         ▼
  Session Created (status: READY_TO_START)
         │
         ▼
[2. Connect WebSocket: /ws/call/{session_id}]
         │
 ┌───────┴───────────────────────────────────────────┐
 │                                                   │
 ▼ (Routine conversation)                            ▼ (Constraint/Commitment violation)
Bot speaks draft_response                   Bot speaks stalling phrase immediately
status: IN_PROGRESS                         status: DECISION_REQUIRED
                                            UI displays Red Decision Card
                                                     │
                                                     ▼ (User selects action)
                                            User sends event: USER_ACTION
                                            status: AWAITING_APPROVAL
                                            UI displays Green Staged Draft
                                                     │
                                                     ▼ (User clicks Approve)
                                            User sends event: APPROVE_AND_SPEAK
                                            Bot speaks counter-offer
                                            status: IN_PROGRESS
 └───────────────────────────────────────────────────┘
         │
         ▼ (Call Finished)
[3. event: END_CALL or POST /api/session/{session_id}/complete]
  Returns structured summary report (Confirmed facts, unresolved items, outcome headline)
```

---

## 2. REST Endpoints

### `POST /api/session/create`
Initializes a new session and extracts structured constraints from natural language.

**Request Body**:
```json
{
  "raw_prompt": "Order 2 pepperoni pizzas from Mario's Pizza. Do not pay more than $30, delivery under 45 minutes. Never share credit card info.",
  "user_language": "Urdu",
  "mode": "delegate"
}
```
* `mode`: `"delegate"` (AI negotiates autonomously within limits) or `"assist"` (AI pauses for human approval on every commitment step).

**Response (200 OK)**:
```json
{
  "status": "success",
  "session_id": "f0c401b6",
  "flags": {
    "is_configured": true,
    "status": "READY_TO_START",
    "mode": "delegate"
  },
  "extracted_rules": {
    "intent": "order",
    "target_business": "Mario's Pizza",
    "action_details": "Order 2 pepperoni pizzas",
    "constraints": [
      { "parameter": "total cost", "operator": "max", "value": "30", "unit": "$" },
      { "parameter": "delivery time", "operator": "max", "value": "45", "unit": "minutes" }
    ],
    "forbidden_disclosures": ["credit card info"],
    "special_notes": null
  }
}
```

---

### `GET /api/session/{session_id}/status`
Poll session state, pending drafts, and the decision audit ledger.

**Response (200 OK)**:
```json
{
  "session_id": "f0c401b6",
  "status": "IN_PROGRESS",
  "mode": "delegate",
  "is_configured": true,
  "pending_draft": null,
  "decision_ledger": [
    {
      "action_chosen": "Negotiate down to $30",
      "summary": "Negotiation drafted at $30"
    }
  ]
}
```

### `POST /api/session/{session_id}/audio-turn`
Upload raw audio (from browser microphone) for transcription via AssemblyAI + instant arbiter evaluation.

* **Content-Type**: `multipart/form-data`
* **Body**: `file: <audio_file.wav / .webm / .mp3>`

**Response (200 OK)**:
```json
{
  "transcribed_text": "Alright, 2 pepperoni pizzas will come out to $38 with delivery.",
  "status": "DECISION_REQUIRED",
  "decision": {
    "is_dealbreaker": true,
    "violation_parameter": "price",
    "violation_reason": "Price exceeds maximum allowed of $30.",
    "immediate_stalling_phrase": "Hold on a moment while I confirm that.",
    "suggested_user_options": [
      "Ask for a discount",
      "Offer a smaller order"
    ],
    "draft_response": "..."
  }
}
```

---

### `POST /api/session/{session_id}/complete`
Generates post-call summary and report card.

**Response (200 OK)**:
```json
{
  "status": "COMPLETED",
  "summary": {
    "outcome_headline": "2 Pepperoni pizzas ordered for $30 with delivery.",
    "outcome_subtext": "Order confirmed under target budget and within delivery window.",
    "confirmed_items": ["2 Pepperoni pizzas", "Total price $30", "Delivery under 45 minutes"],
    "unresolved_items": []
  }
}
```

---

## 2a. Spoken replies in a natural voice (free)

The agent's replies are read aloud using `edge-tts` (Microsoft neural voices). No API key
and no credits are involved. `GET /api/health` reports `tts_configured` and `tts_voice`;
the voice is chosen with `TTS_VOICE` in `.env`.

Both routes below return `audio/mpeg` and **only accept text already in the conversation**,
so they cannot be used to make the agent say something new. Audio is cached server-side,
so asking for the same line twice is instant.

### `POST /api/session/{session_id}/speech`
For the controller. Accepts any line in the transcript **and the draft currently under
review**, so its audio can be prepared while the human reads it and play instantly on
approval.

**Request**: `{"text": "Would twenty dollars work?"}`
**Response (200 OK)**: MP3 bytes, `Content-Type: audio/mpeg`.
**403** if the text is not part of this conversation. **502** if synthesis fails — the page
then falls back to the browser's own voice rather than going silent.

### `POST /api/demo/speech`
Voice for the guided demo, which runs with no call and no API key. The demo lets the
presenter type any reply, so unlike the call routes this cannot check words against a
transcript; it is capped at 600 characters and rate limited to 40 requests per minute
per client instead. Takes the voice in the body since there is no session:
`{"text": "...", "voice": "female"}`. **413** if too long, **429** if too frequent,
**502** if synthesis fails (the page then falls back to the browser voice).

### `POST /api/session/{session_id}/voice-choice`
Chooses the agent's voice for this call. Stored on the session, so the receptionist
hears the same voice and a live AssemblyAI call uses the matching one.

**Request**: `{"voice": "male"}` — `"female"` (default, `en-US-AvaNeural`) or `"male"`
(`en-US-AndrewNeural`). Anything else returns **422**.
**Response (200 OK)**: `{"status": "ok", "tts_voice": "en-US-AndrewNeural", "tts_voice_choice": "male", "tts_voice_choices": ["female", "male"]}`

`POST /api/session/create` also accepts `"voice"` in its body, and `GET /api/session/{id}/status`
reports `tts_voice`, `tts_voice_choice`, and `tts_voice_choices`.

### `POST /api/participant/{token}/speech`
For the receptionist. Stricter: accepts **only agent lines that were actually spoken**.
A draft still under review returns **403**, and so do the receptionist's own words.

---

## 2b. Live voice calls (AssemblyAI Voice Agent API)

Optional. Replaces the browser's built-in speech with a real spoken call. AssemblyAI
handles listening, turn detection, interruptions, and speaking; CallBridge remains the
agent's language model, so the approval gate and every guardrail are unchanged.

Requires `ASSEMBLYAI_API_KEY` and a public `https` `PUBLIC_BASE_URL`. `GET /api/health`
and `GET /api/session/{id}/status` both report `voice_call_configured`, `voice_call_hint`,
and `voice`; the status route also reports `voice_live`.

### `POST /api/session/{session_id}/voice/start`
Registers this call's agent with AssemblyAI and opens the voice line. The agent's opening
line is appended to the transcript, because AssemblyAI speaks the greeting directly.

**Response (200 OK)**:
```json
{
  "voice_live": true,
  "status": "IN_PROGRESS",
  "greeting": "Hello, I am calling about early check-in for my client.",
  "voice": "michael",
  "sample_rate": 24000,
  "participant_path": "/receptionist/<token>"
}
```
**503** when `ASSEMBLYAI_API_KEY` or a public `https` `PUBLIC_BASE_URL` is missing; the
`detail` says which. **502** when AssemblyAI rejects the agent definition.

### `POST /api/session/{session_id}/voice/stop`
Closes the voice line and deletes the AssemblyAI agent. Returns `{"voice_live": false}`.

### `GET /api/participant/{token}/voice`
Called by the receptionist page. Returns a **one-time** token, valid for two minutes, for
opening the AssemblyAI WebSocket. The AssemblyAI API key never leaves the server.

**Response (200 OK)**:
```json
{
  "voice_live": true,
  "agent_id": "7ad24396-b822-4dca-871a-be9cc4781cf9",
  "websocket_url": "wss://agents.assemblyai.com/v1/ws",
  "token": "<one-time token>",
  "voice": "michael",
  "sample_rate": 24000
}
```
When no call is running it returns `{"voice_live": false, "hint": "..."}` with **200**, so
the page can explain rather than error.

### `POST /v1/voice/{session_id}/chat/completions`
**Called by AssemblyAI, not by the frontend.** CallBridge presented as an OpenAI-compatible
model. Requires `Authorization: Bearer <VOICE_BRIDGE_TOKEN>`; anything else gets **401**.

Streams Server-Sent Events in the OpenAI `chat.completion.chunk` shape. The reply depends
on what the turn needs:

| Turn | What is streamed |
|---|---|
| Routine and within limits | The agent's reply, immediately |
| Needs approval | The stalling phrase immediately, then the connection is **held open**; the approved words stream on that same connection the moment Approve is pressed |
| Still waiting after 30s and 70s | A short holding phrase, so the line never goes silent |
| No approval within `VOICE_APPROVAL_TIMEOUT_SECONDS` (default 120) | A polite offer to call back |
| Call already ended | A sign-off |

An unapproved draft is never streamed. `"stream": false` returns an ordinary completion
object, which is useful for testing the endpoint by hand.

---

## 3. Real-Time WebSocket (`/ws/call/{session_id}`)

### A. Events Sent by Frontend to Backend

#### 1. Incoming Transcript Chunk (Caller Speaking)
When the mock delivery person / receptionist speaks:
```json
{
  "event": "TRANSCRIPTION_CHUNK",
  "text": "Alright, 2 pepperoni pizzas will come out to $38 with delivery."
}
```

#### 2. User Action (User clicked an option or typed instruction)
When the user picks a card option or enters custom text:
```json
{
  "event": "USER_ACTION",
  "chosen_action": "Ask for a discount",
  "custom_instruction": "Ask if there are any lunch coupons to make it $30."
}
```

#### 3. User Approves Draft
When user clicks "Approve & Speak":
```json
{
  "event": "APPROVE_AND_SPEAK"
}
```

#### 4. End Call
When the call ends:
```json
{
  "event": "END_CALL"
}
```

---

### B. Events Pushed by Backend to Frontend

#### 1. Live Transcript Broadcast
```json
{
  "event": "TRANSCRIPT_STREAM",
  "speaker": "caller",
  "text": "Alright, 2 pepperoni pizzas will come out to $38 with delivery."
}
```

#### 2. Speak Out Loud (TTS Trigger)
```json
{
  "event": "SPEAK_PHRASE",
  "text": "Hold on a moment while I confirm that.",
  "interrupt": true
}
```

#### 3. Red Decision Card (Dealbreaker / Pause)
```json
{
  "event": "DECISION_REQUIRED",
  "reason": "price exceeds maximum allowed of $30",
  "options": [
    "Ask for a discount",
    "Offer a smaller order",
    "Suggest a different pizza size",
    "Offer to split the cost"
  ]
}
```

#### 4. Staging Update (Bilingual Card)
```json
{
  "event": "STAGING_UPDATE",
  "staged_draft": {
    "english_response": "Could we possibly bring the total to $30 by applying any available coupons?",
    "translated_response": "کیا ہم دستیاب کوپن لگا کر کل رقم 30 ڈالر کر سکتے ہیں؟",
    "action_summary": "Negotiation drafted at $30"
  }
}
```

#### 5. Post-Call Summary
```json
{
  "event": "CALL_SUMMARY",
  "summary": {
    "outcome_headline": "...",
    "outcome_subtext": "...",
    "confirmed_items": [...],
    "unresolved_items": [...]
  }
}
```

## Recovery and safe retries

Call state is persisted locally in SQLite. Run one server worker. Status includes `pending_draft_id`, `control_revision`, `paused`, `created_at`, `ended_at`, `language`, and `raw_prompt` for presenter recovery. These private fields are not available from the receptionist endpoint.

Send an optional unique `request_id` on `evaluate-turn`, participant turns, and `user-action`; reuse it only when retrying that identical request. Successful receipts are retained for the latest 64 requests. Changed payloads and requests invalidated by a pause return 409. Frontend text requests automatically use these IDs; raw audio uploads are not deduplicated.

Draft responses include `draft_id`. Send `{ "draft_id": "..." }` to `approve` to ensure only the reviewed draft can be approved. A retry of the last approved draft returns its receipt without appending or approving it twice. Pause invalidates in-flight model results immediately. End marks the call ended before generating the summary; a summary failure does not reopen it.

## User-language decisions

Turn decisions include `translated_violation_reason` and `translated_user_options` in the session's selected language. The translated options align one-to-one with `suggested_user_options`. Render translated labels but send the corresponding canonical English option as `chosen_action`. Keep `draft_response` / `english_response` as the spoken English and show `translated_response` first for user review. The WebSocket decision event exposes `translated_reason` and `translated_options` alongside its original fields.

## Conversation preferences and presence

`POST /api/session/create` accepts optional `business_name` (up to 160 characters) and `spending_limit` (a finite USD amount from 0 to 1,000,000). Explicit settings are included in goal extraction and then applied on the server; an explicit spending limit replaces model-extracted monetary maximums while preserving other constraints. Omitting the field preserves any limit extracted from the written task.

Presenter status includes `participant_connected`, true while the participant page was seen within eight seconds. This is connection presence only. It does not prove a person is listening. `/app` serves the current workspace; `/demo` remains a compatibility alias.

## Reply preferences

Session creation accepts optional `preferences`: `{ "tone": "professional|friendly|direct", "reply_length": "concise|detailed", "additional_instructions": "up to 1200 characters" }`. Defaults are professional, concise, and empty instructions. Preferences are included in presenter status and retained with saved calls; they are excluded from participant status. They guide planning and generated replies, not verbatim translation, and never override approval rules. Voice and appearance preferences remain in the browser.

## Private pilot access and invitation lifecycle

For remote sharing, configure `DELEGATE_PUBLIC_MODE=true` and a random `DELEGATE_PILOT_PASSCODE` of at least 16 characters. `/api/access/login` accepts `{"passcode":"..."}` and sets a 12-hour HttpOnly, SameSite=Strict cookie (Secure in public mode). `/api/access/logout` clears it. Controller REST and WebSocket endpoints require this cookie when protection is enabled. The participant API uses its separate capability token and does not require a host login. This is one trusted workspace, not multi-tenant accounts.

Health adds `pilot_protected`, `public_access_ready`, and `invitation_hours`. All API and participant-page responses use `Cache-Control: no-store`; cross-origin mutation requests are rejected. Pilot request limits return 429 with `Retry-After`; pause/end controls remain available.

- `POST /api/session/{id}/invitation/renew`: invalidate the previous invitation, return a new `participant_path` and `participant_expires_at` (Unix seconds, 24 hours from issuance).
- `POST /api/session/{id}/invitation/revoke`: invalidate the current invitation; status becomes `participant_path: null`. Existing transcripts stay saved.
- `POST /api/session/{id}/delete`: remove a completed conversation from memory and SQLite and invalidate its invitation. Returns 409 for an active conversation or live voice call, 404 if already absent. This is application deletion, not secure erasure of provider logs or backups.

Stop a live voice call before renewing/revoking its invitation. Unknown/revoked participant tokens return 404; expired tokens return 410. Already-minted provider tokens have their own expiration and cannot be recalled by deleting the invitation. Ended rooms return 409 when requesting new transcription credentials.

Participant status now includes `live_listening_configured` and `voice_live` for capability-aware controls. Controller status also includes `participant_expires_at` and `pilot_protected`. Polling these endpoints remains read-only with respect to AI usage.
