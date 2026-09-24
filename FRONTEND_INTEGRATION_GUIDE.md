# CallBridge Frontend Integration Guide

## Implemented frontend

The reference frontend is now implemented in `frontend/` and served directly by FastAPI. Open `http://localhost:8000` after starting the server. See [frontend/README.md](frontend/README.md) for the current implementation and [API_CONTRACT.md](API_CONTRACT.md) for updated approval semantics. In Assist mode **every reply** waits for approval; generated drafts are **not** approvals. AI roleplay uses REST and uploaded microphone recordings; the guided hotel demo runs locally without credentials. Actual outbound telephony is not implemented.

---

Welcome frontend team! This document details how CallBridge works, how the backend pipeline operates, and how to build the UI dashboard.

---

## 1. High-Level Concept: What is CallBridge?

CallBridge is an **outbound autonomous negotiation agent**:
* The **human client** sets a goal on your frontend (e.g. *"Book Dr. Smith Dental Clinic for Tuesday before 4 PM, max budget $30, never share credit card"*).
* CallBridge **places an outbound phone call** to the business receptionist.
* When conversation is routine, CallBridge answers autonomously.
* When the receptionist proposes something conflicting (e.g. *"Only 5:30 PM is available"* or *"Can I have your credit card?"*), CallBridge **stalls the receptionist live on the phone** (*"Hold on a moment while I check with my client..."*) and **escalates to the frontend with a Red Decision Card**.
* The human clicks a counter-offer option, verifies the bilingual draft, and clicks **"Approve & Speak"**.
* CallBridge speaks the counter-offer back to the receptionist.
* Once the receptionist confirms the booking, CallBridge signs off, concludes the call, and generates a **Post-Call Summary Report Card**.

---

## 2. Frontend State Machine

Your frontend UI should track the session status:

```
[Prompt Setup] ──────────► READY_TO_START
                                │
                                ▼ (Call Starts)
                           IN_PROGRESS ◄──────────────────┐
                                │                         │
             (Dealbreaker /     ▼                         │
             Violation caught)  DECISION_REQUIRED         │ (Approved & Spoken)
                                │ (User picks option)     │
                                ▼                         │
                           AWAITING_APPROVAL ─────────────┘
                                │
                                ▼ (Receptionist confirms / Goal met)
                            COMPLETED (Display Post-Call Report)
```

| State | UI View / Component |
| :--- | :--- |
| `READY_TO_START` | Goal prompt input, language selector (English/Urdu/Spanish), Mode toggle (`delegate` vs `assist`), and **"Start Call"** button. |
| `IN_PROGRESS` | Live audio waveform indicator, live transcript chat bubbles: `caller` (Receptionist) vs `agent` (CallBridge). |
| `DECISION_REQUIRED` | **Red Decision Card**: Displays violation warning, live stalling indicator, and suggested 1-click counter-offer buttons (+ custom text input). |
| `AWAITING_APPROVAL` | **Green Staged Response Card**: Shows English text to be spoken, client native language translation preview, and **"Approve & Speak"** button. |
| `COMPLETED` | **Post-Call Report Card**: Outcome headline, Confirmed items checklist, and Unresolved items list. |

---

## 3. Communication Options

You can build the frontend using either **REST API polling** OR real-time **WebSockets** (recommended for live streaming).

Base URL: `http://127.0.0.1:8000`  
WebSocket URL: `ws://127.0.0.1:8000/ws/call/{session_id}`

---

## 4. REST Endpoints Reference

### 1. Create Call Session
`POST /api/session/create`

**Request Body:**
```json
{
  "raw_prompt": "Call Dr. Smith Dental Clinic to schedule a checkup for Tuesday before 4 PM. Do not accept times after 4 PM, and never share my credit card or SSN.",
  "user_language": "Urdu",
  "mode": "delegate"
}
```

**Response (200 OK):**
```json
{
  "status": "success",
  "session_id": "159ca402",
  "flags": {
    "is_configured": true,
    "status": "READY_TO_START",
    "mode": "delegate"
  },
  "extracted_rules": {
    "intent": "schedule_appointment",
    "target_business": "Dr. Smith Dental Clinic",
    "action_details": "dental checkup for Tuesday before 4 PM",
    "opening_phrase": "Hello, I would like to schedule a dental checkup for this Tuesday before 4 PM.",
    "constraints": [
      { "parameter": "date", "operator": "exact", "value": "Tuesday", "unit": "" },
      { "parameter": "time", "operator": "max", "value": "4", "unit": "PM" }
    ],
    "forbidden_disclosures": ["credit card", "SSN"]
  }
}
```

---

### 2. Check Session Status
`GET /api/session/{session_id}/status`

**Response:**
```json
{
  "session_id": "159ca402",
  "status": "IN_PROGRESS",
  "mode": "delegate",
  "is_configured": true,
  "pending_draft": null,
  "decision_ledger": [
    {
      "action_chosen": "Could we possibly move the appointment to Monday at 2 PM?",
      "summary": "Counter-offer drafted for Monday"
    }
  ]
}
```

---

### 3. Send Incoming Caller Turn (Text or Speech)
* If sending text transcript: `POST /api/session/{session_id}/evaluate-turn`
* If uploading raw microphone audio: `POST /api/session/{session_id}/audio-turn` (`multipart/form-data` with `file: audio.wav`)

**Response when Dealbreaker is caught:**
```json
{
  "status": "DECISION_REQUIRED",
  "decision": {
    "is_dealbreaker": true,
    "call_completed": false,
    "violation_parameter": "date",
    "violation_reason": "No availability on Tuesday; receptionist offered Monday",
    "immediate_stalling_phrase": "Hold on a moment while I check with my client.",
    "suggested_user_options": [
      "Ask if any earlier time slots are available on Tuesday",
      "Ask to move appointment to Monday at 2 PM",
      "Decline and try next week"
    ],
    "draft_response": "..."
  }
}
```

**Response when Call is Finalized:**
```json
{
  "status": "COMPLETED",
  "decision": {
    "is_dealbreaker": false,
    "call_completed": true,
    "draft_response": "Wonderful, thank you so much! Monday at 2 PM is confirmed. Have a great day, goodbye!"
  }
}
```

---

### 4. User Selects Counter-Action
`POST /api/session/{session_id}/user-action`

**Request Body:**
```json
{
  "chosen_action": "Ask to move appointment to Monday at 2 PM",
  "custom_instruction": null
}
```

**Response:**
```json
{
  "status": "AWAITING_APPROVAL",
  "staged_draft": {
    "english_response": "Could we possibly move the appointment to Monday at 2 PM?",
    "translated_response": "کیا ہم اپائنٹمنٹ پیر کے روز دوپہر 2 بجے منتقل کر سکتے ہیں؟",
    "action_summary": "Counter-offer drafted for Monday 2 PM"
  }
}
```

---

### 5. User Clicks "Approve & Speak"
`POST /api/session/{session_id}/approve`

**Response:**
```json
{
  "status": "APPROVED",
  "speak_text": "Could we possibly move the appointment to Monday at 2 PM?",
  "message": "Send this speak_text to the TTS engine to play out loud."
}
```

---

### 6. Get Post-Call Report Card
`POST /api/session/{session_id}/complete`

**Response:**
```json
{
  "status": "COMPLETED",
  "summary": {
    "outcome_headline": "Dental checkup successfully scheduled for Monday at 2:00 PM.",
    "outcome_subtext": "Client agreed to Monday alternate after Tuesday was fully booked.",
    "confirmed_items": [
      "Appointment for Alex Johnson",
      "Date: Monday",
      "Time: 2:00 PM",
      "No credit card info shared"
    ],
    "unresolved_items": []
  }
}
```

---

## 5. WebSocket Protocol (`/ws/call/{session_id}`)

### Frontend -> Backend Events
1. `TRANSCRIPTION_CHUNK`: `{"event": "TRANSCRIPTION_CHUNK", "text": "..."}`
2. `USER_ACTION`: `{"event": "USER_ACTION", "chosen_action": "...", "custom_instruction": "..."}`
3. `APPROVE_AND_SPEAK`: `{"event": "APPROVE_AND_SPEAK"}`
4. `END_CALL`: `{"event": "END_CALL"}`

### Backend -> Frontend Events
1. `TRANSCRIPT_STREAM`: `{"event": "TRANSCRIPT_STREAM", "speaker": "caller"|"agent", "text": "..."}`
2. `SPEAK_PHRASE`: `{"event": "SPEAK_PHRASE", "text": "...", "interrupt": true|false}`
3. `DECISION_REQUIRED`: `{"event": "DECISION_REQUIRED", "reason": "...", "options": [...]}`
4. `STAGING_UPDATE`: `{"event": "STAGING_UPDATE", "staged_draft": { "english_response": "...", "translated_response": "..." }}`
5. `CALL_SUMMARY`: `{"event": "CALL_SUMMARY", "summary": { ... }}`

---

## 6. React / Next.js Hook Blueprint

```typescript
import { useState, useEffect } from 'react';

export function useCallBridge(sessionId: string) {
  const [status, setStatus] = useState<'READY_TO_START' | 'IN_PROGRESS' | 'DECISION_REQUIRED' | 'AWAITING_APPROVAL' | 'COMPLETED'>('READY_TO_START');
  const [transcript, setTranscript] = useState<Array<{ speaker: string; text: string }>>([]);
  const [decisionCard, setDecisionCard] = useState<{ reason: string; options: string[] } | null>(null);
  const [stagedDraft, setStagedDraft] = useState<{ english: string; translated: string } | null>(null);
  const [summaryReport, setSummaryReport] = useState<any>(null);

  // Connect WebSocket
  useEffect(() => {
    if (!sessionId) return;
    const ws = new WebSocket(`ws://127.0.0.1:8000/ws/call/${sessionId}`);

    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.event === 'TRANSCRIPT_STREAM') {
        setTranscript((prev) => [...prev, { speaker: msg.speaker, text: msg.text }]);
      } else if (msg.event === 'DECISION_REQUIRED') {
        setStatus('DECISION_REQUIRED');
        setDecisionCard({ reason: msg.reason, options: msg.options });
      } else if (msg.event === 'STAGING_UPDATE') {
        setStatus('AWAITING_APPROVAL');
        setStagedDraft({
          english: msg.staged_draft.english_response,
          translated: msg.staged_draft.translated_response,
        });
      } else if (msg.event === 'CALL_SUMMARY') {
        setStatus('COMPLETED');
        setSummaryReport(msg.summary);
      }
    };

    return () => ws.close();
  }, [sessionId]);

  return { status, transcript, decisionCard, stagedDraft, summaryReport };
}
```
