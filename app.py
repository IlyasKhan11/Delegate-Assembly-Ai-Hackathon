"""Delegate web app and shared REST/WebSocket call state machine."""
import asyncio
import os
import re
import secrets
import uuid
import time
from functools import wraps
from threading import RLock
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

import pilot_access
import live_transcription
import tts
import voice_agent
from models import IncomingUserRequest, IncomingCallerTurn, UserDecisionAction, DraftApproval, RuleConstraint, SpeechRequest, VoiceChoice
from call_store import CallStore
from localization import CURRENCY_SYMBOLS, money, translate
from ai_config import public_ai_settings, get_provider
from engine import (
    client, extract_rules_from_prompt, evaluate_caller_turn,
    generate_staged_draft, generate_call_summary, transcribe_audio,
)

app = FastAPI(title="Delegate / CallBridge API", version="1.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(","),
    allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["Content-Type"],
)
pilot_access.install(app)
store = CallStore(os.getenv("DELEGATE_DATABASE", str(Path(__file__).parent / ".delegate-data" / "calls.sqlite3")))
sessiondb: Dict[str, Dict] = store.load_all()
for saved_session in sessiondb.values():
    saved_session.pop("participant_last_seen", None)
    if saved_session.get("language") == "Portuguese":
        saved_session["language"] = "Portuguese (Brazil)"
session_locks = {}
state_lock = RLock()
participant_sessions = {session["participant_token"]: sid for sid, session in sessiondb.items() if session.get("participant_token")}


def save_session(session):
    with state_lock:
        # A late async response must never resurrect a deleted conversation.
        if sessiondb.get(session["session_id"]) is session:
            store.save(deepcopy(session))


def assert_current(session, revision):
    if session.get("deleted") or session.get("control_revision", 0) != revision or session["status"] == "COMPLETED":
        raise HTTPException(409, "This response was cancelled because the call was paused or ended. Prepare a new reply to continue.")


def request_fingerprint(kind, payload):
    return {"kind": kind, "payload": payload.model_dump(exclude={"request_id"})}


def previous_result(session, request_id, fingerprint):
    entry = session.get("request_results", {}).get(request_id) if request_id else None
    if entry:
        if entry["fingerprint"] != fingerprint:
            raise HTTPException(409, "This request identifier was already used for a different reply.")
        if entry["revision"] != session.get("control_revision", 0):
            raise HTTPException(409, "That request was superseded by a pause or an ended call. Prepare a new reply.")
        return deepcopy(entry["result"])


def remember_result(session, request_id, fingerprint, result):
    if request_id:
        cache = session.setdefault("request_results", {})
        cache[request_id] = {"fingerprint": fingerprint, "result": deepcopy(result), "revision": session.get("control_revision", 0)}
        # Bound retained retry receipts; the conversation itself remains saved.
        while len(cache) > 64:
            cache.pop(next(iter(cache)))


def serialized_session(function):
    @wraps(function)
    def wrapper(session_id: str, *args, **kwargs):
        with session_locks.setdefault(session_id, RLock()):
            try:
                return function(session_id, *args, **kwargs)
            finally:
                if session_id in sessiondb:
                    save_session(sessiondb[session_id])
    return wrapper


def append_turn(session, speaker, text, translation=None):
    elapsed = max(0, int(time.time() - session.get("created_at", time.time())))
    session["transcript_history"].append({
        "id": uuid.uuid4().hex,
        "speaker": speaker, "text": text, "translation": translation,
        "time": f"{elapsed // 60:02d}:{elapsed % 60:02d}",
    })


def session_for(session_id, *, active=False):
    session = sessiondb.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found.")
    if active and session["status"] == "COMPLETED":
        raise HTTPException(409, "This call has ended. Start a new call.")
    return session


def record(session, action, summary, **extra):
    session["decision_ledger"].append({
        "action_chosen": action, "summary": summary,
        "timestamp": datetime.now(timezone.utc).isoformat(), **extra,
    })


# Words that mark a constraint as a spending limit, in every language the app
# accepts. Without the non-English ones a Portuguese "taxa aceitavel" ceiling
# was ignored entirely, leaving only the model between a caller and an
# over-budget agreement.
MONEY_WORDS = (
    "price", "cost", "budget", "spend", "fee", "amount", "limit",
    "pre\u00e7o", "preco", "custo", "taxa", "valor", "or\u00e7amento", "orcamento", "gasto",
    "precio", "costo", "coste", "tarifa", "importe", "presupuesto",
    "prix", "co\u00fbt", "cout", "frais", "tarif", "montant",
    "preis", "kosten", "geb\u00fchr", "gebuhr", "betrag",
    "limite", "l\u00edmite",
)


def spend_limit(session):
    """The hard ceiling for this call, as (amount, currency unit).

    A ceiling counts as money if its unit is a currency, or if its name says so.
    Matching only on the name missed limits the extractor labelled in the user's
    own words, such as "oferta maxima: 30 reais".
    """
    for rule in session["rules"].get("constraints", []):
        if rule.get("operator") != "max":
            continue
        parameter = str(rule.get("parameter", "")).lower()
        unit = str(rule.get("unit") or "").strip().lower()
        if unit in CURRENCY_SYMBOLS or any(word in parameter for word in MONEY_WORDS):
            match = re.search(r"\d+(?:\.\d+)?", str(rule.get("value", "")).replace(",", ""))
            if match:
                return float(match.group()), rule.get("unit")
    return None, None


def price_ceiling(session):
    return spend_limit(session)[0]


# Currencies a business might quote. Restricting this to dollars meant a fee
# quoted in reais or euros was never checked against the ceiling.
CURRENCY_SYMBOL = r"(?:R\$|US\$|\$|\u20ac|\u00a3|\u20b9|\u20a8|Rs\.?|PKR|INR)"
CURRENCY_WORD = (r"(?:dollars?|usd|reais|real|brl|euros?|eur|pounds?|gbp|libras?"
                 r"|rupees?|rs\.?|pkr|inr)")

# In South Asia amounts are spoken as "one lakh" (100,000) or "two crore"
# (10,000,000), often with the remainder after it: "1 lakh 3,000" is 103,000.
SCALE_WORDS = {"thousand": 1_000, "lakhs": 100_000, "lakh": 100_000, "lac": 100_000,
               "crores": 10_000_000, "crore": 10_000_000}


def scaled_amounts(text):
    """Amounts written with a scale word, e.g. '1 lakh 3,000' -> 103000."""
    found = []
    for word, multiplier in SCALE_WORDS.items():
        pattern = rf"\b(\d+(?:\.\d+)?)\s*{word}\b(?:\s*(?:and\s+)?(\d[\d,]*))?"
        for whole, remainder in re.findall(pattern, text, re.I):
            total = float(whole) * multiplier
            if remainder:
                total += float(remainder.replace(",", ""))
            found.append(total)
    return found


def quoted_prices(text):
    """Recognize explicit currency quotes; keep the model's other constraint checks."""
    result = [float(x.replace(",", "")) for x in re.findall(rf"{CURRENCY_SYMBOL}\s*(\d[\d,]*(?:\.\d+)?)", text)]
    result += [float(x) for x in re.findall(rf"\b(\d+(?:\.\d+)?)\s*{CURRENCY_WORD}\b", text, re.I)]
    result += scaled_amounts(text)
    numbers = {"ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "one hundred": 100}
    for word, value in numbers.items():
        if re.search(rf"\b{word}\s+{CURRENCY_WORD}\b", text, re.I):
            result.append(float(value))
    return sorted(set(result))


# Acting on the client's behalf: agreeing, paying, booking, cancelling. Any of
# these needs a human yes, so the list covers how support agents actually ask.
COMMITMENT_VERBS = r"(?:book|confirm|reserve|hold|apply|process|charge|schedule|activate|cancel|proceed with|go ahead with)"
COMMITMENT_PATTERN = (
    r"\b(?:"
    r"(?:shall|should|can|may|would) (?:I|we) " + COMMITMENT_VERBS +
    r"|would you like (?:me|us) to " + COMMITMENT_VERBS +
    r"|(?:I|we)(?:\'ll| will)(?: go ahead and)? " + COMMITMENT_VERBS +
    r"|confirm (?:the|your) (?:booking|reservation|check-in|order|cancellation|payment|charge)"
    r"|you(?:\'re| are) (?:booked|all set|signed up)"
    r"|(?:your |the )?(?:booking|reservation|check-in|order|cancellation) (?:is |has been )?confirmed"
    r")\b"
)

# Details that must never be given out without the client saying so. Includes
# the Brazilian identifiers a support line would realistically ask for.
SENSITIVE_PATTERN = (
    r"\b(?:card number|credit card|debit card|passport|social security|ssn|cpf|rg"
    r"|(?:home|full|billing|street|complete) address|date of birth|birth date"
    r"|bank account|account number|routing number|security code|cvv|pin code|password"
    r"|n\u00famero do cart\u00e3o|numero do cartao|senha|data de nascimento"
    r"|endere\u00e7o completo|endereco completo)\b"
)


def apply_guardrails(session, caller_text, decision):
    language = session.get("language", "English")
    ceiling, currency = spend_limit(session)
    prices = quoted_prices(caller_text)
    approved_prices = session.get("approved_prices", [])
    over = [p for p in prices if ceiling is not None and p > ceiling and p not in approved_prices]
    commitment = re.search(COMMITMENT_PATTERN, caller_text, re.I)
    committing_reply = re.search(r"\b(?:I|we)(?:'ll| will)? (?:accept|agree|confirm|book)|\b(?:please (?:book|reserve)|go ahead (?:and|with)|that works perfectly)\b", decision.draft_response or "", re.I)
    sensitive_request = re.search(SENSITIVE_PATTERN, caller_text, re.I)
    reason = None
    parameter = None
    if over:
        highest = money(max(over), currency)
        allowed = money(ceiling, currency)
        reason = f"The business is asking for {highest}, above your {allowed} limit. Nothing has been accepted."
        parameter = "price"
        session["quoted_price"] = max(over)
        decision.suggested_user_options = [f"Negotiate down to {allowed}", f"Approve {highest} — this time only", "Decline the offer"]
        decision.translated_user_options = [translate(language, "negotiate", limit=allowed), translate(language, "accept", amount=highest), translate(language, "decline")]
        decision.translated_violation_reason = translate(language, "priceReason", amount=highest, limit=allowed)
    elif sensitive_request:
        reason = "Personal information was requested. Nothing will be shared without your approval."
        parameter = "personal_data"
        decision.suggested_user_options = ["Ask for an alternative", "Decline the offer"]
        decision.translated_user_options = [translate(language, "alternative"), translate(language, "decline")]
        decision.translated_violation_reason = translate(language, "dataReason")
    elif (commitment or committing_reply) and not session.get("commitment_authorized"):
        reason = "A booking or agreement needs your approval. Nothing has been confirmed on your behalf."
        parameter = "commitment"
        decision.suggested_user_options = ["Approve this booking", "Ask for more details before deciding", "Decline the offer"]
        decision.translated_user_options = [translate(language, key) for key in ("confirm", "details", "decline")]
        decision.translated_violation_reason = translate(language, "bookingReason")
    if reason:
        decision.is_dealbreaker = True
        decision.violation_reason = reason
        decision.violation_parameter = parameter
    if decision.is_dealbreaker:
        decision.call_completed = False
        decision.immediate_stalling_phrase = "Let me check with my client before agreeing to anything."
    return decision


@app.get("/api/health")
def health():
    return {"status": "ok", **public_ai_settings(), **voice_agent.public_voice_settings(),
            **tts.public_tts_settings(), **live_transcription.public_listening_settings(),
            "telephony_configured": False,
            "pilot_protected": bool(pilot_access.passcode()),
            "public_access_ready": pilot_access.ready(),
            "invitation_hours": 24}


@app.post("/api/session/create")
def create_session(request: IncomingUserRequest):
    clean_prompt = request.raw_prompt.strip()
    if not clean_prompt:
        raise HTTPException(400, "Please describe what you need.")
    if not health()["ai_configured"]:
        variable = "AIML_API_KEY" if get_provider() == "aimlapi" else "GROQ_API_KEY"
        raise HTTPException(503, f"Add {variable} to .env to use AI roleplay. The guided demo works without a key.")
    try:
        planning_prompt = clean_prompt
        if request.business_name and request.business_name.strip():
            planning_prompt = f"Business: {request.business_name.strip()}\nTask: {planning_prompt}"
        if request.spending_limit is not None:
            planning_prompt += f"\nExplicit maximum spending limit: {request.spending_limit:g} USD."
        planning_prompt += f"\nResponse style: {request.preferences.tone}; {request.preferences.reply_length}."
        if request.preferences.additional_instructions.strip():
            planning_prompt += f"\nAdditional user instructions: {request.preferences.additional_instructions.strip()}"
        rules = extract_rules_from_prompt(planning_prompt, request.user_language)
    except Exception:
        raise HTTPException(502, "The AI service could not prepare your call. Check your API key and connection, then try again.")
    # Explicit form settings take precedence over model extraction.
    rules = rules.model_copy(deep=True)
    if request.business_name and request.business_name.strip():
        rules.target_business = request.business_name.strip()
    if request.spending_limit is not None:
        rules.constraints = [rule for rule in rules.constraints if not (
            rule.operator == "max" and any(word in rule.parameter.lower() for word in ("price", "cost", "budget", "spend", "fee", "amount")))]
        rules.constraints.append(RuleConstraint(parameter="price", operator="max", value=f"{request.spending_limit:g}", unit="USD"))
    session_id = uuid.uuid4().hex
    participant_token = uuid.uuid4().hex
    participant_sessions[participant_token] = session_id
    sessiondb[session_id] = {
        "session_id": session_id, "raw_prompt": clean_prompt,
        "language": request.user_language, "mode": request.mode, "preferences": request.preferences.model_dump(),
        "rules": rules.model_dump(), "transcript_history": [], "decision_ledger": [],
        "pending_draft": None, "pending_action": None, "pending_completion": False,
        "is_configured": True, "status": "READY_TO_START",
        "created_at": time.time(), "participant_token": participant_token,
        "participant_expires_at": time.time() + 86400,
        "control_revision": 0, "pending_draft_id": None, "voice_choice": request.voice,
    }
    save_session(sessiondb[session_id])
    return {"status": "success", "session_id": session_id,
            "flags": {"is_configured": True, "status": "READY_TO_START", "mode": request.mode,
                      "audio_configured": public_ai_settings()["audio_configured"]},
            "participant_path": f"/receptionist/{participant_token}",
            "extracted_rules": rules.model_dump(), **tts.public_tts_settings(request.voice)}


@app.get("/api/session/{session_id}/status")
def get_session_status(session_id: str):
    with state_lock:
        session = session_for(session_id)
        result = deepcopy({key: session.get(key) for key in (
        "session_id", "status", "mode", "preferences", "is_configured", "pending_draft",
        "pending_translation", "decision_ledger", "transcript_history", "rules", "summary", "last_decision",
        "pending_draft_id", "language", "raw_prompt", "created_at", "ended_at", "control_revision", "paused")})
        result["participant_connected"] = time.time() - session.get("participant_last_seen", 0) < 8
        result["participant_path"] = f"/receptionist/{session['participant_token']}" if session.get("participant_token") else None
        result["participant_expires_at"] = session.get("participant_expires_at", session.get("created_at", 0) + 86400)
        result["pilot_protected"] = bool(pilot_access.passcode())
        result["audio_configured"] = public_ai_settings()["audio_configured"]
        result["voice_live"] = bool(session.get("voice_live"))
        result.update(voice_agent.public_voice_settings())
        result.update(tts.public_tts_settings(session.get("voice_choice")))
        result.update(live_transcription.public_listening_settings())
        return result


@app.post("/api/session/{session_id}/evaluate-turn")
@serialized_session
def evaluate_turn(session_id: str, turn: IncomingCallerTurn):
    session = session_for(session_id, active=True)
    fingerprint = request_fingerprint("turn", turn)
    replay = previous_result(session, turn.request_id, fingerprint)
    if replay:
        return replay
    if session["status"] in ("AWAITING_APPROVAL", "DECISION_REQUIRED"):
        raise HTTPException(409, "Resolve the pending decision before continuing the conversation.")
    caller_text = turn.caller_text.strip()
    if not caller_text:
        raise HTTPException(400, "Enter a receptionist response first.")
    revision = session.get("control_revision", 0)
    try:
        decision = evaluate_caller_turn(caller_text, deepcopy(session))
    except Exception:
        raise HTTPException(502, "The AI could not process this response. Please try again.")
    with state_lock:
        assert_current(session, revision)
        decision = apply_guardrails(session, caller_text, decision)
        append_turn(session, "caller", caller_text, decision.translated_caller_text)
        session["pending_completion"] = False
        if decision.is_dealbreaker:
            session["status"] = "DECISION_REQUIRED"
            session["pending_draft"] = None
            session["pending_draft_id"] = None
            session["last_decision"] = decision.model_dump()
            record(session, "Decision required", decision.violation_reason, approved=False)
            if decision.immediate_stalling_phrase:
                append_turn(session, "agent", decision.immediate_stalling_phrase)
        elif session["mode"] == "assist":
            session["status"] = "AWAITING_APPROVAL"
            session["pending_draft"] = decision.draft_response
            session["pending_draft_id"] = uuid.uuid4().hex
            session["pending_translation"] = decision.translated_response
            session["pending_completion"] = decision.call_completed
        else:
            session["status"] = "COMPLETED" if decision.call_completed else "IN_PROGRESS"
            if decision.draft_response:
                append_turn(session, "agent", decision.draft_response, decision.translated_response)
        if session["status"] == "COMPLETED":
            session["ended_at"] = time.time()
        result = {"status": session["status"], "decision": decision.model_dump(), "draft_id": session.get("pending_draft_id")}
        remember_result(session, turn.request_id, fingerprint, result)
        return result


@app.post("/api/session/{session_id}/audio-turn")
async def handle_audio_turn(session_id: str, file: UploadFile = File(...)):
    session = session_for(session_id, active=True)
    if session["status"] in ("AWAITING_APPROVAL", "DECISION_REQUIRED"):
        raise HTTPException(409, "Resolve the pending decision first.")
    if not public_ai_settings()["audio_configured"]:
        raise HTTPException(503, "Microphone input needs ASSEMBLYAI_API_KEY or GROQ_API_KEY. AI/ML text responses work without either; type the receptionist response instead.")
    audio_bytes = await file.read(10 * 1024 * 1024 + 1)
    if len(audio_bytes) > 10 * 1024 * 1024:
        raise HTTPException(413, "Recording exceeds 10 MB. Please record a shorter response.")
    try:
        text = await run_in_threadpool(transcribe_audio, audio_bytes, file.filename or "audio.webm")
    except Exception:
        raise HTTPException(502, "Audio transcription failed. Try again or type the response.")
    result = await run_in_threadpool(evaluate_turn, session_id, IncomingCallerTurn(caller_text=text or ""))
    return {"transcribed_text": text, **result}


@app.post("/api/session/{session_id}/user-action")
@serialized_session
def handle_user_action(session_id: str, action: UserDecisionAction):
    session = session_for(session_id, active=True)
    fingerprint = request_fingerprint("draft", action)
    replay = previous_result(session, action.request_id, fingerprint)
    if replay:
        return replay
    if not action.chosen_action.strip():
        raise HTTPException(400, "Choose an action or type a response.")
    if action.response_mode == "verbatim" and not (action.custom_instruction or "").strip():
        raise HTTPException(400, "Write a reply before preparing it.")
    revision = session.get("control_revision", 0)
    try:
        staged = generate_staged_draft(deepcopy(session), action)
    except Exception:
        raise HTTPException(502, "The AI could not draft your response. Please try again.")
    with state_lock:
        assert_current(session, revision)
        session["pending_draft"] = staged.english_response
        session["pending_draft_id"] = uuid.uuid4().hex
        session["pending_translation"] = staged.translated_response
        session["pending_completion"] = False
        session["pending_action"] = {"action_chosen": action.chosen_action, "summary": staged.action_summary, "custom_instruction": action.custom_instruction}
        session["status"] = "AWAITING_APPROVAL"
        result = {"status": session["status"], "staged_draft": staged.model_dump(), "draft_id": session["pending_draft_id"]}
        remember_result(session, action.request_id, fingerprint, result)
        return result


@app.post("/api/session/{session_id}/approve")
@serialized_session
def approve_and_speak(session_id: str, approval: DraftApproval | None = None):
    with state_lock:
        session = session_for(session_id)
        receipt = session.get("last_approval")
        if approval and approval.draft_id and receipt and receipt["draft_id"] == approval.draft_id:
            if receipt["revision"] == session.get("control_revision", 0):
                return deepcopy(receipt["result"])
        session_for(session_id, active=True)
        text = session.get("pending_draft")
        if not text or session["status"] != "AWAITING_APPROVAL":
            raise HTTPException(409, "No pending draft found to approve.")
        if approval and approval.draft_id and approval.draft_id != session.get("pending_draft_id"):
            raise HTTPException(409, "The draft changed. Review the current reply before approving it.")
        draft_id = session.get("pending_draft_id")
        action = session.get("pending_action")
        if action:
            record(session, action["action_chosen"], action["summary"], approved=True)
            chosen = action["action_chosen"].lower().strip()
            if re.match(r"^(?:approve|accept)\b", chosen):
                session.setdefault("approved_prices", []).extend(quoted_prices(chosen))
            if re.match(r"^(?:approve|confirm|accept)\b", chosen) and re.search(r"\b(?:booking|reservation|agreement|check-in)\b", chosen):
                session["commitment_authorized"] = True
        append_turn(session, "agent", text, session.get("pending_translation"))
        # A live voice call may be holding the line for exactly this approval.
        queue_voice_speech(session, text)
        session["status"] = "COMPLETED" if session.get("pending_completion") else "IN_PROGRESS"
        if session["status"] == "COMPLETED":
            session["ended_at"] = time.time()
        session.update(pending_draft=None, pending_draft_id=None, pending_action=None,
                       pending_completion=False, paused=False)
        session.pop("last_decision", None)
        result = {"status": "APPROVED", "session_status": session["status"], "speak_text": text}
        session["last_approval"] = {"draft_id": draft_id, "revision": session.get("control_revision", 0), "result": result}
        return result


@app.post("/api/session/{session_id}/reject")
@serialized_session
def reject_draft(session_id: str):
    with state_lock:
        session = session_for(session_id, active=True)
        session.update(pending_draft=None, pending_draft_id=None, pending_action=None, pending_completion=False, pending_translation=None)
        session["status"] = "DECISION_REQUIRED" if session.get("last_decision") or session.get("paused") else "IN_PROGRESS"
        return {"status": session["status"]}


@app.post("/api/session/{session_id}/interrupt")
def interrupt_call(session_id: str):
    # This control deliberately does NOT wait for an in-flight model request.
    # A revision check invalidates that request when its response arrives.
    with state_lock:
        session = session_for(session_id, active=True)
        if session.get("paused") and not session.get("pending_draft"):
            return {"status": session["status"], "control_revision": session.get("control_revision", 0)}
        session["control_revision"] = session.get("control_revision", 0) + 1
        session.update(pending_draft=None, pending_draft_id=None, pending_action=None,
                       pending_completion=False, pending_translation=None, paused=True)
        session["status"] = "DECISION_REQUIRED"
        record(session, "Interrupted", "The user stopped speech and took control of the next response.", approved=False)
        save_session(session)
        return {"status": session["status"], "control_revision": session["control_revision"]}


@app.post("/api/session/{session_id}/complete")
def complete_call(session_id: str):
    session = session_for(session_id)
    with state_lock:
        if session.get("summary"):
            return {"status": "COMPLETED", "summary": session["summary"]}
        if session["status"] != "COMPLETED":
            session["control_revision"] = session.get("control_revision", 0) + 1
        session.update(status="COMPLETED", pending_draft=None, pending_draft_id=None,
                       pending_action=None, pending_completion=False, ended_at=session.get("ended_at") or time.time())
        save_session(session)
    # End immediately, then serialize summary generation with outstanding work.
    # Any late model reply sees the ended status and cannot become spoken text.
    with session_locks.setdefault(session_id, RLock()):
        session_for(session_id)
        if session.get("summary"):
            return {"status": "COMPLETED", "summary": session["summary"]}
        try:
            summary = generate_call_summary(deepcopy(session))
        except Exception:
            raise HTTPException(502, "The call has ended and your transcript is saved. The AI summary is unavailable; retry the summary when the service recovers.")
        with state_lock:
            session["summary"] = summary.model_dump()
            save_session(session)
            return {"status": "COMPLETED", "summary": session["summary"]}


def participant_session(token):
    session_id = participant_sessions.get(token)
    if not session_id or session_id not in sessiondb:
        raise HTTPException(404, "This receptionist link has expired. Ask the presenter for a new link.")
    session = sessiondb[session_id]
    if time.time() >= session.get("participant_expires_at", session.get("created_at", 0) + 86400):
        raise HTTPException(410, "This invitation has expired. Ask the host for a new link.")
    return session_id, session


@app.get("/api/participant/{token}")
def participant_status(token: str):
    _, session = participant_session(token)
    with state_lock:
        session["participant_last_seen"] = time.time()
    # Only words already spoken cross to the business side. Never disclose
    # private limits, draft responses, approval choices, or the controller ID.
    return {
        "business": session["rules"].get("target_business", "Receptionist"),
        "control_revision": session.get("control_revision", 0),
        "paused": bool(session.get("paused")),
        "voice_choice": session.get("voice_choice") or "female",
        "live_listening_configured": live_transcription.public_listening_settings()["live_listening_configured"],
        "voice_live": bool(session.get("voice_live")),
        "status": "ended" if session["status"] == "COMPLETED" else
                  "ready" if session["status"] == "IN_PROGRESS" else "waiting",
        "transcript": [{key: row.get(key) for key in ("id", "speaker", "text", "time")}
                       for row in session["transcript_history"] if row["speaker"] in ("agent", "caller")],
    }


@app.post("/api/participant/{token}/turn")
def participant_turn(token: str, turn: IncomingCallerTurn):
    session_id, _ = participant_session(token)
    evaluate_turn(session_id, turn)
    return participant_status(token)


@app.get("/api/models")
def get_available_models():
    try:
        return {"available_models": [m.id for m in client.models.list().data]}
    except Exception:
        raise HTTPException(502, "Could not fetch available models.")


@app.websocket("/ws/call/{session_id}")
async def live_call_socket(websocket: WebSocket, session_id: str):
    if not pilot_access.ready() or not pilot_access.authenticated(websocket):
        await websocket.close(code=1008)
        return
    origin = websocket.headers.get("origin")
    if origin and urlsplit(origin).netloc != websocket.headers.get('host'):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    if session_id not in sessiondb:
        await websocket.send_json({"error": "Session does not exist. Create via /api/session/create first."})
        await websocket.close(code=1008)
        return
    try:
        while True:
            data = await websocket.receive_json()
            event = data.get("event")
            try:
                if event == "TRANSCRIPTION_CHUNK":
                    text = data.get("text", "").strip()
                    if not text:
                        continue
                    result = await run_in_threadpool(evaluate_turn, session_id, IncomingCallerTurn(caller_text=text))
                    decision = result["decision"]
                    await websocket.send_json({"event": "TRANSCRIPT_STREAM", "speaker": "caller", "text": text, "translation": decision.get("translated_caller_text")})
                    if result["status"] == "DECISION_REQUIRED":
                        await websocket.send_json({"event": "SPEAK_PHRASE", "text": decision["immediate_stalling_phrase"], "interrupt": True})
                        await websocket.send_json({"event": "DECISION_REQUIRED", "reason": decision["violation_reason"], "options": decision["suggested_user_options"], "translated_reason": decision["translated_violation_reason"], "translated_options": decision["translated_user_options"]})
                    elif result["status"] == "AWAITING_APPROVAL":
                        await websocket.send_json({"event": "STAGING_UPDATE", "draft_id": result.get("draft_id"), "staged_draft": {"english_response": decision["draft_response"], "translated_response": decision.get("translated_response")}})
                    else:
                        await websocket.send_json({"event": "SPEAK_PHRASE", "text": decision["draft_response"], "interrupt": False})
                    await websocket.send_json({"event": "STATUS_UPDATE", "status": result["status"]})
                elif event == "USER_ACTION":
                    action = UserDecisionAction(chosen_action=data.get("chosen_action", ""), custom_instruction=data.get("custom_instruction"), response_mode=data.get("response_mode", "instruction"))
                    result = await run_in_threadpool(handle_user_action, session_id, action)
                    await websocket.send_json({"event": "STAGING_UPDATE", **result})
                elif event == "APPROVE_AND_SPEAK":
                    result = await run_in_threadpool(approve_and_speak, session_id, DraftApproval(draft_id=data.get("draft_id")))
                    await websocket.send_json({"event": "SPEAK_PHRASE", "text": result["speak_text"], "interrupt": False})
                    await websocket.send_json({"event": "STATUS_UPDATE", "status": result["session_status"]})
                elif event in ("REJECT_DRAFT", "INTERRUPT"):
                    handler = reject_draft if event == "REJECT_DRAFT" else interrupt_call
                    result = await run_in_threadpool(handler, session_id)
                    await websocket.send_json({"event": "STATUS_UPDATE", **result})
                elif event == "END_CALL":
                    result = await run_in_threadpool(complete_call, session_id)
                    await websocket.send_json({"event": "CALL_SUMMARY", **result})
                else:
                    await websocket.send_json({"error": "Unknown event."})
            except HTTPException as error:
                await websocket.send_json({"error": error.detail, "status_code": error.status_code})
            except (ValueError, TypeError):
                await websocket.send_json({"error": "Invalid event payload."})
    except WebSocketDisconnect:
        pass


FRONTEND = Path(__file__).parent / "frontend"


class RevalidatedStaticFiles(StaticFiles):
    """Serves the frontend so browsers always check for a newer version.

    Without this the browser keeps its cached copy of app.js after an update,
    which looks exactly like a change that never happened. The ETag makes the
    check cheap: an unchanged file still answers 304 with no body.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/assets", RevalidatedStaticFiles(directory=FRONTEND), name="assets")


def page(filename):
    """An HTML entry point, never cached, so it always pulls current scripts."""
    return FileResponse(FRONTEND / filename, headers={"Cache-Control": "no-cache"})


@app.get("/receptionist/{token}", include_in_schema=False)
def receptionist_page(token: str):
    return page("receptionist.html")

@app.get("/login", include_in_schema=False)
def login_page():
    return page("login.html")


@app.get("/", include_in_schema=False)
@app.get("/demo", include_in_schema=False)
@app.get("/app", include_in_schema=False)
def frontend():
    return page("index.html")


# --- Live voice calls through the AssemblyAI Voice Agent API ----------------
# AssemblyAI listens and speaks; CallBridge decides every word by serving as
# the agent's language model. The human approval gate is preserved by holding
# the model's reply stream open while the controller decides.

def queue_voice_speech(session, text):
    """Hands an approved sentence to a live voice call that is waiting for it."""
    if text and session.get("voice_live"):
        session.setdefault("voice_outbox", []).append(text)


def take_voice_speech(session_id):
    """Removes and returns the next approved sentence, or None if none is ready."""
    with state_lock:
        session = sessiondb.get(session_id)
        outbox = session.get("voice_outbox") if session else None
        return outbox.pop(0) if outbox else None


def voice_call_ended(session_id):
    """True once there is no point holding the line open any longer."""
    with state_lock:
        session = sessiondb.get(session_id)
        return not session or session["status"] == "COMPLETED" or not session.get("voice_live")


def authorize_voice_bridge(request: Request):
    """Only AssemblyAI, carrying the secret we gave it, may drive this endpoint."""
    header = request.headers.get("authorization") or request.headers.get("x-api-key") or ""
    presented = header[7:] if header[:7].lower() == "bearer " else header
    if not secrets.compare_digest(presented.strip(), voice_agent.bridge_token()):
        raise HTTPException(401, "Unauthorized.")


def voice_turn_plan(session_id, caller_text):
    """Runs one receptionist turn through the normal engine, guardrails included.

    Returns either something to say now, or the holding phrase to say while the
    human decides.
    """
    session = session_for(session_id, active=True)
    stall = "One moment while I check on that with my client."
    if session["status"] in ("AWAITING_APPROVAL", "DECISION_REQUIRED"):
        # A decision is already on the controller's screen; never start a second.
        return {"speak_now": None, "stall": voice_agent.HOLDING_PHRASES[0]}
    try:
        result = evaluate_turn(session_id, IncomingCallerTurn(caller_text=caller_text))
    except Exception as error:
        # Someone is on the phone. A failed turn must still produce a spoken
        # holding line rather than dead air, whatever went wrong behind it.
        detail = error.detail if isinstance(error, HTTPException) else error
        print(f"[voice] turn could not be evaluated: {detail}")
        return {"speak_now": None, "stall": stall}
    decision = result["decision"]
    if result["status"] in ("IN_PROGRESS", "COMPLETED"):
        return {"speak_now": decision.get("draft_response") or "", "stall": None}
    spoken_stall = decision.get("immediate_stalling_phrase") or stall
    if result["status"] == "AWAITING_APPROVAL":
        # DECISION_REQUIRED already logged its stalling phrase; assist mode did
        # not, and the transcript must match what the receptionist actually hears.
        with state_lock:
            append_turn(session, "agent", spoken_stall)
            save_session(session)
    return {"speak_now": None, "stall": spoken_stall}


async def voice_reply_stream(session_id, caller_text, completion_id, model):
    """Streams the agent's spoken reply, pausing for approval when required."""
    plan = await run_in_threadpool(voice_turn_plan, session_id, caller_text)
    if plan["speak_now"] is not None:
        for event in voice_agent.speech_events(plan["speak_now"], completion_id, model, first=True):
            yield event
        for event in voice_agent.finish_events(completion_id, model):
            yield event
        return

    # Fill the silence immediately, then hold the line open for the human.
    for event in voice_agent.speech_events(plan["stall"], completion_id, model, first=True):
        yield event

    deadline = time.monotonic() + voice_agent.approval_timeout()
    next_reassurance = time.monotonic() + 30
    reassurances = list(voice_agent.HOLDING_PHRASES)
    while time.monotonic() < deadline:
        approved = await run_in_threadpool(take_voice_speech, session_id)
        if approved:
            for event in voice_agent.speech_events(approved, completion_id, model):
                yield event
            break
        if voice_call_ended(session_id):
            break
        if time.monotonic() >= next_reassurance and reassurances:
            for event in voice_agent.speech_events(reassurances.pop(0), completion_id, model):
                yield event
            next_reassurance = time.monotonic() + 40
        await asyncio.sleep(0.25)
    else:
        for event in voice_agent.speech_events(voice_agent.FALLBACK_AFTER_TIMEOUT, completion_id, model):
            yield event
    for event in voice_agent.finish_events(completion_id, model):
        yield event


@app.post("/v1/voice/{session_id}/chat/completions", include_in_schema=False)
async def voice_model_bridge(session_id: str, request: Request):
    """CallBridge presented to AssemblyAI as an OpenAI-compatible model."""
    authorize_voice_bridge(request)
    session = session_for(session_id)
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "Expected a JSON chat completion request.")
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    model = str(body.get("model") or "callbridge")

    if session["status"] == "COMPLETED":
        goodbye = "Thank you very much for your help. Have a good day, goodbye."
        if not body.get("stream"):
            return voice_agent.whole_completion(goodbye, completion_id, model)
        async def farewell():
            for event in voice_agent.speech_events(goodbye, completion_id, model, first=True):
                yield event
            for event in voice_agent.finish_events(completion_id, model):
                yield event
        return StreamingResponse(farewell(), media_type="text/event-stream")

    caller_text = voice_agent.last_caller_text(body.get("messages"))
    if not caller_text:
        raise HTTPException(400, "No receptionist speech was included in the request.")

    if not body.get("stream"):
        # Not used by the Voice Agent API, which requires streaming, but it
        # makes the endpoint testable with an ordinary HTTP client.
        plan = await run_in_threadpool(voice_turn_plan, session_id, caller_text)
        return voice_agent.whole_completion(plan["speak_now"] or plan["stall"], completion_id, model)

    return StreamingResponse(
        voice_reply_stream(session_id, caller_text, completion_id, model),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@app.post("/api/session/{session_id}/voice/start")
async def start_voice_call(session_id: str):
    """Registers this call's agent with AssemblyAI and opens the voice line."""
    session = session_for(session_id, active=True)
    reason = voice_agent.unavailable_reason()
    if reason:
        raise HTTPException(503, reason)
    revision = session.get("control_revision", 0)
    if not session.get("voice_agent_id"):
        try:
            agent_id = await run_in_threadpool(voice_agent.create_agent, deepcopy(session))
        except ValueError as error:
            raise HTTPException(502, str(error))
        try:
            with state_lock:
                assert_current(session, revision)
                session["voice_agent_id"] = agent_id
        except HTTPException:
            await run_in_threadpool(voice_agent.delete_agent, agent_id)
            raise
    with state_lock:
        assert_current(session, revision)
        session["voice_live"] = True
        session["voice_outbox"] = []
        if session["status"] == "READY_TO_START":
            session["status"] = "IN_PROGRESS"
        greeting = voice_agent.greeting_for(session)
        # The greeting is spoken by AssemblyAI without passing through the
        # model, so record it here or the transcript would miss the opening.
        if not any(turn["speaker"] == "agent" for turn in session["transcript_history"]):
            append_turn(session, "agent", greeting)
            record(session, "Live voice call started", f"Opening line: {greeting}", approved=True)
        save_session(session)
    return {"voice_live": True, "status": session["status"], "greeting": greeting,
            "participant_path": f"/receptionist/{session['participant_token']}",
            **voice_agent.public_voice_settings()}


@app.post("/api/session/{session_id}/voice/stop")
async def stop_voice_call(session_id: str):
    session = session_for(session_id)
    with state_lock:
        agent_id = session.pop("voice_agent_id", None)
        session["voice_live"] = False
        session["voice_outbox"] = []
        save_session(session)
    await run_in_threadpool(voice_agent.delete_agent, agent_id)
    return {"voice_live": False, "status": session["status"]}


@app.get("/api/participant/{token}/voice")
async def participant_voice_credentials(token: str):
    """One-time token so the receptionist's browser can open the voice socket.

    The AssemblyAI key stays on the server. This token is single use, expires
    in two minutes, and caps how long the call can run.
    """
    _, session = participant_session(token)
    if not session.get("voice_live") or not session.get("voice_agent_id"):
        return {"voice_live": False,
                "hint": "The other participant has not started the live voice call yet."}
    try:
        temporary_token = await run_in_threadpool(voice_agent.session_token)
    except ValueError as error:
        raise HTTPException(502, str(error))
    return {"voice_live": True, "agent_id": session["voice_agent_id"],
            "websocket_url": voice_agent.AGENTS_WEBSOCKET, "token": temporary_token,
            "business": session["rules"].get("target_business", "Receptionist"),
            **voice_agent.public_voice_settings()}


# --- Spoken replies in a natural voice (free, via edge-tts) -----------------
# The browser's own speech sounds robotic. These routes read a line aloud in a
# Microsoft neural voice instead, at no cost and with no API key. They will
# only speak words already in the conversation, so they cannot be used to put
# new words in the agent's mouth.

def speakable_texts(session):
    """Every line that has genuinely been said, plus the draft under review.

    The draft is included so its audio can be prepared while the human reads
    it, and it still cannot be heard by the other side until it is approved.
    """
    spoken = {turn["text"] for turn in session["transcript_history"] if turn.get("text")}
    if session.get("pending_draft"):
        spoken.add(session["pending_draft"])
    return spoken


async def speak_from_session(session, request: SpeechRequest):
    text = request.text.strip()
    if text not in speakable_texts(session):
        raise HTTPException(403, "Only words already in this conversation can be spoken.")
    try:
        audio = await tts.synthesize(text, tts.resolve_voice(session.get("voice_choice")))
    except Exception as error:
        # The page falls back to the browser's own voice when this fails.
        print(f"[tts] could not synthesize: {error}")
        raise HTTPException(502, "The natural voice is unavailable right now.")
    return Response(content=audio, media_type="audio/mpeg",
                    headers={"Cache-Control": "private, max-age=600"})


@app.post("/api/session/{session_id}/speech")
async def speak_session_line(session_id: str, request: SpeechRequest):
    return await speak_from_session(session_for(session_id), request)


@app.post("/api/participant/{token}/speech")
async def speak_participant_line(token: str, request: SpeechRequest):
    _, session = participant_session(token)
    # The receptionist may only hear lines that were actually spoken to them,
    # never a draft the controller is still reviewing.
    text = request.text.strip()
    if text not in {turn["text"] for turn in session["transcript_history"] if turn["speaker"] == "agent"}:
        raise HTTPException(403, "Only words already spoken in this conversation can be read aloud.")
    return await speak_from_session(session, request)


@app.post("/api/session/{session_id}/voice-choice")
def choose_agent_voice(session_id: str, choice: VoiceChoice):
    """Picks the agent's voice for this call, for both the free voice and a live call.

    Stored on the session rather than in the browser, so the receptionist hears
    the same voice the controller chose.
    """
    with state_lock:
        session = session_for(session_id, active=True)
        session["voice_choice"] = choice.voice
        save_session(session)
    return {"status": "ok", **tts.public_tts_settings(choice.voice)}


# The guided demo has no server-side call, and deliberately lets the presenter
# type any reply, so its words cannot be checked against a transcript. It is
# capped and rate limited instead, so it cannot be used as a speech service.
DEMO_SPEECH_LIMIT = 600
DEMO_SPEECH_PER_MINUTE = 40
_demo_speech_hits = {}


def allow_demo_speech(client):
    now = time.monotonic()
    with state_lock:
        recent = [moment for moment in _demo_speech_hits.get(client, []) if now - moment < 60]
        if len(recent) >= DEMO_SPEECH_PER_MINUTE:
            return False
        recent.append(now)
        _demo_speech_hits[client] = recent
        while len(_demo_speech_hits) > 256:
            _demo_speech_hits.pop(next(iter(_demo_speech_hits)))
        return True


@app.post("/api/demo/speech")
async def speak_demo_line(request: Request, speech: SpeechRequest):
    """Natural voice for the guided demo, which runs without an API key or a call."""
    text = speech.text.strip()
    if not text:
        raise HTTPException(400, "There is nothing to speak.")
    if len(text) > DEMO_SPEECH_LIMIT:
        raise HTTPException(413, f"Demo speech is limited to {DEMO_SPEECH_LIMIT} characters.")
    if not allow_demo_speech(request.client.host if request.client else "unknown"):
        raise HTTPException(429, "Too many speech requests. Wait a moment and try again.")
    try:
        audio = await tts.synthesize(text, tts.resolve_voice(speech.voice))
    except Exception as error:
        print(f"[tts] could not synthesize demo line: {error}")
        raise HTTPException(502, "The natural voice is unavailable right now.")
    return Response(content=audio, media_type="audio/mpeg",
                    headers={"Cache-Control": "private, max-age=600"})


# --- Live listening: the other side just talks ------------------------------
# AssemblyAI's streaming API transcribes continuously, so a sentence arrives
# the moment it is finished instead of after a record-and-upload round trip.
# The browser opens that socket itself, so no tunnel is needed; only this
# short-lived token comes from here, and the API key never leaves the server.

async def listening_credentials():
    reason = live_transcription.unavailable_reason()
    if reason:
        raise HTTPException(503, reason)
    try:
        token = await run_in_threadpool(live_transcription.listening_token)
    except ValueError as error:
        raise HTTPException(502, str(error))
    return {"listening": True, "token": token,
            "websocket_url": live_transcription.websocket_url(),
            **live_transcription.public_listening_settings()}


@app.get("/api/session/{session_id}/listen")
async def listen_as_controller(session_id: str):
    """For the presenter's own microphone, when they hold the call themselves."""
    session_for(session_id, active=True)
    return await listening_credentials()


@app.get("/api/participant/{token}/listen")
async def listen_as_participant(token: str):
    """For the business side, so they can speak instead of typing."""
    _, session = participant_session(token)
    if session["status"] == "COMPLETED":
        raise HTTPException(409, "This conversation has ended.")
    return await listening_credentials()


@app.post("/api/session/{session_id}/invitation/renew")
@serialized_session
def renew_invitation(session_id: str):
    with state_lock:
        session = session_for(session_id, active=True)
        if session.get("voice_live"):
            raise HTTPException(409, "End the live voice call before replacing its invitation.")
        participant_sessions.pop(session.get("participant_token"), None)
        token = secrets.token_urlsafe(32)
        session.update(participant_token=token, participant_expires_at=time.time() + 86400)
        session.pop("participant_last_seen", None)
        participant_sessions[token] = session_id
        return {"participant_path": f"/receptionist/{token}", "participant_expires_at": session["participant_expires_at"]}


@app.post("/api/session/{session_id}/invitation/revoke")
@serialized_session
def revoke_invitation(session_id: str):
    with state_lock:
        session = session_for(session_id)
        if session.get("voice_live"):
            raise HTTPException(409, "End the live voice call before revoking its invitation.")
        participant_sessions.pop(session.get("participant_token"), None)
        session.update(participant_token=None, participant_expires_at=0)
        session.pop("participant_last_seen", None)
        return {"status": "revoked"}


@app.post("/api/session/{session_id}/delete")
def delete_conversation(session_id: str):
    # Serialize with model work so a late result cannot resurrect deleted data.
    with session_locks.setdefault(session_id, RLock()), state_lock:
        session = session_for(session_id)
        if session["status"] != "COMPLETED" or session.get("voice_live"):
            raise HTTPException(409, "End the conversation and live voice call before deleting it.")
        participant_sessions.pop(session.get("participant_token"), None)
        session["deleted"] = True
        session["control_revision"] = session.get("control_revision", 0) + 1
        store.delete(session_id)
        del sessiondb[session_id]
    return {"status": "deleted"}
