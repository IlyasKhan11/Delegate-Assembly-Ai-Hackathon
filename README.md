# CallBridge 🌉📞

> **Autonomous Outbound Negotiation & Phone Agent with Human-in-the-Loop Safeguards**

CallBridge is an AI agent that places outbound phone calls to businesses (dental clinics, restaurants, customer service) on behalf of a human client. When negotiation encounters constraint conflicts (unwanted hours, unexpected fees, or requests for sensitive info), CallBridge **stalls the receptionist live on the phone**, escalates a **Red Decision Card** to the human client, drafts a verified counter-offer, and speaks it back to complete the booking.

---

## 🌟 Key Features

* **Natural Goal Parsing**: Takes natural language instructions (e.g., *"Book a dental checkup for Tuesday before 4 PM, max $30, never share credit card"*) and extracts structured limits and opening statements.
* **Autonomous Outbound Caller**: Places the outbound call, introduces the client's request, and converses with the business receptionist.
* **Dealbreaker Interception**: Immediately catches constraint violations and forbidden disclosures.
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
git clone https://github.com/Shariq-duet/CallBridge.git
cd CallBridge
pip install -r requirements.txt # or install fastapi, uvicorn, openai, assemblyai, edge-tts, sounddevice, scipy
```

### 2. Configure Environment Variables

Create a `.env` file based on `.env.example`:
```ini
GROQ_API_KEY=your_groq_api_key
GROQ_MODEL=openai/gpt-oss-20b
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

## 📚 Documentation for Frontend Developers

See [FRONTEND_INTEGRATION_GUIDE.md](FRONTEND_INTEGRATION_GUIDE.md) and [API_CONTRACT.md](API_CONTRACT.md) for full REST & WebSocket endpoint specifications, state machine transitions, and React/Next.js integration blueprints.

---

## 📄 License
MIT License
