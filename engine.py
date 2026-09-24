import os
import json
from localization import model_language
from openai import OpenAI
from ai_config import configured_key, provider_key, provider_url, get_model, output_limit, input_limit

from models import (
    ExtractedRules,
    TurnDecision,
    UserDecisionAction,
    StagedResponse,
    FinalSummary,
)

import io
import assemblyai as aai

aai_key = configured_key("ASSEMBLYAI_API_KEY")
if aai_key:
    aai.settings.api_key = aai_key.strip()

groq_key = configured_key("GROQ_API_KEY")

client = OpenAI(
    base_url=provider_url(),
    api_key=provider_key() or "not-configured",
    timeout=30.0,
    max_retries=0,
)


def budgeted_completion(**kwargs):
    """One bounded request; no silent retries or more expensive model fallback."""
    characters = sum(len(str(message.get("content", ""))) for message in kwargs.get("messages", []))
    if characters > input_limit():
        raise ValueError("This request exceeds the configured input budget. Use a shorter task or call.")
    response = client.chat.completions.create(**kwargs, max_tokens=output_limit())
    if response.choices[0].finish_reason == "length":
        raise ValueError("The response hit the token cap. No automatic retry was made.")
    return response

def extract_rules_from_prompt(
    prompt: str, user_language: str = "English"
) -> ExtractedRules:
    system_instruction = (
        "You are CallBridge's goal parsing engine. Your job is to extract the core intent, "
        "target business, specific action details (items to order, services to book, or request specifics), "
        "a natural opening phrase to speak when the receptionist answers, "
        "all numeric or time-based constraints, and forbidden disclosures from the user's instructions.\n"
        "Return strictly valid JSON matching this schema:\n"
        "{\n"
        '  "intent": "string",\n'
        '  "target_business": "string",\n'
        '  "action_details": "string or null",\n'
        '  "opening_phrase": "Short, polite sentence to speak when calling the business (e.g. Hello, I am calling to schedule an appointment for Tuesday before 4 PM on behalf of my client.)",\n'
        '  "constraints": [{"parameter": "string", "operator": "max|min|exact", "value": "string", "unit": "string"}],\n'
        '  "forbidden_disclosures": ["string"],\n'
        '  "special_notes": "string or null"\n'
        "}"
    )

    response = budgeted_completion(
        model=get_model(),
        messages=[
            {"role": "system", "content": system_instruction},
            {
                "role": "user",
                "content": f"User Language: {user_language}\nPrompt: {prompt}",
            },
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
    )

    raw_json = json.loads(response.choices[0].message.content)
    return ExtractedRules(**raw_json)

def preference_instruction(session):
    preferences = session.get("preferences") or {}
    tone = preferences.get("tone", "professional")
    length = preferences.get("reply_length", "concise")
    return (
        f"REPLY STYLE: {tone}; {length}. Use short direct sentences when concise; include relevant explanation when detailed.\n"
        f"ADDITIONAL USER INSTRUCTIONS: {json.dumps(preferences.get('additional_instructions', ''))}\n"
        "These preferences never override approval, spending, or disclosure rules. Do not invent details to satisfy a style preference.\n"
    )


def evaluate_caller_turn(caller_text: str, session: dict) -> TurnDecision:
    rules = session["rules"]
    mode = session.get("mode", "delegate")
    history = session.get("transcript_history", [])
    ledger = [entry for entry in session.get("decision_ledger", []) if entry.get("approved", True)]

    # Format recent history for conversational memory
    recent_history = history[-8:] if history else []
    conversation_context = "\n".join(
        [f"- {'RECEPTIONIST' if turn['speaker'] == 'caller' else 'CALLBRIDGE (YOU)'}: {turn['text']}" for turn in recent_history]
    )
    
    ledger_context = "\n".join(
        [f"- {entry.get('action_chosen')} ({entry.get('summary', '')})" for entry in ledger]
    )

    assist_mode_instruction = (
        "- In ASSIST mode: The user requires human review on all commitments. If the receptionist asks to confirm an order, "
        "finalize an appointment, provide personal details, or agree to pricing, set is_dealbreaker=true and suggest options "
        "so the user approves the commitment.\n"
        if mode == "assist" else ""
    )

    system_instruction = (
        "CRITICAL ROLE DEFINITION:\n"
        "You are CallBridge, an AI agent placing an OUTBOUND phone call TO a business on behalf of a human client (the patient/customer).\n"
        "The person speaking to you on the phone is the BUSINESS RECEPTIONIST / STAFF.\n"
        "You are NEVER the receptionist. You must ALWAYS speak as the caller representing the client.\n\n"
        f"TARGET BUSINESS: {rules.get('target_business', 'Business')}\n"
        f"CLIENT'S INITIAL GOAL: {rules.get('action_details') or rules.get('intent')}\n"
        f"INITIAL CONSTRAINTS: {json.dumps(rules.get('constraints', []), indent=2)}\n"
        f"CLIENT OVERRIDES & APPROVED COUNTER-OFFERS SO FAR:\n{ledger_context or '(None yet)'}\n\n"
        f"FORBIDDEN INFORMATION TO NEVER SHARE:\n{json.dumps(rules.get('forbidden_disclosures', []), indent=2)}\n"
        f"OPERATIONAL MODE: {mode.upper()}\n\n"
        f"{preference_instruction(session)}"
        "CONVERSATION HISTORY SO FAR:\n"
        f"{conversation_context or '(Call just started)'}\n\n"
        "EVALUATION INSTRUCTIONS:\n"
        "Safety takes precedence over completion: a booking confirmation never overrides an unapproved price, commitment, or disclosure.\n"
        "In BOTH modes, agreeing to a price, booking, cancellation, or sharing personal information requires explicit human approval.\n"
        f"Include translated_caller_text and translated_response in {model_language(session.get('language', 'English'))}.\n"
        "Keep violation_reason and suggested_user_options in English as canonical actions. "
        f"Provide translated_violation_reason and translated_user_options in {model_language(session.get('language', 'English'))}. "
        "Translate every option faithfully in the same order, preserving amounts and conditions. "
        "Keep draft_response and immediate_stalling_phrase in English for the receptionist.\n"
        "1. CLIENT OVERRIDES PREVAIL: If the client already approved an alternative (e.g. moving the appointment to Monday), and the receptionist accepts, confirms, or offers that alternative, IT IS NOT A DEALBREAKER! It is an agreed success. Set is_dealbreaker=false.\n"
        "2. DETECTING CALL COMPLETION & SIGN-OFF:\n"
        "   - ONLY if the human has already approved that exact booking or agreement and the receptionist confirms it, the goal is accomplished. Otherwise raise a commitment decision card.\n"
        "     * Set call_completed=true\n"
        "     * Set is_dealbreaker=false\n"
        "     * Set draft_response to a polite confirmation and warm sign-off (e.g. 'Thank you so much! That works perfectly. Have a wonderful day, goodbye!').\n"
        "   - If the receptionist or CallBridge says goodbye to end the call, set call_completed=true.\n"
        "3. WHEN A REAL CONSTRAINT VIOLATION HAPPENS:\n"
        "   - If the receptionist proposes something violating limits that the client NEVER approved, set is_dealbreaker=true.\n"
        "   - Provide an immediate stalling phrase ('Hold on a moment while I check with my client.').\n"
        "   - Suggest 2-3 SMART, LOGICAL, and ACTIONABLE options for the client. NEVER suggest impossible or circular options (e.g. do not suggest asking for Tuesday if receptionist just said Tuesday has zero slots). Instead offer:\n"
        "     * Accept the alternative proposed by the receptionist (if reasonable)\n"
        "     * Propose a specific different day or time window\n"
        "     * Ask for cancellation waitlist or next available opening\n"
        "     * Decline and politely end the call\n"
        f"   {assist_mode_instruction}"
        "4. ROUTINE CONVERSATION:\n"
        "   - If safe, routine, or answering questions: set is_dealbreaker=false, call_completed=false, and draft the direct, natural response.\n\n"
        "Return strictly valid JSON matching this schema:\n"
        "{\n"
        '  "is_dealbreaker": false,\n'
        '  "call_completed": false,\n'
        '  "violation_parameter": null,\n'
        '  "violation_reason": null,\n'
        '  "immediate_stalling_phrase": null,\n'
        '  "suggested_user_options": [],\n'
        '  "translated_user_options": [],\n'
        '  "translated_violation_reason": null,\n'
        '  "draft_response": "Polite response to speak back to the receptionist",\n'
        '  "translated_caller_text": "Receptionist words translated into the user language",\n'
        '  "translated_response": "Draft response translated into the user language"\n'
        "}"
    )

    response = budgeted_completion(
        model=get_model(),
        messages=[
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": f"Receptionist said: \"{caller_text}\""}
        ],
        response_format={"type": "json_object"},
        temperature=0.1
    )

    raw_json = json.loads(response.choices[0].message.content)
    return TurnDecision(**raw_json)

def generate_staged_draft(session: dict, action: UserDecisionAction) -> StagedResponse:
    user_lang = model_language(session.get("language", "English"))
    rules = session.get("rules", {})
    if action.response_mode == "verbatim":
        text = (action.custom_instruction or "").strip()
        if not text:
            raise ValueError("Enter the reply you want the agent to say.")
        if user_lang == "English":
            return StagedResponse(english_response=text, translated_response=text,
                                  action_summary="User-written reply prepared without changes.")
    
    system_instruction = (
        "CRITICAL ROLE DEFINITION:\n"
        "You are CallBridge, an AI agent representing a human client on an OUTBOUND phone call TO a business receptionist.\n"
        "You are the CALLER asking for goods/services on behalf of the client. You are NEVER the receptionist.\n\n"
        f"Active Task Rules: {json.dumps(rules, indent=2)}\n"
        f"User Preferred Language: {user_lang}\n\n"
        f"{preference_instruction(session)}"
        "The user is preparing a reply, an opening, or a response to a paused call.\n"
        "Follow their selected action exactly; do not invent a conflict or change the task.\n"
        "For 'Introduce the call', use the supplied opening naturally. Never say 'I would like to introduce the call'.\n"
        "Draft a polite, natural sentence CallBridge should speak TO THE RECEPTIONIST in English.\n"
        "Also translate that exact sentence into the user's preferred language so they can verify before speaking.\n\n"
        "Return strictly valid JSON matching this schema:\n"
        "{\n"
        '  "english_response": "Polite sentence to speak to the receptionist in English",\n'
        '  "translated_response": "The same sentence translated into user\'s preferred language",\n'
        '  "action_summary": "Short 1-line summary of this action for ledger (e.g. Counter-offer drafted for Sunday)"\n'
        "}"
    )

    if action.response_mode == "verbatim":
        system_instruction = (
            "You are a faithful translator, not a negotiation agent. The user wrote the exact reply they want spoken. "
            "Translate it into English without adding goals, requests, promises, prices, politeness filler, or context. "
            "If it is already English, preserve it exactly. A greeting such as 'hi' must remain a greeting. "
            f"Also translate the same reply into {user_lang}. "
            "Return JSON with english_response, translated_response, and action_summary. "
            "The action_summary must describe this as a user-written reply, not an approved agreement."
        )
    
    user_context = (
        f"Recent conversation: {json.dumps(session.get('transcript_history', [])[-8:])}. "
        f"User selected action: '{action.chosen_action}'. "
        f"Custom notes: '{action.custom_instruction or 'None'}'."
    )
    if action.response_mode == "verbatim":
        user_context = action.custom_instruction.strip()

    response = budgeted_completion(
        model=get_model(),
        messages=[
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": user_context}
        ],
        response_format={"type": "json_object"},
        temperature=0.2
    )

    raw_json = json.loads(response.choices[0].message.content)
    return StagedResponse(**raw_json)

def generate_call_summary(session: dict) -> FinalSummary:
    history = session.get("transcript_history", [])
    ledger = session.get("decision_ledger", [])
    rules = session.get("rules", {})

    system_instruction = (
        "You are CallBridge's post-call audit and summary engine.\n"
        f"Original Task Constraints: {json.dumps(rules, indent=2)}\n"
        f"Decisions Made During Call: {json.dumps(ledger, indent=2)}\n\n"
        "Analyze the full conversation history. Produce a structured post-call summary.\n"
        "Highlight what was explicitly confirmed, what remains unresolved or uncertain, "
        "and provide an executive outcome headline.\n\n"
        "Only ledger entries with approved=true are human approvals. A quote, proposed draft, blocked request, or receptionist claim is not an agreement by the user. Never invent facts missing from the conversation.\n"
        "Return strictly valid JSON matching this schema:\n"
        "{\n"
        '  "outcome_headline": "string",\n'
        '  "outcome_subtext": "string",\n'
        '  "confirmed_items": ["item 1", "item 2"],\n'
        '  "unresolved_items": ["item 1"]\n'
        "}"
    )

    formatted_history = "\n".join(
        [f"{entry['speaker'].upper()}: {entry['text']}" for entry in history]
    )

    response = budgeted_completion(
        model=get_model(),
        messages=[
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": f"Full Conversation:\n{formatted_history}"}
        ],
        response_format={"type": "json_object"},
        temperature=0.1
    )

    raw_json = json.loads(response.choices[0].message.content)
    return FinalSummary(**raw_json)

def transcribe_audio(audio_input, filename: str = "audio.wav") -> str:
    """Transcribes audio using AssemblyAI (with Groq Whisper as fallback)."""
    # 1. Primary: AssemblyAI
    if aai_key:
        try:
            transcriber = aai.Transcriber()
            if isinstance(audio_input, bytes):
                audio_stream = io.BytesIO(audio_input)
                transcript = transcriber.transcribe(audio_stream)
            else:
                transcript = transcriber.transcribe(audio_input)

            if transcript and transcript.text:
                return transcript.text.strip()
        except Exception as e:
            print(f"AssemblyAI transcription notice: {e}. Trying fallback...")

    # Speech credentials stay independent of the text provider. Never send an
    # AI/ML key to Groq or silently spend AI/ML credits on a speech model.
    if not groq_key:
        raise ValueError("Microphone input needs ASSEMBLYAI_API_KEY or GROQ_API_KEY. You can type responses instead.")

    # 2. Fallback: Groq Whisper, using its own credential and model.
    if isinstance(audio_input, bytes):
        raw_bytes = audio_input
    elif hasattr(audio_input, "read"):
        raw_bytes = audio_input.read()
    else:
        with open(audio_input, "rb") as f:
            raw_bytes = f.read()

    with OpenAI(base_url="https://api.groq.com/openai/v1", api_key=groq_key, timeout=30.0, max_retries=0) as speech_client:
        transcription = speech_client.audio.transcriptions.create(
            file=(filename, raw_bytes),
            model="whisper-large-v3-turbo",
        )
    return transcription.text.strip()
