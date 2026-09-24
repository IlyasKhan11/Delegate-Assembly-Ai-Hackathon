> Interface update: start at `/app`. The default is a blank browser conversation. Enter the hotel scenario below as your task, or use **Explore a sample conversation** for offline rehearsal. Invitation and review labels appear in your selected language.

# Delegate — 4–6 minute judges’ demo

## The sentence everyone should remember

> Delegate helps you speak through an AI without handing over control of your decisions.

Your strongest moment is the $30 hotel offer being stopped by a $20 spending limit. Show that moment early.

## Who does what

| Person | Role | Screen |
| --- | --- | --- |
| Presenter | Explains the problem, represents the traveler, approves decisions | Main Delegate dashboard, projected for the judges |
| You / another teammate | Plays the hotel receptionist and responds naturally | Separate receptionist screen |

With three teammates, the presenter can focus on speaking while a separate operator clicks. With two, the presenter can run the dashboard herself. Nobody needs to pretend to be an AI.

## Prepare before judging

1. Start the existing Python server: `python -m uvicorn app:app --port 8000`.
2. Open `http://localhost:8000/demo`, select **Browser conversation**, choose **Spanish** or **French**, and use **Assist**.
3. Use this task: “Ask Grandview Harbour Hotel for early check-in on Friday at 12 PM. I can pay at most $20 extra. Do not confirm a booking or share payment information without my approval.”
4. Click **Test speaker** and check the laptop volume. This test does not call the paid model.
5. Start the call. Click **Invite participant**, then open the receptionist link in a separate window. The receptionist cannot see private limits or unapproved drafts.
6. Keep the main window on the projector. Use a second browser window on the same laptop for the receptionist if you have only one machine. You can arrange the two windows side by side for rehearsal.
7. Enable **agent audio** on only the screen you want to hear. The presenter screen plays approved replies by default. Keep the microphone off while the agent speaks.
8. If browser dictation works on your laptop, use **Dictate reply**, speak, review the text, then click **Send to Delegate**. If it does not work, type. Do not lose presentation time troubleshooting microphone settings.
9. Do one rehearsal check of custom replies with a non-English language selected. Type a short reply such as “hi” under **Write the exact words…**, click **Prepare reply**, and confirm the English draft is just “hi”, not a longer negotiation sentence. This makes one small model request. It has not been verified against the live model yet, only in mocked tests.
10. Keep the guided demo ready in another tab as a fallback. Label it as a guided simulation if you use it; it makes no AI requests.

Starting or approving a prepared reply does not place a telephone call. This version connects the two sides through the browser.

## Five-minute script

### 0:00–0:40 — The problem

Presenter says:

> “Imagine arriving in a new country and needing to call a hotel. You know exactly what you want, but you do not speak their language. A translation app helps with words. It still leaves you managing a live conversation and deciding what to agree to.
>
> Delegate lets an AI speak for you while you keep control of spending, bookings, and personal information.”

Show the setup screen, not a code editor.

### 0:40–1:20 — What you are testing

Presenter says:

> “I want to check in at noon on Friday. I can spend up to twenty dollars, and I want to approve any booking myself.
>
> My teammate will play the hotel receptionist. These are live human replies processed by our AI and backend. The connection is browser-based today; we are not claiming to dial a real hotel.”

Point to the selected language and the $20 rule. Start the call, open the teammate screen, and approve the opening.

### 1:20–2:30 — The important failure case

Receptionist sends:

> “We can do 12 PM on Friday, but the early check-in fee is thirty dollars.”

Wait for the decision card. Do not send more receptionist messages while the user is reviewing a decision.

Presenter says:

> “The hotel asked for thirty dollars. My limit is twenty. The backend has stopped the agreement and asked me what to do. A price being offered is different from a price being accepted.
>
> I can approve an exception, decline, or negotiate. I will negotiate.”

Click **Negotiate down to $20**. Point to the English draft and its translation. Click **Approve & speak**.

### 2:30–3:35 — A second decision, not automatic consent

Receptionist sends:

> “Twenty dollars is fine. Payment is at the front desk. Shall I confirm the booking?”

Presenter says:

> “A price within my budget still does not give the agent permission to book. It asks me again before making that commitment.”

Select **Approve this booking**, review the draft, then click **Approve & speak**.

Receptionist sends:

> “Your early check-in is confirmed for Friday at 12 PM for twenty dollars, payable at the front desk. Goodbye.”

In Assist mode, approve a final sign-off if one is staged. Then click **View call summary** or **End call & view summary**.

### 3:35–4:25 — Show evidence

Presenter says:

> “The summary separates what was agreed from what remains unresolved. The transcript shows both languages, and the ledger records the decisions I actually approved.
>
> We can review who said what instead of relying on an AI’s unsupported claim that everything is done.”

Show the summary and decision ledger. If the AI response differs slightly from rehearsal, explain what the actual screen says; do not claim an outcome that the transcript does not support.

### 4:25–5:00 — Technical contribution and close

Presenter says:

> “The language model handles conversation and translation. The Python backend manages the call state and approval gates, including explicit checks for common over-budget dollar quotes and booking requests.
>
> We use a small model with response limits to keep the prototype affordable. Our browser connection lets us test the interaction with a real person today. A telephone provider is the next transport integration.
>
> Delegate’s promise is simple: an AI can speak for you, but you still decide.”

## If you have an extra minute

Offer a genuine test instead of a longer speech:

> “Would you like to choose a different price or ask for a booking before approval?”

Ask a judge for the next receptionist line and type it into the receptionist screen. For example, “The fee is forty dollars” or “The fee is $45” triggers the spending gate. Avoid hyphenated spoken numbers such as “forty-five dollars”: the built-in price check does not read those, so only the AI model would catch them. Or request a card number to demonstrate the personal-data gate. Keep examples within the implemented English/dollar scenario; do not promise universal policy coverage.

## If you only have four minutes

Keep the problem to 20 seconds. Spend two minutes on the $30 → $20 → booking-approval sequence. Show the ledger and close. Skip the landing page, optional interruption test, and implementation detail.

## What the controls mean

- **Receptionist response / teammate screen:** what the real human on the business side says.
- **Write the exact words…:** what you want the agent to say. Click **Prepare reply**, review the translation, then approve it.
- **Stop / pause agent:** immediately stops local speech and pauses the agent. It is not a “next step” button.
- **Agent paused:** the call is already paused; prepare a replacement reply and approve it to continue.
- **Assist:** every normal reply is reviewed. A non-binding “let me check” holding phrase may be spoken when a guardrail triggers.
- **Delegate:** routine replies can proceed; the important decisions still return to the user.
- **Guided demo:** prewritten, offline rehearsal. Clearly distinguished from live AI roleplay.

## Likely judge questions

**Is this a real phone call?**

“Not yet. Today it is a live browser conversation between our assistant and a real human acting as the business. The same evaluation and approval backend can be connected to a telephone transport next.”

**Is the AI actually responding, or is this scripted?**

“In AI roleplay, our model processes what my teammate actually types or dictates. The hotel lines are rehearsal cues, not the model’s responses. We also have a separate, clearly labeled guided simulation for rehearsals.”

**What did your team build?**

“The bilingual UI, call state machine, human approval flow, explicit constraint checks, independent receptionist view, and decision audit. We use external models for language generation and translation.”

**Does the model guarantee safety?**

“No. Our prototype adds explicit backend gates and human review. The numeric checks cover common English dollar quotes; other constraints also use model evaluation. Production needs richer structured policies, authentication, managed data retention, and more adversarial testing.”

**Why not use a translation app?**

“Translation helps convey words. We also track the user’s goal and constraints, pause when a decision is required, and preserve an audit of explicit approvals.”

**Why not just let the agent finish automatically?**

“Agreeing to a booking, a charge, or sharing information is a user decision. Automation should make the conversation easier without assuming that consent.”

**How much does it cost?**

“We use GPT-4o mini through AI/ML API, cap output at 700 tokens per request, limit input size, and disable automatic retries. Guided rehearsal uses no model calls. Cost depends on the number and length of the actual requests; these are per-request controls, not a total account budget.”

## Testing on two devices

The easiest reliable presentation setup is two windows on one laptop. For two devices on a trusted Wi-Fi network, run the server with `python -m uvicorn app:app --host 0.0.0.0 --port 8000`, open the presenter page using the laptop’s LAN IP, and then generate/open the teammate link. A link beginning with `localhost` will not point to your laptop when opened on someone else’s device.

Keep this prototype on your trusted local network. It is not deployed with production authentication. Text input works over the LAN; microphone access and browser dictation may require HTTPS or localhost and browser support. A backend transcription key is optional, not needed for typed human testing.

Browser dictation reference: https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition

## Reliability rehearsal

- Refresh the presenter tab after approving the opening: the same call and transcript should return without repeating the audio.
- Pause while the AI is thinking: its late response must not become a speakable draft.
- Refresh the receptionist screen: previously spoken words remain visible; old audio does not replay.
- Disconnect briefly: both screens show a reconnect warning and retain the conversation. The receptionist keeps unsent text.
- For separate devices, mute the presenter audio and enable it only on the receptionist screen.
- Calls are saved in the local `.delegate-data` database, including instructions and transcripts. Keep the server on one process and use fictional guest information during judging.
