import uuid
from typing import Dict
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware

from models import (
    IncomingUserRequest,
    IncomingCallerTurn,
    UserDecisionAction,
)
from engine import (
    client,
    extract_rules_from_prompt,
    evaluate_caller_turn,
    generate_staged_draft,
    generate_call_summary,
    transcribe_audio,
)

app = FastAPI(title="CallBridge API", version="1.0.0")

# Enable CORS for frontend integration (React, Vite, Next.js)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

sessiondb: Dict[str, Dict] = {}

@app.post("/api/session/create")
def create_session(request: IncomingUserRequest):
    clean_prompt = request.raw_prompt.strip()
    if not clean_prompt:
        raise HTTPException(status_code=400, detail={"error": "Empty prompt not allowed"})
    
    session_id = str(uuid.uuid4())[:8]

    try:
        rules = extract_rules_from_prompt(clean_prompt, request.user_language)
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": f"Failed to parse prompt: {str(e)}"})
    
    mode = (request.mode or "delegate").lower()
    sessiondb[session_id] = {
        "session_id": session_id,
        "raw_prompt": clean_prompt,
        "language": request.user_language,
        "mode": mode,
        "rules": rules.model_dump(),
        "transcript_history": [],
        "decision_ledger": [],
        "pending_draft": None,
        "is_configured": True,
        "status": "READY_TO_START"
    }

    return {
        "status": "success",
        "session_id": session_id,
        "flags": {
            "is_configured": True,
            "status": "READY_TO_START",
            "mode": mode
        },
        "extracted_rules": rules.model_dump()
    }

@app.get("/api/session/{session_id}/status")
def get_session_status(session_id: str):
    session = sessiondb.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")

    return {
        "session_id": session_id,
        "status": session["status"],
        "mode": session.get("mode", "delegate"),
        "is_configured": session["is_configured"],
        "pending_draft": session.get("pending_draft"),
        "decision_ledger": session.get("decision_ledger", [])
    }

@app.post("/api/session/{session_id}/evaluate-turn")
def evaluate_turn(session_id: str, turn: IncomingCallerTurn):
    session = sessiondb.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")

    caller_text = turn.caller_text.strip()
    if not caller_text:
        return {
            "status": session["status"],
            "decision": {
                "is_dealbreaker": False,
                "call_completed": False,
                "violation_parameter": None,
                "violation_reason": None,
                "immediate_stalling_phrase": None,
                "suggested_user_options": [],
                "draft_response": "I'm sorry, I couldn't hear you clearly. Could you please repeat that?"
            }
        }

    session.setdefault("transcript_history", []).append({"speaker": "caller", "text": caller_text})

    try:
        decision = evaluate_caller_turn(caller_text, session)
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": f"Failed to evaluate turn: {str(e)}"})

    if decision.call_completed:
        session["status"] = "COMPLETED"
        session["transcript_history"].append({"speaker": "agent", "text": decision.draft_response})
    elif decision.is_dealbreaker:
        session["status"] = "DECISION_REQUIRED"
    else:
        session["status"] = "IN_PROGRESS"
        session["transcript_history"].append({"speaker": "agent", "text": decision.draft_response})

    return {
        "status": session["status"],
        "decision": decision.model_dump()
    }

@app.post("/api/session/{session_id}/audio-turn")
async def handle_audio_turn(session_id: str, file: UploadFile = File(...)):
    session = sessiondb.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")

    try:
        audio_bytes = await file.read()
        caller_text = transcribe_audio(audio_bytes, file.filename or "audio.wav")
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": f"Audio transcription failed: {str(e)}"})

    caller_text = (caller_text or "").strip()
    if not caller_text:
        return {
            "transcribed_text": "(silence / inaudible)",
            "status": session["status"],
            "decision": {
                "is_dealbreaker": False,
                "call_completed": False,
                "violation_parameter": None,
                "violation_reason": None,
                "immediate_stalling_phrase": None,
                "suggested_user_options": [],
                "draft_response": "I'm sorry, I couldn't hear you clearly. Could you please repeat that?"
            }
        }

    session.setdefault("transcript_history", []).append({"speaker": "caller", "text": caller_text})

    try:
        decision = evaluate_caller_turn(caller_text, session)
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": f"Evaluation failed: {str(e)}"})

    if decision.call_completed:
        session["status"] = "COMPLETED"
        session["transcript_history"].append({"speaker": "agent", "text": decision.draft_response})
    elif decision.is_dealbreaker:
        session["status"] = "DECISION_REQUIRED"
    else:
        session["status"] = "IN_PROGRESS"
        session["transcript_history"].append({"speaker": "agent", "text": decision.draft_response})

    return {
        "transcribed_text": caller_text,
        "status": session["status"],
        "decision": decision.model_dump()
    }

@app.post("/api/session/{session_id}/user-action")
def handle_user_action(session_id: str, action: UserDecisionAction):
    session = sessiondb.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")

    try:
        staged = generate_staged_draft(session, action)
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": f"Failed to draft response: {str(e)}"})

    session["pending_draft"] = staged.english_response
    session["status"] = "AWAITING_APPROVAL"
    
    session.setdefault("decision_ledger", []).append({
        "action_chosen": action.chosen_action,
        "summary": staged.action_summary
    })

    return {
        "status": session["status"],
        "staged_draft": staged.model_dump()
    }

@app.post("/api/session/{session_id}/approve")
def approve_and_speak(session_id: str):
    session = sessiondb.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")

    text_to_speak = session.get("pending_draft")
    if not text_to_speak:
        raise HTTPException(status_code=400, detail="No pending draft found to approve.")

    session.setdefault("transcript_history", []).append({"speaker": "agent", "text": text_to_speak})
    session["status"] = "IN_PROGRESS"
    session["pending_draft"] = None

    return {
        "status": "APPROVED",
        "speak_text": text_to_speak,
        "message": "Send this speak_text to the TTS engine to play out loud."
    }

@app.post("/api/session/{session_id}/complete")
def complete_call(session_id: str):
    session = sessiondb.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")
    
    session["status"] = "COMPLETED"
    try:
        summary = generate_call_summary(session)
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": f"Failed to generate summary: {str(e)}"})
    
    return {
        "status": "COMPLETED",
        "summary": summary.model_dump()
    }

@app.get("/api/models")
def get_available_models():
    try:
        models = client.models.list()
        return {
            "available_models": [m.id for m in models.data]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": f"Failed to fetch models: {str(e)}"})

@app.websocket("/ws/call/{session_id}")
async def live_call_socket(websocket: WebSocket, session_id: str):
    await websocket.accept()
    
    session = sessiondb.get(session_id)
    if not session:
        await websocket.send_json({"error": "Session does not exist. Create via /api/session/create first."})
        await websocket.close()
        return

    try:
        while True:
            data = await websocket.receive_json()
            event_type = data.get("event")

            # 1. Incoming transcribed speech from the business/caller
            if event_type == "TRANSCRIPTION_CHUNK":
                text = data.get("text", "").strip()
                if not text:
                    continue

                session.setdefault("transcript_history", []).append({"speaker": "caller", "text": text})

                await websocket.send_json({
                    "event": "TRANSCRIPT_STREAM",
                    "speaker": "caller",
                    "text": text
                })

                decision = evaluate_caller_turn(text, session)

                if decision.is_dealbreaker:
                    session["status"] = "DECISION_REQUIRED"
                    
                    # Speak immediate staller line
                    await websocket.send_json({
                        "event": "SPEAK_PHRASE",
                        "text": decision.immediate_stalling_phrase,
                        "interrupt": True
                    })

                    # Push Red Decision Card to UI
                    await websocket.send_json({
                        "event": "DECISION_REQUIRED",
                        "reason": decision.violation_reason,
                        "options": decision.suggested_user_options
                    })
                else:
                    session["transcript_history"].append({"speaker": "agent", "text": decision.draft_response})
                    await websocket.send_json({
                        "event": "SPEAK_PHRASE",
                        "text": decision.draft_response,
                        "interrupt": False
                    })

            # 2. User tapped an option on the Red Card
            elif event_type == "USER_ACTION":
                action = UserDecisionAction(
                    chosen_action=data.get("chosen_action"),
                    custom_instruction=data.get("custom_instruction")
                )
                staged = generate_staged_draft(session, action)
                session["pending_draft"] = staged.english_response

                await websocket.send_json({
                    "event": "STAGING_UPDATE",
                    "staged_draft": staged.model_dump()
                })

            # 3. User clicked "Approve & speak"
            elif event_type == "APPROVE_AND_SPEAK":
                text_to_speak = session.get("pending_draft")
                if text_to_speak:
                    session.setdefault("transcript_history", []).append({"speaker": "agent", "text": text_to_speak})
                    session["pending_draft"] = None
                    session["status"] = "IN_PROGRESS"

                    await websocket.send_json({
                        "event": "SPEAK_PHRASE",
                        "text": text_to_speak,
                        "interrupt": False
                    })

            # 4. Call ended -> generate post-call summary
            elif event_type == "END_CALL":
                session["status"] = "COMPLETED"
                try:
                    summary = generate_call_summary(session)
                    await websocket.send_json({
                        "event": "CALL_SUMMARY",
                        "summary": summary.model_dump()
                    })
                except Exception as e:
                    await websocket.send_json({"error": f"Failed to generate summary: {str(e)}"})

    except WebSocketDisconnect:
        pass
