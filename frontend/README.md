For the current sharing, access, and first-test workflow, see [the pilot guide](../PILOT_GUIDE.md).

# Delegate frontend

Delegate is a browser conversation workspace with a dark landing page, general task setup, bilingual reply review, participant invitations, and a post-conversation summary. It is served by the existing FastAPI application, with no Node dependencies or separate frontend server.

## Run

From the repository root:

```bash
python -m pip install -r requirements.txt
python -m uvicorn app:app --reload --port 8000
```

Open **http://localhost:8000** for the landing page, or **http://localhost:8000/app** for the conversation workspace. `/demo` is retained as an alias. The same server serves both `/api/*` and the frontend, so no proxy or frontend API URL is needed.

### Sample conversation

No credentials or network services are required. Select **Explore a sample conversation** from the workspace, choose a language and review preference, then prepare the hotel scenario. Negotiate $30 down to $20, approve a one-time $30 exception, or decline. Booking confirmation is a separate decision. Reject, edit, interrupt, end the call, inspect the transcript, and download a text summary with the bilingual transcript. The five sample languages have authored translations. Custom demo text is spoken as entered and explicitly marked as untranslated.

The supplied reference starts partway through a sample conversation at the first blocked price. The sample conversation does the same; this historical dialogue is clearly labeled as simulated.

### Browser conversation

Set a valid `AIML_API_KEY` in the root `.env`, restart the server, and open **New conversation**. Enter your task and optionally the business name and spending limit, choose a language, and prepare the conversation. The opening is staged for review. Play the receptionist by typing responses or recording them with the microphone. The backend evaluates responses and returns bilingual drafts; the browser speaks approved English using its speech synthesis support.

`ASSEMBLYAI_API_KEY` enables AssemblyAI transcription; an optional, separate `GROQ_API_KEY` enables the Groq Whisper fallback. AI/ML text credits are not used for microphone transcription. With only an AI/ML key, type receptionist responses instead. Microphone access requires localhost or HTTPS and browser permission. Recordings stop after one minute, and the backend accepts at most 10 MB per recording.

**There is no telephone provider integration in this repository.** No real number is dialed. Browser conversation requires credentials and external provider availability; it never silently substitutes demo results when a provider fails.

## Checks

### AI/ML credit controls

The default text provider is `AI_PROVIDER=aimlapi` with `AIML_MODEL=openai/gpt-4o-mini`. Every text request has a 700-token output cap and a 24,000-character input ceiling. Automatic SDK retries and model fallback are disabled. Oversized inputs are rejected before making a paid request; truncated outputs do not trigger another paid request. The sample conversation still makes no AI requests.

These per-request controls reduce usage; they are not a total account spending limit. Set an account budget in your provider dashboard if needed. Model selection and limits are configured in `.env`; `.env.example` contains placeholders only. Current model pricing: [AI/ML GPT-4o mini](https://aimlapi.com/models/gpt-4o-mini).

### Validation

Node 22+ is sufficient; there is no `npm install` step.

```bash
npm run check
npm test
python -m unittest discover -s tests -v
```

The Python checks need `httpx` (already included by the OpenAI dependency). The frontend tests use Node's experimental VM module support with a minimal DOM harness. They verify state transitions and generated markup, not browser rendering or audio hardware. External AI services are mocked in backend tests.

## Backend integration fixes

- REST, uploaded audio, and WebSocket messages use the same evaluation and approval functions.
- Assist mode stages every normal reply. Delegate mode speaks routine replies, while the opening, decisions, and commitments require approval.
- Drafted actions do not enter the approval ledger until explicitly approved.
- Rejected and interrupted drafts are removed. Each draft has an identity, so approving stale text returns HTTP 409. Requests carrying a `request_id` can safely retry a completed turn or preparation; approving the same `draft_id` returns its existing receipt.
- Explicit dollar quotes above the extracted ceiling are blocked even if the model incorrectly reports completion. Approving one quote preserves the original ceiling.
- Common booking/commitment and sensitive-data requests are intercepted before completion. Other constraints also use model evaluation; this is a prototype, not a complete policy engine for arbitrary language or currencies.
- Stop immediately invalidates in-flight model replies. Remote audio stops when its screen receives the next polling update (normally within 1.5 seconds).
- Summary failures can be retried without losing the transcript. Successful summaries are cached.
- The frontend can start without API credentials. Health checks disclose configuration booleans only.
- Credentials remain on the Python backend. Frontend text is HTML-escaped before rendering.

## Limitations

Calls are saved locally in SQLite at `.delegate-data/calls.sqlite3` (ignored by Git). The database contains private instructions and transcripts and has no automatic expiry. `DELEGATE_DATABASE` can override its location. Run a single server process: the in-memory working state is not shared across workers. Refreshing the same browser tab restores its active call without replaying old audio. Public pilot mode uses a shared workspace passcode; individual user accounts and real telephony are not implemented. Its guardrail parsers cover common English/dollar forms; production use needs structured pricing and commitment data, stronger validation, and a telephony adapter. Google Fonts is optional: the UI falls back to local fonts if offline.

## Human teammate demo

In a prepared browser conversation, click **Invite participant** (shown in your selected language). The separate receptionist page shares the spoken English transcript and accepts real typed or dictated human replies. It does not expose the user's spending limit, unapproved drafts, or decision options. Presenter and receptionist views synchronize by read-only polling; polling makes no AI calls. Sending a human reply invokes the same backend evaluation and approval gates as the original roleplay.

Browser dictation is available where the browser supports it and may use the browser vendor's speech service. It fills the input for review; the user still presses Send. Typed input remains available. Backend microphone transcription remains an optional AssemblyAI/Groq feature. The current room transport is turn-based and does not stream raw audio or dial a phone number.

See [PRESENTER_GUIDE.md](PRESENTER_GUIDE.md) for the 4–6 minute script, teammate lines, test cases, and honest answers for judges. The guide is also available from **Presenter guide** in the app. For two local windows use the default localhost server. For two devices use a trusted LAN and the server's LAN address; localhost links only work on the originating machine. Use the server without `--reload` during judging to avoid interrupted requests. Calls survive restart, but an AI request running during shutdown may need to be prepared again.

To hear audio only at the receptionist’s device, select **Mute audio here** on the presenter and **Enable agent audio here** on the receptionist screen. Dictation and Send stop local playback to reduce speaker echo.

## Language behavior

The selected language controls recommendation labels, decision explanations, and the reply editor. Suggested replies appear in the user's language first, with the English version below labeled as the words the receptionist will hear. Edit opens the localized wording; Browser conversation translates the user's edit back into English before approval.

Built-in price, booking, and privacy choices use the shared `locales.json` catalog for all five supported languages and require no AI translation request. Other AI-generated choices include localized labels in the existing evaluation request. Canonical English action values stay separate from labels, preserving price and booking approval checks. The receptionist screen remains English. This localizes the decision/reply flow; some navigation, audit, and summary text remains English.

The sample conversation includes translated preset recommendations. Free-form guided-demo text still has no automatic translation and is marked as spoken as entered; use Browser conversation for custom translated replies.

Supported user languages: **Português (Brasil)** (`pt-BR`), **Español**, **Français**, **Deutsch**, and **English**. AI prompts explicitly request Brazilian Portuguese. Existing calls stored as `Portuguese` resume as Brazilian Portuguese. New sessions reject unsupported languages.

## General conversation workspace

The default setup has no sample hotel or budget. Business names and tasks are editable. An optional explicit USD spending limit overrides a conflicting model-extracted maximum; other extracted constraints are preserved. A zero-dollar limit is valid. Limits written in the task remain in effect when the numeric field is blank. The backend validates preferences before contacting the model.

The invitation banner indicates whether the participant page has polled within the last eight seconds. This means the page is connected, not that a person is listening. Manual entry is available in a collapsed section; example dialogue and judging prompts do not appear in the normal workspace. The hotel sample remains available without AI requests.

This is a local browser pilot, not a public release or integration with arbitrary call-center systems. The other person must join the link and communicate in English. There is no IVR, telephone dialing, or continuous audio transport. Invitations now expire after 24 hours and support replacement/revocation; completed conversations can be deleted. Public rollout still requires individual accounts, per-user usage quotas, automatic retention, HTTPS hosting, monitoring, and real-user accessibility/audio evaluation. The numeric guardrails currently recognize common English USD quotes.

## Preferences and appearance

Setup includes reply tone (professional, friendly, direct), reply length (concise or detailed), and additional instructions. These settings are validated and saved with the conversation, supplied to planning and generated replies, and restored on refresh. They do not override approval gates. Exact user-written replies use faithful translation without style rewriting. The sample conversation keeps its preset wording.

Voice playback on this device and speaking speed (0.8×, 1×, 1.2×) are configurable. Tone, length, playback, and speed defaults are remembered locally. Additional private instructions are saved with their conversation but are not copied into browser-wide defaults or the participant API.

Use the moon/sun button in the header or **Voice & appearance → Appearance** to choose Light, Dark, or System. Appearance persists across refreshes and synchronizes between tabs on the same origin. The participant page has its own theme button and uses the same local preference. The theme initializes before CSS to avoid a flash of the wrong background.
