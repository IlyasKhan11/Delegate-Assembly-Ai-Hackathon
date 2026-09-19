"""
CallBridge Interactive Roleplay & Mic Test Client
--------------------------------------------------
Simulate the BUSINESS RECEPTIONIST by speaking into your PC microphone!
CallBridge acts as the CALLER representing the client.

Scenario: Booking a Dentist Appointment for Tuesday before 4 PM.
- You play the Receptionist (can stutter, ask for repetition, offer wrong times like 5:30 PM).
- CallBridge calls you, speaks the opening line, listens to you, catches violations,
  stalls if you break the rules, and negotiates!
"""

import sys
import os
import time
import io
import json
import asyncio
import requests
import numpy as np
import sounddevice as sd
from scipy.io import wavfile

from tts_player import speak_text_aloud

BASE_URL = "http://127.0.0.1:8000"
SAMPLE_RATE = 16000

def record_audio(duration_seconds: int = 8) -> bytes:
    """Records audio from microphone for N seconds and returns WAV bytes."""
    print(f"\n🎙️  Receptionist speaking... Recording for {duration_seconds} seconds! Speak into your mic now:")
    audio_data = sd.rec(
        int(duration_seconds * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype=np.int16,
    )
    for i in range(duration_seconds, 0, -1):
        print(f"   [{i}s remaining...]", end="\r")
        time.sleep(1)
    sd.wait()
    print("   Done recording! Transcribing via AssemblyAI...      ")

    buffer = io.BytesIO()
    wavfile.write(buffer, SAMPLE_RATE, audio_data)
    buffer.seek(0)
    return buffer.read()

def create_dentist_session() -> tuple[str, str]:
    """Creates a dentist appointment booking session."""
    payload = {
        "raw_prompt": (
            "Call Dr. Smith Dental Clinic to schedule a dental checkup for this Tuesday before 4 PM. "
            "Do not accept any time after 4 PM, and do not share my credit card or SSN."
        ),
        "user_language": "English",
        "mode": "delegate"
    }
    res = requests.post(f"{BASE_URL}/api/session/create", json=payload)
    if res.status_code != 200:
        print(f"Failed to create session: {res.text}")
        sys.exit(1)
    data = res.json()
    rules = data["extracted_rules"]
    opening = rules.get("opening_phrase") or "Hello, I am calling on behalf of my client to book an appointment for Tuesday before 4 PM."
    
    print("\n✅ Session Created:", data["session_id"])
    print(f"🎯 Business: {rules['target_business']}")
    print(f"📋 Goal: {rules.get('action_details') or rules['intent']}")
    print(f"🛑 Constraints: {rules['constraints']}")
    print(f"🗣️ CallBridge Opening Phrase: \"{opening}\"")
    return data["session_id"], opening

def create_custom_session(prompt: str, lang: str = "English", mode: str = "delegate") -> tuple[str, str]:
    payload = {"raw_prompt": prompt, "user_language": lang, "mode": mode}
    res = requests.post(f"{BASE_URL}/api/session/create", json=payload)
    if res.status_code != 200:
        print(f"Failed: {res.text}")
        sys.exit(1)
    data = res.json()
    opening = data["extracted_rules"].get("opening_phrase") or "Hello, I am calling on behalf of my client."
    return data["session_id"], opening

async def main():
    print("=" * 65)
    print("      CallBridge Interactive Roleplay Test (Receptionist Mic)")
    print("=" * 65)

    try:
        requests.get(f"{BASE_URL}/api/models", timeout=3)
    except Exception:
        print("❌ Error: CallBridge backend is not running!")
        print("   Please run in Conda: uvicorn app:app --reload --port 8000")
        return

    print("\nSelect Scenario:")
    print("  [1] Dentist Appointment (Tuesday before 4 PM) [RECOMMENDED]")
    print("  [2] Pizza Order (Mario's Pizza under $30)")
    print("  [3] Custom Goal")
    scen_choice = input("Enter choice (default 1): ").strip() or "1"

    if scen_choice == "1":
        session_id, opening_phrase = create_dentist_session()
    elif scen_choice == "2":
        session_id, opening_phrase = create_custom_session(
            "Order 2 pepperoni pizzas from Mario's Pizza under $30, delivery under 45 minutes. Never share credit card info.",
            lang="Urdu"
        )
    else:
        p = input("Enter your custom goal prompt: ").strip()
        session_id, opening_phrase = create_custom_session(p)

    # Speak CallBridge's opening statement to start the call!
    print("\n📞 Phone Ringing... Call Connected!")
    print(f"🤖 CallBridge speaks opening line: \"{opening_phrase}\"")
    print("🔊 Playing CallBridge voice through speakers...")
    await speak_text_aloud(opening_phrase)

    print("\nNow YOU act as the receptionist at the clinic/business.")
    print("Tip: You can stutter, ask for repetition, or offer 5:30 PM to test dealbreaker!")

    while True:
        print("\n" + "-" * 55)
        print("Receptionist Actions:")
        print("  [1] Speak as Receptionist (Record 5 seconds)")
        print("  [2] Speak as Receptionist (Custom duration)")
        print("  [3] Type Receptionist response (No mic)")
        print("  [4] Check Session Status & Ledger")
        print("  [q] End Call / Quit")
        choice = input("Select: ").strip().lower() or "1"

        if choice == "q":
            print("\nEnding call and generating post-call audit summary...")
            comp = requests.post(f"{BASE_URL}/api/session/{session_id}/complete").json()
            summary = comp.get("summary", {})
            print("\n" + "=" * 50)
            print("📋 POST-CALL REPORT CARD:")
            print(f"  Headline: {summary.get('outcome_headline')}")
            print(f"  Subtext:  {summary.get('outcome_subtext')}")
            print(f"  Confirmed: {summary.get('confirmed_items')}")
            print(f"  Unresolved: {summary.get('unresolved_items')}")
            print("=" * 50)
            break

        audio_bytes = None
        receptionist_text = None

        if choice in ["1", "2"]:
            dur = 5 if choice == "1" else int(input("Enter duration (seconds): ") or 5)
            input("Press [Enter] when ready to speak into mic...")
            audio_bytes = record_audio(dur)

            files = {"file": ("receptionist_turn.wav", audio_bytes, "audio/wav")}
            res = requests.post(f"{BASE_URL}/api/session/{session_id}/audio-turn", files=files)
            if res.status_code != 200:
                print(f"Error: {res.text}")
                continue
            data = res.json()
            receptionist_text = data.get("transcribed_text")
            decision = data.get("decision", {})
            status = data.get("status")

        elif choice == "3":
            receptionist_text = input("Type receptionist words: ").strip()
            if not receptionist_text:
                continue
            res = requests.post(
                f"{BASE_URL}/api/session/{session_id}/evaluate-turn",
                json={"caller_text": receptionist_text}
            )
            if res.status_code != 200:
                print(f"Error: {res.text}")
                continue
            data = res.json()
            decision = data.get("decision", {})
            status = data.get("status")

        elif choice == "4":
            res = requests.get(f"{BASE_URL}/api/session/{session_id}/status")
            print("\nSession Status:", json.dumps(res.json(), indent=2))
            continue
        else:
            continue

        # Display Turn Results
        print("\n" + "=" * 45)
        print(f"👩‍💼 Receptionist (Transcribed): \"{receptionist_text}\"")
        print(f"📊 Status: {status}")
        print(f"🚨 Dealbreaker Detected: {decision.get('is_dealbreaker')}")

        if decision.get("is_dealbreaker"):
            print(f"⚠️  Violation: {decision.get('violation_reason')}")
            stalling = decision.get("immediate_stalling_phrase")
            print(f"⏸️  CallBridge Stalling Phrase: \"{stalling}\"")
            print(f"🔘 Human UI Options: {decision.get('suggested_user_options')}")

            # AI speaks stalling phrase to keep receptionist on the line!
            if stalling:
                print("\n🔊 CallBridge is speaking stalling line to receptionist...")
                await speak_text_aloud(stalling)

            # Prompt human user for decision
            print("\n--- [HUMAN CLIENT IN THE LOOP] ---")
            options = decision.get("suggested_user_options", [])
            for idx, opt in enumerate(options, 1):
                print(f"   [{idx}] {opt}")
            print("   [C] Type custom instructions")
            user_choice = input("Select action (or Enter to skip): ").strip()

            chosen_action = ""
            custom_note = None
            if user_choice.isdigit() and 1 <= int(user_choice) <= len(options):
                chosen_action = options[int(user_choice) - 1]
            elif user_choice.lower() == "c":
                chosen_action = "Custom Negotiation"
                custom_note = input("Enter your custom counter-instruction: ")

            if chosen_action:
                act_res = requests.post(
                    f"{BASE_URL}/api/session/{session_id}/user-action",
                    json={"chosen_action": chosen_action, "custom_instruction": custom_note}
                ).json()
                draft = act_res.get("staged_draft", {})
                print(f"\n📝 Staged English Counter: \"{draft.get('english_response')}\"")
                print(f"🌐 Staged Translated: \"{draft.get('translated_response')}\"")

                appr = input("\nApprove and speak out loud to receptionist? (y/n): ").strip().lower()
                if appr == "y":
                    appr_res = requests.post(f"{BASE_URL}/api/session/{session_id}/approve").json()
                    speak_phrase = appr_res.get("speak_text")
                    print(f"🔊 CallBridge speaking: \"{speak_phrase}\"")
                    await speak_text_aloud(speak_phrase)

        else:
            draft = decision.get("draft_response")
            print(f"🤖 CallBridge Speaks to Receptionist: \"{draft}\"")
            if draft:
                print("\n🔊 Playing CallBridge response...")
                await speak_text_aloud(draft)

        print("=" * 45)

        if decision.get("call_completed") or status == "COMPLETED":
            print("\n🎉 CALL COMPLETED! The appointment/order has been finalized.")
            print("Fetching post-call audit summary...")
            try:
                comp = requests.post(f"{BASE_URL}/api/session/{session_id}/complete").json()
                summary = comp.get("summary", {})
                print("\n" + "=" * 50)
                print("📋 POST-CALL REPORT CARD:")
                print(f"  Headline: {summary.get('outcome_headline')}")
                print(f"  Subtext:  {summary.get('outcome_subtext')}")
                print(f"  Confirmed: {summary.get('confirmed_items')}")
                print(f"  Unresolved: {summary.get('unresolved_items')}")
                print("=" * 50)
            except Exception as e:
                print(f"Summary notice: {e}")
            break

if __name__ == "__main__":
    asyncio.run(main())
