# CallBridge Backend API & WebSocket Specification

**Base URL**: `http://127.0.0.1:8000`  
**WebSocket URL**: `ws://127.0.0.1:8000/ws/call/{session_id}`  
**Interactive Docs**: `http://127.0.0.1:8000/docs`  
**CORS**: Enabled for all origins (`allow_origins=["*"]`).

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
